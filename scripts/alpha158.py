"""Alpha158 因子库生成器（design/05 M5，从 Qlib contrib/data/loader.py 翻译为 KEPL）。

组：KBAR(9) + PRICE(5×5) + VOLUME(5×5) + ROLLING(15 类×5 窗) = 134 个因子。
要点：全部用最新 close 归一化 + 1e-12 防除零；价格/量组窗口 [5,10,20,30,60]
（ROC 组与 CLOSE 组在相同窗口下数学重复，按窗口体系统一后省略）。

用法：
    python scripts/alpha158.py insert                # 生成并入库 features 表
    python scripts/alpha158.py compute S E           # 计算 [S,E] 区间值
    python scripts/alpha158.py ic S E [--horizon 10] # IC 体检（逐因子落 factor_ic_stats）
    python scripts/alpha158.py dedup S E [--horizon 10] [--threshold 0.7]  # greedy 去冗精选
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WINDOWS = [5, 10, 20, 30, 60]
EPS = "1e-12"


def _gen_alpha158_formulas():
    """产出 [(name, formula, group, display)]，全部 KEPL 可执行。"""
    out = []

    # ── KBAR：K 线形态（Qlib kbar 组，Greater/Less → max2/min2）──
    kbar = [
        ('KMID',  '(close-open)/(open+EPS)'),
        ('KLEN',  '(high-low)/(open+EPS)'),
        ('KMID2', '(close-open)/(high-low+EPS)'),
        ('KUP',   '(high-max2(open,close))/(open+EPS)'),
        ('KUP2',  '(high-max2(open,close))/(high-low+EPS)'),
        ('KLOW',  '(min2(open,close)-low)/(open+EPS)'),
        ('KLOW2', '(min2(open,close)-low)/(high-low+EPS)'),
        ('KSFT',  '(min2(open,close)-low)/(open+EPS)'),
        ('KSFT2', '(2*close-high-low)/(high-low+EPS)'),
    ]
    # 注：KSFT 与 KLOW 公式同形（Qlib 原版 Less(open,close) 同款），保留下供 IC 对照
    for name, f in kbar:
        out.append((f'a158_{name}', f.replace('EPS', EPS), 'kbar', f'K线形态 {name}'))

    # ── PRICE / VOLUME：滞后值相对最新 close/volume 归一化 ──
    for d in WINDOWS:
        out.append((f'a158_OPEN_{d}',   f'ref(open, {d})/(close+{EPS})',   'price', f'Open{d}/Close'))
        out.append((f'a158_HIGH_{d}',   f'ref(high, {d})/(close+{EPS})',   'price', f'High{d}/Close'))
        out.append((f'a158_LOW_{d}',    f'ref(low, {d})/(close+{EPS})',    'price', f'Low{d}/Close'))
        out.append((f'a158_CLOSE_{d}',  f'ref(close, {d})/(close+{EPS})',  'price', f'Close{d}/Close'))
        out.append((f'a158_VWAP_{d}',   f'ref(amount/(volume+{EPS}), {d})/(close+{EPS})', 'price', f'VWAP{d}/Close'))
        out.append((f'a158_VOLUME_{d}', f'ref(volume, {d})/(max2(volume, 1)+{EPS})', 'volume', f'Volume{d}/Volume'))

    # ── ROLLING：16 类滚动统计 × 5 窗（ROC 与 CLOSE 同窗重复，省略）──
    rolling = [
        ('MA',   lambda d: f'ma(close, {d})/(close+{EPS})'),
        ('STD',  lambda d: f'std(close, {d})/(close+{EPS})'),
        ('BETA', lambda d: f'slope(close, {d})/(close+{EPS})'),
        ('RSQR', lambda d: f'rsquare(close, {d})'),
        ('RESI', lambda d: f'resi(close, {d})/(close+{EPS})'),
        ('MAX',  lambda d: f'hhv(high, {d})/(close+{EPS})'),
        ('MIN',  lambda d: f'llv(low, {d})/(close+{EPS})'),
        ('QTLU', lambda d: f'ts_quantile(close, {d}, 0.8)/(close+{EPS})'),
        ('QTLD', lambda d: f'ts_quantile(close, {d}, 0.2)/(close+{EPS})'),
        ('RANK', lambda d: f'ts_rank(close, {d})'),
        ('RSV',  lambda d: f'(close-llv(low, {d}))/(hhv(high, {d})-llv(low, {d})+{EPS})'),
        ('IMAX', lambda d: f'imax(high, {d})/{d}'),
        ('IMIN', lambda d: f'imin(low, {d})/{d}'),
        ('IMXD', lambda d: f'(imax(high, {d})-imin(low, {d}))/{d}'),
        ('CORR', lambda d: f'ts_corr(close, volume, {d})'),
    ]
    for name, mk in rolling:
        for d in WINDOWS:
            out.append((f'a158_{name}_{d}', mk(d), 'rolling', f'{name}({d}d)'))
    return out


def insert_features(db) -> int:
    """UPSERT 入 features 表（status=enabled，等待 IC 体检筛选）。"""
    from sqlalchemy import text
    n = 0
    for name, formula, group, display in _gen_alpha158_formulas():
        db.execute(text("""
            INSERT INTO features (feature_name, display_name, target_entity, description,
                formula, feature_group, tags, status)
            VALUES (:n, :d, 'stock', :desc, :f, :g, :tg, 'enabled')
            ON CONFLICT (feature_name) DO UPDATE SET
                formula=EXCLUDED.formula, display_name=EXCLUDED.display_name,
                feature_group=EXCLUDED.feature_group, status='enabled', updated_at=CURRENT_TIMESTAMP
        """), {"n": name, "d": display, "desc": f'Alpha158 移植（{group} 组）',
               "f": formula, "g": f'alpha158_{group}', "tg": '["alpha158","qlib"]'})
        n += 1
    db.commit()
    return n


def compute_values(db, start: str, end: str) -> dict:
    from scripts.feature_compute import compute_all_features
    names = [n for n, *_ in _gen_alpha158_formulas()]
    return compute_all_features(db, target_entity='stock', start_date=start,
                                end_date=end, feature_names=names)


def run_ic(db, start: str, end: str, horizon=(1, 5, 10, 20)) -> list:
    from scripts.factor_ic import compute_factor_ic
    names = [n for n, *_ in _gen_alpha158_formulas()]
    results = []
    for i, fn in enumerate(names):
        try:
            rs = compute_factor_ic(db, fn, start, end, horizons=horizon)
            results.extend(rs)
        except ValueError as e:
            results.append({'feature_name': fn, 'error': str(e)[:120]})
        if (i + 1) % 20 == 0:
            print(f'  [ic] {i + 1}/{len(names)}', flush=True)
    return results


def run_dedup(db, start: str, end: str, horizon: int = 10, threshold: float = 0.7,
              max_sections: int = 60) -> dict:
    """与特征页「去冗推荐」同口径：截面 cs_rank → pooled Pearson → greedy_dedup。"""
    import numpy as np
    import pandas as pd
    from sqlalchemy import text
    from scripts.pipeline import cs_rank_features
    from scripts.factor_ic import greedy_dedup

    names = sorted(n for n, *_ in _gen_alpha158_formulas())
    all_dates = [str(r[0])[:10] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM feature_values WHERE feature_name=:f "
        "AND trade_date BETWEEN :sd AND :ed ORDER BY trade_date"),
        {"f": names[0], "sd": start, "ed": end}).fetchall()]
    step = max(1, len(all_dates) // max_sections)
    sample_dates = all_dates[::step]
    selects = ",\n               ".join(
        f"MAX(value) FILTER (WHERE feature_name = '{fn}') AS \"{fn}\"" for fn in names)
    rows = db.execute(text(f"""
        SELECT trade_date, stock_code, {selects}
        FROM feature_values
        WHERE feature_name = ANY(:names) AND trade_date = ANY(CAST(:dates AS date[]))
        GROUP BY stock_code, trade_date
    """), {"names": names, "dates": sample_dates}).fetchall()
    df = pd.DataFrame(rows, columns=['trade_date', 'stock_code'] + names)
    for c in names:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = cs_rank_features(df, names)
    corr_df = df[list(names)].corr()
    corr = {}
    for a in names:
        for b in names:
            v = corr_df.loc[a, b]
            corr[(a, b)] = float(v) if pd.notna(v) else 0.0

    ic_rows = db.execute(text("""
        SELECT DISTINCT ON (feature_name) feature_name, rank_ic_ir, traffic
        FROM factor_ic_stats WHERE horizon = :h AND feature_name LIKE 'a158\\_%'
        ORDER BY feature_name, created_at DESC
    """), {"h": horizon}).fetchall()
    icir = {r[0]: float(r[1]) if r[1] is not None else 0.0 for r in ic_rows}
    traffic = {r[0]: r[2] for r in ic_rows}
    cand = {f: icir.get(f, 0.0) for f in names}
    selected, skipped = greedy_dedup(corr, cand, threshold=threshold)
    return {'sections': len(sample_dates), 'n_factors': len(names),
            'icir': icir, 'traffic': traffic,
            'recommended': selected, 'skipped': skipped}


def main():
    import argparse
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

    p = argparse.ArgumentParser()
    p.add_argument('cmd', choices=['insert', 'compute', 'ic', 'dedup'])
    p.add_argument('start', nargs='?')
    p.add_argument('end', nargs='?')
    p.add_argument('--horizon', type=int, default=10)
    p.add_argument('--threshold', type=float, default=0.7)
    args = p.parse_args()

    from app.db.connection import SyncSessionLocal
    db = SyncSessionLocal()
    try:
        if args.cmd == 'insert':
            n = insert_features(db)
            print(f'Alpha158 因子入库/更新 {n} 条')
        elif args.cmd == 'compute':
            r = compute_values(db, args.start, args.end)
            print(f"计算完成: {r.get('features')} 因子 / {r.get('rows')} 行 / 错误 {len(r.get('errors', []))}")
            for e in r.get('errors', [])[:10]:
                print('  ERR', e)
        elif args.cmd == 'ic':
            rs = run_ic(db, args.start, args.end)
            ok = [r for r in rs if 'error' not in r]
            print(f"IC 完成: {len(ok)} 成功 / {len(rs) - len(ok)} 失败")
            for r in rs:
                if 'error' in r:
                    print('  ERR', r['feature_name'], r['error'])
        elif args.cmd == 'dedup':
            r = run_dedup(db, args.start, args.end, args.horizon, args.threshold)
            import json
            print(json.dumps({k: r[k] for k in ('sections', 'n_factors')}, ensure_ascii=False))
            print(f"推荐精选（|ICIR|≥0.10 且相关≤{args.threshold}）: {len(r['recommended'])} 个")
            for f in r['recommended']:
                print(f"  ✅ {f}  ICIR={r['icir'].get(f, 0):.3f}  {r['traffic'].get(f, '')}")
            print(f"跳过 {len(r['skipped'])} 个（前 10）:")
            for s in r['skipped'][:10]:
                print(f"  ⏭ {s['factor']}: {s['reason']}")
    finally:
        db.close()


if __name__ == '__main__':
    main()
