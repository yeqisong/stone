"""回测引擎 v2 单元测试 — 合成数据已知答案验证。

覆盖：逐日选股对齐（旧实现的核心 bug）、涨跌停买卖约束、T+1、
止损/止盈、trailing 峰值回撤、ideal/random 基线语义、随机基线可复现性。
"""
import numpy as np
import pandas as pd
import pytest

from scripts.pipeline import _simple_backtest


def _mk_df(price_paths):
    """price_paths: {code: [close, ...]} → 宽表结构 DataFrame（trade_date 为 ISO 字符串、按日期+代码排序）。"""
    n = max(len(p) for p in price_paths.values())
    dates = pd.bdate_range('2026-01-05', periods=n).strftime('%Y-%m-%d')
    rows = []
    for code, path in price_paths.items():
        for i, c in enumerate(path):
            rows.append((code, dates[i], float(c)))
    df = pd.DataFrame(rows, columns=['stock_code', 'trade_date', 'close'])
    df['volume'] = 1_000_000.0
    return df.sort_values(['trade_date', 'stock_code']).reset_index(drop=True)


def _val_range(df):
    return df['trade_date'].min(), df['trade_date'].max()


class TestPredMode:
    """打分模式（真模型预测由外部传入）。"""

    def test_daily_alignment_picks_top_each_day(self):
        """逐日对齐：第 1 天预测 A 最优、之后 B 最优，引擎必须从第 2 天起切换买 B。

        旧实现 y_pred[:len(candidates)] 永远读验证期头几行的预测，会一直买 A。
        """
        n = 10
        a = [10.0] * n
        b = [round(10.0 * 1.05 ** i, 4) for i in range(n)]  # B 每天 +5%
        df = _mk_df({'A': a, 'B': b})
        first_day = df['trade_date'].min()
        pred = pd.Series(
            np.where(df['trade_date'] == first_day,
                     np.where(df['stock_code'] == 'A', 2.0, 1.0),
                     np.where(df['stock_code'] == 'A', 1.0, 2.0)),
            index=df.index)
        bt = _simple_backtest(df, pred, *_val_range(df),
                              hold_days=1, stop_loss=0.5, take_profit=0.5, max_pos=1)
        assert bt['total_return'] > 0.10, f"未按日对齐预测选股: {bt}"
        assert bt['win_rate'] >= 0.8, f"B 段卖出应为几乎全胜: {bt}"

    def test_nonpositive_pred_never_buys(self):
        """预测全部 ≤0 时不买入（min_ret 门槛语义保留）。"""
        df = _mk_df({'A': [10.0] * 6})
        pred = pd.Series(-1.0, index=df.index)
        bt = _simple_backtest(df, pred, *_val_range(df), hold_days=5)
        assert bt['total_trades'] == 0 and bt['total_return'] == 0


