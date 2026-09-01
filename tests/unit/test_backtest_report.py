"""绩效报告单测（design/05 M4 验收：与手工计算对照）。

risk_analysis 逐行对照 Qlib evaluate.py:26-93 语义（sum/product 两模式、N=238）。
"""
import numpy as np
import pandas as pd
import pytest

from strategy.backtest.report import alpha_beta, performance_report, risk_analysis


class TestRiskAnalysisSum:
    """sum 模式（Qlib 默认）：算术累计。"""

    def test_constant_positive_return(self):
        """每日 +0.001：mean≈0.0010005，年化=mean×238。"""
        r = np.full(100, 0.001) + np.linspace(0, 1e-6, 100)  # 加微扰避免 std=0
        out = risk_analysis(r, N=238, mode='sum')
        assert out['mean'] == pytest.approx(0.001 + 5e-7, abs=1e-6)
        # annualized 由未舍入 mean 计算，容差需覆盖 mean 的 6 位舍入传播（5e-7×238）
        assert out['annualized_return'] == pytest.approx(out['mean'] * 238, abs=2e-4)

    def test_ir_manual(self):
        """IR = mean/std×√238 手工对照。"""
        rng = np.random.default_rng(7)
        r = rng.normal(0.0005, 0.01, 200)
        out = risk_analysis(r, N=238, mode='sum')
        s = np.asarray(r)
        expect_ir = s.mean() / s.std(ddof=1) * np.sqrt(238)
        assert out['information_ratio'] == pytest.approx(expect_ir, abs=1e-3)

    def test_max_drawdown_cumsum(self):
        """max_dd = 累计收益曲线峰谷差（sum 模式为收益和的回撤，非净值比例）。"""
        r = [0.01, 0.01, -0.03, 0.01]
        out = risk_analysis(r, N=238, mode='sum')
        cs = np.cumsum(r)
        expect = (cs - np.maximum.accumulate(cs)).min()
        assert out['max_drawdown'] == pytest.approx(expect)

    def test_qlib_exact_semantics(self):
        """逐行对照 Qlib：mean/std(ddof=1)/annualized=mean*N。"""
        r = pd.Series([0.01, -0.005, 0.008, 0.002, -0.001])
        out = risk_analysis(r, N=238, mode='sum')
        assert out['mean'] == pytest.approx(float(r.mean()), abs=1e-6)
        assert out['std'] == pytest.approx(float(r.std(ddof=1)), abs=1e-6)
        assert out['annualized_return'] == pytest.approx(float(r.mean()) * 238, abs=1e-4)


class TestRiskAnalysisProduct:
    """product 模式：几何累计（复利）。"""

    def test_geometric_annualized(self):
        """年化 = (1+累计收益)^(N/len)-1 手工对照。"""
        r = [0.01, 0.02, -0.015, 0.008, 0.012, -0.003]
        out = risk_analysis(r, N=238, mode='product')
        cum = float(np.prod(1 + np.asarray(r)) - 1)
        expect = (1 + cum) ** (238 / len(r)) - 1
        assert out['annualized_return'] == pytest.approx(expect, abs=1e-4)

    def test_max_drawdown_ratio(self):
        """product 模式 max_dd = 净值比例回撤（与 sum 模式的收益和回撤不同）。"""
        r = [0.10, -0.20, 0.05]
        out = risk_analysis(r, N=238, mode='product')
        cum = np.cumprod(1 + np.asarray(r))
        expect = (cum / np.maximum.accumulate(cum) - 1).min()
        assert out['max_drawdown'] == pytest.approx(expect, abs=1e-6)

    def test_std_log_returns(self):
        """product 模式 std 用对数收益。"""
        r = [0.01, -0.005, 0.008, 0.002]
        out = risk_analysis(r, N=238, mode='product')
        expect = np.log(1 + np.asarray(r)).std(ddof=1)
        assert out['std'] == pytest.approx(expect, abs=1e-6)


class TestAlphaBeta:
    def test_regression_exact(self):
        """r = 0.5×rb + 0.001 → beta=0.5，alpha_daily=0.001，年化=0.238。"""
        rng = np.random.default_rng(11)
        rb = rng.normal(0, 0.01, 150)
        r = 0.5 * rb + 0.001
        out = alpha_beta(r, rb)
        assert out['beta'] == pytest.approx(0.5, abs=1e-6)
        assert out['alpha_daily'] == pytest.approx(0.001, abs=1e-9)
        assert out['alpha_annualized'] == pytest.approx(0.238, abs=1e-3)

    def test_constant_benchmark(self):
        """基准无波动（var≈0）→ beta=0 不抛异常。"""
        out = alpha_beta([0.01, 0.02, 0.01], [0.001, 0.001, 0.001])
        assert out['beta'] == 0.0


class TestPerformanceReport:
    def _records(self, eq):
        from strategy.backtest.models import DailyRecord
        rets = [1.0] + [eq[i] / eq[i - 1] for i in range(1, len(eq))]
        recs = []
        for i, acc in enumerate(eq):
            recs.append(DailyRecord(trade_date=f'2026-01-{i+1:02d}', account=acc,
                                    cash=0, position_value=acc, turnover=acc * 0.08, cost=0))
        return recs

    def test_full_report(self):
        """净值 + 基准 + 换手 + 成交 → 各块齐全，数值手工对照。"""
        eq = [100, 101, 99, 102, 103]
        bench = [3000, 3015, 3000, 3045, 3060]  # 基准稳步 +2%/期
        recs = self._records(eq)
        trades = [{'pnl': 100}, {'pnl': -50}, {'pnl': 30}]
        out = performance_report(eq, bench, recs, trades)
        assert out['n_days'] == 4
        # sum 模式 IR 手工对照
        rets = np.diff(eq) / np.asarray(eq[:-1], dtype=float)
        assert out['sum']['information_ratio'] == pytest.approx(
            rets.mean() / rets.std(ddof=1) * np.sqrt(238), abs=1e-3)
        # excess 年化 = mean(r−rb)×238
        brets = np.diff(bench) / np.asarray(bench[:-1], dtype=float)
        assert out['excess']['annualized_return'] == pytest.approx(
            float((rets - brets).mean()) * 238, abs=1e-3)
        # beta 手工对照
        expect_beta = np.cov(rets, brets, ddof=1)[0, 1] / np.var(brets, ddof=1)
        assert out['excess']['beta'] == pytest.approx(expect_beta, abs=1e-3)
        # 换手率：total = Σturnover；daily_avg = Σturnover/平均账户值/天数
        assert out['turnover']['total'] == pytest.approx(sum(r.turnover for r in recs), rel=1e-6)
        assert out['turnover']['daily_avg'] == pytest.approx(
            sum(r.turnover for r in recs) / np.mean([r.account for r in recs]) / len(recs), abs=1e-6)
        # 按单胜率（pnl>0 比例）2/3（4 位舍入）
        assert out['win_rate_pos'] == pytest.approx(2 / 3, abs=1e-3)

    def test_minimal(self):
        """无基准/无记录：只返回 sum/product 基础块。"""
        out = performance_report([100, 102, 101])
        assert 'sum' in out and 'product' in out
        assert 'excess' not in out and 'turnover' not in out

    def test_empty(self):
        """空净值不抛异常。"""
        out = performance_report([100])
        assert out['n_days'] == 0
