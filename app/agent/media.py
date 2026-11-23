"""Agent 工作区媒体文件的短时签名 URL。

为什么不用 JWT：`<img src>` 只能带查询参数，把 24h 的登录 Token 拼进 URL 会落到
访问日志/Referer 里。这里签一个只对「会话 + 相对路径」有效、默认 24h 的专用 token，
消息回放时由 API 重新签发，因此旧链接过期不影响再次打开会话。
"""
import hashlib
from datetime import datetime, timedelta
from urllib.parse import quote

from jose import JWTError, jwt

from app.config import settings

SCOPE = "agent_file"
DEFAULT_TTL = 24 * 3600


def _file_key() -> str:
    """文件签名密钥：独立配置优先；否则从 APP_SECRET_KEY 做域分离派生。

    绝不复用登录 JWT 密钥，避免 `?sig=` 被当成 Bearer Token。
    """
    if settings.AGENT_FILE_SECRET:
        return settings.AGENT_FILE_SECRET
    return hashlib.sha256(
        (settings.APP_SECRET_KEY + "|agent-file-v1").encode("utf-8")
    ).hexdigest()


def sign_path(session_id: int, rel: str, user_id: int | None = None,
              ttl: int = DEFAULT_TTL) -> str:
    """返回带签名的文件 URL（rel 为工作区内相对路径，绑定 user_id）。"""
    payload = {"scope": SCOPE, "sid": int(session_id), "rel": rel,
               "exp": datetime.utcnow() + timedelta(seconds=ttl)}
    if user_id is not None:
        payload["uid"] = int(user_id)
    token = jwt.encode(payload, _file_key(), algorithm="HS256")
    return f"/api/agent/sessions/{session_id}/files/{quote(rel)}?sig={token}"


def verify_sig(session_id: int, rel: str, sig: str,
               user_id: int | None = None) -> bool:
    try:
        payload = jwt.decode(sig, _file_key(), algorithms=["HS256"])
    except JWTError:
        return False
    ok = (payload.get("scope") == SCOPE
          and int(payload.get("sid") or 0) == int(session_id)
          and payload.get("rel") == rel)
    if not ok:
        return False
    if user_id is not None:
        return int(payload.get("uid") or 0) == int(user_id)
    return True


def sign_images(session_id: int, images: list, user_id: int | None = None) -> list:
    """把 [{name, url}] 里的 url 重签成短时签名地址。"""
    out = []
    for im in images or []:
        if not isinstance(im, dict):
            continue
        rel = im.get("path") or im.get("name") or ""
        if not rel:
            continue
        out.append({"name": im.get("name") or rel,
                    "url": sign_path(session_id, rel, user_id=user_id)})
    return out
