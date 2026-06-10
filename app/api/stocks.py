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
):
    """分页获取列表（按最新收盘价降序）。

    category 基于 stock_master.stock_type 字段过滤:
      - stock: 仅个股
      - index: 仅指数
      - etf:   仅ETF/LOF
      - bond:  仅可转债/债券
      - all:   全部
    """
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

        base_sql = f"""SELECT sm.stock_code, sm.stock_name, sm.exchange, sm.stock_type,
               (SELECT dq.close FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1) as price,
               (SELECT dq.trade_date FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1) as trade_date,
               (SELECT COUNT(*) FROM daily_quote dq WHERE dq.stock_code=sm.stock_code) as data_rows,
               sf.pe_ttm, sf.pb_mrq, sf.industry, sf.roe
        FROM stock_master sm LEFT JOIN stock_fundamentals sf ON sf.stock_code=sm.stock_code
        WHERE {where_base}
        ORDER BY price DESC NULLS LAST LIMIT :l OFFSET :o"""

        result = db.execute(text(base_sql), {"k": like, "l": page_size, "o": offset})

        stocks = []
        for r in result.fetchall():
            stocks.append({
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "exchange": r.exchange,
                "stock_type": r.stock_type or "",
                "price": float(r.price) if r.price else None,
                "trade_date": str(r.trade_date) if r.trade_date else None,
                "data_rows": r.data_rows,
                "pe_ttm": float(r.pe_ttm) if r.pe_ttm else None,
                "pb_mrq": float(r.pb_mrq) if r.pb_mrq else None,
                "industry": r.industry or "",
                "roe": float(r.roe) if r.roe else None,
            })

        return {
            "page": page, "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
            "stocks": stocks,
        }
    finally:
        db.close()
