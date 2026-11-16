"""盘中实时仓位护栏。

背景：交割单只能收盘后导入，等看到数据时当天的冲动已经发生。
本模块用「最后一次交割单 + 今日手动记录/实时行情」做临时口径，提供：
- state：当前仓位/现金/单日买入额度/冷却状态（持仓页实时卡片）
- check：下单前检查（最大可买股数 + 拦截原因 + 买完后的仓位/风险）
- record：把今天已发生的操作记下来，让护栏立刻更新
- cash：修正券商当前可用资金（出入金/未记录交易导致偏差）

口径：
- 最后一次交割单之后的手动记录才参与临时持仓；导入新交割单后自动忽略旧记录。
- 现金优先用当日 trade_cash_snapshot（券商当前可用资金）；没有则用交割单现金 +/- 手动记录。
- 实时价取腾讯快照，失败回退最后收盘价。
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date as _date, datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger
from sqlalchemy import text

from app.trade_review import get_discipline, today_gate
from app.trade_slip import build_positions, load_slip_rows

_CST = timezone(timedelta(hours=8))


def _now() -> datetime:
    return datetime.now(_CST)


def _today() -> _date:
    return _now().date()


def _money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    n = float(v)
    sign = "-" if n < 0 else ""
    a = abs(n)
    if a >= 1e4:
        return f"{sign}¥{a/1e4:.1f}万"
    return f"{sign}¥{a:,.0f}"


def _pct(v: Optional[float], d: int = 1) -> str:
    return "—" if v is None else f"{v*100:.{d}f}%"


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _slip_end(db) -> Optional[_date]:
    return db.execute(text("SELECT MAX(trade_date) FROM trade_slip")).scalar()


def _snapshot(db, day: _date) -> Optional[float]:
    row = db.execute(text(
        "SELECT cash FROM trade_cash_snapshot WHERE trade_date=:d"
    ), {"d": day}).fetchone()
    return float(row[0]) if row else None


def _save_snapshot(db, day: _date, cash: float) -> None:
    db.execute(text("""
        INSERT INTO trade_cash_snapshot (trade_date, cash, updated_at)
        VALUES (:d, :c, CURRENT_TIMESTAMP)
        ON CONFLICT (trade_date) DO UPDATE SET cash=EXCLUDED.cash, updated_at=CURRENT_TIMESTAMP
    """), {"d": day, "c": round(cash, 2)})


def _manual_rows(db, after: Optional[_date], until: Optional[_date] = None) -> list[dict]:
    sql = "SELECT * FROM trade_intraday"
    cond, params = [], {}
    if after:
        cond.append("trade_date > :after")
        params["after"] = after
    if until:
        cond.append("trade_date <= :until")
        params["until"] = until
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY trade_date, trade_time, id"
    out = []
    for r in db.execute(text(sql), params).mappings():
        d = dict(r)
        for k in ("quantity", "price", "amount", "commission", "stamp_tax",
                  "transfer_fee", "extra_fee", "stop_price"):
            d[k] = float(d[k]) if d[k] is not None else None
        out.append(d)
    return out


def _quotes(codes: list[str]) -> tuple[dict[str, dict], bool]:
    codes = sorted({c for c in codes if c})
    if not codes:
        return {}, True
    try:
        from crawler.adapters.tencent_adapter import realtime_quotes
        raw = realtime_quotes(codes, "stock")
        return {q.get("code"): q for q in raw.values() if q.get("code")}, True
    except Exception as e:
        logger.warning(f"[intraday] 实时快照不可用: {str(e)[:120]}")
        return {}, False


def _stock_name(db, code: str) -> str:
    # 同一代码可能在 stock_master 里既有股票行又有指数行（如 000001），优先股票
    row = db.execute(text("""
        SELECT stock_name FROM stock_master WHERE stock_code=:c
        ORDER BY (stock_type='stock') DESC, id LIMIT 1
    """), {"c": code}).fetchone()
    return row[0] if row else code


def _last_close(db, code: str) -> float:
    row = db.execute(text("""
        SELECT close FROM daily_quote
        WHERE stock_code=:c AND close > 0
        ORDER BY trade_date DESC LIMIT 1
    """), {"c": code}).fetchone()
    return float(row[0]) if row else 0.0


def _quote_price(db, code: str) -> tuple[float, str]:
    qmap, _ = _quotes([code])
    q = qmap.get(code) or {}
    px = _f(q.get("price"))
    return (px or _last_close(db, code), q.get("name") or _stock_name(db, code))


def _apply_manual(pos: dict[str, dict], cash: float, row: dict, apply_cash: bool) -> float:
    code = row["stock_code"]
    q = _f(row["quantity"])
    px = _f(row["price"])
    amt = _f(row["amount"]) or q * px
    fees = sum(_f(row.get(k)) for k in ("commission", "stamp_tax", "transfer_fee", "extra_fee"))
    p = pos.setdefault(code, {"qty": 0.0, "cost": 0.0, "name": row.get("stock_name") or code,
                              "price": px})
    p["name"] = row.get("stock_name") or p["name"]
    p["price"] = px or p["price"]
    if row["side"] == "buy":
        new_qty = p["qty"] + q
        if new_qty > 0:
            p["cost"] = (p["cost"] * p["qty"] + amt + fees) / new_qty
        p["qty"] = new_qty
        if apply_cash:
            cash -= amt + fees
    elif row["side"] == "sell":
        p["qty"] = max(0.0, p["qty"] - q)
        if apply_cash:
            cash += amt - fees
        if p["qty"] <= 0.5:
            pos.pop(code, None)
    return cash


def _today_actions(db, slip_end: Optional[_date], today: _date) -> list[dict]:
    """当天已发生的操作：交割单覆盖今天时用交割单，否则用临时记录。"""
    if slip_end and slip_end >= today:
        rows = load_slip_rows(db, start=today, end=today)
        return [{
            "id": None, "source": "slip", "trade_time": r.get("trade_time") or "",
            "side": r["side"], "stock_code": r["stock_code"], "stock_name": r["stock_name"],
            "quantity": _f(r["quantity"]), "price": _f(r["price"]), "amount": _f(r["amount"]),
            "reason": "", "stop_price": None,
        } for r in rows]
    rows = db.execute(text(
        "SELECT * FROM trade_intraday WHERE trade_date=:d ORDER BY trade_time, id"
    ), {"d": today}).mappings().fetchall()
    return [{
        "id": r["id"], "source": "manual", "trade_time": r["trade_time"] or "",
        "side": r["side"], "stock_code": r["stock_code"], "stock_name": r["stock_name"],
        "quantity": _f(r["quantity"]), "price": _f(r["price"]), "amount": _f(r["amount"]),
        "reason": r["reason"] or "", "stop_price": _f(r["stop_price"]) or None,
    } for r in rows]


def _build_state(db, cash_override: Optional[float] = None) -> dict:
    today = _today()
    now = _now()
    base = build_positions(db)
    slip_end = _slip_end(db)
    rules = get_discipline(db)

    pos: dict[str, dict] = {}
    for p in base.get("positions") or []:
        pos[p["stock_code"]] = {
            "qty": _f(p["quantity"]),
            "cost": _f(p["cost_price"]),
            "name": p["stock_name"],
            "price": _f(p.get("last_price")) or _f(p["cost_price"]),
            "estimated": bool(p.get("cost_estimated")),
        }

    snap = _snapshot(db, today)
    if cash_override is not None:
        snap = _f(cash_override)
    use_snapshot = snap is not None
    cash = _f(snap if use_snapshot else (base.get("summary") or {}).get("cash") or 0.0)
    cash_source = "snapshot" if use_snapshot else ("slip+manual" if slip_end else "manual")

    manual = _manual_rows(db, slip_end)
    for r in manual:
        cash = _apply_manual(pos, cash, r, apply_cash=not use_snapshot)

    codes = list(pos.keys())
    qmap, quote_ok = _quotes(codes)
    for code, p in pos.items():
        q = qmap.get(code) or {}
        if _f(q.get("price")) > 0:
            p["price"] = _f(q.get("price"))
        if q.get("name"):
            p["name"] = q["name"]

    positions = []
    mv = 0.0
    for code, p in pos.items():
        qty = p["qty"]
        if qty <= 0.5:
            continue
        price = p["price"] or p["cost"]
        value = qty * price
        mv += value
        positions.append({
            "stock_code": code, "stock_name": p["name"], "quantity": int(qty) if abs(qty - round(qty)) < 1e-6 else round(qty, 2),
            "cost_price": round(p["cost"], 4), "price": round(price, 4),
            "market_value": round(value, 2),
            "pnl": round((price - p["cost"]) * qty, 2),
            "pnl_pct": round(price / p["cost"] - 1, 6) if p["cost"] else None,
            "cost_estimated": p.get("estimated", False),
        })
    positions.sort(key=lambda x: -x["market_value"])
    equity = cash + mv
    position_ratio = mv / equity if equity > 0 else 0.0
    cash_ratio = cash / equity if equity > 0 else 0.0
    top = positions[0] if positions else None
    top_weight = (top["market_value"] / mv) if top and mv > 0 else 0.0

    # 今日操作 / 额度
    today_actions = _today_actions(db, slip_end, today)
    daily_buys = [a for a in today_actions if a["side"] == "buy"]
    daily_sells = [a for a in today_actions if a["side"] == "sell"]
    daily_buy_amount = sum(a["amount"] for a in daily_buys)
    max_new_buys = int(rules.get("max_new_buys_per_day") or 2)
    remaining_new_buys = max(0, max_new_buys - len(daily_buys))
    max_daily_buy = _f(rules.get("max_daily_buy_pct"), 0.30) * equity
    daily_buy_budget = max(0.0, max_daily_buy - daily_buy_amount)
    target = _f(rules.get("target_position_ratio"), 0.70)
    hard = _f(rules.get("max_position_ratio"), 0.90)
    cash_floor = _f(rules.get("cash_floor"), 0.30)
    max_buy_by_cash = max(0.0, cash - cash_floor * equity)
    max_buy_by_target = max(0.0, target * equity - mv)
    max_buy_amount = min(max_buy_by_cash, max_buy_by_target, daily_buy_budget)

    try:
        gate = today_gate(db)
        consec = int(gate.get("consecutive_loss_days") or 0)
    except Exception:
        consec = 0

    violations = []
    level = "ok"
    if position_ratio >= hard:
        level = "critical"
        violations.append({"rule": "max_position_ratio", "level": "critical",
                           "message": f"仓位 {_pct(position_ratio)} ≥ 硬上限 {_pct(hard)}，必须先减仓"})
    elif position_ratio > target:
        level = "warn"
        violations.append({"rule": "target_position_ratio", "level": "warn",
                           "message": f"仓位 {_pct(position_ratio)} 高于目标 {_pct(target)}，新开仓前先减仓"})
    if cash_ratio < cash_floor:
        level = "critical" if cash_ratio < cash_floor - 0.05 else "warn"
        violations.append({"rule": "cash_floor", "level": "warn",
                           "message": f"现金 {_pct(cash_ratio)} 低于底线 {_pct(cash_floor)}"})
    if remaining_new_buys <= 0:
        level = "critical"
        violations.append({"rule": "max_new_buys_per_day", "level": "critical",
                           "message": f"今日新开仓已用完（{max_new_buys}/{max_new_buys}）"})
    if daily_buy_budget <= 0:
        level = "warn"
        violations.append({"rule": "max_daily_buy_pct", "level": "warn",
                           "message": f"今日买入额度已用完（{_money(max_daily_buy)}）"})
    if consec >= int(rules.get("consecutive_loss_trigger") or 2):
        level = "critical"
        violations.append({"rule": "loss_cooldown", "level": "critical",
                           "message": f"连续亏损 {consec} 天，今日只减不加"})
    max_single_w = _f(rules.get("max_single_weight"), 0.20)
    if top and top_weight > max_single_w:
        if level == "ok":
            level = "warn"
        violations.append({"rule": "max_single_weight", "level": "warn",
                           "message": f"最大单票 {top['stock_name']} 占 {_pct(top_weight)}（上限 {_pct(max_single_w)}），只减不加"})

    allow_new_buy = (level != "critical" and position_ratio < target
                     and remaining_new_buys > 0 and daily_buy_budget > 0
                     and cash_ratio >= cash_floor)

    if position_ratio >= hard:
        need = max(0.0, mv - target * equity)
        advice = (f"仓位 {_pct(position_ratio)} 已达硬上限 {_pct(hard)}：今天禁止开新仓。"
                  f"先减仓到 {_pct(target)} 以下（约需卖出 {_money(need)}）再恢复买入。")
    elif position_ratio > target:
        need = max(0.0, mv - target * equity)
        advice = (f"仓位 {_pct(position_ratio)} 高于目标 {_pct(target)}：先减仓约 {_money(need)} 到 "
                  f"{_pct(target)} 以下，再考虑新开仓。")
    elif consec >= int(rules.get("consecutive_loss_trigger") or 2):
        advice = f"已连续 {consec} 个亏损日：今天只减不加，禁止开新仓。"
    elif remaining_new_buys <= 0:
        advice = f"今日新开仓已用完（{max_new_buys}/{max_new_buys}）：今天不再买。"
    elif daily_buy_budget <= 0:
        advice = f"今日买入额度已用完（{_money(max_daily_buy)}）：今天不再买。"
    elif cash_ratio < cash_floor:
        advice = f"现金 {_pct(cash_ratio)} 低于底线 {_pct(cash_floor)}：先回笼现金，不要开新仓。"
    else:
        advice = (f"可开仓：今日还剩 {remaining_new_buys} 笔，最多再买 {_money(max_buy_amount)}；"
                  f"买完仓位不超过 {_pct(target)}。")
        if top and top_weight > max_single_w:
            advice += f" 最大单票 {top['stock_name']} 占 {_pct(top_weight)} 超上限，只减不加。"

    return {
        "asof": now.strftime("%H:%M:%S"),
        "trade_date": today.isoformat(),
        "slip_end": slip_end.isoformat() if slip_end else None,
        "cash_source": cash_source,
        "quote_ok": quote_ok,
        "cash": round(cash, 2),
        "market_value": round(mv, 2),
        "equity": round(equity, 2),
        "position_ratio": round(position_ratio, 6),
        "cash_ratio": round(cash_ratio, 6),
        "target_position_ratio": target,
        "max_position_ratio": hard,
        "cash_floor": cash_floor,
        "max_buy_amount": round(max_buy_amount, 2),
        "daily_buy_amount": round(daily_buy_amount, 2),
        "daily_buy_budget": round(daily_buy_budget, 2),
        "max_daily_buy": round(max_daily_buy, 2),
        "max_new_buys_per_day": max_new_buys,
        "remaining_new_buys": remaining_new_buys,
        "daily_buy_count": len(daily_buys),
        "daily_sell_count": len(daily_sells),
        "consecutive_loss_days": consec,
        "level": level,
        "allow_new_buy": allow_new_buy,
        "advice": advice,
        "violations": violations,
        "positions": positions,
        "top_position": ({"stock_code": top["stock_code"], "stock_name": top["stock_name"],
                          "weight": round(top_weight, 6), "market_value": top["market_value"]}
                         if top else None),
        "top_weight": round(top_weight, 6),
        "today_actions": today_actions,
        "rules": {
            "max_single_weight": _f(rules.get("max_single_weight"), 0.20),
            "max_positions": int(rules.get("max_positions") or 6),
            "stop_loss_pct": _f(rules.get("stop_loss_pct"), -0.08),
            "risk_per_trade": _f(rules.get("risk_per_trade"), 0.02),
            "no_rebuy_days": int(rules.get("no_rebuy_days") or 5),
            "consecutive_loss_trigger": int(rules.get("consecutive_loss_trigger") or 2),
            "target_position_ratio": target,
            "max_position_ratio": hard,
            "cash_floor": cash_floor,
            "max_daily_buy_pct": _f(rules.get("max_daily_buy_pct"), 0.30),
        },
    }


def state(db) -> dict:
    return _build_state(db)


def quote(db, code: str) -> dict:
    code = str(code or "").strip().zfill(6)
    price, name = _quote_price(db, code)
    return {"stock_code": code, "stock_name": name, "price": round(price, 4)}


def _estimate_fees(side: str, amount: float) -> dict:
    commission = max(5.0, amount * 0.00025)
    stamp = amount * 0.0005 if side == "sell" else 0.0
    transfer = amount * 0.00001
    return {
        "commission": round(commission, 2),
        "stamp_tax": round(stamp, 2),
        "transfer_fee": round(transfer, 2),
        "extra_fee": 0.0,
        "total": round(commission + stamp + transfer, 2),
    }


def _recent_sell(db, code: str, today: _date, days: int) -> Optional[str]:
    start = today - timedelta(days=max(1, days))
    row = db.execute(text("""
        SELECT trade_date FROM trade_slip
        WHERE stock_code=:c AND side='sell' AND trade_date >= :s AND trade_date < :t
        ORDER BY trade_date DESC LIMIT 1
    """), {"c": code, "s": start, "t": today}).fetchone()
    if row:
        return row[0].isoformat()
    row = db.execute(text("""
        SELECT trade_date FROM trade_intraday
        WHERE stock_code=:c AND side='sell' AND trade_date >= :s AND trade_date < :t
        ORDER BY trade_date DESC LIMIT 1
    """), {"c": code, "s": start, "t": today}).fetchone()
    return row[0].isoformat() if row else None


def _max_buy_qty(st: dict, existing: Optional[dict], price: float, stop: float) -> int:
    r = st["rules"]
    equity = st["equity"]
    cash = st["cash"]
    mv = st["market_value"]
    if price <= 0:
        return 0
    existing_mv = existing["market_value"] if existing else 0.0
    caps = []
    if stop > 0 and price > stop:
        caps.append((r["risk_per_trade"] * equity) / (price - stop))
    caps.append(max(0.0, r["max_single_weight"] * equity - existing_mv) / price)
    caps.append(max(0.0, r["target_position_ratio"] * equity - mv) / price)
    caps.append(max(0.0, cash - r["cash_floor"] * equity) / (price * 1.0005))
    caps.append(max(0.0, st["daily_buy_budget"]) / (price * 1.0005))
    if not existing and len(st["positions"]) >= r["max_positions"]:
        caps.append(0)
    qty = int(min(caps) // 100 * 100) if caps else 0
    return max(0, qty)


def check(db, body: dict) -> dict:
    st = _build_state(db, cash_override=body.get("cash"))
    code = str(body.get("stock_code") or "").strip().zfill(6)
    side = str(body.get("side") or "buy").lower()
    qty = _f(body.get("quantity"))
    price = _f(body.get("price"))
    stop = _f(body.get("stop_price"))
    name = _stock_name(db, code)
    if price <= 0:
        price, qname = _quote_price(db, code)
        name = qname or name
    existing = next((p for p in st["positions"] if p["stock_code"] == code), None)
    if existing:
        name = existing["stock_name"]
    r = st["rules"]
    violations: list[dict] = []

    def add(rule, level, message):
        violations.append({"rule": rule, "level": level, "message": message})

    if not code or len(code) != 6:
        add("code", "block", "请填写 6 位证券代码")
    if qty <= 0:
        add("quantity", "block", "数量必须大于 0")
    if price <= 0:
        add("price", "block", "无法获取价格，请手动填写成交价")

    if side == "buy":
        if stop <= 0:
            add("stop_loss", "block", "买入必须填写止损价（建议 -8%）")
        elif stop >= price:
            add("stop_loss", "block", "止损价必须低于买入价")
        max_qty = _max_buy_qty(st, existing, price, stop)
        amount = price * qty
        fees = _estimate_fees("buy", amount)
        new_cash = st["cash"] - amount - fees["total"]
        existing_mv = existing["market_value"] if existing else 0.0
        new_mv = existing_mv + amount
        new_equity = max(1e-9, st["equity"] - fees["total"])
        new_mv_total = st["market_value"] - existing_mv + new_mv
        new_position_ratio = new_mv_total / new_equity
        new_cash_ratio = new_cash / new_equity
        single_weight = new_mv / new_equity
        risk_amount = max(0.0, price - stop) * qty
        risk_pct = risk_amount / st["equity"] if st["equity"] > 0 else 0.0

        if st["position_ratio"] >= r["max_position_ratio"]:
            add("max_position_ratio", "block",
                f"当前仓位 {_pct(st['position_ratio'])} 已到硬上限 {_pct(r['max_position_ratio'])}，先减仓")
        elif st["position_ratio"] >= r["target_position_ratio"]:
            add("target_position_ratio", "block",
                f"当前仓位 {_pct(st['position_ratio'])} 高于目标 {_pct(r['target_position_ratio'])}，先减仓再买")
        if st["cash_ratio"] < r["cash_floor"]:
            add("cash_floor", "block",
                f"现金 {_pct(st['cash_ratio'])} 低于底线 {_pct(r['cash_floor'])}")
        if st["remaining_new_buys"] <= 0:
            add("max_new_buys_per_day", "block",
                f"今日新开仓已用完（{st['max_new_buys_per_day']}/{st['max_new_buys_per_day']}）")
        if st["daily_buy_budget"] <= 0:
            add("max_daily_buy_pct", "block",
                f"今日买入额度已用完（{_money(st['max_daily_buy'])}）")
        if st["consecutive_loss_days"] >= r["consecutive_loss_trigger"]:
            add("loss_cooldown", "block",
                f"连续亏损 {st['consecutive_loss_days']} 天，今天只减不加")
        if existing and existing["cost_price"] > 0 and existing["price"] < existing["cost_price"] * (1 + r["stop_loss_pct"]):
            add("add_to_loser", "block",
                f"{name} 已浮亏 {_pct(existing['price']/existing['cost_price']-1)}（超过 {_pct(abs(r['stop_loss_pct']))} 止损线），禁止加仓")
        same_day_sell = any(a["stock_code"] == code and a["side"] == "sell" for a in st["today_actions"])
        if same_day_sell:
            add("same_day_roundtrip", "block", f"今天已经卖过 {name}，禁止当日回补")
        rs = _recent_sell(db, code, _today(), r["no_rebuy_days"])
        if rs:
            add("no_rebuy_days", "block", f"{name} 在 {rs} 卖出过，{r['no_rebuy_days']} 天内禁止回补")
        if qty > max_qty:
            add("max_qty", "block", f"超过上限：按仓位/现金/风险预算最多买 {max_qty} 股")
        if new_cash < r["cash_floor"] * new_equity:
            add("cash_floor", "block",
                f"买完现金只剩 {_money(new_cash)}（{_pct(new_cash_ratio)}），低于底线 {_pct(r['cash_floor'])}")
        if single_weight > r["max_single_weight"] + 1e-9:
            add("max_single_weight", "block",
                f"买完单票占 {_pct(single_weight)}，超过上限 {_pct(r['max_single_weight'])}")
        if new_position_ratio > r["target_position_ratio"] + 1e-9:
            add("target_position_ratio", "block",
                f"买完仓位 {_pct(new_position_ratio)}，超过目标 {_pct(r['target_position_ratio'])}")
        if risk_pct > r["risk_per_trade"] + 1e-9:
            add("risk_per_trade", "block",
                f"这笔止损亏损 {_money(risk_amount)}（账户 {_pct(risk_pct)}），超过单笔 {_pct(r['risk_per_trade'])} 预算")

        blocked = any(v["level"] == "block" for v in violations)
        if blocked:
            first = next(v["message"] for v in violations if v["level"] == "block")
            advice = f"先别买：{first}。" + (f" 若规则允许，最多 {max_qty} 股。" if max_qty > 0 else "")
        else:
            advice = (f"可以买，最多 {max_qty} 股；买完仓位 {_pct(new_position_ratio)}、"
                      f"现金 {_pct(new_cash_ratio)}、单票 {_pct(single_weight)}；"
                      f"止损亏 {_money(risk_amount)}（账户 {_pct(risk_pct)}）。")
        projected = {
            "position_ratio": round(new_position_ratio, 6),
            "cash_ratio": round(new_cash_ratio, 6),
            "single_weight": round(single_weight, 6),
            "risk_amount": round(risk_amount, 2),
            "risk_pct": round(risk_pct, 6),
            "cash": round(new_cash, 2),
            "amount": round(amount, 2),
            "fees": fees["total"],
        }
        return {"allow": not blocked, "level": "ok" if not blocked else "block",
                "side": "buy", "stock_code": code, "stock_name": name,
                "price": round(price, 4), "quantity": qty, "max_qty": max_qty,
                "projected": projected, "violations": violations, "advice": advice,
                "state": _state_summary(st)}

    # sell：不设仓位护栏，只做 T+1 校验
    if not existing:
        add("position", "block", f"当前没有 {name} 持仓")
        max_qty = 0
    else:
        today_buy_qty = sum(a["quantity"] for a in st["today_actions"]
                            if a["stock_code"] == code and a["side"] == "buy")
        max_qty = max(0, int(existing["quantity"] - today_buy_qty))
        if qty > max_qty:
            add("t_plus_1", "block",
                f"今天买入的部分 T+1 才能卖；当前可卖 {max_qty} 股（今日买入 {today_buy_qty:g} 股）")
    blocked = any(v["level"] == "block" for v in violations)
    amount = price * qty
    fees = _estimate_fees("sell", amount)
    new_cash = st["cash"] + amount - fees["total"]
    return {
        "allow": not blocked, "level": "ok" if not blocked else "block",
        "side": "sell", "stock_code": code, "stock_name": name,
        "price": round(price, 4), "quantity": qty, "max_qty": max_qty,
        "projected": {"cash": round(new_cash, 2), "amount": round(amount, 2),
                      "fees": fees["total"]},
        "violations": violations,
        "advice": ("卖出不受仓位护栏限制；如果这是止损，执行它。" if not blocked
                   else next(v["message"] for v in violations if v["level"] == "block")),
        "state": _state_summary(st),
    }


def _state_summary(st: dict) -> dict:
    keys = ("asof", "trade_date", "slip_end", "cash_source", "quote_ok", "cash", "market_value",
            "equity", "position_ratio", "cash_ratio", "target_position_ratio",
            "max_position_ratio", "cash_floor", "max_buy_amount", "daily_buy_budget",
            "remaining_new_buys", "daily_buy_count", "consecutive_loss_days", "level",
            "allow_new_buy", "advice")
    return {k: st.get(k) for k in keys}


def record(db, body: dict) -> dict:
    """记录今天已发生的操作。即使检查会拦截也照实记录（市场不会因为规则重来）。"""
    code = str(body.get("stock_code") or "").strip().zfill(6)
    side = str(body.get("side") or "buy").lower()
    qty = _f(body.get("quantity"))
    price = _f(body.get("price"))
    if price <= 0:
        price, _ = _quote_price(db, code)
    if qty <= 0 or price <= 0:
        raise ValueError("数量和价格必须大于 0")
    amount = round(price * qty, 2)
    fees = _estimate_fees(side, amount)
    chk = check(db, {**body, "stock_code": code, "side": side, "quantity": qty, "price": price})
    now = _now()
    db.execute(text("""
        INSERT INTO trade_intraday
            (trade_date, trade_time, stock_code, stock_name, side, quantity, price, amount,
             commission, stamp_tax, transfer_fee, extra_fee, stop_price, reason, source)
        VALUES (:d, :t, :c, :n, :s, :q, :p, :a, :com, :st, :tr, :ex, :stop, :r, 'manual')
    """), {
        "d": now.date(), "t": now.strftime("%H:%M:%S"), "c": code,
        "n": chk.get("stock_name") or _stock_name(db, code), "s": side, "q": qty, "p": price,
        "a": amount, "com": fees["commission"], "st": fees["stamp_tax"],
        "tr": fees["transfer_fee"], "ex": fees["extra_fee"],
        "stop": _f(body.get("stop_price")) or None, "r": str(body.get("reason") or "")[:200],
    })
    # 如果用户已经修正过当前现金，记录操作后同步扣/加现金
    snap = _snapshot(db, now.date())
    if snap is not None:
        if side == "buy":
            snap = snap - amount - fees["total"]
        else:
            snap = snap + amount - fees["total"]
        _save_snapshot(db, now.date(), snap)
    db.commit()
    logger.info(f"[intraday] 记录 {side} {code} {qty:g}@{price} allow={chk['allow']}")
    return {"ok": True, "check": chk, "state": _build_state(db)}


def set_cash(db, cash: float) -> dict:
    today = _today()
    _save_snapshot(db, today, _f(cash))
    db.commit()
    return _build_state(db)


def delete_action(db, action_id: int) -> dict:
    row = db.execute(text("SELECT * FROM trade_intraday WHERE id=:id"), {"id": action_id}).mappings().fetchone()
    if not row:
        raise ValueError("记录不存在")
    amount = _f(row["amount"])
    fees = sum(_f(row[k]) for k in ("commission", "stamp_tax", "transfer_fee", "extra_fee"))
    snap = _snapshot(db, row["trade_date"])
    if snap is not None:
        if row["side"] == "buy":
            snap = snap + amount + fees
        else:
            snap = snap - amount + fees
        _save_snapshot(db, row["trade_date"], snap)
    db.execute(text("DELETE FROM trade_intraday WHERE id=:id"), {"id": action_id})
    db.commit()
    return _build_state(db)


def reconcile(db) -> int:
    """导入交割单后，把已覆盖日期的临时记录标记为已对账（不再参与临时持仓）。"""
    n = db.execute(text("""
        UPDATE trade_intraday SET reconciled=true
        WHERE reconciled=false
          AND trade_date <= (SELECT COALESCE(MAX(trade_date), '1900-01-01') FROM trade_slip)
    """)).rowcount or 0
    db.commit()
    return n
