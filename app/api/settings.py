"""系统设置 API — 策略启停、偏好切换、API Key。"""
import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from pydantic import BaseModel

from app.db.connection import get_sync_db
from app.auth.auth import optional_auth

router = APIRouter(tags=["settings"])


class StrategyToggle(BaseModel):
    strategy_name: str
    enabled: bool


class PreferenceSet(BaseModel):
    mode: str


class DeepSeekKey(BaseModel):
    api_key: str


class StrategyParams(BaseModel):
    strategy_name: str
    params: dict


@router.get("/settings")
def get_settings():
    """获取所有配置。API Key 脱敏返回（仅显示后4位）。"""
    db = get_sync_db()
    try:
        result = db.execute(text(
            "SELECT strategy_name, display_name, enabled, params FROM strategy_config ORDER BY strategy_name"
        ))
        indicators = []
        strategies = []
        preference = {"mode": "balanced"}
        for r in result.fetchall():
            params = r.params
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except:
                    params = {}
            entry = {
                "name": r.strategy_name, "display": r.display_name,
                "enabled": r.enabled, "params": params,
            }
            if r.strategy_name == 'global_preference':
                preference = params
            elif r.strategy_name in ('boll','macd','rsi','atr','ma','volume'):
                indicators.append(entry)
            else:
                strategies.append(entry)  # 旧策略，保留兼容

        # API Key 脱敏：仅保留后4位
        raw_key = preference.get("deepseek_key", "")
        deepseek_configured = bool(raw_key)
        if raw_key and len(raw_key) > 4:
            preference["deepseek_key"] = "****" + raw_key[-4:]

        return {"indicators": indicators, "strategies": strategies, "preference": preference, "deepseek_configured": deepseek_configured}
    finally:
        db.close()


