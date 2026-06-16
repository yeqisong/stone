"""统一数据库写入函数。

接收标准化 dataclass 列表，执行 UPSERT 到对应的数据库表。
从 BaostockCrawler._batch_insert_rows() 提取并泛化。

设计参考：design/data-source-adapter-design.md 第八章 §8.3
"""
from typing import List
from sqlalchemy import text
from loguru import logger

from crawler.adapters.base import KlineRow, IndexKlineRow, FundamentalRow


def batch_upsert_kline(db, rows: List[KlineRow], batch_size: int = 200) -> int:
    """批量 UPSERT 个股/ETF 日K线到 daily_quote 表。

    Args:
        db: SQLAlchemy 同步 session
        rows: KlineRow 列表（已标准化：volume 为股，amount 为元）
        batch_size: 每批提交行数

    Returns:
        成功写入的行数
    """
    if not rows:
        return 0

    total = 0
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        placeholders = []
        params = {}
        for j, row in enumerate(chunk):
            idx = start + j
            placeholders.append(
                f"(:td{idx},:ex{idx},:sc{idx},:sn{idx},"
                f":o{idx},:h{idx},:l{idx},:c{idx},:ch{idx},:cq{idx},"
                f":v{idx},:a{idx},:t{idx})"
            )
            params.update({
                f'td{idx}': row.trade_date,
                f'ex{idx}': row.exchange,
                f'sc{idx}': row.stock_code,
                f'sn{idx}': row.stock_name,
                f'o{idx}': row.open,
                f'h{idx}': row.high,
                f'l{idx}': row.low,
                f'c{idx}': row.close,
                f'ch{idx}': row.close_hfq,
                f'cq{idx}': row.close,  # close_qfq = 不复权 close
                f'v{idx}': row.volume,
                f'a{idx}': row.amount,
                f't{idx}': row.turnover,
            })

        sql = (
            "INSERT INTO daily_quote "
            "(trade_date,exchange,stock_code,stock_name,"
            "open,high,low,close,close_hfq,close_qfq,"
            "volume,amount,turnover) "
            "VALUES " + ",".join(placeholders) +
            " ON CONFLICT (stock_code, exchange, trade_date) DO UPDATE SET "
            "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
            "close=EXCLUDED.close, close_hfq=EXCLUDED.close_hfq, "
            "close_qfq=EXCLUDED.close_qfq, volume=EXCLUDED.volume, "
            "amount=EXCLUDED.amount, turnover=EXCLUDED.turnover"
        )
        try:
            db.execute(text(sql), params)
            total += len(chunk)
        except Exception as e:
            logger.error(f"[writers] batch_upsert_kline 异常 (batch {start}): {e}")
    db.commit()
    return total


def batch_upsert_index_kline(db, rows: List[IndexKlineRow], batch_size: int = 200) -> int:
    """批量 UPSERT 指数日K线到 index_daily_quote 表。

    Args:
        db: SQLAlchemy 同步 session
        rows: IndexKlineRow 列表
        batch_size: 每批提交行数

    Returns:
        成功写入的行数
    """
    if not rows:
        return 0

    total = 0
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        placeholders = []
        params = {}
        for j, row in enumerate(chunk):
            idx = start + j
            placeholders.append(
                f"(:d{idx},:c{idx},:n{idx},:o{idx},:h{idx},:l{idx},:cl{idx},:v{idx},:a{idx})"
            )
            params.update({
                f'd{idx}': row.trade_date,
                f'c{idx}': row.index_code,
                f'n{idx}': row.index_name,
                f'o{idx}': row.open,
                f'h{idx}': row.high,
                f'l{idx}': row.low,
                f'cl{idx}': row.close,
                f'v{idx}': row.volume,
                f'a{idx}': row.amount,
            })

        sql = (
            "INSERT INTO index_daily_quote "
            "(trade_date,index_code,index_name,open,high,low,close,volume,amount) "
            "VALUES " + ",".join(placeholders) +
            " ON CONFLICT (trade_date,index_code) DO UPDATE SET "
            "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
            "close=EXCLUDED.close, volume=EXCLUDED.volume, amount=EXCLUDED.amount"
        )
        try:
            db.execute(text(sql), params)
            total += len(chunk)
        except Exception as e:
            logger.error(f"[writers] batch_upsert_index_kline 异常 (batch {start}): {e}")
    db.commit()
    return total


