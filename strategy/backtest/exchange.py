"""撮合层：可交易性 / 成交价 / 费用 / 成交量裁剪 / 冲击成本（对齐 design/05 §3.1）。

语义来源：Qlib exchange.py 精读 + 本系统 _simple_backtest 既有约束，
两处规则在此统一（纸面组合迁移后消除双实现漂移）。
"""
from typing import Optional, Tuple

import pandas as pd

from .models import Order, TradeConfig


# ── 涨跌停（从 scripts/pipeline.py 迁移，纯函数）──

def limit_pct(stock_code) -> float:
    """涨跌停幅度：创业板(30)/科创板(68) 20%，其余主板 10%（北交所不在股票池）。"""
    return 0.198 if str(stock_code).startswith(('30', '68')) else 0.098


def limit_flags(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """面板涨跌停标记（纯函数）：需 stock_code+close 列，组内按日期升序。

    Returns: (limit_up, limit_down)；首日无前收盘为 NaN → False（允许交易）。
    close 为后复权价，除权日幅度略有近似。
    """
    prev = df.groupby('stock_code')['close'].shift(1)
    pct = df['close'] / prev - 1
    lim = df['stock_code'].map(limit_pct)
    return pct >= lim, pct <= -lim


class Exchange:
    """撮合器：判断可交易性、计算成交价与费用、按约束裁剪订单。"""

    def __init__(self, cfg: TradeConfig):
        self.cfg = cfg

    # ── 可交易性 ──
    def is_tradable(self, row: pd.Series, direction: str,
                    limit_up: bool, limit_down: bool) -> Tuple[bool, str]:
        """row: 当日该股行情行（close 等列）。direction: BUY/SELL。"""
        close = row.get('close')
        if close is None or pd.isna(close) or float(close) <= 0:
            return False, '停牌/无行情'
        if direction == 'BUY':
            if limit_up:
                return False, '涨停不可买'
        else:
            if limit_down:
                return False, '跌停不可卖'
            if self.cfg.forbid_all_trade_at_limit and limit_up:
                return False, '涨停不可卖（全禁模式）'
        return True, ''

    # ── 成交价 ──
    def deal_price(self, row: pd.Series, direction: str) -> float:
        """成交价：v1 语义 = 买入 close×(1+slip/2)，卖出按触发价或 close（滑点进成本）。

        deal_price=vwap 时用 amount/volume 加权价（需行含 amount/volume 列）。
        """
        close = float(row['close'])
        if self.cfg.deal_price == 'vwap' and 'amount' in row.index and 'volume' in row.index:
            vol = float(row['volume']) if pd.notna(row['volume']) else 0
            amt = float(row['amount']) if pd.notna(row['amount']) else 0
            if vol > 0 and amt > 0:
                return amt / vol
        if direction == 'BUY':
            return close * (1 + self.cfg.slip / 2)
        return close

    # ── 费用 ──
    def buy_fee(self, gross: float) -> float:
        return max(gross * self.cfg.comm, self.cfg.min_cost)

    def sell_fee(self, gross: float, day_amount: Optional[float] = None) -> float:
        fee = max(gross * (self.cfg.comm + self.cfg.st_tax), self.cfg.min_cost)
        fee += gross * self.cfg.slip
        if self.cfg.impact_cost > 0 and day_amount:
            # Qlib 冲击成本平方模型：impact_cost × (成交额/当日总成交额)²
            fee += self.cfg.impact_cost * (gross / day_amount) ** 2
        return fee

    # ── 成交量裁剪（Qlib _clip_amount_by_volume）──
    def clip_buy_shares(self, shares: int, day_volume: Optional[float]) -> int:
        if self.cfg.vol_limit is None or not day_volume or day_volume <= 0:
            return shares
        return min(shares, int(day_volume * self.cfg.vol_limit))

    def round_lot(self, shares: int) -> int:
        unit = self.cfg.trade_unit
        return int(shares // unit) * unit

    # ── 撮合单个订单 ──
    def execute(self, order: Order, row: pd.Series, limit_up: bool, limit_down: bool,
                cash: float) -> 'Fill':
        """撮合：约束 → 成交价 → 裁剪 → 费用。cash 仅用于买入现金约束。"""
        from .models import Fill

        ok, reason = self.is_tradable(row, order.direction, limit_up, limit_down)
        if not ok:
            return Fill(order=order, rejected=True, reject_reason=reason)

        price = order.limit_price if (order.direction == 'SELL' and order.limit_price) \
            else self.deal_price(row, order.direction)

        if order.direction == 'BUY':
            shares = self.clip_buy_shares(order.shares, row.get('volume'))
            shares = self.round_lot(shares)
            if shares <= 0:
                return Fill(order=order, rejected=True, reject_reason='裁剪后不足整手')
            gross = shares * price
            fee = self.buy_fee(gross)
            if gross + fee > cash:
                if not self.cfg.downsize_buy:
                    return Fill(order=order, rejected=True, reject_reason='现金不足')
                # 现金不足：降档到整手内最大可买
                affordable = int((cash - self.cfg.min_cost) // (price * (1 + self.cfg.comm)))
                shares = self.round_lot(min(shares, affordable))
                if shares <= 0:
                    return Fill(order=order, rejected=True, reject_reason='现金不足')
                gross = shares * price
                fee = self.buy_fee(gross)
            return Fill(order=order, price=price, shares=shares, fee=fee, gross=gross)
        else:
            shares = self.round_lot(min(order.shares, int(row.get('shares', order.shares))))
            if shares <= 0:
                return Fill(order=order, rejected=True, reject_reason='股数不足整手')
            gross = shares * price
            fee = self.sell_fee(gross, row.get('amount'))
            return Fill(order=order, price=price, shares=shares, fee=fee, gross=gross)
