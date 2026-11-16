"""实时仓位护栏纯逻辑单测（无 DB）：费用估算、最大可买、现金/成本更新。"""
from app.trade_intraday import _apply_manual, _estimate_fees, _max_buy_qty


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
