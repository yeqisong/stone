"""买点检查：市场 / 板块 / 个股结构 / 风险 四道闸门。

设计原则：
- 超跌只是背景，不是买点；必须有结构确认（趋势回踩 / 底部反转 / 突破）。
- 市场闸门不通过，不产生买点候选；板块闸门不通过，不产生候选。
- 风险闸门复用 trade_intraday.check，仓位/现金/纪律规则只有一套实现。
- 这里只回答“能不能买、最多买多少”，不代替下单，也不预测涨跌。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import fmean
from typing import Optional

from loguru import logger
from sqlalchemy import text

from app.trade_intraday import (
    _max_buy_qty,
    check as intraday_check,
    quote as intraday_quote,
    state as intraday_state,
)
from crawler.adapters.tencent_adapter import realtime_quotes

# 大盘用“上证 + 中证1000”：一个代表权重，一个代表中小盘，避免只看上证被权重护盘骗过
INDEX_BENCHMARKS = (("000001", "上证指数"), ("000852", "中证1000"))
MIN_BARS = 70          # 至少 70 根日线，才能算 MA60
RR_MIN = 2.0           # 盈亏比下限
BREADTH_MIN = 0.40     # 市场宽度（上涨家数占比）下限
MAX_CHASE = 0.03       # 现价高于参考买点 3% 以上视为追高
FLOW_DAYS = 25         # 板块资金流回看交易日数


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _pct(v, d: int = 1) -> str:
    return f"{_f(v) * 100:.{d}f}%"


def _money(v) -> str:
    v = _f(v)
    if abs(v) >= 1e8:
        return f"{v / 1e8:+.2f}亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:+.1f}万"
    return f"{v:+.0f}"


def _ma(vals: list[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    return fmean(vals[-n:])


# ── 市场闸门 ──────────────────────────────────────────────────────────────

def _index_stats(db, code: str, name: str, n: int = 90) -> dict:
    rows = db.execute(text("""
        SELECT trade_date, close FROM index_daily_quote
        WHERE index_code=:c AND close > 0
        ORDER BY trade_date DESC LIMIT :n
    """), {"c": code, "n": n}).fetchall()
    rows.reverse()
    closes = [_f(r[1]) for r in rows]
    if len(closes) < 25:
        return {"code": code, "name": name, "ok": False, "reason": "指数历史数据不足"}
    close = closes[-1]
    ma20 = fmean(closes[-20:])
    ma20_prev5 = fmean(closes[-25:-5])
    return {
        "code": code, "name": name, "ok": True,
        "date": rows[-1][0].isoformat(),
        "close": round(close, 2),
        "ma20": round(ma20, 2),
        "ma20_prev5": round(ma20_prev5, 2),
        "trend_ok": close >= ma20,
        "slope_ok": ma20 >= ma20_prev5,
        "chg1": round(close / closes[-2] - 1, 6) if len(closes) >= 2 else None,
        "chg5": round(close / closes[-6] - 1, 6) if len(closes) >= 6 else None,
    }


def _breadth(db) -> dict:
    latest = db.execute(text(
        "SELECT MAX(trade_date) FROM daily_quote WHERE close > 0"
    )).scalar()
    if not latest:
        return {"ok": False, "up_ratio": 0.0, "reason": "无日线数据"}
    prev = db.execute(text("""
        SELECT MAX(trade_date) FROM daily_quote
        WHERE close > 0 AND trade_date < :d
    """), {"d": latest}).scalar()
    if not prev:
        return {"ok": False, "up_ratio": 0.0, "reason": "无前一交易日数据"}
    row = db.execute(text("""
        SELECT COUNT(*) FILTER (WHERE c.close > p.close),
               COUNT(*) FILTER (WHERE c.close < p.close),
               COUNT(*) FILTER (WHERE c.close = p.close)
        FROM daily_quote c
        JOIN daily_quote p ON p.stock_code = c.stock_code AND p.trade_date = :pd
        WHERE c.trade_date = :ld AND c.close > 0 AND p.close > 0
    """), {"ld": latest, "pd": prev}).fetchone()
    up, down, flat = (int(x or 0) for x in row)
    total = up + down + flat
    return {
        "ok": total > 0, "date": latest.isoformat(),
        "up": up, "down": down, "flat": flat, "total": total,
        "up_ratio": round(up / total, 6) if total else 0.0,
    }


def _market_verdict(indexes: list[dict], breadth: dict) -> tuple[bool, str, str]:
    """纯判定：返回 (pass, level, reason)。"""
    valid = [x for x in indexes if x.get("ok")]
    index_ok = bool(valid) and len(valid) == len(indexes) and all(
        x.get("trend_ok") and x.get("slope_ok") for x in valid
    )
    breadth_ok = _f(breadth.get("up_ratio")) >= BREADTH_MIN
    pass_ = index_ok and breadth_ok
    if pass_:
        level = "open"
    elif any(x.get("trend_ok") for x in valid) or breadth_ok:
        level = "caution"
    else:
        level = "closed"
    reasons = []
    for x in indexes:
        if not x.get("ok"):
            reasons.append(f"{x.get('name', x.get('code'))} 数据不足")
            continue
        pos = "上方" if x.get("trend_ok") else "下方"
        slope = "向上" if x.get("slope_ok") else "走平/向下"
        reasons.append(f"{x['name']} {x['close']:.2f} 在 MA20 {x['ma20']:.2f} {pos}，MA20 {slope}")
    if breadth.get("ok"):
        reasons.append(f"市场宽度 {_pct(breadth.get('up_ratio'))}（上涨 {breadth.get('up')} / 下跌 {breadth.get('down')}，阈值 {_pct(BREADTH_MIN, 0)}）")
    else:
        reasons.append(breadth.get("reason") or "市场宽度不可用")
    return pass_, level, "；".join(reasons)


def market_gate(db) -> dict:
    indexes = [_index_stats(db, c, n) for c, n in INDEX_BENCHMARKS]
    breadth = _breadth(db)
    pass_, level, reason = _market_verdict(indexes, breadth)
    summary = "；".join(
        f"{x['name']} {x['close']:.0f}/{x['ma20']:.0f}（{'上' if x.get('trend_ok') else '下'}）"
        for x in indexes if x.get("ok")
    )
    if breadth.get("ok"):
        summary += f"；宽度 {_pct(breadth.get('up_ratio'))}"
    dates = sorted({x.get("date") for x in indexes if x.get("ok") and x.get("date")})
    if dates:
        summary += f"（数据 {dates[-1]}）"
    return {
        "id": "market", "label": "市场", "pass": pass_, "level": level,
        "reason": reason, "summary": summary,
        "details": {"indexes": indexes, "breadth": breadth},
    }


# ── 板块闸门 ──────────────────────────────────────────────────────────────

def _load_sector_series(db, names: Optional[set[str]] = None, days: int = FLOW_DAYS) -> dict:
    latest = db.execute(text(
        "SELECT MAX(trade_date) FROM sector_flow_daily WHERE level='l1'"
    )).scalar()
    if not latest:
        return {}
    start = latest - timedelta(days=45)
    rows = db.execute(text("""
        SELECT trade_date, sector_name, main_net, chg_pct, amount
        FROM sector_flow_daily
        WHERE level='l1' AND trade_date BETWEEN :s AND :e
        ORDER BY sector_name, trade_date
    """), {"s": start, "e": latest}).fetchall()
    by: dict[str, list] = defaultdict(list)
    for r in rows:
        name = r[1] or ""
        if names and name not in names:
            continue
        by[name].append({
            "date": r[0], "main": _f(r[2]),
            "chg": _f(r[3]) if r[3] is not None else None,
            "amount": _f(r[4]),
        })
    return {k: v[-days:] for k, v in by.items()}


def _sector_stats(series: list[dict], name: str = "") -> Optional[dict]:
    if not series:
        return None
    main = [x["main"] for x in series]
    chgs = [x["chg"] for x in series if x["chg"] is not None]
    today = main[-1]
    sum3 = sum(main[-3:])
    sum5 = sum(main[-5:])
    sum20 = sum(main[-20:])
    amount = series[-1].get("amount") or 0.0
    streak, sign = 0, 0
    for v in reversed(main):
        if v == 0:
            break
        sg = 1 if v > 0 else -1
        if sign == 0:
            sign = sg
        if sg != sign:
            break
        streak += 1
    # 5 日净流入为正，且不是连续 2 天以上流出，才算板块闸门通过
    pass_ = sum5 > 0 and not (streak >= 2 and sign < 0)
    level = "open" if pass_ else ("caution" if sum5 > 0 else "closed")
    trend = "流入" if sign > 0 else "流出"
    reason = (f"{name or series[-1].get('name', '')} 5日主力 {_money(sum5)}，"
              f"今日 {_money(today)}，连续 {streak} 日{trend}"
              + ("；5日仍为净流出" if sum5 <= 0 else ""))
    return {
        "name": name, "pass": pass_, "level": level,
        "asof": series[-1]["date"].isoformat() if series[-1].get("date") else None,
        "today": round(today, 2), "sum3": round(sum3, 2),
        "sum5": round(sum5, 2), "sum20": round(sum20, 2),
        "amount": round(amount, 2),
        "chg": round(chgs[-1], 2) if chgs else None,
        "streak": streak, "streak_sign": sign,
        "score": round(max(-10.0, min(10.0, sum5 / max(amount, 1.0) * 100)), 2),
        "reason": reason,
    }


def _stock_meta(db, code: str) -> dict:
    row = db.execute(text("""
        SELECT stock_name, industry_l1, stock_type FROM stock_master
        WHERE stock_code=:c AND stock_type IN ('stock', 'etf')
        ORDER BY CASE stock_type WHEN 'stock' THEN 1 ELSE 2 END
        LIMIT 1
    """), {"c": code}).fetchone()
    if not row:
        return {"name": code, "industry": None, "type": None}
    return {"name": row[0] or code, "industry": row[1], "type": row[2]}


def sector_gate(db, code: str) -> dict:
    meta = _stock_meta(db, code)
    industry = meta.get("industry")
    if not industry:
        if meta.get("type") == "etf":
            return {
                "id": "sector", "label": "板块", "pass": True, "level": "skip",
                "reason": "ETF 无行业归属，跳过板块闸门（仍受市场/风险闸门约束）",
                "summary": "ETF：板块闸门跳过",
                "details": {"industry": None, "skipped": True},
            }
        return {
            "id": "sector", "label": "板块", "pass": False, "level": "block",
            "reason": "没有行业归属数据，无法确认板块资金流",
            "summary": "行业归属缺失",
            "details": {"industry": None},
        }
    series = _load_sector_series(db, {industry}).get(industry) or []
    stats = _sector_stats(series, industry)
    if not stats:
        return {
            "id": "sector", "label": "板块", "pass": False, "level": "block",
            "reason": f"{industry} 暂无日频资金流数据",
            "summary": f"{industry}：无资金流数据",
            "details": {"industry": industry},
        }
    return {
        "id": "sector", "label": "板块", "pass": stats["pass"], "level": stats["level"],
        "reason": stats["reason"],
        "summary": f"{industry} 5日 {_money(stats['sum5'])}，连续 {stats['streak']} 日"
                   f"{'流入' if stats['streak_sign'] > 0 else '流出'}"
                   + (f"（数据 {stats['asof']}）" if stats.get("asof") else ""),
        "details": stats,
    }


# ── 个股结构闸门 ──────────────────────────────────────────────────────────

def _load_bars(db, codes: list[str], days: int = 180) -> dict[str, list[dict]]:
    if not codes:
        return {}
    start = date.today() - timedelta(days=300)
    rows = db.execute(text("""
        SELECT stock_code, trade_date, close, close_hfq, high, low, volume
        FROM daily_quote
        WHERE stock_code = ANY(CAST(:codes AS varchar[]))
          AND close > 0 AND trade_date >= :start
        ORDER BY stock_code, trade_date
    """), {"codes": codes, "start": start}).fetchall()
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r[0]].append({
            "trade_date": r[1], "close": _f(r[2]),
            "close_hfq": _f(r[3]) or _f(r[2]),
            "high": _f(r[4]) or _f(r[2]),
            "low": _f(r[5]) or _f(r[2]),
            "volume": _f(r[6]),
        })
    return {k: v[-days:] for k, v in by.items()}


def _none_setup(reason: str) -> dict:
    return {
        "type": "none", "label": "无买点结构", "pass": False, "trigger_ok": False,
        "trigger_source": "none",
        "entry": None, "stop": None, "target": None, "rr": 0.0, "score": 0.0,
        "reason": reason, "summary": reason, "notes": [],
        "chase": False, "broken": False, "price": None,
    }


def detect_setup(bars: list[dict], quote: Optional[dict] = None,
                 max_chase: float = MAX_CHASE) -> dict:
    """从日线识别买点结构。纯函数，便于单测。

    返回的 entry/stop/target 都是“不复权真实价”；entry 是结构确认价（最后一根日线收盘），
    不是现价。现价只用于追高/破位判定和最终风险计算。
    """
    if not bars or len(bars) < MIN_BARS:
        return _none_setup(f"历史数据不足（至少 {MIN_BARS} 根日线）")
    bars = [b for b in bars if _f(b.get("close")) > 0]
    if len(bars) < MIN_BARS:
        return _none_setup("有效日线不足")

    closes = [_f(b.get("close")) for b in bars]
    hfq = [_f(b.get("close_hfq")) or c for b, c in zip(bars, closes)]
    highs = [_f(b.get("high")) or c for b, c in zip(bars, closes)]
    lows = [_f(b.get("low")) or c for b, c in zip(bars, closes)]
    vols = [_f(b.get("volume")) for b in bars]
    if hfq[-1] <= 0:
        return _none_setup("复权价缺失")

    factor = closes[-1] / hfq[-1]
    ma5, ma10, ma20, ma60 = (_ma(hfq, n) for n in (5, 10, 20, 60))
    ma20_prev5 = _ma(hfq[:-5], 20)
    ma20_prev10 = _ma(hfq[:-10], 20)
    ma60_prev10 = _ma(hfq[:-10], 60)
    if None in (ma5, ma10, ma20, ma60, ma20_prev5, ma20_prev10, ma60_prev10):
        return _none_setup("均线数据不足")

    high20 = max(hfq[-21:-1])
    low20 = min(hfq[-21:-1])
    low5 = min(hfq[-5:])
    low10 = min(hfq[-10:])
    prev_low10 = min(hfq[-20:-10])
    vol_avg20 = fmean(vols[-21:-1]) if len(vols) >= 21 else 0.0
    vol_ratio = vols[-1] / vol_avg20 if vol_avg20 > 0 else 0.0

    ma5_r, ma20_r, ma60_r = ma5 * factor, ma20 * factor, ma60 * factor
    high20_r, low20_r = high20 * factor, low20 * factor
    low5_r, low10_r, prev_low10_r = low5 * factor, low10 * factor, prev_low10 * factor
    close_r, prev_close = closes[-1], closes[-2]

    ma20_up = ma20 > ma20_prev5
    above_ma60 = close_r > ma60_r
    near_ma20 = abs(close_r / ma20_r - 1) <= 0.045 if ma20_r > 0 else False
    # 只要求不创 20 日新低；趋势回踩本来就允许贴近 20 日低点
    not_at_low = close_r > low20_r if low20_r > 0 else False
    higher_low = low5_r > prev_low10_r
    range20 = (high20_r - low20_r) / low20_r if low20_r > 0 else 1.0
    prior_down = ma20_prev10 < ma60_prev10

    qprice = _f((quote or {}).get("price"))
    q_vr = _f((quote or {}).get("volume_ratio"))
    q_vwap = _f((quote or {}).get("avg_price"))
    q_date = str((quote or {}).get("trade_date") or "")[:10]
    # 只有今天/未标注日期的实时价才允许触发盘中结构；收盘后/周末不拿旧价冒充盘中
    q_today = bool(qprice > 0 and (not q_date or q_date == date.today().isoformat()))
    q_above_vwap = bool(q_today and q_vwap > 0 and qprice >= q_vwap)
    intraday_vol = q_vr if q_vr > 0 else vol_ratio
    candidates = []

    # 1) 趋势回踩：MA20 向上、价格在 MA60 上方，回踩 MA20 附近后重新站上 MA5
    if above_ma60 and ma20 > ma60 and ma20_up and near_ma20 and not_at_low:
        trigger_daily = close_r > ma5_r and close_r > prev_close
        trigger_intraday = bool(q_today and qprice > ma5_r and qprice > prev_close and q_above_vwap)
        trigger = trigger_daily or trigger_intraday
        source = "daily" if trigger_daily else ("intraday" if trigger_intraday else "none")
        stop = min(low5_r, ma20_r * 0.97) * 0.995
        stop = min(stop, close_r * 0.97)
        r = close_r - stop
        if r > 0:
            target = max(high20_r, close_r + 2.5 * r)
            rr = (target - close_r) / r
            note = ("收盘重新站上 MA5" if source == "daily" else
                    "盘中站上 MA5/VWAP" if source == "intraday" else "等待收盘/盘中站上 MA5")
            candidates.append({
                "type": "trend_pullback", "label": "趋势回踩",
                "trigger_ok": bool(trigger), "trigger_source": source,
                "entry": round(close_r, 2),
                "stop": round(stop, 2), "target": round(target, 2),
                "rr": round(rr, 2), "score": 80 + min(5.0, intraday_vol),
                "notes": ["站上 MA60，MA20 向上", "回踩 MA20 附近未破", note],
            })

    # 2) 底部反转：前期下跌后，低点抬高、放量收复 MA20
    reclaim = close_r > ma20_r or (q_today and qprice > ma20_r and q_above_vwap)
    volume_ok = vol_ratio >= 1.1 or (q_today and q_vr >= 1.1)
    if prior_down and reclaim and higher_low and volume_ok:
        trigger_daily = close_r > ma20_r and close_r > prev_close
        trigger_intraday = bool(q_today and qprice > ma20_r and qprice > prev_close and q_above_vwap)
        trigger = trigger_daily or trigger_intraday
        source = "daily" if trigger_daily else ("intraday" if trigger_intraday else "none")
        stop = min(low10_r, ma20_r * 0.97) * 0.995
        stop = min(stop, close_r * 0.97)
        r = close_r - stop
        if r > 0:
            target = max(ma60_r, high20_r, close_r + 2.5 * r)
            rr = (target - close_r) / r
            candidates.append({
                "type": "bottom_reversal", "label": "底部反转",
                "trigger_ok": bool(trigger), "trigger_source": source,
                "entry": round(close_r, 2),
                "stop": round(stop, 2), "target": round(target, 2),
                "rr": round(rr, 2), "score": 70 + min(5.0, intraday_vol),
                "notes": ["前期 MA20 在 MA60 下方", "近期低点抬高",
                          "收复 MA20" if trigger else "等待收复 MA20"],
            })

    # 3) 突破：横盘收敛后放量突破 20 日高点（日线确认或盘中实时突破）
    daily_break = close_r > high20_r and vol_ratio >= 1.3
    intraday_break = bool(q_today and qprice > high20_r and q_vr >= 1.3 and q_above_vwap)
    above_ma60_now = close_r > ma60_r or (q_today and qprice > ma60_r)
    if range20 <= 0.25 and above_ma60_now and (daily_break or intraday_break):
        # 日线突破后今天又跌回箱体 → 不算有效触发，等重新站上
        hold = (not q_today) or qprice >= high20_r * 0.995
        trigger_daily = daily_break and hold
        trigger = trigger_daily or intraday_break
        source = "daily" if trigger_daily else ("intraday" if intraday_break else "none")
        stop = high20_r * 0.97
        stop = min(stop, close_r * 0.97)
        r = close_r - stop
        if r > 0:
            target = close_r + 3.0 * r
            rr = (target - close_r) / r
            candidates.append({
                "type": "breakout", "label": "放量突破",
                "trigger_ok": bool(trigger), "trigger_source": source,
                "entry": round(close_r, 2),
                "stop": round(stop, 2), "target": round(target, 2),
                "rr": round(rr, 2), "score": 75 + min(5.0, intraday_vol),
                "notes": ["20 日箱体收敛", "放量突破 20 日高点", "站上 MA60"],
            })

    if not candidates:
        return _none_setup("没有趋势回踩 / 底部反转 / 放量突破结构")

    # 优先选已触发且分数最高的结构
    candidates.sort(key=lambda x: (x["trigger_ok"], x["score"]), reverse=True)
    best = candidates[0]
    entry, stop = best["entry"], best["stop"]
    chase = bool(qprice > 0 and entry > 0 and qprice > entry * (1 + max_chase))
    broken = bool(qprice > 0 and stop > 0 and qprice < stop)
    pass_ = bool(best["trigger_ok"] and not chase and not broken and best["rr"] >= RR_MIN)

    if not best["trigger_ok"]:
        reason = "结构在，等触发：收盘或盘中站上 MA5/前收"
    elif chase:
        reason = f"现价 {qprice:.2f} 高于参考买点 {entry:.2f} 超过 {max_chase*100:.0f}%，不追"
    elif broken:
        reason = f"现价 {qprice:.2f} 已跌破止损位 {stop:.2f}"
    elif best["rr"] < RR_MIN:
        reason = f"盈亏比 {best['rr']:.1f} 低于 {RR_MIN:.1f}"
    else:
        reason = "结构触发，价格未追高、未破位"

    best.update({
        "pass": pass_, "reason": reason,
        "summary": (f"{best['label']}：参考买点 {entry:.2f} / 止损 {stop:.2f} / "
                    f"目标 {best['target']:.2f}，盈亏比 {best['rr']:.1f}"
                    + ("（盘中触发）" if best.get("trigger_source") == "intraday" else "")),
        "chase": chase, "broken": broken, "price": qprice or None,
    })
    return best


def setup_gate(db, code: str, quote: Optional[dict] = None) -> dict:
    meta = _stock_meta(db, code)
    bars = _load_bars(db, [code]).get(code, [])
    if quote is None:
        try:
            quote = intraday_quote(db, code)
        except Exception as e:
            logger.warning(f"[buy-check] {code} 实时报价失败: {str(e)[:120]}")
            quote = {}
    setup = detect_setup(bars, quote)
    if not setup.get("price"):
        setup["price"] = bars[-1]["close"] if bars else None
    setup["name"] = meta.get("name") or (quote or {}).get("stock_name") or code
    setup["industry"] = meta.get("industry")
    setup["stock_type"] = meta.get("type")
    level = "ok" if setup["pass"] else ("warn" if setup["type"] != "none" else "block")
    return {
        "id": "setup", "label": "个股结构", "pass": bool(setup["pass"]),
        "level": level, "reason": setup["reason"],
        "summary": setup.get("summary") or setup["reason"],
        "details": setup,
    }


# ── 风险闸门（复用现有交易前检查） ─────────────────────────────────────────

def _call_user(fn, db, user_id, *args, **kwargs):
    """兼容旧调用：user_id=None 时走单用户签名；生产路径始终传 user_id。"""
    if user_id is None:
        return fn(db, *args, **kwargs)
    return fn(db, user_id, *args, **kwargs)


def _risk_gate(db, code: str, entry: float, stop: float, target: float,
               qty: Optional[float] = None,
               user_id: int | None = None) -> dict:
    body = {"stock_code": code, "side": "buy", "quantity": 1,
            "price": entry, "stop_price": stop}
    try:
        probe = _call_user(intraday_check, db, user_id, body)
    except Exception as e:
        logger.warning(f"[buy-check] {code} 风险检查失败: {str(e)[:150]}")
        return {
            "id": "risk", "label": "风险/仓位", "pass": False, "level": "block",
            "reason": f"风险检查失败：{str(e)[:120]}", "summary": "风险检查失败",
            "details": {"max_qty": 0, "violations": [], "rr": 0},
        }

    max_qty = int(probe.get("max_qty") or 0)
    want_qty = float(qty) if qty and float(qty) > 0 else max_qty
    risk = probe
    if want_qty > 0 and want_qty != 1:
        try:
            risk = _call_user(intraday_check, db, user_id,
                              {**body, "quantity": want_qty})
        except Exception as e:
            logger.warning(f"[buy-check] {code} 目标仓位检查失败: {str(e)[:150]}")
            risk = dict(probe)
            risk["allow"] = False
            risk["violations"] = list(probe.get("violations") or []) + [{
                "rule": "risk_check", "level": "block",
                "message": f"目标仓位检查失败：{str(e)[:100]}",
            }]
            risk["advice"] = "目标仓位检查失败，按不通过处理"

    rr = ((target - entry) / (entry - stop)) if entry > stop > 0 and target > 0 else 0.0
    plan_ok = rr >= RR_MIN
    violations = list(risk.get("violations") or [])
    if not plan_ok:
        violations.append({
            "rule": "risk_reward", "level": "block",
            "message": (f"盈亏比 {rr:.1f} < {RR_MIN:.1f}（买点 {entry:.2f} / "
                        f"止损 {stop:.2f} / 目标 {target:.2f}）" if entry > stop > 0 and target > 0
                        else "买点、止损、目标价不完整，无法计算盈亏比"),
        })
    if want_qty <= 0 and not any(v.get("level") == "block" for v in violations):
        violations.append({
            "rule": "max_qty", "level": "block",
            "message": "按当前仓位/现金/风险预算，最多可买 0 股",
        })
    blocked = [v for v in violations if v.get("level") == "block"]
    pass_ = bool(risk.get("allow")) and want_qty > 0 and plan_ok

    if pass_:
        projected = risk.get("projected") or {}
        summary = (f"最多 {max_qty} 股；买完仓位 {_pct(projected.get('position_ratio'))}、"
                   f"单票 {_pct(projected.get('single_weight'))}、"
                   f"止损亏 ¥{_f(projected.get('risk_amount')):,.0f}；"
                   f"T+1：今天买，今天不能止损")
        reason = summary
    else:
        first = blocked[0]["message"] if blocked else (risk.get("advice") or "风险预算不足")
        summary = first
        reason = first

    # 没有可买数量/止损/目标时，probe 的 projected 只是“买 1 股”的估算，不能展示成计划仓位
    projected = (risk.get("projected")
                 if want_qty > 0 and stop > 0 and target > 0 else None)
    return {
        "id": "risk", "label": "风险/仓位", "pass": pass_,
        "level": "ok" if pass_ else "block",
        "reason": reason, "summary": summary,
        "details": {
            "max_qty": max_qty, "qty": want_qty, "rr": round(rr, 2),
            "allow": bool(risk.get("allow")), "projected": projected,
            "violations": violations, "advice": risk.get("advice"),
            "state": risk.get("state"),
        },
    }


# ── 对外：单票检查 / 候选扫描 ─────────────────────────────────────────────

def check(db, code: str, entry: Optional[float] = None,
          stop: Optional[float] = None, target: Optional[float] = None,
          qty: Optional[float] = None, user_id: int | None = None) -> dict:
    code = str(code or "").strip().zfill(6)
    market = market_gate(db)
    sector = sector_gate(db, code)
    setup = setup_gate(db, code)
    det = setup.get("details") or {}

    price = _f(det.get("price"))
    use_entry = _f(entry) or price or _f(det.get("entry"))
    use_stop = _f(stop) or _f(det.get("stop"))
    use_target = _f(target) or _f(det.get("target"))
    risk = _risk_gate(db, code, use_entry, use_stop, use_target, qty,
                      user_id=user_id)

    gates = [market, sector, setup, risk]
    allowed = all(g.get("pass") for g in gates)
    passed = sum(1 for g in gates if g.get("pass"))
    level = "ok" if allowed else ("warn" if passed >= 3 else "block")
    rr = ((use_target - use_entry) / (use_entry - use_stop)
          if use_entry > use_stop > 0 and use_target > 0 else 0.0)

    first_fail = next((g for g in gates if not g.get("pass")), None)
    if allowed:
        advice = (f"四道闸门通过：可以买，最多 {risk['details'].get('max_qty', 0)} 股。"
                  f"参考买点 {use_entry:.2f} / 止损 {use_stop:.2f} / 目标 {use_target:.2f}。"
                  f"注意 T+1：今天买入，今天不能卖出，止损最早明天执行。")
    else:
        advice = f"先别买：{first_fail.get('reason') if first_fail else '闸门未通过'}。"

    return {
        "code": code, "name": det.get("name") or code,
        "industry": det.get("industry"),
        "asof": datetime.now().strftime("%H:%M:%S"),
        "allowed": allowed, "level": level,
        "gates": gates,
        "setup": {
            "type": det.get("type"), "label": det.get("label"),
            "entry": det.get("entry"), "stop": det.get("stop"),
            "target": det.get("target"), "rr": det.get("rr"),
            "trigger_source": det.get("trigger_source"),
            "trigger_ok": det.get("trigger_ok"),
            "pass": det.get("pass"),
            "summary": det.get("summary"),
            "reason": det.get("reason"), "notes": det.get("notes") or [],
        },
        "order": {
            "entry": round(use_entry, 2) if use_entry else None,
            "stop": round(use_stop, 2) if use_stop else None,
            "target": round(use_target, 2) if use_target else None,
            "rr": round(rr, 2), "max_qty": risk["details"].get("max_qty", 0),
        },
        "risk": risk.get("details"),
        "advice": advice,
    }


def _focus_pool(db, user_id: int) -> list[dict]:
    rows = db.execute(text("""
        SELECT DISTINCT ON (fp.stock_code) fp.stock_code, sm.stock_name,
               sm.industry_l1, sm.stock_type
        FROM v_focus_pool fp
        JOIN LATERAL (
            SELECT stock_name, stock_type, industry_l1 FROM stock_master
            WHERE stock_code = fp.stock_code AND stock_type IN ('stock', 'etf')
            ORDER BY CASE stock_type WHEN 'stock' THEN 1 ELSE 2 END
            LIMIT 1
        ) sm ON true
        WHERE fp.user_id=:uid
        ORDER BY fp.stock_code
        LIMIT 50
    """), {"uid": user_id}).fetchall()
    return [{"code": r[0], "name": r[1], "industry": r[2], "type": r[3]} for r in rows]


def _batch_quotes(codes: list[str]) -> dict[str, dict]:
    if not codes:
        return {}
    try:
        raw = realtime_quotes(codes, "stock")
        return {q.get("code"): q for q in raw.values() if q.get("code")}
    except Exception as e:
        logger.warning(f"[buy-check] 批量报价失败: {str(e)[:120]}")
        return {}


def _state_digest(st: dict) -> dict:
    keys = ("asof", "trade_date", "position_ratio", "cash_ratio",
            "target_position_ratio", "allow_new_buy", "advice", "level",
            "remaining_new_buys", "daily_buy_budget")
    return {k: st.get(k) for k in keys}


def _recent_sold_codes(db, st: dict, days: int = 5,
                       user_id: int | None = None) -> set[str]:
    """同日/近期卖出的票不再作为买点候选，避免点进去才发现当日回补/5日回补被拦。"""
    codes = {a.get("stock_code") for a in (st.get("today_actions") or [])
             if a.get("side") == "sell" and a.get("stock_code")}
    if db is None:
        return codes
    cutoff = date.today() - timedelta(days=max(1, int(days)) * 2)
    try:
        rows = db.execute(text("""
            SELECT DISTINCT stock_code FROM trade_slip
            WHERE user_id=:uid AND LOWER(side)='sell' AND trade_date >= :cut
            UNION
            SELECT DISTINCT stock_code FROM trade_intraday
            WHERE user_id=:uid AND LOWER(side)='sell' AND trade_date >= :cut
              AND COALESCE(reconciled, false)=false
        """), {"uid": int(user_id or 0), "cut": cutoff}).fetchall()
        codes |= {r[0] for r in rows if r[0]}
    except Exception:
        pass
    return codes


def candidates(db, limit: int = 8, user_id: int | None = None) -> dict:
    st = _call_user(intraday_state, db, user_id)
    market = market_gate(db)
    digest = _state_digest(st)
    asof = datetime.now().strftime("%H:%M:%S")
    state_ok = bool(st.get("allow_new_buy"))
    market_ok = bool(market.get("pass"))
    allowed = state_ok and market_ok
    if not state_ok:
        gate_reason = st.get("advice") or "当前仓位/纪律不允许开新仓"
    elif not market_ok:
        gate_reason = market.get("reason") or "市场闸门未通过"
    else:
        gate_reason = ""

    pool = _call_user(_focus_pool, db, user_id)
    holdings = {p.get("stock_code") for p in (st.get("positions") or [])}
    no_rebuy = int((st.get("rules") or {}).get("no_rebuy_days") or 5)
    sold_codes = _recent_sold_codes(db, st, no_rebuy, user_id=user_id)
    pool = [p for p in pool if p["code"] not in holdings
            and p["code"] not in sold_codes and p.get("type") == "stock"]
    codes = [p["code"] for p in pool]
    if not codes:
        return {"allowed": allowed, "asof": asof,
                "reason": gate_reason or "关注池没有可扫描的个股",
                "market": market, "state": digest, "items": [], "watch": []}

    bars_map = _load_bars(db, codes)
    quotes = _batch_quotes(codes)
    sector_map = {k: _sector_stats(v, k) for k, v in _load_sector_series(db).items()}

    items, watch = [], []
    for p in pool:
        code = p["code"]
        setup = detect_setup(bars_map.get(code) or [], quotes.get(code))
        if not setup.get("pass"):
            continue
        sec = sector_map.get(p.get("industry") or "")
        if not sec or not sec.get("pass"):
            continue
        price = _f((quotes.get(code) or {}).get("price")) or _f(setup.get("entry"))
        stop = _f(setup.get("stop"))
        target = _f(setup.get("target"))
        if price <= 0 or stop <= 0 or price <= stop:
            continue
        rr_now = (target - price) / (price - stop) if target > price else 0.0
        if rr_now < RR_MIN:
            continue
        max_qty = _max_buy_qty(st, None, price, stop)
        item = {
            "code": code, "name": p.get("name") or code,
            "industry": p.get("industry"),
            "price": round(price, 2),
            "change_pct": (quotes.get(code) or {}).get("change_pct"),
            "setup": {
                "type": setup["type"], "label": setup["label"],
                "entry": setup["entry"], "stop": setup["stop"],
                "target": setup["target"], "rr": round(rr_now, 2),
                "trigger_source": setup.get("trigger_source"),
                "summary": setup.get("summary"),
                "notes": setup.get("notes") or [],
            },
            "sector": {
                "name": sec.get("name"), "sum5": sec.get("sum5"),
                "streak": sec.get("streak"), "reason": sec.get("reason"),
            },
            "max_qty": max_qty,
            "score": round(setup.get("score", 0) + sec.get("score", 0), 1),
            "reason": f"{setup.get('reason')}；{sec.get('reason')}",
            "actionable": bool(allowed and max_qty >= 100),
        }
        if item["actionable"]:
            items.append(item)
        else:
            why = gate_reason or ("按当前仓位/现金，最多可买 0 股" if max_qty < 100 else "")
            why = why.rstrip("。；; ")
            if why:
                item["reason"] += f"；{why}"
            item["reason"] += "；只观察，不下单"
            watch.append(item)

    items.sort(key=lambda x: -x["score"])
    watch.sort(key=lambda x: -x["score"])
    if allowed:
        reason = (f"通过四道闸门的候选 {len(items[:limit])} 只" if items else
                  ("有结构但当前仓位/现金不足，见观察候选" if watch else
                   "当前没有同时通过四道闸门的标的"))
    else:
        reason = gate_reason
    return {
        "allowed": allowed, "asof": asof, "reason": reason,
        "market": market, "state": digest,
        "items": items[:limit] if allowed else [], "watch": watch[:5],
    }
