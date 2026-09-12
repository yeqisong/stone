"""零价/停牌行防护单测（2026-09-11）。

背景：2026-07 有 1055 行停牌股被写成 close=0，经 feature_values → 宽表进入回测，
持仓在停牌日被按 0 计价，净值出现 -34% / 次日 +57% 的成对伪跳变，把 ACTIVE 模型
v15.0 的 max_dd 撑到 47%（真实水平远低于此）。

本组测试锁住四道防线：
  1. 账户估值：无有效报价时沿用最近有效价，绝不按 0、也不按成本价；
  2. 缺行情（行缺失）与零价行（行存在但价格为 0）行为一致；
  3. 策略退出：无有效报价当日不触发止损/移动止盈（否则会假卖一笔）；
  4. 撮合：成交价必须为正，否则拒单。
"""
import pandas as pd
import pytest

from strategy.backtest.account import Account
from strategy.backtest.engine import run_backtest
from strategy.backtest.exchange import Exchange
from strategy.backtest.models import Order, TradeConfig
from strategy.strategy import SignalStrategy


def cfg(**kw):
    base = dict(initial_cash=1_000_000, max_positions=2, comm=0.00025, st_tax=0.001,
                slip=0.0, min_cost=0.0, settle_delay=False, hold_days=10, trailing=0.05,
                stop_loss=0.08, take_profit=0.15)
    base.update(kw)
    return TradeConfig(**base)


class TestMarkToMarketCarryForward:
    def test_零价行不把持仓估成零(self):
        c = cfg()
        acct = Account(c)
        acct.positions['000001'] = _pos('000001', shares=1000, buy=10.0, last=12.0)
        # 当日该股零价（停牌），另一只有正常报价
        eq = acct.mark_to_market({'000001': 0.0, '000002': 20.0}, '2026-07-14')
        assert eq == pytest.approx(acct.cash + 1000 * 12.0)   # 沿用 last_price=12，不是 0
        assert acct.records[-1].position_value == pytest.approx(1000 * 12.0)

    def test_行缺失同样沿用上一价(self):
        acct = Account(cfg())
        acct.positions['000001'] = _pos('000001', shares=1000, buy=10.0, last=12.0)
        eq = acct.mark_to_market({}, '2026-07-14')
        assert eq == pytest.approx(acct.cash + 12000.0)

    def test_无历史报价时回退成本价而非零(self):
        acct = Account(cfg())
        acct.positions['000001'] = _pos('000001', shares=1000, buy=10.0, last=0.0)
        eq = acct.mark_to_market({'000001': 0.0}, '2026-07-14')
        assert eq == pytest.approx(acct.cash + 10000.0)

    def test_NaN报价沿用上一价(self):
        acct = Account(cfg())
        acct.positions['000001'] = _pos('000001', shares=1000, buy=10.0, last=12.0)
        eq = acct.mark_to_market({'000001': float('nan')}, '2026-07-14')
        assert eq == pytest.approx(acct.cash + 12000.0)

    def test_正常报价会刷新last_price(self):
        acct = Account(cfg())
        p = _pos('000001', shares=1000, buy=10.0, last=12.0)
        acct.positions['000001'] = p
        acct.mark_to_market({'000001': 13.5}, '2026-07-15')
        assert p.last_price == pytest.approx(13.5)
        assert p.peak == pytest.approx(13.5)

    def test_逐日记录带持仓快照(self):
        acct = Account(cfg())
        acct.positions['000001'] = _pos('000001', shares=1000, buy=10.0, last=12.0)
        acct.mark_to_market({'000001': 12.5}, '2026-07-15')
        snap = acct.records[-1].holdings['000001']
        assert snap['shares'] == 1000 and snap['price'] == pytest.approx(12.5)
        assert snap['buy_price'] == pytest.approx(10.0)


