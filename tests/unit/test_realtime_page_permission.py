"""实时页（盘中作战台）容器节点的权限矩阵：有权进入 + 无权分区 403/隐藏。

design/14：`research.realtime.view` 只管一级入口；页内三个分区不新增端点，
继续由 research.market.view / research.treemap.view / research.radar.view 二次控制。
"""
import unittest

from app.auth.definitions import (
    PERSONAL_PERMISSION_CODES,
    PERMISSION_CODES,
    PERMISSION_NODES,
    ROLE_PERMISSIONS,
)
from app.auth.route_permissions import ROUTE_RULES, resolve_rule
from app.auth.service import Grant, Principal

PAGE = "research.realtime.view"
ZONE_NODES = ("research.market.view", "research.treemap.view", "research.radar.view")
# 分区端点 → 既有节点（容器页不得截胡，也不得放宽）
ZONE_ROUTES = (
    ("GET", "/api/market/realtime", "research.market.view"),
    ("GET", "/api/market/realtime/zt-history", "research.market.view"),
    ("GET", "/api/treemap_intraday", "research.treemap.view"),
    ("GET", "/api/stock/radar", "research.radar.view"),
    ("GET", "/api/buy/check", "research.radar.view"),
    ("GET", "/api/buy/candidates", "research.radar.view"),
)
SYSTEM_ROLES = ("admin", "operator", "trader", "analyst", "viewer")


def _principal(*codes, support_only=False):
    return Principal(user_id=7, username="u7",
                     permissions={c: Grant(c, "global", support_only) for c in codes})


class TestRealtimePageNode(unittest.TestCase):
    def test_node_is_top_level_menu(self):
        node = next(n for n in PERMISSION_NODES if n["code"] == PAGE)
        self.assertEqual(node["type"], "menu")
        self.assertIsNone(node["parent"])
        self.assertEqual(node["route"], "rt")
        # 全市场共享容器页：不属于个人数据，支持模式也不借它进入
        self.assertNotIn(PAGE, PERSONAL_PERMISSION_CODES)
        self.assertFalse(PAGE.startswith("public."))

    def test_container_owns_no_route(self):
        # 只授容器码的角色拿不到任何数据：没有任何端点归容器码
        owners = {rule.permission for rule in ROUTE_RULES}
        self.assertNotIn(PAGE, owners)
        self.assertIn(PAGE, PERMISSION_CODES)

    def test_zone_routes_stay_on_zone_nodes(self):
        for method, path, perm in ZONE_ROUTES:
            rule = resolve_rule(method, path)
            self.assertIsNotNone(rule, path)
            self.assertEqual(rule.permission, perm, path)

    def test_zone_nodes_declare_double_control(self):
        notes = {n["code"]: n.get("note", "") for n in PERMISSION_NODES}
        # 容器节点点名全部分区节点，分区节点回指页面入口——两处任一漏改都会在这里暴露
        for zone in ZONE_NODES:
            self.assertIn(zone, notes[PAGE], zone)
            self.assertIn("#/rt", notes[zone], zone)


class TestRealtimePageRoleMatrix(unittest.TestCase):
    def test_default_roles_grant_page(self):
        for role in SYSTEM_ROLES:
            self.assertIn((PAGE, "global", False), set(ROLE_PERMISSIONS[role]), role)
        # 支持人员只借支持会话看个人数据，没有共享研究面，也不进实时页
        self.assertNotIn(PAGE, {c for c, _s, _so in ROLE_PERMISSIONS["support"]})

    def test_no_support_only_grant(self):
        for role, grants in ROLE_PERMISSIONS.items():
            for code, _scope, support_only in grants:
                if code == PAGE:
                    self.assertFalse(support_only, role)

    def test_page_never_granted_without_a_zone(self):
        # 默认矩阵不允许出现「看得到空壳」的角色：授了入口至少要有一个分区
        for role, grants in ROLE_PERMISSIONS.items():
            codes = {code for code, _s, _so in grants}
            if PAGE in codes:
                self.assertTrue(set(ZONE_NODES) & codes, role)

    def test_support_cannot_reach_zone_apis(self):
        codes = {code for code, _s, _so in ROLE_PERMISSIONS["support"]}
        self.assertFalse({PAGE, *ZONE_NODES} & codes)


class TestRealtimePageDoubleRole(unittest.TestCase):
    """有权通过 + 无权 403/隐藏。"""

    def test_trader_style_grant_passes_everywhere(self):
        p = _principal(PAGE, *ZONE_NODES)
        self.assertTrue(p.can(PAGE))
        for zone in ZONE_NODES:
            self.assertTrue(p.can(zone), zone)

    def test_page_only_grant_is_an_empty_shell(self):
        # 只授容器码：入口放行（页面能打开），但每个分区端点仍按既有节点拒绝 → 403，
        # 前端对应分区不渲染
        p = _principal(PAGE)
        self.assertTrue(p.can(PAGE))
        for zone in ZONE_NODES:
            self.assertFalse(p.can(zone), zone)
        for method, path, perm in ZONE_ROUTES:
            rule = resolve_rule(method, path)
            self.assertFalse(p.can(rule.permission), path)

    def test_zone_without_page_cannot_enter_but_keeps_zone_apis(self):
        # 老角色（未授新码，如自定义角色/重启播种前）：分区 API 照常可用，只是一级入口不显示
        p = _principal(*ZONE_NODES)
        self.assertFalse(p.can(PAGE))
        for zone in ZONE_NODES:
            self.assertTrue(p.can(zone), zone)


if __name__ == "__main__":
    unittest.main()
