"""模型版本管理 API。"""
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional
import json

from app.db.connection import get_sync_db

router = APIRouter(tags=["models"])


def _safe_fetch_model(db, version: str):
    """查询模型版本，兼容 deleted_at 列未迁移的情况。返回行或 None。"""
    try:
        return db.execute(text(
            "SELECT status, activated_at, deleted_at FROM model_versions WHERE version = :v"
        ), {"v": version}).fetchone()
    except Exception:
        db.rollback()
        return db.execute(text(
            "SELECT status, activated_at, NULL as deleted_at FROM model_versions WHERE version = :v"
        ), {"v": version}).fetchone()


from typing import Optional, List

class CreateModel(BaseModel):
    model_name: str
    # 数据配置
    stock_pool: str = "all"          # all / custom
    train_start: str = "2021-01-01"
    train_end: str = "2025-12-31"
    test_start: str = "2026-01-01"
    test_end: str = ""
    # 特征配置
    features: List[str] = ["boll", "macd", "rsi", "atr", "ma", "volume"]
    # 模型配置
    ml_enabled: bool = False
    model_type: str = "xgboost"
    # 搜索空间
    n_estimators_min: int = 100
    n_estimators_max: int = 500
    max_depth_min: int = 3
    max_depth_max: int = 10
    learning_rate_min: float = 0.01
    learning_rate_max: float = 0.3
    # 信号配置
    buy_threshold: float = 0.6
    sell_threshold: float = 0.4
    ml_confidence_threshold: float = 0.5
    # 风险控制
    stop_loss_pct: float = 8.0
    signal_timeout_days: int = 20


