"""超额标签 + 截面排名标准化（v3.5 方法论）纯函数单测。"""
import numpy as np
import pandas as pd
import pytest

from scripts.pipeline import cs_rank_features, index_forward_return
from strategy.backtest.account import Account
from strategy.backtest.engine import run_backtest
from strategy.backtest.models import Position, TradeConfig
from strategy.strategy import SignalStrategy


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


class TestPaperKernel:
    """纸面组合内核（v2 引擎 + 种子账户续跑，dag_task_paper_portfolio 同路径）。

    paper 单日步进 = 引擎单日运行：行情切片（含前一交易日供涨跌停标记）+ 信号强度作分数
    + same_day_budget 预算口径 + settle_delay=False / downsize_buy=False（旧 paper 口径）。
    """

    def _cfg(self):
        return TradeConfig(initial_cash=1_000_000, max_positions=2, stop_loss=0.05,
                           take_profit=0.10, trailing=0.05, hold_days=10,
                           comm=0.00025, st_tax=0.001, slip=0.001, min_cost=0.0,
                           settle_delay=False, forbid_all_trade_at_limit=False,
                           downsize_buy=False)

    def _seed(self, cash, pos=None):
        """从 paper_positions 状态构建种子账户。"""
        acct = Account(self._cfg())
        acct.cash = cash
        for code, p in (pos or {}).items():
            acct.positions[code] = Position(code=code, shares=p['shares'], buy_price=p['buy_price'],
                                            buy_date=p['buy_date'], cost_basis=p['cost_basis'],
                                            peak=p.get('peak') or p['buy_price'])
        return acct

    def _step(self, acct, rows, scores):
        """单日步进：rows=[(date, code, close)]（可含前一日），scores 与行对齐。"""
        df = pd.DataFrame(rows, columns=['trade_date', 'stock_code', 'close'])
        df = df.sort_values(['trade_date', 'stock_code'], kind='stable').reset_index(drop=True)
        pred = pd.Series(scores, index=df.index)
        td = rows[-1][0]
        return run_backtest(df, pred, SignalStrategy(self._cfg(), same_day_budget=True),
                            self._cfg(), val_start=td, val_end=td, account=acct)

    def _pos(self, code='A', shares=10000, buy=10.0, buy_date='2026-01-05', peak=None):
        return {code: {'shares': shares, 'buy_price': buy, 'cost_basis': shares * buy,
                       'buy_date': buy_date, 'peak': peak or buy, 'signal_id': 1, 'stock_name': code}}

    def test_buy_and_t1(self):
        """空仓买入 → 买入当日不触发卖出检查（T+1）；次日种子续跑正常止损。"""
        r1 = self._step(self._seed(1_000_000), [('2026-01-05', 'A', 10.0)], [3.0])
        assert len(r1.trades) == 1 and r1.trades[0]['action'] == 'BUY'
        assert r1.trades[0]['shares'] == 49900  # 预算 50万 ÷ 10.005 → 49900 股
        r2 = self._step(r1.final_account, [('2026-01-06', 'A', 9.0)], [float('nan')])
        assert r2.trades[0]['action'] == 'SELL' and r2.trades[0]['reason'] == 'stop_loss'
        assert 'A' not in r2.final_account.positions

    def test_resume_equals_full_run(self):
        """分日种子续跑与整段连续运行终态完全一致（纸面恢复状态正确性）。"""
        dates = ['2026-01-05', '2026-01-06', '2026-01-07']
        rows = [('2026-01-05', 'A', 10.0), ('2026-01-06', 'A', 9.4), ('2026-01-07', 'A', 9.8),
                ('2026-01-05', 'B', 20.0), ('2026-01-06', 'B', 20.1), ('2026-01-07', 'B', 20.2)]
        score_map = {('2026-01-05', 'A'): 3.0, ('2026-01-05', 'B'): 1.0}
        # 整段连续运行
        df = pd.DataFrame(rows, columns=['trade_date', 'stock_code', 'close'])
        df = df.sort_values(['trade_date', 'stock_code'], kind='stable').reset_index(drop=True)
        pred = pd.Series([score_map.get((d, c), float('nan')) for d, c in
                          zip(df['trade_date'], df['stock_code'])], index=df.index)
        full = run_backtest(df, pred, SignalStrategy(self._cfg(), same_day_budget=True),
                            self._cfg(), val_start='2026-01-05', val_end='2026-01-07')
        # 分日种子续跑（单日步进含前一日行供涨跌停标记）
        acct = self._seed(1_000_000)
        for i, td in enumerate(dates):
            day_rows = [r for r in rows if r[0] == td or (i > 0 and r[0] == dates[i - 1])]
            day_scores = [score_map.get((r[0], r[1]), float('nan')) for r in day_rows]
            r = self._step(acct, day_rows, day_scores)
            acct = r.final_account
        assert abs(acct.cash - full.final_account.cash) < 1e-6
        assert set(acct.positions) == set(full.final_account.positions)

    def test_stop_loss_at_trigger_price(self):
        acct = self._seed(500_000, self._pos(buy=10.0))
        r = self._step(acct, [('2026-01-20', 'A', 8.0)], [float('nan')])
        assert r.trades[0]['reason'] == 'stop_loss'
        assert r.trades[0]['price'] == pytest.approx(10.0 * 0.95)  # 按触发价成交

    def test_take_profit_and_trailing(self):
        # 止盈
        r1 = self._step(self._seed(500_000, self._pos(buy=10.0)),
                        [('2026-01-20', 'A', 11.5)], [float('nan')])
        assert r1.trades[0]['reason'] == 'take_profit'
        # trailing：峰值 10.4 回撤到 9.86（−5.2% > 5%，且未触止盈/止损）
        r2 = self._step(self._seed(500_000, self._pos(buy=10.0, peak=10.4)),
                        [('2026-01-20', 'A', 9.86)], [float('nan')])
        assert r2.trades[0]['reason'] == 'trailing'

    def test_limit_down_blocks_sell(self):
        """跌停不可卖：前一日 10.0 → 当日 8.0（−20%）触发跌停标记。"""
        acct = self._seed(500_000, self._pos(buy=10.0))
        r = self._step(acct, [('2026-01-19', 'A', 10.0), ('2026-01-20', 'A', 8.0)],
                       [float('nan'), float('nan')])
        assert not r.trades and 'A' in r.final_account.positions  # 跌停被迫持有

    def test_hold_expire(self):
        acct = self._seed(500_000, self._pos(buy=10.0, buy_date='2026-01-01'))
        r = self._step(acct, [('2026-01-15', 'A', 10.5)], [float('nan')])
        assert r.trades[0]['reason'] == 'hold_expire'

    def test_max_positions_and_strength_order(self):
        """max_positions=2，按信号强度取 B(3)、C(2)。"""
        rows = [('2026-01-05', 'A', 10.0), ('2026-01-05', 'B', 10.0), ('2026-01-05', 'C', 10.0)]
        r = self._step(self._seed(1_000_000), rows, [1.0, 3.0, 2.0])
        buys = [t['stock_code'] for t in r.trades if t['action'] == 'BUY']
        assert set(buys) == {'B', 'C'}

    def test_no_close_keeps_position(self):
        """停牌（无收盘价行）持仓保留、无成交。"""
        acct = self._seed(500_000, self._pos())
        r = self._step(acct, [('2026-01-20', 'B', 20.0)], [float('nan')])
        assert not r.trades and 'A' in r.final_account.positions
