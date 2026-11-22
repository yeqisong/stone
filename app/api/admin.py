"""后台管理 API：用户、角色/权限、审计、支持会话。"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth.auth import Principal, get_principal
from app.auth.definitions import PERSONAL_PERMISSION_CODES
from app.auth.security import hash_password
from app.auth.service import audit, safe_ip
from app.db.connection import get_sync_db

router = APIRouter(prefix="/admin", tags=["admin"])


def _actor(principal: Principal) -> int:
    return principal.user_id


def _require_roles_exist(db, role_ids: list[int]) -> list[str]:
    if not role_ids:
        return []
    rows = db.execute(text(
        "SELECT id, code FROM roles WHERE id = ANY(:ids) AND status='active'"
    ), {"ids": role_ids}).fetchall()
    if len(rows) != len(set(role_ids)):
        raise HTTPException(400, "包含不存在的角色")
    return [r[1] for r in rows]


def _actor_permissions(db, user_id: int) -> set[str]:
    rows = db.execute(text("""
        SELECT DISTINCT p.code
        FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id AND r.status='active'
        JOIN role_permissions rp ON rp.role_id = r.id
        JOIN permissions p ON p.id = rp.permission_id
        WHERE ur.user_id = :uid
          AND (ur.expires_at IS NULL OR ur.expires_at > CURRENT_TIMESTAMP)
    """), {"uid": user_id}).fetchall()
    return {r[0] for r in rows}


def _assert_can_grant(actor: Principal, role_codes: list[str], db) -> None:
    """防提权：只能授予自己拥有的权限（admin 拥有全部）。"""
    if "admin" in actor.role_codes:
        return
    actor_perms = _actor_permissions(db, actor.user_id)
    if not role_codes:
        return
    rows = db.execute(text("""
        SELECT DISTINCT p.code
        FROM roles r
        JOIN role_permissions rp ON rp.role_id = r.id
        JOIN permissions p ON p.id = rp.permission_id
        WHERE r.code = ANY(:codes)
    """), {"codes": role_codes}).fetchall()
    missing = {r[0] for r in rows} - actor_perms
    if missing:
        raise HTTPException(403, f"不能授予自己没有的权限: {sorted(missing)[:5]}")


def _active_admin_count(db, exclude_user_id: int | None = None) -> int:
    sql = """
        SELECT count(DISTINCT u.id)
        FROM users u
        JOIN user_roles ur ON ur.user_id = u.id
        JOIN roles r ON r.id = ur.role_id
        WHERE u.status='active' AND r.code='admin'
    """
    params = {}
    if exclude_user_id is not None:
        sql += " AND u.id <> :ex"
        params["ex"] = exclude_user_id
    return int(db.execute(text(sql), params).scalar() or 0)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8)
    display_name: str = ""
    role_codes: list[str] = Field(default_factory=lambda: ["trader"])
    must_change_password: bool = True


class StatusBody(BaseModel):
    status: str  # active / disabled


class RolesBody(BaseModel):
    role_codes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


class OverrideBody(BaseModel):
    effect: str = "deny"  # allow / deny
    scope: str = "self"
    conditions: dict = Field(default_factory=dict)
    reason: str = ""
    expires_at: datetime | None = None


class RoleCreate(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=1, max_length=32)
    description: str = ""
    data_scope: str = "self"


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None


class RolePermItem(BaseModel):
    code: str
    scope: str = "self"
    support_only: bool = False
    conditions: dict = Field(default_factory=dict)


class RolePermsBody(BaseModel):
    permissions: list[RolePermItem] = Field(default_factory=list)


class SupportSessionBody(BaseModel):
    target_user_id: int
    reason: str = Field(min_length=10, max_length=200)
    ttl_minutes: int = Field(default=30, ge=1, le=60)


# ── 用户 ──────────────────────────────────────────────────────────────

@router.get("/users")
def list_users(q: str = "", status: str = "", limit: int = Query(100, ge=1, le=500),
               offset: int = Query(0, ge=0), principal: Principal = Depends(get_principal)):
    db = get_sync_db()
    try:
        where, params = ["1=1"], {"lim": limit, "off": offset}
        if q:
            where.append("(u.username ILIKE :q OR u.display_name ILIKE :q)")
            params["q"] = f"%{q}%"
        if status:
            where.append("u.status = :status")
            params["status"] = status
        rows = db.execute(text(f"""
            SELECT u.id, u.username, u.display_name, u.status, u.must_change_password,
                   u.last_login_at, u.created_at,
                   COALESCE(array_agg(DISTINCT r.code) FILTER (WHERE r.code IS NOT NULL), '{{}}') AS roles
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.id
            LEFT JOIN roles r ON r.id = ur.role_id
            WHERE {' AND '.join(where)}
            GROUP BY u.id
            ORDER BY u.id
            LIMIT :lim OFFSET :off
        """), params).fetchall()
        total = db.execute(text(
            f"SELECT count(*) FROM users u WHERE {' AND '.join(where)}"
        ), {k: v for k, v in params.items() if k not in ("lim", "off")}).scalar()
        return {"items": [{
            "id": r[0], "username": r[1], "display_name": r[2], "status": r[3],
            "must_change_password": bool(r[4]),
            "last_login_at": str(r[5])[:19] if r[5] else None,
            "created_at": str(r[6])[:19] if r[6] else None,
            "roles": list(r[7] or []),
        } for r in rows], "total": int(total or 0)}
    finally:
        db.close()


@router.post("/users")
def create_user(body: UserCreate, request: Request, principal: Principal = Depends(get_principal)):
    db = get_sync_db()
    try:
        if db.execute(text("SELECT 1 FROM users WHERE username=:u"), {"u": body.username}).fetchone():
            raise HTTPException(409, "用户名已存在")
        _assert_can_grant(principal, body.role_codes, db)
        role_ids = db.execute(text(
            "SELECT id FROM roles WHERE code = ANY(:codes) AND status='active'"
        ), {"codes": body.role_codes}).fetchall()
        if len(role_ids) != len(set(body.role_codes)):
            raise HTTPException(400, "包含不存在的角色")
        try:
            pwd_hash = hash_password(body.password)
        except ValueError as e:
            raise HTTPException(422, str(e))
        uid = db.execute(text("""
            INSERT INTO users (username, password_hash, display_name, status,
                               must_change_password, created_by)
            VALUES (:u, :p, :d, 'active', :m, :by) RETURNING id
        """), {"u": body.username, "p": pwd_hash, "d": body.display_name,
               "m": body.must_change_password, "by": principal.user_id}).scalar()
        for (rid,) in role_ids:
            db.execute(text(
                "INSERT INTO user_roles (user_id, role_id, assigned_by) VALUES (:u,:r,:by)"
            ), {"u": uid, "r": rid, "by": principal.user_id})
        # 每个新用户自带默认“自选”组
        db.execute(text("""
            INSERT INTO watch_groups (user_id, group_name, is_default, sort_order)
            VALUES (:u, '自选', true, 0)
            ON CONFLICT (user_id, group_name) DO NOTHING
        """), {"u": uid})
        db.commit()
        audit(db, "user.create", "user", principal.user_id, uid, str(uid),
              after={"username": body.username, "roles": body.role_codes},
              ip=request.client.host if request.client else None,
              user_agent=request.headers.get("user-agent"))
        return {"ok": True, "id": uid}
    finally:
        db.close()


@router.patch("/users/{user_id}/status")
def set_user_status(user_id: int, body: StatusBody, request: Request,
                    principal: Principal = Depends(get_principal)):
    if body.status not in ("active", "disabled"):
        raise HTTPException(422, "status 只能是 active/disabled")
    db = get_sync_db()
    try:
        row = db.execute(text("SELECT id, username FROM users WHERE id=:id"), {"id": user_id}).fetchone()
        if not row:
            raise HTTPException(404, "用户不存在")
        if body.status == "disabled" and user_id != principal.user_id:
            if _active_admin_count(db, exclude_user_id=user_id) == 0:
                raise HTTPException(409, "不能禁用最后一个管理员")
        if body.status == "disabled" and user_id == principal.user_id:
            raise HTTPException(409, "不能禁用自己")
        db.execute(text("""
            UPDATE users SET status=:s, token_version=token_version+1,
                   updated_at=CURRENT_TIMESTAMP
            WHERE id=:id
        """), {"s": body.status, "id": user_id})
        db.commit()
        audit(db, "user.status", "user", principal.user_id, user_id, str(user_id),
              after={"status": body.status},
              ip=request.client.host if request.client else None,
              user_agent=request.headers.get("user-agent"))
        return {"ok": True}
    finally:
        db.close()


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, request: Request, principal: Principal = Depends(get_principal)):
    db = get_sync_db()
    try:
        if not db.execute(text("SELECT 1 FROM users WHERE id=:id"), {"id": user_id}).fetchone():
            raise HTTPException(404, "用户不存在")
        one_time = secrets.token_urlsafe(12)
        db.execute(text("""
            UPDATE users SET password_hash=:p, must_change_password=true,
                   token_version=token_version+1, updated_at=CURRENT_TIMESTAMP
            WHERE id=:id
        """), {"p": hash_password(one_time), "id": user_id})
        db.commit()
        audit(db, "user.reset_password", "user", principal.user_id, user_id, str(user_id),
              ip=request.client.host if request.client else None,
              user_agent=request.headers.get("user-agent"))
        return {"ok": True, "one_time_password": one_time}
    finally:
        db.close()


@router.put("/users/{user_id}/roles")
def set_user_roles(user_id: int, body: RolesBody, request: Request,
                   principal: Principal = Depends(get_principal)):
    db = get_sync_db()
    try:
        if not db.execute(text("SELECT 1 FROM users WHERE id=:id"), {"id": user_id}).fetchone():
            raise HTTPException(404, "用户不存在")
        _assert_can_grant(principal, body.role_codes, db)
        rows = db.execute(text(
            "SELECT id, code FROM roles WHERE code = ANY(:codes) AND status='active'"
        ), {"codes": body.role_codes}).fetchall()
        if len(rows) != len(set(body.role_codes)):
            raise HTTPException(400, "包含不存在的角色")
        if "admin" not in {r[1] for r in rows}:
            is_admin = db.execute(text("""
                SELECT 1 FROM user_roles ur JOIN roles r ON r.id=ur.role_id
                WHERE ur.user_id=:u AND r.code='admin'
            """), {"u": user_id}).fetchone()
            if is_admin and _active_admin_count(db, exclude_user_id=user_id) == 0:
                raise HTTPException(409, "不能移除最后一个管理员")
        db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": user_id})
        for rid, _ in rows:
            db.execute(text("""
                INSERT INTO user_roles (user_id, role_id, assigned_by, expires_at)
                VALUES (:u,:r,:by,:exp)
            """), {"u": user_id, "r": rid, "by": principal.user_id, "exp": body.expires_at})
        db.commit()
        audit(db, "user.roles", "user", principal.user_id, user_id, str(user_id),
              after={"roles": body.role_codes},
              ip=request.client.host if request.client else None,
              user_agent=request.headers.get("user-agent"))
        return {"ok": True}
    finally:
        db.close()


@router.get("/users/{user_id}/permissions")
def list_user_overrides(user_id: int, principal: Principal = Depends(get_principal)):
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT p.code, o.effect, o.scope, o.conditions, o.reason, o.expires_at
            FROM user_permission_overrides o JOIN permissions p ON p.id=o.permission_id
            WHERE o.user_id=:u ORDER BY p.code
        """), {"u": user_id}).fetchall()
        return {"items": [{"permission": r[0], "effect": r[1], "scope": r[2],
                           "conditions": r[3], "reason": r[4],
                           "expires_at": str(r[5])[:19] if r[5] else None} for r in rows]}
    finally:
        db.close()


