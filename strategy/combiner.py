"""F-ST-004 多策略信号融合。3 路独立信号投票。"""
from typing import List
from collections import Counter

from strategy.base import Signal


class SignalCombiner:
    """将 3 个策略的原始信号融合为综合买卖建议。"""

    def merge(self, all_signals: List[Signal]) -> List[Signal]:
        """
        输入：所有策略的原始信号列表（可能包含多个股票、多个策略）。

        融合规则:
        - 同一股票，3 策略同向 → ★★★
        - 同一股票，2 策略同向 → ★★
        - 同一股票，单策略 → ★
        - 同一股票，买卖矛盾 → 多空分歧，不推送
        - 3 策略全 neutral → 不产生融合信号

        返回：原始信号 + 融合后的 combined_signal
        """
        # 按股票分组
        by_stock: dict[str, List[Signal]] = {}
        for s in all_signals:
            key = s.stock_code
            if key not in by_stock:
                by_stock[key] = []
            by_stock[key].append(s)

        results = list(all_signals)  # 保留所有原始信号

        for code, signals in by_stock.items():
            # 统计方向
            directions = [s.direction for s in signals if s.direction in ('buy', 'sell')]
            if not directions:
                continue

            counter = Counter(directions)
            buy_count = counter.get('buy', 0)
            sell_count = counter.get('sell', 0)
            total = buy_count + sell_count

            # 矛盾 → 不融合
            if buy_count > 0 and sell_count > 0:
                results.append(Signal(
                    stock_code=code,
                    stock_name=signals[0].stock_name,
                    signal_date=signals[0].signal_date,
                    direction="neutral",
                    strength=0,
                    strategy_name="signal_combiner",
                    reason=f"多空分歧（买{buy_count}卖{sell_count}），建议观望",
                    price=signals[0].price,
                    suggested_action="关注，暂不操作",
                    combined_signal=True,
                    source_strategies=[s.strategy_name for s in signals],
                ))
                continue

            # 同向 → 叠加
            direction = 'buy' if buy_count > 0 else 'sell'

            if total >= 3:
                strength = 3
                desc = "三策略共振"
            elif total >= 2:
                strength = 2
                desc = "两策略确认"
            else:
                strength = 1
                desc = "单策略信号"

            source_list = [s.strategy_name for s in signals if s.direction == direction]
            reason_text = " + ".join([s.reason.split("（")[0].strip() for s in signals if s.direction == direction])

            results.append(Signal(
                stock_code=code,
                stock_name=signals[0].stock_name,
                signal_date=signals[0].signal_date,
                direction=direction,
                strength=strength,
                strategy_name="signal_combiner",
                reason=f"{reason_text}（{desc}，确定性{'高' if strength>=3 else '中' if strength>=2 else '一般'}）",
                price=signals[0].price,
                suggested_action="关注建仓" if direction == 'buy' else "考虑减仓",
                preference=signals[0].preference,
                combined_signal=True,
                source_strategies=source_list,
            ))

        return results
