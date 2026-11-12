#!/usr/bin/env python
"""分钟级模型研究脚本（P3）——数据够了才训练，只产研究产物，不写生产信号。

数据来源：app/intraday_watch.py 在盘中把 10s 快照合成 1 分钟 bar 落 stock_minute_bar。
本脚本做四件事：
1. 覆盖度体检（交易日数/bar 数/股票数/每分钟截面大小），不达标直接拒绝；
2. 特征/标签：分钟收益、波动、量比、VWAP 偏离、日内位置、时段 + 未来 H 分钟收益；
3. 按交易日时间顺序切 train/val/test（默认 60/20/20，标签不跨午休/收盘）；
4. XGBoost 回归 + 按 bar 的 RankIC、方向命中率、TopK 组合毛/净收益。

为什么先拒绝训练：分钟级模型最容易在「只有几天数据」时过拟合出漂亮数字。
没有 20 个交易日、1 万根 bar、3 只票，脚本不会产出模型——这不是保守，是防止
把噪声当 alpha 写进生产。当前关注池只有几只票时，截面 RankIC 也会非常不稳，
脚本会在 coverage 里明确给出 median_stocks_per_bar。

用法：
    venv/bin/python scripts/train_intraday.py --report          # 只看覆盖度
    venv/bin/python scripts/train_intraday.py --horizon 15      # 达标后训练
    venv/bin/python scripts/train_intraday.py --horizon 30 --topk 2 --cost-bps 15
"""
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from loguru import logger
from scipy.stats import spearmanr
from sqlalchemy import text

from app.db.connection import get_sync_db

BAR_COLUMNS = ['stock_code', 'bar_time', 'open', 'high', 'low', 'close',
               'volume', 'amount', 'vwap', 'volume_ratio', 'snapshots']
FEATURES = [
    'ret_1', 'ret_3', 'ret_5', 'ret_10', 'ret_15', 'ret_30',
    'vol_5', 'vol_15', 'vol_30',
    'vol_ratio_5', 'vol_ratio_15', 'vol_ratio_30', 'vol_ratio_day',
    'vwap_dev', 'trade_avg_dev', 'range_pos', 'range_pct', 'body_pct',
    'log_volume', 'log_amount', 'tod',
]


def load_bars(db, start=None, end=None, min_snapshots=2) -> pd.DataFrame:
    sql = ("SELECT stock_code, bar_time, open, high, low, close, volume, amount, "
           "vwap, volume_ratio, snapshots FROM stock_minute_bar "
           "WHERE snapshots >= :ms")
    params = {'ms': min_snapshots}
    if start:
        sql += " AND bar_time >= :start"
        params['start'] = start
    if end:
        sql += " AND bar_time <= :end"
        params['end'] = f'{end} 23:59:59' if len(str(end)) == 10 else end
    sql += " ORDER BY stock_code, bar_time"
    rows = db.execute(text(sql), params).fetchall()
    df = pd.DataFrame(rows, columns=BAR_COLUMNS)
    # psycopg2 的 NUMERIC → Decimal，pandas 会推成 object，groupby.cummax/rolling 直接炸；
    # 这里统一转数值，bar_time 转 datetime（空表也安全）。
    for c in ['open', 'high', 'low', 'close', 'volume', 'amount',
              'vwap', 'volume_ratio', 'snapshots']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['bar_time'] = pd.to_datetime(df['bar_time'])
    return df


def coverage(df: pd.DataFrame, min_days=20, min_bars=10000, min_stocks=3) -> dict:
    if df.empty:
        return {'rows': 0, 'days': 0, 'stocks': 0, 'first': None, 'last': None,
                'median_stocks_per_bar': 0, 'ready': False,
                'thresholds': {'min_days': min_days, 'min_bars': min_bars,
                               'min_stocks': min_stocks}}
    d = df.copy()
    d['date'] = pd.to_datetime(d['bar_time']).dt.date
    # 按「不同分钟数」而非行数：3 只票的半天也有几百行，会把半日误算成完整交易日。
    # 完整 A 股交易日约 240 个分钟点，留 40 分钟容差（启动/网络抖动）。
    per_day = d.groupby('date')['bar_time'].nunique()
    days = int((per_day >= 200).sum())
    stocks = int(d['stock_code'].nunique())
    per_bar = d.groupby('bar_time')['stock_code'].nunique()
    return {
        'rows': int(len(d)), 'days': days, 'stocks': stocks,
        'first': str(d['bar_time'].min()), 'last': str(d['bar_time'].max()),
        'median_stocks_per_bar': float(per_bar.median()) if len(per_bar) else 0,
        'ready': bool(days >= min_days and len(d) >= min_bars and stocks >= min_stocks),
        'thresholds': {'min_days': min_days, 'min_bars': min_bars,
                       'min_stocks': min_stocks},
    }


