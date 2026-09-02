"""Alpha158 全量计算（2000-01-01 → 今）：按年分段 + 断点续跑 + 共享宽表 + 多进程并发。

- 每年一段：全年 OHLCV 宽表只拉一次（最大 lookback ×2 自然日扩展），该年全部因子共享，
  消除旧版逐因子重复拉表（114 次/年 → 1 次/年）
- 多进程：因子计算与写库在 worker 进程执行（各自独立 DB engine，fork 后禁用父进程连接）；
  PK 含 feature_name，多 worker 并发 DELETE+COPY 零行冲突
- 断点：progress 文件记录已完成 (year, feature)；重跑跳过；仅成功项记断点，失败项重跑重试
- 单 worker 崩溃（OOM 等）会使 Pool 失效 → 脚本退出，@reboot/手动重跑自动续传

用法：venv/bin/python scripts/alpha158_full.py [--start-year 2000] [--end-year 2026] [--workers 6]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROGRESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.a158_full_progress.json')


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE) as f:
                return set(tuple(x) for x in json.load(f))
        except (json.JSONDecodeError, ValueError):
            # 进度文件写坏（进程中途被杀）时重建而非崩溃：已入库的数据有
            # UPSERT 幂等性，重算只是耗时，不会错
            print('⚠ 进度文件损坏，从头开始（已完成项幂等重算）', flush=True)
            return set()
    return set()


def save_progress(done):
    # 原子写：先写临时文件再 rename，进程在 json.dump 中途被杀不会留下截断 JSON
    tmp = PROGRESS_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump([list(x) for x in sorted(done)], f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, PROGRESS_FILE)


# ── worker 进程内全局状态（fork 继承，各进程独立） ──
_W = {}


def _worker_init(df_year):
    """Pool initializer：冻结当年共享宽表 + 自建 DB engine。

    父进程的 SQLAlchemy engine 禁止跨 fork 使用（socket 共享会串数据），
    worker 必须在 fork 后自建连接。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    url = os.environ.get('DATABASE_URL_SYNC')
    assert url, 'DATABASE_URL_SYNC 未加载（.env）'
    eng = create_engine(url, pool_pre_ping=True, pool_size=2)
    _W['engine'] = eng
    _W['db'] = sessionmaker(bind=eng)()
    # drop('_value') 防御：compute_feature 会向传入 df 写 _value 列，
    # 每个 task 显式拿副本写，避免污染跨 task 共享的宽表
    _W['df'] = df_year.drop(columns=['_value'], errors='ignore')


def _compute_one(task):
    """在 worker 进程内计算单个因子并写库（独立事务，与父进程零共享）。"""
    from scripts.feature_compute import compute_feature
    fn, formula, start_date, end_date = task
    t0 = time.time()
    try:
        r = compute_feature(_W['db'], fn, formula, target_entity='stock',
                            start_date=start_date, end_date=end_date,
                            df=_W['df'], chunk_days=0)
        rows = r.get('rows', 0)
        errs = r.get('errors') or ([{'error': r['error']}] if r.get('error') else [])
        status = 'OK' if not errs else f'ERR {errs[0].get("error", "")[:80]}'
        return (fn, rows, time.time() - t0, status, not errs)
    except Exception as ex:
        return (fn, 0, time.time() - t0, f'EXC {str(ex)[:80]}', False)


def main():
    import argparse
    from datetime import datetime as _dtp, timedelta as _tdp
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

    p = argparse.ArgumentParser()
    p.add_argument('--start-year', type=int, default=2000)
    p.add_argument('--end-year', type=int, default=2026)
    p.add_argument('--workers', type=int, default=6, help='并发 worker 数（1 = 单进程串行）')
    args = p.parse_args()

    from multiprocessing import get_context
    from app.db.connection import SyncSessionLocal
    from scripts.alpha158 import _gen_alpha158_formulas
    from scripts.feature_compute import _extract_lookback, _fetch_ohlcv

    formulas = {n: f for n, f, *_ in _gen_alpha158_formulas()}
    names = list(formulas)
    done = load_progress()
    db = SyncSessionLocal()   # 父进程仅用于拉宽表，写入全部在 worker
    t_all = time.time()

    total = len(names) * (args.end_year - args.start_year + 1)
    n_done = sum(1 for y, f in done if args.start_year <= int(y) <= args.end_year)
    max_lb = max(_extract_lookback(f) for f in formulas.values())
    margin_days = max(int(max_lb * 2.0), 10)

    for year in range(args.start_year, args.end_year + 1):
        s, e = f'{year}-01-01', f'{year}-12-31'
        todo = [(fn, formulas[fn]) for fn in names if (str(year), fn) not in done]
        t_year = time.time()
        year_rows = 0
        if not todo:
            print(f'[{year}] 全部已完成，跳过', flush=True)
            continue

        # 共享宽表：全年一次拉取，lookback 扩展取全因子最大值
        fetch_start = max((_dtp.strptime(s, '%Y-%m-%d') - _tdp(days=margin_days)).strftime('%Y-%m-%d'),
                          '2000-01-01')
        t_fetch = time.time()
        df_year = _fetch_ohlcv(db, 'stock', fetch_start, e)
        print(f'[{year}] 宽表 {len(df_year):,} 行（{fetch_start} 起，含 lookback）'
              f' {time.time()-t_fetch:.0f}s | 待算 {len(todo)}/{len(names)} 因子', flush=True)

        # 每年新建 Pool：initializer 把当年宽表注入 worker（fork COW，读多写少）
        ctx = get_context('fork')
        with ctx.Pool(processes=args.workers, initializer=_worker_init,
                      initargs=(df_year,)) as pool:
            tasks = [(fn, f, s, e) for fn, f in todo]
            for fn, rows, dt, status, ok in pool.imap_unordered(_compute_one, tasks, chunksize=1):
                # 只有成功才记断点：失败项重跑时必须重试，避免静默缺口
                if ok:
                    done.add((str(year), fn))
                    save_progress(done)
                year_rows += rows
                n_done += 1
                print(f'[{year}] {fn}: {rows:,} 行 {dt:.0f}s {status}'
                      + ('' if ok else ' ⚠未记断点，重跑将重试'), flush=True)

        print(f'═══ {year} 完成: {year_rows:,} 行, 耗时 {(time.time()-t_year)/60:.1f} 分钟, '
              f'总进度 {n_done}/{total} ({(time.time()-t_all)/3600:.1f}h) ═══', flush=True)
    db.close()
    print(f'✅ 全量完成: {n_done}/{total}, 总耗时 {(time.time()-t_all)/3600:.1f} 小时', flush=True)


if __name__ == '__main__':
    main()
