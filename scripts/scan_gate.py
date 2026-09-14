#!/usr/bin/env python
"""闸门前沿重扫：在**干净数据基线**上重扫 熔断档位 × regime 空仓闸门 × trailing × 持有期。

为什么重扫：v13 期的前沿结论（用户拍板 F1 = 熔断10% + 空仓闸门 ma20/skip2 + trailing 5%）
是在被复权因子假跳变污染的数据上得出的——2026-07-01 批次在 v15 test 窗口制造过单日
+19.5% 假收益并触发过假止损。存量修复 + 特征重算 + eval_version 重算存档完成后，
需要验证：干净基线上 F1 是否仍在前沿上，还是存在更优组合。

实现：完全复用 eval_version 的载入路径（build_feature_wide_table / prepare_model_frame /
pred_score / run_training_backtest 模块级四件套），宽表与预测分只算一次，然后按网格
切换 dd_gate / gated_dates / trailing / hold_days 逐组合回测。不写库，结果落 CSV。

自验锚点：基线组合（现行 F1 档）必须逐位复现 v15.0 存档 label_detail
（5d 夏普 5.00 / 10d 4.27 / 20d -1.08，均值夏普 2.7303）——锚点不中说明 harness 有 bug，
网格数字全部作废。

用法：
    venv/bin/python scripts/scan_gate.py v15.0                 # 全网格（超时预算自动降级）
    venv/bin/python scripts/scan_gate.py v15.0 --quick         # 粗网格
    venv/bin/python scripts/scan_gate.py v15.0 --max-minutes 45 # 预算上限
"""
import argparse
import csv
import json as _json
import os
import pickle
import sys
import time
from datetime import date as _date, timedelta as _td

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from loguru import logger
from sqlalchemy import text

from app.db.connection import get_sync_db
from scripts.pipeline import (build_feature_wide_table, compute_regime_gates,
                              pred_score, prepare_model_frame, run_training_backtest)

TARGETS = [('5d', 'target_5d', 5), ('10d', 'target_10d', 10), ('20d', 'target_20d', 20)]

# ── 网格定义 ────────────────────────────────────────────────────────────
# regime 候选：(标签, cfg)。cfg=None 表示闸门关闭。
BASE_REGIME = {'enabled': True, 'ma_window': 20, 'index_code': '000300', 'max_skip_days': 2}
REGIME_GRID_FULL = [
    ('off', None),
    ('ma20s2(F1)', BASE_REGIME),
    ('ma20s1', {**BASE_REGIME, 'max_skip_days': 1}),
    ('ma20s4', {**BASE_REGIME, 'max_skip_days': 4}),
    ('ma10s2', {**BASE_REGIME, 'ma_window': 10}),
    ('ma60s2', {**BASE_REGIME, 'ma_window': 60}),
]
REGIME_GRID_QUICK = [REGIME_GRID_FULL[i] for i in (0, 1, 4)]

DD_GRID_FULL = [None, 0.05, 0.08, 0.10, 0.12, 0.15]
DD_GRID_QUICK = [None, 0.08, 0.10, 0.12]

TRAIL_GRID_FULL = [0.0, 0.03, 0.05, 0.08, 0.12]
TRAIL_GRID_QUICK = [0.0, 0.05, 0.08]
# ────────────────────────────────────────────────────────────────────────


def _annualize(bt):
    days = len(bt.get('equity_curve', []))
    if days >= 2 and bt.get('total_return', -1) > -1:
        return (1 + bt['total_return']) ** (252 / days) - 1
    return 0


def load_context(db, ver):
    """载入模型/配置/宽表/预测分——全部只算一次，网格循环零重复成本。"""
    row = db.execute(text("SELECT config, best_params FROM model_versions WHERE version=:v"),
                     {"v": ver}).fetchone()
    if not row:
        raise SystemExit(f'版本不存在: {ver}')
    cfg = _json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
    bp = _json.loads(row[1]) if isinstance(row[1], str) else (row[1] or {})
    feature_names = cfg.get('feature_names') or cfg.get('features') or []
    if not feature_names:
        raise SystemExit('该版本未配置 feature_names')

    models = {}
    for label, _, _ in TARGETS:
        path = (bp.get(label) or {}).get('model_path')
        if path and os.path.exists(path):
            with open(path, 'rb') as f:
                models[label] = pickle.load(f)
    if not models:
        raise SystemExit(f'未找到模型文件: {ver}')
    logger.info(f'[scan] {ver} 载入模型: {sorted(models)}')

    data_start = cfg.get('train_start', '2024-01-01')
    end_date = (_date.today() - _td(days=2)).isoformat()
    df = build_feature_wide_table(db, feature_names, data_start, end_date, 'stock')
    if len(df) < 5000:
        raise SystemExit(f'特征数据不足({len(df)} 行)')
    df, FEATURES = prepare_model_frame(db, df, cfg, feature_names, data_start, end_date)
    logger.info(f'[scan] 面板 {len(df):,} 行 / {len(FEATURES)} 特征')

    dates = sorted(df['trade_date'].unique())
    n = len(dates)
    train_cut, test_cut = dates[int(n * 0.6)], dates[int(n * 0.8)]
    val_mask = (df['trade_date'] >= train_cut) & (df['trade_date'] < test_cut)
    test_mask = df['trade_date'] >= test_cut
    logger.info(f'[scan] val {str(train_cut)[:10]}~{str(test_cut)[:10]} / test {str(test_cut)[:10]}~')

    # 每个 mask 的子帧 + 各周期预测分只算一次
    masks = {}
    for mname, mask in [('val', val_mask), ('test', test_mask)]:
        sub = df[mask]
        preds = {label: pred_score(models[label], sub[FEATURES].values) for label in models}
        masks[mname] = (sub, preds)

    sl = cfg.get('risk', {}).get('stop_loss_pct', 8) / 100.0
    common = dict(initial_cash=cfg.get('initial_cash', 1_000_000),
                  max_pos=cfg.get('max_positions', 5), bt_ver=ver,
                  stop_loss=sl, take_profit=sl * 2,
                  commission=cfg.get('commission', 0.00025),
                  stamp_tax=cfg.get('stamp_tax', 0.001),
                  slippage=cfg.get('slippage', 0.001))
    return cfg, models, masks, common


