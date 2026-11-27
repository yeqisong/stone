"""个人卖出信号（user_signal_history）：端点过滤/用户隔离 + 权限归属。

持仓卖出按用户生成、只落个人表。个股详情页叠加 K 线卖出标记必须走受
signal.personal.view 控制的 /api/signals/personal，不得混进全局
/stock/{code}/signals（research.stock.view，多数角色默认持有）——否则等于绕过权限节点。
"""
import datetime as dt
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app.api import signals, stock
from app.auth import auth
from app.auth.definitions import PERMISSION_NODES, ROLE_PERMISSIONS
from app.auth.route_permissions import resolve_rule
from app.auth.service import Grant, Principal

CODE = "600584"
DAY = dt.date(2026, 11, 27)
# 列序同端点 SELECT：id, signal_date, stock_code, stock_name, direction, strength, price,
#                    reason, model_version, predict_score, status, actual_return, close_reason, created_at
ROW = (7, DAY, CODE, "长电科技", "sell", 2, 120.0,
       "模型分位29.76% < 卖出阈值40%；持仓退出", "v21.0", 0.0123, None, None, None, None)


class _FakeDB:
    def __init__(self, rows=(ROW,)):
        self.rows = rows
        self.sql = None
        self.params = None
        self.closed = False

    def execute(self, sql, params):
        self.sql = " ".join(str(sql).split())
        self.params = params
        return SimpleNamespace(fetchall=lambda: self.rows)

    def close(self):
        self.closed = True


def _call(db, principal, factors=None, **kw):
    """直接调用端点：FastAPI 的 Query 默认值在离线调用时不是 None，必须显式传全。"""
    args = {"signal_date": None, "limit": 200, "stock_code": None, "days": None, **kw}
    keys = []
    factors = {} if factors is None else factors

    def fake_factors(_db, pairs):
        keys.extend(pairs)
        return factors

    with patch.object(signals, "get_sync_db", lambda: db), \
            patch.object(signals, "hfq_to_raw_factors", fake_factors):
        return signals.get_personal_signals(principal=principal, **args), keys


class TestPersonalSignalsEndpoint(unittest.TestCase):
    def test_default_lists_recent_of_current_user_only(self):
        db = _FakeDB()
        res, _ = _call(db, Principal(user_id=1, username="u1"))
        self.assertIn("FROM user_signal_history", db.sql)
        self.assertIn("WHERE user_id=:uid", db.sql)
        self.assertNotIn("stock_code=:c", db.sql)
        self.assertNotIn("signal_date >=", db.sql)
        self.assertEqual(db.params, {"uid": 1, "n": 200})
        self.assertEqual(len(res["items"]), 1)
        self.assertTrue(db.closed)

    def test_stock_code_and_days_filter_signal_history(self):
        db = _FakeDB()
        _call(db, Principal(user_id=1, username="u1"), stock_code=CODE, days=1095, limit=1000)
        self.assertIn("AND stock_code=:c", db.sql)
        self.assertIn("AND signal_date >= CURRENT_DATE - :days * INTERVAL '1 day'", db.sql)
        self.assertEqual(db.params, {"uid": 1, "n": 1000, "c": CODE, "days": 1095})

    def test_filters_compose_with_signal_date(self):
        db = _FakeDB()
        _call(db, Principal(user_id=1, username="u1"), signal_date="2026-11-27", stock_code=CODE, days=1095)
        for frag in ("AND signal_date=:d", "AND stock_code=:c", "signal_date >= CURRENT_DATE"):
            self.assertIn(frag, db.sql, frag)
        self.assertEqual(db.params["d"], "2026-11-27")

    def test_scoped_to_calling_user(self):
        a, b = _FakeDB(), _FakeDB()
        _call(a, Principal(user_id=1, username="a"), stock_code=CODE)
        _call(b, Principal(user_id=2, username="b"), stock_code=CODE)
        self.assertEqual((a.params["uid"], b.params["uid"]), (1, 2))

    def test_support_session_reads_target_user(self):
        db = _FakeDB()
        _call(db, Principal(user_id=9, username="support", support_target_id=2), stock_code=CODE)
        self.assertEqual(db.params["uid"], 2)

    def test_real_price_converted_from_hfq(self):
        res, keys = _call(_FakeDB(), Principal(user_id=1, username="u1"),
                          factors={(CODE, "2026-11-27"): 0.5})
        self.assertEqual(keys, [(CODE, DAY)])
        item = res["items"][0]
        self.assertEqual(item["price"], 120.0)          # 原值不动：既有调用方不受影响
        self.assertEqual(item["real_price"], 60.0)
        self.assertEqual((item["stock_code"], item["signal_date"], item["direction"], item["strength"]),
                         (CODE, "2026-11-27", "sell", 2))

    def test_real_price_never_falls_back_to_hfq(self):
        # 缺当日行情：宁可不显示，也不能把后复权价当真实价给界面
        res, _ = _call(_FakeDB(), Principal(user_id=1, username="u1"), factors={})
        self.assertIsNone(res["items"][0]["real_price"])

    def test_empty_history(self):
        res, _ = _call(_FakeDB(rows=[]), Principal(user_id=1, username="u1"), stock_code="000001")
        self.assertEqual(res["items"], [])


