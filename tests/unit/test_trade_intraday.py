"""实时仓位护栏纯逻辑单测（无 DB）：费用估算、最大可买、现金/成本更新。"""
from datetime import date

import app.trade_intraday as ti
from app.trade_intraday import (
    _apply_manual,
    _build_coach,
    _estimate_fees,
    _max_buy_qty,
    _money,
    _profile_from_review,
)


def _state(**kw):
    st = {
        "equity": 100000.0,
        "cash": 50000.0,
        "market_value": 50000.0,
        "positions": [{"market_value": 10000.0}],
        "daily_buy_budget": 20000.0,
        "rules": {
            "risk_per_trade": 0.02,
            "max_single_weight": 0.20,
            "target_position_ratio": 0.70,
            "cash_floor": 0.30,
            "max_positions": 6,
        },
    }
    st.update(kw)
    return st


class TestFees:
    def test_buy_min_commission(self):
        f = _estimate_fees("buy", 10000)
        assert f["commission"] == 5.0
        assert f["stamp_tax"] == 0.0
        assert f["total"] == 5.1

    def test_sell_has_stamp(self):
        f = _estimate_fees("sell", 10000)
        assert f["stamp_tax"] == 5.0
        assert f["total"] == 10.1


class TestMaxBuyQty:
    def test_capped_by_cash_floor(self):
        # 现金 5万、账户 10万、底线 30% → 最多动用 2万；价格 10 → 1900 股（费用后）
        q = _max_buy_qty(_state(), None, price=10.0, stop=9.2)
        assert q == 1900

    def test_capped_by_target_position(self):
        # 已持仓 6.9万 / 10万，目标 70% → 只剩 1000 元空间 → 0 手
        st = _state(market_value=69000.0, cash=31000.0,
                    positions=[{"market_value": 69000.0}])
        assert _max_buy_qty(st, None, price=10.0, stop=9.2) == 0

    def test_capped_by_risk_budget(self):
        # 单笔风险 2% = 2000；止损距离 2 元 → 1000 股；其他条件放宽
        st = _state(cash=100000.0, market_value=0.0, positions=[],
                    daily_buy_budget=100000.0,
                    rules={**_state()["rules"], "target_position_ratio": 0.95,
                           "cash_floor": 0.0, "max_single_weight": 0.95})
        assert _max_buy_qty(st, None, price=10.0, stop=8.0) == 1000

    def test_capped_by_single_weight(self):
        st = _state(equity=100000.0, cash=100000.0, market_value=0.0, positions=[],
                    daily_buy_budget=100000.0,
                    rules={**_state()["rules"], "target_position_ratio": 0.95,
                           "cash_floor": 0.0})
        # 单票 20% = 2万；价格 10 → 2000 股
        assert _max_buy_qty(st, None, price=10.0, stop=9.99) == 2000

    def test_no_new_code_when_positions_full(self):
        st = _state(positions=[{"market_value": 10000.0}] * 6,
                    rules={**_state()["rules"], "max_positions": 6})
        assert _max_buy_qty(st, None, price=10.0, stop=9.2) == 0


