"""模型信号规则回退：无模型时不得因 pred_by_h 未初始化而 NameError。"""
import json
import pickle
import unittest
from unittest.mock import mock_open, patch

import pandas as pd

import scripts.pipeline as pipeline


class _Result:
    def __init__(self, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _FakeDB:
    def __init__(self, best_params="{}", rows=None):
        self.inserts = 0
        self.best_params = best_params
        self.rows = rows or [("600000", "测试股")]

    def execute(self, sql, params=None):
        s = str(sql)
        if "status='ACTIVE'" in s:
            return _Result("v-test")
        if "SELECT config FROM model_versions" in s:
            return _Result(json.dumps({"feature_names": ["f1"], "signal": {}}))
        if "SELECT best_params" in s:
            return _Result(self.best_params)
        if "SELECT MAX(trade_date)" in s:
            return _Result(None)
        if "SELECT stock_code, stock_name FROM stock_master" in s:
            return _Result(rows=self.rows)
        if "INSERT INTO signal_history" in s:
            self.inserts += 1
        return _Result()

    def commit(self):
        pass

    def close(self):
        pass

    def rollback(self):
        pass


class TestModelSignalRuleFallback(unittest.TestCase):
    def test_rule_mode_inserts_without_prediction_maps(self):
        db = _FakeDB()
        today = "2026-01-05"
        wide = pd.DataFrame([{
            "trade_date": today, "stock_code": "600000",
            "f1": 1.0, "close": 10.0,
        }])
        thresholds = {"buy_score_min": 0, "boll_lower": -1, "rsi_oversold": 100}
        with patch("app.db.connection.get_sync_db", return_value=db), \
                patch.object(pipeline, "_get_preference_thresholds",
                             return_value=(thresholds, "balanced")), \
                patch.object(pipeline, "build_feature_wide_table", return_value=wide), \
                patch.object(pipeline, "_feature_coverage", return_value=[]), \
                patch.object(pipeline, "write_node_log"), \
                patch.object(pipeline, "dag_task_paper_portfolio", return_value=0):
            n = pipeline.dag_task_model_signal(today)
        self.assertEqual(n, 1)
        self.assertGreaterEqual(db.inserts, 1)

    def test_two_horizon_models_still_predict(self):
        today = "2026-01-05"
        db = _FakeDB(best_params=json.dumps({
            "5d": {"model_path": "m5", "r2": 0.1},
            "10d": {"model_path": "m10", "r2": 0.1},
        }), rows=[("600000", "A"), ("000001", "B"), ("000002", "C")])
        wide = pd.DataFrame([
            {"trade_date": today, "stock_code": "600000", "f1": 1.0, "close": 10.0},
            {"trade_date": today, "stock_code": "000001", "f1": 1.0, "close": 10.0},
            {"trade_date": today, "stock_code": "000002", "f1": 1.0, "close": 10.0},
        ])
        thresholds = {"buy_score_min": 0, "boll_lower": -1, "rsi_oversold": 100}

        def _pred(_db, _ver, part, _s, _e, horizon):
            if horizon in (5, 10):
                return pd.Series([0.1, 0.2, 0.3], index=part.index), None
            return None, "no model"

        with patch("app.db.connection.get_sync_db", return_value=db), \
                patch.object(pipeline, "_get_preference_thresholds",
                             return_value=(thresholds, "balanced")), \
                patch.object(pipeline, "build_feature_wide_table", return_value=wide), \
                patch.object(pipeline, "_feature_coverage", return_value=[]), \
                patch.object(pipeline, "predict_for_version", side_effect=_pred), \
                patch.object(pipeline, "write_node_log"), \
                patch.object(pipeline, "dag_task_paper_portfolio", return_value=0), \
                patch("os.path.exists", return_value=True), \
                patch("builtins.open", mock_open(read_data=b"")), \
                patch.object(pickle, "load", return_value=object()):
            n = pipeline.dag_task_model_signal(today)
        self.assertGreaterEqual(n, 1, "只有 5d/10d 两个模型时应正常生成信号")
        self.assertGreaterEqual(db.inserts, 1)


if __name__ == "__main__":
    unittest.main()
