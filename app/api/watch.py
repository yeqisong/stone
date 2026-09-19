"""自选/自定义分组 API — 持仓页 tab 化与详情页分组标签的共用后端。

分组模型：watch_groups（is_default=true 为「自选」固定组，不可删）+ watch_group_items。
「持仓」不是分组——它是 portfolio 表的固有状态，个股分组状态接口里单独带出。
"""
from fastapi import APIRouter, Query, HTTPException, Depends
from sqlalchemy import text

from app.db.connection import get_sync_db
from app.auth.auth import get_current_user
from app.api.stocks import ORDER_SQL_MAP, VALID_ORDER_BY, VALID_ORDER_DIR

router = APIRouter(tags=["watch"])


@router.get("/watch/groups")
def list_groups():
    """全部分组（自选恒排最前，动态组按创建序），带组内个股数。"""
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT g.id, g.group_name, g.is_default, g.sort_order,
                   (SELECT COUNT(*) FROM watch_group_items i WHERE i.group_id = g.id) AS cnt
            FROM watch_groups g
            ORDER BY g.is_default DESC, g.sort_order, g.id
        """)).fetchall()
        return {"groups": [
            {"id": r[0], "name": r[1], "is_default": r[2], "count": r[4]} for r in rows
        ]}
    finally:
        db.close()


@router.post("/watch/groups")
def create_group(body: dict, user: str = Depends(get_current_user)):
    """新增分组。body: {"name": "xx组"}"""
    name = (body.get("name") or "").strip()
    if not name or len(name) > 32:
        raise HTTPException(400, "分组名需 1~32 字符")
    if name == "自选" or name == "持仓":
        raise HTTPException(400, "该名称为保留名，请换一个")
    db = get_sync_db()
    try:
        try:
            gid = db.execute(text(
                "INSERT INTO watch_groups (group_name, is_default, sort_order) "
                "VALUES (:n, false, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM watch_groups)) RETURNING id"
            ), {"n": name}).scalar()
            db.commit()
            return {"ok": True, "id": gid, "name": name}
        except Exception as e:
            db.rollback()
            if "duplicate" in str(e).lower() or "unique" in str(e).lower():
                raise HTTPException(400, f"分组「{name}」已存在")
            raise
    finally:
        db.close()


@router.delete("/watch/groups/{group_id}")
def delete_group(group_id: int, user: str = Depends(get_current_user)):
    """删除动态分组（自选组不可删），组内个股随 CASCADE 清除。"""
    db = get_sync_db()
    try:
        is_default = db.execute(text(
            "SELECT is_default FROM watch_groups WHERE id=:g"), {"g": group_id}).scalar()
        if is_default is None:
            raise HTTPException(404, "分组不存在")
        if is_default:
            raise HTTPException(400, "自选组不可删除")
        db.execute(text("DELETE FROM watch_groups WHERE id=:g"), {"g": group_id})
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.get("/watch/groups/{group_id}/stocks")
def list_group_stocks(
    group_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=100),
    keyword: str = Query(""),
    order_by: str = Query("trade_date"),
    order_dir: str = Query("desc"),
):
    """组内个股列表 — 列字段与 /api/stocks 完全一致（代码/名称/交易所/最新价/涨跌幅/PE/行业/数据日期）。"""
    if order_by not in VALID_ORDER_BY:
        raise HTTPException(400, f"无效的 order_by: {order_by}")
    if order_dir not in VALID_ORDER_DIR:
        raise HTTPException(400, f"无效的 order_dir: {order_dir}")
    db = get_sync_db()
    try:
        exists = db.execute(text("SELECT 1 FROM watch_groups WHERE id=:g"), {"g": group_id}).scalar()
        if not exists:
            raise HTTPException(404, "分组不存在")
        offset = (page - 1) * page_size
        # 限定个股/ETF：指数代码与深市个股代码天然冲突（000001=上证指数/平安银行），
        # 不过滤会把同名脏行带进列表（个股列表 /api/stocks 同口径过滤）
        where = "sm.stock_type IN ('stock','etf') AND sm.stock_code IN (SELECT stock_code FROM watch_group_items WHERE group_id = :g)"
        params = {"g": group_id, "l": page_size, "o": offset}
        if keyword:
            where += " AND (sm.stock_code LIKE :k OR sm.stock_name LIKE :k)"
            params["k"] = f"%{keyword}%"
        total = db.execute(text(f"SELECT COUNT(*) FROM stock_master sm WHERE {where}"), params).scalar() or 0
        dir_sql = "DESC NULLS LAST" if order_dir == "desc" else "ASC NULLS LAST"
        order_sql = ORDER_SQL_MAP[order_by]
        # JOIN 直取 added_at（加入时间）：移除即删行、再加入为全新行——天然「取最后一次加入时间」
        rows = db.execute(text(f"""
            SELECT sm.stock_code, sm.stock_name, sm.exchange, sm.stock_type,
                   (SELECT dq.close FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1) AS price,
                   (SELECT dq.close FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1 OFFSET 1) AS prev_close,
                   (SELECT dq.trade_date FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1) AS trade_date,
                   sf.pe_ttm, sm.industry_l1, sm.industry_l2, wgi.added_at
            FROM stock_master sm
            JOIN watch_group_items wgi ON wgi.stock_code = sm.stock_code AND wgi.group_id = :g
            LEFT JOIN LATERAL (
                SELECT pe_ttm FROM stock_fundamentals f
                WHERE f.stock_code = sm.stock_code ORDER BY f.trade_date DESC LIMIT 1
            ) sf ON true
            WHERE {where}
            ORDER BY {order_sql} {dir_sql}, sm.stock_code
            LIMIT :l OFFSET :o
        """), params).fetchall()
        stocks = []
        for r in rows:
            p = float(r.price) if r.price else None
            pp = float(r.prev_close) if r.prev_close else None
            stocks.append({
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "exchange": r.exchange,
                "price": round(p, 3) if p else None,
                "chg_pct": round((p - pp) / pp * 100, 2) if p and pp else None,
                "pe_ttm": float(r.pe_ttm) if r.pe_ttm else None,
                "industry": (r.industry_l1 or "") + (("/" + r.industry_l2) if r.industry_l2 else ""),
                "trade_date": str(r.trade_date) if r.trade_date else None,
                "added_at": str(r.added_at)[:16] if r.added_at else None,
            })
        return {"stocks": stocks, "total": total, "page": page, "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size}
    finally:
        db.close()


@router.put("/watch/stock-groups")
def set_stock_groups(body: dict, user: str = Depends(get_current_user)):
    """整体设置一个个股的分组归属（详情页加组弹窗保存）。

    body: {"stock_code": "600000", "group_ids": [1, 3]} → 差量增删，未列出的组移除。
    """
    code = (body.get("stock_code") or "").strip()
    gids = body.get("group_ids") or []
    if not code:
        raise HTTPException(400, "缺少 stock_code")
    if not isinstance(gids, list) or any(not isinstance(g, int) for g in gids):
        raise HTTPException(400, "group_ids 需为整数数组")
    db = get_sync_db()
    try:
        # 组存在性校验（防写入悬空 id）
        if gids:
            n = db.execute(text(
                "SELECT COUNT(*) FROM watch_groups WHERE id = ANY(:g)"), {"g": gids}).scalar()
            if n != len(set(gids)):
                raise HTTPException(400, "存在无效分组")
        db.execute(text(
            "DELETE FROM watch_group_items WHERE stock_code=:c AND group_id <> ALL(CAST(:g AS int[]))"),
            {"c": code, "g": gids})
        if gids:
            # CAST 而非 ::int[] —— text() 会把 :g::int 解析成第二个绑定参数
            db.execute(text(
                "INSERT INTO watch_group_items (group_id, stock_code) "
                "SELECT g, :c FROM unnest(CAST(:g AS int[])) AS g "
                "ON CONFLICT (group_id, stock_code) DO NOTHING"),
                {"c": code, "g": gids})
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.get("/watch/stock-groups")
def get_stock_groups(code: str):
    """个股当前的分组状态（详情页标签行）：所属分组 + 是否持仓（portfolio 固有状态）。"""
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT g.id, g.group_name, g.is_default, i.added_at
            FROM watch_group_items i JOIN watch_groups g ON g.id = i.group_id
            WHERE i.stock_code = :c
            ORDER BY g.is_default DESC, g.sort_order, g.id
        """), {"c": code}).fetchall()
        in_portfolio = bool(db.execute(text(
            "SELECT 1 FROM portfolio WHERE stock_code=:c AND is_active=true AND quantity > 0 LIMIT 1"
        ), {"c": code}).scalar())
        return {"groups": [{"id": r[0], "name": r[1], "is_default": r[2],
                            "added_at": str(r[3])[:16] if r[3] else None} for r in rows],
                "in_portfolio": in_portfolio}
    finally:
        db.close()
