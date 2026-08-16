"""DeepSeek API 客户端封装。含降级 fallback 逻辑。"""
import json
from openai import OpenAI
from loguru import logger

from app.config import settings

_client = None
_consecutive_failures = 0
MAX_FAILURES = 3


def _get_api_key() -> str:
    """从环境变量或数据库获取 DeepSeek API Key。"""
    key = settings.DEEPSEEK_API_KEY
    if key:
        return key
    # 回退：从数据库 strategy_config 读取
    try:
        from app.db.connection import get_sync_db
        from sqlalchemy import text
        db = get_sync_db()
        row = db.execute(text(
            "SELECT params FROM strategy_config WHERE strategy_name='global_preference'"
        )).fetchone()
        db.close()
        if row and row[0]:
            import json
            params = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            key = params.get("deepseek_key", "")
    except Exception:
        pass
    return key


def get_client() -> OpenAI | None:
    """获取 DeepSeek 客户端。API Key 未配置时返回 None。"""
    global _client
    if _client is None:
        api_key = _get_api_key()
        if api_key:
            _client = OpenAI(
                api_key=api_key,
                base_url=settings.DEEPSEEK_BASE_URL,
            )
    return _client


def reset_client():
    """重置客户端（API Key 变更后调用）。"""
    global _client, _consecutive_failures
    _client = None
    _consecutive_failures = 0


def is_available() -> bool:
    """DeepSeek API 是否可用。"""
    global _consecutive_failures
    client = get_client()
    if client is None:
        return False
    return _consecutive_failures < MAX_FAILURES


def get_fallback_menu() -> str:
    """DeepSeek 不可用时的快捷指令菜单。"""
    return (
        "⚠️ AI 服务暂时不可用，你可以回复数字执行操作：\n"
        "[1] 查看持仓\n"
        "[2] 查看今日买点扫描\n"
        "[3] 查看数据状态\n"
        "[4] 查看策略配置\n"
        "如需修改持仓或参数，请等待 AI 服务恢复后使用自然语言操作。"
    )


def chat(messages: list, tools: list = None) -> dict:
    """
    调用 DeepSeek API 进行对话。

    返回格式:
        {
            "ok": True/False,
            "content": "回复文本" | None,
            "tool_calls": [{"name": ..., "arguments": {...}}] | None,
            "error": "错误信息" | None,
        }
    """
    global _consecutive_failures
    client = get_client()

    if client is None or _consecutive_failures >= MAX_FAILURES:
        return {"ok": False, "content": None, "tool_calls": None, "error": "AI 服务不可用"}

    try:
        kwargs = {
            "model": settings.DEEPSEEK_MODEL,
            "messages": messages,
            "temperature": 0.1,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = client.chat.completions.create(**kwargs)
        msg = response.choices[0].message

        _consecutive_failures = 0  # 成功，重置失败计数

        result = {
            "ok": True,
            "content": msg.content,
            "tool_calls": None,
            "error": None,
        }

        if msg.tool_calls:
            result["tool_calls"] = []
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                result["tool_calls"].append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": args,
                })
            result["content"] = None  # 有 tool_calls 时 content 通常为 None

        return result

    except Exception as e:
        _consecutive_failures += 1
        logger.warning(f"DeepSeek API 调用失败 ({_consecutive_failures}/{MAX_FAILURES}): {e}")
        if _consecutive_failures >= MAX_FAILURES:
            logger.error("DeepSeek API 连续失败，切换为降级模式")
        return {"ok": False, "content": None, "tool_calls": None, "error": str(e)}
