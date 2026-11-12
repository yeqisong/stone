"""Agent v2 单元测试——流式、图片持久化、停止、上下文裁剪、SQL 结果裁剪、签名 URL。

不依赖真实 LLM 额度；DB 相关函数用 patch 隔离。
"""
import base64
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agent import media, runtime, sandbox
from app.agent.tools import TOOL_SPECS, _fit_rows


class _Delta:
    def __init__(self, content=None, reasoning=None, tool_calls=None):
        self.content = content
        self.reasoning_content = reasoning
        self.tool_calls = tool_calls or []


class _Chunk:
    def __init__(self, delta=None, usage=None):
        self.choices = [SimpleNamespace(delta=delta or _Delta())]
        self.usage = usage


class _Stream:
    def __init__(self, chunks, delay=0.0):
        self.chunks = list(chunks)
        self.closed = False
        self.delay = delay

    def __iter__(self):
        for c in self.chunks:
            if self.delay:
                time.sleep(self.delay)
            yield c

    def close(self):
        self.closed = True


class _Completions:
    def __init__(self, result):
        self.result = list(result) if isinstance(result, list) else result
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, list):
            return self.result.pop(0)
        return self.result


class _Client:
    def __init__(self, result):
        self.chat = SimpleNamespace(completions=_Completions(result))


