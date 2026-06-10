"""F-ST-001 日布林线买卖点策略。"""
import pandas as pd
from typing import List

from strategy.base import StrategyBase, Signal
from strategy.indicators import bollinger_bands, rsi, sma


class BollingerDaily(StrategyBase):

    name = "bollinger_daily"
    display_name = "日布林线"

    @property
    def params_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "period": {"type": "integer", "minimum": 10, "maximum": 60, "default": 20},
                "std_mult": {"type": "number", "minimum": 1.5, "maximum": 3.0, "default": 2.0},
                "bandwidth_threshold": {"type": "number", "minimum": 0.02, "maximum": 0.10, "default": 0.04},
            },
            "required": ["period", "std_mult"],
        }

    def default_params(self) -> dict:
        return {"period": 20, "std_mult": 2.0, "bandwidth_threshold": 0.04}

    def min_data_points(self) -> int:
        return 20

    def calculate(self, df: pd.DataFrame, params: dict) -> List[Signal]:
        n = len(df)
        if n < params["period"]:
            return []

        period = params["period"]
        std_mult = params["std_mult"]
        bw_threshold = params.get("bandwidth_threshold", 0.04)

        middle, upper, lower, bandwidth = bollinger_bands(
            df['close'], period=period, std_mult=std_mult
        )
        rsi_series = rsi(df['close'], period=14)
        vol_ma = sma(df['volume'], period=5)

        signals = []
        stock_code = str(df.iloc[-1].get('stock_code', ''))
        stock_name = str(df.iloc[-1].get('stock_name', ''))
        last_date = str(df.iloc[-1]['trade_date'])[:10]
        last_close = float(df.iloc[-1]['close'])

        # 需要至少 period+1 天来判断次日是否回到通道内
        if n < period + 1:
            return []

        # ── 布林带缩口变盘预警 ──
        prev_bandwidth = bandwidth.iloc[-2] if len(bandwidth) >= 2 else 1.0
        curr_bandwidth = bandwidth.iloc[-1]
        squeezing = curr_bandwidth < bw_threshold
        was_squeezing = prev_bandwidth < bw_threshold

        # ── 买入信号 ──
        # 强：收盘价从下方向上突破下轨后，次日回到通道内
        if n >= 3:
            prev2_close = float(df.iloc[-3]['close'])
            prev2_lower = float(lower.iloc[-3]) if not pd.isna(lower.iloc[-3]) else 0
            prev_close = float(df.iloc[-2]['close'])
            if prev2_close < prev2_lower and prev_close > float(lower.iloc[-2]):
                signals.append(Signal(
                    stock_code=stock_code, stock_name=stock_name,
                    signal_date=last_date, direction="buy", strength=3,
                    strategy_name=self.name, price=last_close,
                    reason=f"收盘价从下轨下方反弹回通道内（强力买入）",
                    suggested_action="关注建仓",
                ))

        # 中：触及下轨 + 缩口后开口 + 放量
        curr_vol = float(df.iloc[-1]['volume'])
        prev_vol = float(df.iloc[-2]['volume']) if n >= 2 else 0
        vol_ma_val = float(vol_ma.iloc[-1]) if not pd.isna(vol_ma.iloc[-1]) else 0
        near_lower = last_close <= float(lower.iloc[-1]) * 1.02 if not pd.isna(lower.iloc[-1]) else False

        if near_lower and was_squeezing and not squeezing and curr_vol > vol_ma_val:
            signals.append(Signal(
                stock_code=stock_code, stock_name=stock_name,
                signal_date=last_date, direction="buy", strength=2,
                strategy_name=self.name,
                reason=f"触及下轨+布林带缩口后开口+放量（中位买入）",
                price=last_close,
                suggested_action="关注",
            ))

        # 弱：通道下半区 + RSI 超卖
        rsi_val = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50
        mid_val = float(middle.iloc[-1]) if not pd.isna(middle.iloc[-1]) else last_close * 2
        in_lower_half = last_close < mid_val
        rsi_oversold = rsi_val < params.get('rsi_oversold', 30)

        if in_lower_half and rsi_oversold:
            signals.append(Signal(
                stock_code=stock_code, stock_name=stock_name,
                signal_date=last_date, direction="buy", strength=1,
                strategy_name=self.name,
                reason=f"通道下半区+RSI超卖({rsi_val:.0f})（弱位买入）",
                price=last_close,
                suggested_action="观察",
            ))

        # ── 卖出信号 ──
        # 强：从上方突破上轨后回到通道内
        if n >= 3:
            prev2_close = float(df.iloc[-3]['close'])
            prev2_upper = float(upper.iloc[-3]) if not pd.isna(upper.iloc[-3]) else 1e9
            prev_close = float(df.iloc[-2]['close'])
            if prev2_close > prev2_upper and prev_close < float(upper.iloc[-2]):
                signals.append(Signal(
                    stock_code=stock_code, stock_name=stock_name,
                    signal_date=last_date, direction="sell", strength=3,
                    strategy_name=self.name,
                    reason=f"收盘价从上轨上方回落通道内（强力卖出）",
                    price=last_close,
                    suggested_action="考虑减仓",
                ))

        # 中：触及上轨 + 缩口后开口 + 放量
        near_upper = last_close >= float(upper.iloc[-1]) * 0.98 if not pd.isna(upper.iloc[-1]) else False

        if near_upper and was_squeezing and not squeezing and curr_vol > vol_ma_val:
            signals.append(Signal(
                stock_code=stock_code, stock_name=stock_name,
                signal_date=last_date, direction="sell", strength=2,
                strategy_name=self.name,
                reason=f"触及上轨+布林带缩口后开口+放量（中位卖出）",
                price=last_close,
                suggested_action="考虑减仓",
            ))

        # 弱：通道上半区 + RSI 超买
        rsi_overbought = rsi_val > params.get('rsi_overbought', 70)
        in_upper_half = last_close > mid_val

        if in_upper_half and rsi_overbought:
            signals.append(Signal(
                stock_code=stock_code, stock_name=stock_name,
                signal_date=last_date, direction="sell", strength=1,
                strategy_name=self.name,
                reason=f"通道上半区+RSI超买({rsi_val:.0f})（弱位卖出）",
                price=last_close,
                suggested_action="观察",
            ))

        # ── 缩口变盘预警（不产生买卖信号，但返回 neutral）──
        if squeezing and not signals:
            signals.append(Signal(
                stock_code=stock_code, stock_name=stock_name,
                signal_date=last_date, direction="neutral", strength=0,
                strategy_name=self.name,
                reason=f"布林带缩口(bandwidth={curr_bandwidth:.3f})，变盘预警",
                price=last_close,
                suggested_action="关注变盘",
            ))

        return signals
