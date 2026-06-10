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
    """获取所有配置。"""
    db = get_sync_db()
    try:
        result = db.execute(text(
            "SELECT strategy_name, display_name, enabled, params FROM strategy_config ORDER BY strategy_name"
        ))
        strategies = []
        for r in result.fetchall():
            params = r.params
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except:
                    params = {}
            strategies.append({
                "name": r.strategy_name, "display": r.display_name,
                "enabled": r.enabled, "params": params,
            })

        pref = next((s for s in strategies if s["name"] == "global_preference"), {})
        deepseek_configured = bool(pref.get("params", {}).get("deepseek_key", ""))

        return {"strategies": strategies, "deepseek_configured": deepseek_configured}
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
    """切换全局交易偏好。"""
    if body.mode not in ("left", "right", "balanced"):
        raise HTTPException(400, "无效偏好: left/right/balanced")
    db = get_sync_db()
    try:
        db.execute(text(
            "UPDATE strategy_config SET params=:p, updated_at=CURRENT_TIMESTAMP, updated_by='web' WHERE strategy_name='global_preference'"
        ), {"p": json.dumps({"mode": body.mode, "deepseek_key": ""})})
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
def set_deepseek_key(body: DeepSeekKey):
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
