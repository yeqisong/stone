"""LLM 运行时配置解析——strategy_config('llm_config') 优先，回落 .env。

状态页「大模型配置」卡片写入此处（key/模型/并发/RPM），改配置无需改 .env 重启。
rpm/workers 默认保守：企业 key 实测 8 并发全 429 限速（2026-10-26，code 1302）、
单发 28s/次正常——调用方（news_summary/xueqiu_sentiment 及其 DAG 节点）一律从
这里取并发与限速参数，不要直接用模块常量。
"""
import json

from sqlalchemy import text

# OpenCode Go（订阅制聚合网关）要求：稳定的会话标识头 x-opencode-session（路由与
# prompt 缓存优化）+ 明确的客户端 UA（文档要求不要用通用 SDK 名）。只对 opencode.ai
# 端点注入，其它 OpenAI 兼容服务保持原样。
APP_UA = 'stone-monitor/3.x (internal agent)'


def build_client(api_key: str = '', base_url: str = '', *, session_id: str = 'stone',
                 timeout: float = 180):
    """按运行时配置构建 OpenAI 兼容客户端（缺省取 get_llm_settings）。

    session_id：同一对话/同一批任务用稳定值（Agent 用会话 id，批量任务用任务名）。
    """
    from openai import OpenAI
    cfg = None
    if not api_key or not base_url:
        cfg = get_llm_settings()
        api_key = api_key or cfg['api_key']
        base_url = base_url or cfg['base_url']
    headers = {}
    if 'opencode.ai' in (base_url or ''):
        headers = {'x-opencode-session': session_id, 'User-Agent': APP_UA}
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout,
                  default_headers=headers or None)

CFG_KEY = "llm_config"
DEFAULTS = {"rpm": 6, "workers": 2}


def get_llm_settings(db=None) -> dict:
    """返回生效配置：DB('llm_config') 覆盖 env（settings.llm_config）兜底。

    db 传入时复用调用方连接（不 commit/close）；传 None 则自开自关。
    """
    from app.config import settings

    own = db is None
    if own:
        from app.db.connection import get_sync_db
        db = get_sync_db()
    cfg = {}
    try:
        row = db.execute(text(
            "SELECT params FROM strategy_config WHERE strategy_name=:k"), {"k": CFG_KEY}).fetchone()
        cfg = (json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})) if row else {}
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        if own:
            try:
                db.close()
            except Exception:
                pass
    env = settings.llm_config
    model = cfg.get("model") or env["model"] or ""
    # 可选模型清单：状态页可配置 models（数组或逗号分隔），用于会话级切换；
    # 未配置时至少包含默认模型，前端仍是单选项。
    raw_models = cfg.get("models") or cfg.get("model_list") or []
    if isinstance(raw_models, str):
        raw_models = [x.strip() for x in raw_models.replace("\n", ",").split(",") if x.strip()]
    models = [str(x).strip() for x in (raw_models or []) if str(x).strip()]
    if model and model not in models:
        models.insert(0, model)
    return {
        "api_key": cfg.get("api_key") or env["api_key"] or "",
        "base_url": cfg.get("base_url") or env["base_url"] or "",
        "model": model,
        "models": models,
        "rpm": int(cfg.get("rpm") or DEFAULTS["rpm"]),
        "workers": int(cfg.get("workers") or DEFAULTS["workers"]),
    }
