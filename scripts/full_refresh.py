#!/usr/bin/env python3
"""
全量数据刷新脚本（生产环境使用）。

顺序:
  1. 交易日历（从 baostock 同步）
  2. A 股日K线（全部重新下载，覆盖旧数据）
  3. 指数日K线（全部重新下载）
  4. ETF 日K线（全部重新下载）
  5. 基本面数据（PE/PB/ROE/行业/增长率，覆盖）
  6. 市值数据（从 baostock 最新行情计算并更新）

用法:
  docker compose exec app python3 scripts/full_refresh.py
"""
import sys, time, os
from datetime import date, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from app.db.connection import get_sync_db
from sqlalchemy import text


def step_calendar():
    """1. 刷新交易日历"""
    logger.info("===== [1/6] 交易日历 =====")
    from crawler.trade_calendar import sync_from_baostock
    db = get_sync_db()
    sync_from_baostock(db, start_year=2020, end_year=2030)
    db.close()


def step_kline():
    """2. 全量重刷 A 股日K线"""
    logger.info("===== [2/6] A 股日K线 =====")
    from scripts.refresh_kline import refresh_all
    refresh_all(start_date="2021-01-01")


def step_index():
    """3. 全量指数日K线"""
    logger.info("===== [3/6] 指数日K线 =====")
    import baostock as bs
    db = get_sync_db()
    lg = bs.login()
    if lg.error_code != '0':
        logger.error("baostock 登录失败")
        return

    rs = bs.query_stock_basic()
    index_codes = []
    while rs.next():
        d = rs.get_row_data()
        if len(d) > 4 and d[4] == '2':  # 类型 '2' = 指数
            raw = d[0]
            for p in ('sh.', 'sz.', 'bj.'):
                if raw.startswith(p):
                    code = raw[len(p):]
                    break
            else:
                code = raw
            index_codes.append((raw, code, d[1]))

    logger.info(f"  指数列表: {len(index_codes)} 只")
    fields = 'date,code,open,high,low,close,volume,amount'
    total = 0
    for bs_code, index_code, name in index_codes:
        try:
            rs2 = bs.query_history_k_data_plus(
                bs_code, fields, start_date='2021-01-01', end_date=date.today().isoformat(),
                frequency='d', adjustflag='3')
            while rs2.next():
                d2 = rs2.get_row_data()
                if not d2[0]: continue
                db.execute(text(
                    "INSERT INTO index_daily_quote "
                    "(trade_date, index_code, index_name, open, high, low, close, volume, amount) "
                    "VALUES (:d,:c,:n,:o,:h,:l,:cl,:v,:a) "
                    "ON CONFLICT (trade_date, index_code) DO UPDATE SET "
                    "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
                    "close=EXCLUDED.close, volume=EXCLUDED.volume, amount=EXCLUDED.amount"
                ), {"d": d2[0],"c": index_code,"n": name,
                    "o": float(d2[2]) if d2[2] else 0,
                    "h": float(d2[3]) if d2[3] else 0,
                    "l": float(d2[4]) if d2[4] else 0,
                    "cl": float(d2[5]) if d2[5] else 0,
                        "v": int(float(d2[6])) if d2[6] else 0,
                        "a": float(d2[7]) if d2[7] else 0})
                total += 1
            db.commit()
            time.sleep(0.1)
        except Exception as e:
            logger.warning(f"  指数 {bs_code} 失败: {e}")
    bs.logout()
    db.close()
    logger.info(f"  指数完成: {total} 条")