def compute_features(df: pd.DataFrame, horizon=15, min_snapshots=2):
    """按 (stock_code, date) 分组做特征与标签；返回 (df, feature_cols)。

    标签 = 同一交易日内 H 分钟后的 close/当前 close - 1；要求未来第 H 根 bar 的
    时间戳严格等于当前+H 分钟，因此午休/收盘/断点处的标签为 NaN（不会把隔夜或
    午休跳空当成 H 分钟收益）。标签裁剪到 ±5%，减少极端值对 MSE 的支配。
    """
    d = df.copy()
    d['bar_time'] = pd.to_datetime(d['bar_time'])
    d = d[d['snapshots'] >= min_snapshots]
    if d.empty:
        return d, []
    d = d.sort_values(['stock_code', 'bar_time']).reset_index(drop=True)
    d['date'] = d['bar_time'].dt.date
    keys = ['stock_code', 'date']
    g = d.groupby(keys, sort=False)

    for n in (1, 3, 5, 10, 15, 30):
        d[f'ret_{n}'] = g['close'].pct_change(n)
    d['ret_1'] = d['ret_1'].clip(-0.2, 0.2)      # 坏 tick 保护

    for n in (5, 15, 30):
        d[f'vol_{n}'] = d.groupby(keys)['ret_1'].transform(
            lambda s: s.rolling(n, min_periods=max(2, n // 2)).std())
        d[f'vol_ma_{n}'] = d.groupby(keys)['volume'].transform(
            lambda s: s.rolling(n, min_periods=max(2, n // 2)).mean())
        d[f'vol_ratio_{n}'] = d['volume'] / d[f'vol_ma_{n}'].replace(0, np.nan)

    d['day_high'] = g['high'].cummax()
    d['day_low'] = g['low'].cummin()
    rng = (d['day_high'] - d['day_low']).replace(0, np.nan)
    d['range_pos'] = ((d['close'] - d['day_low']) / rng).clip(0, 1)
    d['range_pct'] = (d['high'] - d['low']) / d['close'].replace(0, np.nan)
    d['body_pct'] = (d['close'] - d['open']) / d['open'].replace(0, np.nan)
    d['vwap_dev'] = d['close'] / d['vwap'].replace(0, np.nan) - 1
    trade_avg = d['amount'] / (d['volume'] * 100)     # volume 单位：手
    d['trade_avg_dev'] = d['close'] / trade_avg.replace(0, np.nan) - 1
    d['log_volume'] = np.log1p(d['volume'].fillna(0).clip(lower=0))
    d['log_amount'] = np.log1p(d['amount'].fillna(0).clip(lower=0))
    d['tod'] = d['bar_time'].dt.hour * 60 + d['bar_time'].dt.minute
    d['vol_ratio_day'] = d['volume_ratio']

    # 标签（严格同交易日 + 严格 H 分钟后）
    d['fwd_time'] = g['bar_time'].shift(-horizon)
    d['fwd_ret'] = g['close'].shift(-horizon) / d['close'] - 1
    expected = d['bar_time'] + pd.Timedelta(minutes=horizon)
    valid = d['fwd_time'].notna() & (d['fwd_time'] == expected)
    d.loc[~valid, 'fwd_ret'] = np.nan
    d['fwd_ret'] = d['fwd_ret'].clip(-0.05, 0.05)

    features = [c for c in FEATURES if c in d.columns and d[c].notna().any()]
    return d, features


def split_dates(dates, train_frac=0.6, val_frac=0.2, embargo_days=0):
    """按交易日时间顺序切分；embargo_days 可丢弃切分边界上的相邻交易日。"""
    ds = sorted(set(dates))
    n = len(ds)
    if n < 5:
        raise ValueError(f'交易日不足 5 天（当前 {n}），无法切分')
    n_train = max(2, int(n * train_frac))
    n_val = max(1, int(n * val_frac))
    train = ds[:n_train]
    val_start = n_train + embargo_days
    val = ds[val_start:val_start + n_val]
    test_start = val_start + n_val + embargo_days
    test = ds[test_start:]
    if not val or not test:
        raise ValueError(f'切分后 val/test 为空（days={n}, train={n_train}, val={n_val}）')
    return train, val, test


def evaluate_predictions(pred, y, dates, bar_times, horizon=15,
                         topk=2, cost_bps=15) -> dict:
    """按 bar 截面 RankIC + 方向命中 + TopK 组合收益（毛/按 H 分钟非重叠净）。"""
    df = pd.DataFrame({'pred': np.asarray(pred), 'y': np.asarray(y),
                       'date': list(dates), 'bar_time': list(bar_times)})
    ics = []
    for _, sub in df.groupby('bar_time'):
        if len(sub) >= 3 and sub['pred'].nunique() > 1 and sub['y'].nunique() > 1:
            ic = spearmanr(sub['pred'], sub['y']).correlation
            if ic == ic:
                ics.append(float(ic))
    ic_mean = float(np.mean(ics)) if ics else None
    ic_std = float(np.std(ics)) if ics else None
    icir = (ic_mean / ic_std) if ic_mean is not None and ic_std else None

    mask = np.abs(df['y'].values) > 0.001
    hit = float((np.sign(df['pred'].values) == np.sign(df['y'].values))[mask].mean()) \
        if mask.any() else None

    top_rets = []
    for _, sub in df.groupby('bar_time'):
        if len(sub) >= topk:
            top_rets.append(float(sub.nlargest(topk, 'pred')['y'].mean()))
    top_gross = float(np.mean(top_rets)) if top_rets else None

    # 非重叠组合：每个交易日按 H 分钟取一个时间点，避免重叠标签重复计利
    non_overlap = []
    for _, sub in df.groupby('date'):
        for bt in sorted(sub['bar_time'].unique()):
            minute = bt.hour * 60 + bt.minute
            if minute % horizon != 0:
                continue
            x = sub[sub['bar_time'] == bt]
            if len(x) >= topk:
                non_overlap.append(float(x.nlargest(topk, 'pred')['y'].mean()))
    top_net = (float(np.mean(non_overlap)) - cost_bps / 1e4) if non_overlap else None

    return {
        'n_obs': int(len(df)), 'n_ic_bars': len(ics),
        'rank_ic_mean': ic_mean, 'rank_ic_std': ic_std, 'rank_ic_icir': icir,
        'rank_ic_pos_ratio': (float(np.mean(np.array(ics) > 0)) if ics else None),
        'direction_hit': hit, 'n_direction_obs': int(mask.sum()),
        'topk_gross': top_gross,
        'topk_net_horizon': top_net,
        'n_non_overlap_bars': len(non_overlap),
    }


def train_model(X_train, y_train, X_val, y_val, params=None):
    from xgboost import XGBRegressor
    p = dict(n_estimators=500, max_depth=3, learning_rate=0.05,
             subsample=0.8, colsample_bytree=0.8, min_child_weight=50,
             reg_lambda=1.0, objective='reg:squarederror',
             n_jobs=-1, random_state=42, early_stopping_rounds=50)
    if params:
        p.update(params)
    model = XGBRegressor(**p)
    model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
    return model


def _print_metrics(tag, m):
    if not m:
        print(f'  {tag}: 无有效样本')
        return
    fmt = lambda v, n=4: '—' if v is None else f'{v:.{n}f}'
    print(f'  {tag}: RankIC {fmt(m["rank_ic_mean"])} ± {fmt(m["rank_ic_std"])} '
          f'(ICIR {fmt(m["rank_ic_icir"], 2)}, 正比例 {fmt(m["rank_ic_pos_ratio"], 3)}, '
          f'bar {m["n_ic_bars"]}) | 方向命中 {fmt(m["direction_hit"], 3)} | '
          f'TopK 毛 {fmt(m["topk_gross"], 5)} / 净 {fmt(m["topk_net_horizon"], 5)}')


def main():
    ap = argparse.ArgumentParser(description='分钟级模型研究（P3）')
    ap.add_argument('--horizon', type=int, default=15, help='标签未来 N 分钟（默认 15）')
    ap.add_argument('--min-days', type=int, default=20, help='最少完整交易日')
    ap.add_argument('--min-bars', type=int, default=10000, help='最少 bar 数')
    ap.add_argument('--min-stocks', type=int, default=3, help='最少股票数')
    ap.add_argument('--min-snapshots', type=int, default=2, help='每根 bar 最少快照数')
    ap.add_argument('--topk', type=int, default=2, help='TopK 组合股票数')
    ap.add_argument('--cost-bps', type=float, default=15, help='单次调仓成本（bps，默认 15）')
    ap.add_argument('--train-frac', type=float, default=0.6)
    ap.add_argument('--val-frac', type=float, default=0.2)
    ap.add_argument('--embargo-days', type=int, default=0)
    ap.add_argument('--start', default=None, help='bar_time >= YYYY-MM-DD')
    ap.add_argument('--end', default=None, help='bar_time <= YYYY-MM-DD 23:59:59')
    ap.add_argument('--out-dir', default='data/models_intraday')
    ap.add_argument('--report', action='store_true', help='只输出覆盖度，不训练')
    args = ap.parse_args()

    db = get_sync_db()
    try:
        df = load_bars(db, args.start, args.end, args.min_snapshots)
    finally:
        db.close()

    cov = coverage(df, args.min_days, args.min_bars, args.min_stocks)
    print('== 数据覆盖度 ==')
    print(json.dumps(cov, ensure_ascii=False, indent=2, default=str))
    if args.report or not cov['ready']:
        if not cov['ready']:
            print('\n数据不足，暂不训练。让 watcher 继续采集（collect_minute=true）；'
                  '达标线：{} 个完整交易日 / {} 根 bar / {} 只股票。'.format(
                      args.min_days, args.min_bars, args.min_stocks))
            return 0 if args.report else 2
        return 0

    d, features = compute_features(df, args.horizon, args.min_snapshots)
    d = d.dropna(subset=features + ['fwd_ret'])
    if len(d) < 500:
        print(f'\n可用样本仅 {len(d)} 行（特征/标签缺失后），不足 500，暂不训练。')
        return 2
    print(f'\n可用样本 {len(d)} 行 | 特征 {len(features)} 个 | horizon={args.horizon}m')
    if cov['median_stocks_per_bar'] < 10:
        print(f"⚠ 每分钟截面仅 {cov['median_stocks_per_bar']:.1f} 只股票，"
              f"RankIC 会很不稳定；建议把关注池扩到几十只以上再解读数字。")

    try:
        train_d, val_d, test_d = split_dates(
            d['date'].unique(), args.train_frac, args.val_frac, args.embargo_days)
    except ValueError as e:
        print(f'切分失败：{e}')
        return 2

    def part(days):
        return d[d['date'].isin(days)]

    tr, va, te = part(train_d), part(val_d), part(test_d)
    if min(len(tr), len(va), len(te)) < 100:
        print(f'切分后样本过少：train={len(tr)} val={len(va)} test={len(te)}')
        return 2

    X_cols = features
    model = train_model(tr[X_cols], tr['fwd_ret'].values,
                        va[X_cols], va['fwd_ret'].values)
    val_m = evaluate_predictions(model.predict(va[X_cols]), va['fwd_ret'].values,
                                 va['date'], va['bar_time'], args.horizon,
                                 args.topk, args.cost_bps)
    test_m = evaluate_predictions(model.predict(te[X_cols]), te['fwd_ret'].values,
                                  te['date'], te['bar_time'], args.horizon,
                                  args.topk, args.cost_bps)

    print(f'\n== 切分 ==\n  train {len(train_d)}d/{len(tr)}行 | '
          f'val {len(val_d)}d/{len(va)}行 | test {len(test_d)}d/{len(te)}行')
    print('== 评估 ==')
    _print_metrics('val ', val_m)
    _print_metrics('test', test_m)

    importance = sorted(zip(features, model.feature_importances_),
                        key=lambda x: -x[1])[:15]
    print('\n== Top 15 特征重要性 ==')
    for name, score in importance:
        print(f'  {name:14s} {score:.4f}')

    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    model_path = os.path.join(out_dir, f'intraday_{args.horizon}m_{stamp}.json')
    meta_path = os.path.join(out_dir, f'intraday_{args.horizon}m_{stamp}_meta.json')
    model.save_model(model_path)
    meta = {
        'kind': 'intraday_research', 'horizon_min': args.horizon,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'coverage': cov, 'features': features,
        'split': {'train_days': [str(x) for x in train_d],
                  'val_days': [str(x) for x in val_d],
                  'test_days': [str(x) for x in test_d],
                  'train_rows': len(tr), 'val_rows': len(va), 'test_rows': len(te)},
        'params': {'n_estimators': 500, 'max_depth': 3, 'learning_rate': 0.05,
                   'subsample': 0.8, 'colsample_bytree': 0.8,
                   'min_child_weight': 50, 'reg_lambda': 1.0},
        'metrics': {'val': val_m, 'test': test_m},
        'feature_importance': [{'name': n, 'score': float(s)} for n, s in importance],
        'note': '研究产物；未写入 signal_history/model_versions，不作为生产信号。',
    }
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, default=str)
    print(f'\n模型: {model_path}\n元数据: {meta_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
