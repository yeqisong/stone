"""数据血缘台账 API：事件查询 + 模型数据漂移检查。"""
from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.db.connection import get_sync_db
from app.lineage import get_events, model_data_drift

router = APIRouter(tags=["lineage"])


@router.get("/lineage")
def list_lineage(event_type: str = None, target: str = None, limit: int = 100):
    """血缘事件列表（新→旧）。可按 event_type / target 过滤。"""
    db = get_sync_db()
    try:
        return {"events": get_events(db, event_type, target, limit)}
    except Exception as e:
        raise HTTPException(500, f"查询失败: {str(e)[:200]}")
    finally:
        db.close()


@router.get("/lineage/model/{version}")
def lineage_for_model(version: str):
    """模型的数据血缘：训练事件 + 训练之后的数据变更（漂移清单）。

    用途：模型存档指标与当前数据口径是否脱节，一条查询回答——
    训练后若有 factor_heal/feature_recompute 等事件命中训练窗，说明
    存档数字已不代表当前数据，应重跑 eval_version 复评。
    """
    db = get_sync_db()
    try:
        drift = model_data_drift(db, version)
        if drift is None:
            raise HTTPException(404, "版本不存在")
        train_events = get_events(db, event_type='train', target=version, limit=5)
        return {**drift, 'model_events': train_events}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"查询失败: {str(e)[:200]}")
    finally:
        db.close()
