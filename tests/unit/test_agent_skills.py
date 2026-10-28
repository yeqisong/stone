"""Agent 运行时与技能（Skills）集成测试——不依赖真实 LLM 额度。

用脚本化的假客户端驱动 ReAct 循环，锁定三件事：
① 模型发起 read_skill 时，循环真的执行工具并把技能正文回灌；
② assistant(tool_calls) / tool 结果都按顺序落库（可回放）；
③ 技能索引出现在系统提示里，save_skill 能沉淀并在下一次 list_skills 可见。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agent import runtime
from app.agent import skills as skill_mod
from app.agent.tools import TOOL_SPECS, execute_tool


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


def _tc(call_id, name, args_json):
    return SimpleNamespace(id=call_id, type="function",
                           function=SimpleNamespace(name=name, arguments=args_json))


class _FakeCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        msg = self.script.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class _FakeClient:
    def __init__(self, script):
        self.chat = SimpleNamespace(completions=_FakeCompletions(script))


class TestSkillPlumbing(unittest.TestCase):
    def test_builtin_skills_listed(self):
        names = [s["name"] for s in skill_mod.list_skills()]
        self.assertIn("stock-deep-dive", names)
        self.assertIn("signal-review", names)
        self.assertIn("dag-builder", names)

    def test_read_skill_body(self):
        r = execute_tool(0, "read_skill", {"name": "stock-deep-dive"})
        self.assertIn("content", r)
        self.assertGreater(len(r["content"]), 500)
        self.assertIn("## 步骤", r["content"])

    def test_read_skill_missing(self):
        r = execute_tool(0, "read_skill", {"name": "no-such-skill"})
        self.assertIn("error", r)

    def test_path_traversal_blocked(self):
        r = execute_tool(0, "read_skill", {"name": "stock-deep-dive", "file": "../../../.env"})
        self.assertIn("error", r)

    def test_system_prompt_indexes_skills(self):
        p = runtime._system_prompt()
        self.assertIn("技能（Skills）", p)
        self.assertIn("stock-deep-dive", p)
        self.assertLess(len(p), 12000, "技能索引不应把全文塞进提示（渐进披露）")

    def test_tool_specs_include_skills(self):
        names = {t["function"]["name"] for t in TOOL_SPECS}
        self.assertTrue({"list_skills", "read_skill", "save_skill"} <= names)


class TestReactLoop(unittest.TestCase):
    """脚本化 LLM：先读技能，再给最终答案。"""

    def setUp(self):
        self.saved = []
        self.patches = [
            patch.object(runtime, "_save", side_effect=self._save),
            patch.object(runtime, "_history_for_model", return_value=[]),
            patch.object(runtime, "_auto_title", return_value=None),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _save(self, sid, role, content="", tool_calls=None, tool_call_id=None,
              tool_name=None, meta=None):
        self.saved.append({"role": role, "content": content or "",
                           "tool_calls": tool_calls, "tool_call_id": tool_call_id,
                           "tool_name": tool_name})
        return 1

    def test_reads_skill_then_answers(self):
        script = [
            _FakeMessage(content="我先读一下个股尽调技能。",
                         tool_calls=[_tc("call_1", "read_skill", '{"name":"stock-deep-dive"}')]),
            _FakeMessage(content="按技能步骤：先看行情，再看资金面。"),
        ]
        fake = _FakeClient(script)
        with patch.object(runtime, "_client", return_value=(fake, {"model": "fake-model", "base_url": "", "api_key": "x"})):
            events = list(runtime.stream_agent(1, "用个股尽调技能看看 600519"))

        types = [e["type"] for e in events]
        self.assertIn("tool_call", types)
        self.assertIn("tool_result", types)
        self.assertEqual(types[-1], "done")
        # 工具调用与结果事件
        tc_ev = next(e for e in events if e["type"] == "tool_call")
        self.assertEqual(tc_ev["name"], "read_skill")
        tr_ev = next(e for e in events if e["type"] == "tool_result")
        self.assertTrue(tr_ev["ok"])
        # 落库顺序：user → assistant(tool_calls) → tool → assistant(final)
        roles = [m["role"] for m in self.saved]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])
        self.assertEqual(self.saved[1]["tool_calls"][0]["function"]["name"], "read_skill")
        self.assertIn("个股尽调", self.saved[2]["content"])
        # 第二次调用模型时，上下文里应包含工具结果
        msgs = fake.chat.completions.calls[1]["messages"]
        self.assertTrue(any(m.get("role") == "tool" for m in msgs))

    def test_max_steps_guard(self):
        """模型反复调工具时不超过 MAX_STEPS 次。"""
        script = [_FakeMessage(content="", tool_calls=[_tc(f"c{i}", "list_workspace", "{}")])
                  for i in range(runtime.MAX_STEPS + 3)]
        fake = _FakeClient(script)
        with patch.object(runtime, "_client", return_value=(fake, {"model": "m", "base_url": "", "api_key": "x"})):
            events = list(runtime.stream_agent(2, "循环调用"))
        self.assertEqual(len(fake.chat.completions.calls), runtime.MAX_STEPS)
        self.assertEqual(events[-1]["type"], "done")


class TestSaveSkill(unittest.TestCase):
    NAME = "ut-test-skill"

    def tearDown(self):
        skill_mod.delete_skill(self.NAME)

    def test_save_then_list_and_delete(self):
        r = execute_tool(0, "save_skill", {
            "name": self.NAME, "description": "单测用：验证技能沉淀",
            "content": "# 步骤\n1. 查库\n2. 输出", "title": "单测技能"})
        self.assertTrue(r.get("ok"), r)
        names = [s["name"] for s in skill_mod.list_skills()]
        self.assertIn(self.NAME, names)
        body = execute_tool(0, "read_skill", {"name": self.NAME})["content"]
        self.assertIn("1. 查库", body)
        self.assertTrue(execute_tool(0, "list_skills", {})["skills"])
        self.assertTrue(skill_mod.delete_skill(self.NAME).get("ok"))

    def test_reject_bad_name(self):
        r = execute_tool(0, "save_skill", {"name": "中文名", "description": "x" * 6, "content": "x"})
        self.assertIn("error", r)

    def test_cannot_overwrite_builtin(self):
        r = execute_tool(0, "save_skill", {"name": "stock-deep-dive", "description": "x" * 6, "content": "x"})
        self.assertIn("error", r)


if __name__ == "__main__":
    unittest.main()
