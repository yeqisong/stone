"""回测引擎单元测试。"""
import pytest
import pandas as pd
import numpy as np
from strategy.backtest import BacktestEngine, BacktestResult, EvaluationReport


class TestSplitWindows:
    def test_basic_split(self):
        engine = BacktestEngine(train_window=50, test_window=10, step=10)
        dates = [f"2021-{i:03d}" for i in range(1, 150)]  # 150 trading days
        windows = engine.split_windows(dates)
        assert len(windows) > 0

    def test_split_all_windows_ordered(self):
        engine = BacktestEngine(train_window=50, test_window=10, step=10)
        dates = [f"2021-{i:03d}" for i in range(1, 200)]
        windows = engine.split_windows(dates)
        for i in range(1, len(windows)):
            # 后续窗口的 test_start 应在前一个窗口之后
            assert windows[i][2] > windows[i-1][2]

    def test_no_overlap_train_test(self):
        engine = BacktestEngine(train_window=50, test_window=10, step=10)
        dates = [f"2021-{i:03d}" for i in range(1, 200)]
        windows = engine.split_windows(dates)
        for w in windows:
            assert w[1] < w[2]  # train_end < test_start


class TestEvaluation:
    def test_sharpe_positive(self):
        returns = [0.01, 0.02, -0.01, 0.03, 0.01]
        s = BacktestEngine._sharpe(returns)
        assert s > 0

    def test_sharpe_zero_std(self):
        returns = [0.01, 0.01, 0.01]
        s = BacktestEngine._sharpe(returns)
        assert s == 0.0

    def test_max_drawdown(self):
        returns = [0.01, 0.02, -0.05, -0.03, 0.01]
        dd = BacktestEngine._max_drawdown(returns)
        assert dd > 0.05  # 回撤应 > 5%

    def test_empty_returns(self):
        assert BacktestEngine._sharpe([]) == 0.0
        assert BacktestEngine._max_drawdown([]) == 0.0


class TestWalkForward:
    def test_run_produces_report(self):
        np.random.seed(42)
        dates = pd.date_range('2021-01-01', periods=500, freq='B')
        df = pd.DataFrame({
            'trade_date': dates,
            'stock_code': '000001',
            'upper': np.random.randn(500) + 20,
            'mid': np.random.randn(500) + 18,
            'lower': np.random.randn(500) + 16,
            'pct_b': np.random.rand(500),
            'dif': np.random.randn(500) * 0.1,
            'dea': np.random.randn(500) * 0.1,
            'hist': np.random.randn(500) * 0.05,
            'rsi': np.random.rand(500) * 100,
            'atr': np.abs(np.random.randn(500)),
            'ma5': np.random.randn(500) + 18,
            'ma20': np.random.randn(500) + 18,
            'vol_ratio': np.random.rand(500) + 1,
        })

        def signal_fn(train_df, test_df):
            signals = []
            for _, row in test_df.iterrows():
                if row['pct_b'] < 0.2 and row['rsi'] < 35:
                    signals.append({'return': float(row['pct_b']) * 0.05})
            return signals

        engine = BacktestEngine(train_window=200, test_window=30, step=30)
        report = engine.run(df, signal_fn)
        assert isinstance(report, EvaluationReport)
        assert len(report.fold_results) > 0

    def test_report_has_metrics(self):
        report = EvaluationReport(sharpe=2.15, win_rate=0.58, max_drawdown=0.12, annual_return=0.22)
        assert report.sharpe == 2.15
        assert report.win_rate == 0.58
