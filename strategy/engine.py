"""策略引擎主循环。全市场 5000 只 × 3 策略并行计算。"""
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from typing import List

from strategy.base import Signal
from strategy.bollinger import BollingerDaily
from strategy.divergence import VolumePriceDivergence
from strategy.weekly_trend import WeeklyTrend
from strategy.combiner import SignalCombiner
from strategy.preference import PREFERENCE_ADJUSTMENTS, apply_preference


class StrategyEngine:
    """策略计算引擎。"""

    def __init__(self):
        self.strategies = [
            BollingerDaily(),
            VolumePriceDivergence(),
            WeeklyTrend(),
        ]
        self.combiner = SignalCombiner()
        self.preference_mode = "balanced"

    def set_preference(self, mode: str):
        """设置全局偏好。mode: 'left' | 'right' | 'balanced'"""
        if mode not in ("left", "right", "balanced"):
            raise ValueError(f"无效偏好模式: {mode}")
        self.preference_mode = mode

    def get_adjuster(self):
        """获取参数修饰函数。"""
        adjustments = PREFERENCE_ADJUSTMENTS.get(self.preference_mode, {})
        return apply_preference(adjustments)

    def run_one_stock(self, df: pd.DataFrame, stock_code: str,
                      stock_name: str, signal_date: str,
                      strategy_params: dict) -> List[Signal]:
        """
        对单只股票执行所有已启用策略的计算。

        Args:
            df: 该股全部历史日线（close_hfq）
            stock_code: 股票代码
            stock_name: 股票名称
            signal_date: 信号日期
            strategy_params: {strategy_name: {enabled, params}} 从 strategy_config 读取

        Returns:
            原始信号列表（含组合信号）
        """
        adjuster = self.get_adjuster()
        all_signals = []

        # 确保 df 包含必要的列
        if 'stock_code' not in df.columns:
            df = df.copy()
            df['stock_code'] = stock_code
        if 'stock_name' not in df.columns:
            df = df.copy()
            df['stock_name'] = stock_name

        for strategy in self.strategies:
            cfg = strategy_params.get(strategy.name, {})
            if not cfg.get('enabled', True):
                continue

            # 检查数据量
            if len(df) < strategy.min_data_points():
                continue

            # 取基础参数 + 全局偏好修饰
            base_params = dict(cfg.get('params', strategy.default_params()))
            adjusted_params = adjuster(strategy.name, base_params)

            try:
                signals = strategy.calculate(df, adjusted_params)
                for s in signals:
                    s.preference = self.preference_mode
                    s.params_snapshot = json.dumps(adjusted_params, ensure_ascii=False)
                all_signals.extend(signals)
            except Exception as e:
                # 单策略异常不阻塞其他策略
                print(f"[WARN] {strategy.name} on {stock_code} failed: {e}")
                continue

        # 信号融合
        if all_signals:
            all_signals = self.combiner.merge(all_signals)

        return all_signals

    def run_all(self, data_loader, signal_date: str, strategy_params: dict,
                stock_filter: List[str] = None) -> List[Signal]:
        """
        全市场策略计算（并行版）。

        Args:
            data_loader: StrategyDataLoader 实例（需有 get_all_codes / load_stock_data 方法）
                         或 {stock_code: DataFrame} 预加载数据字典。
            signal_date: 信号日期
            strategy_params: 策略配置
            stock_filter: 要计算的股票代码列表，None = 全市场

        Returns:
            所有信号列表
        """
        # 支持直接传入 {stock_code: DataFrame} 字典（批量预加载）
        if isinstance(data_loader, dict):
            all_data = data_loader
            stock_filter = list(all_data.keys())
        else:
            if stock_filter is None:
                stock_filter = data_loader.get_all_codes()
            all_data = {}
            for code in stock_filter:
                try:
                    df = data_loader.load_stock_data(code)
                    if df is not None and len(df) > 0:
                        all_data[code] = df
                except Exception as e:
                    print(f"[ERROR] Loading {code}: {e}")

        # ── 并行计算 ──
        max_workers = min(8, os.cpu_count() or 4, len(all_data) or 1)

        def process_one(code: str) -> List[Signal]:
            df = all_data[code]
            name = str(df.iloc[-1].get('stock_name', code))
            return self.run_one_stock(df, code, name, signal_date, strategy_params)

        all_results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_one, code): code for code in all_data}
            for future in as_completed(futures):
                try:
                    all_results.extend(future.result())
                except Exception as e:
                    print(f"[ERROR] Stock {futures[future]}: {e}")

        return all_results
