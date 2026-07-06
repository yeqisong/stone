"""DAG 节点类型 API（v2.0 重构 迭代 3.3）。"""
from fastapi import APIRouter
from scripts.pipeline import NODE_FN_MAP

router = APIRouter(prefix="/api/dag", tags=["dag"])


@router.get("/node-types")
def list_node_types():
    """返回所有已注册的 DAG 节点类型。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text

    db = get_sync_db()
    try:
        rows = db.execute(text("SELECT node_name, deps, label, sort_order FROM dag_config ORDER BY sort_order")).fetchall()
        items = []
        for r in rows:
            items.append({
                "node_name": r[0],
                "deps": [d.strip() for d in (r[1] or "").split(",") if d.strip()],
                "label": r[2] or r[0],
                "sort_order": r[3] or 0,
                "has_function": r[0] in NODE_FN_MAP,
                "status": "active" if r[0] in NODE_FN_MAP else "unregistered",
            })
        db.close()
        return {"items": items, "total": len(items)}
    except Exception as e:
        db.close()
        return {"items": [], "total": 0, "error": str(e)}
