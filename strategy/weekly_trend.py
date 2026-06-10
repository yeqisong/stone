"""F-ST-003 周趋势买卖点策略。"""
import pandas as pd
import numpy as np
from typing import List

from strategy.base import StrategyBase, Signal
from strategy.indicators import aggregate_weekly, sma, macd, atr


class WeeklyTrend(StrategyBase):

    name = "weekly_trend"
    display_name = "周趋势"

    @property
    def params_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "fast_period": {"type": "integer", "minimum": 3, "maximum": 13, "default": 5},
                "slow_period": {"type": "integer", "minimum": 10, "maximum": 40, "default": 20},
            },
            "required": ["fast_period", "slow_period"],
        }

    def default_params(self) -> dict:
        return {"fast_period": 5, "slow_period": 20}

    def min_data_points(self) -> int:
        return 20 * 5  # 至少20周 ≈ 100 个交易日

    def calculate(self, df: pd.DataFrame, params: dict) -> List[Signal]:
        weekly = aggregate_weekly(df)
        if len(weekly) < params["slow_period"]:
            return []

        fast = params["fast_period"]
        slow = params["slow_period"]

        fast_ma = sma(weekly['close'], fast)
        slow_ma = sma(weekly['close'], slow)
        dif, dea, macd_bar = macd(weekly['close'])
        atr_val = atr(weekly, 14)

        stock_code = str(df.iloc[-1].get('stock_code', ''))
        stock_name = str(df.iloc[-1].get('stock_name', ''))
        last_date = str(df.iloc[-1]['trade_date'])[:10]
        last_close = float(df.iloc[-1]['close'])

        n = len(weekly)
        signals = []

        # ── 金叉/死叉检测 ──
        golden_cross = False
        death_cross = False

        if n >= 2:
            prev_fast = float(fast_ma.iloc[-2]) if not pd.isna(fast_ma.iloc[-2]) else 0
            prev_slow = float(slow_ma.iloc[-2]) if not pd.isna(slow_ma.iloc[-2]) else 0
            curr_fast = float(fast_ma.iloc[-1]) if not pd.isna(fast_ma.iloc[-1]) else 0
            curr_slow = float(slow_ma.iloc[-1]) if not pd.isna(slow_ma.iloc[-1]) else 0

            golden_cross = prev_fast <= prev_slow and curr_fast > curr_slow
            death_cross = prev_fast >= prev_slow and curr_fast < curr_slow

        # ── MACD 零轴位置 ──
        dif_val = float(dif.iloc[-1]) if not pd.isna(dif.iloc[-1]) else 0
        dea_val = float(dea.iloc[-1]) if not pd.isna(dea.iloc[-1]) else 0
        macd_golden = False
        macd_death = False
        if n >= 2:
            prev_dif = float(dif.iloc[-2]) if not pd.isna(dif.iloc[-2]) else 0
            prev_dea = float(dea.iloc[-2]) if not pd.isna(dea.iloc[-2]) else 0
            macd_golden = prev_dif <= prev_dea and dif_val > dea_val
            macd_death = prev_dif >= prev_dea and dif_val < dea_val
        dif_above_zero = dif_val > 0
        dif_below_zero = dif_val < 0

        # ── 成交量确认 ──
        curr_vol = float(weekly['volume'].iloc[-1])
        vol_ma_5 = float(sma(weekly['volume'], 5).iloc[-1]) if not pd.isna(sma(weekly['volume'], 5).iloc[-1]) else curr_vol
        vol_confirmed = curr_vol > vol_ma_5
        vol_strong = curr_vol > vol_ma_5 * params.get('vol_strong_mult', 1.5)

        # ── 买入信号 ──
        # 强：金叉 + MACD 金叉在零轴上方
        if golden_cross and macd_golden and dif_above_zero:
            vol_note = "放量确认" if vol_confirmed else ""
            signals.append(Signal(
                stock_code=stock_code, stock_name=stock_name,
                signal_date=last_date, direction="buy", strength=3,
                strategy_name=self.name,
                reason=f"5周均线上穿20周均线+MACD零轴上方金叉 {vol_note}",
                price=last_close,
                suggested_action="关注建仓",
            ))

        # 中：金叉 + MACD 金叉在零轴下方
        elif golden_cross and macd_golden and dif_below_zero:
            signals.append(Signal(
                stock_code=stock_code, stock_name=stock_name,
                signal_date=last_date, direction="buy", strength=2,
                strategy_name=self.name,
                reason=f"5周均线上穿20周均线+MACD零轴下方金叉（中期反弹信号）",
                price=last_close,
                suggested_action="关注",
            ))

        # 弱：仅金叉（MACD 方向不明确时也出弱信号）
        elif golden_cross:
            signals.append(Signal(
                stock_code=stock_code, stock_name=stock_name,
                signal_date=last_date, direction="buy", strength=1,
                strategy_name=self.name,
                reason=f"5周均线上穿20周均线",
                price=last_close,
                suggested_action="观察",
            ))

        # ── 卖出信号 ──
        # 强：死叉 + MACD 死叉在零轴下方
        if death_cross and macd_death and dif_below_zero:
            vol_note = "放量确认" if vol_confirmed else ""
            signals.append(Signal(
                stock_code=stock_code, stock_name=stock_name,
                signal_date=last_date, direction="sell", strength=3,
                strategy_name=self.name,
                reason=f"5周均线下穿20周均线+MACD零轴下方死叉 {vol_note}",
                price=last_close,
                suggested_action="建议减仓或清仓",
            ))

        # 中：死叉 + MACD 死叉在零轴上方
        elif death_cross and macd_death and dif_above_zero:
            signals.append(Signal(
                stock_code=stock_code, stock_name=stock_name,
                signal_date=last_date, direction="sell", strength=2,
                strategy_name=self.name,
                reason=f"5周均线下穿20周均线+MACD零轴上方死叉（中期调整信号）",
                price=last_close,
                suggested_action="考虑减仓",
            ))

        # 弱：仅死叉
        elif death_cross:
            signals.append(Signal(
                stock_code=stock_code, stock_name=stock_name,
                signal_date=last_date, direction="sell", strength=1,
                strategy_name=self.name,
                reason=f"5周均线下穿20周均线",
                price=last_close,
                suggested_action="观察",
            ))

        return signals