@router.get("/v1/models")
def list_models():
    """模型版本列表。"""
    db = get_sync_db()
    try:
        try:
            rows = db.execute(text("""
                SELECT version, model_name, status, config, best_params,
                       evaluation_report, sharpe, win_rate, max_drawdown, annual_return,
                       created_at, trained_at, activated_at
                FROM model_versions
                WHERE deleted_at IS NULL
                ORDER BY created_at DESC
            """)).fetchall()
        except Exception:
            db.rollback()
            # deleted_at 列未迁移时回退
            rows = db.execute(text("""
                SELECT version, model_name, status, config, best_params,
                       evaluation_report, sharpe, win_rate, max_drawdown, annual_return,
                       created_at, trained_at, activated_at
                FROM model_versions
                ORDER BY created_at DESC
            """)).fetchall()
        versions = []
        for r in rows:
            versions.append({
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
    if not body.model_name or not body.model_name.strip():
        raise HTTPException(400, "模型名称不能为空")
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

        config = {
            "stock_pool": body.stock_pool,
            "train_start": body.train_start,
            "train_end": body.train_end,
            "test_start": body.test_start,
            "test_end": body.test_end or None,
            "features": body.features,
            "ml_enabled": body.ml_enabled,
            "model_type": body.model_type,
            "search_space": {
                "n_estimators": [body.n_estimators_min, body.n_estimators_max],
                "max_depth": [body.max_depth_min, body.max_depth_max],
                "learning_rate": [body.learning_rate_min, body.learning_rate_max],
            },
            "signal": {
                "buy_threshold": body.buy_threshold,
                "sell_threshold": body.sell_threshold,
                "ml_confidence_threshold": body.ml_confidence_threshold,
            },
            "risk": {
                "stop_loss_pct": body.stop_loss_pct,
                "signal_timeout_days": body.signal_timeout_days,
            },
        }
        db.execute(text("""
            INSERT INTO model_versions (version, model_name, status, config)
            VALUES (:v, :n, 'DRAFT', :cfg)
        """), {"v": version, "n": body.model_name, "cfg": json.dumps(config)})
        db.commit()
        return {"ok": True, "version": version, "model_name": body.model_name, "status": "DRAFT"}
    finally:
        db.close()


@router.get("/v1/models/{version}/health")
def get_model_health(version: str):
    """模型健康度最新记录。"""
    db = get_sync_db()
    try:
        r = db.execute(text("""
            SELECT health_status, live_win_rate, signal_count, avg_forward_5d, detail, check_date
            FROM model_health WHERE version=:v ORDER BY check_date DESC LIMIT 1
        """), {"v": version}).fetchone()
        if not r:
            return {"health_status": "HEALTHY", "live_win_rate": 0, "signal_count": 0, "avg_forward_5d": 0}
        return {
            "health_status": r[0], "live_win_rate": float(r[1]) if r[1] else 0,
            "signal_count": r[2] or 0, "avg_forward_5d": float(r[3]) if r[3] else 0,
            "detail": r[4] if isinstance(r[4], dict) else (json.loads(r[4]) if r[4] else {}),
            "check_date": str(r[5]) if r[5] else None,
        }
    finally:
        db.close()

@router.get("/v1/models/{version}/signals")
def get_model_signals(version: str):
    """模型信号明细列表（最近 50 条）。"""
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT id, signal_date, stock_code, stock_name, direction, strength, price
            FROM signal_history
            WHERE strategy_name = 'model_signal' AND model_version = :version
            ORDER BY signal_date DESC LIMIT 50
        """), {"version": version}).fetchall()
        signals = []
        for r in rows:
            signals.append({
                "id": r[0], "signal_date": str(r[1]) if r[1] else None,
                "stock_code": r[2], "stock_name": r[3],
                "direction": r[4], "strength": r[5],
                "price": float(r[6]) if r[6] else 0,
            })
        return {"signals": signals, "count": len(signals)}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"查询信号失败: {str(e)[:200]}")
    finally:
        db.close()

@router.get("/v1/models/{version}/delete-check")
def check_model_delete(version: str):
    """检查模型是否可以物理删除，返回关联数据统计。"""
    db = get_sync_db()
    try:
        # 先查模型是否存在（兼容 deleted_at 列未迁移的情况）
        r = _safe_fetch_model(db, version)
        if not r:
            raise HTTPException(404, f"版本 {version} 不存在")
        if r[2] is not None:
            raise HTTPException(400, "该模型已被删除")
        if r[0] == 'ACTIVE':
            raise HTTPException(400, "ACTIVE 状态模型不能删除，请先归档")

        activated = r[1] is not None
        signals = db.execute(text(
            "SELECT COUNT(*) FROM signal_history WHERE model_version = :v"
        ), {"v": version}).scalar() or 0
        trials = db.execute(text(
            "SELECT COUNT(*) FROM training_trials WHERE version = :v"
        ), {"v": version}).scalar() or 0
        health = db.execute(text(
            "SELECT COUNT(*) FROM model_health WHERE version = :v"
        ), {"v": version}).scalar() or 0
        comparisons = db.execute(text(
            "SELECT COUNT(*) FROM version_comparisons WHERE version_a = :v OR version_b = :v"
        ), {"v": version}).scalar() or 0

        can_physical = not activated and signals == 0 and trials == 0 and health == 0 and comparisons == 0
        return {
            "version": version,
            "can_physical_delete": can_physical,
            "related_data": {
                "signals": signals,
                "training_trials": trials,
                "health_records": health,
                "comparisons": comparisons,
                "activated": activated,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"删除检查失败: {str(e)[:200]}")
    finally:
        db.close()


@router.delete("/v1/models/{version}")
def delete_model(version: str, mode: str = Query("soft")):
    """删除模型版本。mode=soft 逻辑删除，mode=hard 物理删除。"""
    if mode not in ("soft", "hard"):
        raise HTTPException(400, "mode 参数只能是 soft 或 hard")
    db = get_sync_db()
    try:
        # 兼容 deleted_at 列未迁移的情况
        r = _safe_fetch_model(db, version)
        if not r:
            raise HTTPException(404, f"版本 {version} 不存在")
        if r[2] is not None:
            raise HTTPException(400, "该模型已被删除")
        if r[0] == 'ACTIVE':
            raise HTTPException(400, "ACTIVE 状态模型不能删除，请先归档")

        if mode == "soft":
            db.execute(text(
                "UPDATE model_versions SET deleted_at = CURRENT_TIMESTAMP WHERE version = :v"
            ), {"v": version})
            db.commit()
            return {"ok": True, "version": version, "mode": "soft"}

        # mode == "hard" — 物理删除
        activated = r[1] is not None
        signals = db.execute(text(
            "SELECT COUNT(*) FROM signal_history WHERE model_version = :v"
        ), {"v": version}).scalar() or 0
        trials = db.execute(text(
            "SELECT COUNT(*) FROM training_trials WHERE version = :v"
        ), {"v": version}).scalar() or 0
        health = db.execute(text(
            "SELECT COUNT(*) FROM model_health WHERE version = :v"
        ), {"v": version}).scalar() or 0
        comparisons = db.execute(text(
            "SELECT COUNT(*) FROM version_comparisons WHERE version_a = :v OR version_b = :v"
        ), {"v": version}).scalar() or 0

        if activated or signals > 0 or trials > 0 or health > 0 or comparisons > 0:
            raise HTTPException(400, "该模型有关联数据，不能物理删除，请使用逻辑删除（mode=soft）")

        # 按 FK 依赖顺序删除
        db.execute(text("DELETE FROM version_comparisons WHERE version_a = :v OR version_b = :v"), {"v": version})
        db.execute(text("DELETE FROM model_health WHERE version = :v"), {"v": version})
        db.execute(text("DELETE FROM training_trials WHERE version = :v"), {"v": version})
        db.execute(text("DELETE FROM signal_history WHERE model_version = :v"), {"v": version})
        db.execute(text("DELETE FROM model_versions WHERE version = :v"), {"v": version})
        db.commit()
        return {"ok": True, "version": version, "mode": "hard"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"删除失败: {str(e)[:200]}")
    finally:
        db.close()


@router.get("/v1/indicators/{name}/status")
def get_indicator_status(name: str):
    """指标表状态：行数 + 最新日期。"""
    valid = {'boll','macd','rsi','atr','ma','volume'}
    if name not in valid:
        raise HTTPException(400, f"无效指标名: {name}，可选: {', '.join(valid)}")
    db = get_sync_db()
    try:
        table = f"stock_indicators_{name}"
        cnt = db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
        latest = db.execute(text(f"SELECT MAX(trade_date) FROM {table}")).scalar()
        return {"name": name, "rows": cnt, "latest": str(latest) if latest else None}
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
        db.execute(text("UPDATE model_versions SET status='ARCHIVED', archived_at=CURRENT_TIMESTAMP WHERE status='ACTIVE'"))
        db.execute(text("UPDATE model_versions SET status='ACTIVE', activated_at=CURRENT_TIMESTAMP WHERE version=:v"), {"v": version})
        db.commit()
        return {"ok": True, "version": version, "status": "ACTIVE"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"审批失败: {str(e)[:200]}")
    finally:
        db.close()

@router.put("/v1/models/{version}/config")
def update_model_config(version: str, body: dict):
    """更新 DRAFT 状态模型的四层配置。"""
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT status FROM model_versions WHERE version=:v"), {"v": version}).fetchone()
        if not r:
            raise HTTPException(404, "版本不存在")
        if r[0] != 'DRAFT':
            raise HTTPException(400, f"只有 DRAFT 状态可编辑，当前为 {r[0]}")
        cfg = dict(body)
        name = cfg.pop('model_name', None)
        db.execute(text("UPDATE model_versions SET config=:cfg WHERE version=:v"),
                   {"v": version, "cfg": json.dumps(cfg)})
        if name:
            db.execute(text("UPDATE model_versions SET model_name=:n WHERE version=:v"),
                       {"v": version, "n": name})
        db.commit()
        return {"ok": True, "version": version}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"更新失败: {str(e)[:200]}")
    finally:
        db.close()


@router.post("/v1/models/{version}/retrain")
def retrain_model(version: str):
    """REJECTED 模型重新训练：清空旧数据，回到 DRAFT。"""
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT status FROM model_versions WHERE version=:v"), {"v": version}).fetchone()
        if not r:
            raise HTTPException(404, "版本不存在")
        if r[0] != 'REJECTED':
            raise HTTPException(400, f"只有 REJECTED 状态可重新训练，当前为 {r[0]}")
        db.execute(text("UPDATE model_versions SET status='DRAFT', best_params=NULL, evaluation_report=NULL, sharpe=NULL, win_rate=NULL, max_drawdown=NULL, annual_return=NULL WHERE version=:v"), {"v": version})
        db.commit()
        return {"ok": True, "version": version, "status": "DRAFT"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"操作失败: {str(e)[:200]}")
    finally:
        db.close()


@router.post("/v1/models/{version}/reject")
def reject_model(version: str):
    """拒绝模型。"""
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT status FROM model_versions WHERE version=:v"), {"v": version}).fetchone()
        if not r:
            raise HTTPException(404, "版本不存在")
        if r[0] != 'PENDING':
            raise HTTPException(400, f"当前状态为 {r[0]}，只有 PENDING 状态可拒绝")
        db.execute(text("UPDATE model_versions SET status='REJECTED', best_params=NULL, evaluation_report=NULL, sharpe=NULL, win_rate=NULL, max_drawdown=NULL, annual_return=NULL WHERE version=:v"), {"v": version})
        db.commit()
        return {"ok": True, "version": version, "status": "REJECTED"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"操作失败: {str(e)[:200]}")
    finally:
        db.close()
