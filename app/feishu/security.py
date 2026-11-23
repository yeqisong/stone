"""飞书回调安全：签名校验、事件解密、重放/重复投递抑制。"""
import base64
import hashlib
import hmac
import json
import threading
import time

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from loguru import logger

MAX_SKEW = 600           # 回调时间戳允许偏差（秒）
_REPLAY_TTL = 15 * 60
_mem_seen: dict[str, float] = {}
_mem_rate: dict[str, list[float]] = {}
_mem_lock = threading.Lock()
_redis = None


def _signature_key() -> str:
    """签名/解密密钥：只使用事件订阅 Encrypt Key（App Secret 仅用于换取 token）。"""
    from app.config import settings
    return settings.FEISHU_ENCRYPT_KEY


def verify_signature(raw_body: bytes, timestamp: str, nonce: str,
                     signature: str, key: str | None = None) -> tuple[bool, str]:
    """按飞书事件订阅规范校验签名。

    官方算法：sha256(timestamp + nonce + encrypt_key + raw_body)，不是 HMAC。
    返回 (是否通过, 失败原因)；调用方只在配置了密钥时调用。
    """
    key = key if key is not None else _signature_key()
    if not key:
        return False, "未配置签名密钥"
    if not timestamp or not nonce or not signature:
        return False, "缺少签名请求头"
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False, "时间戳格式错误"
    if ts > 10_000_000_000:      # 兼容毫秒时间戳
        ts //= 1000
    if abs(int(time.time()) - ts) > MAX_SKEW:
        return False, "时间戳超出允许范围"
    body = raw_body.decode("utf-8", errors="replace")
    expected = hashlib.sha256(
        f"{timestamp}{nonce}{key}{body}".encode("utf-8")).hexdigest()
    if not hmac.compare_digest(expected, signature.strip().lower()):
        return False, "签名不匹配"
    return True, ""


def decrypt_payload(encrypted: str, key: str | None = None) -> dict:
    """解密事件订阅 payload（AES-256-CBC；key=sha256(encrypt_key)，iv 为前 16 字节）。"""
    key = key if key is not None else _signature_key()
    if not key:
        raise ValueError("未配置 Encrypt Key")
    try:
        raw = base64.b64decode(encrypted)
        if len(raw) <= 16:
            raise ValueError("密文长度不足")
        key_bytes = hashlib.sha256(key.encode("utf-8")).digest()
        decryptor = Cipher(algorithms.AES(key_bytes), modes.CBC(raw[:16])).decryptor()
        padded = decryptor.update(raw[16:]) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        plain = unpadder.update(padded) + unpadder.finalize()
        data = json.loads(plain.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("解密结果不是 JSON 对象")
        return data
    except Exception as e:
        raise ValueError(f"事件解密失败: {str(e)[:120]}") from e


def _redis_client():
    global _redis
    from app.config import settings
    if settings.APP_ENV != "prod" or not settings.REDIS_URL:
        return None
    if _redis is None:
        try:
            import redis
            _redis = redis.Redis.from_url(
                settings.REDIS_URL, password=settings.REDIS_PASSWORD or None,
                decode_responses=True, socket_connect_timeout=1,
                socket_timeout=1)
            _redis.ping()
        except Exception as e:
            logger.warning(f"[feishu] Redis 不可用，去重退回进程内: {str(e)[:120]}")
            _redis = False
    return _redis or None


def claim_once(key: str, ttl: int = _REPLAY_TTL) -> bool:
    """原子领取一次性事件键。True=首次，False=重复投递。"""
    if not key:
        return True
    store = _redis_client()
    if store:
        try:
            return bool(store.set(f"feishu:evt:{key}", "1", nx=True, ex=ttl))
        except Exception as e:
            logger.warning(f"[feishu] Redis 去重失败，退回进程内: {str(e)[:120]}")
    now = time.time()
    with _mem_lock:
        for k in [k for k, exp in _mem_seen.items() if exp <= now]:
            _mem_seen.pop(k, None)
        if key in _mem_seen:
            return False
        _mem_seen[key] = now + ttl
        return True


def rate_ok(key: str, limit: int = 20, window: int = 60) -> bool:
    """固定窗口限流：同一绑定用户每分钟最多 limit 条消息。"""
    if not key:
        return True
    store = _redis_client()
    if store:
        try:
            k = f"feishu:rate:{key}"
            pipe = store.pipeline()
            pipe.incr(k)
            pipe.expire(k, window)
            n, _ = pipe.execute()
            return int(n) <= limit
        except Exception as e:
            logger.warning(f"[feishu] Redis 限流失败，退回进程内: {str(e)[:120]}")
    now = time.time()
    with _mem_lock:
        hits = [t for t in _mem_rate.get(key, []) if t > now - window]
        hits.append(now)
        _mem_rate[key] = hits
        return len(hits) <= limit


def reset_seen_for_test() -> None:
    """仅测试使用：清空进程内去重/限流表。"""
    with _mem_lock:
        _mem_seen.clear()
        _mem_rate.clear()