def step_etf():
    """4. 全量 ETF 日K线"""
    logger.info("===== [4/6] ETF 日K线 =====")
    from crawler.baostock_crawler import BaostockCrawler
    from app.db.connection import get_sync_db
    db = get_sync_db()
    c = BaostockCrawler()
    c.login()
    rs = bs.query_stock_basic()
    etf_list = []
    while rs.next():
        d = rs.get_row_data()
        if len(d) > 4 and d[4] == '5':  # 类型 '5' = ETF
            raw = d[0]
            for p in ('sh.', 'sz.', 'bj.'):
                if raw.startswith(p):
                    code = raw[len(p):]; ex = {'sh.':'SSE','sz.':'SZSE','bj.':'BSE'}[p]; break
            else: code, ex = raw, 'SSE'
            etf_list.append((raw, code, d[1], ex))

    logger.info(f"  ETF 列表: {len(etf_list)} 只")
    fields = 'date,open,high,low,close,volume,amount,turn'
    total = 0
    for bs_code, scode, sname, ex in etf_list:
        try:
            # 不复权 OHLCV
            rs2 = bs.query_history_k_data_plus(bs_code, fields, start_date='2021-01-01',
                end_date=date.today().isoformat(), frequency='d', adjustflag='3')
            raw_rows = []
            while rs2.next(): raw_rows.append(rs2.get_row_data())
            # 后复权 close
            rs_hfq = bs.query_history_k_data_plus(bs_code, 'date,close', start_date='2021-01-01',
                end_date=date.today().isoformat(), frequency='d', adjustflag='1')
            hfq_map = {}
            if rs_hfq.error_code == '0':
                while rs_hfq.next():
                    hd = rs_hfq.get_row_data()
                    hfq_map[hd[0]] = hd[1]
            for d in raw_rows:
                if not d[0]: continue
                close_hfq = float(hfq_map.get(d[0], d[4])) if d[4] else 0
                db.execute(text(
                    "INSERT INTO daily_quote "
                    "(trade_date,exchange,stock_code,stock_name,open,high,low,close,close_hfq,close_qfq,volume,amount,turnover) "
                    "VALUES (:td,:ex,:sc,:sn,:o,:h,:l,:c,:ch,:c,:v,:a,:t) "
                    "ON CONFLICT (stock_code, exchange, trade_date) DO UPDATE SET "
                    "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
                    "close=EXCLUDED.close, close_hfq=EXCLUDED.close_hfq, "
                    "volume=EXCLUDED.volume, amount=EXCLUDED.amount, turnover=EXCLUDED.turnover"
                ), {"td":d[0],"ex":ex,"sc":scode,"sn":sname,
                    "o":float(d[1]) if d[1] else 0,"h":float(d[2]) if d[2] else 0,
                    "l":float(d[3]) if d[3] else 0,"c":float(d[4]) if d[4] else 0,
                    "ch":close_hfq,
                    "v":int(float(d[5])) if d[5] else 0,"a":float(d[6]) if d[6] else 0,
                    "t":float(d[7]) if d[7] else None})
                total += 1
            time.sleep(0.1)
            db.commit()
        except Exception as e:
            logger.warning(f"  ETF {bs_code} 失败: {e}")
    db.close()
    logger.info(f"  ETF 完成: {total} 条")


def step_fundamentals():
    """5. 全量基本面数据"""
    logger.info("===== [5/6] 基本面数据 =====")
    from crawler.baostock_crawler import BaostockCrawler
    c = BaostockCrawler()
    count = c.download_fundamentals(skip_existing=False, skip_pe_pb=False)
    c.logout()
    logger.info(f"  基本面完成: {count} 只")


def step_market_cap():
    """6. 更新市值数据（从每日行情最新价计算）"""
    logger.info("===== [6/6] 市值更新 =====")
    db = get_sync_db()

    # 检查 stock_fundamentals 是否有 market_cap 字段
    try:
        db.execute(text("ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS market_cap BIGINT DEFAULT NULL"))
        db.commit()
    except Exception:
        db.rollback()

    # 从最新行情计算市值（close * total_shares）
    # total_shares 从 baostock 的 query_stock_basic 获取
    import baostock as bs
    lg = bs.login()
    if lg.error_code == '0':
        # 获取总股本
        rs = bs.query_stock_basic()
        shares = {}
        while rs.next():
            d = rs.get_row_data()
            for p in ('sh.', 'sz.', 'bj.'):
                if d[0].startswith(p):
                    code = d[0][len(p):]
                    break
            else:
                code = d[0]
            # baostock basic 没有总股本，用另一种方式
            pass
        bs.logout()

    # 简单方式：从 stock_master + daily_quote 最近价计算
    # 先检查是否有 total_shares 数据（旧导入可能有）
    try:
        db.execute(text("""
            UPDATE stock_fundamentals sf
            SET market_cap = sf.total_shares * (
                SELECT dq.close FROM daily_quote dq
                WHERE dq.stock_code = sf.stock_code
                ORDER BY dq.trade_date DESC LIMIT 1
            )
            WHERE sf.total_shares IS NOT NULL
        """))
        db.commit()
        updated = db.execute(text("SELECT COUNT(*) FROM stock_fundamentals WHERE market_cap IS NOT NULL")).scalar()
        logger.info(f"  市值已更新: {updated} 只")
    except Exception as e:
        logger.warning(f"  市值计算失败（可能无 total_shares 数据）: {e}")
    db.close()


if __name__ == '__main__':
    import baostock as bs

    start = time.time()
    logger.info("========== 全量数据刷新开始 ==========")

    step_calendar()
    step_kline()
    step_index()
    step_etf()
    step_fundamentals()
    step_market_cap()

    elapsed = time.time() - start
    logger.info(f"========== 全量刷新完成! 耗时 {elapsed/60:.0f} 分钟 ==========")