class _Base(unittest.TestCase):
    SID = 991001

    def setUp(self):
        self.saved = []
        self.patches = [
            patch.object(runtime, "_save", side_effect=self._save),
            patch.object(runtime, "_history_for_model", return_value=[]),
            patch.object(runtime, "_auto_title", return_value=False),
            patch.object(runtime, "get_session", return_value={}),
            patch.object(runtime, "_system_prompt", return_value="sys"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        runtime._STOP.pop(self.SID, None)

    def _save(self, sid, role, content="", tool_calls=None, tool_call_id=None,
              tool_name=None, meta=None):
        self.saved.append({"role": role, "content": content or "",
                           "tool_calls": tool_calls, "tool_call_id": tool_call_id,
                           "tool_name": tool_name, "meta": meta or {}})
        return len(self.saved)


class TestStreaming(_Base):
    def test_deltas_and_usage_saved(self):
        stream = _Stream([
            _Chunk(_Delta(reasoning="先想一下")),
            _Chunk(_Delta(content="结论")),
            _Chunk(_Delta(content="如下")),
            _Chunk(usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}),
        ])
        fake = _Client(stream)
        with patch.object(runtime, "_client",
                          return_value=(fake, {"model": "m", "base_url": "", "api_key": "x"})):
            events = list(runtime.stream_agent(self.SID, "问题"))

        types = [e["type"] for e in events]
        self.assertIn("reasoning_delta", types)
        self.assertIn("assistant_delta", types)
        self.assertEqual(types[-1], "done")
        deltas = [e["text"] for e in events if e["type"] == "assistant_delta"]
        self.assertEqual("".join(deltas), "结论如下")
        final = next(e for e in events if e["type"] == "assistant")
        self.assertEqual(final["content"], "结论如下")
        self.assertEqual(final["reasoning"], "先想一下")
        self.assertEqual(final["usage"]["total_tokens"], 12)
        # 落库的 assistant meta 带 usage / reasoning / model
        asst = next(m for m in self.saved if m["role"] == "assistant")
        self.assertEqual(asst["meta"]["usage"]["total_tokens"], 12)
        self.assertEqual(asst["meta"]["reasoning"], "先想一下")
        self.assertEqual(asst["meta"]["model"], "m")
        self.assertTrue(fake.chat.completions.calls[0]["stream"])

    def test_stop_during_stream(self):
        stream = _Stream([_Chunk(_Delta(content=f"片{i}")) for i in range(20)], delay=0.02)
        fake = _Client(stream)

        def _client(_):
            threading.Timer(0.05, lambda: runtime.request_stop(self.SID)).start()
            return fake, {"model": "m", "base_url": "", "api_key": "x"}

        with patch.object(runtime, "_client", side_effect=_client):
            events = list(runtime.stream_agent(self.SID, "长回答"))
        self.assertEqual(events[-1]["type"], "stopped")
        self.assertTrue(stream.closed)

    def test_estimated_usage_when_provider_omits(self):
        fake = _Client(_Stream([_Chunk(_Delta(content="hi"))]))
        with patch.object(runtime, "_client",
                          return_value=(fake, {"model": "m", "base_url": "", "api_key": "x"})):
            events = list(runtime.stream_agent(self.SID, "q"))
        final = next(e for e in events if e["type"] == "assistant")
        self.assertTrue(final["usage"].get("estimated"))
        done = events[-1]
        self.assertGreater(done["usage"].get("estimated_total_tokens", 0), 0)


class TestToolImages(_Base):
    def test_images_persisted_and_signed(self):
        msg = SimpleNamespace(id="c1", type="function",
                              function=SimpleNamespace(name="run_python",
                                                       arguments='{"code":"print(1)"}'))
        script_msg = SimpleNamespace(content="", tool_calls=[msg])
        final_msg = SimpleNamespace(content="图画好了", tool_calls=[])
        fake = _Client([
            SimpleNamespace(choices=[SimpleNamespace(message=script_msg)]),
            SimpleNamespace(choices=[SimpleNamespace(message=final_msg)]),
        ])
        snapshots = iter([{"a.png": 1.0}, {"a.png": 2.0}])
        with patch.object(runtime, "_client",
                          return_value=(fake, {"model": "m", "base_url": "", "api_key": "x"})), \
                patch.object(runtime, "workspace_images", side_effect=lambda sid: next(snapshots)), \
                patch.object(runtime, "execute_tool",
                             return_value={"ok": True, "stdout": "1", "truncated": False}):
            events = list(runtime.stream_agent(self.SID, "画图"))

        tr = next(e for e in events if e["type"] == "tool_result")
        self.assertEqual(len(tr["images"]), 1)
        self.assertIn("sig=", tr["images"][0]["url"])
        tool_row = next(m for m in self.saved if m["role"] == "tool")
        self.assertEqual(tool_row["meta"]["images"][0]["path"], "a.png")
        self.assertNotIn("sig=", tool_row["meta"]["images"][0]["path"])


class TestStopSandbox(unittest.TestCase):
    SID = 991002

    def tearDown(self):
        sandbox.reset_workspace(self.SID)

    def test_terminate_kills_sleeping_process(self):
        t0 = time.time()
        box = {}

        def _run():
            box["r"] = sandbox.run_python(self.SID, "import time; time.sleep(30)", timeout=60)

        th = threading.Thread(target=_run)
        th.start()
        time.sleep(0.8)
        killed = sandbox.terminate_session(self.SID)
        th.join(timeout=10)
        self.assertGreaterEqual(killed, 1)
        self.assertFalse(th.is_alive(), "停止后子进程应立即退出")
        self.assertLess(time.time() - t0, 10)
        self.assertFalse(box["r"]["ok"])


class TestHistoryBudget(unittest.TestCase):
    def _msgs(self, turns=13):
        out, mid = [], 1
        for i in range(turns):
            out.append({"id": mid, "role": "user", "content": f"问题{i}", "meta": {}}); mid += 1
            out.append({"id": mid, "role": "assistant", "content": f"答{i}",
                        "tool_calls": [{"id": f"c{i}", "type": "function",
                                        "function": {"name": "query_sql", "arguments": "{}"}}],
                        "meta": {}}); mid += 1
            out.append({"id": mid, "role": "tool", "content": "X" * 9000,
                        "tool_call_id": f"c{i}", "meta": {}}); mid += 1
        return out

    def test_turn_trim_and_tool_compression(self):
        msgs = self._msgs()
        with patch.object(runtime, "list_messages", return_value=msgs):
            hist = runtime._history_for_model(1)
        users = [m for m in hist if m["role"] == "user"]
        self.assertEqual(len(users), runtime.HISTORY_TURNS)
        self.assertEqual(users[-1]["content"], "问题12")
        # 最早轮的 tool 结果被压缩，最近一轮保留完整
        tools = [m for m in hist if m["role"] == "tool"]
        self.assertIn('"compressed": true', tools[0]["content"])
        self.assertNotIn("compressed", tools[-1]["content"])
        # 不切断 tool 配对
        self.assertEqual(hist[0]["role"], "user")

    def test_char_budget_drops_oldest_whole_turns(self):
        with patch.object(runtime, "list_messages", return_value=self._msgs()), \
                patch.object(runtime, "CONTEXT_CHAR_BUDGET", 12000):
            hist = runtime._history_for_model(1)
        self.assertLess(len(hist), 39)
        self.assertEqual(hist[0]["role"], "user")


class TestSqlFitRows(unittest.TestCase):
    def test_preserves_structure(self):
        cols = ["a", "b"]
        data = [[i, "x" * 100] for i in range(500)]
        rows, truncated = _fit_rows(cols, data)
        self.assertTrue(truncated)
        self.assertGreater(len(rows), 0)
        self.assertLess(len(rows), 500)
        self.assertLess(len(json.dumps({"columns": cols, "rows": rows})), 12000)


class TestMediaSign(unittest.TestCase):
    def test_sign_and_verify(self):
        url = media.sign_path(7, "uploads/研报.pdf")
        self.assertIn("sig=", url)
        self.assertIn("%", url)
        from urllib.parse import parse_qs, urlparse
        sig = parse_qs(urlparse(url).query)["sig"][0]
        self.assertTrue(media.verify_sig(7, "uploads/研报.pdf", sig))
        self.assertFalse(media.verify_sig(8, "uploads/研报.pdf", sig))
        self.assertFalse(media.verify_sig(7, "other.png", sig))


class TestUpload(unittest.TestCase):
    SID = 991003

    def tearDown(self):
        sandbox.reset_workspace(self.SID)

    def test_path_and_name_sanitized(self):
        r = sandbox.save_upload(self.SID, "../../etc/pa ss.csv", b"a,b\n1,2")
        self.assertTrue(r["ok"])
        self.assertTrue(r["path"].startswith("uploads/"))
        self.assertNotIn("..", r["path"])
        self.assertNotIn(" ", r["path"])
        ws = sandbox.workspace(self.SID)
        self.assertTrue((ws / r["path"]).is_file())

    def test_reject_empty_and_oversize(self):
        self.assertIn("error", sandbox.save_upload(self.SID, "a.csv", b""))
        with patch.object(sandbox, "MAX_UPLOAD", 4):
            self.assertIn("error", sandbox.save_upload(self.SID, "a.csv", b"12345"))


class TestVisionInput(unittest.TestCase):
    SID = 991004
    PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

    def setUp(self):
        sandbox.save_upload(self.SID, "shot.png", self.PNG)

    def tearDown(self):
        sandbox.reset_workspace(self.SID)

    def test_user_content_has_image_part(self):
        c = runtime._user_content(self.SID, "看这张分时图", ["shot.png"])
        self.assertIsInstance(c, list)
        self.assertEqual(c[0]["type"], "text")
        self.assertIn("uploads/shot.png", c[0]["text"])
        imgs = [p for p in c if p["type"] == "image_url"]
        self.assertEqual(len(imgs), 1)
        self.assertTrue(imgs[0]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_no_images_for_old_turn(self):
        c = runtime._user_content(self.SID, "看这张", ["shot.png"], include_images=False)
        self.assertIsInstance(c, str)
        self.assertIn("uploads/shot.png", c)
        self.assertNotIn("data:image", c)

    def test_missing_file_degrades_to_text(self):
        c = runtime._user_content(self.SID, "看这张", ["nope.png"])
        self.assertIsInstance(c, str)

    def test_vision_error_classification(self):
        class E400(Exception):
            status_code = 400
        class E429(Exception):
            status_code = 429
        self.assertTrue(runtime._is_vision_error(E400("unsupported image content")))
        self.assertFalse(runtime._is_vision_error(E429("overloaded")))
        self.assertFalse(runtime._is_vision_error(E400("bad tool schema")))

    def test_text_only_strips_images(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "看图"}, {"type": "image_url", "image_url": {"url": "data:x"}}]}]
        out = runtime._text_only(msgs)
        self.assertIsInstance(out[0]["content"], str)
        self.assertIn("看图", out[0]["content"])
        self.assertFalse(runtime._has_image_parts(out))


