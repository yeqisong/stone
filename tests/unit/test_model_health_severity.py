"""IC DEGRADED 必须能升级健康度并触发告警（severity 缺失会让判级静默失效）。"""
import unittest

from scripts.pipeline import _SEVERITY, ic_health_status


class TestModelHealthSeverity(unittest.TestCase):
    def test_degraded_is_warning_level(self):
        self.assertEqual(ic_health_status(-0.01), "DEGRADED")
        self.assertGreaterEqual(_SEVERITY["DEGRADED"], _SEVERITY["WARNING"])
        self.assertGreater(_SEVERITY["DEGRADED"], _SEVERITY["HEALTHY"])


if __name__ == "__main__":
    unittest.main()
