#!/usr/bin/env python
"""对**已训练**模型重跑评估（不重训），重算并覆盖评估存档。

为什么需要它：训练期的评估回测与训练在同一次运行里完成，引擎或数据口径一变，
要拿到干净数字就得重训（50 trials 约 40 分钟）。本脚本直接复用
`prepare_model_frame` / `build_targets` / `run_training_backtest` / `pred_score`
这四个模块级实现（与训练节点同一份代码），用落库的模型 pkl 重算 val/test 回测，
产出与训练期同结构的评估存档。

覆盖写入：backtest_records（含 label_detail / equity_tail）、backtest_trades、
backtest_daily_records（逐日净值 + 持仓快照）、model_versions 的
sharpe/win_rate/max_drawdown/annual_return/evaluation_report。

注意：结果反映**当前**的 feature_values 与行情数据口径——若期间重算过特征或修复过
数据，数字会与历史存档不同（这正是重算的目的）。默认只算不写，加 --apply 才落库。

用法：
    venv/bin/python scripts/eval_version.py v15.0
    venv/bin/python scripts/eval_version.py v15.0 --apply
"""
import argparse
import json as _json
import os
import pickle
import sys
from datetime import date as _date, timedelta as _td

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from loguru import logger
from sqlalchemy import text

from app.db.connection import get_sync_db
from scripts.pipeline import (build_feature_wide_table, compute_regime_gates,
                              pred_score, prepare_model_frame, run_training_backtest,
                              val_score)

TARGETS = [('5d', 'target_5d', 5), ('10d', 'target_10d', 10), ('20d', 'target_20d', 20)]


def _annualize(bt):
    """区间总收益 → 252 交易日年化（与训练期同式）。"""
    days = len(bt.get('equity_curve', []))
    if days >= 2 and bt.get('total_return', -1) > -1:
        return (1 + bt['total_return']) ** (252 / days) - 1
    return 0


