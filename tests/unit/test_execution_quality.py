"""执行质量纯逻辑单测：时间解析、价格取点、评级。"""
from datetime import date, datetime

from app import execution_quality as eq

DAY = date(2026, 11, 18)


def _bars():
    return [
        {"t": datetime(2026, 11, 18, 10, 0), "close": 10.0, "vwap": 10.1},
        {"t": datetime(2026, 11, 18, 10, 30), "close": 9.8, "vwap": 10.0},
        {"t": datetime(2026, 11, 18, 11, 0), "close": 9.5, "vwap": 9.9},
    ]


class TestExecutionQuality:
    def test_parse_dt(self):
        assert eq._parse_dt(DAY, "09:30:05") == datetime(2026, 11, 18, 9, 30, 5)
        assert eq._parse_dt(DAY, "09:30") == datetime(2026, 11, 18, 9, 30)
        assert eq._parse_dt(DAY, "") is None
        assert eq._parse_dt(DAY, "bad") is None

    def test_pick_price_after_offset(self):
        bars = _bars()
        assert eq._pick(bars, datetime(2026, 11, 18, 10, 30)) == 9.8
        assert eq._pick(bars, datetime(2026, 11, 18, 10, 45)) == 9.5
        # 超出最后一根：用最后一根
        assert eq._pick(bars, datetime(2026, 11, 18, 14, 0)) == 9.5

    def test_vwap_at(self):
        bars = _bars()
        assert eq._vwap_at(bars, datetime(2026, 11, 18, 10, 0)) == 10.1
        assert eq._vwap_at(bars, datetime(2026, 11, 18, 10, 1)) == 10.0

    def test_grade(self):
        assert eq._grade(0.01) == "good"
        assert eq._grade(0.0) == "neutral"
        assert eq._grade(-0.02) == "poor"
        assert eq._grade(None) == "no_data"

    def test_avg_ignores_none(self):
        assert eq._avg([0.01, None, -0.01]) == 0.0
        assert eq._avg([]) is None
