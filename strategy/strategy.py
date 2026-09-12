"""策略层：信号 → 订单（对齐 design/05 §3.2）。

SignalStrategy 复刻 v1（_simple_backtest）语义：事件驱动卖出（止损/止盈/trailing/到期）
+ 预测分数 top 买入。
TopkDropoutStrategy 移植 Qlib TopkDropout：固定 topk 持仓 + 每日换血 n_drop +
合并排序防高卖低买 + hold_thresh 最短持有（事件驱动退出同样保留）。

约定：engine 预处理时已对全区间 df 计算涨跌停标记并作为 '_limit_up'/'_limit_down'
列传入（跨日期 groupby shift，单日切片重算会错位）；预测分在 '_score' 列。
"""
from abc import ABC, abstractmethod
from typing import List

import pandas as pd

from strategy.backtest.account import Account
from strategy.backtest.exchange import Exchange
from strategy.backtest.models import Order, TradeConfig


class BaseStrategy(ABC):
    def __init__(self, cfg: TradeConfig):
        self.cfg = cfg

    @abstractmethod
    def generate_orders(self, day: pd.DataFrame, account: Account,
                        exchange: Exchange) -> List[Order]:
        """生成当日订单（卖出列表在前，买入在后，engine 按序撮合）。"""


class SignalStrategy(BaseStrategy):
    """v1 兼容策略：事件驱动退出 + 分数 top 买入。

    与 _simple_backtest 的差异仅在于执行细节由 Exchange/Account 统一处理
    （最低佣金/延迟结算等增强项通过 TradeConfig 开关对齐）。
    same_day_budget=True 时（纸面组合口径）买入预算基准 = 当日盘后净值
    （现金 + 卖出净额估计 + 持仓按当日收盘估值）；默认用前一日 EOD 净值（v1 回测口径）。
    """

    def __init__(self, cfg: TradeConfig, same_day_budget: bool = False):
        super().__init__(cfg)
        self.same_day_budget = same_day_budget

    def generate_orders(self, day: pd.DataFrame, account: Account,
                        exchange: Exchange) -> List[Order]:
        orders: List[Order] = []
        td = day['trade_date'].iloc[0]

        # ── 卖出检查（持仓逐票；当日买入当日不检查 → T+1 天然成立）──
        for code, p in list(account.positions.items()):
            sub = day[day['stock_code'] == code]
            if sub.empty:
                continue
            r = sub.iloc[0]
            cur = float(r['close'])
            if not (cur > 0):   # 含 NaN：停牌/零价行当日无有效报价，不触发退出
                continue        # （估值由 account.mark_price 承接，勿用 buy_price 直接比）
            p.peak = max(p.peak, cur)
            sell_p = None
            reason = None
            if cur <= p.buy_price * (1 - self.cfg.stop_loss):
                sell_p = p.buy_price * (1 - self.cfg.stop_loss); reason = 'stop_loss'
            elif cur >= p.buy_price * (1 + self.cfg.take_profit):
                sell_p = p.buy_price * (1 + self.cfg.take_profit); reason = 'take_profit'
            elif self.cfg.trailing > 0 and cur <= p.peak * (1 - self.cfg.trailing):
                sell_p = cur; reason = 'trailing'
            elif (pd.Timestamp(td) - pd.Timestamp(p.buy_date)).days >= self.cfg.hold_days:
                sell_p = cur; reason = 'hold_expire'
            if sell_p is None:
                continue
            if bool(sub['_limit_down'].iloc[0]) or \
                    (self.cfg.forbid_all_trade_at_limit and bool(sub['_limit_up'].iloc[0])):
                continue
            orders.append(Order(stock_code=code, direction='SELL', shares=p.shares,
                                reason=reason, limit_price=sell_p))

        # ── 买入：候选剔除已持/涨停，按分数取 top slots（slots 按"卖出后"持仓数，与 v1 一致）──
        sell_codes = {o.stock_code for o in orders if o.direction == 'SELL'}
        held = set(account.positions.keys()) - sell_codes
        cand = day[~day['stock_code'].isin(held) & (day['close'] > 0) & (~day['_limit_up'])]
        slots = self.cfg.max_positions - len(held)
        if slots > 0 and not cand.empty:
            scored = cand[cand['_score'].notna() & (cand['_score'] > 0)]
            if not scored.empty:
                # kind='stable'：XGBoost 分数离散化后大量并列，不稳定排序（quicksort）的
                # 并列顺序依赖数组长度与内部 pivot，导致与 v1 选股分叉 → 统一按行序（代码升序）
                picks = scored['_score'].sort_values(ascending=False, kind='stable').index[:slots]
                # 预算基准：v1 回测口径 = 前一日收盘净值；纸面口径 = 当日盘后净值
                if self.same_day_budget:
                    # 现金 + 卖出净额估计（与 Exchange 实际费率公式一致）+ 持仓按当日收盘估值
                    # （已生成卖单的持仓只计净额、不再计市值，避免重复）
                    sold_codes = {o.stock_code for o in orders if o.direction == 'SELL'}
                    size_base = account.get_cash()
                    for o in orders:
                        if o.direction != 'SELL':
                            continue
                        px = o.limit_price if o.limit_price else float(
                            day[day['stock_code'] == o.stock_code]['close'].iloc[0])
                        size_base += o.shares * px * (1 - self.cfg.comm - self.cfg.st_tax - self.cfg.slip)
                    close_map = dict(zip(day['stock_code'], day['close']))
                    for code, p in account.positions.items():
                        if code in sold_codes:
                            continue
                        size_base += p.shares * account.mark_price(p, close_map)
                else:
                    size_base = account.records[-1].account if account.records else self.cfg.initial_cash
                for idx in picks:
                    r = scored.loc[idx]
                    price = exchange.deal_price(r, 'BUY')
                    shares = int((size_base / self.cfg.max_positions) // price // 100) * 100
                    if shares > 0:
                        orders.append(Order(stock_code=r['stock_code'], direction='BUY',
                                            shares=shares, reason='signal'))
        return orders


class TopkDropoutStrategy(BaseStrategy):
    """Qlib TopkDropout 移植（signal_strategy.py:138-295）：固定 topk 持仓 + 每日换血。

    每日决策：
      1. 事件驱动退出（止损/止盈/trailing/到期，与 SignalStrategy 相同）——无条件卖出
      2. 换血：last = 剩余持仓按预测分排序；today = 候选 top(n_drop + topk − len(last))；
         合并排序 comb = last ∪ today（降序）→ 卖出 = last ∩ comb 末 n_drop
         —— 杜绝"卖高分买低分"（本系统 v1 没有的能力）
      3. 卖出过滤：持仓天数 < hold_thresh 不卖；跌停不卖（forbid 模式涨停也不卖）
      4. 买入 = today 前 len(sell) + topk − len(last) 名；预算 = 现金 × risk_degree / 买入数
         （现金含预估卖出净额，Qlib 先卖后买语义；engine 撮合兜底现金约束）
    """

    def __init__(self, cfg: TradeConfig, topk: int = 5, n_drop: int = 1,
                 hold_thresh: int = 1, risk_degree: float = 0.95):
        super().__init__(cfg)
        self.topk = topk
        self.n_drop = n_drop
        self.hold_thresh = hold_thresh
        self.risk_degree = risk_degree

    def generate_orders(self, day: pd.DataFrame, account: Account,
                        exchange: Exchange) -> List[Order]:
        orders: List[Order] = []
        td = day['trade_date'].iloc[0]

        # ── 1. 事件驱动退出（无条件卖出，先于换血）──
        sold_event = set()
        for code, p in list(account.positions.items()):
            sub = day[day['stock_code'] == code]
            if sub.empty:
                continue
            r = sub.iloc[0]
            cur = float(r['close'])
            if not (cur > 0):   # 含 NaN：停牌/零价行当日无有效报价，不触发退出
                continue
            p.peak = max(p.peak, cur)
            sell_p, reason = None, None
            if cur <= p.buy_price * (1 - self.cfg.stop_loss):
                sell_p = p.buy_price * (1 - self.cfg.stop_loss); reason = 'stop_loss'
            elif cur >= p.buy_price * (1 + self.cfg.take_profit):
                sell_p = p.buy_price * (1 + self.cfg.take_profit); reason = 'take_profit'
            elif self.cfg.trailing > 0 and cur <= p.peak * (1 - self.cfg.trailing):
                sell_p = cur; reason = 'trailing'
            elif (pd.Timestamp(td) - pd.Timestamp(p.buy_date)).days >= self.cfg.hold_days:
                sell_p = cur; reason = 'hold_expire'
            if sell_p is None:
                continue
            if bool(sub['_limit_down'].iloc[0]) or \
                    (self.cfg.forbid_all_trade_at_limit and bool(sub['_limit_up'].iloc[0])):
                continue
            orders.append(Order(stock_code=code, direction='SELL', shares=p.shares,
                                reason=reason, limit_price=sell_p))
            sold_event.add(code)

        # ── 2. 换血决策（Qlib 合并排序防高卖低买）──
        def _sub(code):
            s = day[day['stock_code'] == code]
            return s.iloc[0] if not s.empty else None

        def _sellable(code) -> bool:
            sub = day[day['stock_code'] == code]
            if sub.empty or not (float(sub['close'].iloc[0]) > 0):
                return False  # 无有效报价（停牌/零价/NaN 行）不可卖
            if bool(sub['_limit_down'].iloc[0]) or \
                    (self.cfg.forbid_all_trade_at_limit and bool(sub['_limit_up'].iloc[0])):
                return False
            return True

        last_codes = [c for c in account.positions if c not in sold_event]
        if last_codes:
            scores = day.set_index('stock_code')['_score'].reindex(last_codes)
            last = scores.sort_values(ascending=False, kind='stable').index.tolist()
        else:
            last = []
        # 事件退出（止损/止盈等）当日不换回：刚卖出的标的与持仓一样排除出候选，
        # 否则同日先卖后买会把退出标的立刻买回（退出失效 + 双倍费用）
        held_set = set(last_codes) | set(sold_event)

        # 候选买入：非持仓、可买（close>0 且非涨停）、有正分，取 top(n_drop + topk − len(last))
        n_candi = self.n_drop + self.topk - len(last)
        today: List[str] = []
        if n_candi > 0:
            cand = day[~day['stock_code'].isin(held_set) & (day['close'] > 0) & (~day['_limit_up'])
                       & day['_score'].notna() & (day['_score'] > 0)]
            if not cand.empty:
                today = cand['_score'].sort_values(ascending=False, kind='stable').index[:n_candi] \
                    .map(lambda i: cand.loc[i, 'stock_code']).tolist()

        # 合并排序：last ∪ today 降序，卖出 = 原持仓 ∩ 合并集末 n_drop
        sell_by_drop: List[str] = []
        if last:
            comb = pd.Index(last + [t for t in today if t not in last])
            comb_sorted = day.set_index('stock_code')['_score'].reindex(comb) \
                .sort_values(ascending=False, kind='stable').index
            drop_codes = set(comb_sorted[-self.n_drop:]) if self.n_drop > 0 else set()
            for c in last:
                if c in drop_codes and _sellable(c) \
                        and account.positions[c].count_days >= self.hold_thresh:
                    sell_by_drop.append(c)

        for code in sell_by_drop:
            p = account.positions[code]
            orders.append(Order(stock_code=code, direction='SELL', shares=p.shares,
                                reason='drop'))
        n_sell = len(sell_by_drop)

        # ── 3. 买入补足 topk：today 前 n_sell + topk − len(last) 名 ──
        n_buy = n_sell + self.topk - len(last)
        buy_codes = today[:max(n_buy, 0)] if n_buy > 0 else []
        if buy_codes:
            # 预算基础 = 现金 + 预估卖出净额（Qlib 先卖后买：卖出所得当日可用）
            est_sell_net = 0.0
            for code in list(sold_event) + sell_by_drop:
                r = _sub(code)
                if r is not None:
                    est_sell_net += float(r['close']) * account.positions[code].shares * \
                        (1 - self.cfg.comm - self.cfg.st_tax - self.cfg.slip)
            cash_base = account.get_cash() + est_sell_net
            value = cash_base * self.risk_degree / len(buy_codes)
            for code in buy_codes:
                r = _sub(code)
                if r is None:
                    continue
                price = exchange.deal_price(r, 'BUY')
                shares = int(value // price // 100) * 100
                if shares > 0:
                    orders.append(Order(stock_code=code, direction='BUY',
                                        shares=shares, reason='signal'))
        return orders
