"""Agent 会话管理 v2 测试——搜索、置顶、分支、回滚、反馈、SSE 包装。

用真实数据库但每条用例自建临时会话并在 finally 删除；不碰用户已有会话。
"""
import asyncio
import json
import unittest
import uuid

from app.agent import runtime


class TestSessionLifecycle(unittest.TestCase):
    def setUp(self):
        self.title = "UT-" + uuid.uuid4().hex[:8]
        self.sid = runtime.create_session(self.title, model="ut-model")["id"]

    def tearDown(self):
        runtime.delete_session(self.sid)

    def test_search_pin_and_model(self):
        runtime._save(self.sid, "user", "单测关键字-资金流-600519")
        ids = [s["id"] for s in runtime.list_sessions(q="关键字-资金流")]
        self.assertIn(self.sid, ids)
        runtime.update_session(self.sid, pinned=True, model="other")
        one = runtime.get_session(self.sid)
        self.assertTrue(one["pinned"])
        self.assertEqual(one["model"], "other")
        listed = [s for s in runtime.list_sessions(q=self.title) if s["id"] == self.sid][0]
        self.assertIn("资金流", listed["last_question"])

    def test_branch_and_rewind(self):
        m1 = runtime._save(self.sid, "user", "第一问")
        m2 = runtime._save(self.sid, "assistant", "第一答")
        m3 = runtime._save(self.sid, "user", "第二问")
        br = runtime.branch_session(self.sid, m2, title=self.title + "-分支")
        self.assertTrue(br.get("ok"), br)
        try:
            msgs = runtime.list_messages(br["id"])
            self.assertEqual(len(msgs), 2)
            self.assertEqual(msgs[-1]["content"], "第一答")
            # 回滚到 m3：删除 m3 及其之后
            self.assertEqual(runtime.truncate_from(self.sid, m3), 1)
            self.assertEqual(len(runtime.list_messages(self.sid)), 2)
        finally:
            runtime.delete_session(br["id"])
        self.assertTrue(m1 < m2 < m3)

    def test_feedback(self):
        mid = runtime._save(self.sid, "user", "这个问题")
        aid = runtime._save(self.sid, "assistant", "这个回答")
        r = runtime.save_feedback(self.sid, aid, -1, "口径不对")
        self.assertTrue(r.get("ok"))
        rows = runtime.list_feedback(limit=50, rating=-1)
        hit = [x for x in rows if x["id"] == r["id"]]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["question"], "这个问题")
        self.assertEqual(hit[0]["comment"], "口径不对")
        self.assertGreater(mid, 0)


class TestMemoCompact(unittest.TestCase):
    def setUp(self):
        self.sid = runtime.create_session("UT-memo-" + uuid.uuid4().hex[:6])["id"]

    def tearDown(self):
        runtime.delete_session(self.sid)

    def test_compact_sets_memo_and_skips_covered(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        last = 0
        for i in range(70):
            last = runtime._save(self.sid, "user" if i % 2 == 0 else "assistant",
                                 f"第{i}条" + "X" * 100)
        fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kw: SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="早期对话摘要"))]))))
        with patch.object(runtime, "_client", return_value=(fake, {"model": "m", "api_key": "x", "base_url": ""})):
            runtime._maybe_compact(self.sid, "m")
        sess = runtime.get_session(self.sid)
        self.assertEqual(sess["memo"], "早期对话摘要")
        self.assertGreater(sess["memo_until"], 0)
        hist = runtime._history_for_model(self.sid, memo_until=sess["memo_until"])
        self.assertTrue(hist)
        self.assertTrue(all("早期" not in json.dumps(m, ensure_ascii=False) for m in hist))
        # 最近的消息仍保留
        self.assertTrue(any("第69条" in json.dumps(m, ensure_ascii=False) for m in hist))


class TestSseWrapper(unittest.TestCase):
    def test_wraps_events_and_ends(self):
        from app.api import agent as api_agent

        def gen():
            yield {"type": "assistant", "content": "你好"}
            yield {"type": "done"}

        async def collect():
            return [x async for x in api_agent._sse(gen())]

        chunks = asyncio.run(collect())
        data = [c for c in chunks if c.startswith("data: ")]
        self.assertEqual(len(data), 2)
        self.assertEqual(json.loads(data[0][6:])["content"], "你好")
        self.assertEqual(json.loads(data[1][6:])["type"], "done")


class TestSessionRunning(unittest.TestCase):
    def test_running_flag(self):
        import threading
        sid = 991999
        ev = threading.Event()
        runtime._STOP[sid] = ev
        try:
            self.assertTrue(runtime.session_running(sid))
            ev.set()
            self.assertFalse(runtime.session_running(sid))
        finally:
            runtime._STOP.pop(sid, None)


if __name__ == "__main__":
    unittest.main()
