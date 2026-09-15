"""差距分解 / purged CV 折 / 种子集成 单元测试。"""
import io
import os
import pickle
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.pipeline import (ProbaSeedEnsemble, SeedEnsemble, _purged_time_folds,
                              compute_gap_decomposition, cross_sectional_rank_ic,
                              pred_score, seed_ensemble,
                              with_training_protocol_defaults)


class TestRankIC(unittest.TestCase):
    def test_perfect_prediction_ic_one(self):
        dates = np.array(['d1'] * 5 + ['d2'] * 5)
        target = np.array([1., 2, 3, 4, 5, 10, 20, 30, 40, 50])
        self.assertAlmostEqual(cross_sectional_rank_ic(target, target, dates), 1.0, places=3)
        self.assertAlmostEqual(cross_sectional_rank_ic(-target, target, dates), -1.0, places=3)

    def test_constant_target_returns_zero(self):
        # 目标全平：日内 std=0 的日子被剔除，无有效日 → 0
        dates = np.array(['d1'] * 5 + ['d2'] * 5)
        self.assertEqual(cross_sectional_rank_ic(np.arange(10.), np.ones(10), dates), 0.0)

    def test_short_input_returns_zero(self):
        self.assertEqual(cross_sectional_rank_ic([1, 2], [2, 1], ['d'] * 2), 0.0)


class _Stub:
    """可 pickle 的假成员模型（predict/predict_proba 由偏移构造）。"""

    def __init__(self, off=0.0):
        self.off = off

    def predict(self, X):
        return np.asarray(X, dtype=float)[:, 0] + self.off

    def predict_proba(self, X):
        p = np.asarray(X, dtype=float)[:, 0] + self.off
        return np.c_[p, 1 - p]


class _RegStub:
    """只有 predict 的回归风格假成员（无 predict_proba）。"""

    def __init__(self, off=0.0):
        self.off = off

    def predict(self, X):
        return np.asarray(X, dtype=float)[:, 0] + self.off


class TestSeedEnsemble(unittest.TestCase):
    def setUp(self):
        self.X = np.array([[1.0], [2.0], [3.0]])
        # 分类风格假成员 → 工厂产出概率版集成
        self.ens = seed_ensemble([_Stub(0.0), _Stub(2.0)])

    def test_factory_routes_by_member_type(self):
        # 全体带概率接口 → 概率版；任一回归成员 → 回归版（无 predict_proba）
        e1 = seed_ensemble([_Stub(0.0), _Stub(2.0)])
        self.assertIsInstance(e1, ProbaSeedEnsemble)
        e2 = seed_ensemble([_RegStub(0.0), _RegStub(2.0)])
        self.assertIsInstance(e2, SeedEnsemble)
        self.assertNotIsInstance(e2, ProbaSeedEnsemble)
        self.assertFalse(hasattr(e2, 'predict_proba'))

    def test_regression_ensemble_pred_score_uses_predict(self):
        # 回归集成：pred_score 的 hasattr 探测落空 → 走 predict 分支（XGBRegressor
        # 曾因误走概率分支在此炸掉）
        e = seed_ensemble([_RegStub(0.0), _RegStub(2.0)])
        np.testing.assert_allclose(pred_score(e, self.X), [2., 3., 4.])

    def test_predict_is_member_mean(self):
        np.testing.assert_allclose(self.ens.predict(self.X), [2., 3., 4.])

    def test_predict_proba_is_member_mean(self):
        pp = self.ens.predict_proba(self.X)
        np.testing.assert_allclose(pp[:, 0], [2., 3., 4.])
        # 列1 = 成员 (1-p) 的均值：x=[1,2,3] → [1-2, 1-3, 1-4]
        np.testing.assert_allclose(pp[:, 1], [-1., -2., -3.])

    def test_score_is_r2_of_mean_prediction(self):
        y = np.array([2., 3., 4.])
        self.assertAlmostEqual(self.ens.score(self.X, y), 1.0, places=4)
        y2 = np.array([2., 3., 5.])
        manual = 1 - np.sum((y2 - np.array([2., 3., 4.])) ** 2) / np.sum((y2 - y2.mean()) ** 2)
        self.assertAlmostEqual(self.ens.score(self.X, y2), round(manual, 4), places=4)

    def test_pred_score_transparent(self):
        # pred_score 走 predict_proba[:,1]（分类风格接口），集成无需特判
        np.testing.assert_allclose(pred_score(self.ens, self.X), [-1., -2., -3.])

    def test_pickle_roundtrip(self):
        buf = io.BytesIO()
        pickle.dump(self.ens, buf)
        buf.seek(0)
        ens2 = pickle.load(buf)
        np.testing.assert_allclose(ens2.predict(self.X), [2., 3., 4.])


def _bt(sharpe):
    return {'sharpe': sharpe, 'max_dd': -0.1, 'total_return': 0.5, 'trades': []}


