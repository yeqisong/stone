#!/usr/bin/env python
"""指数估值历史回补（tushare index_dailybasic → index_valuation）。

macro_daily DAG 节点每日只回补近 40 自然日窗口，表内历史有限（首次上线
2026-09-24）。本脚本一次性把主要指数的估值（PE/PB/换手/市值）回铺到指数
创立日，让大盘详情页的 PE/PB 历史分位有意义。幂等 upsert，可重复执行续跑。

用法：
    venv/bin/python scripts/backfill_index_valuation.py                  # 默认 2005-01-01 起
    venv/bin/python scripts/backfill_index_valuation.py --start 2010-01-01
    venv/bin/python scripts/backfill_index_valuation.py --codes 000300.SH,000001.SH

tushare index_dailybasic 2000 积分实测可用（单次上限约 3000 行，按 8 年分块
7 指数约 21 次调用）。配额经 TushareQuota 计数限流（1.3s 间隔），余量 <20
自动收工，脚本幂等，次日重跑自动续。
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

# 与 dag_task_macro_daily 保持一致的 7 个主要指数
INDEX_CODES = [
    '000300.SH', '000001.SH', '399001.SZ', '399006.SZ',
    '000905.SH', '000852.SH', '000016.SH',
]

UPSERT = """
INSERT INTO index_valuation (ts_code, trade_date, pe, pe_ttm, pb,
    total_mv, float_mv, total_share, float_share,
    turnover_rate, turnover_rate_f, volume, amount)
VALUES (:c, :d, :pe, :pet, :pb, :tmv, :fmv, :ts, :fs, :tr, :trf, :vol, :amt)
ON CONFLICT (ts_code, trade_date) DO UPDATE SET
    pe=EXCLUDED.pe, pe_ttm=EXCLUDED.pe_ttm, pb=EXCLUDED.pb,
    total_mv=EXCLUDED.total_mv, float_mv=EXCLUDED.float_mv,
    total_share=EXCLUDED.total_share, float_share=EXCLUDED.float_share,
    turnover_rate=EXCLUDED.turnover_rate, turnover_rate_f=EXCLUDED.turnover_rate_f,
    volume=EXCLUDED.volume, amount=EXCLUDED.amount
"""


def fv(v):
    """NaN / 缺失 → None（库列不允许 NaN，对齐 pipeline._fv 口径）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


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
    ap = argparse.ArgumentParser(description='指数估值历史回补')
    ap.add_argument('--start', default='2005-01-01', help='回补起始日（默认 2005-01-01，各指数更晚创立则自然为空）')
    ap.add_argument('--end', default=None, help='回补结束日（默认今天）')
    ap.add_argument('--codes', default=','.join(INDEX_CODES), help='逗号分隔的指数 ts_code 列表')
    ap.add_argument('--chunk-years', type=int, default=8, help='单次调用的年份跨度（默认 8 年 ≈ 2000 行）')
    args = ap.parse_args()

    import tushare as ts
    from sqlalchemy import text as _t
    from app.config import settings
    from app.db.connection import get_sync_db
    from crawler.adapters.tushare_quota import QuotaExhausted, TushareQuota

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
            row = db.execute(_t(
                "SELECT min(trade_date) AS mn, max(trade_date) AS mx, count(*) AS n "
                "FROM index_valuation WHERE ts_code=:c"), {'c': code}).fetchone()
            # 目标区间：start → （已有最早日期 - 1 天）；无任何行则铺满到今天
            need_end = (row.mn - timedelta(days=1)) if row.mn else end_d
            if row.mn and row.mn <= start_d:
                logger.info(f'{code}: 已覆盖至 {row.mn}（早于目标起点 {start_d}），跳过')
                continue
            if need_end < start_d:
                logger.info(f'{code}: 无缺口（已有 {row.n} 行，{row.mn} → {row.mx}）')
                continue
            logger.info(f'{code}: 回补 {start_d} → {need_end}（已有 {row.n} 行，{row.mn} → {row.mx}）')
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
                    df = pro.index_dailybasic(ts_code=code, start_date=ws, end_date=we)
                except Exception as e:
                    logger.warning(f'{code} {ws}~{we}: 调用失败 {str(e)[:80]}')
                    continue
                params = [{
                    'c': code, 'd': str(r['trade_date'])[:10],
                    'pe': fv(r.get('pe')), 'pet': fv(r.get('pe_ttm')), 'pb': fv(r.get('pb')),
                    'tmv': fv(r.get('total_mv')), 'fmv': fv(r.get('float_mv')),
                    'ts': fv(r.get('total_share')), 'fs': fv(r.get('float_share')),
                    'tr': fv(r.get('turnover_rate')), 'trf': fv(r.get('turnover_rate_f')),
                    'vol': fv(r.get('volume')), 'amt': fv(r.get('amount')),
                } for _, r in (df.iterrows() if df is not None and len(df) else [])]
                if params:
                    db.execute(_t(UPSERT), params)  # executemany 批量
                    db.commit()
                total_rows += len(params)
                logger.info(f'  {ws}~{we}: {len(params)} 行（累计 {total_rows}，配额 {total_calls} 次）')
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
