"""today_gate 单测：手工临时记录与交割单当日买入并存时的计数正确性。

回归背景：叠加今日临时成交的循环里 `buys` 变量被同名覆盖，导致返回的
today_buys / today_buy_amount / summary 文案按「最后一票」统计。
"""
import datetime as dt
from datetime import date

from app.trade_review import today_gate

TODAY = date(2026, 11, 28)


def _slip_row(rid, code, name, side, qty, price, day, balance, cash, amount=None):
    amt = amount if amount is not None else qty * price
    return {
        "id": rid, "stock_code": code, "stock_name": name, "side": side,
        "business_name": "证券买入" if side == "buy" else "证券卖出",
        "trade_date": day, "trade_time": "10:00:00",
        "quantity": qty, "price": price, "amount": amt,
        "balance_qty": balance, "clean_amount": amt if side == "sell" else -amt,
        "commission": 5.0, "stamp_tax": 0, "transfer_fee": 0, "extra_fee": 0,
        "cash_balance": cash, "order_no": "", "account_no": "",
        "source_file": "t.xls", "import_batch_id": 1,
    }


def _manual_row(rid, code, name, qty, price):
    return {
        "id": rid, "trade_date": TODAY, "trade_time": "14:00:00",
        "stock_code": code, "stock_name": name, "side": "buy",
        "quantity": qty, "price": price, "amount": qty * price,
        "commission": 5.0, "stamp_tax": 0, "transfer_fee": 0, "extra_fee": 0,
        "stop_price": None, "reason": "", "source": "manual",
    }


class _Result:
    def __init__(self, rows=None, one=False, scalar_val=None):
        self._rows = rows or []
        self._one = one
        self._scalar = scalar_val

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._scalar

    def mappings(self):
        rows = self._rows
        class _M:
            def fetchall(self):
                return rows
            def __iter__(self):
                return iter(rows)
        return _M()


class RoutingDB:
    """按 SQL 关键字路由的最小 FakeDB，覆盖 today_gate 的全部查询路径。"""

    def __init__(self, slip_rows, manual_rows, portfolio_rows, closes):
        self.slip_rows = slip_rows
        self.manual_rows = manual_rows
        self.portfolio_rows = portfolio_rows
        self.closes = closes

    def execute(self, sql, params=None):
        s = str(sql)
        if "FROM user_config" in s:
            return _Result(one=True)
        if "SELECT * FROM trade_slip" in s:
            return _Result(rows=self.slip_rows)
        if "FROM trade_intraday" in s:
            return _Result(rows=self.manual_rows)
        if "FROM portfolio WHERE" in s:
            return _Result(rows=self.portfolio_rows)
        if "FROM daily_quote" in s:
            if "trade_date <" in s:      # _close_before：期初收盘价（缺失→用首行价格）
                return _Result(rows=[])
            # DISTINCT ON 最新收盘；(code, close, trade_date)——_last_close 读 3 列，
            # _last_close_map 只读前 2 列，3 元组两边兼容
            return _Result(rows=[(c, px, TODAY) for c, px in self.closes.items()])
        if "trade_cash_snapshot" in s:
            return _Result(scalar_val=None)
        if "FROM stock_master" in s or "FROM trade_calendar" in s:
            return _Result(rows=[])
        raise AssertionError(f"RoutingDB 未覆盖的 SQL: {s[:160]}")

    def commit(self):
        pass


class TestTodayGateCounts:
    def test_manual_record_not_in_portfolio_keeps_today_counts(self):
        """交割单只到昨天、今日 3 笔全为手工记录（其中 2 票不在持仓预览）→ today_buys 应为 3。

        触发条件：slip_end < today 时才读 trade_intraday；手工票不在 portfolio 里
        走「补建持仓」分支，旧 bug 在该分支把外层 `buys` 覆盖成最后一票的买入行，
        today_buys 报 1、today_buy_amount 只算最后一票。
        """
        slip = [
            _slip_row(1, "600000", "浦发银行", "buy", 100, 10.0, date(2026, 11, 27), 100, 9000.0),
        ]
        manual = [
            _manual_row(9, "600001", "已在持仓", 100, 12.0),
            _manual_row(10, "300001", "手工新票甲", 100, 9.0),
            _manual_row(11, "300002", "手工新票乙", 200, 8.0),
        ]
        portfolio = [
            (1, "600000", "浦发银行", 100, 10.0, "SSE"),
            (2, "600001", "已在持仓", 100, 12.0, "SSE"),
        ]
        closes = {"600000": 10.0, "600001": 12.0, "300001": 9.0, "300002": 8.0}
        db = RoutingDB(slip, manual, portfolio, closes)
        r = today_gate(db, user_id=7, today=TODAY)
        assert r["today_buys"] == 3
        assert abs(r["today_buy_amount"] - (1200 + 900 + 1600)) < 1e-6
        assert r["today_sells"] == 0
        # 3 笔买入 > 默认上限 2 → critical 违规 + 禁开新仓
        assert any(v["rule"] == "max_new_buys_per_day" for v in r["violations"])
        assert r["allow_new_buy"] is False
        assert "今日 3 买" in r["summary"]

    def test_no_manual_records_unchanged(self):
        """交割单覆盖今天时行为不变（回归保护）。"""
        slip = [
            _slip_row(1, "600000", "浦发银行", "buy", 100, 10.0, date(2026, 11, 27), 100, 9000.0),
            _slip_row(2, "600000", "浦发银行", "sell", 100, 11.0, TODAY, 0, 10000.0),
        ]
        db = RoutingDB(slip, [], [(1, "600000", "浦发银行", 100, 10.0, "SSE")],
                       {"600000": 11.0})
        r = today_gate(db, user_id=7, today=TODAY)
        assert r["today_buys"] == 0
        assert r["today_sells"] == 1
