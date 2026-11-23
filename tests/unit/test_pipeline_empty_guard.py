"""交易日数据源返回空必须失败，不能静默记 success。"""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import scripts.pipeline as pipeline


class TestEmptyTradingDayGuard(unittest.TestCase):
    def _fake_env(self, is_td):
        src = MagicMock(name="source")
        src.name = "fake"
        for m in ("fetch_stock_kline", "fetch_index_kline", "fetch_etf_kline",
                  "fetch_fundamentals"):
            getattr(src, m).return_value = []
        manager = SimpleNamespace(get_source=lambda: src)
        return manager

    def _call(self, fn, is_td):
        db = MagicMock()
        db.execute.return_value.rowcount = 0
        manager = self._fake_env(is_td)
        with patch.object(pipeline, "write_node_log"), \
                patch.object(pipeline, "_with_hb",
                             side_effect=lambda log_id, rid, f, *a, **k: f()), \
                patch.object(pipeline, "_is_trade_day", return_value=is_td), \
                patch("crawler.adapters.get_data_source_manager", return_value=manager), \
                patch("crawler.writers.batch_upsert_kline", return_value=0), \
                patch("crawler.writers.batch_upsert_index_kline", return_value=0), \
                patch("crawler.writers.batch_upsert_fundamentals", return_value=0), \
                patch("crawler.writers.append_fundamentals_history", return_value=0), \
                patch("app.db.connection.get_sync_db", return_value=db):
            return fn("2026-01-05")

    def test_kline_empty_on_trading_day_fails(self):
        with self.assertRaises(RuntimeError):
            self._call(pipeline.dag_task_kline, True)

    def test_index_empty_on_trading_day_fails(self):
        with self.assertRaises(RuntimeError):
            self._call(pipeline.dag_task_index, True)

    def test_etf_empty_on_trading_day_fails(self):
        with self.assertRaises(RuntimeError):
            self._call(pipeline.dag_task_etf, True)

    def test_fund_empty_on_trading_day_fails(self):
        with self.assertRaises(RuntimeError):
            self._call(pipeline.dag_task_fund, True)

    def test_empty_on_non_trading_day_is_success(self):
        for fn in (pipeline.dag_task_kline, pipeline.dag_task_index,
                   pipeline.dag_task_etf, pipeline.dag_task_fund):
            r = self._call(fn, False)
            rows = r.get("rows", 0) if isinstance(r, dict) else r
            self.assertEqual(rows, 0, fn.__name__)


if __name__ == "__main__":
    unittest.main()
