"""回测引擎 v2 单测：涨跌停/T+1/延迟结算/最低佣金/冲击成本/止损止盈/整手/现金约束。

验收标准（design/05 M1）：上述行为单测全绿。
"""
import pandas as pd
import pytest

from strategy.backtest.account import Account
from strategy.backtest.engine import run_backtest
from strategy.backtest.exchange import Exchange, limit_flags
from strategy.backtest.models import Order, TradeConfig
from strategy.strategy import SignalStrategy


# ── 测试数据构造 ──

def make_df(series, vol=1_000_000, amt=1e8):
    """series: {code: [(date, close), ...]} → 宽表 df（含 volume/amount 供裁剪/冲击成本用）"""
    rows = []
    for code, pairs in series.items():
        for d, c in pairs:
            rows.append({'trade_date': str(d), 'stock_code': code, 'close': float(c),
                         'volume': float(vol), 'amount': float(amt)})
    return pd.DataFrame(rows)


def cfg(**kw):
    base = dict(initial_cash=1_000_000, max_positions=2, comm=0.00025, st_tax=0.001,
                slip=0.0, min_cost=0.0, settle_delay=False, hold_days=10)
    base.update(kw)
    return TradeConfig(**base)


def run(df, pred=None, **cfg_kw):
    return run_backtest(df, pred, SignalStrategy(cfg(**cfg_kw)), cfg(**cfg_kw))


# ── 涨跌停 ──

def test_limit_up_block_buy():
    """涨停股分数最高也不买入，只买非涨停候选。"""
    df = make_df({
        '000001': [('2026-01-05', 10.0), ('2026-01-06', 11.0)],   # +10% 涨停
        '000002': [('2026-01-05', 20.0), ('2026-01-06', 20.1)],
    })
    pred = pd.Series([0.0, 0.9, 0.0, 0.1], index=df.index)  # 000001 分数更高
    r = run(df, pred, max_positions=1)
    buys = [t for t in r.trades if t['action'] == 'BUY']
    assert len(buys) == 1 and buys[0]['stock_code'] == '000002', f"应买 000002，实际 {buys}"


def test_limit_down_block_sell():
    """持仓跌停日即使触发止损也不卖出（次日恢复可卖）。"""
    df = make_df({
        '000001': [('2026-01-05', 10.0), ('2026-01-06', 9.0),    # -10% 跌停，止损触发价 9.2
                   ('2026-01-07', 9.15)],                         # 恢复，≤9.2 且非跌停 → 止损卖出
        '000002': [('2026-01-05', 20.0), ('2026-01-06', 20.0), ('2026-01-07', 20.0)],
    })
    pred = pd.Series([0.5, 0.1, 0.1, 0.1, 0.1, 0.1], index=df.index)
    r = run(df, pred, stop_loss=0.08, max_positions=1)
    sells = [t for t in r.trades if t['action'] == 'SELL']
    # 01-06 跌停不卖；01-07 止损触发卖出
    assert len(sells) == 1, f"只应在 01-07 卖出一次，实际 {sells}"
    assert sells[0]['date'] == '2026-01-07'
    assert sells[0]['price'] == pytest.approx(9.2, abs=1e-6)  # 止损按触发价成交


# ── T+1 ──

def test_t_plus_1():
    """买入当日不进入卖出检查：当日暴跌也不卖，次日才可卖。"""
    df = make_df({
        '000001': [('2026-01-05', 10.0), ('2026-01-06', 9.0), ('2026-01-07', 8.5)],
        '000002': [('2026-01-05', 20.0), ('2026-01-06', 20.0), ('2026-01-07', 20.0)],
    })
    pred = pd.Series([0.5, 0.1, 0.1, 0.1, 0.1, 0.1], index=df.index)
    r = run(df, pred, stop_loss=0.05, max_positions=1)
    dates_sell = {t['date'] for t in r.trades if t['action'] == 'SELL'}
    assert '2026-01-06' not in dates_sell, "买入当日不允许卖出（T+1）"
    assert '2026-01-07' in dates_sell, "次日触发止损应可卖出"


# ── 止损/止盈/移动止损/到期 ──