class TestAshareConstraints:
    """A 股执行约束。"""

    def test_limit_up_blocks_buy(self):
        """B 第 3 天起连板涨停，评分再高也买不进。

        若涨停约束失效，引擎会在涨停价买入 B 吃到 +10%/天的虚增收益。
        """
        a = [10.0] * 8
        b = [10.0, 10.0, 11.0, 12.1, 13.31, 14.64, 16.10, 17.71]  # d3 起每日 +10% 涨停
        df = _mk_df({'A': a, 'B': b})
        # d1-d2 预测 A 最优（正常买入），d3 起 B 最优但被涨停挡住 → 只能继续买 A
        d2 = df['trade_date'].unique()[1]
        pred = pd.Series(
            np.where(df['trade_date'] <= d2,
                     np.where(df['stock_code'] == 'A', 2.0, 1.0),
                     np.where(df['stock_code'] == 'B', 2.0, 1.0)),
            index=df.index)
        bt = _simple_backtest(df, pred, *_val_range(df),
                              hold_days=1, stop_loss=0.5, take_profit=0.5, max_pos=1)
        assert bt['win_rate'] == 0.0, f"涨停股被买入: {bt}"
        assert bt['total_return'] < 0.0, f"涨停约束失效（A 平价往返只剩费用）: {bt}"

    def test_limit_down_blocks_sell(self):
        """跌停日止损不可执行：持仓被迫保留，反弹后脱离止损区 → 全程无成交。"""
        a = [10.0, 9.0, 9.6, 9.6, 9.6]  # d2 -10% 跌停触发止损但不可卖，d3 +6.7% 反弹
        df = _mk_df({'A': a})
        pred = pd.Series(1.0, index=df.index)
        bt = _simple_backtest(df, pred, *_val_range(df),
                              hold_days=10, stop_loss=0.05, take_profit=0.5)
        assert bt['total_trades'] == 0, f"跌停日被卖出: {bt}"

    def test_stop_loss_executes_next_day(self):
        """止损在次日按触发价执行（T+1）；-9.7% 未到跌停边界可正常卖出。"""
        a = [10.0, 9.03, 9.03]  # d2 -9.7%
        df = _mk_df({'A': a})
        pred = pd.Series(1.0, index=df.index)
        bt = _simple_backtest(df, pred, *_val_range(df),
                              hold_days=10, stop_loss=0.08, take_profit=0.5)
        assert bt['total_trades'] == 1 and bt['win_rate'] == 0.0
        # 仓位 = equity/5 ≈ 20% 资金，-8% 止损 → 组合约 -1.6%
        assert -0.03 < bt['total_return'] < -0.01, f"止损未按触发价执行: {bt}"

    def test_trailing_stop_triggers(self):
        """峰值回撤 trailing 触发卖出（早于 hold_days 到期）。"""
        a = [10.0, 11.0, 12.0, 11.3, 11.3, 11.3]  # 峰值 12 → 11.3 回撤 5.8% > trailing 5%
        df = _mk_df({'A': a})
        pred = pd.Series(1.0, index=df.index)
        bt = _simple_backtest(df, pred, *_val_range(df),
                              hold_days=30, stop_loss=0.5, take_profit=0.5, trailing=0.05)
        assert bt['total_trades'] == 1, f"trailing 未触发（否则持有到窗口结束无成交）: {bt}"
        assert bt['win_rate'] == 1.0 and bt['total_return'] > 0.02


class TestBaselines:
    """ideal / random 基线语义。"""

    def test_ideal_buys_future_best(self):
        """完美预知基线：每日买未来 hold_days 实际涨幅最高者。"""
        a = [10.0] * 5 + [11.0] * 10   # 未来 +10%
        b = [10.0] * 5 + [10.2] * 10   # +2%
        c = [10.0] * 5 + [9.5] * 10    # -5%
        df = _mk_df({'A': a, 'B': b, 'C': c})
        dates = df['trade_date'].unique()
        bt = _simple_backtest(df, None, dates[4], dates[11],
                              hold_days=5, stop_loss=0.5, take_profit=0.5,
                              max_pos=1, ideal=True)
        assert bt['total_trades'] == 1 and bt['win_rate'] == 1.0, f"ideal 未选中未来最优股: {bt}"
        assert bt['total_return'] > 0.08, f"ideal 应吃到 A 的 +10%: {bt}"

    def test_random_baseline_trades_and_reproducible(self):
        """随机基线真实交易且同 seed 完全可复现。"""
        n = 12
        a = [10.0] * n
        b = [round(10.0 * 1.01 ** i, 4) for i in range(n)]
        df = _mk_df({'A': a, 'B': b})
        kw = dict(val_start=df['trade_date'].min(), val_end=df['trade_date'].max(),
                  hold_days=1, stop_loss=0.08, take_profit=0.15, max_pos=1, seed=42)
        bt1 = _simple_backtest(df, None, **kw)
        bt2 = _simple_backtest(df, None, **kw)
        assert bt1 == bt2, "同 seed 随机基线必须可复现"
        assert bt1['total_trades'] > 5, "随机基线应真实交易（旧实现因 zeros>0 恒假从不买入）"
        assert bt1['total_cost'] > 0, "成本应被记录"


class TestMisc:
    def test_empty_val_window(self):
        """验证区间无数据返回零指标。"""
        df = _mk_df({'A': [10.0] * 5})
        bt = _simple_backtest(df, None, '2030-01-01', '2030-12-31', hold_days=5)
        assert bt['total_trades'] == 0 and bt['total_return'] == 0 and bt['total_cost'] == 0
