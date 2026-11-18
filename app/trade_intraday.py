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

import json
import math
import time
from collections import defaultdict
from datetime import date as _date, datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger
from sqlalchemy import text

from app.trade_review import build_review, get_discipline, today_gate
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
    if a >= 1e8:
        return f"{sign}¥{a/1e8:.2f}亿"
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
        ORDER BY CASE stock_type WHEN 'stock' THEN 1 WHEN 'etf' THEN 2 ELSE 3 END
        LIMIT 1
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
            "reason": "", "stop_price": None, "hard_block": False,
            "override_reason": "", "emotion": "",
        } for r in rows]
    rows = db.execute(text(
        "SELECT * FROM trade_intraday WHERE trade_date=:d ORDER BY trade_time, id"
    ), {"d": today}).mappings().fetchall()
    return [{
        "id": r["id"], "source": "manual", "trade_time": r["trade_time"] or "",
        "side": r["side"], "stock_code": r["stock_code"], "stock_name": r["stock_name"],
        "quantity": _f(r["quantity"]), "price": _f(r["price"]), "amount": _f(r["amount"]),
        "reason": r["reason"] or "", "stop_price": _f(r["stop_price"]) or None,
        "hard_block": bool(r.get("hard_block")),
        "override_reason": r.get("override_reason") or "",
        "emotion": r.get("emotion") or "",
    } for r in rows]


# ── 交易画像 / 针对性教练 ──
# 从交割单重放里识别用户的高风险模式；缓存 10 分钟，避免每次 20s 轮询都重算。

_PROFILE_CACHE: dict = {"key": None, "ts": 0.0, "data": None}


def _avg_position(r: dict) -> tuple[float, int, int]:
    eq = r.get("equity") or []
    ratios = []
    for p in eq:
        e = _f(p.get("equity"))
        c = _f(p.get("cash"))
        if e > 0:
            ratios.append(max(0.0, min(1.0, (e - c) / e)))
    if not ratios:
        return 0.0, 0, 0
    return sum(ratios) / len(ratios), sum(1 for x in ratios if x >= 0.9), len(ratios)


