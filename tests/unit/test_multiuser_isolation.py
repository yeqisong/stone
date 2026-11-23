"""多用户/RBAC 隔离回归测试（不依赖真实数据库）。

覆盖：
- 管理员默认拥有全部非 public 权限节点（用户明确要求）
- 路由 → 权限码注册表：公开/登录/后台/Agent/飞书
- Agent 技能：个人命名空间隔离、全局技能只读、内置不可覆盖
- Agent 工具清单：非管理员看不到原始 SQL/代码执行
- 飞书：open_id 提取、历史 key 按用户隔离
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import HTTPException

from app.agent import skills as S
from app.agent.tools import ADMIN_ONLY_TOOLS, _sql_table_error, tool_specs_for
from app.api.admin import _assert_can_grant_codes
from app.auth.definitions import PERMISSION_CODES, PUBLIC_PERMISSION_CODES, ROLE_PERMISSIONS
from app.auth.route_permissions import resolve_rule
from app.auth.service import Grant, Principal
from app.feishu.identity import extract_open_id, history_key


class TestAdminPermissions(unittest.TestCase):
    def test_admin_has_all_non_public_nodes(self):
        granted = {code for code, _scope, _support in ROLE_PERMISSIONS["admin"]}
        expected = set(PERMISSION_CODES) - set(PUBLIC_PERMISSION_CODES)
        self.assertEqual(granted, expected,
                         f"admin 缺少 {sorted(expected - granted)}；多出 {sorted(granted - expected)}")

    def test_cb_menu_permission_exists(self):
        self.assertIn("research.cb.view", PERMISSION_CODES)
        rule = resolve_rule("GET", "/api/cb/list")
        self.assertEqual(rule.permission, "research.cb.view")
        self.assertEqual(resolve_rule("GET", "/api/stock/600519/convertible-bonds").permission,
                         "research.cb.view")
        for role in ("admin", "operator", "trader", "analyst", "viewer"):
            grants = set(ROLE_PERMISSIONS[role])
            self.assertIn(("research.cb.view", "global", False), grants, role)

    def test_route_registry(self):
        self.assertTrue(resolve_rule("POST", "/api/login").public)
        self.assertTrue(resolve_rule("POST", "/webhook/feishu").public)
        self.assertTrue(resolve_rule("GET", "/api/me").auth_only)
        self.assertEqual(resolve_rule("GET", "/api/admin/users").permission,
                         "system.user.manage")
        self.assertEqual(resolve_rule("GET", "/api/agent/sessions").permission,
                         "agent.use")
        self.assertEqual(resolve_rule("POST", "/api/portfolio").permission,
                         "portfolio.position.edit")
        # 未知路由没有规则 → authorize_request 默认拒绝
        self.assertIsNone(resolve_rule("GET", "/api/no-such-endpoint"))


class TestRoleGrantGuard(unittest.TestCase):
    def _principal(self, codes, *, admin=False):
        perms = {c: Grant(c) for c in codes}
        return Principal(user_id=2, username="ops", permissions=perms,
                         role_codes=frozenset({"admin"} if admin else {"ops"}))

    def test_cannot_grant_new_permission(self):
        actor = self._principal({"system.role.manage"})
        with self.assertRaises(HTTPException) as ctx:
            _assert_can_grant_codes(actor, ["system.role.manage", "system.user.manage"])
        self.assertEqual(ctx.exception.status_code, 403)

    def test_can_grant_own_permission(self):
        actor = self._principal({"system.role.manage"})
        _assert_can_grant_codes(actor, ["system.role.manage"])

    def test_admin_bypass(self):
        actor = self._principal(set(), admin=True)
        _assert_can_grant_codes(actor, ["system.user.manage", "model.approve"])

    def test_support_only_not_effective_outside_support(self):
        actor = Principal(
            user_id=2, username="support", role_codes=frozenset({"support"}),
            permissions={"portfolio.position.view": Grant("portfolio.position.view",
                                                          support_only=True)},
        )
        with self.assertRaises(HTTPException):
            _assert_can_grant_codes(actor, ["portfolio.position.view"])


class TestSkillNamespace(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._old = (S.USER_DIR, S.GLOBAL_DIR, S.LEGACY_DIR, S._TMP)
        S.USER_DIR = self.root
        S.GLOBAL_DIR = self.root / "global"
        S.LEGACY_DIR = self.root
        S._TMP = self.root / ".import-tmp"
        S.GLOBAL_DIR.mkdir()

    def tearDown(self):
        S.USER_DIR, S.GLOBAL_DIR, S.LEGACY_DIR, S._TMP = self._old
        self.tmp.cleanup()

    def test_personal_skills_isolated(self):
        self.assertTrue(S.save_skill("a-only", "A 的技能", "A body", user_id=101)["ok"])
        self.assertTrue(S.save_skill("b-only", "B 的技能", "B body", user_id=102)["ok"])
        names_a = {x["name"] for x in S.list_skills(101)}
        names_b = {x["name"] for x in S.list_skills(102)}
        self.assertIn("a-only", names_a)
        self.assertNotIn("b-only", names_a)
        self.assertIn("b-only", names_b)
        self.assertNotIn("a-only", names_b)
        self.assertTrue(S.read_skill("b-only", user_id=101).get("error"))
        self.assertEqual(S.read_skill("b-only", user_id=102)["content"], "B body")
        self.assertTrue(S.delete_skill("b-only", user_id=101).get("error"))
        self.assertTrue(S.delete_skill("b-only", user_id=102)["ok"])

    def test_global_skill_readonly_for_users(self):
        d = S.GLOBAL_DIR / "global-demo"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: global-demo\ndescription: 管理员导入的全局技能\n---\n\n全局正文",
            encoding="utf-8")
        sk = {x["name"]: x for x in S.list_skills(101)}["global-demo"]
        self.assertTrue(sk["global"] and sk["readonly"])
        self.assertIn("全局正文", S.read_skill("global-demo", user_id=101)["content"])
        self.assertTrue(S.save_skill("global-demo", "覆盖", "x", user_id=101).get("error"))
        self.assertTrue(S.delete_skill("global-demo", user_id=101).get("error"))

    def test_builtin_cannot_be_overwritten(self):
        self.assertTrue(S.save_skill("stock-deep-dive", "覆盖内置", "x", user_id=101).get("error"))


class TestAgentSqlWhitelist(unittest.TestCase):
    def test_shared_table_allowed(self):
        self.assertIsNone(_sql_table_error(
            "SELECT trade_date, close_hfq FROM daily_quote WHERE stock_code='600519'"))

    def test_personal_table_rejected(self):
        for t in ("portfolio", "watch_groups", "risk_alerts", "agent_sessions",
                  "users", "trade_slip", "user_config"):
            self.assertIn("个人/敏感表", _sql_table_error(f"SELECT * FROM {t}"))

    def test_join_and_subquery_personal_rejected(self):
        self.assertIsNotNone(_sql_table_error(
            "SELECT q.close FROM daily_quote q JOIN portfolio p ON p.stock_code=q.stock_code"))
        self.assertIsNotNone(_sql_table_error(
            "SELECT * FROM (SELECT * FROM risk_alerts) x"))

    def test_cte_allowed_and_cte_name_not_treated_as_table(self):
        self.assertIsNone(_sql_table_error(
            "WITH x AS (SELECT stock_code FROM daily_quote) SELECT * FROM x"))
        self.assertIsNotNone(_sql_table_error(
            "WITH x AS (SELECT * FROM portfolio) SELECT * FROM x"))

    def test_other_schema_and_functions_rejected(self):
        self.assertIsNotNone(_sql_table_error("SELECT * FROM pg_catalog.pg_class"))
        self.assertIsNotNone(_sql_table_error("SELECT pg_read_file('/etc/passwd')"))

    def test_file_advisory_and_backend_functions_rejected(self):
        for expr in (
            "pg_read_binary_file('/etc/passwd')",
            "pg_ls_logdir()", "pg_ls_waldir()", "pg_ls_tmpdir()",
            "pg_advisory_lock(1)", "pg_advisory_xact_lock_shared(1)",
            "pg_try_advisory_lock(1)", "pg_advisory_unlock_all()",
            "pg_terminate_backend(123)", "pg_cancel_backend(123)",
            "pg_sleep_for('1s')", "pg_sleep_until(now())",
            "lo_get(1)", "lo_put(1, 'x')", "dblink_connect('x')",
            "pg_switch_wal()", "pg_create_restore_point('x')",
        ):
            self.assertIsNotNone(_sql_table_error(f"SELECT {expr}"), expr)


class TestAgentToolGate(unittest.TestCase):
    def test_admin_only_tools_filtered(self):
        names = {t["function"]["name"] for t in tool_specs_for(False)}
        self.assertFalse(names & ADMIN_ONLY_TOOLS)
        names_admin = {t["function"]["name"] for t in tool_specs_for(True)}
        self.assertTrue(ADMIN_ONLY_TOOLS <= names_admin)

    def test_no_admin_tool_in_user_list(self):
        for t in tool_specs_for(False):
            self.assertNotIn(t["function"]["name"],
                             {"query_sql", "run_python", "run_node", "pip_install", "npm_install"})


class TestFeishuIdentityHelpers(unittest.TestCase):
    def test_extract_open_id_shapes(self):
        self.assertEqual(extract_open_id({
            "event": {"sender": {"sender_id": {"open_id": "ou_a"}}},
        }), "ou_a")
        self.assertEqual(extract_open_id({"open_id": "ou_b"}), "ou_b")
        self.assertEqual(extract_open_id({
            "event": {"operator": {"open_id": "ou_c"}},
        }), "ou_c")
        self.assertEqual(extract_open_id({}), "")

    def test_history_key_per_user(self):
        self.assertNotEqual(history_key(1), history_key(2))
        self.assertEqual(history_key(7), "feishu:u7")


if __name__ == "__main__":
    unittest.main()
