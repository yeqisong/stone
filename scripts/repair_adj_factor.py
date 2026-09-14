#!/usr/bin/env python
"""修复复权因子"半修正快照"污染：按代码全量重写受影响股票的 close_hfq。

根因（2026-09-12 定位）：tushare 会**回溯性重定基** adj_factor 序列（整段历史换基准），
重定基是分批落地的——旧日期先保持旧基准、新日期先用新基准。我们的采集恰好在
半修正窗口拉数时，边界前的行存旧基准、边界后的行存新基准，跨界处产生
×1.2~×3 的假后复权跳变（原始价不动）。实测批次：2026-06-22（53 只）、
2026-07-01（911 只，回测曲线单日 +19.5% 假收益的来源）+ 离散个案；对照官方
按代码接口确认当前序列在边界处是平的（如 000670 全程 10.09、600202 全程 4.9813）。

假跳变会伪装成超额收益被标签/IC/回测学进去。修复 = 对受影响代码按官方**当前**
因子序列全量重算 close_hfq = close × adj_factor，使每只股票全历史同一基准。

配额：每个受影响代码 1 次调用（adj_factor by ts_code 全历史），~1000 代码 ≈ 1000 次。

用法：
    venv/bin/python scripts/repair_adj_factor.py                      # 体检：列出假跳变
    venv/bin/python scripts/repair_adj_factor.py --apply              # 重写 close_hfq
    venv/bin/python scripts/repair_adj_factor.py --apply --features   # 重写后连特征一起重算
"""
import argparse
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from loguru import logger
from sqlalchemy import text

from app.config import settings
from app.db.connection import get_sync_db

# 假跳变判定：后复权日收益越界而原始价收益正常（真除权的原始价会同步下移，不会命中）
_DETECT_SQL = """
WITH q AS (SELECT stock_code, trade_date, close, close_hfq FROM daily_quote
            WHERE close > 0 AND close_hfq > 0 AND stock_code ~ '^(60|68|00|30)'),
d AS (SELECT *, lag(close_hfq) OVER w h_prev, lag(close) OVER w c_prev,
             lag(trade_date) OVER w pd
      FROM q WINDOW w AS (PARTITION BY stock_code ORDER BY trade_date))
SELECT stock_code, trade_date
FROM d WHERE h_prev > 0 AND c_prev > 0 AND trade_date - pd <= 5
  AND trade_date >= :since
  AND abs(close_hfq/h_prev - 1) > 0.25 AND abs(close/c_prev - 1) < 0.11
"""

# 特征污染窗：边界后 60 个交易日（滚动窗口最长 60d）≈ 90 自然日
_FEATURE_WINDOW_DAYS = 90


def code_to_exchange(code: str) -> str:
    return 'SH' if code.startswith(('6', '9', '5')) else 'SZ'


def detect(db, since):
    rows = db.execute(text(_DETECT_SQL), {"since": since}).fetchall()
    df = pd.DataFrame(rows, columns=['stock_code', 'trade_date'])
    if df.empty:
        return df
    by_date = df.groupby(df['trade_date'].astype(str)).size().sort_values(ascending=False)
    print(f'假跳变 {len(df)} 行 / {df.stock_code.nunique()} 只（{since} 起）')
    for d, n in by_date.head(8).items():
        print(f'  {d}: {n} 只')
    if len(by_date) > 8:
        print(f'  … 其余 {len(by_date) - 8} 个日期')
    return df


def fetch_factors(pro, quota, ts_code, start, end):
    """官方当前因子序列 {date: factor}；失败重试 2 次（限流抖动）。"""
    for attempt in range(3):
        try:
            quota.consume()
            df = pro.adj_factor(ts_code=ts_code, start_date=start, end_date=end)
            if df is None or df.empty:
                return {}
            # tushare 日期键为 YYYYMMDD，统一成库内 YYYY-MM-DD
            out = {}
            for _, r in df.iterrows():
                if pd.notna(r.get('adj_factor')):
                    k = str(r['trade_date']).replace('-', '')
                    out[f"{k[:4]}-{k[4:6]}-{k[6:]}"] = float(r['adj_factor'])
            return out
        except Exception as e:
            if attempt == 2:
                logger.warning(f"[repair] {ts_code} 因子拉取失败: {e}")
                return None
            time.sleep(2)