def _profile_from_review(r: dict) -> dict:
    if not r.get("ok"):
        return {"types": [], "metrics": {}}
    s, b = r.get("summary") or {}, r.get("behavior") or {}
    avg_pos, full_days, n_days = _avg_position(r)
    types: list[dict] = []

    def add(key, label, severity, evidence, rule):
        types.append({"key": key, "label": label, "severity": severity,
                      "evidence": evidence, "rule": rule})

    add_loss = _f(b.get("add_loss_amount"))
    add_win = _f(b.get("add_win_amount"))
    if add_loss > max(add_win * 1.2, 50000) and int(b.get("add_loss_count") or 0) >= 10:
        add("loss_add", "亏损加仓型", 100,
            f"亏损时加仓 {b.get('add_loss_count')} 笔 / {_money(add_loss)}，"
            f"盈利时 {b.get('add_win_count')} 笔 / {_money(add_win)}",
            "浮亏超过 -8% 禁止加仓；加仓只在盈利且收盘站上 20 日线时，单次 ≤ 原仓位 50%")
    if avg_pos >= 0.80:
        add("full_position", "长期满仓型", 98,
            f"平均仓位 {_pct(avg_pos)}，{full_days}/{n_days} 个交易日仓位 ≥90%",
            "账户常态仓位 ≤70%、现金 ≥30%；仓位 >90% 只减不加")
    pl, pw = b.get("post_loss_next_day") or {}, b.get("post_win_next_day") or {}
    if _f(pl.get("avg_buy_amount")) > _f(pw.get("avg_buy_amount")) * 1.2 > 0:
        add("revenge", "报复交易型", 95,
            f"亏损日次日日均 {pl.get('avg_trades')} 笔 / 买入 {_money(pl.get('avg_buy_amount'))}；"
            f"盈利日次日 {pw.get('avg_trades')} 笔 / {_money(pw.get('avg_buy_amount'))}",
            "连续 2 个亏损日 → 次日只减不加；单日 -3% 停手")
    if (_f(b.get("trades_per_day")) > 3 or int(b.get("same_day_roundtrips") or 0) >= 10
            or int(b.get("rebuy_within_7d") or 0) >= 20):
        add("overtrade", "高频交易型", 92,
            f"{b.get('trades')} 笔 / {b.get('active_days')} 天，日均 {b.get('trades_per_day')} 笔；"
            f"同日回转 {b.get('same_day_roundtrips')} 天；7 日回补 {b.get('rebuy_within_7d')} 次",
            "每日新开仓 ≤2；同日不回转；卖出后 5 个交易日内不回补")
    if _f(b.get("max_single_weight")) > 0.35:
        add("concentration", "单票集中型", 90,
            f"最大单票 {_pct(b.get('max_single_weight'))}（{b.get('max_weight_date')}）",
            "单票 ≤20%，单一行业 ≤35%；超过只减不加")
    if _f(s.get("max_drawdown")) > 0.25:
        add("drawdown", "回撤失控型", 88,
            f"最大回撤 {_pct(s.get('max_drawdown'))}（{s.get('peak_date')} → {s.get('trough_date')}）",
            "账户回撤 -15% 降半仓，-20% 停手 5 个交易日")
    if _f(b.get("disposition_ratio")) > 1.25:
        add("disposition", "处置效应型", 80,
            f"盈利单平均持有 {b.get('hold_days_win')} 天，亏损单 {b.get('hold_days_loss')} 天",
            "入场先写止损；亏损单 5 个交易日未回成本减半；盈利单按 20 日线持有")
    net_dep = _f(s.get("net_deposit"))
    eq_start = _f(s.get("equity_start"))
    if net_dep > 0 and eq_start > 0 and net_dep > eq_start * 0.05:
        add("deposit", "入金翻本型", 75,
            f"期间净入金 {_money(net_dep)}；剔除入金后收益 {_pct(s.get('return_pct'))}",
            "入金与交易成绩分开记账；复盘只看剔除入金后的净值；亏损后不要加钱翻本")

    types.sort(key=lambda x: -x["severity"])
    return {
        "types": types,
        "metrics": {
            "avg_position": round(avg_pos, 4),
            "full_days": full_days, "days": n_days,
            "max_drawdown": s.get("max_drawdown"),
            "net_deposit": s.get("net_deposit"),
            "trades_per_day": b.get("trades_per_day"),
        },
    }


def _get_profile(db) -> dict:
    row = db.execute(text("SELECT COUNT(*), MAX(trade_date) FROM trade_slip")).fetchone()
    key = (int(row[0] or 0), str(row[1] or ""))
    now = time.time()
    cached = _PROFILE_CACHE
    if cached["data"] is not None and cached["key"] == key and now - cached["ts"] < 600:
        return cached["data"]
    try:
        data = _profile_from_review(build_review(db))
    except Exception as e:
        logger.warning(f"[intraday] 交易画像失败: {str(e)[:120]}")
        data = {"types": [], "metrics": {}}
    _PROFILE_CACHE.update(key=key, ts=now, data=data)
    return data


