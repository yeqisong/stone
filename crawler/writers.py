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

        # 真实数据行 is_suspended=false（覆盖旧停牌标记）；末尾追加 false 值
        vals = ",".join(placeholders)
        sql = (
            "INSERT INTO daily_quote "
            "(trade_date,exchange,stock_code,stock_name,"
            "open,high,low,close,close_hfq,close_qfq,"
            "volume,amount,turnover,is_suspended) "
            "VALUES " + vals[:-1] + ",false) " +
            " ON CONFLICT (stock_code, exchange, trade_date) DO UPDATE SET "
            "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
            "close=EXCLUDED.close, "
            "close_hfq=CASE WHEN EXCLUDED.close_hfq IS NULL OR EXCLUDED.close_hfq = 0 THEN daily_quote.close_hfq ELSE EXCLUDED.close_hfq END, "
            "close_qfq=EXCLUDED.close_qfq, volume=EXCLUDED.volume, "
            "amount=EXCLUDED.amount, turnover=EXCLUDED.turnover, "
            "is_suspended=false"
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
                f":roe{idx},:rev{idx},:prf{idx},:ts{idx},:mc{idx},"
                f":ps{idx},:pe2{idx},:dvr{idx},:dvt{idx},:tr{idx},:vr{idx},:frs{idx},:ls{idx},:td{idx})"
            )
            params.update({
                f'c{idx}': row.stock_code,
                f'td{idx}': getattr(row, 'trade_date', '') or '',
                f'sn{idx}': row.stock_name,
                f'pe{idx}': row.pe_ttm,
                f'pe2{idx}': getattr(row, 'pe', None),
                f'pb{idx}': row.pb_mrq,
                f'ps{idx}': getattr(row, 'ps', None),
                f'ps2{idx}': getattr(row, 'ps_ttm', None),
                f'dvr{idx}': getattr(row, 'dv_ratio', None),
                f'dvt{idx}': getattr(row, 'dv_ttm', None),
                f'tr{idx}': getattr(row, 'turnover_rate', None),
                f'vr{idx}': getattr(row, 'volume_ratio', None),
                f'ind{idx}': row.industry,
                f'roe{idx}': row.roe,
                f'rev{idx}': row.revenue_yoy,
                f'prf{idx}': row.profit_yoy,
                f'ts{idx}': row.total_shares,
                f'fs{idx}': getattr(row, 'float_share', None),
                f'frs{idx}': getattr(row, 'free_share', None),
                f'mc{idx}': row.market_cap,
                f'cm{idx}': getattr(row, 'circ_mv', None),
                f'ls{idx}': getattr(row, 'limit_status', None),
            })

        sql = (
            "INSERT INTO stock_fundamentals "
            "(stock_code,trade_date,stock_name,pe_ttm,pe,pb_mrq,ps,ps_ttm,dv_ratio,dv_ttm,"
            "turnover_rate,volume_ratio,total_shares,float_share,free_share,market_cap,circ_mv,"
            "roe,revenue_yoy,profit_yoy,limit_status,industry,updated_at) "
            "VALUES " + ",".join(
                f"(:c{start+j},:td{start+j},:sn{start+j},:pe{start+j},:pe2{start+j},:pb{start+j},:ps{start+j},:ps2{start+j},:dvr{start+j},:dvt{start+j},"
                f":tr{start+j},:vr{start+j},:ts{start+j},:fs{start+j},:frs{start+j},:mc{start+j},:cm{start+j},"
                f":roe{start+j},:rev{start+j},:prf{start+j},:ls{start+j},:ind{start+j},CURRENT_TIMESTAMP)"
                for j in range(len(chunk))
            ) +
            " ON CONFLICT (stock_code, trade_date) DO UPDATE SET "
            "stock_name=COALESCE(EXCLUDED.stock_name, stock_fundamentals.stock_name), "
            "pe_ttm=COALESCE(EXCLUDED.pe_ttm, stock_fundamentals.pe_ttm), "
            "pe=COALESCE(EXCLUDED.pe, stock_fundamentals.pe), "
            "pb_mrq=COALESCE(EXCLUDED.pb_mrq, stock_fundamentals.pb_mrq), "
            "ps=COALESCE(EXCLUDED.ps, stock_fundamentals.ps), "
            "ps_ttm=COALESCE(EXCLUDED.ps_ttm, stock_fundamentals.ps_ttm), "
            "dv_ratio=COALESCE(EXCLUDED.dv_ratio, stock_fundamentals.dv_ratio), "
            "dv_ttm=COALESCE(EXCLUDED.dv_ttm, stock_fundamentals.dv_ttm), "
            "turnover_rate=COALESCE(EXCLUDED.turnover_rate, stock_fundamentals.turnover_rate), "
            "volume_ratio=COALESCE(EXCLUDED.volume_ratio, stock_fundamentals.volume_ratio), "
            "total_shares=COALESCE(EXCLUDED.total_shares, stock_fundamentals.total_shares), "
            "float_share=COALESCE(EXCLUDED.float_share, stock_fundamentals.float_share), "
            "free_share=COALESCE(EXCLUDED.free_share, stock_fundamentals.free_share), "
            "market_cap=COALESCE(EXCLUDED.market_cap, stock_fundamentals.market_cap), "
            "circ_mv=COALESCE(EXCLUDED.circ_mv, stock_fundamentals.circ_mv), "
            "roe=COALESCE(EXCLUDED.roe, stock_fundamentals.roe), "
            "revenue_yoy=COALESCE(EXCLUDED.revenue_yoy, stock_fundamentals.revenue_yoy), "
            "profit_yoy=COALESCE(EXCLUDED.profit_yoy, stock_fundamentals.profit_yoy), "
            "limit_status=COALESCE(EXCLUDED.limit_status, stock_fundamentals.limit_status), "
            "industry=COALESCE(EXCLUDED.industry, stock_fundamentals.industry), "
            "updated_at=EXCLUDED.updated_at"
        )
        try:
            db.execute(text(sql), params)
            total += len(chunk)
        except Exception as e:
            logger.error(f"[writers] batch_upsert_fundamentals 异常 (batch {start}): {e}")
    db.commit()
    return total
