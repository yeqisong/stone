"""个股列表 API — 分页浏览 + 搜索，支持个股/指数分类。"""
from fastapi import APIRouter, Query, HTTPException
from sqlalchemy import text

from app.db.connection import get_sync_db

router = APIRouter(tags=["stocks"])

# 白名单验证
VALID_CATEGORIES = {"stock", "index", "etf", "bond", "all"}
VALID_ORDER_BY = {"price", "chg_pct", "pe_ttm", "trade_date", "market_cap"}
VALID_ORDER_DIR = {"asc", "desc"}

ORDER_SQL_MAP = {
    "price": "(SELECT dq.close FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1)",
    "pe_ttm": "sf.pe_ttm",
    "trade_date": "trade_date",
    "market_cap": "sf.market_cap",
    "chg_pct": ("(SELECT dq.close FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1) - "
                "(SELECT dq.close FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1 OFFSET 1)"),
}


@router.get("/stocks")
def list_stocks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=10, le=100),
    keyword: str = Query("", description="搜索代码或名称"),
    category: str = Query("stock"),
    order_by: str = Query("trade_date"),
    order_dir: str = Query("desc"),
):
    """分页获取列表。支持排序: price, chg_pct, pe_ttm, trade_date 升序/降序。"""
    # 参数白名单验证
    if category not in VALID_CATEGORIES:
        raise HTTPException(400, f"无效的 category: {category}")
    if order_by not in VALID_ORDER_BY:
        raise HTTPException(400, f"无效的 order_by: {order_by}")
    if order_dir not in VALID_ORDER_DIR:
        raise HTTPException(400, f"无效的 order_dir: {order_dir}")

    db = get_sync_db()
    try:
        offset = (page - 1) * page_size
        like = f"%{keyword}%"

        type_map = {"stock": "stock", "index": "index", "etf": "etf", "bond": "bond", "all": None}
        stype = type_map[category]
        params = {"k": like, "l": page_size, "o": offset}

        # 用参数化查询替代 f-string
        if stype:
            where_base = "sm.status='N' AND sm.stock_type = :stype"
            params["stype"] = stype
        else:
            where_base = "sm.status='N'"

        if keyword:
            where_base += " AND (sm.stock_code LIKE :k OR sm.stock_name LIKE :k)"

        cnt_sql = f"SELECT COUNT(*) FROM stock_master sm WHERE {where_base}"
        result = db.execute(text(cnt_sql), params)
        total = result.scalar() or 0

        # order_sql 来自白名单 ORDER_SQL_MAP（硬编码 SQL 片段），dir_sql 来自白名单 "ASC"/"DESC"
        # 两者均不可由用户任意控制，安全。如需扩展，保持此白名单模式。
        dir_sql = "DESC NULLS LAST" if order_dir == "desc" else "ASC NULLS LAST"
        order_sql = ORDER_SQL_MAP[order_by]

        base_sql = f"""SELECT sm.stock_code, sm.stock_name, sm.exchange, sm.stock_type,
               CASE WHEN sm.stock_type='index' THEN
                   (SELECT iq.close FROM index_daily_quote iq WHERE iq.index_code=sm.stock_code ORDER BY iq.trade_date DESC LIMIT 1)
               ELSE
                   (SELECT dq.close FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1)
               END as price,
               CASE WHEN sm.stock_type='index' THEN
                   (SELECT iq.close FROM index_daily_quote iq WHERE iq.index_code=sm.stock_code ORDER BY iq.trade_date DESC LIMIT 1 OFFSET 1)
               ELSE
                   (SELECT dq.close FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1 OFFSET 1)
               END as prev_close,
               CASE WHEN sm.stock_type='index' THEN
                   (SELECT iq.trade_date FROM index_daily_quote iq WHERE iq.index_code=sm.stock_code ORDER BY iq.trade_date DESC LIMIT 1)
               ELSE
                   (SELECT dq.trade_date FROM daily_quote dq WHERE dq.stock_code=sm.stock_code ORDER BY dq.trade_date DESC LIMIT 1)
               END as trade_date,
               CASE WHEN sm.stock_type='index' THEN
                   (SELECT COUNT(*) FROM index_daily_quote iq WHERE iq.index_code=sm.stock_code)
               ELSE
                   (SELECT COUNT(*) FROM daily_quote dq WHERE dq.stock_code=sm.stock_code)
               END as data_rows,
               sf.pe_ttm, sf.pb_mrq, sf.roe, sf.market_cap,
               sm.industry_l1, sm.industry_l2
        FROM stock_master sm
        -- LATERAL 取最新一条基本面（避免 stock_fundamentals 多日期行导致笛卡尔爆炸）
        LEFT JOIN LATERAL (
            SELECT pe_ttm, pb_mrq, roe, market_cap
            FROM stock_fundamentals f
            WHERE f.stock_code = sm.stock_code
            ORDER BY f.trade_date DESC
            LIMIT 1
        ) sf ON true
        WHERE {where_base}
        ORDER BY {order_sql} {dir_sql} LIMIT :l OFFSET :o"""

        result = db.execute(text(base_sql), params)

        stocks = []
        for r in result.fetchall():
            p = float(r.price) if r.price else None
            pp = float(r.prev_close) if r.prev_close else None
            l1 = r.industry_l1 or ""
            l2 = r.industry_l2 or ""
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
                "industry": (l1 + " > " + l2) if (l1 and l2) else (l1 or l2),
                "industry_l1": l1,
                "industry_l2": l2,
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

