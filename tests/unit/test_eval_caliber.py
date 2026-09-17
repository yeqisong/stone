"""评估口径补齐单元测试：exec_lag 成交时点 + 成本记账。"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.pipeline import run_training_backtest


def _synthetic(n_days=6, cash=2_000_000):
    """2 只股票 × n 天：A 分数高且持续上涨，B 分数低。生成引擎入参。"""
    dates, codes, preds, trues, closes, vols = [], [], [], [], [], []
    for i in range(n_days):
        d = f'2026-01-{5+i:02d}'
        for code, pred in (('600000', 0.5), ('000001', -0.01)):
            dates.append(d)
            codes.append(code)
            preds.append(pred)
            trues.append(0.1)
            closes.append(10.0 + i * 0.1 if code == '600000' else 10.0)
            vols.append(10_000_000)
    return (np.array(dates), np.array(codes), np.array(preds, dtype=float),
            np.array(trues), np.array(closes, dtype=float), np.array(vols, dtype=float), cash)


class TestCostAccounting(unittest.TestCase):
    """total_cost = 全部成交买入佣金 + 全部卖出(佣金+印花税+冲击)，与逐笔分录对账。"""

    def test_total_cost_matches_trade_ledger(self):
        dates, codes, preds, trues, closes, vols, cash = _synthetic()
        bt = run_training_backtest(trues, preds, dates, codes, closes, vols, hold_days=3,
                                   max_pos=2, initial_cash=cash)
        buys = [t for t in bt['trades'] if t['action'] == 'BUY']
        sells = [t for t in bt['trades'] if t['action'] == 'SELL']
        self.assertTrue(buys, '应有买入')
        expected = (sum(t['commission'] for t in buys)
                    + sum(t['commission_tax'] for t in sells))
        self.assertAlmostEqual(bt['total_cost'], round(expected, 2), places=1)
        self.assertGreater(bt['cost_pct'], 0)
        self.assertLess(bt['cost_pct'], 0.05)   # 6 天小样本，成本拖累不可能大

    def test_skipped_buys_charge_no_cost(self):
        # 回归保护：资金不足被跳过的买单不得计入成本（曾虚增 1499/百万本金）
        dates, codes, preds, trues, closes, vols, _ = _synthetic()
        bt = run_training_backtest(trues, preds, dates, codes, closes, vols, hold_days=3,
                                   max_pos=1, initial_cash=1_000_000)  # 前两日买单必然超现金属跳过
        buys = [t for t in bt['trades'] if t['action'] == 'BUY']
        sells = [t for t in bt['trades'] if t['action'] == 'SELL']
        expected = (sum(t['commission'] for t in buys)
                    + sum(t['commission_tax'] for t in sells))
        self.assertAlmostEqual(bt['total_cost'], round(expected, 2), places=1)

    def test_zero_cost_when_no_trades(self):
        # 全负预测 → 无买入 → 成本为 0
        dates, codes, preds, trues, closes, vols, cash = _synthetic()
        bt = run_training_backtest(trues, preds - 1.0, dates, codes, closes, vols, hold_days=3,
                                   initial_cash=cash)
        self.assertEqual(bt['total_cost'], 0)
        self.assertEqual(bt['cost_pct'], 0)


class TestExecLag(unittest.TestCase):
    """exec_lag=1：候选分数后移一天——首日无分数不买，首笔买入比 T+0 晚一天。"""

    def test_first_buy_shifted_one_day(self):
        dates, codes, preds, trues, closes, vols, cash = _synthetic()
        common = dict(y_true=trues, dates=dates, codes=codes, close_prices=closes,
                      volumes=vols, hold_days=10, max_pos=2, initial_cash=cash)
        bt0 = run_training_backtest(y_pred=preds, **common)
        bt1 = run_training_backtest(y_pred=preds, exec_lag=1, **common)
        first_buy0 = min(t['date'] for t in bt0['trades'] if t['action'] == 'BUY')
        first_buy1 = min(t['date'] for t in bt1['trades'] if t['action'] == 'BUY')
        self.assertEqual(first_buy0, '2026-01-05')        # 信号日收盘即买
        self.assertEqual(first_buy1, '2026-01-06')        # 次日收盘才买（首日分数 NaN）

    def test_exec_lag_default_is_zero(self):
        # 不传 exec_lag 时行为与既有结果完全一致（回归保护）
        dates, codes, preds, trues, closes, vols, cash = _synthetic()
        a = run_training_backtest(trues, preds, dates, codes, closes, vols, hold_days=10,
                                  initial_cash=cash)
        b = run_training_backtest(trues, preds, dates, codes, closes, vols, hold_days=10,
                                  exec_lag=0, initial_cash=cash)
        self.assertEqual(a['total_trades'], b['total_trades'])
        self.assertEqual(a['equity_curve'], b['equity_curve'])

    def test_lag_price_is_execution_day_price(self):
        # 次日成交价必须是次日价：A 首日 10 元、次日 11 元，T+1 买入价应为 11 附近
        dates, codes, preds, trues, closes, vols, cash = _synthetic(n_days=4)
        closes = np.array([10.0, 11.0, 11.0, 11.0, 10.0, 10.0, 10.0, 10.0])
        bt = run_training_backtest(trues, preds, dates, codes, closes, vols, hold_days=10,
                                   exec_lag=1, max_pos=2, initial_cash=cash)
        first_buy = next(t for t in bt['trades'] if t['action'] == 'BUY')
        self.assertEqual(first_buy['date'], '2026-01-06')
        self.assertAlmostEqual(first_buy['price'], 11.0 * (1 + 0.001 / 2), places=2)


if __name__ == '__main__':
    unittest.main()