@router.put("/users/{user_id}/permissions/{permission_code}")
def set_user_override_by_code(user_id: int, permission_code: str, body: OverrideBody,
                              request: Request, principal: Principal = Depends(get_principal)):
    if body.effect not in ("allow", "deny"):
        raise HTTPException(422, "effect 只能是 allow/deny")
    if body.scope not in ("self", "team", "global"):
        raise HTTPException(422, "scope 无效")
    db = get_sync_db()
    try:
        perm = db.execute(text("SELECT id, code FROM permissions WHERE code=:c"),
                          {"c": permission_code}).fetchone()
        if not perm:
            raise HTTPException(404, "权限不存在")
        scope = body.scope
        if perm[1] in PERSONAL_PERMISSION_CODES:
            scope = "self"
        _assert_can_grant(principal, [], db)  # admin/all; 非 admin 只能操作自己有权限的
        if "admin" not in principal.role_codes and not principal.can(perm[1]):
            raise HTTPException(403, "不能操作自己没有的权限")
        db.execute(text("""
            INSERT INTO user_permission_overrides
                (user_id, permission_id, effect, scope, conditions, reason, expires_at, created_by)
            VALUES (:u,:p,:e,:s,CAST(:c AS JSONB),:r,:exp,:by)
            ON CONFLICT (user_id, permission_id) DO UPDATE SET
                effect=EXCLUDED.effect, scope=EXCLUDED.scope, conditions=EXCLUDED.conditions,
                reason=EXCLUDED.reason, expires_at=EXCLUDED.expires_at
        """), {
            "u": user_id, "p": perm[0], "e": body.effect, "s": scope,
            "c": __import__("json").dumps(body.conditions or {}, ensure_ascii=False),
            "r": body.reason, "exp": body.expires_at, "by": principal.user_id,
        })
        db.execute(text("UPDATE users SET perm_version=perm_version+1 WHERE id=:u"), {"u": user_id})
        db.commit()
        audit(db, "user.permission_override", "user", principal.user_id, user_id,
              permission_code, after={"effect": body.effect, "scope": scope},
              ip=request.client.host if request.client else None)
        return {"ok": True}
    finally:
        db.close()


