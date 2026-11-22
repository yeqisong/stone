"""密码哈希与校验。

使用 passlib + bcrypt；密码长度超过 bcrypt 72 字节上限时拒绝（由调用方返回 422）。
"""
from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    """生成 bcrypt 哈希。"""
    raw = (password or "").encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise ValueError("密码过长（最多 72 字节）")
    return _pwd_context.hash(password)


def verify_password_hash(password: str, password_hash: str) -> bool:
    """校验明文与 bcrypt 哈希；异常时返回 False。"""
    try:
        return _pwd_context.verify(password or "", password_hash or "")
    except Exception:
        return False
