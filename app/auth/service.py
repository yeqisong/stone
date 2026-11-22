"""用户/权限服务：Principal 加载、登录校验、JWT 签发。"""
from __future__ import annotations

import hashlib
import ipaddress
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from loguru import logger
from sqlalchemy import text

from app.auth.definitions import PERSONAL_PERMISSION_CODES
from app.auth.security import verify_password_hash
from app.config import settings


@dataclass(frozen=True)
class Grant:
    code: str
    scope: str = "self"
    support_only: bool = False
    conditions: tuple[dict, ...] = ()


@dataclass
class Principal:
    user_id: int
    username: str
    display_name: str = ""
    status: str = "active"
    role_codes: frozenset[str] = frozenset()
    permissions: dict[str, Grant] = field(default_factory=dict)
    perm_version: int = 0
    token_version: int = 0
    must_change_password: bool = False
    feishu_open_id: str | None = None
    support_session_id: int | None = None
    support_target_id: int | None = None
    support_target_name: str = ""

    @property
    def support_mode(self) -> bool:
        return self.support_target_id is not None

    @property
    def data_user_id(self) -> int:
        return self.support_target_id or self.user_id

    def grant(self, code: str) -> Grant | None:
        g = self.permissions.get(code)
        if g is None:
            return None
        if g.support_only and not self.support_mode:
            return None
        return g

    def can(self, code: str) -> bool:
        return self.grant(code) is not None

    def scope_for(self, code: str) -> str:
        if self.support_mode:
            return "support"
        g = self.grant(code)
        return g.scope if g else "self"

    def permission_codes(self) -> list[str]:
        return sorted(c for c in self.permissions if self.grant(c) is not None)


def _hash_support_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def safe_ip(value: str | None) -> str | None:
    """把 TestClient/代理头等非法值清洗成合法 inet 或 None。"""
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(str(value)))
    except ValueError:
        return None


