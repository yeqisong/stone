"""交割单交易复盘（行为画像 + 纪律闸门）。

设计原则：
1. 只基于事实（交割单 + 行情），不猜测情绪；
2. 每个结论都带数字证据，且给出可机械执行的规则；
3. 期间收益用「期初持仓按期初市值计价」口径 —— 选近30天时衡量的是这30天
   的操作，而不是把几年前的老成本算进来。

核心口径：
- 已实现盈亏：FIFO 逐笔配对，买入费用进成本、卖出费用从收入扣；
- 期初持仓：按区间开始前最后一个收盘价计价（机会成本口径），标记 seeded；
- 账户曲线：现金（资金本次余额）+ 持仓市值，逐交易日盯市。
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import date as _date, timedelta
from typing import Any, Optional

from sqlalchemy import text

from app.trade_slip import (
    _apply_row,
    _close_before,
    _fee_of,
    load_slip_rows,
    replay_positions,
)

DISCIPLINE_KEY = "trade_discipline"

DEFAULT_DISCIPLINE: dict[str, Any] = {
    "max_new_buys_per_day": 2,     # 每日新开仓上限（笔）
    "max_positions": 6,            # 同时持仓上限（只）
    "max_single_weight": 0.20,     # 单票市值占账户上限
    "stop_loss_pct": -0.08,        # 单笔止损线
    "no_rebuy_days": 5,            # 卖出后 N 个自然日内禁止回补
    "no_same_day_roundtrip": True, # 禁止同一标的当日又买又卖
    "max_daily_loss_pct": -0.03,   # 单日账户亏损上限
    "consecutive_loss_trigger": 2, # 连续亏损 N 日 → 次日只减不加
    "max_single_risk_pct": 0.02,   # 单笔最大风险预算（账户 %）
}


# ── 配置读写 ──

def get_discipline(db) -> dict:
    row = db.execute(text(
        "SELECT params FROM strategy_config WHERE strategy_name=:k ORDER BY id DESC LIMIT 1"
    ), {"k": DISCIPLINE_KEY}).fetchone()
    cfg = dict(DEFAULT_DISCIPLINE)
    if row and row[0]:
        try:
            cfg.update(json.loads(row[0]) or {})
        except Exception:
            pass
    return cfg


def save_discipline(db, params: dict) -> dict:
    cfg = dict(DEFAULT_DISCIPLINE)
    for k, v in (params or {}).items():
        if k in cfg:
            cfg[k] = v
    db.execute(text("DELETE FROM strategy_config WHERE strategy_name=:k"), {"k": DISCIPLINE_KEY})
    db.execute(text(
        "INSERT INTO strategy_config (strategy_name, display_name, enabled, params) "
        "VALUES (:k, '交易纪律', true, :p)"
    ), {"k": DISCIPLINE_KEY, "p": json.dumps(cfg, ensure_ascii=False)})
    db.commit()
    return cfg


# ── 格式化 ──

def _money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    n = float(v)
    sign = "-" if n < 0 else ""
    a = abs(n)
    if a >= 1e8:
        return f"{sign}¥{a/1e8:.2f}亿"
    if a >= 1e4:
        return f"{sign}¥{a/1e4:.1f}万"
    return f"{sign}¥{a:,.0f}"


def _pct(v: Optional[float], d: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v*100:+.{d}f}%"


def _num(v: Optional[float], d: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:,.{d}f}"


def _mean(vals: list[float]) -> Optional[float]:
    return round(statistics.mean(vals), 4) if vals else None


def _median(vals: list[float]) -> Optional[float]:
    return round(statistics.median(vals), 4) if vals else None


# ── 行情 ──

def _market(db, codes: list[str], d0: _date, d1: _date):
    closes: dict[str, dict[_date, float]] = defaultdict(dict)
    bars: dict[tuple[str, _date], tuple[Optional[float], Optional[float], Optional[float]]] = {}
    if not codes:
        return closes, bars
    rows = db.execute(text("""
        SELECT stock_code, trade_date, close, open, high, low
        FROM daily_quote
        WHERE stock_code = ANY(CAST(:codes AS varchar[]))
          AND trade_date BETWEEN :d0 AND :d1 AND close > 0
    """), {"codes": codes, "d0": d0, "d1": d1}).fetchall()
    for r in rows:
        closes[r[0]][r[1]] = float(r[2])
        bars[(r[0], r[1])] = (
            float(r[3]) if r[3] is not None else None,
            float(r[4]) if r[4] is not None else None,
            float(r[5]) if r[5] is not None else None,
        )
    return closes, bars


def _calendar(db, d0: _date, d1: _date) -> list[_date]:
    rows = db.execute(text("""
        SELECT cal_date FROM trade_calendar
        WHERE is_trade_day=true AND cal_date BETWEEN :d0 AND :d1
        ORDER BY cal_date
    """), {"d0": d0, "d1": d1}).fetchall()
    return [r[0] for r in rows]


def _index_closes(db, code: str, d0: _date, d1: _date) -> dict[_date, float]:
    rows = db.execute(text("""
        SELECT trade_date, close FROM index_daily_quote
        WHERE index_code=:c AND trade_date BETWEEN :d0 AND :d1 AND close > 0
        ORDER BY trade_date
    """), {"c": code, "d0": d0, "d1": d1}).fetchall()
    return {r[0]: float(r[1]) for r in rows}


def _last_close_map(db, codes: list[str]) -> dict[str, float]:
    if not codes:
        return {}
    rows = db.execute(text("""
        SELECT DISTINCT ON (stock_code) stock_code, close
        FROM daily_quote
        WHERE stock_code = ANY(CAST(:codes AS varchar[])) AND close > 0
        ORDER BY stock_code, trade_date DESC
    """), {"codes": codes}).fetchall()
    return {r[0]: float(r[1]) for r in rows}


# ── 账户曲线 ──

def _equity_curve(rows: list[dict], state0: dict, cash0: Optional[float],
                  dates: list[_date], closes, index_closes) -> tuple[list[dict], list[dict]]:
    """逐日盯市。返回 (points, external_flows)。

    资金流识别：当日现金变化 - 当日清算金额合计 = 外部入金/出金。
    这是关键——「亏了再加钱」会让账户余额看起来只亏一点，必须剔除后才能看到真实收益。
    """
    holds = {c: float(s["qty"]) for c, s in state0.items() if s["qty"] > 0.5}
    last_px = {c: float(s["avg"]) for c, s in state0.items() if s["qty"] > 0.5}
    cash = cash0
    by_date: dict[_date, list[dict]] = defaultdict(list)
    for r in rows:
        by_date[r["trade_date"]].append(r)
    # 基准从区间第一个交易日的收盘归一化（不能用更早日期的数据当 1.0）
    bench0 = None
    if index_closes and dates:
        for d, v in sorted(index_closes.items()):
            if d <= dates[0]:
                bench0 = v
            else:
                break
    bench_last = bench0
    out = []
    flows = []
    cum_flow = 0.0
    for d in dates:
        cash_start = cash
        day_rows = by_date.get(d, [])
        for r in day_rows:
            code = r["stock_code"]
            # 只关心股数与现金；成本变化不影响曲线
            tmp = {"qty": holds.get(code, 0.0), "avg": last_px.get(code, 0.0), "realized": 0.0, "estimated": False}
            _apply_row({code: tmp}, r)
            holds[code] = tmp["qty"]
            if r.get("cash_balance") is not None:
                cash = float(r["cash_balance"])
        flow = 0.0
        if day_rows and cash is not None and cash_start is not None:
            has_cash = any(r.get("cash_balance") is not None for r in day_rows)
            if has_cash:
                day_clean = sum(float(r.get("clean_amount") or 0) for r in day_rows)
                flow = cash - (cash_start + day_clean)
                if abs(flow) < 1.0:
                    flow = 0.0
        if flow:
            flows.append({"date": d.isoformat(), "amount": round(flow, 2)})
            cum_flow += flow
        mv_by_code = {}
        for code, qty in holds.items():
            if qty <= 0.5:
                continue
            px = closes.get(code, {}).get(d)
            if px is None:
                px = last_px.get(code)
            if px is None:
                continue
            last_px[code] = px
            mv_by_code[code] = qty * px
        mv = sum(mv_by_code.values())
        equity = (cash or 0.0) + mv
        if index_closes:
            if d in index_closes:
                bench_last = index_closes[d]
        top_code = max(mv_by_code, key=mv_by_code.get) if mv_by_code else None
        out.append({
            "date": d,
            "equity": round(equity, 2),
            "adjusted": round(equity - cum_flow, 2),
            "flow": round(flow, 2),
            "cash": round(cash or 0.0, 2),
            "market_value": round(mv, 2),
            "n_positions": sum(1 for q in holds.values() if q > 0.5),
            "top_code": top_code,
            "max_weight": round(mv_by_code[top_code] / equity, 4) if top_code and equity > 0 else 0.0,
            "benchmark": (round(bench_last / bench0, 6) if bench0 and bench_last else None),
        })
    return out, flows


def _drawdown(points: list[dict]) -> dict:
    peak = -1e18
    peak_date = None
    mdd = 0.0
    trough_date = None
    mdd_peak_date = None
    mdd_peak_equity = None
    mdd_trough_equity = None
    for p in points:
        e = p["equity"]
        if e > peak:
            peak = e
            peak_date = p["date"]
        if peak > 0:
            dd = (peak - e) / peak
            if dd > mdd:
                mdd, trough_date = dd, p["date"]
                mdd_peak_date, mdd_peak_equity, mdd_trough_equity = peak_date, peak, e
    return {
        "max_drawdown": round(mdd, 6),
        "peak_date": mdd_peak_date.isoformat() if mdd_peak_date else None,
        "trough_date": trough_date.isoformat() if trough_date else None,
        "peak_equity": round(mdd_peak_equity, 2) if mdd_peak_equity else None,
        "trough_equity": round(mdd_trough_equity, 2) if mdd_trough_equity else None,
    }


# ── FIFO 配对 + 交易回合 ──

def _match_rows(rows: list[dict], state0: dict, closes) -> dict:
    """FIFO 逐笔配对，返回 realized / 回合(episode) / 卖出事件。"""
    lots: dict[str, list[list[float]]] = {}
    for code, st in state0.items():
        if st["qty"] > 0.5:
            lots[code] = [[float(st["qty"]), float(st["avg"])]]
    state = {c: {"qty": float(s["qty"]), "avg": float(s["avg"]),
                 "realized": 0.0, "estimated": bool(s.get("estimated"))}
             for c, s in state0.items()}
    episodes: list[dict] = []
    open_ep: dict[str, dict] = {}
    sell_events: list[dict] = []
    add_win: list[float] = []
    add_loss: list[float] = []
    add_loss_detail: dict[str, dict] = {}
    realized_total = 0.0

    for code, st in state.items():
        if st["qty"] > 0.5:
            open_ep[code] = {
                "code": code, "name": code, "start_date": rows[0]["trade_date"] if rows else None,
                "seeded": True, "pnl": 0.0, "buy_amount": 0.0, "sell_amount": 0.0,
                "fees": 0.0, "buy_count": 0, "sell_count": 0, "max_qty": st["qty"],
                "first_price": st["avg"], "cost_basis": st["avg"], "last_date": None,
            }

    for r in rows:
        code = r["stock_code"]
        st = state.setdefault(code, {"qty": 0.0, "avg": 0.0, "realized": 0.0, "estimated": False})
        q = float(r.get("quantity") or 0)
        px = float(r.get("price") or 0)
        amt = float(r.get("amount") or 0)
        fees = _fee_of(r)
        side = r["side"]

        if side == "buy" and q > 0:
            if code in open_ep:
                ep = open_ep[code]
                ep["buy_amount"] += amt
                ep["buy_count"] += 1
                ep["name"] = r["stock_name"]
                if st["qty"] > 0.5 and st["avg"] > 0:
                    if px >= st["avg"]:
                        add_win.append(amt)
                    else:
                        add_loss.append(amt)
                        d = add_loss_detail.setdefault(code, {
                            "code": code, "name": r["stock_name"], "count": 0, "amount": 0.0,
                            "first_price": px, "last_price": px, "first_date": r["trade_date"],
                            "last_date": r["trade_date"], "avg_cost": st["avg"],
                        })
                        d["count"] += 1
                        d["amount"] += amt
                        d["last_price"] = px
                        d["last_date"] = r["trade_date"]
                        d["avg_cost"] = st["avg"]
            elif st["qty"] <= 0.5:
                open_ep[code] = {
                    "code": code, "name": r["stock_name"], "start_date": r["trade_date"],
                    "seeded": False, "pnl": 0.0, "buy_amount": amt, "sell_amount": 0.0,
                    "fees": 0.0, "buy_count": 1, "sell_count": 0, "max_qty": 0.0,
                    "first_price": px, "cost_basis": px, "last_date": None,
                }
            unit = (amt if amt > 0 else px * q) + fees
            unit_cost = unit / q if q > 0 else 0.0
            lots.setdefault(code, []).append([q, unit_cost])

        elif side == "sell" and q > 0:
            remain = q
            cost = 0.0
            matched = 0.0
            while remain > 1e-9 and lots.get(code):
                lot = lots[code][0]
                take = min(remain, lot[0])
                cost += take * lot[1]
                matched += take
                lot[0] -= take
                remain -= take
                if lot[0] <= 1e-9:
                    lots[code].pop(0)
            if remain > 1e-9:
                # 无对应买入（历史缺行）→ 用当前均价兜底
                cost += remain * float(st["avg"] or 0)
                matched += remain
            pnl = px * matched - cost - fees
            realized_total += pnl
            ev = {"date": r["trade_date"], "code": code, "name": r["stock_name"],
                  "qty": matched, "price": px, "pnl": pnl, "fees": fees,
                  "seeded": bool(open_ep.get(code, {}).get("seeded"))}
            sell_events.append(ev)
            ep = open_ep.get(code)
            if ep:
                ep["pnl"] += pnl
                ep["sell_amount"] += amt
                ep["fees"] += fees
                ep["sell_count"] += 1
                ep["last_date"] = r["trade_date"]
                ep["name"] = r["stock_name"]

        elif r.get("subtype") == "bonus" and q > 0:
            # 送股/红股：零成本增加股数，总成本不变（均价自然摊薄）。
            # 若给红股 lot 赋一个非零成本，会把总成本凭空放大，后续卖出会产生假亏损。
            lots.setdefault(code, []).append([q, 0.0])
            ep = open_ep.get(code)
            if ep:
                ep["max_qty"] = max(ep["max_qty"], st["qty"] + q)

        _apply_row(state, r)
        ep = open_ep.get(code)
        if ep and st["qty"] > 1e-9:
            ep["max_qty"] = max(ep["max_qty"], st["qty"])
            if side == "buy":
                ep["cost_basis"] = st["avg"]
        if st["qty"] <= 0.5 and code in open_ep:
            ep = open_ep.pop(code)
            ep["end_date"] = r["trade_date"]
            ep["closed"] = True
            ep["hold_days"] = (ep["end_date"] - ep["start_date"]).days if ep.get("start_date") else None
            episodes.append(ep)

    for code, ep in open_ep.items():
        ep["closed"] = False
        ep["end_date"] = rows[-1]["trade_date"] if rows else None
        ep["hold_days"] = (ep["end_date"] - ep["start_date"]).days if ep.get("start_date") else None
        episodes.append(ep)

    return {
        "realized_total": realized_total,
        "episodes": sorted(episodes, key=lambda e: (e.get("end_date") or _date.min), reverse=True),
        "sell_events": sell_events,
        "add_win": add_win,
        "add_loss": add_loss,
        "add_loss_detail": add_loss_detail,
    }


# ── 行为指标 ──

def _behavior(rows: list[dict], match: dict, closes, bars, equity: list[dict]) -> dict:
    buys = [r for r in rows if r["side"] == "buy"]
    sells = [r for r in rows if r["side"] == "sell"]
    active_days = sorted({r["trade_date"] for r in rows})
    by_day: dict[_date, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[r["trade_date"]].append(r)

    # 频率
    trades_per_day = len(buys) + len(sells)
    max_day = max(by_day.items(), key=lambda kv: len([r for r in kv[1] if r["side"] in ("buy", "sell")]), default=(None, []))
    max_day_n = len([r for r in max_day[1] if r["side"] in ("buy", "sell")])
    max_day_buys = max((len([r for r in rs if r["side"] == "buy"]) for rs in by_day.values()), default=0)
    distinct_stocks = len({r["stock_code"] for r in rows})
    week_sets: dict[tuple, set] = defaultdict(set)
    for r in rows:
        week_sets[r["trade_date"].isocalendar()[:2]].add(r["stock_code"])
    stocks_per_week = (_mean([len(s) for s in week_sets.values()]) or 0) if week_sets else 0

    # 同日回转 / 卖出后回补
    same_day = []
    for d, rs in by_day.items():
        codes_buy = {r["stock_code"] for r in rs if r["side"] == "buy"}
        codes_sell = {r["stock_code"] for r in rs if r["side"] == "sell"}
        for c in codes_buy & codes_sell:
            same_day.append({"date": d, "code": c,
                             "name": next((r["stock_name"] for r in rs if r["stock_code"] == c), c)})
    rebuys = []
    sell_dates: dict[str, list[_date]] = defaultdict(list)
    for r in sells:
        sell_dates[r["stock_code"]].append(r["trade_date"])
    for r in buys:
        for sd in sell_dates.get(r["stock_code"], []):
            gap = (r["trade_date"] - sd).days
            if 0 < gap <= 7:
                rebuys.append({"date": r["trade_date"], "sell_date": sd, "code": r["stock_code"],
                               "name": r["stock_name"], "gap_days": gap, "amount": r["amount"]})
                break

    # 亏损日次日行为
    daily_realized: dict[_date, float] = defaultdict(float)
    for ev in match["sell_events"]:
        daily_realized[ev["date"]] += ev["pnl"]
    day_buy_amt = {d: sum(r["amount"] for r in rs if r["side"] == "buy") for d, rs in by_day.items()}
    day_ntrades = {d: len([r for r in rs if r["side"] in ("buy", "sell")]) for d, rs in by_day.items()}
    after_loss, after_win = [], []
    for i in range(len(active_days) - 1):
        d, nxt = active_days[i], active_days[i + 1]
        if (nxt - d).days > 4:
            continue
        rec = (day_ntrades.get(nxt, 0), day_buy_amt.get(nxt, 0.0))
        if rec[0] <= 0:
            continue
        (after_loss if daily_realized.get(d, 0.0) < 0 else after_win).append(rec)
    max_consec_loss = 0
    cur = 0
    for d in active_days:
        if daily_realized.get(d, 0.0) < 0:
            cur += 1
            max_consec_loss = max(max_consec_loss, cur)
        elif daily_realized.get(d, 0.0) > 0:
            cur = 0

    # 追高：买入价在当日振幅中的位置 + 当日涨幅
    chase, buy_day_chg = [], []
    for r in buys:
        o, h, l = bars.get((r["stock_code"], r["trade_date"]), (None, None, None))
        if h and l and h > l:
            chase.append(max(0.0, min(1.0, (r["price"] - l) / (h - l))))
        ds = sorted(closes.get(r["stock_code"], {}).keys())
        prev = [d for d in ds if d < r["trade_date"]]
        if prev and r["price"] > 0:
            buy_day_chg.append(r["price"] / closes[r["stock_code"]][prev[-1]] - 1)

    # 卖出后走势（卖飞检验）
    post: dict[int, list[float]] = {5: [], 10: [], 20: []}
    for r in sells:
        ds = sorted(closes.get(r["stock_code"], {}).keys())
        fut = [d for d in ds if d > r["trade_date"]]
        for n in post:
            if len(fut) >= n and r["price"] > 0:
                post[n].append(closes[r["stock_code"]][fut[n - 1]] / r["price"] - 1)

    # 回合统计
    closed = [e for e in match["episodes"] if e.get("closed")]
    seeded = [e for e in closed if e.get("seeded")]
    wins = [e for e in closed if e["pnl"] > 0]
    losses = [e for e in closed if e["pnl"] < 0]
    hold_win = [e["hold_days"] for e in wins if e.get("hold_days") is not None and not e.get("seeded")]
    hold_loss = [e["hold_days"] for e in losses if e.get("hold_days") is not None and not e.get("seeded")]
    pf = (sum(e["pnl"] for e in wins) / abs(sum(e["pnl"] for e in losses))) if losses else None
    worst = min(closed, key=lambda e: e["pnl"], default=None)
    best = max(closed, key=lambda e: e["pnl"], default=None)
    top_wins = sorted((e["pnl"] for e in closed), reverse=True)[:3]
    top_losses = sorted((e["pnl"] for e in closed))[:3]

    total_amount = sum(r["amount"] for r in rows if r["side"] in ("buy", "sell"))
    fee_total = sum(_fee_of(r) for r in rows)
    eq_start = equity[0]["equity"] if equity else None
    eq_end = equity[-1]["equity"] if equity else None
    avg_equity = ((eq_start or 0) + (eq_end or 0)) / 2 if eq_start and eq_end else None

    max_weight_point = max(equity, key=lambda p: p["max_weight"], default=None)
    max_positions = max((p["n_positions"] for p in equity), default=0)
    daily_returns = []
    for i in range(1, len(equity)):
        prev = equity[i - 1]["equity"]
        if prev:
            daily_returns.append((equity[i]["date"], equity[i]["equity"] / prev - 1))
    min_daily = min(daily_returns, key=lambda x: x[1], default=(None, None))

    return {
        "trades": trades_per_day,
        "active_days": len(active_days),
        "trades_per_day": round(trades_per_day / len(active_days), 2) if active_days else 0,
        "max_day_trades": max_day_n,
        "max_day_buys": max_day_buys,
        "max_day_date": max_day[0].isoformat() if max_day[0] else None,
        "distinct_stocks": distinct_stocks,
        "stocks_per_week": round(stocks_per_week, 2),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "buy_amount": round(sum(r["amount"] for r in buys), 2),
        "sell_amount": round(sum(r["amount"] for r in sells), 2),
        "fee_total": round(fee_total, 2),
        "fee_bps": round(fee_total / total_amount * 10000, 2) if total_amount else None,
        "turnover_ratio": round(total_amount / avg_equity, 2) if avg_equity else None,
        "same_day_roundtrips": len(same_day),
        "same_day_samples": same_day[:8],
        "rebuy_within_7d": len(rebuys),
        "rebuy_samples": sorted(rebuys, key=lambda x: x["date"], reverse=True)[:8],
        "add_win_count": len(match["add_win"]),
        "add_win_amount": round(sum(match["add_win"]), 2),
        "add_loss_count": len(match["add_loss"]),
        "add_loss_amount": round(sum(match["add_loss"]), 2),
        "worst_avgdown": _worst_avgdown(match.get("add_loss_detail") or {}),
        "post_loss_next_day": {
            "n": len(after_loss),
            "avg_trades": round(_mean([x[0] for x in after_loss]) or 0, 2),
            "avg_buy_amount": round(_mean([x[1] for x in after_loss]) or 0, 2),
        },
        "post_win_next_day": {
            "n": len(after_win),
            "avg_trades": round(_mean([x[0] for x in after_win]) or 0, 2),
            "avg_buy_amount": round(_mean([x[1] for x in after_win]) or 0, 2),
        },
        "max_consecutive_loss_days": max_consec_loss,
        "chase_position": _mean(chase),
        "chase_high_share": round(sum(1 for x in chase if x > 0.8) / len(chase), 4) if chase else None,
        "buy_up_3pct_share": round(sum(1 for x in buy_day_chg if x > 0.03) / len(buy_day_chg), 4) if buy_day_chg else None,
        "post_sell_5d": _mean(post[5]),
        "post_sell_10d": _mean(post[10]),
        "post_sell_20d": _mean(post[20]),
        "win_rate": round(len(wins) / len(closed), 4) if closed else None,
        "closed_episodes": len(closed),
        "seeded_episodes": len(seeded),
        "avg_win": round(_mean([e["pnl"] for e in wins]) or 0, 2) if wins else None,
        "avg_loss": round(_mean([e["pnl"] for e in losses]) or 0, 2) if losses else None,
        "profit_factor": round(pf, 2) if pf is not None else None,
        "expectancy": round(sum(e["pnl"] for e in closed) / len(closed), 2) if closed else None,
        "hold_days_win": _mean(hold_win),
        "hold_days_loss": _mean(hold_loss),
        "hold_days_win_median": _median(hold_win),
        "hold_days_loss_median": _median(hold_loss),
        "disposition_ratio": round((_mean(hold_loss) / _mean(hold_win)), 2)
        if hold_win and hold_loss and _mean(hold_win) else None,
        "worst_episode": ({k: (v.isoformat() if isinstance(v, _date) else v) for k, v in worst.items()}
                          if worst else None),
        "best_episode": ({k: (v.isoformat() if isinstance(v, _date) else v) for k, v in best.items()}
                         if best else None),
        "worst_episode_return": round(worst["pnl"] / worst["buy_amount"], 4)
        if worst and worst.get("buy_amount") else None,
        "top3_wins": [round(x, 2) for x in top_wins],
        "top3_losses": [round(x, 2) for x in top_losses],
        "max_single_buy": round(max((r["amount"] for r in buys), default=0), 2),
        "max_positions": max_positions,
        "max_single_weight": round(max_weight_point.get("max_weight") or 0, 4) if max_weight_point else None,
        "max_weight_date": max_weight_point["date"].isoformat() if max_weight_point else None,
        "max_weight_code": max_weight_point.get("top_code") if max_weight_point else None,
        "worst_daily_return": round(min_daily[1], 4) if min_daily[1] is not None else None,
        "worst_daily_date": min_daily[0].isoformat() if min_daily[0] else None,
    }


# ── 结论 / 规则 / 评分 ──

def _worst_avgdown(detail: dict) -> Optional[dict]:
    if not detail:
        return None
    code, d = max(detail.items(), key=lambda kv: kv[1]["amount"])
    return {
        **{k: (v.isoformat() if isinstance(v, _date) else v) for k, v in d.items()},
        "drop_pct": round(d["last_price"] / d["first_price"] - 1, 4) if d["first_price"] else None,
    }


def _findings(summary: dict, beh: dict, cfg: dict, counterfactual: dict | None) -> list[dict]:
    out = []

    def add(level, key, title, evidence, advice):
        out.append({"level": level, "key": key, "title": title, "evidence": evidence, "advice": advice})

    dd = summary.get("max_drawdown") or 0
    if dd >= 0.25:
        add("critical", "drawdown",
            f"剔除入金后从峰值回撤 {dd*100:.0f}%，把浮盈全部交回去",
            f"调整后净值 {summary.get('peak_date')} 峰值 {_money(summary.get('peak_equity'))} → "
            f"{summary.get('trough_date')} {_money(summary.get('trough_equity'))}；"
            f"同期基准 {summary.get('benchmark_name')} {_pct(summary.get('benchmark_return'))}。",
            "加账户级回撤闸门：回撤 -10% 仓位减半，-15% 只减不加，-20% 停手 5 个交易日。"
            "回撤期的首要目标不是赚回来，是止血。")

    net_dep = summary.get("net_deposit") or 0
    if net_dep and summary.get("equity_start") and abs(net_dep) > summary["equity_start"] * 0.05:
        direction = "净入金" if net_dep > 0 else "净出金"
        add("critical" if net_dep > 0 else "warn", "capital_injection",
            f"期间{direction} {_money(abs(net_dep))}：资金进出掩盖了真实亏损",
            f"账户 {_money(summary.get('equity_start'))} → {_money(summary.get('equity_end'))}"
            f"（表面 {_pct((summary.get('account_change') or 0) / (summary.get('equity_start') or 1))}）；"
            f"剔除{direction}后交易盈亏 {_money(summary.get('pnl'))}，"
            f"时间加权收益 {_pct(summary.get('return_pct'))}。",
            "入金和交易成绩必须分开记账：每次银证转账单独记录，复盘只看剔除入金后的净值。"
            "亏损后加钱翻本，只会把亏损按更大的本金放大一遍。")

    add_loss_amt = beh.get("add_loss_amount") or 0
    add_win_amt = beh.get("add_win_amount") or 0
    if add_loss_amt > max(add_win_amt * 1.2, 50000):
        wd = beh.get("worst_avgdown") or {}
        case = ""
        if wd:
            case = (f"最典型的是 {wd.get('name')}（{wd.get('code')}）：{wd.get('first_date')} 起 "
                    f"以 {_num(wd.get('first_price'), 2)} 一路加到 {_num(wd.get('last_price'), 2)}"
                    f"（{_pct(wd.get('drop_pct'), 0)}），共 {wd.get('count')} 笔 / {_money(wd.get('amount'))}。")
        add("critical", "averaging_down",
            "亏损加仓是最大亏损来源：越跌越买，把仓位压进下跌趋势",
            f"已有持仓上的加仓：亏损时 {beh['add_loss_count']} 笔 / {_money(add_loss_amt)}，"
            f"盈利时 {beh['add_win_count']} 笔 / {_money(add_win_amt)}。{case}",
            "机械规则：浮亏超过 -8% 的持仓禁止加仓；加仓只允许在盈利且收盘价站上 20 日线时，"
            "单次不超过原仓位 50%。")

    pf = beh.get("profit_factor")
    if pf is not None and pf < 1.0:
        add("critical" if pf < 0.8 else "warn", "profit_factor",
            f"盈亏比 {pf:.2f}：赚小钱、亏大钱，数学上必然亏",
            f"胜率 {_pct(beh.get('win_rate'), 1)}，平均盈利 {_money(beh.get('avg_win'))}，"
            f"平均亏损 {_money(abs(beh.get('avg_loss') or 0))}，单笔期望 {_money(beh.get('expectancy'))}；"
            f"最大 3 笔盈利 {_money(sum(beh.get('top3_wins') or []))}，"
            f"最大 3 笔亏损 {_money(abs(sum(beh.get('top3_losses') or [])))}。",
            "开仓前先算盈亏比：至少 1.5:1 才允许买。做不到就放弃这笔交易。")

    disp = beh.get("disposition_ratio")
    if disp is not None and disp > 1.25:
        add("warn", "disposition",
            "处置效应：盈利单拿不住，亏损单舍不得砍",
            f"盈利单平均持有 {_num(beh.get('hold_days_win'), 1)} 天，亏损单 "
            f"{_num(beh.get('hold_days_loss'), 1)} 天（比值 {disp:.2f}）。",
            "入场时同时写下止损价和“证伪条件”；亏损单超过 5 个交易日仍未回到成本，无条件减半。")

    tpd = beh.get("trades_per_day") or 0
    spw = beh.get("stocks_per_week") or 0
    if tpd > 3 or spw > 5:
        add("warn", "overtrading",
            f"过度交易：日均 {tpd:.1f} 笔、一年 {beh['distinct_stocks']} 只标的",
            f"{beh['trades']} 笔 / {beh['active_days']} 个交易日；"
            f"同日又买又卖 {beh['same_day_roundtrips']} 天；卖出后 7 天内回补 {beh['rebuy_within_7d']} 次；"
            f"单日最多 {beh['max_day_trades']} 笔成交、{beh.get('max_day_buys')} 笔开仓（{beh.get('max_day_date')}）。",
            "每天最多 2 笔新开仓；同时持仓不超过 6 只；卖出后 5 个交易日内不碰同一只。")

    pl = beh.get("post_loss_next_day") or {}
    pw = beh.get("post_win_next_day") or {}
    if pl.get("avg_buy_amount") and pw.get("avg_buy_amount") and pl["avg_buy_amount"] > pw["avg_buy_amount"] * 1.2:
        add("critical", "revenge_trading",
            "亏损后第二天交易更猛：典型的报复性交易",
            f"亏损日次日日均 {_num(pl.get('avg_trades'), 1)} 笔 / 买入 {_money(pl.get('avg_buy_amount'))}；"
            f"盈利日次日 {_num(pw.get('avg_trades'), 1)} 笔 / {_money(pw.get('avg_buy_amount'))}；"
            f"最长连续亏损 {beh.get('max_consecutive_loss_days')} 天。",
            "连续 2 个亏损日 → 次日禁止开新仓，只允许减仓；违反一次，强制空仓 1 天。")

    worst_ret = beh.get("worst_episode_return")
    if worst_ret is not None and worst_ret < -0.05:
        w = beh.get("worst_episode") or {}
        add("critical", "single_loss",
            f"单笔最大亏损 {_money(abs(w.get('pnl') or 0))}（本金的 {_pct(abs(worst_ret), 1)}），没有止损",
            f"{w.get('name')} 从 {w.get('start_date')} 持有到 {w.get('end_date')}，"
            f"成本 {_num(w.get('cost_basis'), 2)}；买入合计 {_money(w.get('buy_amount'))}。",
            "单笔风险预算 ≤ 账户 2%；按止损距离反推仓位：仓位 = 2% × 账户 ÷ 止损幅度。"
            "先定止损，再算能买多少股。")

    mw = beh.get("max_single_weight")
    if mw is not None and mw > 0.35:
        add("critical", "concentration",
            f"单票一度占账户 {mw*100:.0f}%，把账户压在一只下跌股上",
            f"{beh.get('max_weight_date')} 最大持仓 {beh.get('max_weight_code')} 占比 {_pct(mw, 0)}；"
            f"同时持仓最多 {beh.get('max_positions')} 只。",
            "单票 ≤ 20%，单一行业 ≤ 35%；超过就只减不加。集中不是勇敢，是把一次判断错误放大成账户级错误。")

    if counterfactual:
        hold_ret = counterfactual.get("hold_return_pct")
        if hold_ret is not None and hold_ret < (summary.get("return_pct") or 0):
            add("ok", "strength_selection",
                "你并不是不会选股：换股让你避开了期初持仓的深坑",
                f"期初 6 只票原样持有约 {_pct(hold_ret)}（{_money(counterfactual.get('hold_pnl'))}）；"
                f"实际交易（剔除入金）{_pct(summary.get('return_pct'))}（{_money(summary.get('pnl'))}）。"
                f"卖出后 5 日均值 {_pct(beh.get('post_sell_5d'), 1)}，说明卖出时点并不差。",
                "问题不在眼光，在仓位和手速：把选股能力留在场上，把风险控制和交易频率降下来。")
        elif hold_ret is not None:
            gap = (summary.get("return_pct") or 0) - hold_ret
            add("critical" if gap < -0.10 else "warn", "strength_selection",
                "你的交易跑输了「什么都不做」",
                f"期初持仓原样持有约 {_pct(hold_ret)}（{_money(counterfactual.get('hold_pnl'))}）；"
                f"实际交易（剔除入金）{_pct(summary.get('return_pct'))}（{_money(summary.get('pnl'))}）。"
                f"多亏的 {_money(abs((summary.get('pnl') or 0) - (counterfactual.get('hold_pnl') or 0)))} "
                f"来自交易本身，不是市场。",
                "先停止增加交易复杂度：仓位降到能承受的水平，只做最熟悉的 2-3 只，"
                "每笔写下买入理由和止损价；连续 20 笔没有正期望就停手一周。")

    if beh.get("fee_total"):
        add("info", "fees",
            f"全年手续费/印花税 {_money(beh['fee_total'])}（{beh.get('fee_bps')} 个基点）",
            f"共 {beh['trades']} 笔成交，摩擦成本占买入+卖出总额的 "
            f"{_pct((beh.get('fee_bps') or 0) / 10000, 3)}。",
            "费用不是主因，但高频会让它变成持续的负期望；按上面的频率规则降下来即可。")

    order = {"critical": 0, "warn": 1, "ok": 2, "info": 3}
    out.sort(key=lambda f: order.get(f["level"], 9))
    return out


def _rules(beh: dict, summary: dict, cfg: dict) -> list[dict]:
    def rule(key, name, current, target, unit, fmt, hint):
        ok = (current <= target) if isinstance(target, (int, float)) and target >= 0 and key not in ("stop_loss_pct", "max_daily_loss_pct") else (current >= target)
        return {"key": key, "name": name, "current": current, "target": target,
                "unit": unit, "display": fmt(current), "target_display": fmt(target),
                "status": "pass" if ok else "fail", "hint": hint}

    rules = [
        rule("max_new_buys_per_day", "每日新开仓上限", beh.get("max_day_buys") or 0,
             cfg["max_new_buys_per_day"], "笔", lambda v: f"{v:g}",
             "按最差的一天算；目标是把单日新开仓压到 2 笔以内"),
        rule("max_positions", "同时持仓上限", beh.get("max_positions") or 0,
             cfg["max_positions"], "只", lambda v: f"{v:g}",
             "持仓越多，注意力和止损纪律越难维持"),
        rule("max_single_weight", "单票仓位上限", beh.get("max_single_weight") or 0,
             cfg["max_single_weight"], "%", lambda v: f"{(v or 0)*100:.0f}%",
             "单票超过 20% 时，一次 -20% 就是账户 -4%"),
        rule("stop_loss_pct", "单笔止损线", beh.get("worst_episode_return") or 0,
             cfg["stop_loss_pct"], "%", lambda v: f"{(v or 0)*100:+.1f}%",
             "最差一笔的亏损幅度；超过 -8% 说明没有执行止损"),
        rule("no_same_day_roundtrip", "禁止当日回转", beh.get("same_day_roundtrips") or 0,
             0, "天", lambda v: f"{v:g}",
             "同一标的当天又买又卖，通常是手痒而非计划"),
        rule("no_rebuy_days", "卖出后回补冷却", beh.get("rebuy_within_7d") or 0,
             0, "次", lambda v: f"{v:g}",
             f"卖出后 {cfg['no_rebuy_days']} 个自然日内回补的次数"),
        rule("max_daily_loss_pct", "单日亏损上限", beh.get("worst_daily_return") or 0,
             cfg["max_daily_loss_pct"], "%", lambda v: f"{(v or 0)*100:+.1f}%",
             "单日账户跌幅；触发后当天停止开新仓"),
        rule("consecutive_loss_trigger", "连续亏损冷静期", beh.get("max_consecutive_loss_days") or 0,
             cfg["consecutive_loss_trigger"], "天", lambda v: f"{v:g}",
             "连续亏损这么多天后，次日只允许减仓、禁止开新仓"),
    ]
    return rules


def _score(summary: dict, beh: dict, cfg: dict) -> dict:
    parts = []

    dd = summary.get("max_drawdown") or 0
    p_dd = min(25.0, max(0.0, (dd - 0.15) / 0.35 * 25)) if dd > 0.15 else 0.0
    parts.append({"key": "回撤控制", "penalty": round(p_dd, 1), "max": 25})

    pf = beh.get("profit_factor")
    p_pf = min(20.0, max(0.0, (1.2 - (pf if pf is not None else 1.2)) / 1.2 * 20)) if pf is not None else 10.0
    parts.append({"key": "盈亏结构", "penalty": round(p_pf, 1), "max": 20})

    disp = beh.get("disposition_ratio")
    p_disp = min(15.0, max(0.0, ((disp or 1.0) - 1.0) * 15)) if disp is not None else 0.0
    parts.append({"key": "处置效应", "penalty": round(p_disp, 1), "max": 15})

    tpd = beh.get("trades_per_day") or 0
    p_freq = min(15.0, max(0.0, (tpd - 2) * 5)) if tpd > 2 else 0.0
    parts.append({"key": "交易频率", "penalty": round(p_freq, 1), "max": 15})

    impulse = 0.0
    impulse += min(6.0, (beh.get("same_day_roundtrips") or 0) / 10.0)
    impulse += min(5.0, (beh.get("rebuy_within_7d") or 0) / 20.0)
    pl, pw = beh.get("post_loss_next_day") or {}, beh.get("post_win_next_day") or {}
    if pl.get("avg_buy_amount") and pw.get("avg_buy_amount") and pw["avg_buy_amount"] > 0:
        ratio = pl["avg_buy_amount"] / pw["avg_buy_amount"]
        impulse += min(4.0, max(0.0, (ratio - 1.0) * 10))
    parts.append({"key": "冲动控制", "penalty": round(min(15.0, impulse), 1), "max": 15})

    mw = beh.get("max_single_weight") or 0
    p_pos = min(10.0, max(0.0, (mw - 0.20) * 30)) if mw > 0.20 else 0.0
    parts.append({"key": "仓位纪律", "penalty": round(p_pos, 1), "max": 10})

    penalty = sum(p["penalty"] for p in parts)
    score = max(0, round(100 - penalty, 1))
    if score >= 80:
        grade, label = "A", "纪律良好"
    elif score >= 65:
        grade, label = "B", "基本可控"
    elif score >= 50:
        grade, label = "C", "需要收紧"
    elif score >= 35:
        grade, label = "D", "高风险"
    else:
        grade, label = "E", "失控"
    return {"score": score, "grade": grade, "label": label, "parts": parts,
            "summary": "分数低不代表没救，代表当前操作方式在数学上必然亏钱；先改最容易的两项。"}


# ── 主入口 ──

def build_review(db, start: Optional[_date] = None, end: Optional[_date] = None,
                 benchmark: str = "000300") -> dict:
    all_rows = load_slip_rows(db, end=end)
    if not all_rows:
        return {"ok": False, "error": "尚无交割单数据，请先在持仓页导入交割单",
                "warnings": ["尚无交割单数据"]}
    first_date = all_rows[0]["trade_date"]
    last_date = all_rows[-1]["trade_date"]
    start = max(start, first_date) if start else first_date
    end = min(end, last_date) if end else last_date
    if start > end:
        return {"ok": False, "error": "区间开始晚于结束"}

    win_rows = [r for r in all_rows if start <= r["trade_date"] <= end]
    if not win_rows:
        return {"ok": False, "error": "所选区间内没有成交记录"}

    codes = sorted({r["stock_code"] for r in all_rows})
    d0 = start - timedelta(days=90)
    d1 = end + timedelta(days=90)
    closes, bars = _market(db, codes, d0, d1)
    index_closes = _index_closes(db, benchmark, start - timedelta(days=10), end)
    names = {}
    for r in all_rows:
        names[r["stock_code"]] = r["stock_name"]

    # 期初状态：先重放区间前的成交，再把剩余持仓按区间前收盘价重新计价（机会成本口径）
    state0, cash0 = replay_positions(db, all_rows, until=start)
    start_px = _close_before(db, list(state0), start)
    for code, st in state0.items():
        st["avg"] = float(start_px.get(code) or st["avg"] or 0)
        st["estimated"] = True
    equity_start_mv = sum(s["qty"] * s["avg"] for s in state0.values() if s["qty"] > 0)
    if cash0 is None:
        cash0 = 0.0

    match = _match_rows(win_rows, state0, closes)

    dates = _calendar(db, start, end)
    if not dates:
        dates = sorted({d for c in closes for d in closes[c] if start <= d <= end})
    extra = sorted({r["trade_date"] for r in win_rows if r["trade_date"] not in set(dates)})
    dates = sorted(set(dates) | set(extra))
    if not dates:
        dates = [start, end]
    equity, flows = _equity_curve(win_rows, state0, cash0, dates, closes, index_closes)

    raw_start = round(equity[0]["equity"], 2) if equity else round(cash0 + equity_start_mv, 2)
    raw_end = round(equity[-1]["equity"], 2) if equity else None
    net_deposit = round(sum(f["amount"] for f in flows), 2)
    trading_pnl = round((raw_end or 0) - raw_start - net_deposit, 2)
    # 时间加权收益（TWR）：逐日收益链乘，剔除入金/出金的影响
    twr = 1.0
    for i in range(1, len(equity)):
        prev = equity[i - 1]["adjusted"]
        cur = equity[i]["adjusted"]
        if prev and prev > 0:
            twr *= cur / prev
    twr = round(twr - 1, 6) if equity else None
    adj_points = [{"date": p["date"], "equity": p["adjusted"]} for p in equity]
    dd = _drawdown(adj_points)

    summary_base = {
        "equity_start": raw_start,
        "equity_end": raw_end,
        "account_change": round((raw_end or 0) - raw_start, 2),
        "net_deposit": net_deposit,
        "trading_pnl": trading_pnl,
        "pnl": trading_pnl,
        "return_pct": twr,
        "realized_pnl": round(match["realized_total"], 2),
        "adjusted_start": equity[0]["adjusted"] if equity else None,
        "adjusted_end": equity[-1]["adjusted"] if equity else None,
    }
    summary_base.update(dd)

    # 基准
    bench_start = next((v for d, v in sorted(index_closes.items()) if d <= start), None)
    bench_end = None
    for d, v in sorted(index_closes.items()):
        if d <= end:
            bench_end = v
    if bench_start and bench_end:
        summary_base["benchmark_name"] = "沪深300" if benchmark == "000300" else benchmark
        summary_base["benchmark_return"] = round(bench_end / bench_start - 1, 6)
        summary_base["excess_return"] = round((summary_base["return_pct"] or 0) - summary_base["benchmark_return"], 6)
    else:
        summary_base["benchmark_name"] = None
        summary_base["benchmark_return"] = None
        summary_base["excess_return"] = None

    beh = _behavior(win_rows, match, closes, bars, equity)

    # “什么都不做”的反事实：区间初持仓原样持有到期末
    hold_pnl = 0.0
    hold_cost = 0.0
    for code, st in state0.items():
        q = st["qty"]
        if q <= 0.5:
            continue
        cs = closes.get(code)
        if not cs:
            continue
        px_end = None
        for d in sorted(cs):
            if d <= end:
                px_end = cs[d]
        if px_end:
            hold_pnl += q * (px_end - st["avg"])
            hold_cost += q * st["avg"]
    counterfactual = {"hold_pnl": round(hold_pnl, 2),
                      "hold_return_pct": round(hold_pnl / hold_cost, 6) if hold_cost else None}

    monthly = []
    prev_end = None
    for p in equity:
        m = p["date"].strftime("%Y-%m")
        if monthly and monthly[-1]["month"] == m:
            monthly[-1]["end"] = p["adjusted"]
            monthly[-1]["net_deposit"] += p["flow"]
        else:
            if monthly:
                prev_end = monthly[-1]["end"]
            monthly.append({"month": m, "start": p["adjusted"], "end": p["adjusted"],
                            "prev_end": prev_end, "net_deposit": p["flow"]})
    for m in monthly:
        base = m["prev_end"] if m["prev_end"] else m["start"]
        m["return_pct"] = round(m["end"] / base - 1, 6) if base else None
        m["pnl"] = round(m["end"] - base, 2) if base else None
        m["trades"] = sum(1 for r in win_rows if r["trade_date"].strftime("%Y-%m") == m["month"]
                          and r["side"] in ("buy", "sell"))
        m["start"] = round(m["start"], 2)
        m["end"] = round(m["end"], 2)
        m["net_deposit"] = round(m["net_deposit"], 2)
        m.pop("prev_end", None)

    cfg = get_discipline(db)
    findings = _findings(summary_base, beh, cfg, counterfactual)
    rules = _rules(beh, summary_base, cfg)
    score = _score(summary_base, beh, cfg)
    summary = {**summary_base, **beh, "discipline_score": score["score"],
               "discipline_grade": score["grade"], "discipline_label": score["label"],
               "counterfactual": counterfactual,
               "trades": beh["trades"], "active_days": beh["active_days"]}

    # 个股汇总
    stocks = []
    by_code = defaultdict(lambda: {"buy_count": 0, "sell_count": 0, "buy_amount": 0.0,
                                   "sell_amount": 0.0, "fee": 0.0, "realized": 0.0,
                                   "first_date": None, "last_date": None})
    for r in win_rows:
        s = by_code[r["stock_code"]]
        s["first_date"] = s["first_date"] or r["trade_date"]
        s["last_date"] = r["trade_date"]
        s["fee"] += _fee_of(r)
        if r["side"] == "buy":
            s["buy_count"] += 1
            s["buy_amount"] += r["amount"]
        elif r["side"] == "sell":
            s["sell_count"] += 1
            s["sell_amount"] += r["amount"]
    for ev in match["sell_events"]:
        by_code[ev["code"]]["realized"] += ev["pnl"]
    # 期末持仓（不是期初）：用于个股汇总里的 current_qty
    end_state, _ = replay_positions(db, all_rows, until=None)
    cur_pos = {c: st for c, st in end_state.items() if st["qty"] > 0.5}
    for code, s in by_code.items():
        last_px = None
        cs = closes.get(code, {})
        for d in sorted(cs):
            if d <= end:
                last_px = cs[d]
        qty0 = cur_pos.get(code, {}).get("qty", 0.0)
        stocks.append({
            "stock_code": code,
            "stock_name": names.get(code, code),
            **{k: (round(v, 2) if isinstance(v, float) else (v.isoformat() if isinstance(v, _date) else v))
               for k, v in s.items()},
            "realized": round(s["realized"], 2),
            "current_qty": qty0,
            "last_price": last_px,
        })
    stocks.sort(key=lambda x: x["realized"])

    warnings = []
    if not index_closes:
        warnings.append("基准指数无数据，未计算超额收益")
    if not closes:
        warnings.append("行情库缺少这些标的的日线，账户曲线可能不完整")
    notes = [
        "已实现盈亏按 FIFO 配对；期初持仓按区间开始前收盘价重估（机会成本口径）。",
        "账户曲线 = 交割单现金余额 + 持仓收盘市值，分红/红利税计入现金。",
        "收益/回撤已剔除外部入金与出金（按资金流调整后的净值计算），避免「亏了再加钱」掩盖亏损。",
    ]

    return {
        "ok": True,
        "period": {
            "start": start.isoformat(), "end": end.isoformat(),
            "days": (end - start).days + 1,
            "trades": len([r for r in win_rows if r["side"] in ("buy", "sell")]),
            "active_days": len({r["trade_date"] for r in win_rows}),
            "slip_start": first_date.isoformat(), "slip_end": last_date.isoformat(),
        },
        "summary": summary,
        "equity": [{"date": p["date"].isoformat(), "equity": p["equity"],
                    "adjusted": p["adjusted"], "flow": p["flow"],
                    "benchmark": p["benchmark"], "n_positions": p["n_positions"],
                    "max_weight": p["max_weight"], "cash": p["cash"]} for p in equity],
        "flows": flows,
        "monthly": monthly,
        "behavior": beh,
        "findings": findings,
        "rules": rules,
        "score": score,
        "episodes": {
            "closed": [{k: (v.isoformat() if isinstance(v, _date) else v) for k, v in e.items()}
                       for e in match["episodes"] if e.get("closed")][:200],
            "open": [{k: (v.isoformat() if isinstance(v, _date) else v) for k, v in e.items()}
                     for e in match["episodes"] if not e.get("closed")][:100],
        },
        "stocks": stocks,
        "warnings": warnings,
        "notes": notes,
    }


# ── 今日纪律闸门 ──

def _daily_realized(rows: list[dict], state0: dict) -> dict[_date, float]:
    lots: dict[str, list[list[float]]] = {}
    for code, st in state0.items():
        if st["qty"] > 0.5:
            lots[code] = [[float(st["qty"]), float(st["avg"])]]
    out: dict[_date, float] = defaultdict(float)
    for r in rows:
        code = r["stock_code"]
        q = float(r.get("quantity") or 0)
        if r["side"] == "buy" and q > 0:
            fees = _fee_of(r)
            amt = float(r.get("amount") or 0) or float(r.get("price") or 0) * q
            lots.setdefault(code, []).append([q, (amt + fees) / q])
        elif r["side"] == "sell" and q > 0:
            remain, cost = q, 0.0
            while remain > 1e-9 and lots.get(code):
                lot = lots[code][0]
                take = min(remain, lot[0])
                cost += take * lot[1]
                lot[0] -= take
                remain -= take
                if lot[0] <= 1e-9:
                    lots[code].pop(0)
            px = float(r.get("price") or 0)
            fee = _fee_of(r)
            out[r["trade_date"]] += px * (q - max(remain, 0)) - cost - fee
            if remain > 1e-9:
                st = state0.get(code) or {}
                out[r["trade_date"]] += (px - float(st.get("avg") or px)) * remain
    return out


def today_gate(db, today: Optional[_date] = None) -> dict:
    today = today or _date.today()
    cfg = get_discipline(db)
    rows = load_slip_rows(db, end=today)
    if not rows:
        return {"ok": True, "date": today.isoformat(), "checked": False,
                "message": "尚无交割单数据", "violations": [], "allow_new_buy": True}
    today_rows = [r for r in rows if r["trade_date"] == today]
    lookback = max(30, cfg["no_rebuy_days"] + 10)
    window_start = today - timedelta(days=lookback)
    recent = [r for r in rows if r["trade_date"] >= window_start]
    # 过去 30 天的逐日已实现盈亏：从窗口开始前的持仓状态起算
    state_win, _ = replay_positions(db, rows, until=window_start)
    daily = _daily_realized(recent, state_win)
    prev_days = sorted(d for d in daily if d < today)
    consec = 0
    for d in reversed(prev_days):
        if daily.get(d, 0) < 0:
            consec += 1
        else:
            break
    buys = [r for r in today_rows if r["side"] == "buy"]
    sells = [r for r in today_rows if r["side"] == "sell"]
    same_day = ({r["stock_code"] for r in buys} & {r["stock_code"] for r in sells})
    violations = []
    if len(buys) > cfg["max_new_buys_per_day"]:
        violations.append({
            "rule": "max_new_buys_per_day", "level": "critical",
            "message": f"今日新开仓 {len(buys)} 笔，超过上限 {cfg['max_new_buys_per_day']} 笔",
        })
    if same_day and cfg["no_same_day_roundtrip"]:
        violations.append({
            "rule": "no_same_day_roundtrip", "level": "warn",
            "message": f"今日同一标的又买又卖：{', '.join(sorted(same_day))}",
        })
    sell_dates: dict[str, list[_date]] = defaultdict(list)
    for r in recent:
        if r["side"] == "sell" and r["trade_date"] < today:
            sell_dates[r["stock_code"]].append(r["trade_date"])
    rebuys = []
    for r in buys:
        for sd in sell_dates.get(r["stock_code"], []):
            if 0 < (r["trade_date"] - sd).days <= cfg["no_rebuy_days"]:
                rebuys.append(f"{r['stock_name']}({r['stock_code']})")
                break
    if rebuys:
        violations.append({
            "rule": "no_rebuy_days", "level": "warn",
            "message": f"卖出后 {cfg['no_rebuy_days']} 日内回补：{', '.join(rebuys)}",
        })
    if consec >= cfg["consecutive_loss_trigger"] and buys:
        violations.append({
            "rule": "loss_cooldown", "level": "critical",
            "message": f"已连续 {consec} 个亏损日，今日仍开仓 {len(buys)} 笔；纪律要求只减不加",
        })

    # 当前持仓集中度（用最新收盘）
    from app.trade_slip import build_positions
    preview = build_positions(db)
    positions = preview.get("positions") or []
    last_px = _last_close_map(db, [p["stock_code"] for p in positions])
    mv = {p["stock_code"]: (p["quantity"] * (last_px.get(p["stock_code"]) or p.get("cost_price") or 0))
          for p in positions}
    total = sum(mv.values())
    if total > 0:
        top_code = max(mv, key=mv.get)
        w = mv[top_code] / total
        if w > cfg["max_single_weight"]:
            name = next((p["stock_name"] for p in positions if p["stock_code"] == top_code), top_code)
            violations.append({
                "rule": "max_single_weight", "level": "warn",
                "message": f"当前单票集中：{name} 占 {w*100:.0f}%（上限 {cfg['max_single_weight']*100:.0f}%）",
            })
    if len(positions) > cfg["max_positions"]:
        violations.append({
            "rule": "max_positions", "level": "warn",
            "message": f"当前持仓 {len(positions)} 只，超过上限 {cfg['max_positions']} 只",
        })
    critical = [v for v in violations if v["level"] == "critical"]
    return {
        "ok": True, "date": today.isoformat(), "checked": bool(today_rows),
        "today_buys": len(buys), "today_sells": len(sells),
        "today_buy_amount": round(sum(r["amount"] for r in buys), 2),
        "consecutive_loss_days": consec,
        "violations": violations,
        "allow_new_buy": not critical,
        "summary": (f"今日 {len(buys)} 买 / {len(sells)} 卖，"
                    f"{len(violations)} 项纪律越界" if violations else
                    (f"今日 {len(buys)} 买 / {len(sells)} 卖，纪律正常" if today_rows else "今日暂无成交记录")),
    }
