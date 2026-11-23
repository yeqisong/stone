"""飞书开放平台出站客户端：tenant_access_token + 消息回复。

只依赖 httpx；未配置 App ID/Secret 时全部 no-op，便于本地无飞书环境运行。
"""
import asyncio
import json
import time
from urllib.parse import quote

import httpx
from loguru import logger

from app.config import settings

BASE_URL = "https://open.feishu.cn"
_token = ""
_token_exp = 0.0
_token_lock = asyncio.Lock()


def configured() -> bool:
    return bool(settings.FEISHU_APP_ID and settings.FEISHU_APP_SECRET)


async def tenant_access_token(force: bool = False) -> str:
    """获取并缓存 tenant_access_token（提前 60s 过期）。"""
    global _token, _token_exp
    if not configured():
        return ""
    if not force and _token and time.time() < _token_exp:
        return _token
    async with _token_lock:
        if not force and _token and time.time() < _token_exp:
            return _token
        try:
            async with httpx.AsyncClient(timeout=8) as cli:
                r = await cli.post(
                    f"{BASE_URL}/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": settings.FEISHU_APP_ID,
                          "app_secret": settings.FEISHU_APP_SECRET})
            data = r.json() if r.content else {}
            if r.status_code != 200 or data.get("code") != 0:
                logger.warning(f"[feishu] 获取 tenant_access_token 失败: "
                               f"http={r.status_code} code={data.get('code')} msg={data.get('msg')}")
                return ""
            _token = str(data.get("tenant_access_token") or "")
            _token_exp = time.time() + max(int(data.get("expire") or 7200) - 60, 60)
            return _token
        except Exception as e:
            logger.warning(f"[feishu] 获取 tenant_access_token 异常: {str(e)[:160]}")
            return ""


async def reply_message(message_id: str, payload: dict) -> bool:
    """回复某条消息。payload 为飞书消息体：{"msg_type": "text", "content": "{...}"}。"""
    if not message_id or not payload:
        return False
    token = await tenant_access_token()
    if not token:
        return False
    mid = quote(str(message_id), safe="")
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.post(
                f"{BASE_URL}/open-apis/im/v1/messages/{mid}/reply",
                headers={"Authorization": f"Bearer {token}"},
                json=payload)
        data = r.json() if r.content else {}
        if r.status_code == 200 and data.get("code") == 0:
            return True
        logger.warning(f"[feishu] 回复消息失败: http={r.status_code} "
                       f"code={data.get('code')} msg={data.get('msg')}")
    except Exception as e:
        logger.warning(f"[feishu] 回复消息异常: {str(e)[:160]}")
    return False


def text_payload(text: str) -> dict:
    return {"msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)}


def card_payload(card: dict) -> dict:
    return {"msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False)}


def normalize_reply(reply: str) -> dict:
    """把 handler 返回的文本/卡片 JSON 统一成飞书消息体。"""
    s = (reply or "").strip()
    if s.startswith("{"):
        try:
            obj = json.loads(s)
            if isinstance(obj, dict) and obj.get("msg_type") in ("text", "interactive"):
                content = obj.get("content")
                if obj["msg_type"] == "interactive":
                    card = obj.get("card")
                    if isinstance(card, dict):
                        return card_payload(card)
                    if isinstance(content, dict):
                        return card_payload(content)
                elif obj["msg_type"] == "text" and isinstance(content, dict):
                    return text_payload(str(content.get("text") or ""))
                if isinstance(content, str):
                    return {"msg_type": obj["msg_type"], "content": content}
        except (json.JSONDecodeError, TypeError):
            pass
    return text_payload(s or "（无回复）")
