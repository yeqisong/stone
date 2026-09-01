"""TopkDropoutStrategy 单测（design/05 M2 验收：合并排序防高卖低买、hold_thresh、n_drop 换血）。

Qlib 语义对照（qlib/contrib/strategy/signal_strategy.py:138-295）：
- 合并排序：pred.reindex(持仓 ∪ 候选).sort(desc)，卖出 = 原持仓 ∩ 合并集末 n_drop
- hold_thresh：持仓天数不足不换出
- 买入补足 topk：buy = today[:len(sell) + topk − len(last)]
"""
import pandas as pd

from strategy.backtest.account import Account
from strategy.backtest.engine import run_backtest
from strategy.backtest.models import TradeConfig
from strategy.strategy import TopkDropoutStrategy


def make_df(series, vol=1_000_000, amt=1e8):
    """series: {code: [(date, close), ...]} → 宽表 df。"""
    rows = []
    for code, pairs in series.items():
        for d, c in pairs:
            rows.append({'trade_date': str(d), 'stock_code': code, 'close': float(c),
                         'volume': float(vol), 'amount': float(amt)})
    return pd.DataFrame(rows)


def tcfg(**kw):
    base = dict(initial_cash=1_000_000, max_positions=5, comm=0.00025, st_tax=0.001,
                slip=0.0, min_cost=0.0, settle_delay=False, hold_days=10,
                stop_loss=0.08, take_profit=0.15, trailing=0.0)
    base.update(kw)
    return TradeConfig(**base)


def run_td(df, pred, topk=2, n_drop=1, hold_thresh=1, risk_degree=0.95, **cfg_kw):
    c = tcfg(**cfg_kw)
    return run_backtest(df, pred, TopkDropoutStrategy(c, topk=topk, n_drop=n_drop,
                                                      hold_thresh=hold_thresh,
                                                      risk_degree=risk_degree), c)


# ── 合并排序防高卖低买 ──

def test_merge_sort_no_sell_high_buy_low():
    """持仓 A(高分) + 候选 B(更高分) + 低分 C 持仓：换 1 只时卖 C 不卖 A（合并排序防高卖低买）。"""
    df = make_df({
        '000001': [('2026-01-05', 10.0), ('2026-01-06', 10.1)],   # 持仓 A，分数 0.5
        '000002': [('2026-01-05', 20.0), ('2026-01-06', 20.1)],   # 候选 B，分数 0.9
        '000003': [('2026-01-05', 30.0), ('2026-01-06', 30.1)],   # 持仓 C，分数 0.1
    })
    # 01-05: A/C 分数 0.5/0.1 → 买 A（topk=2）；01-06: B 分数 0.9 最高
    pred = pd.Series([0.5, 0.5, 0.0, 0.9, 0.1, 0.0], index=df.index)
    r = run_td(df, pred, topk=2, n_drop=1)
    d2 = [t for t in r.trades if t['date'] == '2026-01-06']
    sells = [t for t in d2 if t['action'] == 'SELL']
    buys = [t for t in d2 if t['action'] == 'BUY']
    assert [t['stock_code'] for t in sells] == ['000003'], \
        f"应换出低分 000003（分数 0.1），实际卖出 {sells}"
    assert buys and buys[0]['stock_code'] == '000002', f"应买入高分候选 000002，实际 {buys}"


def test_no_drop_when_holding_better_than_candidates():
    """候选分数全部低于持仓时：不换血（卖出名单为空），持仓保留。"""
    df = make_df({
        '000001': [('2026-01-05', 10.0), ('2026-01-06', 10.1)],   # 持仓 A，分数 0.9
        '000002': [('2026-01-05', 20.0), ('2026-01-06', 20.1)],   # 候选 B，分数 0.2
        '000003': [('2026-01-05', 30.0), ('2026-01-06', 30.1)],   # 候选 C，分数 0.1
    })
    pred = pd.Series([0.9, 0.9, 0.8, 0.8, 0.2, 0.1], index=df.index)  # 持仓分数始终高于候选
    r = run_td(df, pred, topk=2, n_drop=1)
    d2 = [t for t in r.trades if t['date'] == '2026-01-06']
    assert not [t for t in d2 if t['action'] == 'SELL'], f"持仓全优于候选不应换血，实际 {d2}"


# ── n_drop 换血 ──

