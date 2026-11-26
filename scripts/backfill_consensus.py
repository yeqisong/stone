#!/usr/bin/env python
"""每日一致预期快照回补（stock_report_rc → stock_consensus_daily）。

纯库内聚合，不消耗 tushare 配额。按交易日逐日调用
crawler.consensus.compute_consensus_daily，与每日 DAG 节点完全同一实现，
保证历史与增量 PIT 口径一致。5 年约 1,250 天，视机器约 20-60 分钟。

用法：
    venv/bin/python scripts/backfill_consensus.py                  # 2021-01-01 起
    venv/bin/python scripts/backfill_consensus.py --start 2025-01-01
    venv/bin/python scripts/backfill_consensus.py --force          # 重算已有快照

前置：先跑 backfill_report_rc.py / backfill_income.py（或至少覆盖目标区间），
否则快照会缺预测明细。
"""
import argparse
import fcntl
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger
from sqlalchemy import text

LOCK_FILE = '/tmp/stone_backfill_consensus.lock'


def _acquire_lock():
    fh = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.error(f'已有回补进程在跑（{LOCK_FILE}），退出')
        sys.exit(1)
    return fh


def main():
    ap = argparse.ArgumentParser(description='每日一致预期快照回补')
    ap.add_argument('--start', default=None,
                    help='起始交易日（默认取 stock_report_rc 最早报告日）')
    ap.add_argument('--end', default=None,
                    help='结束日（默认取 stock_report_rc 最晚报告日）')
    ap.add_argument('--lookback', type=int, default=180, help='研报回看天数（默认 180）')
    ap.add_argument('--calc-ver', default='c1', help='口径版本标记（默认 c1）')
    ap.add_argument('--force', action='store_true', help='重算已有快照日期')
    args = ap.parse_args()

    _acquire_lock()

    from app.db.connection import get_sync_db
    from crawler.consensus import compute_consensus_daily

    start = date.fromisoformat(args.start) if args.start else None
    end = date.fromisoformat(args.end) if args.end else None
    db = get_sync_db()
    if start is None or end is None:
        row = db.execute(text(
            "SELECT MIN(report_date), MAX(report_date) FROM stock_report_rc")).fetchone()
        if not row or not row[0]:
            db.close()
            logger.warning('stock_report_rc 为空，先跑 scripts/backfill_report_rc.py')
            return
        start = start or row[0]
        end = end or row[1]
    dates = [r[0] for r in db.execute(text("""
        SELECT cal_date FROM trade_calendar
        WHERE cal_date BETWEEN :s AND :e AND is_trade_day = true
        ORDER BY cal_date
    """), {'s': start, 'e': end}).fetchall()]
    logger.info(f'一致预期快照：{len(dates)} 个交易日（{start} → {end}）')

    total = done = skipped = 0
    t0 = time.time()
    try:
        for i, d in enumerate(dates):
            ds = d.isoformat()
            if not args.force and db.execute(text(
                    "SELECT 1 FROM stock_consensus_daily WHERE asof_date=:d LIMIT 1"),
                    {'d': ds}).fetchone():
                skipped += 1
                continue
            n = compute_consensus_daily(db, d, calc_ver=args.calc_ver,
                                        lookback_days=args.lookback)
            db.commit()
            total += n
            done += 1
            if done % 20 == 0 or i == len(dates) - 1:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta_min = (len(dates) - i - 1) / rate / 60 if rate > 0 else 0
                logger.info(f'进度 {i + 1}/{len(dates)}：{ds} {n} 只，'
                            f'累计 {total:,}，ETA {eta_min:.0f}min')
    finally:
        db.close()
    logger.info(f'完成：{done} 天 / {total:,} 条快照，跳过 {skipped} 天')


if __name__ == '__main__':
    main()
