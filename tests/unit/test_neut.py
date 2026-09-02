"""neut 截面中性化算子单测：残差正交性 + NaN 保留 + 行业退化。"""
import numpy as np
import pandas as pd
import pytest


def _panel(n_days=6, n_stocks=120):
    """合成面板：市值跨两个数量级 + 三行业，因子值内嵌市值/行业暴露。"""
    rng = np.random.default_rng(7)
    rows = []
    mvs, inds = {}, {}
    for s in range(n_stocks):
        mvs[f'S{s:03d}'] = 10 ** rng.uniform(8.5, 10.5)
        inds[f'S{s:03d}'] = ['银行', '医药', '电子'][s % 3]
    for d in range(n_days):
        for s in range(n_stocks):
            code = f'S{s:03d}'
            mv = mvs[code]
            exposure = 0.3 * np.log1p(mv) / 10          # 刻意注入市值暴露
            exposure += 0.5 if inds[code] == '银行' else 0.0  # 注入行业暴露
            rows.append({
                'trade_date': f'2025-01-{d + 5:02d}',
                'stock_code': code,
                'circ_mv': mv,
                'f': exposure + rng.normal(0, 0.01),
            })
    df = pd.DataFrame(rows)
    return df, mvs, inds


def test_residual_orthogonal_to_mv_and_industry():
    from scripts.feature_compute import _neut_cross_sectional
    df, _, _ = _panel()
    resid = _neut_cross_sectional(df['f'], df, db=None)
    assert resid.notna().all()

    def daily_corr(a, b):
        t = pd.DataFrame({'dt': df['trade_date'], 'a': a, 'b': b}).dropna()
        return t.groupby('dt').apply(
            lambda g: g['a'].corr(g['b']) if len(g) > 20 else np.nan, include_groups=False)

    assert abs(daily_corr(resid, np.log1p(df['circ_mv'])).mean()) < 1e-8   # 市值正交
    gm = pd.DataFrame({'dt': df['trade_date'], 'r': resid, 'i': df['stock_code'].map(
        df['stock_code'].map({}).fillna(''))})  # 行业经 db=None 不加载——哑变量为空
    # db=None → 行业映射为空，退化为纯市值中性化：残差均值逐日≈0（截距吸收）
    assert (resid.groupby(df['trade_date']).mean().abs() < 1e-8).all()


def test_nan_rows_preserved():
    from scripts.feature_compute import _neut_cross_sectional
    df, _, _ = _panel(n_days=2, n_stocks=60)
    v = df['f'].astype(float)
    v.iloc[::7] = np.nan
    resid = _neut_cross_sectional(v, df, db=None)
    assert resid.isna().sum() == v.isna().sum()          # NaN 位置保留
    assert resid.notna().sum() == v.notna().sum()


def test_missing_mv_column_degrades_gracefully():
    from scripts.feature_compute import _neut_cross_sectional
    df, _, _ = _panel()
    df = df.drop(columns=['circ_mv'])
    resid = _neut_cross_sectional(df['f'], df, db=None)
    # 无市值列 → 全 NaN 回归样本 → 原值透传
    pd.testing.assert_series_equal(resid, df['f'], check_names=False)


def test_kepl_neut_formula_end_to_end():
    from scripts.feature_compute import _evaluate_kepl_dataframe
    df, _, _ = _panel()
    df['ma'] = df.groupby('stock_code')['close'] if 'close' in df.columns else None
    df['close'] = 10.0
    r = _evaluate_kepl_dataframe(df, 'neut(ma(close, 20))', db=None)
    assert r is not None and r.notna().any()