class TestStrategyExitOnNoQuote:
    def _held(self, close):
        """持有 000001（成本 10，峰值 12），当日该股报价为 close。"""
        acct = Account(cfg())
        acct.positions['000001'] = _pos('000001', shares=1000, buy=10.0, last=12.0)
        day = pd.DataFrame([{'trade_date': '2026-07-15', 'stock_code': '000001',
                             'close': float(close), 'volume': 1e6, 'amount': 1e8,
                             '_limit_up': False, '_limit_down': False, '_score': 0.0}])
        return acct, day

    def test_零价不触发止损(self):
        acct, day = self._held(0.0)
        orders = SignalStrategy(cfg()).generate_orders(day, acct, Exchange(cfg()))
        assert [o for o in orders if o.direction == 'SELL'] == []

    def test_零价不污染峰值(self):
        acct, day = self._held(0.0)
        SignalStrategy(cfg()).generate_orders(day, acct, Exchange(cfg()))
        assert acct.positions['000001'].peak == pytest.approx(12.0)

    def test_正常下跌仍触发止损(self):
        acct, day = self._held(9.0)   # 成本 10，-10% → 触发 8% 止损
        orders = SignalStrategy(cfg()).generate_orders(day, acct, Exchange(cfg()))
        assert [o.reason for o in orders if o.direction == 'SELL'] == ['stop_loss']


class TestExchangeRejectInvalidPrice:
    def test_零价行拒单(self):
        e = Exchange(cfg())
        row = pd.Series({'close': 0.0, 'volume': 1e6, 'amount': 1e8})
        f = e.execute(Order(stock_code='000001', direction='BUY', shares=100), row, False, False, 1e6)
        assert f.rejected and not f.shares

    def test_零值触发价按市价成交而非零价(self):
        e = Exchange(cfg())
        row = pd.Series({'close': 10.0, 'volume': 1e6, 'amount': 1e8})
        # limit_price=0 语义是"无触发价"→ 应按市价成交；绝不允许成交价为 0
        f = e.execute(Order(stock_code='000001', direction='SELL', shares=100, limit_price=0.0),
                      row, False, False, 0.0)
        assert not f.rejected and f.price == pytest.approx(10.0)
        assert f.gross == pytest.approx(1000.0)


class TestBacktestEquityNoZeroHole:
    def test_停牌日净值不出现单日塌陷(self):
        """持仓股中途停牌两天（宽表中缺行）→ 净值应平移，不应出现 -30% 级跳变。"""
        rows = []
        dates = ['2026-01-05', '2026-01-06', '2026-01-07', '2026-01-08', '2026-01-09', '2026-01-12']
        for i, d in enumerate(dates):
            # 000001 在 01-07/01-08 停牌（无行）；000002 始终有行（保持候选）
            if d not in ('2026-01-07', '2026-01-08'):
                rows.append({'trade_date': d, 'stock_code': '000001', 'close': 20.0,
                             'volume': 1e6, 'amount': 1e8})
            rows.append({'trade_date': d, 'stock_code': '000002', 'close': 10.0,
                         'volume': 1e6, 'amount': 1e8})
        df = pd.DataFrame(rows)
        pred = pd.Series(0.0, index=df.index)
        pred[df['stock_code'] == '000001'] = 1.0     # 每天优先买 000001
        res = run_backtest(df, pred, SignalStrategy(cfg(max_positions=1, hold_days=99,
                                                        stop_loss=9.0, take_profit=9.0,
                                                        trailing=0.0)),
                           cfg(max_positions=1, hold_days=99, stop_loss=9.0,
                               take_profit=9.0, trailing=0.0))
        eq = res.equity_curve
        rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
        assert all(abs(r) < 0.25 for r in rets), f'出现异常跳变: {[round(r, 3) for r in rets]}'


def _pos(code, shares, buy, last):
    from strategy.backtest.models import Position
    return Position(code=code, shares=shares, buy_price=buy, buy_date='2026-07-01',
                    cost_basis=shares * buy, peak=max(buy, last), last_price=last)
