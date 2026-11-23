"""个人实盘风控规则评估：止损/行业上限只写 owner 名下告警。"""
import json
import unittest

from app.risk import evaluate_user_risk_rules
from app.auth.route_permissions import resolve_rule


class _R:
    def __init__(self, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    def __init__(self):
        self.inserts = []

    def execute(self, sql, params=None):
        s = str(sql)
        if "FROM user_config WHERE user_id=:uid" in s:
            return _R(rows=[(json.dumps({"enabled": True, "stop_loss_pct": 0.08,
                                         "industry_cap_pct": 0.45,
                                         "max_alerts_per_day": 5}), True)])
        if "SELECT count(*) FROM risk_alerts" in s:
            return _R(scalar=0)
        if "FROM portfolio p" in s and "industry_l1" not in s:
            return _R(rows=[("600000", "测试股", 100, 10.0, 8.0)])
        if "industry_l1" in s:
            return _R(rows=[("银行", 800.0), ("科技", 200.0)])
        if "SELECT id FROM risk_alerts" in s:
            return _R(rows=[])
        if "INSERT INTO risk_alerts" in s:
            self.inserts.append(dict(params))
            return _R(rows=[(1,)])
        return _R()

    def commit(self):
        pass

    def rollback(self):
        pass


class TestPersonalRisk(unittest.TestCase):
    def test_stop_loss_and_industry_cap_are_user_scoped(self):
        db = _FakeDB()
        alerts = evaluate_user_risk_rules(db, 42, "2026-01-05")
        kinds = {a["kind"] for a in alerts}
        self.assertEqual(kinds, {"stop_loss", "industry_cap"})
        self.assertTrue(all(a["user_id"] == 42 for a in alerts))
        self.assertTrue(all(i["uid"] == 42 for i in db.inserts))
        self.assertIn("8.00", db.inserts[0]["b"])
        self.assertIn("10.00", db.inserts[0]["b"])

    def test_route_requires_rule_edit_permission(self):
        rule = resolve_rule("POST", "/api/risk/evaluate-personal")
        self.assertEqual(rule.permission, "risk.rules.edit")


if __name__ == "__main__":
    unittest.main()
