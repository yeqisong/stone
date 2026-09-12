"""统一数据库写入函数。

接收标准化 dataclass 列表，执行 UPSERT 到对应的数据库表。
统一 UPSERT 写入（tushare 主源 + baostock 补充共用）。
"""
from typing import List
from sqlalchemy import text
from loguru import logger

from crawler.adapters.base import KlineRow, IndexKlineRow, FundamentalRow


def supplement_fundamentals_extra(db, rows: List[FundamentalRow]) -> int:
    """baostock 补充 ROE/营收/净利（tushare daily_basic 无此 3 字段）。

    用 baostock 补充器的 fetch_fundamentals_extra 结果，按 COALESCE 语义
    仅更新这 3 个字段（不覆盖已有值）。baostock 不可用时调用方自行降级。

    Args:
        db: 同步 session
        rows: 含 roe/revenue_yoy/profit_yoy 的 FundamentalRow 列表（可为空）

    Returns:
        实际更新的股票数
    """
    vals = [r for r in rows
            if r.roe is not None or r.revenue_yoy is not None or r.profit_yoy is not None]
    if not vals:
        return 0
    db.execute(text("""
        UPDATE stock_fundamentals sf SET
            roe = COALESCE(v.roe, sf.roe),
            revenue_yoy = COALESCE(v.revenue_yoy, sf.revenue_yoy),
            profit_yoy = COALESCE(v.profit_yoy, sf.profit_yoy)
        FROM (SELECT unnest(:codes) AS stock_code,
                     unnest(:roes) AS roe,
                     unnest(:revs) AS revenue_yoy,
                     unnest(:prfs) AS profit_yoy) v
        WHERE sf.stock_code = v.stock_code
    """), {
        "codes": [r.stock_code for r in vals],
        "roes": [r.roe for r in vals],
        "revs": [r.revenue_yoy for r in vals],
        "prfs": [r.profit_yoy for r in vals],
    })
    db.commit()
    return len(vals)


def _fill_names_from_master(db, rows: List[KlineRow]) -> None:
    """空名称行从 stock_master（证券主档）补全名称。

    tushare daily 接口不返回名称，写入端统一补全，避免每日行情持续写空名。
    """
    missing = [r for r in rows if not r.stock_name]
    if not missing:
        return
    codes = [(r.stock_code, r.exchange) for r in missing]
    try:
        res = db.execute(text("""
            SELECT DISTINCT ON (stock_code, exchange) stock_code, exchange, stock_name
            FROM stock_master
            WHERE (stock_code, exchange) IN (
                SELECT unnest(:codes), unnest(:exs))
              AND stock_name <> '' AND stock_name <> stock_code
        """), {"codes": [c for c, _ in codes], "exs": [e for _, e in codes]}).fetchall()
    except Exception as e:
        logger.warning(f"[writers] stock_master 名称补全查询失败: {e}")
        return
    name_map = {(r[0], r[1]): r[2] for r in res}
    for row in missing:
        row.stock_name = name_map.get((row.stock_code, row.exchange)) or row.stock_name