def _top_sectors(db, positions: list[dict]) -> list[dict]:
    """最大 3 个持仓所属行业的近 3 日主力净流入与连续天数。"""
    tops = positions[:3]
    codes = [p["stock_code"] for p in tops]
    if not codes:
        return []
    rows = db.execute(text("""
        SELECT stock_code, MAX(industry_l1)
        FROM stock_master
        WHERE stock_code = ANY(CAST(:codes AS varchar[])) AND stock_type='stock'
        GROUP BY stock_code
    """), {"codes": codes}).fetchall()
    ind = {r[0]: r[1] for r in rows if r[1]}
    names = sorted({v for v in ind.values()})
    if not names:
        return []
    flow_rows = db.execute(text("""
        SELECT sector_name, trade_date, main_net
        FROM sector_flow_daily
        WHERE level='l1' AND sector_name = ANY(CAST(:names AS varchar[]))
          AND trade_date >= (SELECT MAX(trade_date) FROM sector_flow_daily) - INTERVAL '10 days'
        ORDER BY sector_name, trade_date
    """), {"names": names}).fetchall()
    by_name: dict[str, list] = defaultdict(list)
    for r in flow_rows:
        by_name[r[0]].append((r[1], _f(r[2])))
    out = []
    for p in tops:
        industry = ind.get(p["stock_code"])
        if not industry:
            continue
        series = by_name.get(industry) or []
        if not series:
            continue
        series.sort(key=lambda x: x[0])
        last3 = [v for _, v in series[-3:]]
        streak, sign = 0, 0
        for _, v in reversed(series):
            if v == 0:
                break
            sg = 1 if v > 0 else -1
            if sign == 0:
                sign = sg
            if sg != sign:
                break
            streak += 1
        out.append({
            "code": p["stock_code"], "name": p["stock_name"], "industry": industry,
            "main_net": round(series[-1][1], 2), "sum3": round(sum(last3), 2),
            "streak": streak, "sign": sign, "date": series[-1][0].isoformat(),
        })
    return out


def _plan_from_state(st: dict) -> Optional[dict]:
    """从当前状态生成当日减仓计划（只在当天第一次生成时落库）。"""
    target = _f(st.get("target_position_ratio"), 0.70)
    mv = _f(st.get("market_value"))
    equity = _f(st.get("equity"))
    top_p = st.get("top_position") or {}
    max_single = _f((st.get("rules") or {}).get("max_single_weight"), 0.20)
    need_pos = max(0.0, mv - target * equity)
    top_mv = _f(top_p.get("market_value"))
    need_single = max(0.0, top_mv - max_single * equity) if top_p and equity > 0 else 0.0
    positions = st.get("positions") or []
    rows = []
    if any(_f(p.get("quantity")) > 0 and _f(p.get("price")) > 0 for p in positions):
        # 用集中度计算器同时处理「仓位目标 + 所有超标的单票」，
        # 避免只减最大单票后，第二只票仍超 20% 却没进计划。
        from app.reduction_plan import build_reduction_plan
        rp = build_reduction_plan(
            [{"code": p.get("stock_code"), "name": p.get("stock_name"),
              "qty": p.get("quantity"), "price": p.get("price")} for p in positions],
            st.get("cash") or 0.0, target, max_single,
        )
        need = _f(rp.get("need"))
        if need <= 0:
            return None
        rows = rp.get("rows") or []
        binding = rp.get("binding") or "position"
        top_name = (rows[0]["name"] if rows else top_p.get("stock_name")) or "最大单票"
        top_code = rows[0]["code"] if rows else top_p.get("stock_code")
    else:
        need = max(need_pos, need_single)
        if need <= 0:
            return None
        binding = "single" if need_single > need_pos + 1e-9 else "position"
        top_name = top_p.get("stock_name") or "最大单票"
        top_code = top_p.get("stock_code")
    single_binding = binding == "single"
    names = [r["name"] for r in rows[:3]] or [top_name]
    each = round(need / 3, 2)
    first_hint = ("开盘 15 分钟不操作；按清单优先压超标单票" if single_binding
                  else "开盘 15 分钟不操作；优先减流出最久的板块")
    return {
        "need": round(need, 2),
        "need_pos": round(need_pos, 2),
        "need_single": round(need_single, 2),
        "binding": binding,
        "top_code": top_code,
        "top_name": top_name,
        "names": names,
        "rows": rows,
        "target": target,
        "max_single": max_single,
        "created_asof": st.get("asof"),
        "tranches": [
            {"amount": each, "when": "10:00-10:30", "hint": first_hint},
            {"amount": each, "when": "14:00-14:30",
             "hint": "若大盘/板块资金流转正，暂停这一份"},
            {"amount": each, "when": "条件单",
             "hint": "跌破前低再卖；站回 5 日线保留；剩余单票 -8% 减半"},
        ],
    }