def run_one(masks, mask_name, label, hdays, gated, dd, trail, common):
    """单组合单周期回测（gated/dd/trail 为该组合的闸门参数）。"""
    sub, preds = masks[mask_name]
    tname = next(t[1] for t in TARGETS if t[0] == label)
    bt = run_training_backtest(sub[tname].values, preds[label], sub['trade_date'].values,
                               sub['stock_code'].values, sub['close'].values,
                               sub['volume'].values, hdays, bt_label=label,
                               limit_up=sub['_limit_up'].values,
                               limit_down=sub['_limit_down'].values,
                               gated_dates=gated, dd_gate=dd, trailing=trail, **common)
    return bt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('version')
    ap.add_argument('--quick', action='store_true', help='粗网格（组合数 ~1/4）')
    ap.add_argument('--max-minutes', type=float, default=75,
                    help='网格预算（分钟），超限自动降级粗网格')
    ap.add_argument('--out', default=None, help='CSV 输出路径（默认 data/scan_gate_<ver>_<ts>.csv）')
    args = ap.parse_args()

    db = get_sync_db()
    try:
        cfg, models, masks, common = load_context(db, args.version)
        db.commit()   # 释放载入阶段的读锁（长事务会卡住别处的 DDL，见下注）
        labels_scan = [l for l in ('5d', '10d') if l in models]   # 粗扫只跑健康周期
        if not labels_scan:
            raise SystemExit('无 5d/10d 模型可扫')

        # ── 锚点自验：现行配置（F1 档）必须复现存档数字 ──
        base_regime_cfg = cfg.get('regime') or {}
        base_gated = compute_regime_gates(db, masks['test'][0]['trade_date'].unique(),
                                          base_regime_cfg)
        base_dd = (cfg.get('portfolio_gate') or {}).get('dd')
        base_trail = float((cfg.get('risk', {}) or {}).get('trailing_retracement') or 0)
        logger.info(f'[scan] 锚点: 现行档 regime={base_regime_cfg} dd={base_dd} trail={base_trail}')
        anchor = {}
        t0 = time.time()
        for label in labels_scan:
            bt = run_one(masks, 'test', label, int(label[:-1]), base_gated, base_dd,
                         base_trail, common)
            anchor[label] = bt
        per_run = (time.time() - t0) / len(labels_scan)
        logger.info(f'[scan] 锚点复现: ' + '  '.join(
            f'{l}: 夏普{anchor[l]["sharpe"]} 回撤{anchor[l]["max_dd"]:.1%} '
            f'收益{anchor[l]["total_return"]:.1%}' for l in labels_scan))

        # 网格规模决策：预算内跑全网格，超限降级
        quick = args.quick
        regimes = REGIME_GRID_QUICK if quick else REGIME_GRID_FULL
        dds = DD_GRID_QUICK if quick else DD_GRID_FULL
        trails = TRAIL_GRID_QUICK if quick else TRAIL_GRID_FULL
        est = len(regimes) * len(dds) * len(trails) * len(labels_scan) * per_run / 60
        if not quick and est > args.max_minutes:
            logger.warning(f'[scan] 全网格预估 {est:.0f} 分钟超预算 {args.max_minutes}，自动降级粗网格')
            quick = True
            regimes, dds, trails = REGIME_GRID_QUICK, DD_GRID_QUICK, TRAIL_GRID_QUICK
        combos = len(regimes) * len(dds) * len(trails)
        logger.info(f'[scan] 网格: {len(regimes)} regime × {len(dds)} 熔断 × {len(trails)} trailing '
                    f'= {combos} 组合 × {len(labels_scan)} 周期，单次 {per_run:.1f}s，预估 {est:.0f} 分钟')

        # regime 日期集只按 regime 配置算一次
        # 注意每次读库后 commit：psycopg2 长事务会一直持有表锁（idle in
        # transaction），把后端启动的 ALTER TABLE 卡在锁队列里（2026-09-14 撞过）
        gated_cache = {}
        for rname, rcfg in regimes:
            gated_cache[rname] = (compute_regime_gates(db, masks['test'][0]['trade_date'].unique(),
                                                       rcfg) if rcfg else set())
            db.commit()

        rows = []
        done = 0
        t_start = time.time()
        for rname, _rc in regimes:
            gated = gated_cache[rname]
            for dd in dds:
                for trail in trails:
                    for label in labels_scan:
                        bt = run_one(masks, 'test', label, int(label[:-1]), gated, dd,
                                     trail if trail else None, common)
                        rows.append({
                            'regime': rname, 'dd_gate': dd, 'trailing': trail, 'label': label,
                            'sharpe': bt['sharpe'], 'max_dd': bt['max_dd'],
                            'total_return': bt['total_return'], 'annual': _annualize(bt),
                            'win_rate': bt['win_rate'], 'trades': bt['total_trades'],
                        })
                    done += 1
                    if done % 10 == 0:
                        db.commit()   # 缩短空闲事务：纯计算循环 1 小时不发 SQL，长事务既持锁又怕对端重启
                        el = (time.time() - t_start) / 60
                        logger.info(f'[scan] 进度 {done}/{combos} 组合 ({el:.1f} 分钟)')

        # ── 组合级汇总：按 5d/10d 两周期聚合排前沿 ──
        from collections import defaultdict
        by_combo = defaultdict(dict)
        for r in rows:
            by_combo[(r['regime'], str(r['dd_gate']), str(r['trailing']))][r['label']] = r

        def combo_score(ls):
            shs = [ls[l]['sharpe'] for l in labels_scan]
            dds_ = [abs(ls[l]['max_dd']) for l in labels_scan]
            anns = [ls[l]['annual'] for l in labels_scan]
            return (float(np.mean(shs)), float(np.max(dds_)), float(np.mean(anns)))

        ranked = sorted(by_combo.items(), key=lambda kv: combo_score(kv[1])[0], reverse=True)
        print(f'\n══ {args.version} 闸门前沿（test 段，{combos} 组合，干净数据基线）══')
        print(f'{"regime":>10} {"熔断":>5} {"trail":>5} │ {"5d夏普":>7} {"5d回撤":>7} {"5d年化":>7} '
              f'│ {"10d夏普":>7} {"10d回撤":>7} {"10d年化":>7} │ {"均夏普":>6} {"最差回撤":>7}')
        for (regime, dd, trail), ls in ranked[:20]:
            s5, s10 = ls.get('5d', {}), ls.get('10d', {})
            m_sh, m_dd, _ = combo_score(ls)
            mark = ' ←F1' if (regime == 'ma20s2(F1)' and dd == str(base_dd)
                              and trail == str(float(base_trail))) else ''
            print(f'{regime:>10} {dd:>5} {trail:>5} │ {s5.get("sharpe", 0):>7} {s5.get("max_dd", 0):>7.1%} '
                  f'{s5.get("annual", 0):>7.1%} │ {s10.get("sharpe", 0):>7} {s10.get("max_dd", 0):>7.1%} '
                  f'{s10.get("annual", 0):>7.1%} │ {m_sh:>6.2f} {m_dd:>7.1%}{mark}')

        # CSV 先落盘再做复核：复核段要查库，DB 若中途重启（2026-09-14 撞过容器级
        # 重启丢过整轮结果），网格成果已经保住
        out = args.out or f'data/scan_gate_{args.version}_{_date.today():%Y%m%d_%H%M}.csv'
        with open(out, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        logger.info(f'[scan] 网格结果已写 {len(rows)} 行 → {out}')

        # ── 前 3 名复核：补 20d + val 段（过拟合对照）。用新连接：主连接跑完
        # 网格可能已失效（长空闲 + 对端重启容忍度差）──
        try:
            db.close()
        except Exception:
            pass
        from app.db.connection import get_sync_db as _gdb
        db = _gdb()
        print('\n── 前 3 名复核（20d 周期 + val 段对照）──')
        for (regime, dd, trail), ls in ranked[:3]:
            dd_v, trail_v = (None if dd == 'None' else float(dd)), float(trail)
            gated_t = gated_cache[regime]
            rcfg = next(c for n, c in regimes if n == regime)
            gated_v = (compute_regime_gates(db, masks['val'][0]['trade_date'].unique(), rcfg)
                       if rcfg else set())
            parts = []
            for mname in ('test', 'val'):
                for label in labels_scan + (['20d'] if '20d' in models else []):
                    bt = run_one(masks, mname, label, int(label[:-1]),
                                 gated_t if mname == 'test' else gated_v, dd_v,
                                 trail_v if trail_v else None, common)
                    parts.append(f'{mname}-{label}: 夏普{bt["sharpe"]} 回撤{bt["max_dd"]:.1%}')
            print(f'  [{regime} dd={dd} trail={trail}]  ' + '  '.join(parts))

        print(f'\n完成：{len(rows)} 行结果在 {out}（试运行，不改任何生产配置）')
    finally:
        db.close()


if __name__ == '__main__':
    main()
