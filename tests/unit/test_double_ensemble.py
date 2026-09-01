"""DoubleEnsemble 单测（design/05 M6：SR 公式手工对照 / loss 曲线 / FS 子集 / pickle 往返）。"""
import pickle

import numpy as np
import pandas as pd
import pytest

from strategy.models.double_ensemble import DEModel


def _synthetic(n=600, seed=7):
    """y = 2·f1 − f2 + 0.1·f3 + 噪声；f4 纯噪声。"""
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.normal(size=(n, 4)), columns=['f1', 'f2', 'f3', 'f4'])
    y = 2 * X['f1'] - X['f2'] + 0.1 * X['f3'] + rng.normal(scale=0.05, size=n)
    return X, y


def _tiny_model(**kw):
    base = dict(num_models=2, epochs=15, early_stopping_rounds=5, bins_sr=4, bins_fs=3)
    base.update(kw)
    X, y = _synthetic()
    return DEModel(list(X.columns), **base), X, y


def test_fit_predict_beats_mean():
    X, y = _synthetic()
    model = DEModel(list(X.columns), num_models=2, epochs=20, early_stopping_rounds=5,
                    bins_sr=4, bins_fs=3).fit(X, y)
    pred = model.predict(X.values)
    assert pred.shape == (len(y),)
    mse_model = float(np.mean((pred - y.values) ** 2))
    mse_mean = float(np.mean((y.mean() - y.values) ** 2))
    assert mse_model < mse_mean * 0.5, f"模型应显著优于均值基线: {mse_model} vs {mse_mean}"
    assert model.fitted_ and len(model.ensemble) == 2


def test_loss_curve_decreasing():
    """逐树累计 MSE 应随树数下降（前若干树内单调改善）。"""
    m, X, y = _tiny_model()
    model = DEModel(list(X.columns), num_models=1, epochs=20, early_stopping_rounds=0 or 5,
                    bins_sr=4, bins_fs=3).fit(X, y)
    X, y = _synthetic()
    curve = model._retrieve_loss_curve(model.ensemble[0], X, y)
    assert curve.shape[1] == model.ensemble[0].num_trees()
    assert curve.iloc[:, -1].mean() < curve.iloc[:, 0].mean()


def test_sample_reweight_manual():
    """构造 loss 曲线手工对照 weights = 1/(decay^k·bin_avg + 0.1)。

    3 样本 × 2 轮：loss_curve = [[1,0.5],[4,2.0],[9,4.5]]（rank 后逐列 pct），
    loss_values = [0.25, 1.0, 4.0]（集成损失）。
    """
    m = DEModel(['f1'], decay=0.1, alpha1=1.0, alpha2=1.0, bins_sr=3, num_models=2)
    lc = pd.DataFrame({0: [1.0, 4.0, 9.0], 1: [0.5, 2.0, 4.5]},
                      index=['s1', 's2', 's3'])
    lv = pd.Series([0.25, 1.0, 4.0], index=['s1', 's2', 's3'])
    w = m._sample_reweight(lc, lv, k_th=1)
    # 手工推导（pandas rank pct，并列取平均）：
    # loss_curve 两列 rank 相同：s1=1/3, s2=2/3, s3=1 → l_start=l_end
    # → l_end/l_start 恒 1 → 并列 → h2 全体 = 2/3（平均秩）
    # h1 = rank(-lv) = [1, 2/3, 1/3]（s1 集成损失最小=已学好 → h1 最高）
    # h = h1 + h2 = [5/3, 4/3, 1.0] → cut(3) 三箱 → bin_avg = h 本身
    # w = 1/(0.1·h + 0.1) → [3.75, 4.2857, 5.0]
    assert w['s1'] == pytest.approx(1 / (0.1 * 5 / 3 + 0.1), rel=1e-3)
    assert w['s2'] == pytest.approx(1 / (0.1 * 4 / 3 + 0.1), rel=1e-3)
    assert w['s3'] == pytest.approx(1 / (0.1 * 1.0 + 0.1), rel=1e-3)
    # 难样本（损失低=已学好）权重低，易学样本权重高 → 单调
    assert w['s1'] < w['s2'] < w['s3']


def test_feature_selection_subset():
    """FS 返回特征全集的非空子集。"""
    X, y = _synthetic()
    m = DEModel(list(X.columns), num_models=1, epochs=10, early_stopping_rounds=0,
                bins_sr=4, bins_fs=3).fit(X, y)
    loss_values = pd.Series(np.random.default_rng(1).random(len(X)), index=X.index)
    feats = m._feature_selection(X, y, loss_values)
    assert feats and set(feats) <= set(X.columns)


def test_pickle_roundtrip():
    """pickle 往返后预测一致（predict_for_version 加载兼容性）。"""
    m, X, y = _tiny_model()
    model = m.fit(X, y)
    p1 = model.predict(X.values)
    model2 = pickle.loads(pickle.dumps(model))
    p2 = model2.predict(X.values)
    assert np.allclose(p1, p2)
    assert model2.feature_names_in_ == model.feature_names_in_


def test_unfitted_raises():
    m = DEModel(['f1'])
    with pytest.raises(RuntimeError):
        m.predict(np.zeros((3, 1)))
