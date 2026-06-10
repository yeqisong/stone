"""个股列表 API — 分页浏览 + 搜索，支持个股/指数分类。"""
from fastapi import APIRouter, Query
from sqlalchemy import text

from app.db.connection import get_sync_db

router = APIRouter(tags=["stocks"])


@router.get("/stocks")
def list_stocks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=10, le=100),
    keyword: str = Query("", description="搜索代码或名称"),
    category: str = Query("stock", description="stock|index|etf|bond|all"),
    order_by: str = Query("trade_date", description="排序字段: price|chg_pct|pe_ttm|trade_date"),
    order_dir: str = Query("desc", description="排序方向: asc|desc"),
):
    """分页获取列表。支持排序: price, chg_pct, pe_ttm 升序/降序。"""
    db = get_sync_db()
    try:
        offset = (page - 1) * page_size
        like = f"%{keyword}%"

        type_map = {"stock": "stock", "index": "index", "etf": "etf", "bond": "bond"}
        category_clause = ""
        if category in type_map:
            category_clause = f" AND sm.stock_type = '{type_map[category]}'"

        where_base = "sm.status='N'" + category_clause
        if keyword:
            where_base += " AND (sm.stock_code LIKE :k OR sm.stock_name LIKE :k)"

        result = db.execute(text(f"SELECT COUNT(*) FROM stock_master sm WHERE {where_base}"), {"k": like})
        total = result.scalar() or 0

        # 动态排序
        dir_sql = "DESC NULLS LAST" if order_dir == "desc" else "ASC NULLS LAST"
        if order_by == "price":
            order_sql = "(SELECT dq.close FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1)"
        elif order_by == "pe_ttm":
            order_sql = "sf.pe_ttm"
        elif order_by == "trade_date":
            order_sql = "trade_date"
        elif order_by == "chg_pct":
            order_sql = ("(SELECT dq.close FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1) - "
                        "(SELECT dq.close FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1 OFFSET 1)")
        else:
            order_sql = "(SELECT dq.close FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1)"
        order_clause = f"ORDER BY {order_sql} {dir_sql}"

        base_sql = f"""SELECT sm.stock_code, sm.stock_name, sm.exchange, sm.stock_type,
               (SELECT dq.close FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1) as price,
               (SELECT dq.close FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1 OFFSET 1) as prev_close,
               (SELECT dq.trade_date FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1) as trade_date,
               (SELECT COUNT(*) FROM daily_quote dq WHERE dq.stock_code=sm.stock_code) as data_rows,
               sf.pe_ttm, sf.pb_mrq, sf.industry, sf.roe, sf.market_cap
        FROM stock_master sm LEFT JOIN stock_fundamentals sf ON sf.stock_code=sm.stock_code
        WHERE {where_base}
        {order_clause} LIMIT :l OFFSET :o"""

        result = db.execute(text(base_sql), {"k": like, "l": page_size, "o": offset})

        stocks = []
        for r in result.fetchall():
            p = float(r.price) if r.price else None
            pp = float(r.prev_close) if r.prev_close else None
            stocks.append({
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "exchange": r.exchange,
                "stock_type": r.stock_type or "",
                "price": p,
                "prev_close": pp,
                "chg_pct": round((p - pp) / pp * 100, 2) if p and pp else None,
                "trade_date": str(r.trade_date) if r.trade_date else None,
                "data_rows": r.data_rows,
                "pe_ttm": float(r.pe_ttm) if r.pe_ttm else None,
                "pb_mrq": float(r.pb_mrq) if r.pb_mrq else None,
                "industry": r.industry or "",
                "roe": float(r.roe) if r.roe else None,
                "market_cap": float(r.market_cap) if r.market_cap else None,
            })

        return {
            "page": page, "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
            "stocks": stocks,
        }
    finally:
        db.close()