def _window_passed(when: str, now_hm: str) -> bool:
    end = str(when or "").split("-")[-1]
    if ":" not in end or ":" not in str(now_hm or ""):
        return False
    return str(now_hm) >= end


def _plan_status(plan: dict, st: dict) -> dict:
    """按今日卖出额回填每档执行状态；单票约束只认最大单票的卖出。"""
    actions = st.get("today_actions") or []
    plan_rows = plan.get("rows") or []
    if plan_rows:
        rows = []
        sold = 0.0
        for row in plan_rows:
            code = row.get("code")
            target_amt = _f(row.get("sell_amount"))
            got = sum(_f(a.get("amount")) for a in actions
                      if a.get("side") == "sell" and a.get("stock_code") == code)
            ex = min(target_amt, got)
            sold += ex
            status = ("done" if target_amt > 0 and ex >= target_amt * 0.95 else
                      "partial" if ex > 0 else "pending")
            rows.append({**row, "executed": round(ex, 2), "status": status})
        plan = {**plan, "rows": rows}
    else:
        binding = plan.get("binding")
        top_code = plan.get("top_code")
        if binding == "single" and top_code:
            sold = sum(_f(a.get("amount")) for a in actions
                       if a.get("side") == "sell" and a.get("stock_code") == top_code)
        else:
            sold = sum(_f(a.get("amount")) for a in actions if a.get("side") == "sell")
    now_hm = str(st.get("asof") or "")[:5]
    rem = sold
    tranches = []
    for t in plan.get("tranches") or []:
        amt = _f(t.get("amount"))
        ex = min(amt, max(0.0, rem))
        rem -= ex
        if amt > 0 and ex >= amt * 0.95:
            status = "done"
        elif ex > 0:
            status = "partial"
        elif _window_passed(t.get("when"), now_hm):
            status = "missed"
        else:
            status = "pending"
        tranches.append({**t, "executed": round(ex, 2), "status": status})
    need = _f(plan.get("need"))
    return {
        **plan, "tranches": tranches,
        "executed": round(sold, 2),
        "remaining": round(max(0.0, need - sold), 2),
        "now": now_hm or "09:30",
    }


def get_or_create_plan(db, st: dict, today: Optional[_date] = None) -> Optional[dict]:
    """当天第一次生成后固定计划，盘中只回填执行状态，不随价格漂移。"""
    day = today or _date.fromisoformat(str(st["trade_date"])[:10])
    row = db.execute(text(
        "SELECT plan FROM trade_plan WHERE trade_date=:d"
    ), {"d": day}).scalar()
    if row is not None:
        plan = json.loads(row) if isinstance(row, str) else row
        if "rows" not in plan:
            # 旧版计划只按最大单票估算，升级为“所有超标单票”清单
            new_plan = _plan_from_state(st)
            if new_plan:
                db.execute(text("""
                    UPDATE trade_plan SET plan=CAST(:p AS jsonb),
                        updated_at=CURRENT_TIMESTAMP WHERE trade_date=:d
                """), {"d": day, "p": json.dumps(new_plan, ensure_ascii=False)})
                db.commit()
                return new_plan
        return plan
    plan = _plan_from_state(st)
    if not plan:
        return None
    db.execute(text("""
        INSERT INTO trade_plan (trade_date, plan) VALUES (:d, CAST(:p AS jsonb))
        ON CONFLICT (trade_date) DO NOTHING
    """), {"d": day, "p": json.dumps(plan, ensure_ascii=False)})
    db.commit()
    row = db.execute(text(
        "SELECT plan FROM trade_plan WHERE trade_date=:d"
    ), {"d": day}).scalar()
    if row is None:
        return plan
    return json.loads(row) if isinstance(row, str) else row


