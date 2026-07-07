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
