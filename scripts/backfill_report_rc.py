#!/usr/bin/env python
"""券商盈利预测（report_rc）历史回补。

实测限制（2026-11，本账号）：report_rc 不是“1 次/分钟”，而是 **10 次/天**
（错误原文：频率超限(10次/天)）。因此逐日回补 5 年需要 125 天，不可行；
默认改为按 --step 抽样（20 个交易日 ≈ 月度），5 年约 63 次调用、7 天配额完成。
抽样日的 report_rc 仍返回当日全市场研报，180 天回看窗口足以形成有代表性的
一致预期快照；PEG 历史带在回补完成前用 G_hist 占位（见 app/peg.py）。

用法：
    venv/bin/python scripts/backfill_report_rc.py                  # 2021 起，每 20 交易日
    venv/bin/python scripts/backfill_report_rc.py --step 1 --max-days 7 --start 2026-11-17
    venv/bin/python scripts/backfill_report_rc.py --start 2023-01-01 --step 10

说明：
- 已入库日期跳过；过去日 0 行登记 backfill_ext_checked，避免反复重拉空日；
- 命中 10 次/天限制会抛 QuotaExhausted 并收工，次日续跑；
- 单进程 flock 防重复启动。
之后用 scripts/backfill_consensus.py 生成每日一致预期快照。
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

LOCK_FILE = '/tmp/stone_backfill_report_rc.lock'


def _acquire_lock():
    fh = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.error(f'已有回补进程在跑（{LOCK_FILE}），退出')
        sys.exit(1)
    return fh


def main():
    ap = argparse.ArgumentParser(description='券商盈利预测 report_rc 历史回补（10 次/天）')
    ap.add_argument('--start', default='2021-01-01', help='起始交易日（默认 2021-01-01）')
    ap.add_argument('--end', default=None, help='结束日（默认今天）')
    ap.add_argument('--step', type=int, default=20,
                    help='每 N 个交易日抽样一次（默认 20≈月度；1=逐日但受 10 次/天限制）')
    ap.add_argument('--reserve', type=int, default=500, help='配额保留余量（默认 500）')
    ap.add_argument('--max-days', type=int, default=10, help='本次最多调用多少次（默认 10=单日上限）')
    ap.add_argument('--reverse', action='store_true', help='从结束日向前回补（优先最近数据）')
    ap.add_argument('--force', action='store_true', help='忽略已有数据/已核空标记，全部重拉')
    args = ap.parse_args()

    _acquire_lock()

    from app.db.connection import get_sync_db
    from crawler.adapters import get_data_source_manager
    from crawler.adapters.tushare_quota import TushareQuota, QuotaExhausted
    from scripts.pipeline import _ext_report_rc

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today()
    db = get_sync_db()
    quota = TushareQuota.get()
    source = get_data_source_manager().get_source()

    dates = [r[0] for r in db.execute(text("""
        SELECT cal_date FROM trade_calendar
        WHERE cal_date BETWEEN :s AND :e AND is_trade_day = true
        ORDER BY cal_date
    """), {'s': start, 'e': end}).fetchall()]
    dates = dates[::max(1, args.step)]
    if args.reverse:
        dates = list(reversed(dates))
    logger.info(f'report_rc 回补：{len(dates)} 个抽样日（step={args.step}，{start} → {end}），'
                f'受 10 次/天限制，本次最多 {args.max_days} 次')

    total = saved_days = skipped = 0
    try:
        for i, d in enumerate(dates):
            ds = d.isoformat()
            if not args.force:
                if db.execute(text(
                        "SELECT 1 FROM stock_report_rc WHERE report_date=:d LIMIT 1"),
                        {'d': ds}).fetchone():
                    skipped += 1
                    continue
                if db.execute(text(
                        "SELECT 1 FROM backfill_ext_checked "
                        "WHERE table_name='report_rc' AND checked_date=:d"),
                        {'d': ds}).fetchone():
                    skipped += 1
                    continue
            if quota.remaining() <= args.reserve:
                logger.warning(f'配额余 {quota.remaining()} ≤ 保留 {args.reserve}，收工；'
                               f'已补 {saved_days} 天，下次续跑')
                break
            if args.max_days and saved_days >= args.max_days:
                logger.info(f'达单次上限 {args.max_days} 次，收工')
                break
            try:
                n = _ext_report_rc(source, db, ds)
            except QuotaExhausted as e:
                logger.warning(f'{str(e)[:160]}；收工，次日续跑')
                break
            total += n
            saved_days += 1
            db.execute(text(
                "INSERT INTO backfill_ext_checked (table_name, checked_date, rows_found) "
                "VALUES ('report_rc', :d, :n) ON CONFLICT (table_name, checked_date) "
                "DO UPDATE SET rows_found=EXCLUDED.rows_found, checked_at=CURRENT_TIMESTAMP"),
                {'d': ds, 'n': n})
            db.commit()
            if saved_days % 5 == 0 or i == len(dates) - 1:
                logger.info(f'进度 {i + 1}/{len(dates)}：{ds} +{n} 行，'
                            f'累计 {total:,} 行，调用 {saved_days} 次')
    finally:
        db.close()
    logger.info(f'完成：实际补 {saved_days} 个抽样日 / {total:,} 行，跳过 {skipped} 天')



if __name__ == '__main__':
    main()
