"""F-ST-002 量价背离策略。检测日/周/月三周期量价背离形态。"""
import pandas as pd
import numpy as np
from typing import List

from strategy.base import StrategyBase, Signal
from strategy.indicators import aggregate_weekly, aggregate_monthly, sma


class VolumePriceDivergence(StrategyBase):

    name = "volume_price_divergence"
    display_name = "量价背离"

    @property
    def params_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "levels": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
                    "default": ["daily", "weekly", "monthly"],
                },
                "lookback_daily": {"type": "integer", "minimum": 10, "maximum": 60, "default": 20},
                "lookback_weekly": {"type": "integer", "minimum": 12, "maximum": 52, "default": 24},
                "lookback_monthly": {"type": "integer", "minimum": 6, "maximum": 24, "default": 12},
                "min_gap_daily": {"type": "integer", "minimum": 3, "maximum": 10, "default": 5},
                "min_gap_weekly": {"type": "integer", "minimum": 2, "maximum": 8, "default": 3},
                "min_gap_monthly": {"type": "integer", "minimum": 1, "maximum": 4, "default": 2},
                "vol_ratio_threshold": {"type": "number", "minimum": 0.5, "maximum": 1.0, "default": 0.8},
            },
            "required": ["levels"],
        }

    def default_params(self) -> dict:
        return {
            "levels": ["daily", "weekly", "monthly"],
            "lookback_daily": 20,
            "lookback_weekly": 24,
            "lookback_monthly": 12,
            "min_gap_daily": 5,
            "min_gap_weekly": 3,
            "min_gap_monthly": 2,
            "vol_ratio_threshold": 0.8,
        }

    def min_data_points(self) -> int:
        return 20

    def calculate(self, df: pd.DataFrame, params: dict) -> List[Signal]:
        levels = params.get("levels", ["daily", "weekly", "monthly"])
        signals = []

        stock_code = str(df.iloc[-1].get('stock_code', ''))
        stock_name = str(df.iloc[-1].get('stock_name', ''))
        last_date = str(df.iloc[-1]['trade_date'])[:10]
        last_close = float(df.iloc[-1]['close'])

        # 日线级别
        if "daily" in levels:
            daily_signals = self._detect_divergence(
                df, "daily",
                lookback=params["lookback_daily"],
                min_gap=params.get("min_gap_daily", 5),
                vol_ratio=params.get("vol_ratio_threshold", 0.8),
                stock_code=stock_code, stock_name=stock_name,
                signal_date=last_date, price=last_close,
            )
            signals.extend(daily_signals)

        # 周线级别
        if "weekly" in levels:
            weekly = aggregate_weekly(df)
            if len(weekly) >= params["lookback_weekly"]:
                weekly_signals = self._detect_divergence(
                    weekly, "weekly",
                    lookback=params["lookback_weekly"],
                    min_gap=params.get("min_gap_weekly", 3),
                    vol_ratio=params.get("vol_ratio_threshold", 0.85),
                    stock_code=stock_code, stock_name=stock_name,
                    signal_date=last_date, price=last_close,
                )
                signals.extend(weekly_signals)

        # 月线级别
        if "monthly" in levels:
            monthly = aggregate_monthly(df)
            if len(monthly) >= params["lookback_monthly"]:
                monthly_signals = self._detect_divergence(
                    monthly, "monthly",
                    lookback=params["lookback_monthly"],
                    min_gap=params.get("min_gap_monthly", 2),
                    vol_ratio=params.get("vol_ratio_threshold", 0.9),
                    stock_code=stock_code, stock_name=stock_name,
                    signal_date=last_date, price=last_close,
                )
                signals.extend(monthly_signals)

        return signals

    def _detect_divergence(
        self, df: pd.DataFrame, level: str, lookback: int, min_gap: int,
        vol_ratio: float, stock_code: str, stock_name: str,
        signal_date: str, price: float,
    ) -> List[Signal]:
        """检测单个周期的背离信号。"""
        n = len(df)
        if n < lookback:
            return []

        close = df['close'].values
        volume = df['volume'].values
        signals = []

        # 在 lookback 窗口内寻找价格极值点
        window_close = close[-lookback:]
        window_vol = volume[-lookback:]

        # 顶背离：价格创新高但成交量未能同步创新高
        price_high_idx = int(np.argmax(window_close))
        price_high = float(window_close[price_high_idx])
        vol_at_high = float(window_vol[price_high_idx])

        # 在前方寻找前一个高点（需间隔 ≥ min_gap）
        prev_section = window_close[:max(0, len(window_close) - min_gap)]
        if len(prev_section) > 0:
            prev_high_idx = int(np.argmax(prev_section))
            prev_high = float(prev_section[prev_high_idx])
            prev_vol_at_high = float(window_vol[prev_high_idx])

            if price_high > prev_high and vol_at_high < prev_vol_at_high * vol_ratio:
                signals.append(Signal(
                    stock_code=stock_code, stock_name=stock_name,
                    signal_date=signal_date, direction="sell", strength=2,
                    strategy_name=self.name,
                    reason=f"{level}线顶背离（价创新高量未跟进）",
                    price=price,
                    suggested_action="考虑减仓",
                ))

        # 底背离：价格创新低但成交量未能同步创新低
        price_low_idx = int(np.argmin(window_close))
        price_low = float(window_close[price_low_idx])
        vol_at_low = float(window_vol[price_low_idx])

        prev_section_low = window_close[:max(0, len(window_close) - min_gap)]
        if len(prev_section_low) > 0:
            prev_low_idx = int(np.argmin(prev_section_low))
            prev_low = float(prev_section_low[prev_low_idx])
            prev_vol_at_low = float(window_vol[prev_low_idx])

            if price_low < prev_low and vol_at_low < prev_vol_at_low * vol_ratio:
                signals.append(Signal(
                    stock_code=stock_code, stock_name=stock_name,
                    signal_date=signal_date, direction="buy", strength=2,
                    strategy_name=self.name,
                    reason=f"{level}线底背离（价创新低量未萎缩）",
                    price=price,
                    suggested_action="关注建仓",
                ))

        # 量增价滞 (卖出信号): 成交量持续放大但价格涨幅收窄
        if n >= 10:
            recent_vol_ma = sma(df['volume'], 5)
            recent_price_chg = df['close'].pct_change(5).iloc[-1]
            prev_price_chg = df['close'].pct_change(5).iloc[-6] if n >= 11 else 0

            vol_expanding = float(df['volume'].iloc[-1]) > float(recent_vol_ma.iloc[-1]) * 1.2 if not pd.isna(recent_vol_ma.iloc[-1]) else False
            price_decelerating = abs(float(recent_price_chg)) < abs(float(prev_price_chg)) if prev_price_chg else False

            if vol_expanding and price_decelerating and float(recent_price_chg) > 0:
                signals.append(Signal(
                    stock_code=stock_code, stock_name=stock_name,
                    signal_date=signal_date, direction="sell", strength=1,
                    strategy_name=self.name,
                    reason=f"{level}线量增价滞（放量滞涨）",
                    price=price,
                    suggested_action="观察",
                ))

        return signals