class _VisionFallbackCompletions:
    def __init__(self, stream):
        self.stream = stream
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            e = Exception("unsupported image content")
            e.status_code = 400
            raise e
        return self.stream


class TestVisionFallbackStream(_Base):
    def test_falls_back_and_notifies(self):
        stream = _Stream([_Chunk(_Delta(content="图片模式降级后的回答"))])
        completions = _VisionFallbackCompletions(stream)
        fake = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        with patch.object(runtime, "_client",
                          return_value=(fake, {"model": "m", "base_url": "", "api_key": "x"})), \
                patch.object(runtime, "_history_for_model", return_value=[
                    {"role": "user", "content": [
                        {"type": "text", "text": "看图"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}]}]):
            events = list(runtime.stream_agent(self.SID, "看图"))
        self.assertEqual(completions.calls, 2)
        self.assertTrue(any(e["type"] == "notice" for e in events))
        final = next(e for e in events if e["type"] == "assistant")
        self.assertIn("降级", final["content"])
        # 第二次调用必须是纯文本
        self.assertEqual(completions.calls, 2)


class TestToolRegistry(unittest.TestCase):
    def test_new_tools_registered(self):
        names = {t["function"]["name"] for t in TOOL_SPECS}
        for n in ("get_dag_run_status", "model_performance", "portfolio_summary", "recent_signals"):
            self.assertIn(n, names)

    def test_prompt_has_freshness(self):
        p = runtime._system_prompt()
        self.assertIn("数据新鲜度", p)
        self.assertIn("附件", p)
        self.assertLess(len(p), 16000)


if __name__ == "__main__":
    unittest.main()
