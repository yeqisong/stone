"""市值树图 API — 全量初始视图 + 按父节点下钻。"""
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from app.db.connection import get_sync_db
from app.treemap_intraday import get_tree as get_intraday_tree
from app.sector_flow import history as sector_flow_history
import json

router = APIRouter(tags=["treemap"])


@router.get("/market_sentiment")
def get_market_sentiment():
    """市场情绪仪表：宽度/中位数/涨跌停/指数 VWAP → 恐慌/偏弱/中性/偏强。

    附带实时空仓闸门 gate（design/13 批次三：市场闸门 × regime × 盘面情绪，
    buy_check.live_gate 组合，60s 缓存）——前端情绪条在盘面分之外展示开仓闸门。
    """
    from app.market_sentiment import get as market_sentiment
    from app.market_realtime import _safe_live_gate
    db = get_sync_db()
    try:
        data = market_sentiment(db)
        # get() 返回的是模块缓存里的同一个 dict（/api/market/overview、intraday_watch
        # 的上下文也在读）：不能往里写，否则 gate 会泄漏给其他消费者。拷贝后再附带；
        # 闸门算不出来时 _safe_live_gate 给 unknown（而不是静默 None）
        return {**data, "gate": _safe_live_gate(db)}
    finally:
        db.close()


@router.get("/treemap_latest_date")
def get_treemap_latest_date():
    """返回 stock_treemap_cache 中最新有数据的日期，无数据返回 null。"""
    db = get_sync_db()
    try:
        result = db.execute(text("SELECT MAX(trade_date) FROM stock_treemap_cache"))
        latest = result.scalar()
        return {"latest_date": str(latest) if latest else None}
    finally:
        db.close()


@router.get("/treemap_intraday")
def get_treemap_intraday(
    trade_date: str = Query(..., description="日期 YYYY-MM-DD"),
    parent: str = Query("", description="父节点: 空=全量, l1/l2=下钻"),
    slot: str = Query("latest", description="快照 slot，如 10:30；latest=最新"),
):
    """分时市值树图：实时市值层级 + 日涨跌/近10分钟 + 市场宽度。"""
    db = get_sync_db()
    try:
        return get_intraday_tree(db, trade_date, parent, slot)
    finally:
        db.close()


@router.get("/sector_flow_history")
def get_sector_flow_history(
    days: int = Query(60, ge=5, le=250),
    level: str = Query("l1", description="行业层级，目前 l1"),
    sector: str = Query("", description="指定行业 id（可选）"),
):
    """行业资金流多日趋势：主力净流入累计、5/20日、连续流入流出、排名变化。"""
    db = get_sync_db()
    try:
        return sector_flow_history(db, days=days, level=level, sector=sector)
    finally:
        db.close()


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
