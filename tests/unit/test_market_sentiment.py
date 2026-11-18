"""市场情绪判定纯逻辑单测。"""
from app.market_sentiment import _verdict


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
