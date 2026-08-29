"""超额标签 + 截面排名标准化（v3.5 方法论）纯函数单测。"""
import numpy as np
import pandas as pd
import pytest

from scripts.pipeline import cs_rank_features, index_forward_return


def _mk_df():
    """两个交易日 × 3 股的小面板。"""
    return pd.DataFrame({
        'trade_date': ['2026-01-05', '2026-01-05', '2026-01-05',
                       '2026-01-06', '2026-01-06', '2026-01-06'],
        'stock_code': ['A', 'B', 'C', 'A', 'B', 'C'],
        'f1': [30.0, 10.0, 20.0, 1.0, 5.0, np.nan],
        'f2': [7.0, 7.0, 7.0, 0.1, 0.2, 0.3],
    })


class TestCsRank:
    def test_per_date_rank_pct(self):
        """每个交易日内独立排名为 0~1 分位。"""
        df = cs_rank_features(_mk_df(), ['f1'])
        d1 = df[df['trade_date'] == '2026-01-05']['f1'].tolist()
        d2 = df[df['trade_date'] == '2026-01-06']['f1'].tolist()
        assert d1 == pytest.approx([1.0, 1/3, 2/3])   # A 最高
        assert d2[0] == pytest.approx(0.5)
        assert d2[1] == pytest.approx(1.0)
        assert np.isnan(d2[2])  # C 为 NaN 不参与

    def test_nan_preserved(self):
        df = cs_rank_features(_mk_df(), ['f1'])
        assert np.isnan(df[df['stock_code'] == 'C']['f1'].iloc[1])

    def test_constant_column_same_pct(self):
        """常数列（如 idx_ret_20d 同日全市场同值）得到同一分位（rank/count，三并列=2/3）
        ——同日无方差，信息量归零，正确消除 beta 泄漏。"""
        df = cs_rank_features(_mk_df(), ['f2'])
        assert df[df['trade_date'] == '2026-01-05']['f2'].tolist() == pytest.approx([2/3, 2/3, 2/3])
        assert df[df['trade_date'] == '2026-01-06']['f2'].tolist() == pytest.approx([1/3, 2/3, 1.0])

    def test_missing_col_ignored(self):
        df = cs_rank_features(_mk_df(), ['not_exist'])
        assert 'not_exist' not in df.columns


class TestIndexForwardReturn:
    def _mk_idx(self):
        dates = [f'2026-01-{d:02d}' for d in range(5, 31)]  # 26 个交易日
        closes = [100.0 + i for i in range(len(dates))]      # 每日 +1
        return dates, closes

    def test_exact_date(self):
        dates, closes = self._mk_idx()
        # t0=100，10 个交易日后=110 → +10%
        assert index_forward_return(dates, closes, '2026-01-05', 10) == pytest.approx(0.10)

    def test_missing_date_falls_back(self):
        # 日历中间挖掉 2026-01-10，其前一日 01-09 收 104，10 个交易日后收 114
        dates = [f'2026-01-{d:02d}' for d in range(5, 31) if d != 10]
        closes = list(range(100, 100 + len(dates)))
        assert index_forward_return(dates, closes, '2026-01-10', 10) == pytest.approx(114/104 - 1)
        # 日历之前的日期无前身可回退 → None
        assert index_forward_return(dates, closes, '2026-01-01', 5) is None

    def test_insufficient_window_returns_none(self):
        dates, closes = self._mk_idx()
        assert index_forward_return(dates, closes, '2026-01-25', 10) is None

    def test_date_before_calendar_returns_none(self):
        dates, closes = self._mk_idx()
        assert index_forward_return(dates, closes, '2026-01-01', 5) is None


class TestIcHealth:
    """IC 衰减监控纯函数（model_health 记 IC）。"""

    def test_rolling_stats(self):
        from scripts.pipeline import ic_rolling_stats
        dates = [f'2026-01-{d:02d}' for d in range(1, 31)]
        ics = [0.03 + 0.001 * i for i in range(30)]
        s = ic_rolling_stats(dates, ics, window=20)
        assert s['n'] == 20 and len(s['dates']) == 20
        assert s['rolling_mean'] == pytest.approx(sum(ics[10:]) / 20, abs=1e-6)
        assert s['rolling_icir'] > 5  # 单调递增序列 ICIR 极高

    def test_rolling_stats_insufficient(self):
        from scripts.pipeline import ic_rolling_stats
        s = ic_rolling_stats(['2026-01-01'], [0.02], window=20)
        assert s['n'] == 1 and s['rolling_mean'] is None

    def test_health_status_thresholds(self):
        from scripts.pipeline import ic_health_status
        assert ic_health_status(0.02) == 'HEALTHY'
        assert ic_health_status(0.003) == 'CAUTION'
        assert ic_health_status(-0.001) == 'DEGRADED'
        assert ic_health_status(None) is None


