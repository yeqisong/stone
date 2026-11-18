"""集中度减仓计算器：同时满足目标仓位与单票上限，按 100 股整手。

纯函数，便于单测。输入当前持仓/现金，输出每只票建议卖出股数、卖后仓位与单票占比。
不含手续费、印花税，也不处理 T+1（今天买入部分不可卖，由调用方提示）。
"""
from __future__ import annotations

from math import ceil


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _metrics(pos: list[dict], cash: float) -> dict:
    mv = sum(p["mv"] for p in pos)
    equity = cash + mv
    weights = [(p, p["mv"] / equity) for p in pos if equity > 0 and p["mv"] > 0]
    top = max(weights, key=lambda x: x[1], default=(None, 0.0))
    return {
        "market_value": round(mv, 2), "cash": round(cash, 2),
        "equity": round(equity, 2),
        "position_ratio": round(mv / equity, 6) if equity > 0 else 0.0,
        "cash_ratio": round(cash / equity, 6) if equity > 0 else 0.0,
        "top_code": top[0]["code"] if top[0] else None,
        "top_name": top[0]["name"] if top[0] else None,
        "top_weight": round(top[1], 6),
    }


def build_reduction_plan(positions: list[dict], cash: float,
                         target: float = 0.70, max_single: float = 0.20) -> dict:
    target = max(0.0, min(1.0, _f(target) or 0.70))
    max_single = max(0.0, min(1.0, _f(max_single) or 0.20))
    pos = []
    for p in positions or []:
        qty = _f(p.get("qty", p.get("quantity")))
        price = _f(p.get("price"))
        if qty <= 0 or price <= 0:
            continue
        pos.append({
            "code": p.get("code") or p.get("stock_code"),
            "name": p.get("name") or p.get("stock_name") or p.get("code"),
            "qty": qty, "price": price, "mv": qty * price,
        })
    cash = max(0.0, _f(cash))
    equity = cash + sum(p["mv"] for p in pos)
    before = _metrics(pos, cash)
    original = {p["code"]: dict(p) for p in pos}
    sells: dict[str, float] = {}
    single_driven = False
    for _ in range(50):
        if equity <= 0:
            break
        mv = sum(p["mv"] for p in pos)
        pos_ratio = mv / equity
        weights = {p["code"]: p["mv"] / equity for p in pos if p["mv"] > 0}
        over = [(p, weights[p["code"]] - max_single) for p in pos
                if weights.get(p["code"], 0) > max_single + 1e-9]
        if pos_ratio <= target + 1e-9 and not over:
            break
        if over:
            p, _ = max(over, key=lambda x: x[1])
            need = max(0.0, (weights[p["code"]] - max_single) * equity)
            single_driven = True
        else:
            p = max((x for x in pos if x["mv"] > 0), key=lambda x: x["mv"])
            need = max(0.0, mv - target * equity)
        if need <= 0 or p["price"] <= 0:
            break
        # A 股 100 股整手；向上取整到整手，宁可多卖 <100 股也要真正落到上限内
        qty = min(p["qty"], ceil(need / p["price"] / 100) * 100)
        if qty <= 0:
            break
        amount = qty * p["price"]
        p["qty"] -= qty
        p["mv"] -= amount
        cash += amount
        sells[p["code"]] = sells.get(p["code"], 0.0) + qty
        pos = [x for x in pos if x["qty"] > 0.5]

    after = _metrics(pos, cash)
    rows = []
    for code, qty in sells.items():
        o = original.get(code)
        if not o:
            continue
        left = max(0.0, o["qty"] - qty)
        after_p = next((x for x in pos if x["code"] == code), None)
        rows.append({
            "code": code, "name": o["name"], "price": round(o["price"], 4),
            "qty_before": int(o["qty"]) if abs(o["qty"] - round(o["qty"])) < 1e-6 else round(o["qty"], 2),
            "sell_qty": int(qty) if abs(qty - round(qty)) < 1e-6 else round(qty, 2),
            "sell_amount": round(qty * o["price"], 2),
            "qty_after": int(left) if abs(left - round(left)) < 1e-6 else round(left, 2),
            "after_weight": round((after_p["mv"] / equity) if after_p and equity > 0 else 0.0, 6),
        })
    rows.sort(key=lambda x: -x["sell_amount"])
    need = round(sum(r["sell_amount"] for r in rows), 2)
    binding = ("single" if single_driven and before["top_weight"] > max_single
               else "position" if before["position_ratio"] > target else "none")
    return {
        "ok": True, "target": target, "max_single": max_single,
        "equity": round(equity, 2), "before": before, "after": after,
        "rows": rows, "need": need, "binding": binding,
        "note": "按100股整手估算、不含费用；今天买入的部分 T+1 不可卖。",
    }
