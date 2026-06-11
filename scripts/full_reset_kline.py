#!/usr/bin/env python3
"""全量重刷 A 股/指数/ETF 日K线数据。先清空再下载。"""
import sys, time, os
sys.path.insert(0, '/app')
from datetime import date
from loguru import logger
from app.db.connection import get_sync_db
from sqlalchemy import text
import baostock as bs

START = "2021-01-01"
END = date.today().isoformat()


def step_1_truncate():
    logger.info("清空 daily_quote...")
    db = get_sync_db()
    db.execute(text("TRUNCATE TABLE daily_quote"))
    db.execute(text("TRUNCATE TABLE index_daily_quote"))
    db.commit()
    db.close()
    logger.info("清空完成")


def step_2_reindex_master():
    """确保 stock_master 准确（含 ETF/指数代码）。"""
    logger.info("更新 stock_master（含指数/ETF）...")
    db = get_sync_db()
    lg = bs.login()
    rs = bs.query_stock_basic()
    type_map = {'1':'stock','2':'index','5':'etf'}
    count = 0
    while rs.next():
        d = rs.get_row_data()
        raw_code, name = d[0], d[1]
        stype = type_map.get(d[4], 'other')
        if stype not in ('stock','index','etf'): continue
        for prefix, ex in [('sh.','SSE'),('sz.','SZSE'),('bj.','BSE')]:
            if raw_code.startswith(prefix):
                code = raw_code[len(prefix):]; exchange = ex; break
        else:
            code = raw_code; exchange = 'SSE'
        db.execute(text(
            "INSERT INTO stock_master (stock_code,stock_name,exchange,status,stock_type) "
            "VALUES (:c,:n,:e,'N',:t) ON CONFLICT (stock_code) DO UPDATE SET "
            "stock_name=EXCLUDED.stock_name, stock_type=EXCLUDED.stock_type"
        ), {"c":code,"n":name,"e":exchange,"t":stype})
        count += 1
    db.commit()
    bs.logout()
    db.close()
    logger.info(f"stock_master 更新: {count} 条")


def step_3_stock_kline():
    """A股日K线全量重刷。"""
    logger.info(f"===== A股日K线 ({START} ~ {END}) =====")
    from crawler.baostock_crawler import BaostockCrawler
    c = BaostockCrawler()
    result = c.download_all_stocks(START, END, None, False)
    logger.info(f"A股完成: {result.get('total_rows',0)} 行, {result.get('errors',0)} 错误")
    c.logout()


def step_4_index_kline():
    """指数日K线全量重刷。"""
    logger.info(f"===== 指数日K线 ({START} ~ {END}) =====")
    db = get_sync_db()
    lg = bs.login()
    rs = bs.query_stock_basic()
    index_codes = []
    while rs.next():
        d = rs.get_row_data()
        if d[4] == '2':
            raw = d[0]
            for p in ('sh.','sz.','bj.'):
                if raw.startswith(p): code=raw[len(p):]; break
            else: code=raw
            index_codes.append((raw, code, d[1]))
    total = 0
    for bs_code, idx_code, name in index_codes:
        try:
            rs2 = bs.query_history_k_data_plus(bs_code, "date,code,open,high,low,close,volume,amount",
                START, END, 'd', '3')
            while rs2.next():
                d2 = rs2.get_row_data()
                if not d2[0]: continue
                db.execute(text(
                    "INSERT INTO index_daily_quote (trade_date,index_code,index_name,"
                    "open,high,low,close,volume,amount) VALUES (:d,:c,:n,:o,:h,:l,:cl,:v,:a) "
                    "ON CONFLICT (trade_date,index_code) DO UPDATE SET "
                    "open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,"
                    "close=EXCLUDED.close,volume=EXCLUDED.volume,amount=EXCLUDED.amount"
                ), {"d":d2[0],"c":idx_code,"n":name,
                    "o":float(d2[2]) if d2[2] else 0,
                    "h":float(d2[3]) if d2[3] else 0,
                    "l":float(d2[4]) if d2[4] else 0,
                    "cl":float(d2[5]) if d2[5] else 0,
                    "v":int(float(d2[6])) if d2[6] else 0,
                    "a":float(d2[7]) if d2[7] else 0})
                total += 1
            time.sleep(0.05)
        except Exception as e:
            pass
    db.commit()
    bs.logout()
    db.close()
    logger.info(f"指数完成: {total} 行")


def step_5_etf_kline():
    """ETF日K线全量重刷。"""
    logger.info(f"===== ETF日K线 ({START} ~ {END}) =====")
    from crawler.baostock_crawler import BaostockCrawler
    c = BaostockCrawler()
    c.login()
    db = get_sync_db()
    rs = bs.query_stock_basic()
    total = 0
    while rs.next():
        d = rs.get_row_data()
        if d[4] == '5':
            raw = d[0]; sn = d[1]
            for p,ex in [('sh.','SSE'),('sz.','SZSE'),('bj.','BSE')]:
                if raw.startswith(p): code=raw[len(p):]; exchange=ex; break
            else: code=raw; exchange='SSE'
            try:
                rs2 = bs.query_history_k_data_plus(raw, "date,open,high,low,close,volume,amount,turn",
                    START, END, 'd', '3')
                while rs2.next():
                    d2 = rs2.get_row_data()
                    if not d2[0]: continue
                    db.execute(text(
                        "INSERT INTO daily_quote (trade_date,exchange,stock_code,stock_name,"
                        "open,high,low,close,close_hfq,close_qfq,volume,amount,turnover) "
                        "VALUES (:d,:e,:c,:n,:o,:h,:l,:cl,:cl,:cl,:v,:a,:t) "
                        "ON CONFLICT (stock_code,exchange,trade_date) DO UPDATE SET "
                        "open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,"
                        "close=EXCLUDED.close,volume=EXCLUDED.volume,amount=EXCLUDED.amount,turnover=EXCLUDED.turnover"
                    ), {"d":d2[0],"e":exchange,"c":code,"n":sn,
                        "o":float(d2[1]) if d2[1] else 0,
                        "h":float(d2[2]) if d2[2] else 0,
                        "l":float(d2[3]) if d2[3] else 0,
                        "cl":float(d2[4]) if d2[4] else 0,
                        "v":int(float(d2[5])) if d2[5] else 0,
                        "a":float(d2[6]) if d2[6] else 0,
                        "t":float(d2[7]) if d2[7] else None})
                    total += 1
                time.sleep(0.03)
            except Exception as e:
                pass
    db.commit()
    bs.logout()
    db.close()
    logger.info(f"ETF完成: {total} 行")


if __name__ == '__main__':
    t0 = time.time()
    logger.info("====== 全量数据重刷开始 ======")
    step_1_truncate()
    step_2_reindex_master()
    step_3_stock_kline()
    step_4_index_kline()
    step_5_etf_kline()
    elapsed = time.time() - t0
    logger.info(f"====== 完成! 耗时 {elapsed/60:.1f} 分钟 ======")
