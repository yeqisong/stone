"""DoubleEnsemble 模型（design/05 M6，从 Qlib contrib/model/double_ensemble.py 移植）。

结构（Qlib 语义逐条对应）：
- fit: 循环 num_models 次 → 样本权重 weights + 特征子集 features 训练 LightGBM 子模型
- retrieve_loss_curve: 逐树增量预测 → N×T 逐样本训练误差矩阵
- sample_reweight: h1=rank(-集成损失) + h2=rank(末段/首段误差比) → 分箱 → 1/(decay^k·bin+0.1)
- feature_selection: 逐特征 permutation → 集成损失增量 g 值 → 分箱按 sample_ratios 采样
- predict: Σ submodel_k(X[sub_features_k])·w_k / Σw

兼容约定：提供 feature_names_in_ + predict(ndarray)（列序=feature_names_in_），
使现有 predict_for_version/评估/walk-forward 栈无需改动即可加载（pickle）。
"""
from typing import List, Optional

import numpy as np
import pandas as pd
import lightgbm as lgb


class DEModel:
    """Double Ensemble（LightGBM 子模型）。

    Args:
        feature_names: 初始特征全集（有序）
        num_models: 子模型数；alpha1/alpha2: SR 的 h 权重
        bins_sr/bins_fs: 样本/特征分箱数；decay: 样本权重衰减
        sample_ratios: FS 每箱特征采样比（len==bins_fs）
        epochs: 每个子模型 boosting 轮数；early_stopping_rounds: 验证集早停
        lgb_params: LightGBM 参数（objective/learning_rate/num_leaves 等）
    """

    def __init__(self, feature_names: List[str], num_models: int = 5,
                 alpha1: float = 0.6, alpha2: float = 0.4,
                 bins_sr: int = 10, bins_fs: int = 5, decay: float = 0.1,
                 sample_ratios: Optional[List[float]] = None,
                 epochs: int = 200, early_stopping_rounds: int = 50,
                 lgb_params: Optional[dict] = None):
        self.feature_names_in_ = list(feature_names)
        self.num_models = num_models
        self.alpha1 = alpha1
        self.alpha2 = alpha2
        self.bins_sr = bins_sr
        self.bins_fs = bins_fs
        self.decay = decay if decay is not None else 0.1
        self.sample_ratios = list(sample_ratios) if sample_ratios is not None \
            else list(np.round(np.linspace(0.8, 0.4, bins_fs), 2))  # Qlib 风格递减，长度随 bins_fs
        if len(self.sample_ratios) != bins_fs:
            raise ValueError(f"sample_ratios 长度必须等于 bins_fs（{bins_fs}）")
        self.epochs = epochs
        self.early_stopping_rounds = early_stopping_rounds
        self.lgb_params = {"objective": "regression", "learning_rate": 0.05,
                           "num_leaves": 31, "verbose": -1}
        if lgb_params:
            self.lgb_params.update(lgb_params)
        self.ensemble: list = []       # 子模型列表
        self.sub_features: List[List[str]] = []  # 每个子模型使用的特征
        self.sub_weights = [1.0] * num_models
        self.fitted_ = False

    # ── 训练 ──
    def fit(self, X: pd.DataFrame, y: pd.Series,
            X_valid: Optional[pd.DataFrame] = None,
            y_valid: Optional[pd.Series] = None) -> "DEModel":
        """Qlib fit 循环：子模型训练 → loss 曲线 → SR（样本重加权）→ FS（特征筛选）。"""
        feats = list(self.feature_names_in_)
        x_train, y_train = X.loc[:, feats], y.reindex(X.index)
        weights = pd.Series(np.ones(len(x_train)), index=x_train.index)
        pred_sub = pd.DataFrame(np.zeros((len(x_train), self.num_models)), index=x_train.index)

        has_valid = X_valid is not None and y_valid is not None
        for k in range(self.num_models):
            self.sub_features.append(list(feats))
            model_k = self._train_submodel(x_train.loc[:, feats], y_train, weights,
                                           X_valid.loc[:, feats] if has_valid else None,
                                           y_valid if has_valid else None)
            self.ensemble.append(model_k)
            if k + 1 == self.num_models:
                break

            loss_curve = self._retrieve_loss_curve(model_k, x_train.loc[:, feats], y_train)
            pred_sub.iloc[:, k] = model_k.predict(x_train.loc[:, feats].values)
            w = np.array(self.sub_weights[:k + 1])
            pred_ensemble = (pred_sub.iloc[:, :k + 1] * w).sum(axis=1) / w.sum()
            loss_values = pd.Series(self._get_loss(y_train.values.squeeze(), pred_ensemble.values),
                                    index=x_train.index)
            weights = self._sample_reweight(loss_curve, loss_values, k + 1)

            feats = self._feature_selection(x_train, y_train, loss_values)
            if not feats:
                feats = list(self.feature_names_in_)
        self.fitted_ = True
        return self

    def _train_submodel(self, x_train, y_train, weights, x_valid=None, y_valid=None):
        dtrain = lgb.Dataset(x_train, label=y_train.values, weight=weights.values)
        callbacks = [lgb.log_evaluation(0)]
        valid_sets = [dtrain]
        if x_valid is not None and self.early_stopping_rounds:
            dvalid = lgb.Dataset(x_valid, label=y_valid.values, reference=dtrain)
            valid_sets.append(dvalid)
            callbacks.append(lgb.early_stopping(self.early_stopping_rounds, verbose=False))
        return lgb.train(self.lgb_params, dtrain, num_boost_round=self.epochs,
                         valid_sets=valid_sets, callbacks=callbacks)

    # ── N×T 逐样本误差矩阵（逐树增量预测，Qlib retrieve_loss_curve 原样）──
    def _retrieve_loss_curve(self, model, x_train: pd.DataFrame, y_train: pd.Series) -> pd.DataFrame:
        y = np.squeeze(y_train.values)
        n = x_train.shape[0]
        num_trees = model.num_trees()
        loss_curve = pd.DataFrame(np.zeros((n, num_trees)), index=x_train.index)
        pred_tree = np.zeros(n, dtype=float)
        for i_tree in range(num_trees):
            pred_tree += model.predict(x_train.values, start_iteration=i_tree, num_iteration=1)
            loss_curve.iloc[:, i_tree] = self._get_loss(y, pred_tree)
        return loss_curve

    # ── SR：样本重加权（Qlib sample_reweight 原样 + 除零保护）──
    def _sample_reweight(self, loss_curve: pd.DataFrame, loss_values: pd.Series, k_th: int) -> pd.Series:
        loss_curve_norm = loss_curve.rank(axis=0, pct=True)
        loss_values_norm = (-loss_values).rank(pct=True)

        n, t = loss_curve.shape
        part = max(int(t * 0.1), 1)
        l_start = loss_curve_norm.iloc[:, :part].mean(axis=1)
        l_end = loss_curve_norm.iloc[:, -part:].mean(axis=1)

        h1 = loss_values_norm
        h2 = (l_end / l_start.replace(0, np.nan)).rank(pct=True)
        h = pd.DataFrame({"h_value": self.alpha1 * h1 + self.alpha2 * h2})

        h["bins"] = pd.cut(h["h_value"], self.bins_sr)
        h_avg = h.groupby("bins", group_keys=False, observed=False)["h_value"].mean()
        weights = pd.Series(np.zeros(n, dtype=float), index=loss_curve.index)
        for b in h_avg.index:
            mask = h["bins"] == b
            if mask.any():
                weights[mask] = 1.0 / (self.decay ** k_th * h_avg[b] + 0.1)
        return weights

    # ── FS：特征筛选（permutation g 值 + 分箱采样，Qlib feature_selection 原样）──
    def _feature_selection(self, x_train: pd.DataFrame, y_train: pd.Series,
                           loss_values: pd.Series) -> List[str]:
        feats = list(x_train.columns)
        n, f = x_train.shape
        m = len(self.ensemble)
        g_value = np.zeros(f)
        rng = np.random.default_rng()

        for i_f, feat in enumerate(feats):
            x_tmp = x_train.copy()
            x_tmp[feat] = rng.permutation(x_tmp[feat].values)
            pred = pd.Series(np.zeros(n), index=x_tmp.index)
            for i_s, submodel in enumerate(self.ensemble):
                sub_feats = self.sub_features[i_s]
                pred += pd.Series(submodel.predict(x_tmp.loc[:, sub_feats].values),
                                  index=x_tmp.index) / m
            loss_feat = self._get_loss(y_train.values.squeeze(), pred.values)
            diff = loss_feat - loss_values.values
            g_value[i_f] = np.mean(diff) / (np.std(diff) + 1e-7)

        g = pd.Series(np.nan_to_num(g_value), index=feats)
        bins = pd.cut(g, self.bins_fs)
        res: List[str] = []
        # 按 g 值降序的箱依次采样（重要特征箱保留更高比例）；NaN 箱跳过
        def _bin_key(b):
            try:
                return float(b.right)
            except Exception:
                return -np.inf
        valid_bins = [b for b in bins.unique() if b is not None and b == b]
        for i_b, b in enumerate(sorted(valid_bins, key=_bin_key, reverse=True)):
            b_feats = [feats[i] for i in range(f) if bins.iloc[i] == b]
            if not b_feats:
                continue
            ratio = self.sample_ratios[min(i_b, len(self.sample_ratios) - 1)]
            num = int(np.ceil(ratio * len(b_feats)))
            take = rng.choice(b_feats, size=min(num, len(b_feats)), replace=False)
            res.extend(take.tolist())
        # 保序去重
        seen = set(res)
        return [x for x in feats if x in seen]

    @staticmethod
    def _get_loss(label: np.ndarray, pred: np.ndarray) -> np.ndarray:
        return (np.asarray(label, dtype=float) - np.asarray(pred, dtype=float)) ** 2

    # ── 预测（Σ submodel·w / Σw；输入 ndarray 列序=feature_names_in_）──
    def predict(self, X) -> np.ndarray:
        if not self.fitted_:
            raise RuntimeError("模型未训练")
        X = np.asarray(X, dtype=float)
        name_to_idx = {n: i for i, n in enumerate(self.feature_names_in_)}
        pred = np.zeros(X.shape[0], dtype=float)
        for submodel, sub_feats in zip(self.ensemble, self.sub_features):
            cols = [name_to_idx[f] for f in sub_feats]
            pred += submodel.predict(X[:, cols]) * 1.0
        return pred / len(self.ensemble)
