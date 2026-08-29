"""因子 IC 检验纯函数单测 — 合成数据已知答案验证（不依赖 DB）。"""
import numpy as np
import pandas as pd
import pytest

from scripts.factor_ic import _daily_ic, _layer_stats, traffic_light, IC_GREEN


def _mk_factor_df(n_days=80, n_stocks=60, seed=7):
    """构造因子值与未来收益强正相关的合成面板：value = 0.5*fret + 噪声。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range('2026-01-05', periods=n_days).strftime('%Y-%m-%d')
    rows = []
    for d in dates:
        frets = rng.normal(0, 0.02, n_stocks)
        for i in range(n_stocks):
            fret = frets[i]
            value = 0.5 * fret + rng.normal(0, 0.01)  # 因子 ≈ 前瞻收益的带噪信号
            rows.append((d, f'{600000+i:06d}', value, 10.0, 10.0 * (1 + fret)))
    df = pd.DataFrame(rows, columns=['trade_date', 'stock_code', 'value', 'close', 'fwd_5'])
    df['fret_5'] = df['fwd_5'] / df['close'] - 1
    return df


class TestDailyIC:
    def test_strong_signal_positive_ic(self):
        """因子与未来收益强正相关 → RankIC 均值应显著为正。"""
        df = _mk_factor_df()
        dates, ic_list, rank_list, n_list = _daily_ic(df, 5, min_names=30)
        assert len(dates) == 80
        assert np.mean(rank_list) > 0.5, f"强信号因子 RankIC 应 >0.5: {np.mean(rank_list):.3f}"
        assert all(n == 60 for n in n_list)

    def test_too_thin_section_skipped(self):
        """截面股票数 < min_names 的日期被剔除。"""
        df = _mk_factor_df(n_stocks=10)
        dates, _, _, n_list = _daily_ic(df, 5, min_names=30)
        assert len(dates) == 0, "10 股截面 < 30 应全部剔除"


class TestLayerStats:
    def test_monotonic_layer_returns(self):
        """强信号因子分层收益应单调：低组 < 高组。"""
        df = _mk_factor_df()
        ls = _layer_stats(df, 5, layers=5)
        assert len(ls['dates']) == 80
        means = ls['mean_fret']
        assert means[-1] > means[0], f"最高组收益应 > 最低组: {means}"
        # 分层近似单调（允许相邻层微小逆序）
        diffs = np.diff(means)
        assert np.mean(np.array(diffs) > 0) >= 0.75, f"分层应基本单调: {means}"

    def test_nav_shapes(self):
        df = _mk_factor_df()
        ls = _layer_stats(df, 5, layers=5)
        assert set(ls['navs'].keys()) == {'0', '1', '2', '3', '4'}
        for k, nav in ls['navs'].items():
            assert len(nav) == 80 and nav[0] > 0.99  # 首日 ≈ 1+日均收益
        assert len(ls['ls_nav']) == 80


class TestTrafficLight:
    def test_thresholds(self):
        # 三项达标 → 绿
        assert traffic_light(0.03, 0.4, 0.6) == 'green'
        # 两项 → 黄
        assert traffic_light(0.03, 0.4, 0.4) == 'yellow'   # RankIC + ICIR
        assert traffic_light(0.03, 0.1, 0.6) == 'yellow'   # RankIC + 同号
        assert traffic_light(0.01, 0.4, 0.6) == 'yellow'   # ICIR + 同号
        assert traffic_light(0.01, 0.4, 0.6) == 'yellow'
        assert traffic_light(-0.03, -0.4, 0.4) == 'yellow'  # 反向因子按绝对值判
        # 一项 → 红
        assert traffic_light(0.01, 0.1, 0.4) == 'red'

    def test_constants_match_doc(self):
        assert IC_GREEN == {'abs_rank_ic': 0.02, 'abs_icir': 0.30, 'same_sign': 0.55}