def create_token(
    user_id: int,
    username: str = "",
    token_version: int = 0,
    perm_version: int = 0,
    expires_hours: int | None = None,
) -> str:
    now = datetime.utcnow()
    payload = {
        "sub": str(user_id),
        "uid": int(user_id),
        "username": username,
        "token_version": int(token_version),
        "perm_version": int(perm_version),
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(hours=expires_hours or settings.JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, settings.APP_SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.APP_SECRET_KEY, algorithms=["HS256"])


def _effective_grants(db, user_id: int) -> tuple[dict[str, Grant], frozenset[str]]:
    rows = db.execute(text("""
        SELECT p.code, rp.scope, rp.support_only, rp.conditions, r.code AS role_code
        FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id AND r.status = 'active'
        JOIN role_permissions rp ON rp.role_id = r.id
        JOIN permissions p ON p.id = rp.permission_id
        WHERE ur.user_id = :uid
          AND (ur.expires_at IS NULL OR ur.expires_at > CURRENT_TIMESTAMP)
    """), {"uid": user_id}).fetchall()

    denies = {
        r[0] for r in db.execute(text("""
            SELECT p.code
            FROM user_permission_overrides o
            JOIN permissions p ON p.id = o.permission_id
            WHERE o.user_id = :uid AND o.effect = 'deny'
              AND (o.expires_at IS NULL OR o.expires_at > CURRENT_TIMESTAMP)
        """), {"uid": user_id}).fetchall()
    }

    merged: dict[str, dict] = {}
    roles: set[str] = set()
    for code, scope, support_only, conditions, role_code in rows:
        roles.add(role_code)
        if code in denies:
            continue
        if code in PERSONAL_PERMISSION_CODES:
            scope = "self"
        item = merged.setdefault(code, {"scope": scope, "support_only": True, "conditions": []})
        item["support_only"] = item["support_only"] and bool(support_only)
        if conditions:
            item["conditions"].append(conditions if isinstance(conditions, dict) else {})

    # allow override：可新增授权，也可把已有授权改为非 support_only
    allow_rows = db.execute(text("""
        SELECT p.code, o.scope, o.conditions
        FROM user_permission_overrides o
        JOIN permissions p ON p.id = o.permission_id
        WHERE o.user_id = :uid AND o.effect = 'allow'
          AND (o.expires_at IS NULL OR o.expires_at > CURRENT_TIMESTAMP)
    """), {"uid": user_id}).fetchall()
    for code, scope, conditions in allow_rows:
        if code in denies:
            continue
        if code in PERSONAL_PERMISSION_CODES:
            scope = "self"
        item = merged.setdefault(code, {"scope": scope, "support_only": False, "conditions": []})
        item["support_only"] = False
        if conditions:
            item["conditions"].append(conditions if isinstance(conditions, dict) else {})

    grants = {
        code: Grant(
            code=code,
            scope=v["scope"] or "self",
            support_only=bool(v["support_only"]),
            conditions=tuple(v["conditions"]),
        )
        for code, v in merged.items()
    }
    return grants, frozenset(roles)


def load_principal(
    user_id: int,
    db=None,
    support_token: str | None = None,
) -> Principal | None:
    """按 user_id 加载 Principal；db 为空时自行开/关连接。"""
    own_db = db is None
    if own_db:
        from app.db.connection import get_sync_db
        db = get_sync_db()
    try:
        row = db.execute(text("""
            SELECT id, username, display_name, status, token_version, perm_version,
                   must_change_password, feishu_open_id
            FROM users WHERE id = :uid
        """), {"uid": user_id}).fetchone()
        if not row or row[3] != "active":
            return None

        grants, roles = _effective_grants(db, user_id)
        principal = Principal(
            user_id=int(row[0]),
            username=row[1],
            display_name=row[2] or "",
            status=row[3],
            role_codes=roles,
            permissions=grants,
            perm_version=int(row[5] or 0),
            token_version=int(row[4] or 0),
            must_change_password=bool(row[6]),
            feishu_open_id=row[7],
        )

        if support_token:
            sess = db.execute(text("""
                SELECT id, target_user_id
                FROM support_sessions
                WHERE token_hash = :h
                  AND admin_user_id = :admin
                  AND revoked_at IS NULL
                  AND expires_at > CURRENT_TIMESTAMP
                LIMIT 1
            """), {"h": _hash_support_token(support_token), "admin": user_id}).fetchone()
            if not sess:
                return None
            target_ok = db.execute(text(
                "SELECT username FROM users WHERE id = :tid AND status = 'active'"
            ), {"tid": sess[1]}).fetchone()
            if not target_ok:
                return None
            principal.support_session_id = int(sess[0])
            principal.support_target_id = int(sess[1])
            principal.support_target_name = target_ok[0] or ""
            db.execute(text("""
                UPDATE support_sessions
                SET used_at = COALESCE(used_at, CURRENT_TIMESTAMP),
                    last_access_at = CURRENT_TIMESTAMP,
                    access_count = access_count + 1
                WHERE id = :id
            """), {"id": sess[0]})
            db.commit()
        return principal
    finally:
        if own_db:
            db.close()


def authenticate(username: str, password: str, db=None) -> tuple[Principal | None, str]:
    """验证用户名/密码；返回 (principal, error)。"""
    own_db = db is None
    if own_db:
        from app.db.connection import get_sync_db
        db = get_sync_db()
    try:
        row = db.execute(text("""
            SELECT id, password_hash, status, failed_login_count, locked_until
            FROM users WHERE username = :u LIMIT 1
        """), {"u": username}).fetchone()
        if not row:
            return None, "用户名或密码错误"
        user_id, password_hash, status, failed, locked_until = row
        if status != "active":
            return None, "账号不可用"
        if locked_until and locked_until > datetime.now():
            return None, "账号已临时锁定，请稍后再试"
        if not verify_password_hash(password, password_hash):
            failed = int(failed or 0) + 1
            lock_sql = ""
            params = {"n": failed, "uid": user_id}
            if failed >= 5:
                lock_sql = ", locked_until = CURRENT_TIMESTAMP + INTERVAL '15 minutes'"
            db.execute(text(
                "UPDATE users SET failed_login_count = :n" + lock_sql + " WHERE id = :uid"
            ), params)
            db.commit()
            return None, "用户名或密码错误"

        db.execute(text("""
            UPDATE users
            SET failed_login_count = 0, locked_until = NULL,
                last_login_at = CURRENT_TIMESTAMP
            WHERE id = :uid
        """), {"uid": user_id})
        db.commit()
        principal = load_principal(user_id, db=db)
        return principal, ""
    finally:
        if own_db:
            db.close()


def issue_login_token(principal: Principal) -> str:
    return create_token(
        principal.user_id,
        principal.username,
        principal.token_version,
        principal.perm_version,
    )


def principal_payload(principal: Principal) -> dict:
    return {
        "user": {
            "id": principal.user_id,
            "username": principal.username,
            "display_name": principal.display_name,
            "status": principal.status,
            "feishu_bound": bool(principal.feishu_open_id),
        },
        "roles": sorted(principal.role_codes),
        "permissions": principal.permission_codes(),
        "perm_version": principal.perm_version,
        "must_change_password": principal.must_change_password,
        "support_mode": principal.support_mode,
        "support_target_id": principal.support_target_id,
        "support_target_name": principal.support_target_name,
    }


def audit(
    db,
    action: str,
    resource_type: str,
    actor_user_id: int | None = None,
    target_user_id: int | None = None,
    resource_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
) -> None:
    """审计（best-effort；失败只告警，不影响主链路）。"""
    import json
    try:
        db.execute(text("""
            INSERT INTO audit_log
                (actor_user_id, target_user_id, action, resource_type, resource_id,
                 before, after, ip, user_agent, request_id)
            VALUES
                (:actor, :target, :action, :rtype, :rid,
                 CAST(:before AS JSONB), CAST(:after AS JSONB), :ip, :ua, :request_id)
        """), {
            "actor": actor_user_id,
            "target": target_user_id,
            "action": action,
            "rtype": resource_type,
            "rid": resource_id,
            "before": json.dumps(before, ensure_ascii=False) if before is not None else None,
            "after": json.dumps(after, ensure_ascii=False) if after is not None else None,
            "ip": safe_ip(ip),
            "ua": (user_agent or "")[:256],
            "request_id": request_id,
        })
        db.commit()
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning(f"[audit] 写入失败（不影响主链路）: {str(e)[:160]}")