@router.delete("/users/{user_id}/permissions/{permission_code}")
def delete_user_override(user_id: int, permission_code: str, request: Request,
                         principal: Principal = Depends(get_principal)):
    db = get_sync_db()
    try:
        perm = db.execute(text("SELECT id FROM permissions WHERE code=:c"),
                          {"c": permission_code}).fetchone()
        if not perm:
            raise HTTPException(404, "权限不存在")
        db.execute(text(
            "DELETE FROM user_permission_overrides WHERE user_id=:u AND permission_id=:p"
        ), {"u": user_id, "p": perm[0]})
        db.execute(text("UPDATE users SET perm_version=perm_version+1 WHERE id=:u"), {"u": user_id})
        db.commit()
        audit(db, "user.permission_override_delete", "user", principal.user_id, user_id,
              permission_code, ip=request.client.host if request.client else None)
        return {"ok": True}
    finally:
        db.close()


# ── 角色 / 权限 ──────────────────────────────────────────────────────

@router.get("/roles")
def list_roles(principal: Principal = Depends(get_principal)):
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT r.id, r.code, r.name, r.description, r.is_system, r.data_scope, r.status,
                   (SELECT count(*) FROM role_permissions rp
                      JOIN permissions p ON p.id = rp.permission_id
                     WHERE rp.role_id = r.id
                       AND p.is_public = false AND p.code <> 'ws.connect') AS n
            FROM roles r
            ORDER BY r.is_system DESC, r.id
        """)).fetchall()
        return {"items": [{
            "id": r[0], "code": r[1], "name": r[2], "description": r[3],
            "is_system": bool(r[4]), "data_scope": r[5], "status": r[6],
            "permission_count": int(r[7] or 0),
        } for r in rows]}
    finally:
        db.close()


@router.post("/roles")
def create_role(body: RoleCreate, request: Request, principal: Principal = Depends(get_principal)):
    if body.data_scope not in ("self", "team"):
        raise HTTPException(422, "data_scope 只能是 self/team")
    db = get_sync_db()
    try:
        if db.execute(text("SELECT 1 FROM roles WHERE code=:c"), {"c": body.code}).fetchone():
            raise HTTPException(409, "角色码已存在")
        rid = db.execute(text("""
            INSERT INTO roles (code,name,description,is_system,data_scope)
            VALUES (:c,:n,:d,false,:s) RETURNING id
        """), {"c": body.code, "n": body.name, "d": body.description, "s": body.data_scope}).scalar()
        db.commit()
        audit(db, "role.create", "role", principal.user_id, None, str(rid),
              after={"code": body.code}, ip=request.client.host if request.client else None)
        return {"ok": True, "id": rid}
    finally:
        db.close()


@router.put("/roles/{role_id}")
def update_role(role_id: int, body: RoleUpdate, request: Request,
                principal: Principal = Depends(get_principal)):
    db = get_sync_db()
    try:
        row = db.execute(text("SELECT code,is_system FROM roles WHERE id=:id"), {"id": role_id}).fetchone()
        if not row:
            raise HTTPException(404, "角色不存在")
        sets, params = [], {"id": role_id}
        if body.name is not None:
            sets.append("name=:n"); params["n"] = body.name
        if body.description is not None:
            sets.append("description=:d"); params["d"] = body.description
        if body.status is not None:
            if body.status not in ("active", "disabled"):
                raise HTTPException(422, "status 无效")
            sets.append("status=:s"); params["s"] = body.status
        if sets:
            sets.append("updated_at=CURRENT_TIMESTAMP")
            db.execute(text(f"UPDATE roles SET {', '.join(sets)} WHERE id=:id"), params)
            db.execute(text("UPDATE users SET perm_version=perm_version+1 WHERE id IN (SELECT user_id FROM user_roles WHERE role_id=:id)"), {"id": role_id})
            db.commit()
        audit(db, "role.update", "role", principal.user_id, None, str(role_id),
              after=body.model_dump(exclude_none=True),
              ip=request.client.host if request.client else None)
        return {"ok": True}
    finally:
        db.close()


@router.delete("/roles/{role_id}")
def delete_role(role_id: int, request: Request, principal: Principal = Depends(get_principal)):
    db = get_sync_db()
    try:
        row = db.execute(text("SELECT code,is_system FROM roles WHERE id=:id"), {"id": role_id}).fetchone()
        if not row:
            raise HTTPException(404, "角色不存在")
        if row[1]:
            raise HTTPException(400, "系统角色不可删除")
        if db.execute(text("SELECT 1 FROM user_roles WHERE role_id=:id LIMIT 1"), {"id": role_id}).fetchone():
            raise HTTPException(409, "角色仍被用户使用")
        db.execute(text("DELETE FROM roles WHERE id=:id"), {"id": role_id})
        db.commit()
        audit(db, "role.delete", "role", principal.user_id, None, str(role_id),
              before={"code": row[0]}, ip=request.client.host if request.client else None)
        return {"ok": True}
    finally:
        db.close()


@router.get("/roles/{role_id}/permissions")
def get_role_permissions(role_id: int, principal: Principal = Depends(get_principal)):
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT p.code, rp.scope, rp.support_only, rp.conditions
            FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id
            WHERE rp.role_id=:id ORDER BY p.code
        """), {"id": role_id}).fetchall()
        return {"items": [{"code": r[0], "scope": r[1], "support_only": bool(r[2]),
                           "conditions": r[3]} for r in rows]}
    finally:
        db.close()