class TestWalkForward:
    """walk-forward 窗口切分 + 晋升门槛纯函数。"""

    def test_split_windows_equal_chunks(self):
        from scripts.pipeline import split_windows
        dates = [f'2026-0{i}-01' for i in range(1, 9)]  # 8 个点
        ws = split_windows(dates, 4)
        assert len(ws) == 4
        assert ws[0] == ('2026-01-01', '2026-02-01')
        assert ws[3] == ('2026-07-01', '2026-08-01')
        # 连续不重叠
        for (s1, e1), (s2, e2) in zip(ws, ws[1:]):
            assert s2 > e1

    def test_split_windows_more_windows_than_dates(self):
        from scripts.pipeline import split_windows
        ws = split_windows(['2026-01-01', '2026-01-02'], 4)
        assert len(ws) == 2

    def test_promotion_gate_pass(self):
        from scripts.pipeline import promotion_gate
        windows = [
            {'new': {'sharpe': 1.2, 'rank_ic': 0.10}, 'baseline': {'sharpe': 0.5, 'rank_ic': 0.05}},
            {'new': {'sharpe': 0.8, 'rank_ic': 0.08}, 'baseline': {'sharpe': 0.6, 'rank_ic': 0.06}},
            {'new': {'sharpe': 0.3, 'rank_ic': 0.02}, 'baseline': {'sharpe': 0.7, 'rank_ic': 0.04}},
        ]
        v, _ = promotion_gate(windows, min_win_windows=2)
        assert v == 'PASS'   # 2/3 胜出 + IC 0.0667 ≥ 0.05

    def test_promotion_gate_fail_on_ic_degrade(self):
        from scripts.pipeline import promotion_gate
        windows = [
            {'new': {'sharpe': 1.2, 'rank_ic': 0.01}, 'baseline': {'sharpe': 0.5, 'rank_ic': 0.05}},
            {'new': {'sharpe': 0.9, 'rank_ic': 0.01}, 'baseline': {'sharpe': 0.6, 'rank_ic': 0.05}},
        ]
        v, _ = promotion_gate(windows)   # 2/2 胜出但 IC 退化
        assert v == 'FAIL'

    def test_promotion_gate_no_baseline(self):
        from scripts.pipeline import promotion_gate
        v, _ = promotion_gate([])
        assert v == 'NO_BASELINE'


class TestPaperDayStep:
    """纸面组合单日步进纯函数（影子运行核心）。"""

    def _cfg(self):
        return {'max_positions': 2, 'stop_loss': 0.05, 'take_profit': 0.10,
                'trailing': 0.05, 'hold_days': 10, 'comm': 0.00025, 'st_tax': 0.001, 'slip': 0.001}

    def _pos(self, code='A', shares=10000, buy=10.0, buy_date='2026-01-05', peak=None):
        return {code: {'shares': shares, 'buy_price': buy, 'cost_basis': shares * buy,
                       'buy_date': buy_date, 'peak': peak or buy, 'signal_id': 1, 'stock_name': code}}

    def test_buy_and_t1(self):
        """空仓买入 → 买入当日不触发卖出检查（T+1）。"""
        from scripts.pipeline import paper_day_step
        cash, pos, trades, eq = paper_day_step(
            1_000_000, {}, [{'stock_code': 'A', 'strength': 3}],
            {'A': 10.0}, self._cfg(), '2026-01-05')
        assert len(trades) == 1 and trades[0]['action'] == 'BUY'
        assert pos['A']['shares'] == 49900  # 预算 50万 ÷ 10.005 → 49900 股
        # 次日暴跌超止损：因买入日=td 的 T+1 已过 → 正常止损
        cash2, pos2, tr2, _ = paper_day_step(cash, pos, [], {'A': 9.0}, self._cfg(), '2026-01-06')
        assert tr2[0]['action'] == 'SELL' and tr2[0]['reason'] == 'stop_loss'
        assert 'A' not in pos2

    def test_stop_loss_at_trigger_price(self):
        from scripts.pipeline import paper_day_step
        pos = self._pos(buy=10.0)
        cash, kept, trades, _ = paper_day_step(500_000, pos, [], {'A': 8.0}, self._cfg(), '2026-01-20')
        assert trades[0]['reason'] == 'stop_loss'
        assert trades[0]['price'] == pytest.approx(10.0 * 0.95)  # 按触发价成交

    def test_take_profit_and_trailing(self):
        from scripts.pipeline import paper_day_step
        # 止盈
        _, _, tr1, _ = paper_day_step(500_000, self._pos(buy=10.0), [], {'A': 11.5}, self._cfg(), '2026-01-20')
        assert tr1[0]['reason'] == 'take_profit'
        # trailing：峰值 10.4 回撤到 9.86（−5.2% > 5%，且未触止盈/止损）
        pos = self._pos(buy=10.0, peak=10.4)
        _, _, tr2, _ = paper_day_step(500_000, pos, [], {'A': 9.86}, self._cfg(), '2026-01-20')
        assert tr2[0]['reason'] == 'trailing'

    def test_limit_down_blocks_sell(self):
        from scripts.pipeline import paper_day_step
        _, kept, trades, _ = paper_day_step(500_000, self._pos(buy=10.0), [],
                                            {'A': 8.0}, self._cfg(), '2026-01-20', limit_down={'A'})
        assert not trades and 'A' in kept  # 跌停被迫持有

    def test_hold_expire(self):
        from scripts.pipeline import paper_day_step
        pos = self._pos(buy=10.0, buy_date='2026-01-01')
        _, _, trades, _ = paper_day_step(500_000, pos, [], {'A': 10.5}, self._cfg(), '2026-01-15')
        assert trades[0]['reason'] == 'hold_expire'

    def test_max_positions_and_strength_order(self):
        from scripts.pipeline import paper_day_step
        sigs = [{'stock_code': c, 'strength': s} for c, s in [('A', 1), ('B', 3), ('C', 2)]]
        _, pos, trades, _ = paper_day_step(1_000_000, {}, sigs, {'A': 10, 'B': 10, 'C': 10},
                                           self._cfg(), '2026-01-05')
        assert set(pos) == {'B', 'C'}  # max_positions=2，按强度取 B、C
        assert len([t for t in trades if t['action'] == 'BUY']) == 2

    def test_no_close_keeps_position(self):
        """停牌（无收盘价）持仓保留。"""
        from scripts.pipeline import paper_day_step
        _, kept, trades, _ = paper_day_step(500_000, self._pos(), [], {}, self._cfg(), '2026-01-20')
        assert 'A' in kept and not trades