def _fill_index_names(db, rows: List[IndexKlineRow]) -> None:
    """空名称指数行从 index_daily_quote 已有名称继承（首次回填后即有参照）。"""
    missing = [r for r in rows if not r.index_name]
    if not missing:
        return
    codes = [r.index_code for r in missing]
    try:
        res = db.execute(text("""
            SELECT DISTINCT ON (index_code) index_code, index_name
            FROM index_daily_quote
            WHERE index_code = ANY(:codes) AND index_name <> ''
        """), {"codes": codes}).fetchall()
    except Exception as e:
        logger.warning(f"[writers] index 名称补全查询失败: {e}")
        return
    name_map = {r[0]: r[1] for r in res}
    for row in missing:
        row.index_name = name_map.get(row.index_code) or row.index_name


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

    # 零价/负价行一律丢弃：停牌日应"无行"而非写 0——写 0 会让回测估值把持仓
    # 按 0 计价（伪回撤）、让涨跌幅/标签出现 ±100% 的假跳变（2026-09-11 定位）
    bad = [r for r in rows if not (r.close is not None and r.close > 0)]
    if bad:
        logger.warning(f"[writers] 丢弃 {len(bad)} 行非正收盘价（如 {bad[0].stock_code} "
                       f"{bad[0].trade_date} close={bad[0].close}）——停牌应为无行")
        bad_ids = {id(r) for r in bad}
        rows = [r for r in rows if id(r) not in bad_ids]
    if not rows:
        return 0

    _fill_names_from_master(db, rows)

    total = 0
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        placeholders = []
        params = {}
        for j, row in enumerate(chunk):
            idx = start + j
            # 真实数据行 is_suspended=false（覆盖旧停牌标记）
            placeholders.append(
                f"(:td{idx},:ex{idx},:sc{idx},:sn{idx},"
                f":o{idx},:h{idx},:l{idx},:c{idx},:ch{idx},:cq{idx},"
                f":v{idx},:a{idx},:t{idx},false)"
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
            "volume,amount,turnover,is_suspended) "
            "VALUES " + ",".join(placeholders) +
            " ON CONFLICT (stock_code, exchange, trade_date) DO UPDATE SET "
            "stock_name=CASE WHEN EXCLUDED.stock_name='' THEN daily_quote.stock_name ELSE EXCLUDED.stock_name END, "
            "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
            "close=EXCLUDED.close, "
            "close_hfq=CASE WHEN EXCLUDED.close_hfq IS NULL OR EXCLUDED.close_hfq = 0 THEN daily_quote.close_hfq ELSE EXCLUDED.close_hfq END, "
            "close_qfq=EXCLUDED.close_qfq, volume=EXCLUDED.volume, "
            "amount=EXCLUDED.amount, turnover=EXCLUDED.turnover, "
            "is_suspended=false"
        )
        # SAVEPOINT 逐批隔离：单批失败只回滚该批，不毒化事务（前序批次照常提交）
        db.execute(text("SAVEPOINT sp_bf"))
        try:
            db.execute(text(sql), params)
            total += len(chunk)
            db.execute(text("RELEASE SAVEPOINT sp_bf"))
        except Exception as e:
            logger.error(f"[writers] batch_upsert_kline 异常 (batch {start}): {e}")
            try:
                db.execute(text("ROLLBACK TO SAVEPOINT sp_bf"))
            except Exception:
                pass
            # 降级逐行：单行脏数据（如超长代码）跳过，其余照常写入，避免整批丢失
            for row in chunk:
                try:
                    db.execute(text(
                        "INSERT INTO daily_quote (trade_date,exchange,stock_code,stock_name,"
                        "open,high,low,close,close_hfq,close_qfq,volume,amount,turnover,is_suspended) "
                        "VALUES (:td,:ex,:sc,:sn,:o,:h,:l,:c,:ch,:cq,:v,:a,:t,false) "
                        "ON CONFLICT (stock_code, exchange, trade_date) DO UPDATE SET "
                        "stock_name=CASE WHEN EXCLUDED.stock_name='' THEN daily_quote.stock_name ELSE EXCLUDED.stock_name END, "
                        "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
                        "close=EXCLUDED.close, "
                        "close_hfq=CASE WHEN EXCLUDED.close_hfq IS NULL OR EXCLUDED.close_hfq = 0 THEN daily_quote.close_hfq ELSE EXCLUDED.close_hfq END, "
                        "close_qfq=EXCLUDED.close_qfq, volume=EXCLUDED.volume, "
                        "amount=EXCLUDED.amount, turnover=EXCLUDED.turnover, "
                        "is_suspended=false"
                    ), {
                        'td': row.trade_date, 'ex': row.exchange, 'sc': row.stock_code,
                        'sn': row.stock_name, 'o': row.open, 'h': row.high, 'l': row.low,
                        'c': row.close, 'ch': row.close_hfq, 'cq': row.close,
                        'v': row.volume, 'a': row.amount, 't': row.turnover,
                    })
                    total += 1
                except Exception as ex:
                    logger.warning(f"[writers] 跳过坏行 {row.trade_date} {row.stock_code}: {ex}")
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

    _fill_index_names(db, rows)

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
            "index_name=CASE WHEN EXCLUDED.index_name='' THEN index_daily_quote.index_name ELSE EXCLUDED.index_name END, "
            "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
            "close=EXCLUDED.close, volume=EXCLUDED.volume, amount=EXCLUDED.amount"
        )
        # SAVEPOINT 逐批隔离：单批失败（如超长 index_code）只跳过该批，不毒化事务
        db.execute(text("SAVEPOINT sp_bf"))
        try:
            db.execute(text(sql), params)
            total += len(chunk)
            db.execute(text("RELEASE SAVEPOINT sp_bf"))
        except Exception as e:
            logger.error(f"[writers] batch_upsert_index_kline 异常 (batch {start}): {e}")
            try:
                db.execute(text("ROLLBACK TO SAVEPOINT sp_bf"))
            except Exception:
                pass
    db.commit()
    return total



