"""RBAC 种子：权限节点、系统角色、角色授权、种子管理员。

幂等：每次启动可重复执行；不删除自定义角色/用户授权。
"""
from __future__ import annotations

import secrets

from loguru import logger
from sqlalchemy import text

from app.auth.definitions import PERMISSION_NODES, ROLE_DEFS, ROLE_PERMISSIONS
from app.auth.security import hash_password
from app.config import settings

ADMIN_USERNAME = "admin"


def _upsert_roles(db) -> None:
    for idx, role in enumerate(ROLE_DEFS):
        db.execute(text("""
            INSERT INTO roles (code, name, description, is_system, data_scope)
            VALUES (:code, :name, :description, :is_system, :data_scope)
            ON CONFLICT (code) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                is_system = EXCLUDED.is_system,
                data_scope = EXCLUDED.data_scope,
                updated_at = CURRENT_TIMESTAMP
        """), {**role, "idx": idx})


def _upsert_permissions(db) -> None:
    for idx, node in enumerate(PERMISSION_NODES):
        db.execute(text("""
            INSERT INTO permissions
                (code, name, type, parent_id, route, api_pattern, sort_order,
                 is_system, is_public, description)
            VALUES
                (:code, :name, :type, NULL, :route, :api_pattern, :sort_order,
                 true, :is_public, :description)
            ON CONFLICT (code) DO UPDATE SET
                name = EXCLUDED.name,
                type = EXCLUDED.type,
                route = EXCLUDED.route,
                api_pattern = EXCLUDED.api_pattern,
                sort_order = EXCLUDED.sort_order,
                is_public = EXCLUDED.is_public,
                description = EXCLUDED.description,
                updated_at = CURRENT_TIMESTAMP
        """), {
            "code": node["code"],
            "name": node["name"],
            "type": node["type"],
            "route": node.get("route") or None,
            "api_pattern": (node.get("api") or "")[:256] or None,
            "sort_order": idx * 10,
            "is_public": node["code"].startswith("public."),
            "description": (node.get("note") or "")[:200],
        })

    # 第二遍回填 parent_id（权限码 -> id）
    for node in PERMISSION_NODES:
        parent = node.get("parent")
        if not parent:
            continue
        db.execute(text("""
            UPDATE permissions
            SET parent_id = (SELECT id FROM permissions WHERE code = :parent)
            WHERE code = :code AND (SELECT id FROM permissions WHERE code = :parent) IS NOT NULL
        """), {"parent": parent, "code": node["code"]})


def _upsert_role_permissions(db) -> None:
    for role_code, grants in ROLE_PERMISSIONS.items():
        for code, scope, support_only in grants:
            db.execute(text("""
                INSERT INTO role_permissions (role_id, permission_id, scope, support_only)
                SELECT r.id, p.id, :scope, :support_only
                FROM roles r, permissions p
                WHERE r.code = :role AND p.code = :perm
                ON CONFLICT (role_id, permission_id) DO UPDATE SET
                    scope = EXCLUDED.scope,
                    support_only = EXCLUDED.support_only
            """), {
                "role": role_code,
                "perm": code,
                "scope": scope,
                "support_only": bool(support_only),
            })


def _ensure_admin(db) -> int:
    row = db.execute(text(
        "SELECT id FROM users WHERE username = :u LIMIT 1"
    ), {"u": ADMIN_USERNAME}).fetchone()
    if row:
        admin_id = int(row[0])
    else:
        password = settings.LOGIN_PASSWORD or secrets.token_urlsafe(12)
        must_change = not bool(settings.LOGIN_PASSWORD)
        admin_id = db.execute(text("""
            INSERT INTO users
                (username, password_hash, display_name, status, must_change_password)
            VALUES (:u, :p, :d, 'active', :m)
            RETURNING id
        """), {
            "u": ADMIN_USERNAME,
            "p": hash_password(password),
            "d": "管理员",
            "m": must_change,
        }).scalar()
        if must_change:
            logger.warning(
                "[auth] 已创建种子管理员 admin，一次性密码=%s（首次登录必须修改）", password
            )
        else:
            logger.info("[auth] 已创建种子管理员 admin（密码来自 LOGIN_PASSWORD）")

    db.execute(text("""
        INSERT INTO user_roles (user_id, role_id)
        SELECT :uid, id FROM roles WHERE code = 'admin'
        ON CONFLICT (user_id, role_id) DO NOTHING
    """), {"uid": admin_id})
    return admin_id


def seed_auth(db) -> int:
    """播种权限/角色/admin 用户；返回 admin user_id。"""
    _upsert_roles(db)
    _upsert_permissions(db)
    _upsert_role_permissions(db)
    return _ensure_admin(db)
