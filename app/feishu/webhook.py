"""飞书 Webhook 路由。

安全要点：
- 按飞书事件订阅规范做 sha256(timestamp+nonce+encrypt_key+body) 签名校验；
- Encrypt Key 配置时解密 `encrypt` 事件；拒绝超过 10 分钟的回调（防重放）；
- 事件按 event_id/message_id 去重，避免飞书重试导致重复回复或重复写持仓；
- 消息事件先返回 200，再在后台调用 LLM 并通过「回复消息」API 发回结果（3 秒回调时限）。
"""
import asyncio
import json
import re
from fastapi import APIRouter, Request, HTTPException, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.config import settings
from app.db.connection import get_db, AsyncSessionLocal
from app.feishu.handler import handle_message
from app.feishu.identity import (bind_by_code, extract_open_id,
                                 extract_union_id, history_key, resolve_user)
from app.feishu.history import save_history
from app.feishu import client as feishu_client
from app.feishu.security import (claim_once, decrypt_payload, rate_ok,
                                 verify_signature)

router = APIRouter(tags=["feishu"])

_BIND_RE = re.compile(r"^(?:绑定|bind)\s*(\d{6})$", re.I)


def _unbound_hint() -> str:
    return ("该飞书账号尚未绑定系统用户。请先在 Web 端「用户菜单 → 绑定飞书」"
            "生成一次性绑定码，然后发送「绑定 123456」完成绑定。")


async def _resolve_principal(db, open_id: str):
    """open_id → Principal；未绑定/禁用返回 None。"""
    u = await resolve_user(db, open_id)
    if not u:
        return None
    from app.auth.service import load_principal
    return await asyncio.to_thread(load_principal, int(u["id"]))


def _message_text(payload: dict) -> str:
    event = payload.get("event") or {}
    content = (event.get("message") or {}).get("content", "{}")
    try:
        content_json = json.loads(content)
        return str(content_json.get("text", "") or "")
    except (json.JSONDecodeError, TypeError):
        return str(content or "")


async def _process_text(payload: dict, db: AsyncSession) -> str:
    """处理文本消息并返回回复文本/卡片 JSON。绑定命令不进入 LLM。"""
    open_id = extract_open_id(payload)
    user_text = _message_text(payload).strip()
    if not user_text:
        return ""
    if not rate_ok(f"u:{open_id}", limit=20, window=60):
        return "⏳ 操作过于频繁，请稍后再试。"
    logger.info(f"收到飞书消息 open_id={open_id[:12]}...: {user_text[:100]}")

    m = _BIND_RE.match(user_text)
    principal = await _resolve_principal(db, open_id)
    if m:
        if principal is not None:
            return "当前飞书账号已绑定，如需换绑请先在 Web 端「绑定飞书」里解绑。"
        await db.rollback()
        r = await bind_by_code(db, m.group(1), open_id, extract_union_id(payload))
        if r.get("error"):
            return f"❌ {r['error']}"
        logger.info(f"飞书账号已绑定用户 {r['username']}")
        return f"✅ 绑定成功，欢迎 {r['username']}。现在可以问我持仓、信号和行情了。"

    if principal is None:
        return _unbound_hint()
    try:
        return await handle_message(user_text, db, principal)
    except Exception as e:
        logger.error(f"处理飞书消息失败: {e}")
        return f"处理失败，请稍候重试。错误：{str(e)[:200]}"


async def _process_text_event(payload: dict) -> None:
    """后台任务：独立 DB 会话处理消息，再通过回复 API 发回。"""
    message_id = str(((payload.get("event") or {}).get("message") or {})
                     .get("message_id") or "")
    try:
        async with AsyncSessionLocal() as db:
            try:
                reply = await _process_text(payload, db)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        if reply and message_id:
            await feishu_client.reply_message(message_id,
                                              feishu_client.normalize_reply(reply))
    except Exception as e:
        logger.error(f"[feishu] 后台处理失败 message={message_id[:20]}: {str(e)[:200]}")


def _payload_text(payload: dict) -> str:
    """从 _handle_card_action 的返回体里提取纯文本，供 toast/回复使用。"""
    if not isinstance(payload, dict):
        return str(payload or "")
    content = payload.get("content")
    if isinstance(content, dict):
        return str(content.get("text") or "")
    if isinstance(content, str):
        try:
            obj = json.loads(content)
            if isinstance(obj, dict):
                return str(obj.get("text") or content)
        except (json.JSONDecodeError, TypeError):
            return content
    return str(payload)


async def _reply_report(open_message_id: str, result: dict) -> None:
    """卡片操作完成后异步回复结果，不阻塞卡片回调。"""
    text = _payload_text(result)
    if open_message_id and text:
        await feishu_client.reply_message(
            open_message_id, feishu_client.normalize_reply(text))


def _card_key(payload: dict, action: str, value: dict) -> str:
    header = payload.get("header") or {}
    eid = header.get("event_id")
    if eid:
        return f"card:{eid}"
    oid = str(payload.get("open_message_id") or "")
    raw = json.dumps(value or {}, ensure_ascii=False, sort_keys=True, default=str)
    return f"card:{oid}:{action}:{raw}" if oid else ""