def _enrich_market_cap(db, rows: List[FundamentalRow]) -> int:
    """从本站 daily_quote 补全缺失的 market_cap（tushare 已给则跳过）。

    对 rows 中 market_cap 为空的行，按 (code, 最新 close_hfq) × total_shares 估算。
    返回补全的股票数。
    """
    if not rows:
        return 0
    missing = [r for r in rows if not r.market_cap and r.total_shares]
    if not missing:
        return 0
    codes = [r.stock_code for r in missing]
    try:
        res = db.execute(text(
            "SELECT DISTINCT ON (stock_code) stock_code, close_hfq "
            "FROM daily_quote WHERE stock_code = ANY(:codes) "
            "AND close_hfq IS NOT NULL AND close_hfq > 0 "
            "ORDER BY stock_code, trade_date DESC"
        ), {"codes": codes}).fetchall()
    except Exception as e:
        logger.warning(f"[writers] 市值补全查询失败: {e}")
        return 0
    close_map = {r[0]: float(r[1]) for r in res}
    filled = 0
    for r in missing:
        close = close_map.get(r.stock_code)
        if close:
            r.market_cap = int(round(close * r.total_shares))
            filled += 1
    return filled


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
        params = {}
        for j, row in enumerate(chunk):
            idx = start + j
            params.update({
                f'c{idx}': row.stock_code,
                f'td{idx}': getattr(row, 'trade_date', '') or '',
                f'pe{idx}': row.pe_ttm,
                f'pe2{idx}': getattr(row, 'pe', None),
                f'pb{idx}': row.pb_mrq,
                f'ps{idx}': getattr(row, 'ps', None),
                f'ps2{idx}': getattr(row, 'ps_ttm', None),
                f'dvr{idx}': getattr(row, 'dv_ratio', None),
                f'dvt{idx}': getattr(row, 'dv_ttm', None),
                f'tr{idx}': getattr(row, 'turnover_rate', None),
                f'vr{idx}': getattr(row, 'volume_ratio', None),
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
            "(stock_code,trade_date,pe_ttm,pe,pb_mrq,ps,ps_ttm,dv_ratio,dv_ttm,"
            "turnover_rate,volume_ratio,total_shares,float_share,free_share,market_cap,circ_mv,"
            "roe,revenue_yoy,profit_yoy,limit_status,updated_at) "
            "VALUES " + ",".join(
                f"(:c{start+j},:td{start+j},:pe{start+j},:pe2{start+j},:pb{start+j},:ps{start+j},:ps2{start+j},:dvr{start+j},:dvt{start+j},"
                f":tr{start+j},:vr{start+j},:ts{start+j},:fs{start+j},:frs{start+j},:mc{start+j},:cm{start+j},"
                f":roe{start+j},:rev{start+j},:prf{start+j},:ls{start+j},CURRENT_TIMESTAMP)"
                for j in range(len(chunk))
            ) +
            " ON CONFLICT (stock_code) DO UPDATE SET "
            "trade_date=EXCLUDED.trade_date, "
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
            "updated_at=EXCLUDED.updated_at"
        )
        # SAVEPOINT 逐批隔离（对齐 batch_upsert_kline）：单批失败只回滚该批，
        # 否则事务 aborted 后所有后续批静默失败，末尾 commit 等价全量回滚但 total 虚报
        db.execute(text("SAVEPOINT sp_fund"))
        try:
            db.execute(text(sql), params)
            db.execute(text("RELEASE SAVEPOINT sp_fund"))
            total += len(chunk)
        except Exception as e:
            db.execute(text("ROLLBACK TO SAVEPOINT sp_fund"))
            logger.error(f"[writers] batch_upsert_fundamentals 异常 (batch {start}): {e}")
    db.commit()
    return total


def append_fundamentals_history(db, rows: List[FundamentalRow], batch_size: int = 500) -> int:
    """按交易日追加 daily_basic 字段到 stock_fundamentals_history（report_date 存交易日）。

    - tushare daily_basic 按日全市场：PE/PB/换手率/市值/股息率等日度历史（因子化数据源）
    - 幂等：UNIQUE(stock_code, report_date)，同日重复拉取 DO UPDATE（COALESCE 保护已有值）
    - 只写有任一日度字段的行；ROE/营收等季度字段不在日度行填充
    """
    vals = []
    for r in rows:
        td = getattr(r, 'trade_date', None)
        if not td:
            continue
        pe = r.pe_ttm if getattr(r, 'pe_ttm', None) is not None else getattr(r, 'pe', None)
        if (pe is None and r.pb_mrq is None and r.turnover_rate is None
                and r.circ_mv is None and r.dv_ttm is None):
            continue
        vals.append((r.stock_code, str(td)[:10], pe, r.pb_mrq, r.turnover_rate, r.volume_ratio,
                     r.circ_mv, r.market_cap, r.dv_ratio, r.dv_ttm, r.ps_ttm))
    if not vals:
        return 0

    total = 0
    for start in range(0, len(vals), batch_size):
        chunk = vals[start:start + batch_size]
        placeholders = []
        params = {}
        for j, (code, td, pe, pb, tr, vr, cmv, tmv, dvr, dvttm, psttm) in enumerate(chunk):
            idx = start + j
            placeholders.append(f"(:c{idx},:d{idx},:pe{idx},:pb{idx},:tr{idx},:vr{idx},:cmv{idx},:tmv{idx},:dvr{idx},:dvttm{idx},:psttm{idx})")
            params.update({f'c{idx}': code, f'd{idx}': td, f'pe{idx}': pe, f'pb{idx}': pb,
                           f'tr{idx}': tr, f'vr{idx}': vr, f'cmv{idx}': cmv, f'tmv{idx}': tmv,
                           f'dvr{idx}': dvr, f'dvttm{idx}': dvttm, f'psttm{idx}': psttm})
        sql = (
            "INSERT INTO stock_fundamentals_history "
            "(stock_code, report_date, pe_ttm, pb_mrq, turnover_rate, volume_ratio, circ_mv, total_mv, dv_ratio, dv_ttm, ps_ttm) "
            "VALUES " + ",".join(placeholders) +
            " ON CONFLICT (stock_code, report_date) DO UPDATE SET "
            "pe_ttm=COALESCE(EXCLUDED.pe_ttm, stock_fundamentals_history.pe_ttm), "
            "pb_mrq=COALESCE(EXCLUDED.pb_mrq, stock_fundamentals_history.pb_mrq), "
            "turnover_rate=COALESCE(EXCLUDED.turnover_rate, stock_fundamentals_history.turnover_rate), "
            "volume_ratio=COALESCE(EXCLUDED.volume_ratio, stock_fundamentals_history.volume_ratio), "
            "circ_mv=COALESCE(EXCLUDED.circ_mv, stock_fundamentals_history.circ_mv), "
            "total_mv=COALESCE(EXCLUDED.total_mv, stock_fundamentals_history.total_mv), "
            "dv_ratio=COALESCE(EXCLUDED.dv_ratio, stock_fundamentals_history.dv_ratio), "
            "dv_ttm=COALESCE(EXCLUDED.dv_ttm, stock_fundamentals_history.dv_ttm), "
            "ps_ttm=COALESCE(EXCLUDED.ps_ttm, stock_fundamentals_history.ps_ttm)"
        )
        # SAVEPOINT 逐批隔离：失败只回滚该批（原 rollback 会连带丢掉同调用内
        # 前序已成功批次，且 total 虚报落库行数）
        db.execute(text("SAVEPOINT sp_fh"))
        try:
            db.execute(text(sql), params)
            db.execute(text("RELEASE SAVEPOINT sp_fh"))
            total += len(chunk)
        except Exception as e:
            db.execute(text("ROLLBACK TO SAVEPOINT sp_fh"))
            logger.error(f"[writers] append_fundamentals_history 异常: {e}")
    db.commit()
    return total


def batch_upsert_moneyflow(db, records):
    """批量 UPSERT 资金流向（stock_moneyflow，PK trade_date+stock_code）。"""
    from sqlalchemy import text
    if not records:
        return 0
    sql = text("""
        INSERT INTO stock_moneyflow (trade_date, stock_code, stock_name,
            buy_lg_amt, sell_lg_amt, buy_elg_amt, sell_elg_amt,
            buy_md_amt, sell_md_amt, buy_sm_amt, sell_sm_amt, net_mf_amt)
        VALUES (:trade_date, :stock_code, :stock_name,
            :buy_lg_amt, :sell_lg_amt, :buy_elg_amt, :sell_elg_amt,
            :buy_md_amt, :sell_md_amt, :buy_sm_amt, :sell_sm_amt, :net_mf_amt)
        ON CONFLICT (trade_date, stock_code) DO UPDATE SET
            stock_name=EXCLUDED.stock_name, buy_lg_amt=EXCLUDED.buy_lg_amt,
            sell_lg_amt=EXCLUDED.sell_lg_amt, buy_elg_amt=EXCLUDED.buy_elg_amt,
            sell_elg_amt=EXCLUDED.sell_elg_amt, buy_md_amt=EXCLUDED.buy_md_amt,
            sell_md_amt=EXCLUDED.sell_md_amt, buy_sm_amt=EXCLUDED.buy_sm_amt,
            sell_sm_amt=EXCLUDED.sell_sm_amt, net_mf_amt=EXCLUDED.net_mf_amt
    """)
    saved = 0
    for i in range(0, len(records), 1000):
        db.execute(sql, records[i:i + 1000])
        db.commit()
        saved += len(records[i:i + 1000])
    return saved



# ── 拓展数据接入（step2 扩展）──

def batch_upsert_top_list(db, records):
    """龙虎榜：ON CONFLICT (trade_date, stock_code, reason) 幂等。"""
    from sqlalchemy import text
    if not records:
        return 0
    sql = text("""
        INSERT INTO stock_top_list (trade_date, stock_code, stock_name, close, pct_chg,
            turnover_ratio, total_amount, buy_amount, sell_amount, net_amount, reason)
        VALUES (:trade_date, :stock_code, :stock_name, :close, :pct_chg,
            :turnover_ratio, :total_amount, :buy_amount, :sell_amount, :net_amount, :reason)
        ON CONFLICT (trade_date, stock_code, reason) DO UPDATE SET
            stock_name=EXCLUDED.stock_name, close=EXCLUDED.close, pct_chg=EXCLUDED.pct_chg,
            turnover_ratio=EXCLUDED.turnover_ratio, total_amount=EXCLUDED.total_amount,
            buy_amount=EXCLUDED.buy_amount, sell_amount=EXCLUDED.sell_amount,
            net_amount=EXCLUDED.net_amount
    """)
    saved = 0
    for i in range(0, len(records), 500):
        db.execute(sql, records[i:i + 500])
        db.commit()
        saved += len(records[i:i + 500])
    return saved


def batch_upsert_margin_detail(db, records):
    from sqlalchemy import text
    if not records:
        return 0
    sql = text("""
        INSERT INTO stock_margin_detail (trade_date, stock_code, stock_name,
            fin_amount, fin_buy_amount, sec_amount, sec_sell_amount, total_amount)
        VALUES (:trade_date, :stock_code, :stock_name,
            :fin_amount, :fin_buy_amount, :sec_amount, :sec_sell_amount, :total_amount)
        ON CONFLICT (trade_date, stock_code) DO UPDATE SET
            stock_name=EXCLUDED.stock_name, fin_amount=EXCLUDED.fin_amount,
            fin_buy_amount=EXCLUDED.fin_buy_amount, sec_amount=EXCLUDED.sec_amount,
            sec_sell_amount=EXCLUDED.sec_sell_amount, total_amount=EXCLUDED.total_amount
    """)
    saved = 0
    for i in range(0, len(records), 1000):
        db.execute(sql, records[i:i + 1000])
        db.commit()
        saved += len(records[i:i + 1000])
    return saved


def batch_upsert_moneyflow_hsgt(db, records):
    from sqlalchemy import text
    if not records:
        return 0
    sql = text("""
        INSERT INTO moneyflow_hsgt (trade_date, ggt_ss, ggt_sz, hgt, sgt, north_money)
        VALUES (:trade_date, :ggt_ss, :ggt_sz, :hgt, :sgt, :north_money)
        ON CONFLICT (trade_date) DO UPDATE SET
            ggt_ss=EXCLUDED.ggt_ss, ggt_sz=EXCLUDED.ggt_sz,
            hgt=EXCLUDED.hgt, sgt=EXCLUDED.sgt, north_money=EXCLUDED.north_money
    """)
    db.execute(sql, records)
    db.commit()
    return len(records)


def batch_upsert_block_trade(db, records):
    from sqlalchemy import text
    if not records:
        return 0
    sql = text("""
        INSERT INTO block_trade (trade_date, stock_code, price, vol, amount, buyer, seller)
        VALUES (:trade_date, :stock_code, :price, :vol, :amount, :buyer, :seller)
        ON CONFLICT (trade_date, stock_code, price, vol) DO UPDATE SET
            amount=EXCLUDED.amount, buyer=EXCLUDED.buyer, seller=EXCLUDED.seller
    """)
    saved = 0
    for i in range(0, len(records), 500):
        db.execute(sql, records[i:i + 500])
        db.commit()
        saved += len(records[i:i + 500])
    return saved


def batch_insert_events(db, table, records, conflict_cols, all_cols):
    """事件表通用插入（forecast/express/dividend/share_float/repurchase）。

    conflict_cols 建有唯一索引；冲突即跳过（ON CONFLICT DO NOTHING，保留首见）。"""
    from sqlalchemy import text
    if not records:
        return 0
    col_str = ", ".join(all_cols)
    ph = ", ".join(f":{c}" for c in all_cols)
    conflict_ph = ", ".join(conflict_cols)
    sql = text(f"INSERT INTO {table} ({col_str}) VALUES ({ph}) ON CONFLICT ({conflict_ph}) DO NOTHING")
    saved = 0
    for i in range(0, len(records), 500):
        cur = db.execute(sql, records[i:i + 500])
        db.commit()
        saved += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    return saved


def batch_upsert_fina_indicator(db, records):
    from sqlalchemy import text
    if not records:
        return 0
    cols = ["stock_code", "end_date", "ann_date", "eps", "eps_ttm", "bps", "roe", "roe_waa",
            "roe_dt", "roa", "grossprofit_margin", "netprofit_margin", "ocf_to_or", "ocfps",
            "profit_dedt", "debt_to_assets", "current_ratio", "quick_ratio", "invturn",
            "ar_turn", "assets_turn", "netprofit_yoy", "netprofit_2yoy", "or_yoy", "or_2yoy"]
    col_str = ", ".join(cols)
    ph = ", ".join(f":{c}" for c in cols)
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in ("stock_code", "end_date"))
    sql = text(f"""
        INSERT INTO fina_indicator ({col_str}) VALUES ({ph})
        ON CONFLICT (stock_code, end_date) DO UPDATE SET {updates}
    """)
    saved = 0
    for i in range(0, len(records), 500):
        db.execute(sql, records[i:i + 500])
        db.commit()
        saved += len(records[i:i + 500])
    return saved


def batch_upsert_index_weight(db, records):
    from sqlalchemy import text
    if not records:
        return 0
    sql = text("""
        INSERT INTO index_weight (index_code, trade_date, stock_code, weight)
        VALUES (:index_code, :trade_date, :stock_code, :weight)
        ON CONFLICT (index_code, trade_date, stock_code) DO UPDATE SET weight=EXCLUDED.weight
    """)
    saved = 0
    for i in range(0, len(records), 1000):
        db.execute(sql, records[i:i + 1000])
        db.commit()
        saved += len(records[i:i + 1000])
    return saved
