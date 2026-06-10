"""认证模块：JWT Token 签发与校验。单用户场景，密码从环境变量读取。"""
from datetime import datetime, timedelta
from jose import jwt, JWTError
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import settings

security = HTTPBearer(auto_error=False)


def create_token(username: str) -> str:
    """签发 JWT Token，有效期 24 小时。"""
    payload = {
        "sub": username,
        "exp": datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRE_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, settings.APP_SECRET_KEY, algorithm="HS256")


def verify_token(token: str) -> dict:
    """校验 JWT Token，返回 payload 或抛异常。"""
    try:
        payload = jwt.decode(token, settings.APP_SECRET_KEY, algorithms=["HS256"])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="无效或过期的 Token")


def verify_password(username: str, password: str) -> bool:
    """校验用户名密码。"""
    return username == settings.LOGIN_USERNAME and password == settings.LOGIN_PASSWORD


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """获取当前认证用户。无 Token 返回 401。"""
    if credentials is None:
        raise HTTPException(status_code=401, detail="请提供认证 Token")
    payload = verify_token(credentials.credentials)
    return payload.get("sub", "unknown")


async def optional_auth(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str | None:
    """可选认证：dev 环境无 Token 放行，prod 环境强制。"""
    if settings.APP_ENV == "dev" and credentials is None:
        return "dev_user"
    if credentials is None:
        raise HTTPException(status_code=401, detail="请提供认证 Token")
    return await get_current_user(credentials)
