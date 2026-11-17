"""买点检查纯逻辑单测：市场/板块判定、结构识别、候选扫描门控。"""
from app import buy_check as bc


def _bars(prices, volumes=None):
    vols = volumes or [1000] * len(prices)
    return [
        {"close": p, "close_hfq": p, "high": p * 1.01, "low": p * 0.99, "volume": v}
        for p, v in zip(prices, vols)
    ]


def _trend_pullback_bars():
    # 45 个交易日上涨到 94.5，回踩 9 天，最后一天重新站上 MA5
    prices = [50 + i * 0.5 for i in range(90)]
    prices += [94.5 - 0.4 * (i + 1) for i in range(9)]
    prices += [92.2]
    return _bars(prices)


def _bottom_reversal_bars():
    # 80 天下跌，15 天筑底且低点抬高，最后一天放量收复 MA20
    prices = [100 - i * 0.5 for i in range(80)]
    prices += [60 + 0.5 * i for i in range(15)]
    prices += [67.5]
    vols = [1000] * 95 + [2000]
    return _bars(prices, vols)


def _breakout_bars():
    import math
    prices = [50 + math.sin(i) * 2 for i in range(80)] + [56.5]
    vols = [1000] * 80 + [2500]
    return _bars(prices, vols)


class TestMarketVerdict:
    def test_all_above_ma20_and_breadth_ok(self):
        idx = [
            {"ok": True, "name": "上证指数", "close": 3600, "ma20": 3500,
             "trend_ok": True, "slope_ok": True},
            {"ok": True, "name": "中证1000", "close": 6600, "ma20": 6400,
             "trend_ok": True, "slope_ok": True},
        ]
        ok, level, reason = bc._market_verdict(idx, {"ok": True, "up_ratio": 0.55, "up": 3000, "down": 2000})
        assert ok is True
        assert level == "open"
        assert "上方" in reason

    def test_one_index_below_ma20_blocks(self):
        idx = [
            {"ok": True, "name": "上证指数", "close": 3600, "ma20": 3500,
             "trend_ok": True, "slope_ok": True},
            {"ok": True, "name": "中证1000", "close": 6100, "ma20": 6500,
             "trend_ok": False, "slope_ok": False},
        ]
        ok, level, _ = bc._market_verdict(idx, {"ok": True, "up_ratio": 0.55, "up": 3000, "down": 2000})
        assert ok is False
        assert level == "caution"

    def test_breadth_too_low_blocks(self):
        idx = [
            {"ok": True, "name": "上证指数", "close": 3600, "ma20": 3500,
             "trend_ok": True, "slope_ok": True},
            {"ok": True, "name": "中证1000", "close": 6600, "ma20": 6400,
             "trend_ok": True, "slope_ok": True},
        ]
        ok, level, reason = bc._market_verdict(idx, {"ok": True, "up_ratio": 0.20, "up": 1000, "down": 4000})
        assert ok is False
        assert "市场宽度" in reason


class TestSectorStats:
    def test_positive_flow_passes(self):
        s = bc._sector_stats([
            {"main": 1e8, "chg": 0.5, "amount": 1e10} for _ in range(5)
        ], "银行")
        assert s["pass"] is True
        assert s["level"] == "open"
        assert s["streak"] == 5
        assert s["streak_sign"] == 1

    def test_continuous_outflow_blocks(self):
        s = bc._sector_stats([
            {"main": -1e8, "chg": -1.0, "amount": 1e10} for _ in range(5)
        ], "电子")
        assert s["pass"] is False
        assert s["level"] == "closed"
        assert s["streak"] == 5

    def test_positive_5d_but_two_day_outflow_caution(self):
        series = [
            {"main": 2e8, "chg": 1.0, "amount": 1e10},
            {"main": 2e8, "chg": 0.8, "amount": 1e10},
            {"main": 2e8, "chg": 0.5, "amount": 1e10},
            {"main": -1e8, "chg": -0.5, "amount": 1e10},
            {"main": -1e8, "chg": -0.8, "amount": 1e10},
        ]
        s = bc._sector_stats(series, "计算机")
        assert s["sum5"] > 0
        assert s["pass"] is False
        assert s["level"] == "caution"