@router.post("/webhook/feishu")
async def feishu_webhook(request: Request, background: BackgroundTasks,
                         db: AsyncSession = Depends(get_db)):
    """飞书事件回调接口（按 open_id 绑定系统用户，未绑定拒绝）。"""
    raw = await request.body()

    # 1. 签名校验：Encrypt Key 用官方 SHA-256 规范；未配置时退回事件 token 校验
    sign_key = settings.FEISHU_ENCRYPT_KEY
    if settings.APP_ENV == "prod" and not sign_key and not settings.FEISHU_VERIFY_TOKEN:
        raise HTTPException(status_code=503, detail="飞书回调未配置签名密钥/校验 token，已拒绝")
    if sign_key:
        ok, reason = verify_signature(
            raw,
            request.headers.get("X-Lark-Request-Timestamp", ""),
            request.headers.get("X-Lark-Request-Nonce", ""),
            request.headers.get("X-Lark-Signature", ""),
            sign_key)
        if not ok:
            logger.warning(f"[feishu] 签名校验失败: {reason}")
            raise HTTPException(status_code=403, detail="签名校验失败")

    # 2. 解析（加密事件先解密）
    try:
        body_json = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    if isinstance(body_json, dict) and body_json.get("encrypt"):
        if not settings.FEISHU_ENCRYPT_KEY:
            raise HTTPException(status_code=503, detail="收到加密事件但未配置 Encrypt Key")
        try:
            body_json = decrypt_payload(body_json["encrypt"],
                                        settings.FEISHU_ENCRYPT_KEY)
        except ValueError as e:
            logger.warning(f"[feishu] {e}")
            raise HTTPException(status_code=400, detail="事件解密失败")

    # 2.1 未启用加密时用事件 token 兜底验真（v2 在 header.token，v1/卡片在顶层 token）
    if not sign_key and settings.FEISHU_VERIFY_TOKEN:
        evt_token = str((body_json.get("header") or {}).get("token")
                        or body_json.get("token") or "")
        if evt_token != settings.FEISHU_VERIFY_TOKEN:
            logger.warning("[feishu] 事件 token 校验失败")
            raise HTTPException(status_code=403, detail="事件 token 校验失败")

    # 3. URL 验证（飞书配置回调地址时发送）
    if body_json.get("type") == "url_verification":
        token = body_json.get("token", "")
        challenge = body_json.get("challenge", "")
        if settings.FEISHU_VERIFY_TOKEN and token != settings.FEISHU_VERIFY_TOKEN:
            raise HTTPException(status_code=403, detail="Invalid verify token")
        return {"challenge": challenge}

    # 4. 文本消息
    event = body_json.get("event") or {}
    message = event.get("message") or {}
    msg_type = message.get("message_type", "")
    message_id = str(message.get("message_id") or "")

    if msg_type == "text":
        if not _message_text(body_json).strip():
            return {"code": 0}
        # 绑定命令需要在回调内快速返回；其余消息统一后台处理并通过回复 API 发送
        event_id = str((body_json.get("header") or {}).get("event_id") or "")
        dedup_key = f"msg:{event_id or message_id}"
        if not message_id:
            # 兼容本地/测试回调：无 message_id 时同步返回，不调用外部 API
            reply = await _process_text(body_json, db)
            return feishu_client.normalize_reply(reply)
        if not claim_once(dedup_key):
            logger.info(f"[feishu] 重复投递已忽略: {dedup_key}")
            return {"code": 0}
        background.add_task(_process_text_event, body_json)
        return {"code": 0}

    # 5. 卡片按钮回调（身份只认 open_id，不信任卡片 value 里的用户字段）
    if body_json.get("type") == "card_action":
        principal = await _resolve_principal(db, extract_open_id(body_json))
        if principal is None:
            return {"toast": {"type": "error", "content": _unbound_hint()[:100]}}
        action_value = (body_json.get("action") or {}).get("value", {})
        if isinstance(action_value, str):
            try:
                action_value = json.loads(action_value)
            except (json.JSONDecodeError, TypeError):
                action_value = {}
        action = action_value.get("action", "") if isinstance(action_value, dict) else ""
        dedup_key = _card_key(body_json, action, action_value)
        if dedup_key and not claim_once(dedup_key):
            return {"toast": {"type": "info", "content": "该操作已处理，请勿重复提交"}}
        result = await _handle_card_action(action, action_value, db, principal)
        text = _payload_text(result)
        open_message_id = str(body_json.get("open_message_id") or "")
        if open_message_id:
            background.add_task(_reply_report, open_message_id, result)
        toast_type = "error" if "❌" in text else "success"
        return {"toast": {"type": toast_type, "content": (text or "操作已完成")[:120]}}

    # 非文本消息
    return {"code": 0}