class TestGapDecomposition(unittest.TestCase):
    def test_gap_numbers(self):
        val0 = {'5d': _bt(5.0), '10d': _bt(4.0)}
        test0 = {'5d': _bt(2.0), '10d': _bt(3.0)}
        g = compute_gap_decomposition(val0, test0)
        self.assertEqual(g['sharpe_gap_val_test_t0'], 2.0)   # (4.5 - 2.5)
        self.assertIsNone(g['sharpe_gap_val_test_t1'])

    def test_cause_execution_caliber(self):
        # T+1 下差距收敛（1.8 → 0.1 < max(0.5, 1.8*0.3)）→ 执行口径
        val0 = {'5d': _bt(5.8)}
        test0 = {'5d': _bt(4.0)}
        val1 = {'5d': _bt(3.1)}
        test1 = {'5d': _bt(3.0)}
        g = compute_gap_decomposition(val0, test0, test1, val1)
        self.assertEqual(g['per_label'][0]['cause'], '执行口径')
        self.assertEqual(g['exec_optimism']['val'], 2.7)
        self.assertEqual(g['exec_optimism']['test'], 1.0)

    def test_cause_regime_when_ic_stable(self):
        # T+1 差距未收敛但 RankIC 平移 → 环境主导
        val0, test0 = {'5d': _bt(5.0)}, {'5d': _bt(2.0)}
        val1, test1 = {'5d': _bt(4.0)}, {'5d': _bt(2.0)}
        ric = {'5d': {'train': 0.09, 'val': 0.068, 'test': 0.067}}
        g = compute_gap_decomposition(val0, test0, test1, val1, rank_ic=ric)
        self.assertEqual(g['per_label'][0]['cause'], '环境主导')

    def test_cause_model_decay_when_ic_drops(self):
        val0, test0 = {'5d': _bt(5.0)}, {'5d': _bt(2.0)}
        val1, test1 = {'5d': _bt(4.0)}, {'5d': _bt(2.0)}
        ric = {'5d': {'train': 0.09, 'val': 0.09, 'test': 0.02}}
        g = compute_gap_decomposition(val0, test0, test1, val1, rank_ic=ric)
        self.assertEqual(g['per_label'][0]['cause'], '模型退化')


class TestProtocolDefaults(unittest.TestCase):
    """克隆/旧配置升级：缺键补默认，显式选择不被覆盖。"""

    def test_missing_keys_get_defaults(self):
        out = with_training_protocol_defaults({'train_start': '2023-01-01'})
        self.assertEqual(out['selection_cv'], {'enabled': True, 'folds': 4, 'embargo_days': 25})
        self.assertEqual(out['ensemble'], {'seeds': 3})
        self.assertEqual(out['train_start'], '2023-01-01')   # 原键不动

    def test_explicit_off_not_overridden(self):
        out = with_training_protocol_defaults(
            {'selection_cv': {'enabled': False}, 'ensemble': {'seeds': 1}})
        self.assertEqual(out['selection_cv'], {'enabled': False})
        self.assertEqual(out['ensemble'], {'seeds': 1})

    def test_none_and_empty(self):
        self.assertEqual(with_training_protocol_defaults(None)['ensemble']['seeds'], 3)
        self.assertTrue(with_training_protocol_defaults({})['selection_cv']['enabled'])


class TestPurgedFolds(unittest.TestCase):
    def setUp(self):
        self.dates = [f'2026-01-{i:02d}' for i in range(1, 41)]   # 40 个"交易日"
        self.folds = _purged_time_folds(self.dates, folds=4, embargo=5)

    def test_folds_partition_all_dates(self):
        union = set().union(*[f for f, _ in self.folds])
        self.assertEqual(union, set(self.dates))
        # 折互斥
        all_elems = [d for f, _ in self.folds for d in f]
        self.assertEqual(len(all_elems), len(set(all_elems)))

    def test_no_leakage_between_train_and_fold(self):
        for fold, train in self.folds:
            self.assertFalse(fold & train)

    def test_embargo_respected(self):
        # 折 k 开始前 embargo 个交易日不得出现在训练集（标签前瞻泄漏窗）
        ds = self.dates
        for (fold, train), k in zip(self.folds, range(4)):
            lo = min(fold, key=ds.index)
            embargo_zone = set(ds[max(0, ds.index(lo) - 5):ds.index(lo)])
            self.assertFalse(embargo_zone & train, f'折{k} 前的 embargo 窗泄漏进训练集')

    def test_post_fold_dates_in_train(self):
        ds = self.dates
        for (fold, train), k in zip(self.folds, range(4)):
            hi = max(fold, key=ds.index)
            after = set(ds[ds.index(hi) + 1:])
            self.assertTrue(after <= train)

    def test_folds_clamped_for_tiny_input(self):
        # 日期太少时折数被压到合法范围且不抛异常
        folds = _purged_time_folds(self.dates[:6], folds=4, embargo=3)
        self.assertTrue(2 <= len(folds) <= 4)


if __name__ == '__main__':
    unittest.main()
