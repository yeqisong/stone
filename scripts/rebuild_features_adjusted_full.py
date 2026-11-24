#!/usr/bin/env python
"""全量复权特征重算（按年分段 + 多进程 + 断点续跑）。

把所有启用且公式引用 OHLCV 的 stock 特征，从 raw 价口径重算为后复权口径。
按年拉一次共享宽表（含最大 lookback 预热），fork worker 计算并在各自连接写库；
进度文件 `.rebuild_adjusted_progress.json` 记录已完成 (year, feature)，中断可续跑。

用法：
    venv/bin/python scripts/rebuild_features_adjusted_full.py --start-year 2017
    venv/bin/python scripts/rebuild_features_adjusted_full.py --start-year 2017 --workers 6 --apply
    venv/bin/python scripts/rebuild_features_adjusted_full.py --dry-run
"""
import argparse
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PROGRESS_FILE = os.path.join(ROOT, '.rebuild_adjusted_progress.json')
_PRICE_RE = re.compile(r"\b(open|high|low|close|volume)\b", re.I)
_W = {}


def load_progress() -> set:
    if not os.path.exists(PROGRESS_FILE):
        return set()
    try:
        with open(PROGRESS_FILE) as f:
            return set(tuple(x) for x in json.load(f))
    except Exception:
        print('⚠ 进度文件损坏，从头开始（已完成项幂等重算）', flush=True)
        return set()


def save_progress(done: set) -> None:
    tmp = PROGRESS_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump([list(x) for x in sorted(done)], f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, PROGRESS_FILE)


def _worker_init(df_year):
    """worker 内自建 DB 连接（禁止复用父进程连接），共享 fork 只读宽表。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    url = os.environ.get('DATABASE_URL_SYNC')
    assert url, 'DATABASE_URL_SYNC 未加载（.env）'
    eng = create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=0)
    _W['engine'] = eng
    _W['db'] = sessionmaker(bind=eng)()
    _W['df'] = df_year.drop(columns=['_value'], errors='ignore')


def _compute_one(task):
    from scripts.feature_compute import compute_feature
    fn, formula, start_date, end_date = task
    t0 = time.time()
    try:
        r = compute_feature(_W['db'], fn, formula, target_entity='stock',
                            start_date=start_date, end_date=end_date,
                            df=_W['df'], chunk_days=0, adjust_prices=True)
        rows = r.get('rows', 0)
        err = r.get('error') or (r.get('errors') or [{}])[0].get('error')
        return fn, rows, time.time() - t0, err, bool(r.get('ok'))
    except Exception as e:
        return fn, 0, time.time() - t0, f'{type(e).__name__}: {str(e)[:120]}', False


def _union_columns(rows):
    """特征公式引用字段并集（OHLCV + 基本面/资金流/市值等派生输入）。"""
    from scripts.feature_compute import (EXTRA_FIELD_SOURCES, _ALL_EXTRA_TABLE_COLS,
                                         _extract_lookback)
    base = {"close", "open", "high", "low", "volume", "amount"}
    all_fields = sorted(base | set(EXTRA_FIELD_SOURCES) | set(_ALL_EXTRA_TABLE_COLS))
    union = set(base)
    max_lb = 0
    for _, formula in rows:
        union |= set(re.findall(r'\b(' + '|'.join(all_fields) + r')\b', formula or ''))
        if re.search(r'\batr\b', formula or ''):
            union |= {"high", "low"}
        if re.search(r'\bneut\b', formula or ''):
            union |= {"circ_mv"}
        max_lb = max(max_lb, _extract_lookback(formula or ''))
    return sorted(union), max_lb


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, '.env'))

    ap = argparse.ArgumentParser()
    ap.add_argument('--start-year', type=int, default=2017)
    ap.add_argument('--end-year', type=int, default=0)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--features', default='', help='逗号分隔；空=全部价格/量相关特征')
    ap.add_argument('--apply', action='store_true', help='真正写库')
    ap.add_argument('--dry-run', action='store_true', help='只打印计划')
    args = ap.parse_args()

    from datetime import date, datetime as _dtp, timedelta as _tdp
    from multiprocessing import get_context
    from sqlalchemy import text
    from app.db.connection import SyncSessionLocal
    from scripts.feature_compute import _fetch_ohlcv

    end_year = args.end_year or date.today().year
    db = SyncSessionLocal()
    try:
        rows = db.execute(text("""
            SELECT feature_name, formula FROM features
            WHERE target_entity='stock' AND status='enabled' ORDER BY feature_name
        """)).fetchall()
    finally:
        db.close()
    if args.features:
        wanted = {x.strip() for x in args.features.split(',') if x.strip()}
        rows = [r for r in rows if r[0] in wanted]
    else:
        rows = [r for r in rows if _PRICE_RE.search(r[1] or '')]
    names = [r[0] for r in rows]
    union, max_lb = _union_columns(rows)
    margin = max(int(max_lb * 2.0), 10)
    print(f'years={args.start_year}..{end_year} features={len(names)} workers={args.workers} '
          f'apply={args.apply} lookback={max_lb} columns={len(union)}', flush=True)
    if args.dry_run or not args.apply:
        for n in names:
            print('  -', n)
        print('\n预览模式：未写库。确认后加 --apply。', flush=True)
        return

    done = load_progress()
    t_all = time.time()
    total_rows = 0
    all_errors = []
    for year in range(args.start_year, end_year + 1):
        s, e = f'{year}-01-01', f'{year}-12-31'
        todo = [(fn, formula) for fn, formula in rows if (str(year), fn) not in done]
        if not todo:
            print(f'[{year}] 全部已完成，跳过', flush=True)
            continue
        fetch_start = max((_dtp.strptime(s, '%Y-%m-%d') - _tdp(days=margin)).strftime('%Y-%m-%d'),
                          '2000-01-01')
        t_fetch = time.time()
        pdb = SyncSessionLocal()
        try:
            df_year = _fetch_ohlcv(pdb, 'stock', fetch_start, e,
                                   columns=union, adjust_prices=True)
        finally:
            pdb.close()
        print(f'[{year}] 宽表 {len(df_year):,} 行（{fetch_start} 起）'
              f' {time.time()-t_fetch:.0f}s | 待算 {len(todo)} 因子', flush=True)
        if df_year.empty:
            print(f'[{year}] 无行情，跳过', flush=True)
            continue

        ctx = get_context('fork')
        t_year = time.time()
        with ctx.Pool(processes=max(1, args.workers), initializer=_worker_init,
                      initargs=(df_year,)) as pool:
            tasks = [(fn, formula, s, e) for fn, formula in todo]
            for k, (fn, nrows, sec, err, ok) in enumerate(
                    pool.imap_unordered(_compute_one, tasks), 1):
                total_rows += nrows
                if ok:
                    done.add((str(year), fn))
                else:
                    all_errors.append({'year': year, 'feature': fn, 'error': err})
                    print(f'  ✗ {year}/{fn}: {err}', flush=True)
                if k % 10 == 0 or k == len(tasks):
                    print(f'[{year}] {k}/{len(tasks)} 完成，累计 {total_rows:,} 行，'
                          f'已用 {time.time()-t_year:.0f}s', flush=True)
                    save_progress(done)
        save_progress(done)
        print(f'[{year}] DONE {time.time()-t_year:.0f}s，累计 {total_rows:,} 行', flush=True)

    print(f'ALL DONE rows={total_rows:,} errors={len(all_errors)} '
          f'elapsed={time.time()-t_all:.0f}s', flush=True)
    if all_errors:
        print(json.dumps(all_errors[:30], ensure_ascii=False, indent=2), flush=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
