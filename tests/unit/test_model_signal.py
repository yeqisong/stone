"""模型信号辅助函数单元测试。

覆盖两处曾导致「只有买入、没有卖出」的回归点：
① `_model_feature_names`：SeedEnsemble 顶层没有 feature_names_in_，必须回落到成员模型；
② `_sell_strength`：卖出强度按截面分位分档，避免分位阈值语义漂移。
"""
import unittest

import numpy as np
import pandas as pd

from scripts.pipeline import _feature_coverage, _model_feature_names, _sell_strength


class _SingleModel:
    def __init__(self, names):
        self.feature_names_in_ = np.array(names, dtype=object)


class _MemberModel(_SingleModel):
    pass


class _SeedEnsemble:
    """模拟 SeedEnsemble：顶层只有 models 列表，没有 feature_names_in_。"""

    def __init__(self, members):
        self.models = members


class _Booster:
    feature_names = ["a", "b"]


class _BoosterModel:
    def get_booster(self):
        return _Booster()


class TestModelFeatureNames(unittest.TestCase):
    def test_single_model_names(self):
        model = _SingleModel(["a158_CORR_10", "a158_VOLUME_20"])
        self.assertEqual(_model_feature_names(model, {}),
                         ["a158_CORR_10", "a158_VOLUME_20"])

    def test_seed_ensemble_uses_member_names(self):
        """v20.0 事故：顶层无 feature_names_in_，旧实现直接报错并回退买全市场。"""
        model = _SeedEnsemble([_MemberModel(["f1", "f2", "idx_ret_20d"])])
        self.assertEqual(_model_feature_names(model, {}),
                         ["f1", "f2", "idx_ret_20d"])

    def test_booster_fallback(self):
        self.assertEqual(_model_feature_names(_BoosterModel(), {}), ["a", "b"])

    def test_config_fallback(self):
        class _NoNames:
            pass

        self.assertEqual(_model_feature_names(_NoNames(), {"feature_names": ["x", "y"]}),
                         ["x", "y"])

    def test_empty_when_no_source(self):
        class _NoNames:
            pass

        self.assertEqual(_model_feature_names(_NoNames(), {}), [])


class TestFeatureCoverage(unittest.TestCase):
    def test_all_columns_dense(self):
        df = pd.DataFrame({"f1": [1.0, 2.0, 3.0], "f2": [1.0, 2.0, 3.0]})
        self.assertEqual(_feature_coverage(df, ["f1", "f2"]), [])

    def test_missing_column_is_zero_coverage(self):
        df = pd.DataFrame({"f1": [1.0, 2.0, 3.0]})
        self.assertEqual(_feature_coverage(df, ["f1", "f2"]), ["f2:0%"])

    def test_thin_column_rejected(self):
        """feature_compute 逐特征重算时整列会被短暂删除，预测必须拒绝。"""
        df = pd.DataFrame({"f1": [1.0, None, None, None]})
        self.assertEqual(_feature_coverage(df, ["f1"]), ["f1:25%"])

    def test_threshold_boundary(self):
        df = pd.DataFrame({"f1": [1.0, 2.0, None, None]})  # 50% 恰好不低于阈值
        self.assertEqual(_feature_coverage(df, ["f1"]), [])


class TestSellStrength(unittest.TestCase):
    def test_strength_tiers(self):
        # sell_thr=0.4：<20% 强卖 3；[20%,30%) 2；[30%,40%) 1
        self.assertEqual(_sell_strength(0.05, 0.4), 3)
        self.assertEqual(_sell_strength(0.199, 0.4), 3)
        self.assertEqual(_sell_strength(0.2, 0.4), 2)
        self.assertEqual(_sell_strength(0.299, 0.4), 2)
        self.assertEqual(_sell_strength(0.3, 0.4), 1)
        self.assertEqual(_sell_strength(0.399, 0.4), 1)

    def test_above_threshold_not_called_by_caller(self):
        # 强度函数本身不做触发判断；调用方先比较 rank_pct < sell_thr
        self.assertEqual(_sell_strength(0.39, 0.4), 1)


if __name__ == "__main__":
    unittest.main()