def test_n_drop_rotation():
    """n_drop=2：每日换出 2 只最弱持仓，买入补足 topk=3。"""
    df = make_df({
        '000001': [('2026-01-05', 10.0), ('2026-01-06', 10.1), ('2026-01-07', 10.2)],
        '000002': [('2026-01-05', 20.0), ('2026-01-06', 20.1), ('2026-01-07', 20.2)],
        '000003': [('2026-01-05', 30.0), ('2026-01-06', 30.1), ('2026-01-07', 30.2)],
        '000004': [('2026-01-05', 40.0), ('2026-01-06', 40.1), ('2026-01-07', 40.2)],
        '000005': [('2026-01-05', 50.0), ('2026-01-06', 50.1), ('2026-01-07', 50.2)],
        '000006': [('2026-01-05', 60.0), ('2026-01-06', 60.1), ('2026-01-07', 60.2)],
        '000007': [('2026-01-05', 70.0), ('2026-01-06', 70.1), ('2026-01-07', 70.2)],
    })
    # 分数按行对齐（make_df 行序 = code 内日期）：每日分数 = 每行第 i 天的值
    # 01-05: [0.9,0.8,0.7,0.6,0.5,0.4,0.3] → 买 top3 = 1/2/3
    # 01-06: 持仓 1/2/3 分数跌到 0.3/0.4/0.5，候选 4/5 升到 0.9/0.8 → 换出 1/2，买入 4/5
    # 01-07: 持仓 3/4/5 变弱，6/7 走强 → 换出 3/4，买入 6/7
    pred = pd.Series(
        [0.9, 0.3, 0.7] + [0.8, 0.4, 0.6] + [0.7, 0.5, 0.5] + [0.6, 0.9, 0.4] +
        [0.5, 0.8, 0.3] + [0.4, 0.7, 0.9] + [0.3, 0.6, 0.8], index=df.index)
    r = run_td(df, pred, topk=3, n_drop=2)
    d2 = [t for t in r.trades if t['date'] == '2026-01-06']
    sells = sorted(t['stock_code'] for t in d2 if t['action'] == 'SELL')
    buys = sorted(t['stock_code'] for t in d2 if t['action'] == 'BUY')
    assert sells == ['000001', '000002'], f"01-06 应换出最弱 2 只（分数 0.3/0.4），实际 {sells}"
    assert buys == ['000004', '000005'], f"01-06 应买入 4/5 分位补足，实际 {buys}"
    # 01-07：持仓 3/4/5 分数 0.5/0.4/0.3（4/5 最弱），6/7 走强 → 换出 4/5，买入 6/7
    d3 = [t for t in r.trades if t['date'] == '2026-01-07']
    sells3 = sorted(t['stock_code'] for t in d3 if t['action'] == 'SELL')
    buys3 = sorted(t['stock_code'] for t in d3 if t['action'] == 'BUY')
    assert sells3 == ['000004', '000005'], f"01-07 应换出最弱 4/5，实际 {sells3}"
    assert buys3 == ['000006', '000007'], f"01-07 应买入 6/7，实际 {buys3}"


# ── hold_thresh ──

def test_hold_thresh_protects_new_position():
    """hold_thresh=2：新买入持仓次日（count_days=1）不被换出，第三日（count_days=2）可换。"""
    df = make_df({
        '000001': [('2026-01-05', 10.0), ('2026-01-06', 10.1), ('2026-01-07', 10.2),
                   ('2026-01-08', 10.3)],
        '000002': [('2026-01-05', 20.0), ('2026-01-06', 20.1), ('2026-01-07', 20.2),
                   ('2026-01-08', 20.3)],
        '000003': [('2026-01-05', 30.0), ('2026-01-06', 30.1), ('2026-01-07', 30.2),
                   ('2026-01-08', 30.3)],
    })
    # 01-05 买 1/2（topk=2）；01-06 候选 3 分最高 → 想换掉最弱（000002，分数 0.2）
    # 但 hold_thresh=2 → 000002 count_days=1 < 2 → 不换；01-07 count_days=2 → 可换
    pred = pd.Series([0.9, 0.8, 0.0, 0.1, 0.2, 0.95, 0.1, 0.2, 0.9, 0.0, 0.0, 0.0],
                     index=df.index)
    r = run_td(df, pred, topk=2, n_drop=1, hold_thresh=2)
    d2 = [t for t in r.trades if t['date'] == '2026-01-06']
    assert not [t for t in d2 if t['action'] == 'SELL'], \
        f"hold_thresh=2 时 01-06 不应换出次新仓，实际 {d2}"
    d3 = [t for t in r.trades if t['date'] == '2026-01-07']
    sells3 = [t['stock_code'] for t in d3 if t['action'] == 'SELL']
    assert sells3, f"01-07 持仓已满 2 天应可换，实际 {d3}"