def _build_coach(st: dict, profile: dict, sectors: list[dict],
                 plan: Optional[dict] = None) -> dict:
    active: list[dict] = []
    today = st["trade_date"]
    target = _f(st.get("target_position_ratio"), 0.70)
    hard = _f(st.get("max_position_ratio"), 0.90)
    pos = _f(st.get("position_ratio"))
    mv = _f(st.get("market_value"))
    equity = _f(st.get("equity"))
    top_w = _f(st.get("top_weight"))
    top_p = st.get("top_position") or {}
    max_single = _f((st.get("rules") or {}).get("max_single_weight"), 0.20)
    worst_sector = min(sectors, key=lambda x: _f(x.get("sum3"))) if sectors else None
    sector_bad = bool(worst_sector and _f(worst_sector.get("sum3")) < 0
                      and int(worst_sector.get("streak") or 0) >= 2
                      and int(worst_sector.get("sign") or 0) < 0)

    if pos >= hard:
        active.append({"level": "critical", "title": f"仓位 {_pct(pos)} 已超硬上限 {_pct(hard)}",
                       "action": "今天只减不加；先减 1/3 到目标仓位",
                       "rule": "目标 ≤70%，硬上限 90%"})
    elif pos > target:
        active.append({"level": "warn", "title": f"仓位 {_pct(pos)} 高于目标 {_pct(target)}",
                       "action": "先分批减到 70% 以下，再考虑新开仓",
                       "rule": "目标仓位 ≤70%"})
    if top_w > max_single and top_p:
        active.append({"level": "critical" if top_w > 0.35 else "warn",
                       "title": f"最大单票 {top_p.get('stock_name')} 占账户 {_pct(top_w)}，超上限 {_pct(max_single)}",
                       "action": f"只减不加；优先把 {top_p.get('stock_name')} 降到 {_pct(max_single, 0)} 以下",
                       "rule": "单票市值/账户 ≤20%；超过只减不加"})
    if int(st.get("consecutive_loss_days") or 0) >= 2:
        active.append({"level": "critical",
                       "title": f"连续亏损 {st['consecutive_loss_days']} 天",
                       "action": "今天只减不加；收盘后复盘，不做新开仓",
                       "rule": "连续 2 个亏损日 → 次日冷却"})
    losers = [p for p in (st.get("positions") or []) if _f(p.get("pnl_pct")) < -0.08]
    if losers:
        worst = min(losers, key=lambda p: _f(p.get("pnl_pct")))
        active.append({"level": "warn",
                       "title": f"{worst['stock_name']} 浮亏 {_pct(_f(worst.get('pnl_pct')))}",
                       "action": f"禁止补仓；优先减 {worst['stock_name']}",
                       "rule": "浮亏 >8% 禁止加仓；-8% 无条件减半"})
    if sector_bad:
        w = worst_sector
        active.append({"level": "warn",
                       "title": f"持仓板块 {w['industry']} 持续流出",
                       "action": f"优先减 {w['name']}（{w['industry']}），不补",
                       "rule": f"近3日主力净流出 {_money(abs(w['sum3']))}，连续 {w['streak']} 天"})
    if int(st.get("remaining_new_buys") or 0) <= 0:
        active.append({"level": "warn", "title": "今日新开仓已用完",
                       "action": "今天不再买；有仓位就按计划减",
                       "rule": "每日新开仓 ≤2 笔"})
    if st.get("slip_end") != today and not (st.get("today_actions") or []):
        active.append({"level": "info", "title": "今日操作/交割单未记录",
                       "action": "先把今天成交记一笔或导入交割单，否则仓位数据是旧的",
                       "rule": "数据不完整时不做交易决定"})

    # 当日计划首次生成后固定（trade_plan），这里只回填执行状态和按时间给下一动作
    if plan is None:
        plan = _plan_from_state(st)
    if plan:
        plan = _plan_status(plan, st)
    need = _f(plan.get("need")) if plan else 0.0
    single_binding = bool(plan and plan.get("binding") == "single")
    top_name = (plan or {}).get("top_name") or top_p.get("stock_name") or "最大单票"
    names = (plan or {}).get("names") or [top_name]
    names_str = "、".join(names[:2])
    tranches = (plan or {}).get("tranches") or []
    remaining = _f(plan.get("remaining")) if plan else 0.0
    executed = _f(plan.get("executed")) if plan else 0.0

    if plan and remaining <= 0.01:
        nxt = "今天只做一件事：计划已完成，不再交易，收盘后复盘。"
    elif plan:
        each = _f((tranches[0] if tranches else {}).get("amount")) or round(need / 3, 2)
        now_hm = str(plan.get("now") or "09:30")
        if now_hm < "10:00":
            step = min(each, remaining)
            nxt = f"今天只做一件事：下一档 10:00-10:30 先减 {_money(step)}"
        elif now_hm < "10:30":
            step = min(each, remaining)
            nxt = f"今天只做一件事：现在 {now_hm}，第一档窗口，先减 {_money(step)}"
        elif now_hm < "14:00":
            step = min(each * 2, remaining)
            nxt = (f"今天只做一件事：第一档窗口已过；14:00-14:30 "
                   f"执行剩余 {_money(step)}（可合并两档）")
        elif now_hm < "14:30":
            step = min(each, remaining)
            nxt = f"今天只做一件事：现在 {now_hm}，第二档窗口，减 {_money(step)}"
        else:
            nxt = (f"今天只做一件事：今天窗口已过；剩余 {_money(remaining)} "
                   f"明天第一档处理，收盘前只做条件单")
        if single_binding:
            nxt += f"，优先把 {names_str} 的超标单票压到 {_pct(max_single, 0)} 以下"
        else:
            nxt += f"，目标仓位 {_pct(target, 0)}"
        prio = []
        if top_w > max_single and top_p and not single_binding:
            prio.append(f"{top_name}（占账户 {_pct(top_w)}）")
        if sector_bad:
            prio.append(f"{worst_sector['name']}（{worst_sector['industry']} 连续流出）")
        if prio:
            nxt += "，优先减 " + "、".join(prio[:2])
        nxt += "；不补任何亏损仓。"
    elif int(st.get("consecutive_loss_days") or 0) >= 2:
        nxt = "今天只做一件事：只减不加，收盘后复盘。"
    elif st.get("slip_end") != today and not (st.get("today_actions") or []):
        nxt = "今天只做一件事：先把今天成交记录/导入交割单，再谈操作。"
    elif active:
        nxt = f"今天只做一件事：{active[0]['action']}。"
    else:
        nxt = "今天只做一件事：按计划执行，不追涨；收盘后导入交割单。"

    plan_out = None
    if plan:
        plan_out = {
            "need": round(need, 2), "executed": round(executed, 2),
            "remaining": round(remaining, 2), "binding": plan.get("binding"),
            "top_name": plan.get("top_name"), "names": plan.get("names") or [],
            "rows": plan.get("rows") or [], "now": plan.get("now"),
            "done": remaining <= 0.01,
        }
    return {"next": nxt, "active": active[:5], "tranches": tranches,
            "plan": plan_out,
            "profile": (profile.get("types") or [])[:4],
            "metrics": profile.get("metrics") or {},
            "sectors": sectors}


