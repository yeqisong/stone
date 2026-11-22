"""认证与授权依赖。

- JWT 只携带 user_id/token_version/perm_version；
- 每个请求从 DB 加载 Principal（角色、授权、支持会话）；
- 路由权限由 route_permissions 统一解析，未知路由默认拒绝。
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from app.auth.service import (
    Principal,
    authenticate,
    create_token as _create_token,
    decode_token,
    load_principal,
    principal_payload,
)
from app.auth.route_permissions import resolve_rule

security = HTTPBearer(auto_error=False)

CHANGE_PASSWORD_ALLOWED = {"/api/me", "/api/me/password", "/api/login", "/health"}

# 支持模式不可读：个人配置、Agent 会话、身份/设置类资源
SUPPORT_BLOCKED_PREFIXES = (
    "/api/me/settings",
    "/api/risk/rules",
    "/api/risk/intraday-rules",
    "/api/portfolio/trades/discipline/rules",
    "/api/settings",
    "/api/llm_config",
    "/api/agent",
)


def _extract_principal(request: Request,
                       credentials: HTTPAuthorizationCredentials | None) -> Principal:
    if credentials is None:
        raise HTTPException(status_code=401, detail="请提供认证 Token")
    try:
        payload = decode_token(credentials.credentials)
    except JWTError:
        raise HTTPException(status_code=401, detail="无效或过期的 Token")

    raw_uid = payload.get("uid", payload.get("sub"))
    try:
        user_id = int(raw_uid)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="无效的 Token 载荷")

    support_token = request.headers.get("X-Support-Session") or None
    principal = load_principal(user_id, support_token=support_token)
    if principal is None:
        raise HTTPException(status_code=401, detail="用户不存在、已禁用或支持会话无效")
    if int(payload.get("token_version", 0)) != principal.token_version:
        raise HTTPException(status_code=401, detail="Token 已失效，请重新登录")
    return principal


def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Principal:
    """当前请求的 Principal；同一次请求内缓存到 request.state。"""
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    principal = _extract_principal(request, credentials)
    request.state.principal = principal
    return principal


# 兼容旧代码名称；返回值已从 str 升级为 Principal
get_current_user = get_principal
optional_auth = get_principal


def require_perm(code: str):
    """端点级权限依赖：require_perm('portfolio.position.edit')。"""
    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.can(code):
            raise HTTPException(status_code=403, detail=f"无权限: {code}")
        return principal
    return _dep


def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if "admin" not in principal.role_codes:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return principal


def _credentials_from_request(request: Request) -> HTTPAuthorizationCredentials | None:
    """手动解析 Authorization；供 HTTP 中间件使用（WebSocket 不走这里）。"""
    raw = request.headers.get("authorization", "")
    if raw.lower().startswith("bearer "):
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=raw[7:].strip())
    return None


def authorize_http_request(request: Request) -> Principal | None:
    """HTTP 请求级授权核心：public 放行；其余按路由注册表校验权限。"""
    path = request.url.path
    # 静态文件/文档/健康检查等非 /api 路径不参与 RBAC
    if not (path.startswith("/api") or path in ("/health", "/webhook/feishu")):
        return None
    rule = resolve_rule(request.method, path)
    if rule is None:
        raise HTTPException(status_code=403, detail="该路由未配置权限")
    if rule.public:
        return None

    principal = _extract_principal(request, _credentials_from_request(request))
    if principal.must_change_password and request.url.path not in CHANGE_PASSWORD_ALLOWED:
        raise HTTPException(status_code=403, detail="请先修改初始密码")

    if principal.support_mode:
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            raise HTTPException(status_code=403, detail="支持模式只读")
        if any(request.url.path.startswith(p) for p in SUPPORT_BLOCKED_PREFIXES):
            raise HTTPException(status_code=403, detail="该资源不支持支持模式")

    if rule.permission and not principal.can(rule.permission):
        raise HTTPException(status_code=403, detail=f"无权限: {rule.permission}")

    request.state.principal = principal
    return principal


def authorize_request(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Principal | None:
    """兼容旧依赖用法；应用入口已改为 HTTP 中间件（避免命中 WebSocket）。"""
    return authorize_http_request(request)


def create_token(username_or_id, **kwargs) -> str:
    """兼容旧调用：传 username 或 user_id。"""
    if isinstance(username_or_id, int):
        return _create_token(username_or_id, **kwargs)
    username = str(username_or_id)
    if username.isdigit():
        return _create_token(int(username), username=username, **kwargs)

    from sqlalchemy import text
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        row = db.execute(text(
            "SELECT id, username FROM users WHERE username = :u LIMIT 1"
        ), {"u": username}).fetchone()
        if not row:
            raise ValueError(f"用户不存在: {username}")
        return _create_token(int(row[0]), username=row[1], **kwargs)
    finally:
        db.close()


def verify_token(token: str) -> dict:
    try:
        return decode_token(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="无效或过期的 Token")


def verify_password(username: str, password: str) -> bool:
    principal, _ = authenticate(username, password)
    return principal is not None


__all__ = [
    "Principal", "security", "get_principal", "get_current_user", "optional_auth",
    "require_perm", "require_admin", "authorize_request", "create_token",
    "verify_token", "verify_password", "principal_payload",
]
