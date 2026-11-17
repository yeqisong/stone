"""持仓盘中动作信号纯逻辑单测。"""
from app.intraday_signal import holding_signal


class TestHoldingSignal:
    def test_overweight_bounce_is_reduce(self):
        # 用户场景：阳光电源 +3%，但单票 44% 超配、成本仍浮亏 → 减仓，不是追涨
        s = holding_signal(80, 3.0, 1.2, 80, 92, 0.44)
        assert s["action"] == "reduce"
        assert "超配" in s["reason"]

    def test_deep_loss_is_stop(self):
        s = holding_signal(90, -1.0, -1.0, 20, 100, 0.10)
        assert s["action"] == "stop"
        assert s["level"] == "critical"

    def test_hold_when_not_overweight(self):
        s = holding_signal(105, 1.0, 0.5, 60, 100, 0.10)
        assert s["action"] == "hold"

    def test_trim_when_overweight_but_flat(self):
        s = holding_signal(101, 0.5, 0.2, 60, 100, 0.30)
        assert s["action"] == "trim"

    def test_reduce_watch_when_above_vwap_fails(self):
        s = holding_signal(103, 3.0, -0.2, 40, 100, 0.10)
        assert s["action"] == "reduce_watch"

    def test_watch_stop_on_vwap_break(self):
        s = holding_signal(97, -3.0, -1.0, 20, 100, 0.10)
        assert s["action"] == "watch_stop"