def apply_fix(db, codes, dry_run_label=''):
    """逐代码重写 close_hfq = close × 官方因子。返回 {code: 改写行数}。"""
    import tushare as ts
    from crawler.adapters.tushare_quota import TushareQuota
    pro = ts.pro_api(settings.TUSHARE_TOKEN)
    quota = TushareQuota.get()

    changed = {}
    for i, code in enumerate(sorted(codes), 1):
        rng = db.execute(text(
            "SELECT min(trade_date), max(trade_date) FROM daily_quote WHERE stock_code=:c"),
            {"c": code}).fetchone()
        if not rng or not rng[0]:
            continue
        fac = fetch_factors(pro, quota, f"{code}.{code_to_exchange(code)}",
                            str(rng[0])[:10].replace('-', ''), str(rng[1])[:10].replace('-', ''))
        if not fac:   # 拉取失败或空返回都算失败，不得误报"已一致"
            changed[code] = -1
            logger.warning(f"[repair] {code} 因子序列为空，跳过")
            continue
        rows = db.execute(text(
            "SELECT trade_date, close, close_hfq FROM daily_quote "
            "WHERE stock_code=:c AND close > 0"), {"c": code}).fetchall()
        updates = []
        for d, close, hfq in rows:
            f = fac.get(str(d)[:10])
            if not f or not close:
                continue
            target = float(close) * f
            if hfq is None or abs(float(hfq) / target - 1) > 0.005:
                updates.append((target, code, d))
        if updates:
            db.execute(text(
                "UPDATE daily_quote SET close_hfq = :v WHERE stock_code=:c AND trade_date=:d"),
                [{"v": u[0], "c": u[1], "d": u[2]} for u in updates])
            db.commit()
        changed[code] = len(updates)
        if i % 100 == 0:
            print(f'  进度 {i}/{len(codes)}（累计改写 {sum(v for v in changed.values() if v > 0)} 行）')
    return changed


def recompute_features(db, boundaries):
    """对受影响代码 × 污染窗重算全部启用特征（DELETE 带代码过滤，不动其它股票）。"""
    from scripts.feature_compute import compute_feature
    feats = db.execute(text(
        "SELECT feature_name, formula FROM features "
        "WHERE target_entity='stock' AND status='enabled'")).fetchall()
    # 代码 → 污染窗 [min_boundary, max_boundary+90d]；窗与窗合并成少数大区间批量跑
    win_by_code = {}
    for code, d in boundaries:
        lo, hi = str(d)[:10], (pd.Timestamp(d) + timedelta(days=_FEATURE_WINDOW_DAYS)).strftime('%Y-%m-%d')
        if code in win_by_code:
            lo = min(lo, win_by_code[code][0])
            hi = max(hi, win_by_code[code][1])
        win_by_code[code] = (lo, hi)
    # 按起始日聚成 ≤6 个组，组内代码共用 [min_lo, max_hi]
    groups = {}
    for code, (lo, hi) in win_by_code.items():
        groups.setdefault(lo[:7], []).append((code, lo, hi))
    print(f'特征重算：{len(feats)} 个特征 × {len(groups)} 个窗口组 × {len(win_by_code)} 只')
    total = 0
    for gname, members in sorted(groups.items()):
        codes = [m[0] for m in members]
        lo = min(m[1] for m in members)
        hi = max(m[2] for m in members)
        for fn, formula in feats:
            try:
                res = compute_feature(db, fn, formula, 'stock', start_date=lo, end_date=hi,
                                      stock_codes=codes)
                total += res.get('rows', 0)
            except Exception as e:
                logger.warning(f'[repair] 特征 {fn} 重算失败（{lo}~{hi}）: {e}')
        print(f'  组 {gname}（{len(codes)} 只，{lo}~{hi}）完成')
    print(f'特征重算合计 {total:,} 行')
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2024-01-01', help='检测起点（默认 2024-01-01）')
    ap.add_argument('--apply', action='store_true', help='重写 close_hfq（默认只体检）')
    ap.add_argument('--features', action='store_true', help='--apply 后连特征一起重算')
    args = ap.parse_args()

    db = get_sync_db()
    try:
        bd = detect(db, args.since)
        if bd.empty:
            print('无需修复')
            return
        if not args.apply:
            print('\n（试运行：未改动。加 --apply 重写 close_hfq，--apply --features 连特征重算）')
            return
        codes = sorted(bd.stock_code.unique())
        changed = apply_fix(db, codes)
        n_ok = sum(1 for v in changed.values() if v > 0)
        n_zero = sum(1 for v in changed.values() if v == 0)
        n_fail = sum(1 for v in changed.values() if v < 0)
        print(f'\n重写完成：{len(codes)} 只中改写 {n_ok} 只 / 已一致 {n_zero} 只 / 拉取失败 {n_fail} 只；'
              f'共 {sum(v for v in changed.values() if v > 0):,} 行 close_hfq')
        # 复检
        print('\n── 复检 ──')
        detect(db, args.since)
        if args.features:
            print('\n── 特征重算 ──')
            recompute_features(db, bd[['stock_code', 'trade_date']].values.tolist())
    finally:
        db.close()


if __name__ == '__main__':
    main()