def evaluate(db, ver, apply=False):
    row = db.execute(text("SELECT config, best_params FROM model_versions WHERE version=:v"),
                     {"v": ver}).fetchone()
    if not row:
        raise SystemExit(f'版本不存在: {ver}')
    cfg = _json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
    bp = _json.loads(row[1]) if isinstance(row[1], str) else (row[1] or {})
    feature_names = cfg.get('feature_names') or cfg.get('features') or []
    if not feature_names:
        raise SystemExit('该版本未配置 feature_names，无法评估')

    models = {}
    for label, _, _ in TARGETS:
        path = (bp.get(label) or {}).get('model_path')
        if path and os.path.exists(path):
            with open(path, 'rb') as f:
                models[label] = pickle.load(f)
    if not models:
        raise SystemExit(f'未找到模型文件（best_params[*].model_path），无法评估: {ver}')
    logger.info(f'[eval] {ver} 载入模型: {sorted(models)}')

    data_start = cfg.get('train_start', '2024-01-01')
    end_date = (_date.today() - _td(days=2)).isoformat()
    df = build_feature_wide_table(db, feature_names, data_start, end_date, 'stock')
    if len(df) < 5000:
        raise SystemExit(f'特征数据不足({len(df)} 行)，请先执行特征计算')
    df, FEATURES = prepare_model_frame(db, df, cfg, feature_names, data_start, end_date)
    logger.info(f'[eval] 面板 {len(df):,} 行 / {len(FEATURES)} 特征 → 标签构建完成')

    dates = sorted(df['trade_date'].unique())
    n = len(dates)
    train_cut, test_cut = dates[int(n * 0.6)], dates[int(n * 0.8)]
    val_mask = (df['trade_date'] >= train_cut) & (df['trade_date'] < test_cut)
    test_mask = df['trade_date'] >= test_cut
    logger.info(f'[eval] val {str(train_cut)[:10]}~{str(test_cut)[:10]} / test {str(test_cut)[:10]}~')

    # 空仓闸门 + 组合熔断 + 移动止盈（与训练评估、实盘信号同一规则）
    regime_cfg = cfg.get('regime') or {}
    pdd_gate = (cfg.get('portfolio_gate') or {}).get('dd')
    trail_val = float((cfg.get('risk') or {}).get('trailing_retracement') or 0)
    regime_gates = compute_regime_gates(db, df['trade_date'].unique(), regime_cfg)
    logger.info(f'[eval] 风险日 {len(regime_gates)} 个 / 组合熔断 {pdd_gate} / 移动止盈 {trail_val}')

    sl = cfg.get('risk', {}).get('stop_loss_pct', 8) / 100.0
    common = dict(initial_cash=cfg.get('initial_cash', 1_000_000),
                  max_pos=cfg.get('max_positions', 5), bt_ver=ver,
                  stop_loss=sl, take_profit=sl * 2,
                  commission=cfg.get('commission', 0.00025),
                  stamp_tax=cfg.get('stamp_tax', 0.001),
                  slippage=cfg.get('slippage', 0.001),
                  gated_dates=regime_gates, dd_gate=pdd_gate, trailing=trail_val)

    def run_all(mask, mask_name):
        res, sub = {}, df[mask]
        for label, tname, hdays in TARGETS:
            if label not in models:
                continue
            y_pred = pred_score(models[label], sub[FEATURES].values)
            bt = run_training_backtest(sub[tname].values, y_pred, sub['trade_date'].values,
                                       sub['stock_code'].values, sub['close'].values,
                                       sub['volume'].values, hdays, bt_label=label,
                                       limit_up=sub['_limit_up'].values,
                                       limit_down=sub['_limit_down'].values, **common)
            res[label] = bt
            logger.info(f'[eval] {label} {mask_name}: sharpe={bt["sharpe"]} dd={bt["max_dd"]} '
                        f'ret={bt["total_return"]:.1%} 卖出{bt["total_trades"]}笔')
        return res

    val_results = run_all(val_mask, 'val')
    test_results = run_all(test_mask, 'test')
    if not test_results:
        raise SystemExit('回测无结果')

    labels = [l for l in ['5d', '10d', '20d'] if l in test_results]
    avg_sharpe = float(np.mean([test_results[l]['sharpe'] for l in labels]))
    avg_win = float(np.mean([test_results[l]['win_rate'] for l in labels]))
    max_dd_avg = float(np.mean([test_results[l]['max_dd'] for l in labels]))
    annual_return = float(np.mean([_annualize(test_results[l]) for l in labels]))
    avg_total_return = float(np.mean([test_results[l]['total_return'] for l in labels]))
    initial_cash = common['initial_cash']
    all_trades = []
    for bt in test_results.values():
        all_trades.extend(bt.get('trades', []))
    win_trades = len([t for t in all_trades if t.get('action') == 'SELL' and (t.get('pnl') or 0) > 0])
    equity_tail = test_results.get('10d', test_results[labels[0]]).get('equity_curve', [])

    # 基准：沪深300 同期
    benchmark_return = 0.0
    td_test = df[test_mask]['trade_date']
    bm = db.execute(text(
        "SELECT close FROM index_daily_quote WHERE index_code='000300' "
        "AND trade_date BETWEEN :s AND :e ORDER BY trade_date"),
        {"s": str(td_test.min())[:10], "e": str(td_test.max())[:10]}).fetchall()
    if len(bm) >= 2 and float(bm[0][0]) > 0:
        benchmark_return = float(bm[-1][0]) / float(bm[0][0]) - 1

    # 过拟合对比：val/test 的 R²（按训练目标口径）与回测夏普
    obj = cfg.get('train_objective', 'regression')
    val_r2s, test_r2s = [], []
    for l in labels:
        _, tname, _ = next(t for t in TARGETS if t[0] == l)
        Xv, yv = df[val_mask][FEATURES].values, df[val_mask][tname]
        Xt, yt = df[test_mask][FEATURES].values, df[test_mask][tname]
        val_r2s.append(val_score(models[l], Xv, yv, obj))
        test_r2s.append(val_score(models[l], Xt, yt, obj))
    val_r2_avg = float(np.mean(val_r2s)) if val_r2s else 0
    test_r2_avg = float(np.mean(test_r2s)) if test_r2s else 0
    val_sharpe_avg = float(np.mean([val_results[l]['sharpe'] for l in val_results])) if val_results else 0

    # 质量指标（SELL 逐笔盈亏）
    sell_pnls = [t.get('pnl', 0) for t in all_trades
                 if t.get('action') == 'SELL' and t.get('pnl') is not None]
    wins = [p for p in sell_pnls if p > 0]
    losses = [abs(p) for p in sell_pnls if p < 0]
    profit_factor = sum(wins) / sum(losses) if losses else (999 if wins else 0)
    top3 = sum(sorted(wins, reverse=True)[:3]) / sum(wins) * 100 if wins else 0

    # 与训练期同结构：evaluation_report 供 UI 读取；detail 存 label_detail/equity_tail
    report = {
        'labels': labels,
        'label_detail': {l: {'sharpe': test_results[l]['sharpe'], 'win_rate': test_results[l]['win_rate'],
                             'total_return': test_results[l]['total_return'], 'max_dd': test_results[l]['max_dd']}
                         for l in labels},
        'equity_tail': equity_tail,
        'benchmark_return': round(benchmark_return, 4),
        'evaluation_report': {
            'trials': [],   # 复评不重训，无 trial 记录
            'trades': all_trades,
            'trade_count': len(all_trades),
            'top3_concentration': round(top3, 1),
            'val_r2': round(val_r2_avg, 4),
            'test_r2': round(test_r2_avg, 4),
            'val_sharpe': round(val_sharpe_avg, 4),
            'test_sharpe': round(avg_sharpe, 4),
            'overfit_gap': round(val_sharpe_avg - avg_sharpe, 4),
            'profit_factor': round(profit_factor, 2),
            'avg_win': round(float(np.mean(wins)), 2) if wins else 0,
            'avg_loss': round(float(np.mean(losses)), 2) if losses else 0,
            'benchmark_return': round(benchmark_return, 4),
            'sharpe_5d': test_results.get('5d', {}).get('sharpe', 0),
            'sharpe_10d': test_results.get('10d', {}).get('sharpe', 0),
            'sharpe_20d': test_results.get('20d', {}).get('sharpe', 0),
            'win_rate_5d': test_results.get('5d', {}).get('win_rate', 0),
            'win_rate_10d': test_results.get('10d', {}).get('win_rate', 0),
            'win_rate_20d': test_results.get('20d', {}).get('win_rate', 0),
        },
    }
    print(f'\n══ {ver} 复评结果（当前数据口径）══')
    for l in labels:
        d = test_results[l]
        print(f'  {l:>3}: 夏普 {d["sharpe"]:>6}  回撤 {d["max_dd"]:>7.1%}  '
              f'收益 {d["total_return"]:>7.1%}  胜率 {d["win_rate"]:>5.1%}  卖出 {d["total_trades"]:>4} 笔')
    print(f'  合计: 夏普 {avg_sharpe:.3f}  回撤 {abs(max_dd_avg):.1%}  年化 {annual_return:.1%}  '
          f'基准 {benchmark_return:.1%}  逐日记录 {len(equity_tail)} 点')
    if val_results:
        print('  val（OOS 对照，写库不落 val 明细）: ' + '  '.join(
            f'{l}=夏普{d["sharpe"]} 收益{d["total_return"]:.0%} 卖出{d["total_trades"]}笔'
            for l, d in val_results.items()))

    if not apply:
        print('\n（试运行：未写库。加 --apply 覆盖 backtest_records / model_versions / 逐日记录）')
        return report

    test_start, test_end = str(td_test.min())[:10], str(td_test.max())[:10]
    db.execute(text("DELETE FROM backtest_records WHERE version=:v"), {"v": ver})
    db.execute(text("DELETE FROM backtest_trades WHERE version=:v"), {"v": ver})
    db.execute(text("DELETE FROM backtest_daily_records WHERE version=:v"), {"v": ver})
    db.execute(text("""
        INSERT INTO backtest_records (version, start_date, end_date, initial_cash, final_equity,
            sharpe_ratio, win_rate, max_drawdown, annual_return, total_trades, winning_trades, detail)
        VALUES (:v,:s,:e,:ic,:fe,:sh,:wr,:md,:ar,:tt,:wt,:dt)
    """), {"v": ver, "s": test_start, "e": test_end, "ic": initial_cash,
           "fe": round(initial_cash * (1 + avg_total_return), 2),
           "sh": round(avg_sharpe, 4), "wr": round(avg_win, 4),
           "md": round(abs(max_dd_avg), 4), "ar": round(annual_return, 4),
           "tt": len(all_trades), "wt": win_trades,
           "dt": _json.dumps({k: report[k] for k in ('labels', 'label_detail', 'benchmark_return', 'equity_tail')},
                             ensure_ascii=False)})
    for t in all_trades:
        is_buy = t.get('action') == 'BUY'
        db.execute(text("""
            INSERT INTO backtest_trades (version, stock_code, trade_date, direction, price, shares,
                cost, profit_loss, equity_before, equity_after, reason)
            VALUES (:v,:c,:d,:dir,:p,:sh,:cost,:pnl,:eb,:ea,:rs)
        """), {"v": ver, "c": t.get('code', ''), "d": t.get('date'), "dir": t.get('action', 'BUY'),
               "p": t.get('price', 0), "sh": t.get('shares', 0),
               "cost": t.get('amount', 0) if is_buy else t.get('commission_tax', 0),
               "pnl": None if is_buy else t.get('pnl'),
               "eb": t.get('cash_before', 0), "ea": t.get('market_value', 0),
               "rs": (t.get('signal_reason') if is_buy else t.get('reason')) or ''})
    for label, bt in test_results.items():
        for rec in bt.get('daily', []):
            db.execute(text("""
                INSERT INTO backtest_daily_records
                (version, label, trade_date, equity, cash, position_value, n_positions, holdings)
                VALUES (:v,:l,:d,:eq,:ca,:pv,:n,:h)
                ON CONFLICT (version, label, trade_date) DO NOTHING
            """), {"v": ver, "l": label, "d": rec['date'], "eq": rec['equity'], "ca": rec['cash'],
                   "pv": rec['position_value'], "n": rec['n_positions'],
                   "h": _json.dumps(rec['holdings'], ensure_ascii=False)})
    db.execute(text("""
        UPDATE model_versions SET sharpe=:sh, win_rate=:wr, max_drawdown=:md, annual_return=:ar,
               evaluation_report=:rep WHERE version=:v
    """), {"v": ver, "sh": round(avg_sharpe, 4), "wr": round(avg_win, 4),
           "md": round(abs(max_dd_avg), 4), "ar": round(annual_return, 4),
           "rep": _json.dumps(report['evaluation_report'], ensure_ascii=False)})
    db.commit()
    print(f'\n已覆盖写库：backtest_records / backtest_trades / backtest_daily_records / model_versions({ver})')
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('version', help='模型版本，如 v15.0')
    ap.add_argument('--apply', action='store_true', help='覆盖写库（默认只算不写）')
    args = ap.parse_args()
    db = get_sync_db()
    try:
        evaluate(db, args.version, apply=args.apply)
    finally:
        db.close()


if __name__ == '__main__':
    main()
