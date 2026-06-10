"""策略基类与信号数据结构。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List


@dataclass
class Signal:
    stock_code: str
    stock_name: str
    signal_date: str          # YYYY-MM-DD
    direction: str            # 'buy' | 'sell' | 'neutral'
    strength: int             # 1-3
    strategy_name: str        # bollinger_daily | volume_price_divergence | weekly_trend
    reason: str
    price: float              # close_hfq
    suggested_action: str = ""
    preference: str = "balanced"
    combined_signal: bool = False       # 是否为融合信号
    source_strategies: list | None = None  # 融合信号的来源策略列表
    risk_note: str = "仅供理论学习和理论练习使用，不构成投资建议"

    def __post_init__(self):
        if self.source_strategies is None:
            self.source_strategies = []


class StrategyBase(ABC):
    """策略基类。所有策略必须继承并实现。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称，与 strategy_config.strategy_name 对应。"""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """中文显示名。"""
        ...

    @property
    def params_schema(self) -> dict:
        """参数 JSON Schema，用于飞书调参时的校验。子类可以覆盖。"""
        return {"type": "object", "properties": {}, "required": []}

    @abstractmethod
    def calculate(self, df, params: dict) -> List[Signal]:
        """
        计算策略信号。

        Args:
            df: pandas DataFrame，按 trade_date 升序，至少包含:
                trade_date, open, high, low, close(=close_hfq), volume
            params: 策略参数（已通过 params_schema 校验，并已由全局偏好修饰）

        Returns:
            Signal 列表（可以为空）
        """
        ...

    @abstractmethod
    def default_params(self) -> dict:
        """默认参数。"""
        ...

    def min_data_points(self) -> int:
        """最少需要多少个交易日数据。默认 0。"""
        return 0
