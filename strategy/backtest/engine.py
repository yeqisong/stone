"""回测引擎 v2 主循环（对齐 design/05 §3.6）。

- 纯函数：输入宽表 df（trade_date/stock_code/close 等列）+ 预测 Series + 策略 + 配置
- 每日顺序：延迟结算入账 → 策略生成订单（先卖后买）→ 逐单撮合 → 记账 → 持仓计龄 → 收盘估值
- 指标口径与 v1（_simple_backtest）一致：sharpe=日收益均值/标准差×√252（复利净值）
"""
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from strategy.backtest.account import Account
from strategy.backtest.exchange import Exchange, limit_flags
from strategy.backtest.models import BacktestResult, TradeConfig
from strategy.strategy import BaseStrategy


def run_backtest(
    df: pd.DataFrame,
    pred: Optional[pd.Series],
    strategy: BaseStrategy,
    cfg: TradeConfig,
    val_start: str = '',
    val_end: str = '',
    compound: bool = True,
    account: Optional[Account] = None,
) -> BacktestResult:
    """运行回测。

    Args:
        df: 宽表（trade_date/stock_code/close 列；volume/amount 可选——vol_limit/vwap 需要）
        pred: 与 df 行索引对齐的预测 Series（SignalStrategy 需要；可空）
        strategy: 策略对象（生成订单）
        cfg: 撮合/组合配置
        val_start/val_end: 验证区间（YYYY-MM-DD）
        compound: False 时买入预算按初始资金定额（不复利）
        account: 种子账户（纸面组合续跑：现金/持仓从既有状态恢复；None=新建）
    """
    if df is None or df.empty:
        result = BacktestResult(config=cfg)
        result.final_account = account or Account(cfg)
        return result

    df = df.sort_values(['trade_date', 'stock_code'], kind='stable').copy()
    df['close'] = df['close'].astype(float)
    if 'volume' in df.columns:
        df['volume'] = df['volume'].fillna(0).astype(float)
    if 'amount' in df.columns:
        df['amount'] = df['amount'].fillna(0).astype(float)

    # 区间筛选（含涨跌停标记需要区间外前收盘 → 先算 flags 再筛）
    df['_limit_up'], df['_limit_down'] = limit_flags(df)
    if val_start:
        df = df[df['trade_date'] >= val_start]
    if val_end:
        df = df[df['trade_date'] <= val_end]
    if df.empty:
        result = BacktestResult(config=cfg)
        result.final_account = account or Account(cfg)
        return result

    # 预测对齐（策略通过 '_score' 列消费）
    if pred is not None:
        df['_score'] = pred.reindex(df.index).astype(float)
    else:
        df['_score'] = np.nan

    exchange = Exchange(cfg)
    account = account or Account(cfg)
    # 种子账户续跑时只返回本次运行新增的成交/记录（账户内部列表跨运行累积）
    trades_start = len(account.trades)
    records_start = len(account.records)
    dates = sorted(df['trade_date'].unique())

    for d in dates:
        day = df[df['trade_date'] == d]
        if day.empty:
            continue
        # 1. 延迟结算入账（T+1 资金当日可用起点）
        account.settle_commit()
        # 2. 策略生成订单（先卖后买）
        orders = strategy.generate_orders(day, account, exchange)
        # 3. 撮合：先卖后买两轮——卖出实时入账（settle_delay 决定入 cash 还是 cash_delay），
        #    买入用入账后的可用现金（复刻 v1 实时结算语义；延迟结算时卖出资金当日仍不可用）
        def _row(order):
            sub = day[day['stock_code'] == order.stock_code]
            return sub.iloc[0] if not sub.empty else None

        sell_fills, buy_fills = [], []
        for order in orders:
            r = _row(order)
            if r is None:
                continue
            if order.direction == 'SELL':
                sell_fills.append(exchange.execute(order, r, bool(r['_limit_up']),
                                                   bool(r['_limit_down']), account.get_cash()))
        account.apply_fills(sell_fills, str(d)[:10])
        for order in orders:
            r = _row(order)
            if r is None or order.direction != 'BUY':
                continue
            # 逐笔撮合立即入账：后续 BUY 订单必须看到已扣现金（v1 逐笔递减语义）
            f = exchange.execute(order, r, bool(r['_limit_up']),
                                 bool(r['_limit_down']), account.get_cash())
            buy_fills.append(f)
            account.apply_fills([f], str(d)[:10])
        fills = sell_fills + buy_fills
        # 4. 计龄 + 收盘估值
        account.age_positions()
        close_map = dict(zip(day['stock_code'], day['close']))
        day_amount = sum(f.gross for f in fills if not f.rejected)
        day_cost = sum(f.fee for f in fills if not f.rejected)
        account.mark_to_market(close_map, str(d)[:10], day_amount=day_amount, day_cost=day_cost)

    # ── 指标（v1 同口径；种子续跑时 eq 跨运行累积 → 曲线连续）──
    eq = np.array([r.account for r in account.records])
    result = BacktestResult(config=cfg)
    if len(eq) > 1:
        rets = eq[1:] / eq[:-1] - 1
        result.sharpe = round(float(np.mean(rets) / np.std(rets) * np.sqrt(252)), 4) if np.std(rets) > 0 else 0.0
        result.max_dd = round(float(np.min((eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq))), 4)
        result.total_return = round(float(eq[-1] / cfg.initial_cash - 1), 4)
    result.total_trades = len(account.trades)
    result.total_cost = round(sum(t.get('fee', 0) for t in account.trades), 2)
    sells = [t for t in account.trades if t['action'] == 'SELL' and t.get('pnl') is not None]
    result.win_rate = round(len([t for t in sells if t['pnl'] > 0]) / max(len(sells), 1), 4)
    result.equity_curve = [round(float(x), 2) for x in eq]
    result.trades = account.trades[trades_start:]
    result.daily_records = account.records[records_start:]
    result.final_account = account
    return result