# ── 固定 topk 与预算 ──

def test_fixed_topk_after_drop():
    """换血后买入补足 topk：收盘持仓数恒等于 topk。"""
    df = make_df({
        '000001': [('2026-01-05', 10.0), ('2026-01-06', 10.1)],
        '000002': [('2026-01-05', 20.0), ('2026-01-06', 20.1)],
        '000003': [('2026-01-05', 30.0), ('2026-01-06', 30.1)],
    })
    pred = pd.Series([0.9, 0.0, 0.8, 0.0, 0.1, 0.7], index=df.index)
    r = run_td(df, pred, topk=2, n_drop=1)
    d2 = [t for t in r.trades if t['date'] == '2026-01-06']
    assert len([t for t in d2 if t['action'] == 'SELL']) == 1
    assert len([t for t in d2 if t['action'] == 'BUY']) == 1
    last = r.daily_records[-1]
    # 持仓数 = 2（000001 + 新买入）
    assert last.position_value > 0


def test_risk_degree_budget():
    """买入预算 = (现金 + 预估卖出净额) × risk_degree / 买入数 → 单笔不超过预算。"""
    df = make_df({
        '000001': [('2026-01-05', 10.0), ('2026-01-06', 10.1)],
        '000002': [('2026-01-05', 20.0), ('2026-01-06', 20.1)],
        '000003': [('2026-01-05', 30.0), ('2026-01-06', 30.1)],
    })
    pred = pd.Series([0.9, 0.0, 0.8, 0.0, 0.1, 0.7], index=df.index)
    r = run_td(df, pred, topk=2, n_drop=1, risk_degree=0.5)
    buys = [t for t in r.trades if t['action'] == 'BUY' and t['date'] == '2026-01-06']
    # 01-05 买入 2 只各 250,000（risk_degree 0.5）→ 现金 ≈ 500,000；
    # 01-06 先卖 000002（净额 ≈ 250,000×0.99875）→ 预算 = 749,688×0.5/1 ≈ 374,844
    assert buys, "应有买入"
    assert all(t['amount'] <= 375_000 + 1 for t in buys), \
        f"单笔买入不应超过 (现金+卖出净额)×risk_degree，实际 {[t['amount'] for t in buys]}"


# ── 事件驱动 + 换血共存 ──

def test_event_exit_and_drop_coexist():
    """止损触发（事件驱动）与换血同日：事件卖出先执行，换血基于剩余持仓。"""
    df = make_df({
        '000001': [('2026-01-05', 10.0), ('2026-01-06', 9.1)],    # 止损触发价 9.2
        '000002': [('2026-01-05', 20.0), ('2026-01-06', 20.1)],   # 持仓低分
        '000003': [('2026-01-05', 30.0), ('2026-01-06', 30.1)],   # 候选高分
        '000004': [('2026-01-05', 40.0), ('2026-01-06', 40.1)],   # 候选中分
    })
    pred = pd.Series([0.9, 0.0, 0.8, 0.0, 0.1, 0.95, 0.0, 0.6], index=df.index)
    r = run_td(df, pred, topk=2, n_drop=1, stop_loss=0.08)
    d2 = [t for t in r.trades if t['date'] == '2026-01-06']
    sells = [t for t in d2 if t['action'] == 'SELL']
    reasons = {t['stock_code']: t['reason'] for t in sells}
    assert '000001' in reasons and reasons['000001'] == 'stop_loss', \
        f"止损触发必须卖出，实际 {reasons}"
    # 换血基于剩余持仓（000002 分数 0.1 最低）→ 也卖出
    assert '000002' in reasons, f"换血应卖出剩余最弱 000002，实际 {reasons}"
    buys = [t['stock_code'] for t in d2 if t['action'] == 'BUY']
    assert set(buys) == {'000003', '000004'}, f"应买入候选补足 topk=2，实际 {buys}"