def live_snapshot(db, cash_override: Optional[float] = None) -> dict:
    """实时持仓/现金快照（行情无关）：交割单 + 今日未对账手工成交。

    这是交易链路的实时持仓唯一口径：护栏、教练、雷达、盘中 watcher 都必须用它，
    不能再直接读 portfolio 表——否则手工卖出后，旧持仓还会继续触发止损提醒。
    （持仓页的财务报表仍以交割单为准，P0-3 会给它加“待导入”提示。）
    """
    today = _today()
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

    return {
        "positions": pos, "cash": cash, "cash_source": cash_source,
        "slip_end": slip_end, "rules": rules, "today": today,
    }


def live_positions(db, cash_override: Optional[float] = None) -> dict:
    """实时持仓的展示口径（不含行情）：供 watcher/雷达/临时持仓提示复用。"""
    live = live_snapshot(db, cash_override)
    out = []
    for code, p in live["positions"].items():
        qty = p["qty"]
        if qty <= 0.5:
            continue
        out.append({
            "stock_code": code,
            "stock_name": p["name"],
            "quantity": int(qty) if abs(qty - round(qty)) < 1e-6 else round(qty, 2),
            "cost_price": round(p["cost"], 4),
            "cost_estimated": bool(p.get("estimated")),
        })
    out.sort(key=lambda x: -(x["quantity"] * x["cost_price"]))
    return {
        "positions": out,
        "cash": round(live["cash"], 2),
        "trade_date": live["today"].isoformat(),
        "slip_end": live["slip_end"].isoformat() if live["slip_end"] else None,
        "cash_source": live["cash_source"],
    }


