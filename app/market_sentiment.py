"""市场情绪仪表：宽度/中位数/涨跌停/指数 VWAP → 恐慌/偏弱/中性/偏强。

用于两件事：
1. 前端展示当日市场风向（不再只看自己持仓）；
2. 盘中止损分层：市场恐慌/偏弱但个股站上 VWAP 时，提示先卖 50%，
   剩余挂 VWAP/前高、14:50 无条件处理；个股跌破 VWAP 则直接止损。

数据源：stock_treemap_intraday（10分钟全市场快照）+ 腾讯指数实时快照。
"""
from __future__ import annotations

import statistics
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from loguru import logger
from sqlalchemy import text

from crawler.adapters.tencent_adapter import realtime_quotes

_CST = timezone(timedelta(hours=8))
_CACHE = {"ts": 0.0, "data": None}
_TTL = 60


def _bj_today() -> date:
    """北京时间的自然日。「今天」判定不能依赖主机时区（v3.27.1 §10.9：UTC 主机在
    北京时间 00:00–08:00 会把今天算成昨天，stale/asof 全部标错）。"""
    return datetime.now(_CST).date()

LEVEL_LABEL = {"panic": "恐慌", "weak": "偏弱", "neutral": "中性", "strong": "偏强"}
ADVICE = {
    "panic": {"entry": "禁止新开仓", "stop": "止损先卖50%，剩余挂VWAP/14:50，不裸等"},
    "weak": {"entry": "只减不加", "stop": "止损分层：站上VWAP留剩余，14:50无条件处理"},
    "neutral": {"entry": "按买点检查", "stop": "止损正常执行；不追跌"},
    "strong": {"entry": "可正常按买点检查，不追高", "stop": "止损正常执行"},
}


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _limit_pct(code: str) -> float:
    return 19.8 if code.startswith(("300", "301", "688", "689")) else 9.8


def _tree_breadth(db) -> Optional[dict]:
    try:
        row = db.execute(text("""
            SELECT trade_date, slot FROM stock_treemap_intraday
            ORDER BY trade_date DESC, slot DESC LIMIT 1
        """)).fetchone()
        if not row:
            return None
        day, slot = row
        rows = db.execute(text("""
            SELECT stock_code, chg_pct FROM stock_treemap_intraday
            WHERE trade_date=:d AND slot=:s AND price > 0
        """), {"d": day, "s": slot}).fetchall()
    except Exception:
        return None
    if not rows:
        return None
    chgs = [_f(r[1]) for r in rows if r[1] is not None]
    up = sum(1 for x in chgs if x > 0)
    down = sum(1 for x in chgs if x < 0)
    flat = len(chgs) - up - down
    limit_up = sum(1 for r in rows if _f(r[1]) >= _limit_pct(str(r[0])))
    limit_down = sum(1 for r in rows if _f(r[1]) <= -_limit_pct(str(r[0])))
    return {
        "date": day.isoformat(), "slot": slot,
        "up": up, "down": down, "flat": flat, "total": len(chgs),
        "up_ratio": round(up / len(chgs), 6) if chgs else 0.0,
        "median": round(statistics.median(chgs), 2) if chgs else 0.0,
        "limit_up": limit_up, "limit_down": limit_down,
    }


def _indices() -> list[dict]:
    try:
        raw = realtime_quotes(["000001", "399006"], "index")
    except Exception as e:
        logger.debug(f"[sentiment] 指数快照失败: {str(e)[:100]}")
        return []
    out = []
    for q in raw.values():
        price = _f(q.get("price"))
        vwap = _f(q.get("avg_price"))
        out.append({
            "code": q.get("code"), "name": q.get("name") or q.get("code"),
            "price": round(price, 2), "chg_pct": q.get("change_pct"),
            "vwap": round(vwap, 2) if vwap else None,
            "above_vwap": bool(price and vwap and price >= vwap),
        })
    return out


def _verdict(up_ratio: Optional[float], median: Optional[float],
             limit_up: int, limit_down: int,
             above_count: int, index_count: int) -> tuple[str, float, list[str]]:
    score = 0.0
    reasons = []
    if up_ratio is not None:
        score += max(0.0, min(1.0, up_ratio)) * 40
        reasons.append(f"上涨占比 {up_ratio*100:.1f}%")
    else:
        score += 20
        reasons.append("宽度数据缺失")
    if median is not None:
        score += max(0.0, min(1.0, (median + 2) / 4)) * 20
        reasons.append(f"中位数 {median:+.2f}%")
    else:
        score += 10
    total_limit = limit_up + limit_down
    if total_limit > 0:
        bal = max(-1.0, min(1.0, (limit_up - limit_down) / total_limit))
        score += (bal + 1) / 2 * 20
        reasons.append(f"涨停 {limit_up} / 跌停 {limit_down}")
    else:
        score += 10
    if index_count > 0:
        score += above_count / index_count * 20
        reasons.append(f"指数站上VWAP {above_count}/{index_count}")
    else:
        score += 10
    score = round(max(0.0, min(100.0, score)), 1)

    panic = (score < 25 or (up_ratio is not None and up_ratio < 0.20)
             or (limit_down > limit_up * 2 and limit_down >= 20))
    weak = (not panic and (score < 45 or (up_ratio is not None and up_ratio < 0.40)))
    strong = (not panic and not weak and score >= 70 and
              (up_ratio is None or up_ratio >= 0.60) and limit_up >= limit_down)
    level = "panic" if panic else "weak" if weak else "strong" if strong else "neutral"
    return level, score, reasons


def get(db, use_cache: bool = True) -> dict:
    now = time.monotonic()
    if use_cache and _CACHE["data"] and now - _CACHE["ts"] < _TTL:
        return _CACHE["data"]
    breadth = _tree_breadth(db)
    indices = _indices()
    above = sum(1 for x in indices if x["above_vwap"])
    level, score, reasons = _verdict(
        breadth.get("up_ratio") if breadth else None,
        breadth.get("median") if breadth else None,
        breadth.get("limit_up", 0) if breadth else 0,
        breadth.get("limit_down", 0) if breadth else 0,
        above, len(indices),
    )
    today = _bj_today().isoformat()
    stale = bool(breadth and breadth.get("date") and breadth["date"] != today)
    asof = ((f"{breadth['date']} {breadth['slot']}" if stale else breadth.get("slot"))
            if breadth else datetime.now(_CST).strftime("%H:%M"))
    data = {
        "ok": True, "asof": asof, "stale": stale,
        "date": (breadth or {}).get("date") or today,
        "level": level, "label": LEVEL_LABEL[level], "score": score,
        "breadth": breadth or {},
        "indices": indices, "reasons": reasons,
        "advice": ADVICE[level],
    }
    _CACHE.update(ts=now, data=data)
    return data
