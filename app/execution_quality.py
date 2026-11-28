"""交易执行质量：卖出 vs VWAP/30/60分钟/收盘，提醒执行率与等待成本。

回答两个问题：
1. 卖出执行得好不好：卖价相对卖出时 VWAP、30/60 分钟后价格、收盘价；
2. 系统提醒有没有执行：止损/减仓提醒后是否卖出，滑点多少；没卖的话收盘价相对提醒价走了多少。

数据源只用已有表（trade_slip / trade_intraday / stock_minute_bar /
stock_treemap_intraday / daily_quote / risk_alerts），不新增外部请求。
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from statistics import fmean
from typing import Optional

from sqlalchemy import text

KIND_LABEL = {
    "it_stop": "止损", "it_take": "止盈", "it_reduce_bounce": "超配反弹减仓",
}


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_dt(day: date, trade_time) -> Optional[datetime]:
    if isinstance(trade_time, datetime):
        return trade_time
    s = str(trade_time or "").strip()
    m = re.match(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if not m:
        return None
    return datetime.combine(day, time(int(m.group(1)), int(m.group(2)),
                                     int(m.group(3) or 0)))


def _price_from_body(body: str) -> Optional[float]:
    m = re.search(r"现价\s*([0-9]+(?:\.[0-9]+)?)", body or "")
    return float(m.group(1)) if m else None


def _load_sells(db, user_id: int, start: date) -> list[dict]:
    try:
        rows = db.execute(text("""
            SELECT trade_date, trade_time, stock_code, stock_name,
                   quantity, price, amount, 'slip' AS src
            FROM trade_slip
            WHERE user_id=:uid AND LOWER(side)='sell' AND trade_date >= :s
            UNION ALL
            SELECT trade_date, trade_time, stock_code, stock_name,
                   quantity, price, amount, 'manual' AS src
            FROM trade_intraday
            WHERE user_id=:uid AND LOWER(side)='sell' AND trade_date >= :s
              AND COALESCE(reconciled, false)=false
            ORDER BY trade_date, trade_time
        """), {"uid": user_id, "s": start}).mappings().fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows if _f(r.get("price")) > 0]


def _bars(db, code: str, day: date) -> list[dict]:
    """当日分钟线（优先），缺失时退回 10 分钟快照。"""
    start = datetime.combine(day, time.min)
    try:
        rows = db.execute(text("""
            SELECT bar_time, close, vwap FROM stock_minute_bar
            WHERE stock_code=:c AND bar_time >= :s AND bar_time < :e
            ORDER BY bar_time
        """), {"c": code, "s": start, "e": start + timedelta(days=1)}).fetchall()
    except Exception:
        rows = []
    out = [{"t": r[0], "close": _f(r[1]), "vwap": _f(r[2])}
           for r in rows if _f(r[1]) > 0]
    if out:
        return out
    try:
        rows = db.execute(text("""
            SELECT slot, price FROM stock_treemap_intraday
            WHERE stock_code=:c AND trade_date=:d AND price > 0
            ORDER BY slot
        """), {"c": code, "d": day}).fetchall()
    except Exception:
        rows = []
    for slot, price in rows:
        try:
            h, m = [int(x) for x in str(slot).split(":")[:2]]
            out.append({"t": datetime.combine(day, time(h, m)),
                        "close": _f(price), "vwap": 0.0})
        except (TypeError, ValueError):
            continue
    return out


def _day_stats(db, code: str, day: date, bars: list[dict]) -> tuple[Optional[float], Optional[float]]:
    """日收盘 + 全日 VWAP（amount/volume），无日线时退回最后一根 bar。"""
    try:
        row = db.execute(text("""
            SELECT close, amount, volume FROM daily_quote
            WHERE stock_code=:c AND trade_date=:d AND close > 0
            LIMIT 1
        """), {"c": code, "d": day}).fetchone()
        if row and _f(row[0]) > 0:
            close = _f(row[0])
            vol = _f(row[2])
            vwap = _f(row[1]) / vol if vol > 0 else None
            return close, vwap
    except Exception:
        pass
    close = bars[-1]["close"] if bars else None
    vwap = next((b["vwap"] for b in reversed(bars) if b["vwap"] > 0), None)
    return close, vwap


def _pick(bars: list[dict], target: datetime) -> Optional[float]:
    for b in bars:
        if b["t"] >= target:
            return b["close"]
    return bars[-1]["close"] if bars else None


def _vwap_at(bars: list[dict], target: datetime) -> Optional[float]:
    for b in bars:
        if b["t"] >= target:
            return b["vwap"] if b["vwap"] > 0 else b["close"]
    return None


def _grade(vs30: Optional[float]) -> str:
    if vs30 is None:
        return "no_data"
    if vs30 >= 0.005:
        return "good"
    if vs30 <= -0.01:
        return "poor"
    return "neutral"


def _avg(vals) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return round(fmean(vals), 6) if vals else None


def sell_quality(db, user_id: int, start: date, limit: int = 100) -> dict:
    rows = _load_sells(db, user_id, start)
    cache: dict[tuple[str, date], list[dict]] = {}
    out = []
    for r in rows:
        code = r["stock_code"]
        day = r["trade_date"]
        key = (code, day)
        if key not in cache:
            cache[key] = _bars(db, code, day)
        bars = cache[key]
        close, day_vwap = _day_stats(db, code, day, bars)
        dt = _parse_dt(day, r.get("trade_time"))
        sell = _f(r.get("price"))
        vwap, p30, p60 = day_vwap, None, None
        vs30 = vs60 = None
        vs_vwap = sell / vwap - 1 if vwap else None
        vs_close = sell / close - 1 if close else None
        if dt and bars:
            vwap = _vwap_at(bars, dt) or day_vwap
            p30 = _pick(bars, dt + timedelta(minutes=30))
            p60 = _pick(bars, dt + timedelta(minutes=60))
            vs_vwap = sell / vwap - 1 if vwap else None
            vs30 = sell / p30 - 1 if p30 else None
            vs60 = sell / p60 - 1 if p60 else None
            vs_close = sell / close - 1 if close else None
        out.append({
            "trade_date": day.isoformat(),
            "trade_time": str(r.get("trade_time") or ""),
            "stock_code": code, "stock_name": r.get("stock_name") or code,
            "quantity": round(_f(r.get("quantity")), 2),
            "price": round(sell, 4),
            "vwap": round(vwap, 4) if vwap else None,
            "p30": round(p30, 4) if p30 else None,
            "p60": round(p60, 4) if p60 else None,
            "close": round(close, 4) if close else None,
            "vs_vwap": round(vs_vwap, 6) if vs_vwap is not None else None,
            "vs_30m": round(vs30, 6) if vs30 is not None else None,
            "vs_60m": round(vs60, 6) if vs60 is not None else None,
            "vs_close": round(vs_close, 6) if vs_close is not None else None,
            "grade": _grade(vs30),
        })
    out.sort(key=lambda x: (x["trade_date"], x["trade_time"]), reverse=True)
    grades = [x["grade"] for x in out]
    summary = {
        "count": len(out),
        "shown": min(len(out), limit),
        "good": grades.count("good"),
        "neutral": grades.count("neutral"),
        "poor": grades.count("poor"),
        "no_data": grades.count("no_data"),
        "avg_vs_vwap": _avg([x["vs_vwap"] for x in out]),
        "avg_vs_30m": _avg([x["vs_30m"] for x in out]),
        "avg_vs_close": _avg([x["vs_close"] for x in out]),
    }
    return {"rows": out[:limit], "summary": summary}


def alert_execution(db, user_id: int, start: date, limit: int = 100) -> dict:
    try:
        alerts = db.execute(text("""
            SELECT id, trade_date, created_at, stock_code, kind, title, body, trigger_price
            FROM risk_alerts
            WHERE user_id=:uid AND trade_date >= :s
              AND kind IN ('it_stop', 'it_take', 'it_reduce_bounce')
            ORDER BY created_at DESC
        """), {"uid": user_id, "s": start}).mappings().fetchall()
    except Exception:
        alerts = []
    sells = _load_sells(db, user_id, start)
    by_code: dict[tuple[str, date], list[dict]] = {}
    for s in sells:
        s["_dt"] = _parse_dt(s["trade_date"], s.get("trade_time"))
        by_code.setdefault((s["stock_code"], s["trade_date"]), []).append(s)
    names = {}
    try:
        codes = list({a["stock_code"] for a in alerts})
        if codes:
            for r in db.execute(text("""
                SELECT stock_code, stock_name FROM stock_master
                WHERE stock_code = ANY(CAST(:codes AS varchar[]))
                ORDER BY CASE stock_type WHEN 'stock' THEN 1 ELSE 2 END
            """), {"codes": codes}).fetchall():
                names.setdefault(r[0], r[1])
    except Exception:
        pass
    cache: dict[tuple[str, date], list[dict]] = {}
    out = []
    for a in alerts:
        code = a["stock_code"]
        day = a["trade_date"]
        alert_dt = a["created_at"]
        alert_px = _f(a["trigger_price"]) or _price_from_body(a["body"])
        if not alert_px:
            continue
        candidates = by_code.get((code, day)) or []
        hit = next((s for s in candidates
                    if s.get("_dt") and alert_dt and s["_dt"] >= alert_dt - timedelta(minutes=1)), None)
        if hit is None and candidates and not any(s.get("_dt") for s in candidates):
            hit = candidates[0]
        # 一笔卖出只能核销一条提醒：否则同天同票 N 条提醒 + 1 笔卖出
        # 会被全部标成「已卖」，执行率虚高（曾把 1 次执行算成 3 次）
        if hit is not None:
            candidates.remove(hit)
        key = (code, day)
        if key not in cache:
            cache[key] = _bars(db, code, day)
        close, _ = _day_stats(db, code, day, cache[key])
        row = {
            "alert_id": a["id"], "trade_date": day.isoformat(),
            "alert_time": alert_dt.strftime("%H:%M:%S") if alert_dt else "",
            "stock_code": code, "stock_name": names.get(code) or code,
            "kind": a["kind"], "kind_label": KIND_LABEL.get(a["kind"], a["kind"]),
            "alert_price": round(alert_px, 4), "executed": bool(hit),
            "sell_time": "", "sell_price": None, "slippage_pct": None,
            "close": round(close, 4) if close else None,
            "missed_move_pct": None,
        }
        if hit:
            sell_px = _f(hit.get("price"))
            row["sell_time"] = str(hit.get("trade_time") or "")
            row["sell_price"] = round(sell_px, 4)
            row["slippage_pct"] = round(sell_px / alert_px - 1, 6) if sell_px else None
        elif close:
            row["missed_move_pct"] = round(close / alert_px - 1, 6)
        out.append(row)
    executed = [x for x in out if x["executed"]]
    missed = [x for x in out if not x["executed"]]
    return {
        "rows": out[:limit],
        "summary": {
            "count": len(out), "shown": min(len(out), limit),
            "executed": len(executed),
            "execution_rate": round(len(executed) / len(out), 6) if out else None,
            "avg_slippage": _avg([x["slippage_pct"] for x in executed]),
            "missed": len(missed),
            "avg_missed_move": _avg([x["missed_move_pct"] for x in missed]),
        },
    }


def emotion_summary(db, user_id: int, start: date) -> list[dict]:
    try:
        rows = db.execute(text("""
            SELECT COALESCE(NULLIF(emotion, ''), '未标注') AS emotion,
                   side, COUNT(*) AS n, SUM(amount) AS amount
            FROM trade_intraday
            WHERE user_id=:uid AND trade_date >= :s
            GROUP BY 1, 2 ORDER BY n DESC
        """), {"uid": user_id, "s": start}).fetchall()
    except Exception:
        rows = []
    return [{"emotion": r[0], "side": r[1], "count": int(r[2] or 0),
             "amount": round(_f(r[3]), 2)} for r in rows]


def report(db, user_id: int, days: int = 30) -> dict:
    days = max(1, min(int(days or 30), 180))
    start = date.today() - timedelta(days=days)
    sells = sell_quality(db, user_id, start)
    alerts = alert_execution(db, user_id, start)
    return {
        "ok": True, "days": days, "start": start.isoformat(),
        "sells": sells["rows"], "sell_summary": sells["summary"],
        "alerts": alerts["rows"], "alert_summary": alerts["summary"],
        "emotions": emotion_summary(db, user_id, start),
    }
