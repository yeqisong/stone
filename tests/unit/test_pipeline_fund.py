"""dag_task_fund：ROE 前向填充 SQL 必须真正执行（曾因漏 import text 静默失败）。"""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import scripts.pipeline as pipeline


class TestDagTaskFund(unittest.TestCase):
    def test_roe_forward_fill_sql_executes(self):
        db = MagicMock()
        db.execute.return_value.rowcount = 3
        src = SimpleNamespace(name="fake", fetch_fundamentals=lambda _codes: [])
        manager = SimpleNamespace(get_source=lambda: src)
        with patch.object(pipeline, "write_node_log"), \
                patch.object(pipeline, "_with_hb",
                             side_effect=lambda log_id, rid, fn, *a, **k: fn()), \
                patch("crawler.adapters.get_data_source_manager", return_value=manager), \
                patch("crawler.writers.batch_upsert_fundamentals", return_value=0), \
                patch("crawler.writers.append_fundamentals_history", return_value=0), \
                patch("app.db.connection.get_sync_db", return_value=db):
            r = pipeline.dag_task_fund("2026-01-05")
        self.assertEqual(r, 0)
        self.assertEqual(db.execute.call_count, 1, "ROE 前向填充 SQL 未执行")
        db.commit.assert_called_once()
        db.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