def test_stop_loss_trigger_price():
    """止损按触发价成交（buy×(1-sl)），非收盘价。"""
    df = make_df({
        '000001': [('2026-01-05', 10.0), ('2026-01-06', 9.1), ('2026-01-07', 9.1)],  # -9% 非跌停，≤9.2
        '000002': [('2026-01-05', 20.0), ('2026-01-06', 20.0), ('2026-01-07', 20.0)],
    })
    pred = pd.Series([0.5, 0.1, 0.1, 0.1, 0.1, 0.1], index=df.index)
    r = run(df, pred, stop_loss=0.08, max_positions=1)
    sells = [t for t in r.trades if t['action'] == 'SELL']
    assert len(sells) == 1 and sells[0]['price'] == pytest.approx(9.2, abs=1e-6)


def test_take_profit_trigger_price():
    df = make_df({
        '000001': [('2026-01-05', 10.0), ('2026-01-06', 11.6), ('2026-01-07', 11.6)],
        '000002': [('2026-01-05', 20.0), ('2026-01-06', 20.0), ('2026-01-07', 20.0)],
    })
    pred = pd.Series([0.5, 0.1, 0.1, 0.1, 0.1, 0.1], index=df.index)
    r = run(df, pred, take_profit=0.15, max_positions=1)
    sells = [t for t in r.trades if t['action'] == 'SELL']
    assert len(sells) == 1 and sells[0]['price'] == pytest.approx(11.5, abs=1e-6)


def test_trailing_stop():
    """移动止损：峰值回撤触发按当前价卖出（用创业板 300001，±19.8% 避开涨跌停干扰）。"""
    df = make_df({
        '300001': [('2026-01-05', 10.0), ('2026-01-06', 11.9), ('2026-01-07', 10.5)],
        '300002': [('2026-01-05', 20.0), ('2026-01-06', 20.0), ('2026-01-07', 20.0)],
    })
    pred = pd.Series([0.5, 0.1, 0.1, 0.1, 0.1, 0.1], index=df.index)
    r = run(df, pred, trailing=0.1, take_profit=1.0, max_positions=1)  # peak 11.9×0.9=10.71 ≥ 10.5 → 触发
    sells = [t for t in r.trades if t['action'] == 'SELL']
    assert len(sells) == 1 and sells[0]['price'] == pytest.approx(10.5, abs=1e-6)
    assert sells[0]['reason'] == 'trailing'


def test_hold_expire():
    df = make_df({
        '000001': [('2026-01-05', 10.0), ('2026-01-06', 10.0), ('2026-01-07', 10.0),
                   ('2026-01-08', 10.0)],
        '000002': [('2026-01-05', 20.0), ('2026-01-06', 20.0), ('2026-01-07', 20.0),
                   ('2026-01-08', 20.0)],
    })
    pred = pd.Series([0.5, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1], index=df.index)
    r = run(df, pred, hold_days=3, max_positions=1)
    sells = [t for t in r.trades if t['action'] == 'SELL']
    assert len(sells) == 1 and sells[0]['reason'] == 'hold_expire' and sells[0]['date'] == '2026-01-08'


# ── 整手/现金约束 ──

def test_lot_rounding():
    """买入股数按 100 股整手取整。"""
    df = make_df({'000001': [('2026-01-05', 10.0), ('2026-01-06', 10.0)]})
    pred = pd.Series([0.5, 0.1], index=df.index)
    r = run(df, pred, max_positions=1, initial_cash=100_000)
    buys = [t for t in r.trades if t['action'] == 'BUY']
    assert len(buys) == 1 and buys[0]['shares'] % 100 == 0


def test_cash_constraint():
    """预算不足高价股时不买入（整手后金额超现金）。"""
    df = make_df({'000001': [('2026-01-05', 1000.0), ('2026-01-06', 1000.0)]})
    pred = pd.Series([0.5, 0.1], index=df.index)
    r = run(df, pred, max_positions=2, initial_cash=100_000)  # 预算 5 万买不起 1 手 10 万
    assert not any(t['action'] == 'BUY' for t in r.trades)


# ── 延迟结算（T+1 资金）──

def test_settle_delay():
    """卖出所得进入 cash_delay，当日不可用；次日 settle_commit 并入现金。"""
    from strategy.backtest.models import Fill
    a = Account(TradeConfig(initial_cash=100_000, settle_delay=True))
    # 先建仓再卖出（引擎流程保证先买后卖）
    buy = Order(stock_code='000001', direction='BUY', shares=100)
    a.apply_fills([Fill(order=buy, price=10.0, shares=100, fee=0.25, gross=1000.0)], '2026-01-05')
    assert a.cash == pytest.approx(98_999.75, abs=1e-6)
    sell = Order(stock_code='000001', direction='SELL', shares=100)
    a.apply_fills([Fill(order=sell, price=10.0, shares=100, fee=5.0, gross=1000.0)], '2026-01-06')
    assert a.cash == pytest.approx(98_999.75, abs=1e-6), "卖出资金当日不应进入可用现金"
    assert a.cash_delay == pytest.approx(995.0, abs=1e-6)
    a.settle_commit()
    assert a.cash == pytest.approx(99_994.75, abs=1e-6) and a.cash_delay == 0.0


