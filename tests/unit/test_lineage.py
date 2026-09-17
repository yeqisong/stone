"""数据血缘台账单元测试：log_event 旁路安全性 + model_data_drift 过滤逻辑。"""
import json
import sys
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.lineage import DATA_MUTATION_TYPES, log_event, model_data_drift


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class TestLineageLogEvent(unittest.TestCase):
    """log_event 必须是旁路：任何失败都不得向主链路抛异常。"""

    def test_success_inserts_and_commits(self):
        db = MagicMock()
        db.execute.return_value = MagicMock()
        ok = log_event(db, 'factor_heal', 'daily_quote.close_hfq',
                       scope='近7日 3只', detail={'rows': 100})
        self.assertTrue(ok)
        db.execute.assert_called_once()
        sql = db.execute.call_args[0][0].text
        self.assertIn('INSERT INTO data_lineage', sql)
        db.commit.assert_called_once()

    def test_db_failure_returns_false_no_raise(self):
        db = MagicMock()
        db.execute.side_effect = RuntimeError('connection gone')
        ok = log_event(db, 'train', 'v17.0', detail={'sharpe': 2.0})
        self.assertFalse(ok)          # 不抛异常，只返回 False
        db.rollback.assert_called_once()

    def test_detail_serialized_as_json(self):
        db = MagicMock()
        log_event(db, 'train', 'v17.0', detail={'中文': 1, 'x': [1, 2]})
        params = db.execute.call_args[0][1]
        parsed = json.loads(params['d'])
        self.assertEqual(parsed, {'中文': 1, 'x': [1, 2]})

    def test_commit_failure_also_swallowed(self):
        db = MagicMock()
        db.commit.side_effect = RuntimeError('commit fail')
        ok = log_event(db, 'eval_rerun', 'v15.0')
        self.assertFalse(ok)


class TestModelDataDrift(unittest.TestCase):
    """漂移判定：训练之后的数据变更事件才算漂移；模型侧事件不计入。"""

    def _db_with(self, model_row, lineage_rows):
        db = MagicMock()
        db.execute.side_effect = [
            _FakeResult([model_row]),                       # model_versions 查询
            _FakeResult(lineage_rows),                      # data_lineage 查询
            _FakeResult([]),                                # get_events(train) — 若被调用
        ]
        return db

    def test_drift_detected_for_post_train_events(self):
        trained = datetime(2026, 9, 8, 18, 0, 0)
        later = trained + timedelta(days=2)
        db = self._db_with(
            (trained, json.dumps({'train_start': '2023-01-01'})),
            [('factor_heal', 'daily_quote.close_hfq', '2026-09-01~2026-09-10 5只',
              json.dumps({'rows': 999}), later)])
        r = model_data_drift(db, 'v15.0')
        self.assertTrue(r['has_drift'])
        self.assertEqual(len(r['events']), 1)
        self.assertTrue(r['events'][0]['overlaps_train_window'])

    def test_model_events_not_counted_as_drift(self):
        # eval_rerun 属模型侧事件：SQL 侧被 ANY(:ts) 过滤（这里喂空集模拟），
        # 且类型清单本身就不含它
        trained = datetime(2026, 9, 8)
        db = self._db_with(
            (trained, {'train_start': '2023-01-01'}),
            [])  # SQL 过滤后无事件
        r = model_data_drift(db, 'v15.0')
        self.assertFalse(r['has_drift'])
        self.assertNotIn('eval_rerun', DATA_MUTATION_TYPES)
        self.assertNotIn('train', DATA_MUTATION_TYPES)
        self.assertNotIn('rolling_retrain', DATA_MUTATION_TYPES)

    def test_no_model_returns_none(self):
        db = MagicMock()
        db.execute.return_value = _FakeResult([])
        self.assertIsNone(model_data_drift(db, 'v99.0'))

    def test_window_entirely_before_train_start_not_overlapping(self):
        trained = datetime(2026, 9, 8)
        db = self._db_with(
            (trained, {'train_start': '2023-01-01'}),
            [('backfill', 'stock_top_list', '2010-01-04~2012-06-30 历史回补',
              '{}', trained + timedelta(days=1))])
        r = model_data_drift(db, 'v15.0')
        self.assertTrue(r['has_drift'])                       # 事件本身在
        self.assertFalse(r['events'][0]['overlaps_train_window'])  # 但窗口不撞训练窗


if __name__ == '__main__':
    unittest.main()
