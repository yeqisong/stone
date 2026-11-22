"""飞书身份绑定：open_id ↔ 系统用户。

单用户时代飞书回调不区分发送者，所有消息共用一份上下文与持仓。多用户版本改为：
- 管理员/用户先在 Web 端生成一次性绑定码（默认 10 分钟有效）；
- 飞书里发送「绑定 123456」完成 open_id 绑定；
- 之后所有消息/卡片回调都按 open_id 解析出 user_id，未绑定直接拒绝。

绑定码只存 sha256，过期即失效；一个 open_id 只能绑定一个用户。
"""
import hashlib
import secrets
import time

from sqlalchemy import text

BIND_TTL = 600          # 一次性码有效期（秒）
CONFIG_KEY = "feishu_bind"


def _hash_code(code: str) -> str:
    return hashlib.sha256((code or "").strip().encode("utf-8")).hexdigest()


def history_key(user_id: int) -> str:
    """飞书对话历史的 Redis/内存键（按系统用户隔离）。"""
    return f"feishu:u{int(user_id)}"


def extract_open_id(payload: dict) -> str:
    """从事件/卡片回调里尽量提取发送者 open_id（兼容 v1/v2 多种结构）。"""
    payload = payload or {}
    event = payload.get("event") or {}
    sender = event.get("sender") or payload.get("sender") or {}
    sender_id = sender.get("sender_id") or {}
    operator = event.get("operator") or payload.get("operator") or {}
    action = payload.get("action") or {}
    for value in (
        sender_id.get("open_id"),
        operator.get("open_id"),
        payload.get("open_id"),
        action.get("open_id"),
    ):
        if value:
            return str(value)
    return ""


def extract_union_id(payload: dict) -> str:
    payload = payload or {}
    event = payload.get("event") or {}
    sender = event.get("sender") or payload.get("sender") or {}
    sender_id = sender.get("sender_id") or {}
    return str(sender_id.get("union_id") or "")


async def resolve_user(db, open_id: str) -> dict | None:
    """open_id → 有效用户；未绑定/已禁用返回 None。"""
    if not open_id:
        return None
    row = (await db.execute(text("""
        SELECT id, username, display_name, status
        FROM users WHERE feishu_open_id = :o LIMIT 1
    """), {"o": open_id})).mappings().first()
    if not row or row["status"] != "active":
        return None
    return dict(row)


async def issue_bind_code(db, user_id: int) -> dict:
    """为用户生成一次性绑定码（覆盖旧码）。"""
    now = int(time.time())
    code = ""
    for _ in range(8):
        cand = f"{secrets.randbelow(1_000_000):06d}"
        exists = (await db.execute(text("""
            SELECT 1 FROM user_config
            WHERE config_key = :k AND params->>'code_hash' = CAST(:h AS text)
              AND (params->>'exp')::bigint > CAST(:now AS bigint) LIMIT 1
        """), {"k": CONFIG_KEY, "h": _hash_code(cand), "now": now})).first()
        if not exists:
            code = cand
            break
    if not code:
        return {"error": "验证码生成失败，请重试"}
    await db.execute(text("""
        INSERT INTO user_config (user_id, config_key, params, enabled, updated_at)
        VALUES (:u, :k,
                jsonb_build_object('code_hash', CAST(:h AS text),
                                   'exp', CAST(:exp AS bigint)), true,
                CURRENT_TIMESTAMP)
        ON CONFLICT (user_id, config_key) DO UPDATE
           SET params = EXCLUDED.params, enabled = true,
               updated_at = CURRENT_TIMESTAMP
    """), {"u": int(user_id), "k": CONFIG_KEY, "h": _hash_code(code), "exp": now + BIND_TTL})
    await db.commit()
    return {"ok": True, "code": code, "expires_in": BIND_TTL}


async def bind_by_code(db, code: str, open_id: str,
                       union_id: str = "") -> dict:
    """用一次性码把 open_id 绑定到用户；返回 {ok/user_id/username} 或 {error}。"""
    code = (code or "").strip()
    if not code or not open_id:
        return {"error": "验证码或飞书身份为空"}
    now = int(time.time())
    row = (await db.execute(text("""
        SELECT uc.user_id, u.username
        FROM user_config uc JOIN users u ON u.id = uc.user_id
        WHERE uc.config_key = :k
          AND uc.params->>'code_hash' = CAST(:h AS text)
          AND (uc.params->>'exp')::bigint > CAST(:now AS bigint)
          AND u.status = 'active'
        LIMIT 1
    """), {"k": CONFIG_KEY, "h": _hash_code(code), "now": now})).mappings().first()
    if not row:
        return {"error": "绑定码无效或已过期，请在 Web 端重新生成"}
    uid = int(row["user_id"])
    other = (await db.execute(text(
        "SELECT id FROM users WHERE feishu_open_id = :o AND id <> :u LIMIT 1"
    ), {"o": open_id, "u": uid})).first()
    if other:
        return {"error": "该飞书账号已绑定其他用户，请先解绑"}
    await db.execute(text("""
        UPDATE users SET feishu_open_id = :o,
                         feishu_union_id = COALESCE(NULLIF(:un, ''), feishu_union_id),
                         updated_at = CURRENT_TIMESTAMP
        WHERE id = :u
    """), {"o": open_id, "un": union_id or "", "u": uid})
    await db.execute(text(
        "DELETE FROM user_config WHERE user_id = :u AND config_key = :k"
    ), {"u": uid, "k": CONFIG_KEY})
    await db.commit()
    return {"ok": True, "user_id": uid, "username": row["username"]}


async def unbind(db, user_id: int) -> dict:
    await db.execute(text("""
        UPDATE users SET feishu_open_id = NULL, feishu_union_id = NULL,
                         updated_at = CURRENT_TIMESTAMP
        WHERE id = :u
    """), {"u": int(user_id)})
    await db.commit()
    return {"ok": True}


async def is_admin(db, user_id: int) -> bool:
    row = (await db.execute(text("""
        SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id
        WHERE ur.user_id = :u AND r.code = 'admin' AND r.status = 'active'
          AND (ur.expires_at IS NULL OR ur.expires_at > CURRENT_TIMESTAMP)
        LIMIT 1
    """), {"u": int(user_id)})).first()
    return bool(row)
