"""P3 分钟级研究脚本纯逻辑单测（无 DB/无训练）。

覆盖：
- 覆盖度门槛（不足时脚本拒绝训练）
- 特征/标签：H 分钟前瞻收益、午休断点标签无效、最后 H 根无标签
- 按交易日切分（无重叠、顺序正确）
- 评估：RankIC、方向命中、TopK 毛/净收益
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from scripts import train_intraday as ti


def _bar_df(days=20, stocks=3, bars_per_day=240, start='2026-09-01'):
    rows = []
    d0 = datetime.strptime(start, '%Y-%m-%d')
    for di in range(days):
        day = d0 + timedelta(days=di)
        for si in range(stocks):
            code = f'60000{si}'
            for bi in range(bars_per_day):
                t = day.replace(hour=9, minute=30) + timedelta(minutes=bi)
                price = 10.0 + si + bi * 0.001
                rows.append((code, t, price, price + .005, price - .005, price,
                             1000 + bi, (1000 + bi) * 100 * price, price, 1.0, 6))
    return pd.DataFrame(rows, columns=ti.BAR_COLUMNS)


def _one_stock_bars(times, closes=None, code='600519'):
    closes = closes or [10.0 + i * 0.01 for i in range(len(times))]
    return pd.DataFrame([{
        'stock_code': code, 'bar_time': t,
        'open': c, 'high': c + 0.005, 'low': c - 0.005, 'close': c,
        'volume': 1000, 'amount': 1000 * 100 * c, 'vwap': c,
        'volume_ratio': 1.0, 'snapshots': 6,
    } for t, c in zip(times, closes)], columns=ti.BAR_COLUMNS)


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *a, **k):
        return _Rows(self.rows)


class TestLoadBars:
    def test_coerces_decimal_and_datetime(self):
        # 真实 psycopg2 返回 NUMERIC=Decimal；不转 float 会让 groupby.cummax 在 object 上炸
        row = ('600519', datetime(2026, 11, 12, 9, 30), Decimal('10.5'),
               Decimal('10.6'), Decimal('10.4'), Decimal('10.55'),
               Decimal('1000'), Decimal('1055000'), Decimal('10.5'),
               Decimal('1.2'), Decimal('6'))
        df = ti.load_bars(_DB([row]))
        assert df.loc[0, 'close'] == pytest.approx(10.55)
        assert df['close'].dtype == 'float64'
        assert pd.api.types.is_datetime64_any_dtype(df['bar_time'])


class TestCoverage:
    def test_ready(self):
        cov = ti.coverage(_bar_df(), min_days=20, min_bars=10000, min_stocks=3)
        assert cov['rows'] == 20 * 3 * 240
        assert cov['days'] == 20
        assert cov['stocks'] == 3
        assert cov['ready'] is True

    def test_not_ready(self):
        cov = ti.coverage(_bar_df(days=2), min_days=20, min_bars=10000, min_stocks=3)
        assert cov['ready'] is False

    def test_partial_day_not_counted(self):
        # 每天只有 100 个分钟点（下午才启动）→ 不算完整交易日
        cov = ti.coverage(_bar_df(days=20, bars_per_day=100),
                          min_days=20, min_bars=0, min_stocks=3)
        assert cov['days'] == 0
        assert cov['ready'] is False

    def test_empty(self):
        cov = ti.coverage(pd.DataFrame(columns=ti.BAR_COLUMNS))
        assert cov['rows'] == 0 and cov['ready'] is False


class TestFeatures:
    def test_forward_return_and_tail_nan(self):
        times = [datetime(2026, 11, 12, 9, 30) + timedelta(minutes=i) for i in range(20)]
        df = _one_stock_bars(times)
        d, feats = ti.compute_features(df, horizon=5)
        d = d.sort_values('bar_time').reset_index(drop=True)
        assert d.loc[0, 'fwd_ret'] == pytest.approx(0.05 / 10.0, abs=1e-6)
        assert d.loc[5, 'ret_5'] == pytest.approx(0.05 / 10.0, abs=1e-6)
        assert d.loc[15:, 'fwd_ret'].isna().all()      # 最后 5 根无标签
        assert 'ret_1' in feats and 'vwap_dev' in feats

    def test_lunch_gap_label_invalid(self):
        am = [datetime(2026, 11, 12, 11, 20) + timedelta(minutes=i) for i in range(10)]
        pm = [datetime(2026, 11, 12, 13, 0) + timedelta(minutes=i) for i in range(10)]
        df = _one_stock_bars(am + pm)
        d, _ = ti.compute_features(df, horizon=5)
        d = d.set_index('bar_time')
        assert pd.notna(d.loc[datetime(2026, 11, 12, 11, 20), 'fwd_ret'])
        assert pd.isna(d.loc[datetime(2026, 11, 12, 11, 25), 'fwd_ret'])
        assert pd.isna(d.loc[datetime(2026, 11, 12, 11, 29), 'fwd_ret'])
        assert pd.notna(d.loc[datetime(2026, 11, 12, 13, 0), 'fwd_ret'])


class TestSplit:
    def test_chronological(self):
        dates = [date(2026, 9, 1) + timedelta(days=i) for i in range(20)]
        tr, va, te = ti.split_dates(dates, 0.6, 0.2)
        assert len(tr) == 12 and len(va) == 4 and len(te) == 4
        assert max(tr) < min(va) < max(va) < min(te)
        assert not (set(tr) & set(va)) and not (set(va) & set(te))

    def test_too_few_days(self):
        with pytest.raises(ValueError):
            ti.split_dates([date(2026, 9, 1), date(2026, 9, 2)])


class TestEvaluate:
    def test_rank_ic_and_topk(self):
        bar_times, preds, ys, dates = [], [], [], []
        for hh, mm in [(9, 30), (9, 45), (10, 0), (10, 15), (10, 30)]:
            bt = datetime(2026, 11, 12, hh, mm)
            for p, y in [(4, .04), (3, .03), (2, .02), (1, .01)]:
                bar_times.append(bt); preds.append(p); ys.append(y)
                dates.append(date(2026, 11, 12))
        m = ti.evaluate_predictions(preds, ys, dates, bar_times, horizon=15,
                                    topk=2, cost_bps=15)
        assert m['rank_ic_mean'] == pytest.approx(1.0)
        assert m['direction_hit'] == 1.0
        assert m['topk_gross'] == pytest.approx(0.035)
        assert m['topk_net_horizon'] == pytest.approx(0.035 - 15 / 1e4)
        assert m['n_ic_bars'] == 5

    def test_constant_pred_skips_ic(self):
        bt = datetime(2026, 11, 12, 10, 0)
        m = ti.evaluate_predictions([1, 1, 1], [.01, .02, .03],
                                    [date(2026, 11, 12)] * 3, [bt] * 3)
        assert m['rank_ic_mean'] is None
        assert m['n_ic_bars'] == 0