@router.put("/roles/{role_id}/permissions")
def set_role_permissions(role_id: int, body: RolePermsBody, request: Request,
                         principal: Principal = Depends(get_principal)):
    db = get_sync_db()
    try:
        role = db.execute(text("SELECT code FROM roles WHERE id=:id"), {"id": role_id}).fetchone()
        if not role:
            raise HTTPException(404, "角色不存在")
        # 防提权
        codes = [p.code for p in body.permissions]
        _assert_can_grant(principal, [role[0]], db)
        perm_rows = db.execute(text(
            "SELECT id, code FROM permissions WHERE code = ANY(:codes)"
        ), {"codes": codes}).fetchall()
        if len(perm_rows) != len(set(codes)):
            raise HTTPException(400, "包含不存在的权限")
        # public / ws.connect 是全局接入能力，不应作为角色功能节点分配
        public_codes = db.execute(text("""
            SELECT code FROM permissions
            WHERE code = ANY(:codes) AND (is_public = true OR code = 'ws.connect')
        """), {"codes": codes}).fetchall()
        if public_codes:
            raise HTTPException(400, f"不可分配公共接入权限: {', '.join(r[0] for r in public_codes)}")
        # 个人节点强制 self
        db.execute(text("DELETE FROM role_permissions WHERE role_id=:id"), {"id": role_id})
        for p in body.permissions:
            code = p.code
            scope = "self" if code in PERSONAL_PERMISSION_CODES else (p.scope or "global")
            db.execute(text("""
                INSERT INTO role_permissions (role_id, permission_id, scope, support_only, conditions)
                VALUES (:r,(SELECT id FROM permissions WHERE code=:c),:s,:so,CAST(:cond AS JSONB))
            """), {"r": role_id, "c": code, "s": scope, "so": p.support_only,
                   "cond": __import__("json").dumps(p.conditions or {}, ensure_ascii=False)})
        db.execute(text("UPDATE users SET perm_version=perm_version+1 WHERE id IN (SELECT user_id FROM user_roles WHERE role_id=:id)"), {"id": role_id})
        db.commit()
        audit(db, "role.permissions", "role", principal.user_id, None, str(role_id),
              after={"permissions": codes},
              ip=request.client.host if request.client else None)
        return {"ok": True}
    finally:
        db.close()


