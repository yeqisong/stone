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
