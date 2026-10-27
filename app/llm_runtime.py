"""LLM 运行时配置解析——strategy_config('llm_config') 优先，回落 .env。

状态页「大模型配置」卡片写入此处（key/模型/并发/RPM），改配置无需改 .env 重启。
rpm/workers 默认保守：企业 key 实测 8 并发全 429 限速（2026-10-26，code 1302）、
单发 28s/次正常——调用方（news_summary/xueqiu_sentiment 及其 DAG 节点）一律从
这里取并发与限速参数，不要直接用模块常量。
"""
import json

from sqlalchemy import text

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
    return {
        "api_key": cfg.get("api_key") or env["api_key"] or "",
        "base_url": cfg.get("base_url") or env["base_url"] or "",
        "model": cfg.get("model") or env["model"] or "",
        "rpm": int(cfg.get("rpm") or DEFAULTS["rpm"]),
        "workers": int(cfg.get("workers") or DEFAULTS["workers"]),
    }
