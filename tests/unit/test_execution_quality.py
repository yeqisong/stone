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


from datetime import timedelta

# ── alert_execution：一笔卖出只能核销一条提醒 ──

class _R:
    def __init__(self, rows=None, one=False):
        self._rows = rows or []
        self._one = one

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def mappings(self):
        rows = self._rows
        class _M:
            def fetchall(self):
                return rows
        return _M()


class _DB:
    """按 SQL 关键字路由的 FakeDB，覆盖 alert_execution 的查询路径。"""

    def __init__(self, alerts, sells, day_close=10.0):
        self.alerts = alerts
        self.sells = sells
        self.day_close = day_close

    def execute(self, sql, params=None):
        s = str(sql)
        if "FROM risk_alerts" in s:
            return _R(rows=self.alerts)
        if "UNION ALL" in s:
            return _R(rows=self.sells)
        if "FROM stock_master" in s:
            return _R(rows=[])
        if "FROM stock_minute_bar" in s or "FROM stock_treemap_intraday" in s:
            return _R(rows=[])
        if "FROM daily_quote" in s:
            return _R(rows=[(self.day_close, None, None)], one=True)
        raise AssertionError(f"_DB 未覆盖的 SQL: {s[:160]}")

    def commit(self):
        pass


class TestAlertExecutionConsume:
    def _alert(self, aid, hh, mm):
        from datetime import datetime
        return {
            "id": aid, "trade_date": DAY,
            "created_at": datetime(2026, 11, 18, hh, mm, 0),
            "stock_code": "600000", "kind": "it_stop",
            "title": "止损提醒", "body": "现价 10.00",
            "trigger_price": 10.0,
        }

    def _sell(self, hh, mm, price=9.5):
        from datetime import datetime
        return {
            "trade_date": DAY, "trade_time": datetime(2026, 11, 18, hh, mm, 0),
            "stock_code": "600000", "stock_name": "浦发银行",
            "quantity": 100, "price": price, "amount": price * 100, "src": "slip",
        }

    def test_two_alerts_one_sell_executes_only_one(self):
        """同天同票 2 条止损提醒 + 1 笔卖出 → 只有 1 条「已卖」，执行率 1/2。

        旧 bug：每条提醒独立匹配「提醒后第一笔卖出」且不核销，
        2 条提醒全部标已卖，执行率虚报 100%。
        """
        db = _DB(
            alerts=[self._alert(2, 11, 0), self._alert(1, 10, 0)],  # created_at DESC
            sells=[self._sell(11, 30)],
        )
        r = eq.alert_execution(db, user_id=7, start=DAY - timedelta(days=1))
        executed = [x for x in r["rows"] if x["executed"]]
        assert len(executed) == 1
        assert executed[0]["alert_id"] == 2          # 11:00 的提醒由 11:30 卖出核销
        assert r["summary"]["executed"] == 1
        assert r["summary"]["missed"] == 1
        assert abs(r["summary"]["execution_rate"] - 0.5) < 1e-9

    def test_sell_before_alert_does_not_match(self):
        """提醒之前的卖出不能核销该提醒。"""
        db = _DB(alerts=[self._alert(1, 14, 0)], sells=[self._sell(10, 0)])
        r = eq.alert_execution(db, user_id=7, start=DAY - timedelta(days=1))
        assert r["rows"][0]["executed"] is False
        assert r["summary"]["executed"] == 0
