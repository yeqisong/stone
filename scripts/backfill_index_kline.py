#!/usr/bin/env python
"""指数日K线历史回补（tushare index_daily 按 ts_code 区间直查 → index_daily_quote）。

DAG 节点 index 按交易日全市场拉取（1 调用/天），历史只有采集启动日（2021-06-16）
起。本脚本按 ts_code 区间直查，把 7 个主要指数的日K铺回指数创立日（约 2005），
让大盘详情页的 5 年/10 年/20 年区间档位有真数据。幂等 upsert，可重复执行续跑。

用法：
    venv/bin/python scripts/backfill_index_kline.py                 # 默认 2005-01-01 起
    venv/bin/python scripts/backfill_index_kline.py --start 2010-01-01
    venv/bin/python scripts/backfill_index_kline.py --codes 000300.SH,000001.SH

tushare index_daily 单次约 2000 行上限，按 8 年分块 7 指数约 21 次调用。
字段换算沿用 fetch_index_kline 口径：vol×100（手→股）、amount×1000（千元→元）。
配额经 TushareQuota 计数限流，余量 <20 自动收工，次日重跑自动续。
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

# 与 dag_task_macro_daily / backfill_index_valuation 一致的 7 个主要指数
INDEX_CODES = [
    '000300.SH', '000001.SH', '399001.SZ', '399006.SZ',
    '000905.SH', '000852.SH', '000016.SH',
]


def chunk_ranges(start_d: date, end_d: date, years: int):
    """把 [start, end] 切成升序的 ≤years 年窗口，返回 [('YYYYMMDD','YYYYMMDD')]。"""
    out = []
    cur = start_d
    while cur <= end_d:
        nxt = min(cur + timedelta(days=365 * years), end_d)
        out.append((cur.isoformat().replace('-', ''), nxt.isoformat().replace('-', '')))
        cur = nxt + timedelta(days=1)
    return out


def main():
    ap = argparse.ArgumentParser(description='指数日K线历史回补')
    ap.add_argument('--start', default='2005-01-01', help='回补起始日（默认 2005-01-01）')
    ap.add_argument('--end', default=None, help='回补结束日（默认今天）')
    ap.add_argument('--codes', default=','.join(INDEX_CODES), help='逗号分隔的指数 ts_code 列表')
    ap.add_argument('--chunk-years', type=int, default=8, help='单次调用的年份跨度（默认 8 年）')
    args = ap.parse_args()

    import pandas as pd
    import tushare as ts
    from sqlalchemy import text as _t
    from app.config import settings
    from app.db.connection import get_sync_db
    from crawler.adapters.base import IndexKlineRow
    from crawler.adapters.tushare_quota import QuotaExhausted, TushareQuota
    from crawler.writers import batch_upsert_index_kline

    codes = [c.strip() for c in args.codes.split(',') if c.strip()]
    start_d = date.fromisoformat(args.start)
    end_d = date.fromisoformat(args.end) if args.end else date.today()
    pro = ts.pro_api(settings.TUSHARE_TOKEN)
    quota = TushareQuota.get()
    db = get_sync_db()

    total_rows = 0
    total_calls = 0
    stop = ''
    try:
        for code in codes:
            if stop:
                break
            bare = code.split('.')[0].zfill(6)  # tushare 带后缀，库内 6 位裸码
            row = db.execute(_t(
                "SELECT min(trade_date) AS mn, max(trade_date) AS mx, count(*) AS n "
                "FROM index_daily_quote WHERE index_code=:c"), {'c': bare}).fetchone()
            need_end = (row.mn - timedelta(days=1)) if row.mn else end_d
            if row.mn and row.mn <= start_d:
                logger.info(f'{bare}: 已覆盖至 {row.mn}（早于目标起点 {start_d}），跳过')
                continue
            if need_end < start_d:
                logger.info(f'{bare}: 无缺口（已有 {row.n} 行，{row.mn} → {row.mx}）')
                continue
            logger.info(f'{bare}: 回补 {start_d} → {need_end}（已有 {row.n} 行，{row.mn} → {row.mx}）')
            for ws, we in chunk_ranges(start_d, need_end, args.chunk_years):
                if quota.remaining() < 20:
                    stop = '配额余量不足 20，收工；脚本幂等，明日续跑'
                    break
                try:
                    quota.consume()
                except QuotaExhausted as e:
                    stop = str(e)
                    break
                total_calls += 1
                try:
                    df = pro.index_daily(ts_code=code, start_date=ws, end_date=we)
                except Exception as e:
                    logger.warning(f'{code} {ws}~{we}: 调用失败 {str(e)[:80]}')
                    continue
                rows = [IndexKlineRow(
                    trade_date=str(r['trade_date']), index_code=bare, index_name='',
                    open=float(r['open']), high=float(r['high']),
                    low=float(r['low']), close=float(r['close']),
                    volume=int(r['vol']) * 100 if pd.notna(r.get('vol')) else 0,
                    amount=float(r['amount']) * 1000 if pd.notna(r.get('amount')) else 0,
                ) for _, r in (df.iterrows() if df is not None and len(df) else [])]
                n = batch_upsert_index_kline(db, rows)  # 内部批量提交 + 非正收盘守卫
                total_rows += n
                logger.info(f'  {ws}~{we}: {n} 行（累计 {total_rows}，配额 {total_calls} 次）')
    finally:
        try:
            quota.persist(db)
        except Exception as e:
            logger.warning(f'配额持久化失败（不影响数据）: {e}')
        db.close()

    if stop:
        logger.warning(stop)
    logger.info(f'回补完成：{total_rows} 行 / {total_calls} 次 API 调用')
    return 1 if stop else 0


if __name__ == '__main__':
    sys.exit(main())
