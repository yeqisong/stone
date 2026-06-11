"""市值树图 API — 全量初始视图 + 按父节点下钻。"""
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from app.db.connection import get_sync_db
import json

router = APIRouter(tags=["treemap"])


@router.get("/treemap_data")
def get_treemap_data(
    trade_date: str = Query(..., description="日期 YYYY-MM-DD"),
    metric: str = Query("mcap", description="指标: mcap/volume/amount"),
    parent: str = Query("", description="父节点: 空=全量树, A=该行业下二级+个股, C15=该二级下个股"),
):
    """返回树图数据。parent 为空时返回全量扁平树(L1→个股)；指定 parent 返回该节点下直系子级。"""
    db = get_sync_db()
    try:
        has = db.execute(text(
            "SELECT COUNT(*) FROM stock_treemap_cache WHERE trade_date=:d AND metric=:m"
        ), {"d": trade_date, "m": metric}).scalar() or 0
        if not has:
            return {"trade_date": trade_date, "children": []}

        rows = db.execute(text(
            "SELECT parent, node_id, name, value, chg_pct, trend_up, node_type, detail "
            "FROM stock_treemap_cache WHERE trade_date=:d AND metric=:m ORDER BY parent, value DESC"
        ), {"d": trade_date, "m": metric}).fetchall()

        # 构建索引
        by_id = {}
        for r in rows:
            by_id[r[1]] = {"id": r[1], "name": r[2], "value": float(r[3]),
                           "chg_pct": float(r[4]) if r[4] else 0,
                           "trend_up": bool(r[5]), "type": r[6], "parent": r[0]}
            if r[7]:
                try: by_id[r[1]]["detail"] = json.loads(r[7])
                except: by_id[r[1]]["detail"] = {}

        tree = {}
        for nid, node in by_id.items():
            p = node.pop("parent")
            tree.setdefault(p, {"children": []})["children"].append(node)

        def get_stocks(nid):
            result = []
            for c in tree.get(nid, {}).get("children", []):
                if c["type"] == "stock":
                    result.append(c)
                elif c["type"] in ("l1", "l2"):
                    result.extend(get_stocks(c["id"]))
            return result

        root_kids = tree.get("root", {}).get("children", [])

        if not parent:
            # 全量扁平树: L1 → 个股
            for l1 in root_kids:
                if l1["type"] == "l1":
                    l1["children"] = get_stocks(l1["id"])
            return {"trade_date": trade_date, "children": root_kids}
        else:
            # 指定 parent: 返回直系子级
            kids = tree.get(parent, {}).get("children", [])
            # 如果子级是 L2，附加它们的个股
            for k in kids:
                if k["type"] == "l2":
                    stks = get_stocks(k["id"])
                    if stks:
                        k["children"] = stks
            return {"trade_date": trade_date, "parent": parent, "children": kids}
    finally:
        db.close()
