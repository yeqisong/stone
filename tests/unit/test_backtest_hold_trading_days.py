"""回测持有期按交易日计，且卖出当日不得回补同一代码。"""
import unittest

import numpy as np
import pandas as pd

from scripts.pipeline import run_training_backtest


class TestBacktestHoldTradingDays(unittest.TestCase):
    def test_hold_days_are_trading_days_and_no_same_day_reentry(self):
        n = 30
        dates = pd.bdate_range("2026-01-05", periods=n)
        result = run_training_backtest(
            np.zeros(n), np.ones(n), dates, ["600000"] * n,
            100.0 * (1.001 ** np.arange(n)), np.full(n, 1e9),
            hold_days=10, initial_cash=1_000_000, max_pos=1,
            stop_loss=0.5, take_profit=0.5,
            commission=0, stamp_tax=0, slippage=0)
        trades = result["trades"]
        sells = [t for t in trades if t["action"] == "SELL"]
        self.assertTrue(sells)
        self.assertEqual(sells[0]["hold_days"], 10)
        self.assertEqual(sells[0]["date"], str(dates[10])[:10])
        # 卖出当日不能出现同代码买入（否则回测靠日内回补刷换手）
        buys = {(t["date"], t["code"]) for t in trades if t["action"] == "BUY"}
        for t in sells:
            self.assertNotIn((t["date"], t["code"]), buys)


if __name__ == "__main__":
    unittest.main()
