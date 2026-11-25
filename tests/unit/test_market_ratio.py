"""比值中枢（风格比价分位）纯函数测试 — 不连库。"""
from datetime import date, timedelta

from app.api.market import _compute_ratio_series


def _rows(n=300, num_up=True):
    d0 = date(2024, 1, 1)
    rows = []
    for i in range(n):
        d = d0 + timedelta(days=i)
        rows.append((d, '399006', 100 + i if num_up else 1000 - i))
        rows.append((d, '000922', 100))
    return rows


class TestComputeRatioSeries:
    def test_ratio_and_percentile(self):
        out = _compute_ratio_series(_rows(300), '399006', '000922', 1260)
        assert len(out) == 300
        assert out[-1]['ratio'] == round(399 / 100, 4)
        # min_periods=250：第 250 个点才出现分位
        assert out[248]['pct'] is None
        assert out[249]['pct'] is not None
        # 单调上升序列的最新值处于窗口最高位
        assert out[-1]['pct'] > 99

    def test_declining_series_low_percentile(self):
        out = _compute_ratio_series(_rows(300, num_up=False), '399006', '000922', 1260)
        assert out[-1]['pct'] < 1

    def test_missing_denominator_returns_empty(self):
        rows = [(date(2024, 1, 1), '399006', 100)]
        assert _compute_ratio_series(rows, '399006', '000922', 1260) == []

    def test_dates_aligned_by_inner_join(self):
        rows = [
            (date(2024, 1, 1), '399006', 100), (date(2024, 1, 2), '399006', 110),
            (date(2024, 1, 2), '000922', 100), (date(2024, 1, 3), '000922', 105),
        ]
        out = _compute_ratio_series(rows, '399006', '000922', 1260)
        assert [x['date'] for x in out] == ['2024-01-02']
        assert out[0]['ratio'] == 1.1
