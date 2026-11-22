"""飞书 Webhook 路由。"""
import asyncio
import json
import hashlib
import hmac
import re
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.config import settings
from app.db.connection import get_db
from app.feishu.handler import handle_message
from app.feishu.identity import (bind_by_code, extract_open_id,
                                 extract_union_id, history_key, resolve_user)
from app.feishu.history import save_history

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


@router.post("/webhook/feishu")
async def feishu_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """飞书事件回调接口（按 open_id 绑定系统用户，未绑定拒绝）。"""
    body = await request.body()
    body_json = await request.json()

    # 1. URL 验证（飞书配置回调地址时发送）
    if body_json.get("type") == "url_verification":
        token = body_json.get("token", "")
        challenge = body_json.get("challenge", "")
        # 可选：校验 verify_token
        if settings.FEISHU_VERIFY_TOKEN and token != settings.FEISHU_VERIFY_TOKEN:
            raise HTTPException(status_code=403, detail="Invalid verify token")
        return {"challenge": challenge}

    # 生产环境必须配置签名密钥，否则任何人都能伪造事件写持仓
    if settings.APP_ENV == "prod" and not settings.FEISHU_APP_SECRET:
        raise HTTPException(status_code=503, detail="飞书回调未配置签名密钥，已拒绝")

    # 2. 校验签名（FEISHU_APP_SECRET 配置时始终校验）
    if settings.FEISHU_APP_SECRET:
        timestamp = request.headers.get("X-Lark-Request-Timestamp", "")
        nonce = request.headers.get("X-Lark-Request-Nonce", "")
        signature = request.headers.get("X-Lark-Signature", "")
        # 飞书签名校验：HMAC-SHA256(secret, timestamp + nonce + body)
        body_str = body.decode("utf-8") if isinstance(body, bytes) else str(body)
        sign_str = f"{timestamp}{nonce}{body_str}"
        expected = hmac.new(
            settings.FEISHU_APP_SECRET.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        if signature != expected:
            logger.warning(f"飞书签名校验失败: expected={expected[:16]}... received={signature[:16]}...")
            raise HTTPException(status_code=403, detail="签名校验失败")

    # 3. 身份解析：open_id → 系统用户
    open_id = extract_open_id(body_json)

    # 4. 处理消息
    event = body_json.get("event", {})
    msg_type = event.get("message", {}).get("message_type", "")

    if msg_type == "text":
        content = event.get("message", {}).get("content", "{}")
        try:
            content_json = json.loads(content)
            user_text = content_json.get("text", "")
        except (json.JSONDecodeError, TypeError):
            user_text = content

        if user_text.strip():
            logger.info(f"收到飞书消息 open_id={open_id[:12]}...: {user_text[:100]}")
            # 绑定命令单独处理：未绑定→绑定；已绑定→提示先解绑，绝不进 LLM
            m = _BIND_RE.match(user_text.strip())
            principal = await _resolve_principal(db, open_id)
            if m:
                if principal is not None:
                    return {"msg_type": "text",
                            "content": {"text": "当前飞书账号已绑定，如需换绑请先在 Web 端「绑定飞书」里解绑。"}}
                await db.rollback()
                r = await bind_by_code(db, m.group(1), open_id,
                                       extract_union_id(body_json))
                if r.get("error"):
                    return {"msg_type": "text",
                            "content": {"text": f"❌ {r['error']}"}}
                logger.info(f"飞书账号已绑定用户 {r['username']}")
                return {"msg_type": "text",
                        "content": {"text": f"✅ 绑定成功，欢迎 {r['username']}。现在可以问我持仓、信号和行情了。"}}
            if principal is None:
                return {"msg_type": "text", "content": {"text": _unbound_hint()}}
            try:
                reply = await handle_message(user_text, db, principal)
                return {
                    "msg_type": "text",
                    "content": {"text": reply},
                }
            except Exception as e:
                logger.error(f"处理飞书消息失败: {e}")
                return {
                    "msg_type": "text",
                    "content": {"text": f"处理失败，请稍候重试。错误：{str(e)[:200]}"},
                }

    # 5. 卡片按钮回调（同样按 open_id 解析用户，不信任卡片 value 里的身份）
    if body_json.get("type") == "card_action":
        principal = await _resolve_principal(db, open_id)
        if principal is None:
            return {"msg_type": "text", "content": {"text": _unbound_hint()}}
        action_value = body_json.get("action", {}).get("value", {})
        if isinstance(action_value, str):
            try:
                action_value = json.loads(action_value)
            except (json.JSONDecodeError, TypeError):
                pass

        action = action_value.get("action", "") if isinstance(action_value, dict) else ""
        return await _handle_card_action(action, action_value, db, principal)

    # 非文本消息
    return {"msg_type": "text", "content": {"text": "暂不支持此类消息。请发送文字与我交流。"}}


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
        # 使用同步 session 确保写入可靠
        sdb = get_sync_db()
        try:
            # 查股票名称与交易所
            row = sdb.execute(text(
                "SELECT stock_name, COALESCE(exchange, 'SSE') FROM stock_master "
                "WHERE stock_code = :c AND stock_type = 'stock' LIMIT 1"
            ), {"c": code}).fetchone()
            name = row[0] if row else code
            exchange = row[1] if row else "SSE"

            # 检查当前用户是否已有同一活跃持仓
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
