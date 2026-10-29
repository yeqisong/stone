"""LLM 实时指标（并发/速率/错误码）单元测试。"""
import threading
import time
import unittest

from app import llm_metrics as M


class TestLlmMetrics(unittest.TestCase):
    def setUp(self):
        M.reset()

    def test_success_counters_and_latency(self):
        with M.track("glm-test"):
            time.sleep(0.01)
        snap = M.snapshot()
        self.assertEqual(snap["active"], 0)
        self.assertEqual(snap["total"], 1)
        self.assertEqual(snap["ok"], 1)
        self.assertEqual(snap["fail"], 0)
        self.assertIsNotNone(snap["avg_ms"])
        self.assertGreaterEqual(snap["avg_ms"], 5)
        self.assertEqual(snap["rpm_1m"], 1)
        self.assertIn("glm-test", snap["by_model"])

    def test_failure_records_error_code(self):
        class RateLimit(Exception):
            status_code = 429
            body = {"error": {"code": "1302", "message": "rate limit"}}

        with self.assertRaises(RateLimit):
            with M.track("glm-test"):
                raise RateLimit("boom")
        snap = M.snapshot()
        self.assertEqual(snap["fail"], 1)
        self.assertEqual(snap["ok"], 0)
        self.assertEqual(snap["active"], 0)
        self.assertTrue(snap["recent_errors"])
        self.assertEqual(snap["recent_errors"][-1]["code"], "1302")

    def test_concurrency_measured(self):
        stop = threading.Event()

        def worker():
            with M.track("glm-test"):
                stop.wait(0.4)

        ts = [threading.Thread(target=worker) for _ in range(3)]
        for t in ts:
            t.start()
        time.sleep(0.15)
        mid = M.snapshot()
        self.assertGreaterEqual(mid["active"], 2, "在途并发应被实时计入")
        self.assertGreaterEqual(mid["peak"], 2)
        stop.set()
        for t in ts:
            t.join()
        end = M.snapshot()
        self.assertEqual(end["active"], 0)
        self.assertEqual(end["total"], 3)
        self.assertGreaterEqual(end["peak"], 2)

    def test_snapshot_has_config_limits(self):
        snap = M.snapshot()
        self.assertIn("config", snap)
        # rpm_pct/saturated 仅在能读到运行时配置时出现
        if snap.get("config", {}).get("rpm"):
            self.assertIsNotNone(snap.get("rpm_pct"))
            self.assertIn("saturated", snap)


if __name__ == "__main__":
    unittest.main()
