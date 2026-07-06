"""独立回测引擎（v2.0 重构 迭代 5.3）。

CrossSectionalBacktest / TimeSeriesBacktest 策略模式工厂。
"""
from typing import Dict, List, Optional
from datetime import date
from sqlalchemy import text
from loguru import logger


class BacktestResult:
    def __init__(self):
        self.total_return: float = 0
        self.sharpe_ratio: float = 0
        self.win_rate: float = 0
        self.max_drawdown: float = 0
        self.annual_return: float = 0
        self.total_trades: int = 0
        self.winning_trades: int = 0
        self.start_equity: float = 0
        self.final_equity: float = 0
        self.trades: List[Dict] = []


class BacktestEngine:
    """回测引擎基类。"""

    def __init__(self, initial_cash: float = 1_000_000, commission: float = 0.00025,
                 stamp_tax: float = 0.001, slippage: float = 0.001):
        self.initial_cash = initial_cash
        self.commission = commission
        self.stamp_tax = stamp_tax
        self.slippage = slippage

    def run(self, signals: List[Dict], daily_data: Dict[str, Dict]) -> BacktestResult:
        """执行回测。子类实现。"""
        raise NotImplementedError


class CrossSectionalBacktest(BacktestEngine):
    """截面排序策略回测：每日按信号评分排序选股，等权持仓。"""

    def run(self, signals: List[Dict], daily_data: Dict[str, Dict],
            max_positions: int = 5, stop_loss: float = 0.08) -> BacktestResult:
        result = BacktestResult()
        result.start_equity = self.initial_cash
        result.final_equity = self.initial_cash

        equity = self.initial_cash
        cash = self.initial_cash
        positions: Dict[str, Dict] = {}  # stock_code -> {shares, cost_basis}
        equity_curve = [equity]
        trade_log = []

        # 按日期排序信号
        dates = sorted(set(s["signal_date"] for s in signals))

        for dt in dates:
            if dt not in daily_data:
                continue
            day_data = daily_data[dt]

            # 1. 检查止盈止损
            to_sell = []
            for code, pos in list(positions.items()):
                if code in day_data:
                    price = day_data[code]["close"]
                    pnl = (price - pos["cost_basis"]) / pos["cost_basis"]
                    if pnl <= -stop_loss:
                        to_sell.append((code, price, "止损"))
                    elif pnl >= stop_loss * 2:
                        to_sell.append((code, price, "止盈"))

            for code, price, reason in to_sell:
                pos = positions.pop(code)
                sell_amount = price * pos["shares"]
                cost = sell_amount * (self.commission + self.stamp_tax)
                cash += sell_amount - cost
                trade_log.append({
                    "date": dt, "stock_code": code, "direction": "SELL",
                    "price": price, "shares": pos["shares"], "cost": cost,
                    "profit_loss": (price - pos["cost_basis"]) * pos["shares"] - cost,
                    "reason": reason,
                })

            # 2. 买入新信号
            day_signals = [s for s in signals if s["signal_date"] == dt and s["direction"] == "buy"]
            day_signals.sort(key=lambda s: s.get("strength", 0), reverse=True)

            slots = max_positions - len(positions)
            for sig in day_signals[:slots]:
                code = sig["stock_code"]
                if code in positions or code not in day_data:
                    continue
                price = day_data[code]["close"] * (1 + self.slippage)
                per_stock_cash = equity / max_positions
                shares = int(per_stock_cash / price / 100) * 100
                if shares < 100:
                    continue
                cost = shares * price
                if cost > cash:
                    shares = int(cash / price / 100) * 100
                    if shares < 100:
                        continue
                    cost = shares * price
                fee = cost * self.commission
                cash -= cost + fee
                positions[code] = {"shares": shares, "cost_basis": price}
                trade_log.append({
                    "date": dt, "stock_code": code, "direction": "BUY",
                    "price": price, "shares": shares, "cost": fee,
                })

            # 3. 计算当日权益
            pos_value = sum(
                day_data.get(c, {}).get("close", pos["cost_basis"]) * pos["shares"]
                for c, pos in positions.items()
            )
            equity = cash + pos_value
            equity_curve.append(equity)

        result.final_equity = equity
        result.total_return = (equity - self.initial_cash) / self.initial_cash
        result.total_trades = len(trade_log)
        result.winning_trades = sum(1 for t in trade_log if t.get("profit_loss", 0) > 0)
        result.win_rate = result.winning_trades / max(result.total_trades, 1)
        result.trades = trade_log

        # 夏普比例（简化）
        if len(equity_curve) > 1:
            import numpy as np
            returns = np.diff(equity_curve) / equity_curve[:-1]
            result.sharpe_ratio = float(np.mean(returns) / max(np.std(returns), 1e-10) * np.sqrt(252))
            result.annual_return = float((equity / self.initial_cash) ** (252 / max(len(equity_curve), 1)) - 1)

        # 最大回撤
        peak = equity_curve[0]
        dd = 0
        for e in equity_curve:
            if e > peak:
                peak = e
            dd = max(dd, (peak - e) / peak if peak > 0 else 0)
        result.max_drawdown = dd

        return result


class TimeSeriesBacktest(BacktestEngine):
    """时序逐股策略回测：逐个股票独立回测，按信号入场/离场。"""

    def run(self, signals: List[Dict], daily_data: Dict[str, Dict],
            stop_loss: float = 0.08, take_profit: float = 0.20) -> BacktestResult:
        result = BacktestResult()
        result.start_equity = self.initial_cash
        equity = self.initial_cash
        trade_log = []

        by_code: Dict[str, List[Dict]] = {}
        for s in signals:
            by_code.setdefault(s["stock_code"], []).append(s)

        for code, code_signals in by_code.items():
            code_signals.sort(key=lambda s: s["signal_date"])
            in_position = False
            entry_price = 0

            for sig in code_signals:
                dt = sig["signal_date"]
                if dt not in daily_data or code not in daily_data[dt]:
                    continue
                price = daily_data[dt][code]["close"]

                if not in_position and sig["direction"] == "buy":
                    # 买入 10% 仓位
                    amount = equity * 0.1
                    shares = int(amount / price / 100) * 100
                    if shares >= 100:
                        cost = shares * price * (1 + self.commission)
                        equity -= cost
                        in_position = True
                        entry_price = price
                        trade_log.append({"date": dt, "stock_code": code, "direction": "BUY", "price": price, "shares": shares})

                elif in_position:
                    pnl = (price - entry_price) / entry_price
                    if sig["direction"] == "sell" or pnl <= -stop_loss or pnl >= take_profit:
                        shares_held = trade_log[-1]["shares"]
                        amount = shares_held * price * (1 - self.commission - self.stamp_tax)
                        equity += amount
                        trade_log.append({"date": dt, "stock_code": code, "direction": "SELL", "price": price, "shares": shares_held,
                                         "profit_loss": (price - entry_price) * shares_held})
                        in_position = False

        result.final_equity = equity
        result.total_return = (equity - self.initial_cash) / self.initial_cash
        result.total_trades = len(trade_log)
        result.trades = trade_log
        return result