class TestDetectSetup:
    def test_trend_pullback(self):
        s = bc.detect_setup(_trend_pullback_bars())
        assert s["type"] == "trend_pullback"
        assert s["trigger_ok"] is True
        assert s["pass"] is True
        assert s["stop"] < s["entry"] < s["target"]
        assert s["rr"] >= 2

    def test_bottom_reversal_requires_higher_low_and_volume(self):
        s = bc.detect_setup(_bottom_reversal_bars())
        assert s["type"] == "bottom_reversal"
        assert s["pass"] is True
        assert s["stop"] < s["entry"]

    def test_breakout(self):
        s = bc.detect_setup(_breakout_bars())
        assert s["type"] == "breakout"
        assert s["pass"] is True

    def test_choppy_market_has_no_setup(self):
        import math
        s = bc.detect_setup(_bars([50 + math.sin(i) * 2 for i in range(100)]))
        assert s["type"] == "none"
        assert s["pass"] is False
        assert "没有" in s["reason"]

    def test_chase_above_entry_blocks(self):
        s = bc.detect_setup(_trend_pullback_bars(), {"price": 100.0})
        assert s["type"] == "trend_pullback"
        assert s["chase"] is True
        assert s["pass"] is False
        assert "不追" in s["reason"]

    def test_price_below_stop_blocks(self):
        s = bc.detect_setup(_trend_pullback_bars(), {"price": 80.0})
        assert s["broken"] is True
        assert s["pass"] is False
        assert "跌破止损" in s["reason"]


class TestRiskGate:
    def test_no_capacity_hides_one_share_projection(self, monkeypatch):
        monkeypatch.setattr(bc, "intraday_check", lambda db, body: {
            "allow": False, "max_qty": 0, "violations": [], "advice": "仓位已满",
            "projected": {"position_ratio": 0.98, "single_weight": 0.24},
        })
        g = bc._risk_gate(None, "600000", 10.0, 9.0, 12.0)
        assert g["pass"] is False
        assert g["details"]["projected"] is None

    def test_risk_gate_checks_rr_and_size(self, monkeypatch):
        monkeypatch.setattr(bc, "intraday_check", lambda db, body: {
            "allow": True, "max_qty": 500, "violations": [], "advice": "可以买",
            "projected": {"position_ratio": 0.55, "single_weight": 0.12,
                          "risk_amount": 1700, "risk_pct": 0.01},
        })
        g = bc._risk_gate(None, "600000", 75.2, 71.8, 84.0)
        assert g["pass"] is True
        assert g["details"]["max_qty"] == 500
        assert g["details"]["rr"] == 2.59
        assert g["details"]["projected"]["position_ratio"] == 0.55


class TestCandidatesGate:
    def test_disallowed_state_returns_no_items(self, monkeypatch):
        monkeypatch.setattr(bc, "intraday_state", lambda db: {
            "allow_new_buy": False, "advice": "仓位高于目标，先减仓",
        })
        monkeypatch.setattr(bc, "market_gate", lambda db: {"pass": True, "reason": "ok"})
        r = bc.candidates(None)
        assert r["allowed"] is False
        assert r["items"] == []
        assert "减仓" in r["reason"]

    def test_candidate_requires_market_sector_setup_risk(self, monkeypatch):
        monkeypatch.setattr(bc, "intraday_state", lambda db: {
            "allow_new_buy": True, "positions": [], "remaining_new_buys": 2,
            "daily_buy_budget": 100000, "position_ratio": 0.3, "cash_ratio": 0.7,
            "target_position_ratio": 0.7, "advice": "可开仓", "level": "ok",
        })
        monkeypatch.setattr(bc, "market_gate", lambda db: {"pass": True, "reason": "ok"})
        monkeypatch.setattr(bc, "_focus_pool", lambda db: [
            {"code": "600000", "name": "测试股份", "industry": "银行", "type": "stock"},
        ])
        monkeypatch.setattr(bc, "_load_bars", lambda db, codes: {
            "600000": _trend_pullback_bars(),
        })
        monkeypatch.setattr(bc, "_batch_quotes", lambda codes: {
            "600000": {"code": "600000", "price": 92.2, "change_pct": 1.0},
        })
        monkeypatch.setattr(bc, "_load_sector_series", lambda db: {
            "银行": [{"main": 1e8, "chg": 0.5, "amount": 1e10} for _ in range(5)],
        })
        monkeypatch.setattr(bc, "_max_buy_qty", lambda st, existing, price, stop: 500)

        r = bc.candidates(None)
        assert r["allowed"] is True
        assert len(r["items"]) == 1
        item = r["items"][0]
        assert item["code"] == "600000"
        assert item["setup"]["type"] == "trend_pullback"
        assert item["max_qty"] == 500
        assert item["setup"]["rr"] >= 2
