#!/usr/bin/env python
"""利润表（income）历史回补——PEG 实际归母净利 CAGR 的数据底座。

按股票代码逐只拉全历史（tushare income 必须带 ts_code），一只 1 次调用，
约 5,400 只 × 1.3s ≈ 2 小时；配额 8,000/天足够一轮。断点续跑靠
`.backfill_income_progress.json`（任务结束可删）。

用法：
    venv/bin/python scripts/backfill_income.py
    venv/bin/python scripts/backfill_income.py --index 000300.SH
    venv/bin/python scripts/backfill_income.py --index 000001.SH,000300.SH,000905.SH
    venv/bin/python scripts/backfill_income.py --codes 600519,000001
    venv/bin/python scripts/backfill_income.py --limit 500
    venv/bin/python scripts/backfill_income.py --all --reserve 500

之后每日由 DAG「一致预期」流程的 income_daily 节点按披露计划增量更新。
"""
import argparse
import fcntl
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loguru import logger
from sqlalchemy import text

LOCK_FILE = '/tmp/stone_backfill_income.lock'
PROGRESS_FILE = ROOT / '.backfill_income_progress.json'


def _acquire_lock():
    fh = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.error(f'已有回补进程在跑（{LOCK_FILE}），退出')
        sys.exit(1)
    return fh


def _load_done() -> set:
    try:
        data = json.loads(PROGRESS_FILE.read_text())
        return set(data.get('done', []))
    except Exception:
        return set()


def _save_done(done: set):
    tmp = PROGRESS_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps({'done': sorted(done),
                               'updated_at': time.strftime('%Y-%m-%d %H:%M:%S')},
                              ensure_ascii=False))
    tmp.replace(PROGRESS_FILE)


def main():
    ap = argparse.ArgumentParser(description='利润表 income 历史回补（逐票）')
    ap.add_argument('--codes', default=None, help='指定代码，逗号分隔（默认全部上市股票）')
    ap.add_argument('--index', default=None,
                    help='只回补指数最新成分（逗号分隔，如 000300.SH 或 000300.SH,000905.SH）')
    ap.add_argument('--all', action='store_true', help='包含已退市股票')
    ap.add_argument('--limit', type=int, default=0, help='本次最多处理多少只（0=不限）')
    ap.add_argument('--reserve', type=int, default=500, help='配额保留余量（默认 500）')
    ap.add_argument('--force', action='store_true', help='忽略进度文件，全部重拉')
    args = ap.parse_args()

    _acquire_lock()

    from app.db.connection import get_sync_db
    from crawler.adapters import get_data_source_manager
    from crawler.adapters.tushare_quota import TushareQuota, QuotaExhausted
    from crawler.writers import batch_upsert_income

    db = get_sync_db()
    try:
        if args.codes:
            codes = [c.strip().zfill(6) for c in args.codes.split(',') if c.strip()]
        elif args.index:
            idxs = [x.strip() for x in args.index.split(',') if x.strip()]
            codes = []
            for idx in idxs:
                snap = db.execute(text(
                    "SELECT MAX(trade_date) FROM index_weight WHERE index_code=:i"),
                    {'i': idx}).scalar()
                if not snap:
                    logger.warning(f'指数 {idx} 无成分快照，跳过')
                    continue
                rows = db.execute(text(
                    "SELECT w.stock_code FROM index_weight w "
                    "JOIN stock_master m ON m.stock_code=w.stock_code AND m.stock_type='stock' "
                    "WHERE w.index_code=:i AND w.trade_date=:d "
                    "ORDER BY w.stock_code"), {'i': idx, 'd': snap}).fetchall()
                codes.extend(r[0] for r in rows)
            codes = sorted(set(codes))
            logger.info(f'指数成分 {args.index} 去重后 {len(codes)} 只')
        else:
            sql = ("SELECT stock_code FROM stock_master WHERE stock_type='stock'"
                   + ('' if args.all else " AND status='N'")
                   + " ORDER BY stock_code")
            codes = [r[0] for r in db.execute(text(sql)).fetchall()]
    finally:
        db.close()

    done = set() if args.force else _load_done()
    todo = [c for c in codes if c not in done]
    if args.limit:
        todo = todo[:args.limit]
    logger.info(f'income 回补：总 {len(codes)} 只，已完成 {len(done & set(codes))}，'
                f'本次 {len(todo)} 只（约 {len(todo) * 1.5 / 60:.0f} 分钟）')
    if not todo:
        return

    quota = TushareQuota.get()
    source = get_data_source_manager().get_source()
    db = get_sync_db()
    saved = ok = fail = 0
    t0 = time.time()
    try:
        for i, code in enumerate(todo):
            if quota.remaining() <= args.reserve:
                logger.warning(f'配额余 {quota.remaining()} ≤ 保留 {args.reserve}，收工；'
                               f'已完成 {ok} 只，下次续跑')
                break
            try:
                rows = source.fetch_income_by_code(code)
                saved += batch_upsert_income(db, rows)
                done.add(code)
                ok += 1
            except QuotaExhausted:
                logger.warning('配额耗尽，收工；下次续跑')
                break
            except Exception as e:
                fail += 1
                logger.warning(f'income {code}: {str(e)[:100]}')
            if (i + 1) % 20 == 0 or i == len(todo) - 1:
                elapsed = time.time() - t0
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                eta_min = (len(todo) - i - 1) / rate / 60 if rate > 0 else 0
                logger.info(f'进度 {i + 1}/{len(todo)}：{code}，累计 {saved:,} 行，'
                            f'失败 {fail}，ETA {eta_min:.0f}min')
            if (i + 1) % 100 == 0:
                _save_done(done)
                db.commit()
        db.commit()
    finally:
        db.close()
        _save_done(done)
    logger.info(f'完成：{ok} 只 / {saved:,} 行，失败 {fail}，进度已存 {PROGRESS_FILE}')


if __name__ == '__main__':
    main()