class TestCoach:
    def test_money_units(self):
        assert _money(14765) == "¥1.5万"
        assert _money(569390000) == "¥5.69亿"
        assert _money(-1000) == "-¥1,000"

    def test_profile_detects_weaknesses(self):
        r = {
            "ok": True,
            "summary": {"max_drawdown": 0.5, "net_deposit": 20000,
                        "equity_start": 100000, "return_pct": -0.5},
            "behavior": {
                "add_loss_amount": 200000, "add_win_amount": 50000,
                "add_loss_count": 20, "add_win_count": 5,
                "post_loss_next_day": {"avg_buy_amount": 40000},
                "post_win_next_day": {"avg_buy_amount": 30000},
                "trades_per_day": 5, "same_day_roundtrips": 20,
                "rebuy_within_7d": 30, "trades": 500, "active_days": 100,
                "max_single_weight": 0.5, "max_weight_date": "2026-01-01",
                "disposition_ratio": 1.5, "hold_days_win": 5, "hold_days_loss": 8,
            },
            "equity": [{"equity": 100000, "cash": 1000},
                       {"equity": 110000, "cash": 2000}],
        }
        p = _profile_from_review(r)
        keys = {t["key"] for t in p["types"]}
        assert {"loss_add", "full_position", "revenge", "overtrade",
                "concentration", "drawdown", "disposition", "deposit"} <= keys
        assert p["types"][0]["key"] == "loss_add"

    def test_coach_plan_and_tranches(self):
        st = {
            "trade_date": "2026-11-17", "slip_end": "2026-11-17",
            "target_position_ratio": 0.70, "max_position_ratio": 0.90,
            "position_ratio": 0.95, "market_value": 95000.0, "equity": 100000.0,
            "top_weight": 0.40, "top_position": {"stock_name": "A"},
            "positions": [{"stock_name": "A", "pnl_pct": -0.20}],
            "consecutive_loss_days": 2, "remaining_new_buys": 0,
            "today_actions": [], "rules": {"max_single_weight": 0.20},
        }
        sectors = [{"name": "A", "industry": "电子", "sum3": -1e9,
                    "streak": 3, "sign": -1}]
        c = _build_coach(st, {"types": [{"key": "x", "label": "测试"}], "metrics": {}}, sectors)
        assert c["next"].startswith("今天只做一件事")
        assert "A" in c["next"] and "电子" in c["next"]
        assert any("最大单票" in a["title"] for a in c["active"])
        assert any("电子" in a["title"] for a in c["active"])
        assert len(c["tranches"]) == 3
        assert abs(c["tranches"][0]["amount"] - 8333.33) < 0.01

    def test_single_stock_cap_can_be_binding(self):
        # 仓位目标只要求卖 2.5 万；但 60% 单票要降到 20% 需卖 4 万 → 取 4 万
        st = {
            "trade_date": "2026-11-17", "slip_end": "2026-11-17",
            "target_position_ratio": 0.70, "max_position_ratio": 0.90,
            "position_ratio": 0.95, "market_value": 95000.0, "equity": 100000.0,
            "top_weight": 0.60,
            "top_position": {"stock_name": "阳光电源", "market_value": 60000.0},
            "positions": [{"stock_name": "阳光电源", "pnl_pct": -0.18}],
            "consecutive_loss_days": 0, "remaining_new_buys": 1,
            "today_actions": [], "rules": {"max_single_weight": 0.20},
        }
        c = _build_coach(st, {"types": []}, [])
        assert abs(c["tranches"][0]["amount"] - 13333.33) < 0.01
        assert "阳光电源" in c["next"]
        assert "20%" in c["next"]


class TestApplyManual:
    def test_buy_updates_cash_and_cost(self):
        pos = {}
        cash = _apply_manual(pos, 10000.0, {
            "stock_code": "600000", "stock_name": "浦发银行", "side": "buy",
            "quantity": 100, "price": 10.0, "amount": 1000.0,
            "commission": 5.0, "stamp_tax": 0.0, "transfer_fee": 0.0, "extra_fee": 0.0,
        }, apply_cash=True)
        assert abs(cash - 8995.0) < 1e-9
        assert pos["600000"]["qty"] == 100
        assert abs(pos["600000"]["cost"] - 10.05) < 1e-9

    def test_sell_removes_position_and_adds_cash(self):
        pos = {"600000": {"qty": 100.0, "cost": 10.0, "name": "浦发银行", "price": 10.0}}
        cash = _apply_manual(pos, 0.0, {
            "stock_code": "600000", "stock_name": "浦发银行", "side": "sell",
            "quantity": 100, "price": 12.0, "amount": 1200.0,
            "commission": 5.0, "stamp_tax": 0.6, "transfer_fee": 0.0, "extra_fee": 0.0,
        }, apply_cash=True)
        assert abs(cash - 1194.4) < 1e-9
        assert "600000" not in pos


def _buy_state():
    return {
        "cash": 50000.0, "equity": 100000.0, "market_value": 50000.0,
        "position_ratio": 0.50, "cash_ratio": 0.50,
        "target_position_ratio": 0.70, "max_position_ratio": 0.90,
        "cash_floor": 0.30, "max_buy_amount": 20000.0,
        "daily_buy_budget": 20000.0, "remaining_new_buys": 2,
        "max_new_buys_per_day": 2, "consecutive_loss_days": 0,
        "positions": [], "today_actions": [],
        "rules": {
            "max_single_weight": 0.20, "max_positions": 6,
            "stop_loss_pct": -0.08, "risk_per_trade": 0.02,
            "no_rebuy_days": 5, "consecutive_loss_trigger": 2,
            "target_position_ratio": 0.70, "max_position_ratio": 0.90,
            "cash_floor": 0.30, "max_daily_buy_pct": 0.30,
        },
    }


class TestTPlusOne:
    def test_buy_check_warns_t_plus_one(self, monkeypatch):
        st = _buy_state()
        monkeypatch.setattr(ti, "_build_state", lambda db, cash_override=None: st)
        monkeypatch.setattr(ti, "_stock_name", lambda db, code: "测试股份")
        monkeypatch.setattr(ti, "_today", lambda: date(2026, 11, 17))
        monkeypatch.setattr(ti, "_recent_sell", lambda db, code, day, days: None)
        res = ti.check(None, {
            "stock_code": "600000", "side": "buy", "quantity": 100,
            "price": 10.0, "stop_price": 9.0,
        })
        assert res["allow"] is True
        assert any(v["rule"] == "t_plus_1" and v["level"] == "warn"
                   for v in res["violations"])
        assert "T+1" in res["advice"]


