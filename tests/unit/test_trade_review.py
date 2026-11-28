"""交易复盘纯逻辑单测：FIFO 回合、送股零成本、外部入金剔除。"""
import datetime as dt

from app.trade_review import (
    _apply_twr,
    _behavior,
    _drawdown,
    _equity_curve,
    _match_rows,
    _worst_avgdown,
)

D0 = dt.date(2026, 1, 2)
D1 = dt.date(2026, 1, 5)
D2 = dt.date(2026, 1, 6)


def _row(code, side, qty, price, day, fee=0.0, amount=None, balance=None,
         subtype="", clean=None, cash=None, name=None):
    return {
        "stock_code": code, "stock_name": name or code, "side": side, "subtype": subtype,
        "trade_date": day, "quantity": qty, "price": price,
        "amount": amount if amount is not None else qty * price,
        "balance_qty": balance,
        "clean_amount": clean if clean is not None else (qty * price if side == "sell" else -qty * price),
        "commission": fee, "stamp_tax": 0, "transfer_fee": 0, "extra_fee": 0,
        "cash_balance": cash,
    }


class TestMatchRows:
    def test_round_trip_fifo(self):
        state0 = {}
        rows = [
            _row("600000", "buy", 100, 10, D0, fee=5, balance=100, cash=0),
            _row("600000", "sell", 100, 12, D1, fee=5, balance=0, cash=1195),
        ]
        m = _match_rows(rows, state0, {})
        closed = [e for e in m["episodes"] if e["closed"]]
        assert len(closed) == 1
        # 买 1005 + 卖费 5 → 1200 - 1005 - 5
        assert abs(closed[0]["pnl"] - 190) < 1e-6
        assert closed[0]["hold_days"] == 3
        assert abs(m["realized_total"] - 190) < 1e-6

    def test_bonus_lot_is_zero_cost(self):
        # 100 股 @10 + 送 40 股；卖 140 @10 应只赚 400，不能把红股按 25 元成本算成亏
        state0 = {}
        rows = [
            _row("600000", "buy", 100, 10, D0, balance=100),
            _row("600000", "other", 40, 0, D0, amount=0, balance=140, subtype="bonus"),
            _row("600000", "sell", 140, 10, D1, balance=0),
        ]
        m = _match_rows(rows, state0, {})
        closed = [e for e in m["episodes"] if e["closed"]]
        assert len(closed) == 1
        assert abs(closed[0]["pnl"] - 400) < 1e-6

    def test_seeded_episode_uses_start_market(self):
        state0 = {"600000": {"qty": 100, "avg": 10, "realized": 0, "estimated": True}}
        rows = [_row("600000", "sell", 100, 12, D1, fee=5, balance=0)]
        m = _match_rows(rows, state0, {})
        closed = [e for e in m["episodes"] if e["closed"]]
        assert closed[0]["seeded"] is True
        assert abs(closed[0]["pnl"] - 195) < 1e-6

    def test_add_to_loser_case(self):
        state0 = {}
        rows = [
            _row("600000", "buy", 100, 10, D0, balance=100),
            _row("600000", "buy", 100, 8, D1, balance=200),   # 向下加仓
            _row("600000", "buy", 100, 12, D2, balance=300),  # 向上加仓
        ]
        m = _match_rows(rows, state0, {})
        assert len(m["add_loss"]) == 1
        assert len(m["add_win"]) == 1
        assert m["add_loss"][0] == 800
        wd = _worst_avgdown(m["add_loss_detail"])
        assert wd["code"] == "600000"
        assert wd["count"] == 1


class TestEquityCurve:
    def test_external_deposit_excluded_from_adjusted(self):
        state0 = {"600000": {"qty": 100, "avg": 10, "realized": 0, "estimated": True}}
        closes = {"600000": {D0: 10, D1: 10, D2: 10}}
        rows = [
            # 分红 100：现金 1000 → 1100，不是外部资金
            _row("600000", "other", 0, 0, D1, amount=0, subtype="dividend",
                 clean=100, cash=1100),
            # 现金 1100 → 2100 但清算金额 0：外部入金 1000
            _row("600000", "other", 0, 0, D2, amount=0, subtype="tax",
                 clean=0, cash=2100),
        ]
        points, flows = _equity_curve(rows, state0, 1000, [D0, D1, D2], closes, {})
        assert len(points) == 3
        assert points[0]["equity"] == 2000
        assert points[1]["equity"] == 2100
        assert points[2]["equity"] == 3100
        assert points[2]["adjusted"] == 2100
        assert flows == [{"date": D2.isoformat(), "amount": 1000.0}]

    def test_sell_updates_position_and_cash(self):
        state0 = {"600000": {"qty": 100, "avg": 10, "realized": 0, "estimated": True}}
        closes = {"600000": {D0: 10, D1: 12}}
        rows = [_row("600000", "sell", 100, 12, D1, clean=1195, cash=1195, balance=0)]
        points, flows = _equity_curve(rows, state0, 0, [D0, D1], closes, {})
        assert points[0]["equity"] == 1000
        assert points[1]["equity"] == 1195
        assert points[1]["n_positions"] == 0
        assert flows == []