async def _handle_card_action(action: str, value: dict, db: AsyncSession, principal):
    """处理飞书卡片按钮回调（owner = principal.data_user_id）。"""
    from sqlalchemy import text
    from app.db.connection import get_sync_db

    user_id = int(principal.data_user_id)

    if action == "confirm_add":
        if not principal.can("portfolio.position.edit"):
            return {"msg_type": "text", "content": {"text": "❌ 无权限：当前账号不能编辑持仓"}}
        code = str(value.get("stock_code", "")).strip()
        try:
            qty = int(value.get("quantity", 0))
            cost = float(value.get("cost_price", 0))
        except (TypeError, ValueError):
            return {"msg_type": "text", "content": {"text": "❌ 参数错误"}}
        if not re.fullmatch(r"\d{6}", code) or qty <= 0 or cost <= 0:
            return {"msg_type": "text", "content": {"text": "❌ 参数错误：代码/数量/成本无效"}}
        sdb = get_sync_db()
        try:
            row = sdb.execute(text(
                "SELECT stock_name, COALESCE(exchange, 'SSE') FROM stock_master "
                "WHERE stock_code = :c AND stock_type = 'stock' LIMIT 1"
            ), {"c": code}).fetchone()
            name = row[0] if row else code
            exchange = row[1] if row else "SSE"

            existing = sdb.execute(text(
                "SELECT id, quantity, cost_price FROM portfolio "
                "WHERE user_id = :u AND stock_code = :c AND is_active = true"
            ), {"u": user_id, "c": code}).fetchone()

            if existing:
                old_qty = int(existing[1])
                old_cost = float(existing[2])
                new_qty = old_qty + qty
                new_cost = round((old_cost * old_qty + cost * qty) / new_qty, 3)
                sdb.execute(text(
                    "UPDATE portfolio SET quantity = :q, cost_price = :c, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = :id AND user_id = :u"
                ), {"q": new_qty, "c": new_cost, "id": existing[0], "u": user_id})
                sdb.execute(text(
                    "INSERT INTO portfolio_history (user_id, stock_code, stock_name, action, "
                    "quantity_before, quantity_after, cost_before, cost_after) "
                    "VALUES (:u, :sc, :sn, 'add', :qb, :qa, :cb, :ca)"
                ), {"u": user_id, "sc": code, "sn": name, "qb": old_qty, "qa": new_qty,
                    "cb": old_cost, "ca": new_cost})
            else:
                sdb.execute(text(
                    "INSERT INTO portfolio (user_id, stock_code, stock_name, exchange, "
                    "quantity, cost_price) VALUES (:u, :sc, :sn, :ex, :q, :c)"
                ), {"u": user_id, "sc": code, "sn": name, "ex": exchange, "q": qty, "c": cost})
                sdb.execute(text(
                    "INSERT INTO portfolio_history (user_id, stock_code, stock_name, action, "
                    "quantity_before, quantity_after, cost_before, cost_after) "
                    "VALUES (:u, :sc, :sn, 'add', 0, :q, 0, :c)"
                ), {"u": user_id, "sc": code, "sn": name, "q": qty, "c": cost})

            sdb.commit()
            save_history(history_key(user_id), user_msg="",
                         assistant_msg=f"已确认添加持仓 {name} {code} {qty}股 成本{cost}")
            return {"msg_type": "text", "content": {"text": f"✅ 已更新！{name} {code} 持仓已更新。"}}
        except Exception as e:
            try:
                sdb.rollback()
            except Exception:
                pass
            logger.error(f"confirm_add 失败 user={user_id}: {e}")
            return {"msg_type": "text", "content": {"text": f"❌ 操作失败：{str(e)[:200]}"}}
        finally:
            sdb.close()

    elif action == "confirm_preference":
        if not principal.can("portfolio.position.edit"):
            return {"msg_type": "text", "content": {"text": "❌ 无权限：当前账号不能修改交易偏好"}}
        mode = value.get("mode", "balanced")
        if mode not in ("left", "right", "balanced"):
            return {"msg_type": "text", "content": {"text": "❌ 未知偏好"}}
        sdb = get_sync_db()
        try:
            mode_names = {"left": "左侧交易", "right": "右侧交易", "balanced": "均衡"}
            sdb.execute(text("""
                INSERT INTO user_config (user_id, config_key, params, enabled, updated_at)
                VALUES (:u, 'trade_preference', CAST(:p AS jsonb), true, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, config_key) DO UPDATE
                   SET params = EXCLUDED.params, enabled = true,
                       updated_at = CURRENT_TIMESTAMP
            """), {"u": user_id, "p": json.dumps({"mode": mode}, ensure_ascii=False)})
            sdb.commit()
            return {"msg_type": "text", "content": {"text": f"✅ 已切换为{mode_names.get(mode, mode)}偏好。"}}
        except Exception as e:
            try:
                sdb.rollback()
            except Exception:
                pass
            return {"msg_type": "text", "content": {"text": f"❌ 操作失败：{str(e)[:200]}"}}
        finally:
            sdb.close()

    elif action == "cancel":
        return {"msg_type": "text", "content": {"text": "已取消操作。"}}

    return {"msg_type": "text", "content": {"text": "未知操作。"}}