@router.post("/settings/toggle_strategy")
def toggle_strategy(body: StrategyToggle, user: str = Depends(optional_auth)):
    """启用/停用策略。"""
    if body.strategy_name == "global_preference":
        raise HTTPException(400, "不允许关闭全局偏好")
    db = get_sync_db()
    try:
        db.execute(text(
            "UPDATE strategy_config SET enabled=:e, updated_at=CURRENT_TIMESTAMP, updated_by='web' WHERE strategy_name=:n"
        ), {"e": body.enabled, "n": body.strategy_name})
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.get("/settings/preference")
def get_preference(user: str = Depends(optional_auth)):
    """读取全局交易偏好。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    import json
    db = get_sync_db()
    try:
        r = db.execute(text(
            "SELECT params FROM strategy_config WHERE strategy_name='global_preference'"
        )).fetchone()
        db.close()
        if r:
            params = json.loads(r[0]) if isinstance(r[0], str) else {}
            return {"mode": params.get("mode", "balanced")}
        return {"mode": "balanced"}
    except Exception:
        db.close()
        return {"mode": "balanced"}


@router.post("/settings/preference")
def set_preference(body: PreferenceSet, user: str = Depends(optional_auth)):
    """切换全局交易偏好（保留已有 deepseek_key，不覆盖）。"""
    if body.mode not in ("left", "right", "balanced"):
        raise HTTPException(400, "无效偏好: left/right/balanced")
    db = get_sync_db()
    try:
        # 读取现有配置，保留 deepseek_key
        row = db.execute(text(
            "SELECT params FROM strategy_config WHERE strategy_name='global_preference'"
        )).fetchone()
        existing = {}
        if row and row[0]:
            try:
                existing = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
            except:
                pass
        # merge: 保留已有 deepseek_key
        merged = {**existing, "mode": body.mode}
        db.execute(text(
            "UPDATE strategy_config SET params=:p, updated_at=CURRENT_TIMESTAMP, updated_by='web' WHERE strategy_name='global_preference'"
        ), {"p": json.dumps(merged)})
        db.commit()
        return {"ok": True, "mode": body.mode}
    finally:
        db.close()


@router.post("/settings/update_params")
def update_params(body: StrategyParams, user: str = Depends(optional_auth)):
    """更新策略参数。"""
    db = get_sync_db()
    try:
        db.execute(text(
            "UPDATE strategy_config SET params=:p, updated_at=CURRENT_TIMESTAMP, updated_by='web' WHERE strategy_name=:n"
        ), {"p": json.dumps(body.params), "n": body.strategy_name})
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.post("/settings/deepseek_key")
def set_deepseek_key(body: DeepSeekKey, user: str = Depends(optional_auth)):
    """设置 DeepSeek API Key。"""
    db = get_sync_db()
    try:
        result = db.execute(text(
            "SELECT params FROM strategy_config WHERE strategy_name='global_preference'"
        ))
        row = result.fetchone()
        params = {}
        if row and row.params:
            try:
                params = json.loads(row.params)
            except:
                pass
        params["deepseek_key"] = body.api_key

        db.execute(text(
            "UPDATE strategy_config SET params=:p, updated_at=CURRENT_TIMESTAMP WHERE strategy_name='global_preference'"
        ), {"p": json.dumps(params)})
        db.commit()

        from app.ai.deepseek_client import reset_client
        reset_client()
        return {"ok": True}
    finally:
        db.close()


# ═══════════════════════════════════════════════
#  LLM 运行时配置（状态页「大模型配置」卡片；news_summary/xueqiu_sentiment 共用）
# ═══════════════════════════════════════════════

class LLMConfig(BaseModel):
    api_key: str = ""       # 空=保留已存值
    base_url: str = ""
    model: str = ""
    rpm: int = 0            # 0=保留已存值/默认
    workers: int = 0


def _read_llm_row(db):
    row = db.execute(text(
        "SELECT params FROM strategy_config WHERE strategy_name='llm_config'")).fetchone()
    return (json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})) if row else {}


def _write_llm_row(db, cfg):
    db.execute(text("""
        INSERT INTO strategy_config (strategy_name, display_name, enabled, params)
        VALUES ('llm_config', '大模型运行时配置', true, :p)
        ON CONFLICT (strategy_name) DO UPDATE SET params=:p, updated_at=CURRENT_TIMESTAMP
    """), {"p": json.dumps(cfg, ensure_ascii=False)})
    db.commit()


@router.get("/llm_config")
def get_llm_config(user: str = Depends(optional_auth)):
    """LLM 运行时配置（key 只回尾号掩码，明文不出接口）。"""
    from app.llm_runtime import get_llm_settings
    db = get_sync_db()
    try:
        llm = get_llm_settings(db)
        saved = _read_llm_row(db)
        key = llm["api_key"]
        return {
            "configured": bool(key),
            "key_tail": key[-4:] if key else "",
            "has_env_fallback": bool(saved.get("api_key")),
            "base_url": llm["base_url"], "model": llm["model"],
            "rpm": llm["rpm"], "workers": llm["workers"],
        }
    finally:
        db.close()


@router.post("/llm_config")
def save_llm_config(body: LLMConfig, user: str = Depends(optional_auth)):
    """保存 LLM 配置。api_key 留空=保留已存值（明文不回传前端，避免覆盖）。"""
    db = get_sync_db()
    try:
        cfg = _read_llm_row(db)
        if body.api_key.strip():
            cfg["api_key"] = body.api_key.strip()
        if body.base_url.strip():
            cfg["base_url"] = body.base_url.strip()
        if body.model.strip():
            cfg["model"] = body.model.strip()
        if body.rpm > 0:
            cfg["rpm"] = body.rpm
        if body.workers > 0:
            cfg["workers"] = body.workers
        _write_llm_row(db, cfg)
        return {"ok": True, "key_tail": (cfg.get("api_key") or "")[-4:]}
    finally:
        db.close()


@router.post("/llm_config/test")
def test_llm_config(body: LLMConfig, user: str = Depends(optional_auth)):
    """连通性测试：优先用请求携带的覆盖值，未带则用已存配置。单次小请求计耗时。"""
    import time as _t
    from openai import OpenAI
    from app.llm_runtime import get_llm_settings
    db = get_sync_db()
    try:
        llm = get_llm_settings(db)
        key = body.api_key.strip() or llm["api_key"]
        base = body.base_url.strip() or llm["base_url"]
        model = body.model.strip() or llm["model"]
        if not key:
            return {"ok": False, "error": "未配置 API Key"}
        t0 = _t.time()
        try:
            client = OpenAI(api_key=key, base_url=base, timeout=60)
            r = client.chat.completions.create(
                model=model, temperature=0, max_tokens=512,
                messages=[{"role": "user", "content": '输出 JSON：{"ok": true}'}])
            dt = int((_t.time() - t0) * 1000)
            content = (r.choices[0].message.content or "").strip()
            return {"ok": bool(content), "latency_ms": dt, "model": model,
                    "sample": content[:80], "error": "" if content else "返回内容为空（思考型模型请增大 max_tokens 或降低温度）"}
        except Exception as e:
            return {"ok": False, "latency_ms": int((_t.time() - t0) * 1000),
                    "model": model, "error": f"{type(e).__name__}: {str(e)[:180]}"}
    finally:
        db.close()