def test_settle_delay_blocks_same_day_buy():
    """延迟结算开启：卖出当日可用现金不足 → 买入被拒；关闭时同场景可买（降档成交）。"""
    # 01-05 全仓买 000001（预算 5 万→4900 股，cash≈987）→ 01-06 止损卖出（净额≈4.5 万）
    # 01-06 000002 高分想买（预算=前日净值≈5 万）：
    #   settle_delay=True:  可用 cash≈987 → 拒买
    #   settle_delay=False: cash≈4.6 万 → 降档买 2400 股
    df = make_df({
        '000001': [('2026-01-05', 10.0), ('2026-01-06', 9.1), ('2026-01-07', 9.1)],
        '000002': [('2026-01-05', 19.0), ('2026-01-06', 19.0), ('2026-01-07', 19.0)],
    })
    pred = pd.Series([0.5, 0.1, 0.9, 0.1, 0.1, 0.1], index=df.index)
    r_on = run(df, pred, stop_loss=0.08, max_positions=1, settle_delay=True,
               initial_cash=50_000)
    amt_on = sum(t['amount'] for t in r_on.trades if t['action'] == 'BUY' and t['date'] == '2026-01-06')
    assert amt_on < 1000, f"延迟结算下卖出当日只能用原有现金（<1000），实际买入 {amt_on}"
    r_off = run(df, pred, stop_loss=0.08, max_positions=1, settle_delay=False,
                initial_cash=50_000)
    amt_off = sum(t['amount'] for t in r_off.trades if t['action'] == 'BUY' and t['date'] == '2026-01-06')
    assert amt_off > 40_000, f"关闭延迟结算时卖出资金当日可用（>4 万），实际买入 {amt_off}"


# ── 费用：最低佣金 / 冲击成本 ──

def test_min_cost():
    ex = Exchange(TradeConfig(comm=0.00025, min_cost=5.0))
    assert ex.buy_fee(1000) == pytest.approx(5.0, abs=1e-9)      # 0.25 < 5 → 5
    assert ex.buy_fee(100_000) == pytest.approx(25.0, abs=1e-9)  # 25 > 5 → 25
    assert ex.sell_fee(1000) == pytest.approx(6.0, abs=1e-9)     # 5(佣金) + 1(滑点)


def test_impact_cost_square():
    """冲击成本平方模型：大单成本占比更高。"""
    ex1 = Exchange(TradeConfig(impact_cost=0.01))
    ex2 = Exchange(TradeConfig(impact_cost=0.01))
    small = ex1.sell_fee(10_000, day_amount=1_000_000)
    large = ex2.sell_fee(100_000, day_amount=1_000_000)
    # 大单：0.01×(0.1)²×100000=100；小单：0.01×(0.01)²×10000=0.01 → 大单占比更高
    assert large / 100_000 > small / 10_000


# ── 净值曲线连续 ──

def test_equity_curve_continuous():
    """无候选/无持仓日也记录净值（曲线连续）。"""
    df = make_df({'000001': [('2026-01-05', 10.0), ('2026-01-06', 10.0), ('2026-01-07', 10.0)]})
    pred = pd.Series([-0.5, -0.5, -0.5], index=df.index)  # 无正分 → 全程不交易
    r = run(df, pred)
    assert len(r.equity_curve) == 3 and len(r.trades) == 0


def test_win_rate():
    df = make_df({
        '000001': [('2026-01-05', 10.0), ('2026-01-06', 12.0), ('2026-01-07', 12.0)],  # 止盈 +20%
        '000002': [('2026-01-05', 20.0), ('2026-01-06', 20.0), ('2026-01-07', 20.0)],
    })
    pred = pd.Series([0.5, 0.1, 0.1, 0.1, 0.1, 0.1], index=df.index)
    r = run(df, pred, take_profit=0.15, max_positions=1)
    assert r.win_rate == 1.0