def _build_state(db, cash_override: Optional[float] = None) -> dict:
    now = _now()
    live = live_snapshot(db, cash_override)
    today = live["today"]
    pos = live["positions"]
    slip_end = live["slip_end"]
    rules = live["rules"]
    cash = live["cash"]
    cash_source = live["cash_source"]

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
    # 与 max_single_weight 同口径：单票市值 / 账户权益（现金+持仓），不是占持仓
    top_weight = (top["market_value"] / equity) if top and equity > 0 else 0.0

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
                           "message": f"最大单票 {top['stock_name']} 占账户 {_pct(top_weight)}（上限 {_pct(max_single_w)}），只减不加"})

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
            advice += f" 最大单票 {top['stock_name']} 占账户 {_pct(top_weight)} 超上限，只减不加。"

    state = {
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
    plan = None
    try:
        plan = get_or_create_plan(db, state, today)
    except Exception as e:
        logger.warning(f"[intraday] 计划生成失败: {str(e)[:120]}")
    try:
        profile = _get_profile(db)
        sectors = _top_sectors(db, positions)
        state["coach"] = _build_coach(state, profile, sectors, plan=plan)
    except Exception as e:
        logger.warning(f"[intraday] 教练生成失败: {str(e)[:120]}")
        state["coach"] = None
    try:
        state["journal"] = journal_get(db, today)
    except Exception:
        state["journal"] = None
    return state


def state(db) -> dict:
    return _build_state(db)


def journal_get(db, day: Optional[_date] = None) -> dict:
    day = day or _today()
    try:
        row = db.execute(text("""
            SELECT trade_date, pre_plan, post_review, updated_at
            FROM trade_journal WHERE trade_date=:d
        """), {"d": day}).mappings().fetchone()
    except Exception:
        row = None
    if not row:
        return {"trade_date": day.isoformat(), "pre_plan": "",
                "post_review": "", "updated_at": None}
    return {"trade_date": row["trade_date"].isoformat(),
            "pre_plan": row["pre_plan"] or "",
            "post_review": row["post_review"] or "",
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None}


def journal_save(db, pre_plan: Optional[str] = None,
                 post_review: Optional[str] = None,
                 day: Optional[_date] = None) -> dict:
    day = day or _today()
    cur = journal_get(db, day)
    pre = cur["pre_plan"] if pre_plan is None else str(pre_plan)[:4000]
    post = cur["post_review"] if post_review is None else str(post_review)[:4000]
    db.execute(text("""
        INSERT INTO trade_journal (trade_date, pre_plan, post_review, updated_at)
        VALUES (:d, :pre, :post, CURRENT_TIMESTAMP)
        ON CONFLICT (trade_date) DO UPDATE SET
            pre_plan=EXCLUDED.pre_plan, post_review=EXCLUDED.post_review,
            updated_at=CURRENT_TIMESTAMP
    """), {"d": day, "pre": pre, "post": post})
    db.commit()
    return journal_get(db, day)


def quote(db, code: str) -> dict:
    code = str(code or "").strip().zfill(6)
    qmap, _ = _quotes([code])
    q = qmap.get(code) or {}
    price = _f(q.get("price")) or _last_close(db, code)
    return {
        "stock_code": code,
        "stock_name": q.get("name") or _stock_name(db, code),
        "price": round(price, 4),
        "change_pct": q.get("change_pct"),
        "volume_ratio": q.get("volume_ratio"),
        "avg_price": q.get("avg_price"),
        "open": q.get("open"),
        "high": q.get("high"),
        "low": q.get("low"),
        "prev_close": q.get("prev_close"),
        "trade_date": q.get("trade_date"),
    }


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


HARD_BLOCK_RULES = {"same_day_roundtrip", "add_to_loser", "loss_cooldown", "cooling_off"}
COOLING_MIN = 30


def _is_hard_block(chk: dict) -> bool:
    """当日回补/亏损加仓/连亏冷却/冷静期——记录后触发 30 分钟买入冷却。"""
    return any(v.get("level") == "block" and v.get("rule") in HARD_BLOCK_RULES
               for v in (chk.get("violations") or []))


def _cooling_off(db) -> Optional[dict]:
    """最近一次硬拦截买入后的 30 分钟冷静期；返回剩余时间或 None。"""
    try:
        row = db.execute(text("""
            SELECT created_at, stock_name FROM trade_intraday
            WHERE trade_date=CURRENT_DATE AND side='buy' AND hard_block=true
            ORDER BY created_at DESC LIMIT 1
        """)).fetchone()
    except Exception:
        return None
    if not row or row[0] is None:
        return None
    until = row[0] + timedelta(minutes=COOLING_MIN)
    now = datetime.now()
    remain = int(math.ceil((until - now).total_seconds() / 60))
    if remain <= 0:
        return None
    return {"until": until.strftime("%H:%M"), "remaining_min": remain,
            "stock_name": row[1] or "上一笔"}


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
        cool = _cooling_off(db)
        if cool:
            add("cooling_off", "block",
                f"{COOLING_MIN}分钟冷静期：{cool['until']} 前禁止新买入"
                f"（上次硬拦截：{cool['stock_name']}）")
        add("t_plus_1", "warn",
            "T+1：今天买入，今天不能卖出；如果跌破止损，最早明天才能执行")
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
                      f"止损亏 {_money(risk_amount)}（账户 {_pct(risk_pct)}）。"
                      f"注意 T+1：今天买入，今天不能卖出，止损最早明天执行。")
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
    hard_block = _is_hard_block(chk)
    reason_text = str(body.get("reason") or "")[:200]
    now = _now()
    db.execute(text("""
        INSERT INTO trade_intraday
            (trade_date, trade_time, stock_code, stock_name, side, quantity, price, amount,
             commission, stamp_tax, transfer_fee, extra_fee, stop_price, reason,
             hard_block, override_reason, emotion, source)
        VALUES (:d, :t, :c, :n, :s, :q, :p, :a, :com, :st, :tr, :ex, :stop, :r,
                :hb, :or, :em, 'manual')
    """), {
        "d": now.date(), "t": now.strftime("%H:%M:%S"), "c": code,
        "n": chk.get("stock_name") or _stock_name(db, code), "s": side, "q": qty, "p": price,
        "a": amount, "com": fees["commission"], "st": fees["stamp_tax"],
        "tr": fees["transfer_fee"], "ex": fees["extra_fee"],
        "stop": _f(body.get("stop_price")) or None, "r": reason_text,
        "hb": hard_block, "or": reason_text,
        "em": str(body.get("emotion") or "")[:16],
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
    logger.info(f"[intraday] 记录 {side} {code} {qty:g}@{price} allow={chk['allow']} "
                f"hard_block={hard_block}")
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
    """导入交割单后，把已覆盖日期的临时记录标记为已对账（不再参与临时持仓）。

    同时清掉这些日期的现金快照：交割单里的资金余额是权威值，
    之前手动修正的现金可能已过期；用户如仍有出入金，重新修正即可。
    """
    n = db.execute(text("""
        UPDATE trade_intraday SET reconciled=true
        WHERE reconciled=false
          AND trade_date <= (SELECT COALESCE(MAX(trade_date), '1900-01-01') FROM trade_slip)
    """)).rowcount or 0
    try:
        db.execute(text("""
            DELETE FROM trade_cash_snapshot
            WHERE trade_date <= (SELECT COALESCE(MAX(trade_date), '1900-01-01') FROM trade_slip)
        """))
    except Exception:
        pass
    db.commit()
    return n