class TestPersonalSignalPermission(unittest.TestCase):
    def test_route_owned_by_personal_signal_node(self):
        self.assertEqual(resolve_rule("GET", "/api/signals/personal").permission,
                         "signal.personal.view")

    def test_global_stock_signals_endpoint_stays_global(self):
        # 个人卖出只能经个人端点下发；全局端点源码不得读个人表
        rule = resolve_rule("GET", f"/api/stock/{CODE}/signals")
        self.assertIsNotNone(rule)
        self.assertEqual(rule.permission, "research.stock.view")
        for fn in (stock.get_stock_signals, stock.get_stock_detail):
            self.assertNotIn("user_signal_history", inspect.getsource(fn), fn.__name__)

    def test_role_matrix_grants(self):
        for role in ("admin", "trader"):
            self.assertIn(("signal.personal.view", "self", False),
                          set(ROLE_PERMISSIONS[role]), role)
        for role in ("operator", "analyst", "viewer"):
            self.assertNotIn("signal.personal.view",
                             {code for code, _s, _so in ROLE_PERMISSIONS[role]}, role)
        self.assertIn(("signal.personal.view", "self", True),
                      set(ROLE_PERMISSIONS["support"]))

    def test_grant_always_comes_with_portfolio_view(self):
        # 持仓页「今日信号」与详情页卖出标记同源，两个入口必须同进同退
        for role, grants in ROLE_PERMISSIONS.items():
            codes = {code for code, _s, _so in grants}
            if "signal.personal.view" in codes:
                self.assertIn("portfolio.position.view", codes, role)

    def test_node_parent_and_double_control_note(self):
        node = next(n for n in PERMISSION_NODES if n["code"] == "signal.personal.view")
        self.assertEqual(node["parent"], "portfolio.position.view")
        stock_node = next(n for n in PERMISSION_NODES if n["code"] == "research.stock.view")
        self.assertIn("signal.personal.view", stock_node["note"])

    def test_support_only_grant_needs_support_session(self):
        grants = {"signal.personal.view": Grant("signal.personal.view", "self", True)}
        self.assertFalse(Principal(user_id=5, username="support", permissions=grants).can("signal.personal.view"))
        in_session = Principal(user_id=5, username="support", support_target_id=9, permissions=grants)
        self.assertTrue(in_session.can("signal.personal.view"))
        self.assertEqual(in_session.data_user_id, 9)


def _request(path: str) -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "query_string": b"", "headers": [],
             "scheme": "http", "server": ("testserver", 80), "root_path": ""}
    return Request(scope)


class TestPersonalSignalHttpAuthz(unittest.TestCase):
    """RBAC 中间件调用的授权核心（app.auth.auth.authorize_http_request）：无权 403、有权放行。"""

    def _authorize(self, path: str, principal: Principal):
        with patch.object(auth, "_extract_principal", lambda _req, _cred: principal):
            return auth.authorize_http_request(_request(path))

    def test_role_without_grant_gets_403(self):
        analyst = Principal(user_id=3, username="analyst",
                            permissions={"research.stock.view": Grant("research.stock.view")})
        with self.assertRaises(HTTPException) as ctx:
            self._authorize("/api/signals/personal", analyst)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("signal.personal.view", ctx.exception.detail)

    def test_role_with_grant_passes(self):
        trader = Principal(user_id=4, username="trader",
                           permissions={"signal.personal.view": Grant("signal.personal.view")})
        self.assertIs(self._authorize("/api/signals/personal", trader), trader)

    def test_requires_login(self):
        with self.assertRaises(HTTPException) as ctx:
            auth.authorize_http_request(_request("/api/signals/personal"))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_global_signals_endpoint_is_open_to_stock_view(self):
        # 正因为没有个人信号权限的角色也能读全局信号端点，个人卖出才不能并入它
        analyst = Principal(user_id=3, username="analyst",
                            permissions={"research.stock.view": Grant("research.stock.view")})
        self.assertIs(self._authorize(f"/api/stock/{CODE}/signals", analyst), analyst)
        with self.assertRaises(HTTPException):
            self._authorize("/api/signals/personal", analyst)


if __name__ == "__main__":
    unittest.main()