class TestHardBlock:
    def test_is_hard_block(self):
        assert ti._is_hard_block({"violations": [
            {"rule": "same_day_roundtrip", "level": "block"}]}) is True
        assert ti._is_hard_block({"violations": [
            {"rule": "add_to_loser", "level": "block"}]}) is True
        assert ti._is_hard_block({"violations": [
            {"rule": "target_position_ratio", "level": "block"}]}) is False
        assert ti._is_hard_block({"violations": []}) is False

    def test_cooling_off_reads_latest_hard_block(self):
        from datetime import datetime, timedelta

        class _Fake:
            def execute(self, *a, **k): return self
            def fetchone(self): return (datetime.now() - timedelta(minutes=5), "沪电股份")

        cool = ti._cooling_off(_Fake())
        assert cool is not None
        assert 0 < cool["remaining_min"] <= 30
        assert cool["stock_name"] == "沪电股份"

    def test_buy_check_blocks_during_cooling_off(self, monkeypatch):
        st = _buy_state()
        monkeypatch.setattr(ti, "_build_state", lambda db, cash_override=None: st)
        monkeypatch.setattr(ti, "_stock_name", lambda db, code: "测试股份")
        monkeypatch.setattr(ti, "_today", lambda: date(2026, 11, 17))
        monkeypatch.setattr(ti, "_recent_sell", lambda db, code, day, days: None)
        monkeypatch.setattr(ti, "_cooling_off", lambda db: {
            "until": "10:50", "remaining_min": 20, "stock_name": "沪电股份"})
        res = ti.check(None, {
            "stock_code": "600000", "side": "buy", "quantity": 100,
            "price": 10.0, "stop_price": 9.0,
        })
        assert res["allow"] is False
        assert any(v["rule"] == "cooling_off" and v["level"] == "block"
                   for v in res["violations"])


class TestTradePlan:
    def _state_with_positions(self):
        return {
            "trade_date": "2026-11-18", "asof": "10:29:00",
            "target_position_ratio": 0.70, "max_position_ratio": 0.90,
            "position_ratio": 0.836, "market_value": 132000.0,
            "equity": 158000.0, "cash": 26000.0,
            "top_weight": 0.42,
            "top_position": {"stock_code": "300274", "stock_name": "阳光电源",
                             "market_value": 66000.0},
            "positions": [
                {"stock_code": "300274", "stock_name": "阳光电源",
                 "quantity": 900, "price": 73.3, "market_value": 65970},
                {"stock_code": "002463", "stock_name": "沪电股份",
                 "quantity": 500, "price": 76.0, "market_value": 38000},
            ],
            "consecutive_loss_days": 3, "remaining_new_buys": 0,
            "today_actions": [], "rules": {"max_single_weight": 0.20},
        }

    def _state(self):
        return {
            "trade_date": "2026-11-18", "asof": "10:20:00",
            "target_position_ratio": 0.70, "max_position_ratio": 0.90,
            "position_ratio": 0.84, "market_value": 132000.0, "equity": 158000.0,
            "top_weight": 0.42,
            "top_position": {"stock_code": "300274", "stock_name": "阳光电源",
                             "market_value": 66000.0},
            "positions": [], "consecutive_loss_days": 3,
            "remaining_new_buys": 0, "today_actions": [],
            "rules": {"max_single_weight": 0.20},
        }

    def test_plan_uses_single_cap_when_binding(self):
        p = ti._plan_from_state(self._state())
        # 仓位目标需减 21,400；单票 20% 需减 34,400 → 取 34,400
        assert p["binding"] == "single"
        assert abs(p["need"] - (66000 - 0.20 * 158000)) < 0.01
        assert len(p["tranches"]) == 3

    def test_plan_includes_all_over_cap_rows(self):
        p = ti._plan_from_state(self._state_with_positions())
        # 阳光降到20%后沪电仍超20%，必须两只都进清单
        names = {r["name"] for r in p["rows"]}
        assert {"阳光电源", "沪电股份"} <= names
        assert p["need"] > 38000

    def test_status_tracks_rows_per_stock(self):
        st = self._state_with_positions()
        p = ti._plan_from_state(st)
        st["today_actions"] = [
            {"side": "sell", "stock_code": r["code"], "amount": r["sell_amount"]}
            for r in p["rows"]
        ]
        out = ti._plan_status(p, st)
        assert out["remaining"] == 0
        assert all(r["status"] == "done" for r in out["rows"])

    def test_status_counts_only_top_stock_when_single_binding(self):
        p = ti._plan_from_state(self._state())
        st = self._state()
        st["today_actions"] = [
            {"side": "sell", "stock_code": "002463", "amount": 20000.0},
        ]
        out = ti._plan_status(p, st)
        # 卖的不是最大单票，单票约束没被满足
        assert out["executed"] == 0
        assert out["tranches"][0]["status"] == "pending"
        st["today_actions"] = [
            {"side": "sell", "stock_code": "300274", "amount": 12000.0},
        ]
        out = ti._plan_status(p, st)
        assert out["tranches"][0]["status"] == "done"
        assert out["remaining"] > 0

    def test_position_binding_counts_any_sell(self):
        st = self._state()
        st["top_position"] = {"stock_code": "300274", "stock_name": "阳光电源",
                              "market_value": 30000.0}
        p = ti._plan_from_state(st)
        assert p["binding"] == "position"
        st["today_actions"] = [{"side": "sell", "stock_code": "002463", "amount": 9000.0}]
        out = ti._plan_status(p, st)
        assert out["executed"] == 9000.0
        assert out["tranches"][0]["status"] == "done"
        assert out["tranches"][1]["status"] == "partial"

    def test_coach_time_aware_after_first_window(self):
        st = self._state()
        st["asof"] = "14:12:00"
        p = ti._plan_from_state(st)
        c = ti._build_coach(st, {"types": []}, [], plan=p)
        assert "第二档窗口" in c["next"] and "14:12" in c["next"]
        assert "计划已完成" not in c["next"]
        assert c["tranches"][0]["status"] == "missed"

    def test_coach_plan_done(self):
        st = self._state()
        st["today_actions"] = [
            {"side": "sell", "stock_code": "300274", "amount": 40000.0},
        ]
        p = ti._plan_from_state(st)
        c = ti._build_coach(st, {"types": []}, [], plan=p)
        assert "计划已完成" in c["next"]
        assert c["plan"]["done"] is True
        assert all(t["status"] == "done" for t in c["tranches"])


