"""Agent 工具与沙箱的边界测试（不依赖 LLM，可离线跑）。

覆盖：只读 SQL 的写入/多语句拦截、参数校验、沙箱 Python/Node 执行与依赖隔离。
DB 相关用例在无库时跳过。
"""
import unittest

from app.agent import sandbox
from app.agent.tools import execute_tool


class TestQuerySqlGuard(unittest.TestCase):
    """query_sql 必须在执行前拦掉写操作与多语句。"""

    def test_reject_write(self):
        for sql in ("DELETE FROM portfolio", "UPDATE portfolio SET quantity=0",
                    "INSERT INTO portfolio (stock_code) VALUES ('1')",
                    "DROP TABLE portfolio", "  -- x\nDELETE FROM portfolio"):
            r = execute_tool(0, "query_sql", {"sql": sql})
            self.assertIn("error", r, f"{sql[:20]} 应被拒绝")

    def test_reject_multi_statement(self):
        r = execute_tool(0, "query_sql", {"sql": "SELECT 1; DELETE FROM portfolio"})
        self.assertIn("error", r)

    def test_reject_empty(self):
        self.assertIn("error", execute_tool(0, "query_sql", {"sql": "  "}))

    def test_allow_cte(self):
        r = execute_tool(0, "query_sql", {"sql": "WITH x AS (SELECT 1 AS a) SELECT a FROM x"})
        # 有库时应拿到结果；无库时是 DB 错误而非「只允许 SELECT」拦截
        self.assertNotEqual(r.get("error", "")[:20], "只允许 SELECT 或 WITH 开头"[:20])


class TestToolArgs(unittest.TestCase):
    def test_stock_overview_code_format(self):
        r = execute_tool(0, "stock_overview", {"stock_code": "60051"})
        self.assertIn("error", r)

    def test_unknown_tool(self):
        self.assertIn("error", execute_tool(0, "no_such_tool", {}))

    def test_describe_unknown_table(self):
        self.assertIn("error", execute_tool(0, "describe_table", {"table": "nope"}))


class TestSandbox(unittest.TestCase):
    SID = 990001   # 测试专用会话号，避免污染真实会话

    def test_python_runs(self):
        r = sandbox.run_python(self.SID, "print('ok', 1 + 1)")
        self.assertTrue(r["ok"], r.get("stderr"))
        self.assertIn("ok 2", r["stdout"])

    def test_timeout_killed(self):
        r = sandbox.run_python(self.SID, "import time; time.sleep(10)", timeout=2)
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("timed_out"))

    def test_node_runs(self):
        r = sandbox.run_node(self.SID, "console.log(2 ** 10)")
        self.assertTrue(r["ok"], r.get("stderr"))
        self.assertIn("1024", r["stdout"])

    def test_workspace_persists(self):
        sandbox.run_python(self.SID, "open('keep.txt','w').write('x')")
        names = [f["name"] for f in sandbox.list_workspace(self.SID)["files"]]
        self.assertIn("keep.txt", names)


if __name__ == "__main__":
    unittest.main()
