"""模型版本管理 API。"""
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional
import json

from app.db.connection import get_sync_db

router = APIRouter(tags=["models"])


class CreateModel(BaseModel):
    model_name: str


@router.get("/v1/models")
def list_models():
    """模型版本列表。"""
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT version, model_name, status, best_params,
                   evaluation_report, sharpe, win_rate, max_drawdown, annual_return,
                   created_at, trained_at, activated_at
            FROM model_versions ORDER BY created_at DESC
        """)).fetchall()
        versions = []
        for r in rows:
            versions.append({
                "version": r[0], "model_name": r[1], "status": r[2],
                "best_params": r[3] if isinstance(r[3], dict) else (json.loads(r[3]) if r[3] else None),
                "evaluation_report": r[4] if isinstance(r[4], dict) else (json.loads(r[4]) if r[4] else None),
                "sharpe": float(r[5]) if r[5] else None,
                "win_rate": float(r[6]) if r[6] else None,
                "max_drawdown": float(r[7]) if r[7] else None,
                "annual_return": float(r[8]) if r[8] else None,
                "created_at": str(r[9]) if r[9] else None,
                "trained_at": str(r[10]) if r[10] else None,
                "activated_at": str(r[11]) if r[11] else None,
            })
        return {"versions": versions, "count": len(versions)}
    finally:
        db.close()


@router.get("/v1/models/{version}")
def get_model(version: str):
    """模型版本详情。"""
    db = get_sync_db()
    try:
        r = db.execute(text("""
            SELECT version, model_name, status, config, best_params,
                   evaluation_report, sharpe, win_rate, max_drawdown, annual_return,
                   created_at, trained_at, activated_at, archived_at
            FROM model_versions WHERE version = :v
        """), {"v": version}).fetchone()
        if not r:
            raise HTTPException(404, "版本不存在")
        return {
            "version": r[0], "model_name": r[1], "status": r[2],
            "config": r[3] if isinstance(r[3], dict) else (json.loads(r[3]) if r[3] else {}),
            "best_params": r[4] if isinstance(r[4], dict) else (json.loads(r[4]) if r[4] else None),
            "evaluation_report": r[5] if isinstance(r[5], dict) else (json.loads(r[5]) if r[5] else None),
            "sharpe": float(r[6]) if r[6] else None,
            "win_rate": float(r[7]) if r[7] else None,
            "max_drawdown": float(r[8]) if r[8] else None,
            "annual_return": float(r[9]) if r[9] else None,
            "created_at": str(r[10]) if r[10] else None,
            "trained_at": str(r[11]) if r[11] else None,
            "activated_at": str(r[12]) if r[12] else None,
            "archived_at": str(r[13]) if r[13] else None,
        }
    finally:
        db.close()


@router.post("/v1/models")
def create_model(body: CreateModel):
    """创建新模型版本（状态 DRAFT，自动生成版本号）。"""
    db = get_sync_db()
    try:
        # 生成版本号: 查询当前最大主版本号 + 1
        max_ver = db.execute(text("SELECT MAX(version) FROM model_versions")).scalar()
        if max_ver:
            parts = max_ver.lstrip('v').split('.')
            major = int(parts[0]) + 1
            minor = 0
        else:
            major, minor = 1, 0
        version = f"v{major}.{minor}"

        db.execute(text("""
            INSERT INTO model_versions (version, model_name, status, config)
            VALUES (:v, :n, 'DRAFT', '{}')
        """), {"v": version, "n": body.model_name})
        db.commit()
        return {"ok": True, "version": version, "model_name": body.model_name, "status": "DRAFT"}
    finally:
        db.close()


@router.post("/v1/models/{version}/approve")
def approve_model(version: str):
    """审批模型上线：旧 ACTIVE → ARCHIVED，新版本 → ACTIVE。"""
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT status FROM model_versions WHERE version=:v"), {"v": version}).fetchone()
        if not r:
            raise HTTPException(404, "版本不存在")
        if r[0] != 'PENDING':
            raise HTTPException(400, f"当前状态为 {r[0]}，只有 PENDING 状态可审批")

        # 事务：旧 ACTIVE → ARCHIVED，PENDING → ACTIVE
        db.execute(text("UPDATE model_versions SET status='ARCHIVED', archived_at=CURRENT_TIMESTAMP WHERE status='ACTIVE'"))
        db.execute(text("UPDATE model_versions SET status='ACTIVE', activated_at=CURRENT_TIMESTAMP WHERE version=:v"), {"v": version})
        db.commit()
        return {"ok": True, "version": version, "status": "ACTIVE"}
    finally:
        db.close()
