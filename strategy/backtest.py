"""回测框架 — 时间序列 Walk-Forward 验证 + 评估指标。

训练/验证/测试按时间顺序切分，严禁随机打乱。
"""
import pandas as pd
import numpy as np
from typing import List, Tuple, Dict
from dataclasses import dataclass, field


@dataclass
class BacktestResult:
    """单期回测结果。"""
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    signals: int = 0
    win_count: int = 0
    total_return: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0

@dataclass
class EvaluationReport:
    """汇总评估报告。"""
    sharpe: float = 0.0
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    annual_return: float = 0.0
    total_signals: int = 0
    fold_results: List[BacktestResult] = field(default_factory=list)
    fold_sharpes: List[float] = field(default_factory=list)


class BacktestEngine:
    """时间序列 Walk-Forward 回测引擎。"""

    def __init__(self, train_window: int = 500, test_window: int = 60, step: int = 60):
        """
        Args:
            train_window: 训练集交易天数
            test_window: 测试集交易天数
            step: 滚动步长（交易天数）
        """
        self.train_window = train_window
        self.test_window = test_window
        self.step = step

    def split_windows(self, dates: List[str]) -> List[Tuple[int, int, int, int]]:
        """生成 Walk-Forward 窗口索引 (train_start, train_end, test_start, test_end)。

        dates 已按时间升序排列。
        """
        n = len(dates)
        windows = []
        test_end = n - 1
        while test_end - self.test_window >= self.train_window:
            test_start = test_end - self.test_window + 1
            train_end = test_start - 1
            train_start = max(0, train_end - self.train_window + 1)
            windows.append((train_start, train_end, test_start, test_end))
            test_end -= self.step
        windows.reverse()
        return windows

    def evaluate_fold(self, df: pd.DataFrame, train_idx: Tuple[int, int],
                       test_idx: Tuple[int, int], signal_fn) -> BacktestResult:
        """单 fold 评估：训练集训练 → 测试集生成信号 → 计算指标。"""
        train_df = df.iloc[train_idx[0]:train_idx[1]+1]
        test_df = df.iloc[test_idx[0]:test_idx[1]+1]
        dates = df['trade_date'].values if 'trade_date' in df.columns else df.index.values

        result = BacktestResult(
            train_start=str(dates[train_idx[0]])[:10],
            train_end=str(dates[train_idx[1]])[:10],
            test_start=str(dates[test_idx[0]])[:10],
            test_end=str(dates[test_idx[1]])[:10],
        )

        # 用训练集拟合模型（此处为简化版：直接用 signal_fn 在测试集生成信号）
        signals = signal_fn(train_df, test_df)
        if not signals:
            return result

        result.signals = len(signals)
        returns = [s.get('return', 0) for s in signals if s.get('return') is not None]
        if not returns:
            return result

        result.win_count = sum(1 for r in returns if r > 0)
        result.total_return = sum(returns)
        result.sharpe = self._sharpe(returns)
        result.max_drawdown = self._max_drawdown(returns)
        return result

    def run(self, df: pd.DataFrame, signal_fn) -> EvaluationReport:
        """全量 Walk-Forward 回测。"""
        dates = df['trade_date'].astype(str).values if 'trade_date' in df.columns else df.index.astype(str).values
        windows = self.split_windows(list(dates))

        report = EvaluationReport()
        all_returns = []
        for w in windows:
            fold = self.evaluate_fold(df, (w[0], w[1]), (w[2], w[3]), signal_fn)
            report.fold_results.append(fold)
            report.fold_sharpes.append(fold.sharpe)
            report.total_signals += fold.signals
            if fold.signals > 0:
                all_returns.extend([fold.total_return / max(fold.signals, 1)] * fold.signals)

        if report.fold_results:
            report.sharpe = float(np.mean(report.fold_sharpes))
            win_folds = [f for f in report.fold_results if f.signals > 0]
            if win_folds:
                report.win_rate = sum(f.win_count for f in win_folds) / max(sum(f.signals for f in win_folds), 1)
            report.max_drawdown = max((f.max_drawdown for f in report.fold_results), default=0)
            if all_returns:
                report.annual_return = float(np.mean(all_returns)) * 252

        return report

    @staticmethod
    def _sharpe(returns: List[float], risk_free: float = 0.02) -> float:
        """夏普比率。"""
        if len(returns) < 2:
            return 0.0
        arr = np.array(returns)
        excess = np.mean(arr) - risk_free / 252
        std = np.std(arr, ddof=1)
        return float(excess / std * np.sqrt(252)) if std > 0 else 0.0

    @staticmethod
    def _max_drawdown(returns: List[float]) -> float:
        """最大回撤。"""
        if not returns:
            return 0.0
        cum = np.cumprod(1 + np.array(returns))
        peak = np.maximum.accumulate(cum)
        dd = (cum - peak) / peak
        return float(abs(min(dd)))
