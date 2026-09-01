"""DoubleEnsemble 训练脚本（design/05 M6）：v12 实验版，与 v11 同特征同区间。

数据管线复刻 v11（cs_rank + excess 标签 + 派生 idx_ret_20d）；模型换 DEModel
（LightGBM 子模型集成）。产物 pickle 落 data/models/{ver}/xgb_{h}d.pkl——
DEModel 暴露 feature_names_in_ + predict(ndarray)，predict_for_version /
walk-forward / 信号栈零改动兼容。

用法：
    venv/bin/python scripts/train_de.py --version v12.0 --baseline v11.0
可选：--horizons 5,10,20 --train-start --train-end --valid-start --valid-end
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
import pickle

import numpy as np
import pandas as pd


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))
    from sqlalchemy import text
    from app.db.connection import SyncSessionLocal
    from scripts.pipeline import build_feature_wide_table, cs_rank_features
    from strategy.models.double_ensemble import DEModel

    p = argparse.ArgumentParser()
    p.add_argument('--version', default='v12.0')
    p.add_argument('--baseline', default='v11.0')
    p.add_argument('--horizons', default='5,10,20')
    p.add_argument('--train-start', default=None, help='默认取 baseline 配置')
    p.add_argument('--train-end', default=None)
    p.add_argument('--valid-days', type=int, default=90, help='训练窗尾部 N 自然日做早停验证')
    p.add_argument('--num-models', type=int, default=5)
    p.add_argument('--epochs', type=int, default=300)
    p.add_argument('--early-stopping', type=int, default=0,
                   help='早停轮数；0=关闭（Qlib DE 默认无早停——excess 标签的验证 MSE 纯噪声，'
                        '早停会把弱信号子模型掐死在 1 棵树）')
    args = p.parse_args()

    db = SyncSessionLocal()
    base_row = db.execute(text(
        "SELECT config FROM model_versions WHERE version=:v"), {"v": args.baseline}).fetchone()
    if not base_row:
        raise SystemExit(f'基线 {args.baseline} 不存在')
    bcfg = base_row[0] if isinstance(base_row[0], dict) else json.loads(base_row[0] or '{}')
    feature_names = bcfg.get('feature_names', [])
    feature_norm = bcfg.get('feature_norm', 'none')
    label_mode = bcfg.get('label_mode', 'excess')
    train_start = args.train_start or bcfg.get('train_start', '2023-01-01')
    train_end = args.train_end or bcfg.get('train_end', '2025-12-31')
    horizons = [int(h) for h in args.horizons.split(',')]
    print(f"基线 {args.baseline}: 特征 {len(feature_names)} norm={feature_norm} "
          f"label={label_mode} 训练窗 {train_start}~{train_end}")

    # ── 宽表（含派生 idx_ret_20d：与训练/推理端一致）──
    df = build_feature_wide_table(db, feature_names, train_start, train_end, 'stock')
    df = df.sort_values(['trade_date', 'stock_code'], kind='stable').reset_index(drop=True)
    df['close'] = df['close'].astype(float)
    mf = list(feature_names)
    if 'idx_ret_20d' in mf or True:
        idx_rows = db.execute(text(
            "SELECT trade_date, close FROM index_daily_quote WHERE index_code='000300' "
            "AND trade_date BETWEEN :s AND :e ORDER BY trade_date"),
            {"s": train_start, "e": train_end}).fetchall()
        if idx_rows:
            idx_df = pd.DataFrame(idx_rows, columns=['trade_date', 'idx_close'])
            idx_df['trade_date'] = idx_df['trade_date'].astype(str)
            idx_df['idx_close'] = idx_df['idx_close'].astype(float)
            idx_df['idx_ret_20d'] = idx_df['idx_close'].pct_change(20)
            df = df.merge(idx_df[['trade_date', 'idx_ret_20d']], on='trade_date', how='left')
            mf = mf + ['idx_ret_20d'] if 'idx_ret_20d' not in mf else mf
    mf = list(dict.fromkeys(mf))
    df['idx_close'] = df['trade_date'].map(idx_df.set_index('trade_date')['idx_close']) if idx_rows else np.nan

    if feature_norm == 'cs_rank':
        df = cs_rank_features(df, mf)

    # ── 标签：前瞻 h 日收益（excess=减同期指数）──
    g = df.groupby('stock_code')['close']
    for h in horizons:
        fwd = g.shift(-h) / df['close'] - 1
        if label_mode == 'excess':
            idx_fwd = df.groupby('trade_date')['idx_close'].transform(lambda s: s.shift(-h) / s - 1)
            fwd = fwd - idx_fwd
        df[f'label_{h}d'] = fwd
    print(f"样本 {len(df):,} 行 / 特征 {mf}")

    # ── 训练/验证切分：训练窗尾部 valid-days 做早停验证 ──
    cut_date = (pd.Timestamp(train_end) - pd.Timedelta(days=args.valid_days)).strftime('%Y-%m-%d')
    tr_mask = df['trade_date'] < cut_date
    va_mask = (df['trade_date'] >= cut_date) & (df['trade_date'] <= train_end)
    print(f"训练 {tr_mask.sum():,} / 早停验证 {va_mask.sum():,}（切分日 {cut_date}）")

    os.makedirs(f'data/models/{args.version}', exist_ok=True)
    best_params = {}
    for h in horizons:
        sub = df.dropna(subset=mf + [f'label_{h}d'])
        is_va = sub['trade_date'] >= cut_date
        X_tr, y_tr = sub.loc[~is_va, mf], sub.loc[~is_va, f'label_{h}d']
        X_va, y_va = sub.loc[is_va, mf], sub.loc[is_va, f'label_{h}d']
        print(f"[{h}d] 训练 {len(X_tr):,} / 验证 {len(X_va):,}")
        model = DEModel(mf, num_models=args.num_models, epochs=args.epochs,
                        early_stopping_rounds=args.early_stopping or None)
        model.fit(X_tr, y_tr, X_va, y_va)
        pred = model.predict(X_va.values)
        ss_res = float(((y_va.values - pred) ** 2).sum())
        ss_tot = float(((y_va.values - y_va.mean()) ** 2).sum())
        r2 = 1 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
        path = os.path.abspath(f'data/models/{args.version}/xgb_{h}d.pkl')
        with open(path, 'wb') as f:
            pickle.dump(model, f)
        best_params[f'{h}d'] = {'r2': round(r2, 4), 'model_path': path,
                                'params': {'model_type': 'de_lightgbm', 'num_models': args.num_models,
                                           'epochs': args.epochs, 'lgb_params': model.lgb_params,
                                           'sub_features': model.sub_features}}
        print(f"[{h}d] 验证 r²={r2:.4f}（子模型 {len(model.ensemble)} 个）→ {path}")

    # ── 落 model_versions（PENDING，走同一审批/walk-forward 门禁）──
    config = dict(bcfg)
    config.update({'model_type': 'de_lightgbm', 'feature_names': feature_names,
                   'feature_norm': feature_norm, 'label_mode': label_mode,
                   'train_start': train_start, 'train_end': train_end,
                   'derived_features': mf})
    db.execute(text("""
        INSERT INTO model_versions (version, model_name, status, config, best_params, trained_at)
        VALUES (:v, :n, 'PENDING', :c, :bp, CURRENT_TIMESTAMP)
        ON CONFLICT (version) DO UPDATE SET status='PENDING', config=EXCLUDED.config,
            best_params=EXCLUDED.best_params, trained_at=EXCLUDED.trained_at
    """), {"v": args.version, "n": f"DoubleEnsemble-LightGBM 实验（基线 {args.baseline}）",
           "c": json.dumps(config, ensure_ascii=False), "bp": json.dumps(best_params)})
    db.commit()
    db.close()
    print(f"✅ {args.version} 训练完成（PENDING）")


if __name__ == '__main__':
    main()
