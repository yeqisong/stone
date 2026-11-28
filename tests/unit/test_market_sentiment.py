"""市场情绪判定纯逻辑单测。"""
from datetime import datetime, timedelta, timezone

from app.market_sentiment import _bj_today, _verdict

_CST = timezone(timedelta(hours=8))


class TestMarketSentiment:
    def test_panic(self):
        level, score, reasons = _verdict(0.10, -2.2, 5, 60, 0, 2)
        assert level == "panic"
        assert 0 <= score <= 100
        assert reasons

    def test_weak(self):
        level, _, _ = _verdict(0.30, -0.5, 20, 30, 0, 2)
        assert level == "weak"

    def test_neutral(self):
        level, _, _ = _verdict(0.50, 0.1, 20, 20, 1, 2)
        assert level == "neutral"

    def test_strong(self):
        level, _, _ = _verdict(0.70, 0.8, 50, 5, 2, 2)
        assert level == "strong"

    def test_missing_breadth(self):
        level, score, _ = _verdict(None, None, 0, 0, 1, 2)
        assert level in ("neutral", "weak", "strong")
        assert 0 <= score <= 100


class TestBjToday:
    def test_bj_today_is_beijing_date(self):
        """stale/asof 的「今天」必须按北京时间算（v3.27.1 §10.9）：host tz 是 UTC 时
        date.today() 会把北京凌晨判成昨天，stale 恒真、asof 错标日期。"""
        expected = datetime.now(_CST).date()
        assert abs((_bj_today() - expected).days) <= 1
