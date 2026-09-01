"""账户：现金/持仓/延迟结算/净值/换手记录（对齐 design/05 §3.4 与 Qlib Position）。

- settle_delay=True：卖出所得进 cash_delay，当日不可用，次日 settle_commit 并入现金
  （Qlib Position settle_start/commit 语义——卖出当日不能用于买入，比 v1 更贴近实盘）
- count_days：每交易日全部持仓 +1，T+1 由"当日买入当日不进入卖出检查"天然成立
"""
from datetime import date as _date
from typing import Dict, List, Optional

from .models import DailyRecord, Fill, Position, TradeConfig


class Account:
    def __init__(self, cfg: TradeConfig):
        self.cfg = cfg
        self.cash = cfg.initial_cash
        self.cash_delay = 0.0
        self.positions: Dict[str, Position] = {}
        self.records: List[DailyRecord] = []
        self.trades: List[dict] = []

    # ── 结算 ──
    def settle_commit(self) -> None:
        """次日把延迟结算资金并入可用现金。"""
        if self.cash_delay > 1e-9:
            self.cash += self.cash_delay
            self.cash_delay = 0.0

    # ── 记账 ──
    def apply_fills(self, fills: List[Fill], trade_date: str) -> None:
        for f in fills:
            if f.rejected or f.shares <= 0:
                continue
            if f.order.direction == 'BUY':
                self.cash -= f.gross + f.fee
                p = self.positions.get(f.order.stock_code)
                if p is None:
                    self.positions[f.order.stock_code] = Position(
                        code=f.order.stock_code, shares=f.shares, buy_price=f.price,
                        buy_date=trade_date, cost_basis=f.gross + f.fee, peak=f.price)
                else:
                    # 加仓：合并成本（含新费用）
                    total_cost = p.cost_basis + f.gross + f.fee
                    p.shares += f.shares
                    p.buy_price = total_cost / p.shares
                    p.cost_basis = total_cost
                self.trades.append({'action': 'BUY', 'stock_code': f.order.stock_code,
                                    'date': trade_date, 'price': round(f.price, 4),
                                    'shares': f.shares, 'amount': round(f.gross + f.fee, 2),
                                    'fee': round(f.fee, 2), 'reason': f.order.reason,
                                    'rejected': False})
            else:
                p = self.positions[f.order.stock_code]
                net = f.gross - f.fee
                if self.cfg.settle_delay:
                    self.cash_delay += net
                else:
                    self.cash += net
                pnl = net - p.cost_basis * (f.shares / p.shares)  # 部分卖出按股数比例摊成本
                # gross_win：卖出价>买入价（v1 win_rate 口径，不含费用；M7 兼容层数字不变的关键）
                gross_win = f.price > p.buy_price
                # gross_win：卖出价>买入价（v1 win_rate 口径，不含费用；M7 兼容层数字不变的关键）
                gross_win = f.price > p.buy_price
                p.shares -= f.shares
                if p.shares <= 0:
                    del self.positions[f.order.stock_code]
                self.trades.append({'action': 'SELL', 'stock_code': f.order.stock_code,
                                    'date': trade_date, 'price': round(f.price, 4),
                                    'shares': f.shares, 'amount': round(f.gross, 2),
                                    'fee': round(f.fee, 2), 'pnl': round(pnl, 2),
                                    'gross_win': gross_win,
                                    'reason': f.order.reason, 'rejected': False})

    def age_positions(self) -> None:
        for p in self.positions.values():
            p.count_days += 1

    # ── 估值 ──
    def mark_to_market(self, close_map: Dict[str, float], trade_date: str,
                       day_amount: float = 0.0, day_cost: float = 0.0,
                       bench: Optional[float] = None) -> float:
        pos_val = 0.0
        for code, p in self.positions.items():
            cur = close_map.get(code, p.buy_price)
            pos_val += p.shares * cur
            p.peak = max(p.peak, cur)
        equity = self.cash + self.cash_delay + pos_val
        self.records.append(DailyRecord(
            trade_date=trade_date, account=equity, cash=self.cash + self.cash_delay,
            position_value=pos_val, turnover=day_amount, cost=day_cost, bench=bench))
        return equity

    def get_cash(self) -> float:
        return self.cash