class TestTWR:
    def test_deposit_does_not_create_return(self):
        # 期初 1000；第 1 天赚 10%；第 2 天入金 1000（当天无盈亏）；
        # 第 3 天赚 15.238%。入金不能算收益，TWR = 1.1 × 1.15238 - 1
        eq = [
            {"date": D0, "equity": 1100.0, "flow": 0.0, "adjusted": 0},
            {"date": D1, "equity": 2100.0, "flow": 1000.0, "adjusted": 0},
            {"date": D2, "equity": 2420.0, "flow": 0.0, "adjusted": 0},
        ]
        r = _apply_twr(eq, 1000.0)
        assert abs(r - (1.1 * (2420 / 2100) - 1)) < 1e-6
        # 每个点改写为净值：入金当天净值不变
        assert abs(eq[0]["adjusted"] - 1100.0) < 1e-6
        assert abs(eq[1]["adjusted"] - 1100.0) < 1e-6

    def test_withdrawal_does_not_create_loss(self):
        # 期初 1000；第 1 天出金 500（当天无盈亏）→ 权益 500，净值仍 1000
        eq = [{"date": D0, "equity": 500.0, "flow": -500.0, "adjusted": 0}]
        r = _apply_twr(eq, 1000.0)
        assert abs(r) < 1e-9
        assert abs(eq[0]["adjusted"] - 1000.0) < 1e-9


class TestDrawdown:
    def test_max_drawdown(self):
        pts = [{"date": D0, "equity": 100}, {"date": D1, "equity": 120},
               {"date": D2, "equity": 60}]
        d = _drawdown(pts)
        assert abs(d["max_drawdown"] - 0.5) < 1e-9
        assert d["peak_date"] == D1.isoformat()
        assert d["trough_date"] == D2.isoformat()


class TestBehavior:
    def test_basic_stats(self):
        state0 = {"600000": {"qty": 100, "avg": 10, "realized": 0, "estimated": True}}
        rows = [
            _row("600000", "sell", 100, 12, D1, fee=5, balance=0, name="浦发银行"),
            _row("000001", "buy", 100, 10, D1, fee=5, balance=100, name="平安银行"),
            _row("000001", "sell", 100, 8, D2, fee=5, balance=0, name="平安银行"),
        ]
        m = _match_rows(rows, state0, {})
        equity = [
            {"date": D0, "equity": 2000, "adjusted": 2000, "n_positions": 1, "max_weight": 0.5},
            {"date": D1, "equity": 2100, "adjusted": 2100, "n_positions": 1, "max_weight": 0.5},
            {"date": D2, "equity": 1900, "adjusted": 1900, "n_positions": 0, "max_weight": 0.0},
        ]
        closes = {"600000": {D0: 10, D1: 12, D2: 13},
                  "000001": {D0: 10, D1: 10, D2: 8}}
        bars = {("000001", D1): (10, 11, 9)}
        b = _behavior(rows, m, closes, bars, equity)
        assert b["closed_episodes"] == 2
        assert b["win_rate"] == 0.5
        assert abs(b["profit_factor"] - round(195 / 210, 2)) < 1e-9
        assert b["trades_per_day"] == 1.5
        assert b["max_positions"] == 1
        assert b["worst_daily_return"] is not None
        # 只有 1 个未来交易日，5 日/10 日/20 日卖飞检验样本不足
        assert b["post_sell_5d"] is None
        assert b["chase_position"] == 0.5


class TestRebuyWindow:
    """回补冷却窗口必须跟随 cfg no_rebuy_days（与 today_gate/纪律规则同一口径）。"""

    def _rebuy_rows(self, gap_days):
        sell_d = dt.date(2026, 1, 5)
        buy_d = sell_d + dt.timedelta(days=gap_days)
        rows = [
            _row("600000", "sell", 100, 12, sell_d, fee=0, balance=0),
            _row("600000", "buy", 100, 10, buy_d, fee=0, balance=100),
        ]
        return rows

    def _beh(self, gap_days, rebuy_days):
        rows = self._rebuy_rows(gap_days)
        m = _match_rows(rows, {}, {})
        equity = [{"date": dt.date(2026, 1, 5), "equity": 1200, "adjusted": 1200,
                   "n_positions": 0, "max_weight": 0.0}]
        return _behavior(rows, m, {}, {}, equity, rebuy_days=rebuy_days)

    def test_gap6_counts_with_window7(self):
        b = self._beh(6, 7)
        assert b["rebuy_within_window"] == 1
        assert b["rebuy_window_days"] == 7

    def test_gap6_not_counted_with_window5(self):
        # 用户配置 5 日冷却：第 6 天回补不计入（旧硬编码 7 天会误计）
        b = self._beh(6, 5)
        assert b["rebuy_within_window"] == 0
        assert b["rebuy_window_days"] == 5

    def test_gap4_counted_with_window5(self):
        b = self._beh(4, 5)
        assert b["rebuy_within_window"] == 1
