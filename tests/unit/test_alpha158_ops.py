"""Alpha158 移植算子单测（design/05 M5：ref/hhv/llv/ts_quantile/ts_rank/回归系/imax/ts_corr/max2）。

通过 _evaluate_kepl_dataframe（与生产同一执行路径）对手工构造序列断言。
"""
import numpy as np
import pandas as pd
import pytest

from scripts.feature_compute import _evaluate_kepl_dataframe


def make_df(n: int = 8, noise=None) -> pd.DataFrame:
    """单只股票 n 日面板：close 线性递增（斜率 1），volume 与 close 正相关。"""
    close = np.arange(1, n + 1, dtype=float)  # 1..n
    high = close + 0.5
    low = close - 0.5
    open_ = close + 0.1
    volume = close * 100
    return pd.DataFrame({
        'stock_code': ['000001'] * n,
        'trade_date': [f'2026-01-{i+1:02d}' for i in range(n)],
        'open': open_, 'high': high, 'low': low, 'close': close,
        'volume': volume, 'amount': volume * close,
    })


def ev(df, formula):
    r = _evaluate_kepl_dataframe(df, formula)
    assert r is not None, f'公式不可执行: {formula}'
    return pd.to_numeric(r, errors='coerce')


def test_ref():
    df = make_df()
    r = ev(df, 'ref(close, 1)')
    assert r.iloc[0] != r.iloc[0]           # 首行 NaN
    assert r.iloc[1] == pytest.approx(1.0)  # 昨收
    # 负偏移 = 未来值（显式语义）
    r2 = ev(df, 'ref(close, -1)')
    assert r2.iloc[0] == pytest.approx(2.0)


def test_hhv_llv():
    df = make_df()
    r = ev(df, 'hhv(high, 3)')
    assert r.iloc[1] != r.iloc[1]  # 未满窗 NaN（min_periods=window）
    assert r.iloc[2] == pytest.approx(df['high'].iloc[:3].max())
    r2 = ev(df, 'llv(low, 3)')
    assert r2.iloc[3] == pytest.approx(df['low'].iloc[1:4].min())


def test_ts_quantile():
    df = make_df()
    r = ev(df, 'ts_quantile(close, 4, 0.5)')
    assert r.iloc[3] == pytest.approx(np.median(df['close'].iloc[:4]))


def test_ts_rank():
    df = make_df()
    r = ev(df, 'ts_rank(close, 4)')
    # 线性递增序列：当前值恒为窗口最大 → 1.0
    assert r.iloc[3] == pytest.approx(1.0)
    assert r.iloc[6] == pytest.approx(1.0)


def test_regression_linear():
    """完美线性序列：slope=1、rsquare=1、resi=0。"""
    df = make_df()
    assert ev(df, 'slope(close, 5)').iloc[4] == pytest.approx(1.0, abs=1e-9)
    assert ev(df, 'rsquare(close, 5)').iloc[4] == pytest.approx(1.0, abs=1e-9)
    assert ev(df, 'resi(close, 5)').iloc[4] == pytest.approx(0.0, abs=1e-9)


def test_imax_imin():
    df = make_df()
    # 递增序列：当日即窗口最高 → 距最高 0 天
    assert ev(df, 'imax(high, 3)').iloc[3] == pytest.approx(0.0)
    # 逆序构造：递减序列当日即窗口最低 → imin=0
    df2 = df.iloc[::-1].reset_index(drop=True)
    r = ev(df2, 'imin(low, 3)')
    assert r.iloc[3] == pytest.approx(0.0)


def test_ts_corr():
    df = make_df()
    r = ev(df, 'ts_corr(close, volume, 5)')
    # close 与 volume 完全线性相关 → corr=1
    assert r.iloc[4] == pytest.approx(1.0, abs=1e-6)


def test_max2_min2():
    df = make_df()
    r = ev(df, 'max2(open, close)')
    assert r.iloc[0] == pytest.approx(df['open'].iloc[0])   # open = close+0.1 更大
    r2 = ev(df, 'min2(open, close)')
    assert r2.iloc[0] == pytest.approx(df['close'].iloc[0])


def test_std_builtin():
    df = make_df()
    r = ev(df, 'std(close, 4)')
    assert r.iloc[3] == pytest.approx(np.std(df['close'].iloc[:4], ddof=1))


def test_alpha158_formulas_end_to_end():
    """生成器公式抽样（覆盖全部算子）逐条可执行且产出非全 NaN。"""
    from scripts.alpha158 import _gen_alpha158_formulas
    df = make_df(70)
    formulas = dict((n, f) for n, f, *_ in _gen_alpha158_formulas())
    sample = ['a158_KMID', 'a158_KUP', 'a158_OPEN_5', 'a158_VWAP_10', 'a158_VOLUME_20',
              'a158_MA_5', 'a158_STD_10', 'a158_BETA_20', 'a158_RSQR_30', 'a158_MAX_5',
              'a158_QTLU_20', 'a158_RANK_60', 'a158_RSV_30',
              'a158_IMAX_10', 'a158_IMXD_60', 'a158_CORR_5']
    for name in sample:
        f = formulas[name]
        r = ev(df, f)
        assert r.notna().sum() > 0, f'{name} 全 NaN: {f}'
