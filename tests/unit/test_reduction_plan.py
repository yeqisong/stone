"""集中度减仓计算器单测：同时满足 70% 仓位与 20% 单票、100 股整手。"""
from app.reduction_plan import build_reduction_plan


def _pos(code, name, qty, price):
    return {"code": code, "name": name, "qty": qty, "price": price}


class TestReductionPlan:
    def test_user_case_top_and_position(self):
        positions = [
            _pos("300274", "阳光电源", 900, 73.9),
            _pos("002463", "沪电股份", 500, 76.0),
            _pos("513780", "港股新药", 18700, 1.53),
        ]
        plan = build_reduction_plan(positions, 25959.0, 0.70, 0.20)
        assert plan["before"]["position_ratio"] > 0.70
        assert plan["before"]["top_weight"] > 0.20
        assert plan["need"] > 30000
        assert plan["rows"][0]["name"] == "阳光电源"
        assert plan["rows"][0]["sell_qty"] >= 500
        assert plan["after"]["position_ratio"] <= 0.70 + 1e-6
        assert plan["after"]["top_weight"] <= 0.20 + 1e-6
        assert plan["binding"] == "single"

    def test_single_cap_only(self):
        positions = [
            _pos("300274", "阳光电源", 400, 75.0),   # 30,000 / 100,000
            _pos("002463", "沪电股份", 400, 75.0),   # 30,000
        ]
        plan = build_reduction_plan(positions, 40000.0, 0.70, 0.20)
        assert plan["before"]["position_ratio"] == 0.60
        assert plan["before"]["top_weight"] == 0.30
        assert plan["rows"][0]["name"] == "阳光电源"
        assert plan["after"]["top_weight"] <= 0.20 + 1e-6
        assert plan["after"]["position_ratio"] < 0.60

    def test_t1_today_buy_is_not_sellable(self):
        positions = [{"code": "600000", "name": "浦发银行", "qty": 1000,
                      "price": 10.0, "sellable_qty": 200}]
        plan = build_reduction_plan(positions, 0.0, 0.50, 1.0)
        row = plan["rows"][0]
        assert row["sell_qty"] == 200
        assert row["t1_qty"] == 800
        assert plan["t1_deferred"] == 8000
        assert plan["after"]["position_ratio"] == 0.80
        assert "T+1" in plan["note"]

    def test_today_buy_qty_argument(self):
        positions = [_pos("600000", "浦发银行", 1000, 10.0)]
        plan = build_reduction_plan(positions, 0.0, 0.50, 1.0,
                                    today_buy_qty={"600000": 900})
        assert plan["rows"][0]["sell_qty"] == 100
        assert plan["t1_deferred"] == 9000

    def test_already_compliant(self):
        positions = [_pos("600000", "浦发银行", 100, 10.0)]
        plan = build_reduction_plan(positions, 9000.0, 0.70, 0.20)
        assert plan["rows"] == []
        assert plan["need"] == 0
        assert plan["binding"] == "none"

    def test_no_positions(self):
        plan = build_reduction_plan([], 10000.0, 0.70, 0.20)
        assert plan["rows"] == []
        assert plan["after"]["position_ratio"] == 0