def _enrich_market_cap(db, rows: List[FundamentalRow]) -> int:
    """从本站 daily_quote 表补全市值和总股本（不依赖 baostock K 线 API）。

    依赖链：基本面模块在 DAG 中排在 K 线之后，daily_quote 已有最新数据。
    往前查找最新一条 volume > 0 的 K 线来推算市值。
    极端情况（完全没有交易记录）：市值保持为 None，调用方后续可设 0。
    """
    if not rows:
        return 0
    codes = [r.stock_code for r in rows if r.market_cap is None]
    if not codes:
        return 0

    latest = db.execute(text("""
        SELECT DISTINCT ON (stock_code) stock_code, close, volume, turnover
        FROM daily_quote
        WHERE stock_code = ANY(:codes) AND volume > 0 AND turnover > 0
        ORDER BY stock_code, trade_date DESC
    """), {"codes": codes}).fetchall()

    kline_map = {}
    for r in latest:
        try:
            kline_map[r[0]] = (float(r[1]), float(r[2]), float(r[3]))
        except (ValueError, TypeError):
            pass

    enriched = 0
    for row in rows:
        if row.market_cap is not None:
            continue
        data = kline_map.get(row.stock_code)
        if not data:
            continue
        close, volume, turnover = data
        if turnover > 0:
            total_shares = int(volume / (turnover / 100))
            row.total_shares = total_shares
            row.market_cap = int(close * total_shares)
            enriched += 1
    return enriched


def batch_upsert_fundamentals(db, rows: List[FundamentalRow], batch_size: int = 100) -> int:
    """批量 UPSERT 基本面数据到 stock_fundamentals 表。

    写入前自动从本站 daily_quote 补全市值（不依赖数据源 K 线 API）。
    DAG 保证 K 线数据先于基本面完成，极端情况查不到交易数据时市值留空。

    Args:
        db: SQLAlchemy 同步 session
        rows: FundamentalRow 列表
        batch_size: 每批提交行数

    Returns:
        成功写入的行数
    """
    # 从本站 daily_quote 补全市值
    enriched = _enrich_market_cap(db, rows)
    if enriched:
        logger.info(f"[writers] 从 daily_quote 补全 {enriched} 只股票的市值")
    if not rows:
        return 0

    total = 0
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        placeholders = []
        params = {}
        for j, row in enumerate(chunk):
            idx = start + j
            placeholders.append(
                f"(:c{idx},:sn{idx},:pe{idx},:pb{idx},:ind{idx},"
                f":roe{idx},:rev{idx},:prf{idx},:ts{idx},:mc{idx})"
            )
            params.update({
                f'c{idx}': row.stock_code,
                f'sn{idx}': row.stock_name,
                f'pe{idx}': row.pe_ttm,
                f'pb{idx}': row.pb_mrq,
                f'ind{idx}': row.industry,
                f'roe{idx}': row.roe,
                f'rev{idx}': row.revenue_yoy,
                f'prf{idx}': row.profit_yoy,
                f'ts{idx}': row.total_shares,
                f'mc{idx}': row.market_cap,
            })

        sql = (
            "INSERT INTO stock_fundamentals "
            "(stock_code,stock_name,pe_ttm,pb_mrq,industry,"
            "roe,revenue_yoy,profit_yoy,total_shares,market_cap,updated_at) "
            "VALUES " + ",".join(
                f"(:c{start+j},:sn{start+j},:pe{start+j},:pb{start+j},:ind{start+j},"
                f":roe{start+j},:rev{start+j},:prf{start+j},:ts{start+j},:mc{start+j},CURRENT_TIMESTAMP)"
                for j in range(len(chunk))
            ) +
            " ON CONFLICT (stock_code) DO UPDATE SET "
            "stock_name=EXCLUDED.stock_name, pe_ttm=EXCLUDED.pe_ttm, "
            "pb_mrq=EXCLUDED.pb_mrq, industry=EXCLUDED.industry, "
            "roe=EXCLUDED.roe, revenue_yoy=EXCLUDED.revenue_yoy, "
            "profit_yoy=EXCLUDED.profit_yoy, total_shares=EXCLUDED.total_shares, "
            "market_cap=EXCLUDED.market_cap, updated_at=EXCLUDED.updated_at"
        )
        try:
            db.execute(text(sql), params)
            total += len(chunk)
        except Exception as e:
            logger.error(f"[writers] batch_upsert_fundamentals 异常 (batch {start}): {e}")
    db.commit()
    return total