class TestLivePositions:
    def _patch(self, monkeypatch):
        monkeypatch.setattr(ti, "_today", lambda: date(2026, 11, 17))
        monkeypatch.setattr(ti, "build_positions", lambda db: {
            "positions": [
                {"stock_code": "002463", "stock_name": "沪电股份",
                 "quantity": 500, "cost_price": 86.228, "cost_estimated": False},
                {"stock_code": "603986", "stock_name": "兆易创新",
                 "quantity": 100, "cost_price": 340.534, "cost_estimated": False},
            ],
            "summary": {"cash": 3656.24},
        })
        monkeypatch.setattr(ti, "_slip_end", lambda db: date(2026, 11, 16))
        monkeypatch.setattr(ti, "get_discipline", lambda db: {})
        monkeypatch.setattr(ti, "_snapshot", lambda db, day: None)
        monkeypatch.setattr(ti, "_manual_rows", lambda db, after: [
            {"stock_code": "603986", "stock_name": "兆易创新", "side": "sell",
             "quantity": 100, "price": 220.35, "amount": 22035.0,
             "commission": 5.51, "stamp_tax": 11.02, "transfer_fee": 0.22, "extra_fee": 0.0},
            {"stock_code": "002463", "stock_name": "沪电股份", "side": "sell",
             "quantity": 200, "price": 76.76, "amount": 15352.0,
             "commission": 3.84, "stamp_tax": 7.68, "transfer_fee": 0.15, "extra_fee": 0.0},
            {"stock_code": "002463", "stock_name": "沪电股份", "side": "buy",
             "quantity": 200, "price": 75.25, "amount": 15050.0,
             "commission": 3.76, "stamp_tax": 0.0, "transfer_fee": 0.15, "extra_fee": 0.0},
        ])

    def test_applies_manual_trades_and_removes_sold_stock(self, monkeypatch):
        self._patch(monkeypatch)
        snap = ti.live_positions(None)
        codes = {p["stock_code"]: p for p in snap["positions"]}
        assert "603986" not in codes
        assert codes["002463"]["quantity"] == 500
        # 卖 200 后买回 200 @75.25：成本从 86.228 摊到约 81.845
        assert abs(codes["002463"]["cost_price"] - 81.845) < 0.01
        assert snap["cash"] > 25000

    def test_snapshot_is_the_same_source_as_display(self, monkeypatch):
        self._patch(monkeypatch)
        live = ti.live_snapshot(None)
        assert "603986" not in live["positions"]
        assert live["positions"]["002463"]["qty"] == 500
        assert abs(live["positions"]["002463"]["cost"] - 81.845) < 0.01
