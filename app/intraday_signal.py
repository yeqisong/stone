"""持仓盘中动作信号（纯函数）。

与「盘中雷达」的 VWAP 偏离/日位置/涨速结合，把价格变化翻译成**持仓动作**：
- reduce / trim：超配 + 反弹 → 减仓（不是追买）
- stop：跌破成本止损线 → 减仓/止损
- reduce_watch：冲高未站稳 VWAP → 反弹不追，优先减
- hold：站上 VWAP、日内偏强且未超配 → 持有，不追加
- watch_stop：跌破 VWAP → 跌破前低/止损位减仓

设计原则：只对持仓给动作；系统在仓位高于目标时不会给“买入”信号。
"""
from __future__ import annotations

from typing import Optional


def holding_signal(
    price: Optional[float],
    chg_pct: Optional[float],
    vwap_dev: Optional[float],
    day_pos: Optional[float],
    cost: Optional[float],
    weight: Optional[float],
    max_single: float = 0.20,
    stop_loss: float = -0.08,
) -> dict:
    """返回 {action, level, reason}。price/cost 不复权，weight 为单票市值/账户权益。"""
    price = float(price or 0)
    chg = float(chg_pct or 0)
    weight = float(weight or 0)
    vs_cost = (price / cost - 1) if price and cost else None

    if cost and weight >= max_single and chg >= 2.0:
        return {
            "action": "reduce", "level": "warn",
            "reason": f"日内 {chg:+.2f}%，单票 {weight*100:.1f}% 占账户超配；反弹减仓，不补",
        }
    if vs_cost is not None and vs_cost <= stop_loss:
        return {
            "action": "stop", "level": "critical",
            "reason": f"浮亏 {vs_cost*100:+.1f}%，已破止损线；减仓/止损",
        }
    if weight >= max_single and chg > 0:
        return {
            "action": "trim", "level": "warn",
            "reason": f"单票 {weight*100:.1f}% 占账户超配；逢强减仓到 20% 以下",
        }
    if chg >= 2.0 and vwap_dev is not None and vwap_dev < 0:
        return {
            "action": "reduce_watch", "level": "warn",
            "reason": "日内冲高但未站稳 VWAP；反弹不追，优先减",
        }
    if chg > 0 and (vwap_dev is None or vwap_dev >= 0) and (day_pos is None or day_pos >= 50):
        return {
            "action": "hold", "level": "ok",
            "reason": "站上 VWAP、日内位置偏强；持有，不追加",
        }
    if chg < 0 and vwap_dev is not None and vwap_dev < -0.5:
        return {
            "action": "watch_stop", "level": "warn",
            "reason": "跌破 VWAP；跌破前低/止损位就减仓",
        }
    return {"action": "hold", "level": "info", "reason": "信号不明显；按计划持有，不追加"}
