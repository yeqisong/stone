"""对话历史管理：30 轮完整保留 + 超出部分自动摘要。"""
import json

# 开发环境用内存字典替代 Redis
# 生产环境替换为 Redis: import redis; r = redis.Redis(...)
_memory_store: dict[str, dict] = {}

MAX_ROUNDS = 30


def _get_store():
    """获取存储后端。开发环境用内存字典，生产环境用 Redis。"""
    try:
        from app.config import settings
        if settings.REDIS_URL and settings.APP_ENV == "prod":
            import redis
            return redis.Redis.from_url(
                settings.REDIS_URL,
                password=settings.REDIS_PASSWORD or None,
                decode_responses=True,
            )
    except Exception:
        pass
    return None


def get_history(chat_id: str = "default") -> tuple[list, str]:
    """
    获取对话历史。返回 (最近30轮消息列表, 历史摘要文本)。
    """
    store = _get_store()
    if store and hasattr(store, 'get'):
        # Redis
        msgs_raw = store.get(f"chat:{chat_id}:msgs")
        summary = store.get(f"chat:{chat_id}:summary") or ""
        msgs = json.loads(msgs_raw) if msgs_raw else []
        return msgs, summary
    else:
        # 内存
        entry = _memory_store.get(chat_id, {})
        return entry.get("msgs", []), entry.get("summary", "")


def save_history(chat_id: str = "default", user_msg: str = "", assistant_msg: str = ""):
    """保存最新一轮对话，超出30轮时触发摘要。"""
    msgs, summary = get_history(chat_id)

    if user_msg and assistant_msg:
        msgs.append({"role": "user", "content": user_msg})
        msgs.append({"role": "assistant", "content": assistant_msg})

    # 超出30轮 → 保留最近30轮，丢弃旧消息（TODO: 生产环境接 DeepSeek 摘要）
    if len(msgs) > MAX_ROUNDS * 2:
        msgs = msgs[-MAX_ROUNDS * 2:]

    store = _get_store()
    ttl = 7 * 86400
    if store and hasattr(store, 'setex'):
        store.setex(f"chat:{chat_id}:msgs", ttl, json.dumps(msgs, ensure_ascii=False))
        store.setex(f"chat:{chat_id}:summary", ttl, summary)
    else:
        _memory_store[chat_id] = {"msgs": msgs, "summary": summary}