# ── 股票输入建议（suggest）──
# 数字 = 代码前缀；中文 = 名称前缀；字母 = 名称前缀 或 拼音首字母前缀。
# 拼音首字母映射进程内缓存（pypinyin，首查构建，~6000 只 <100ms）。
_py_cache = {"initials": None}


def _get_initials_map(db):
    if _py_cache["initials"] is None:
        from pypinyin import lazy_pinyin, Style
        # stock_type ASC：复合主键同代码多类型（如 000001 平安银行/上证指数）时，
        # stock 行最后写入 dict 覆盖 index 行（'stock' 字母序最大）
        rows = db.execute(text(
            "SELECT stock_code, stock_name FROM stock_master "
            "WHERE stock_type IN ('stock','etf','index') AND status='N' "
            "ORDER BY stock_type ASC"
        )).fetchall()
        m = {}
        for code, name in rows:
            try:
                parts = lazy_pinyin(name or '', style=Style.FIRST_LETTER, errors='ignore')
                ini = ''.join(p for p in parts if p and p[0].isascii()).lower()
            except Exception:
                ini = ''
            m[code] = (name or '', ini)
        _py_cache["initials"] = m
    return _py_cache["initials"]


@router.get("/stocks/suggest")
def suggest_stocks(
    q: str = Query("", max_length=20),
    limit: int = Query(10, ge=1, le=20),
):
    """股票输入建议：代码/名称/拼音首字母 前匹配 TopN。"""
    q = (q or '').strip()
    if not q:
        return {"items": []}
    db = get_sync_db()
    try:
        out = []
        seen = set()

        def add(code, name):
            if code not in seen:
                seen.add(code)
                out.append({"code": code, "name": name})

        if q[0].isdigit():
            rows = db.execute(text(
                "SELECT stock_code, stock_name FROM stock_master "
                "WHERE stock_code LIKE :q AND stock_type IN ('stock','etf','index') AND status='N' "
                "ORDER BY stock_type DESC, stock_code LIMIT :l"
            ), {"q": q + '%', "l": limit}).fetchall()
            for c, n in rows:
                add(c, n)
        else:
            ql = q.lower()
            # 1) 名称前匹配（中文输入主路径）
            rows = db.execute(text(
                "SELECT stock_code, stock_name FROM stock_master "
                "WHERE stock_name LIKE :q AND stock_type IN ('stock','etf','index') AND status='N' "
                "ORDER BY stock_type DESC, stock_code LIMIT :l"
            ), {"q": q + '%', "l": limit}).fetchall()
            for c, n in rows:
                add(c, n)
            # 2) 字母输入：拼音首字母前匹配（进程内缓存）
            if q[0].isascii() and len(out) < limit:
                initials = _get_initials_map(db)
                for c in sorted(initials):
                    name, ini = initials[c]
                    if ini.startswith(ql):
                        add(c, name)
                        if len(out) >= limit:
                            break
        return {"items": out[:limit]}
    finally:
        db.close()