@router.get("/permissions")
def list_permissions(principal: Principal = Depends(get_principal)):
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT id, code, name, type, parent_id, route, sort_order, is_public
            FROM permissions ORDER BY sort_order, id
        """)).fetchall()
        return {"items": [{
            "id": r[0], "code": r[1], "name": r[2], "type": r[3],
            "parent_id": r[4], "route": r[5], "sort_order": r[6],
            "is_public": bool(r[7]),
        } for r in rows]}
    finally:
        db.close()


# ── 审计 ──────────────────────────────────────────────────────────────

@router.get("/audit")
def list_audit(action: str = "", actor: str = "", resource: str = "",
               limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
               principal: Principal = Depends(get_principal)):
    db = get_sync_db()
    try:
        where, params = ["1=1"], {"lim": limit, "off": offset}
        if action:
            where.append("a.action = :action"); params["action"] = action
        if actor:
            where.append("(u.username ILIKE :actor OR CAST(a.actor_user_id AS TEXT)=:actor)")
            params["actor"] = f"%{actor}%"
        if resource:
            where.append("a.resource_type = :resource"); params["resource"] = resource
        rows = db.execute(text(f"""
            SELECT a.id, a.created_at, a.actor_user_id, u.username,
                   a.target_user_id, a.action, a.resource_type, a.resource_id,
                   a.before, a.after, a.ip
            FROM audit_log a LEFT JOIN users u ON u.id=a.actor_user_id
            WHERE {' AND '.join(where)}
            ORDER BY a.id DESC LIMIT :lim OFFSET :off
        """), params).fetchall()
        return {"items": [{
            "id": r[0], "created_at": str(r[1])[:19], "actor_user_id": r[2],
            "actor": r[3], "target_user_id": r[4], "action": r[5],
            "resource_type": r[6], "resource_id": r[7],
            "before": r[8], "after": r[9], "ip": str(r[10]) if r[10] else None,
        } for r in rows]}
    finally:
        db.close()


# ── 支持会话 ──────────────────────────────────────────────────────────

@router.post("/support/sessions")
def create_support_session(body: SupportSessionBody, request: Request,
                           principal: Principal = Depends(get_principal)):
    if body.target_user_id == principal.user_id:
        raise HTTPException(409, "不能对自己创建支持会话")
    db = get_sync_db()
    try:
        target = db.execute(text(
            "SELECT username, status FROM users WHERE id=:id"
        ), {"id": body.target_user_id}).fetchone()
        if not target or target[1] != "active":
            raise HTTPException(404, "目标用户不存在或不可用")
        raw = secrets.token_urlsafe(32)
        h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        expires = datetime.now() + timedelta(minutes=body.ttl_minutes)
        sid = db.execute(text("""
            INSERT INTO support_sessions
                (admin_user_id, target_user_id, reason, token_hash, expires_at, created_ip)
            VALUES (:a,:t,:r,:h,:e,:ip) RETURNING id
        """), {"a": principal.user_id, "t": body.target_user_id, "r": body.reason,
               "h": h, "e": expires,
               "ip": safe_ip(request.client.host if request.client else None)}).scalar()
        db.commit()
        audit(db, "admin.support.create", "support_session", principal.user_id,
              body.target_user_id, str(sid), after={"reason": body.reason, "ttl": body.ttl_minutes},
              ip=request.client.host if request.client else None)
        return {"ok": True, "id": sid, "support_token": raw,
                "expires_at": expires.isoformat(), "target": target[0]}
    finally:
        db.close()


@router.get("/support/sessions")
def list_support_sessions(principal: Principal = Depends(get_principal)):
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT s.id, s.target_user_id, u.username, s.reason, s.created_at,
                   s.expires_at, s.revoked_at, s.access_count
            FROM support_sessions s JOIN users u ON u.id=s.target_user_id
            WHERE s.admin_user_id=:a ORDER BY s.id DESC LIMIT 50
        """), {"a": principal.user_id}).fetchall()
        return {"items": [{
            "id": r[0], "target_user_id": r[1], "target": r[2], "reason": r[3],
            "created_at": str(r[4])[:19], "expires_at": str(r[5])[:19],
            "revoked_at": str(r[6])[:19] if r[6] else None,
            "access_count": r[7],
        } for r in rows]}
    finally:
        db.close()


@router.delete("/support/sessions/{session_id}")
def revoke_support_session(session_id: int, request: Request,
                           principal: Principal = Depends(get_principal)):
    db = get_sync_db()
    try:
        row = db.execute(text("""
            SELECT id FROM support_sessions
            WHERE id=:id AND admin_user_id=:a AND revoked_at IS NULL
        """), {"id": session_id, "a": principal.user_id}).fetchone()
        if not row:
            raise HTTPException(404, "会话不存在或已撤销")
        db.execute(text("""
            UPDATE support_sessions SET revoked_at=CURRENT_TIMESTAMP, revoked_by=:by
            WHERE id=:id
        """), {"id": session_id, "by": principal.user_id})
        db.commit()
        audit(db, "admin.support.revoke", "support_session", principal.user_id,
              None, str(session_id), ip=request.client.host if request.client else None)
        return {"ok": True}
    finally:
        db.close()
