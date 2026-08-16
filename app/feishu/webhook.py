"""飞书 Webhook 路由。"""
import json
import hashlib
import hmac
from fastapi import APIRouter, Request, HTTPException, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.config import settings
from app.db.connection import get_db
from app.feishu.handler import handle_message
from app.feishu.history import save_history

router = APIRouter(tags=["feishu"])


@router.post("/webhook/feishu")
async def feishu_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """飞书事件回调接口。"""
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

    # 3. 处理消息
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
            logger.info(f"收到飞书消息: {user_text[:100]}")
            try:
                reply = await handle_message(user_text, db)
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

    # 4. 卡片按钮回调
    if body_json.get("type") == "card_action":
        action_value = body_json.get("action", {}).get("value", {})
        if isinstance(action_value, str):
            try:
                action_value = json.loads(action_value)
            except (json.JSONDecodeError, TypeError):
                pass

        action = action_value.get("action", "") if isinstance(action_value, dict) else ""
        return await _handle_card_action(action, action_value, db)

    # 非文本消息
    return {"msg_type": "text", "content": {"text": "暂不支持此类消息。请发送文字与我交流。"}}


async def _handle_card_action(action: str, value: dict, db: AsyncSession):
    """处理飞书卡片按钮回调。"""
    from sqlalchemy import text
    from app.db.connection import get_sync_db

    if action == "confirm_add":
        code = value.get("stock_code", "")
        qty = value.get("quantity", 0)
        cost = float(value.get("cost_price", 0))
        # 使用同步 session 确保写入可靠
        sdb = get_sync_db()
        try:
            # 查股票名称
            result = sdb.execute(text(
                "SELECT stock_name FROM stock_master WHERE stock_code = :c"
            ), {"c": code})
            row = result.fetchone()
            name = row[0] if row else code

            # 检查是否已有活跃持仓
            result = sdb.execute(text(
                "SELECT id, quantity, cost_price FROM portfolio WHERE stock_code = :c AND is_active = true"
            ), {"c": code})
            existing = result.fetchone()

            if existing:
                old_qty = existing[1]
                old_cost = float(existing[2])
                new_qty = old_qty + qty
                new_cost = round((old_cost * old_qty + cost * qty) / new_qty, 3)
                sdb.execute(text(
                    "UPDATE portfolio SET quantity = :q, cost_price = :c, updated_at = CURRENT_TIMESTAMP WHERE id = :id"
                ), {"q": new_qty, "c": new_cost, "id": existing[0]})
                sdb.execute(text(
                    "INSERT INTO portfolio_history (stock_code, stock_name, action, quantity_before, quantity_after, cost_before, cost_after) "
                    "VALUES (:sc, :sn, 'add', :qb, :qa, :cb, :ca)"
                ), {"sc": code, "sn": name, "qb": old_qty, "qa": new_qty, "cb": old_cost, "ca": new_cost})
            else:
                sdb.execute(text(
                    "INSERT INTO portfolio (stock_code, stock_name, exchange, quantity, cost_price) "
                    "VALUES (:sc, :sn, 'SSE', :q, :c)"
                ), {"sc": code, "sn": name, "q": qty, "c": cost})
                sdb.execute(text(
                    "INSERT INTO portfolio_history (stock_code, stock_name, action, quantity_before, quantity_after, cost_before, cost_after) "
                    "VALUES (:sc, :sn, 'add', 0, :q, 0, :c)"
                ), {"sc": code, "sn": name, "q": qty, "c": cost})

            sdb.commit()
            save_history(user_msg="", assistant_msg=f"已确认添加持仓 {name} {code} {qty}股 成本{cost}")
            return {"msg_type": "text", "content": {"text": f"✅ 已更新！{name} {code} 持仓已更新。"}}
        except Exception as e:
            sdb.close()
            logger.error(f"confirm_add 失败: {e}")
            return {"msg_type": "text", "content": {"text": f"❌ 操作失败：{str(e)[:200]}"}}
        finally:
            sdb.close()

    elif action == "confirm_preference":
        mode = value.get("mode", "balanced")
        sdb = get_sync_db()
        try:
            mode_names = {"left": "左侧交易", "right": "右侧交易", "balanced": "均衡"}
            # 先读后 merge：只更新 mode 键，避免覆盖 deepseek_key 等其他配置
            cur = sdb.execute(text(
                "SELECT params FROM strategy_config WHERE strategy_name = 'global_preference'"
            )).fetchone()
            params = {}
            if cur and cur[0]:
                params = json.loads(cur[0]) if isinstance(cur[0], str) else (cur[0] or {})
            params["mode"] = mode
            sdb.execute(text(
                "UPDATE strategy_config SET params = :p, updated_at = CURRENT_TIMESTAMP, updated_by = 'feishu' "
                "WHERE strategy_name = 'global_preference'"
            ), {"p": json.dumps(params, ensure_ascii=False)})
            sdb.commit()
            return {"msg_type": "text", "content": {"text": f"✅ 已切换为{mode_names.get(mode, mode)}偏好。"}}
        except Exception as e:
            return {"msg_type": "text", "content": {"text": f"❌ 操作失败：{str(e)[:200]}"}}
        finally:
            sdb.close()

    elif action == "cancel":
        return {"msg_type": "text", "content": {"text": "已取消操作。"}}

    return {"msg_type": "text", "content": {"text": "未知操作。"}}
