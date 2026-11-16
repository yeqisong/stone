"""行业资金流日频聚合与多日趋势。

数据源：
- 行业归属/股票池：stock_treemap_cache（最近一个 <= 交易日的 mcap 快照；行业稳定，可套用到更早日期）
- 日线：daily_quote（close/high/low/amount，amount 单位元）
- 主力净流入：stock_moneyflow（buy_lg/buy_elg - sell_lg/sell_elg，单位万元）

口径：
- main_net：tushare 大单+特大单净额（收盘后，元）
- net_mf：tushare net_mf_amt（全部，元）
- pressure：Σ 成交额 × CLV（收盘在当日振幅中的位置，元）——买卖压力估算
- amount：行业成交额（元）
- chg_pct：行业内个股涨跌幅等权平均（%）
"""
from __future__ import annotations

from datetime import date as _date, timedelta
from typing import Optional

from loguru import logger
from sqlalchemy import text


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _prev_trade_day(db, day: _date) -> Optional[_date]:
    return db.execute(text("""
        SELECT MAX(cal_date) FROM trade_calendar
        WHERE is_trade_day=true AND cal_date < :d
    """), {"d": day}).scalar()


def load_hierarchy(db, day: _date) -> tuple[dict[str, str], dict[str, str], Optional[_date]]:
    """返回 (stock_code -> l1_id, l1_id -> l1_name, cache_date)。"""
    d = db.execute(text("""
        SELECT MAX(trade_date) FROM stock_treemap_cache
        WHERE metric='mcap' AND trade_date <= :d
    """), {"d": day}).scalar()
    if not d:
        return {}, {}, None
    rows = db.execute(text("""
        SELECT node_id, parent, node_type, name
        FROM stock_treemap_cache WHERE trade_date=:d AND metric='mcap'
    """), {"d": d}).fetchall()
    by_id = {r[0]: r for r in rows}
    stock_l1: dict[str, str] = {}
    l1_names: dict[str, str] = {}
    for r in rows:
        code, parent, ntype, name = r
        if ntype == "l1":
            l1_names[code] = name
        elif ntype == "stock":
            p = by_id.get(parent)
            if not p:
                continue
            if p[2] == "l1":
                stock_l1[code] = p[0]
            elif p[2] == "l2":
                l1 = by_id.get(p[1])
                if l1 and l1[2] == "l1":
                    stock_l1[code] = l1[0]
    return stock_l1, l1_names, d


def build_day(db, day: _date, stock_l1: dict[str, str], l1_names: dict[str, str]) -> int:
    """聚合单个交易日；返回写入的行业数。"""
    codes = list(stock_l1)
    if not codes:
        return 0
    dq = db.execute(text("""
        SELECT stock_code, close, high, low, amount
        FROM daily_quote
        WHERE trade_date=:d AND stock_code = ANY(CAST(:codes AS varchar[]))
    """), {"d": day, "codes": codes}).fetchall()
    if len(dq) < 500:
        logger.warning(f"[sector-flow] {day} 日线覆盖不足（{len(dq)}），跳过")
        return 0
    mf = db.execute(text("""
        SELECT stock_code,
               COALESCE(buy_lg_amt,0) + COALESCE(buy_elg_amt,0)
               - COALESCE(sell_lg_amt,0) - COALESCE(sell_elg_amt,0),
               COALESCE(net_mf_amt,0)
        FROM stock_moneyflow
        WHERE trade_date=:d AND stock_code = ANY(CAST(:codes AS varchar[]))
    """), {"d": day, "codes": codes}).fetchall()
    if len(mf) < 500:
        logger.warning(f"[sector-flow] {day} 资金流覆盖不足（{len(mf)}），跳过")
        return 0
    prev_day = _prev_trade_day(db, day)
    prev = {}
    if prev_day:
        prev = {r[0]: _f(r[1]) for r in db.execute(text("""
            SELECT stock_code, close FROM daily_quote
            WHERE trade_date=:d AND stock_code = ANY(CAST(:codes AS varchar[]))
        """), {"d": prev_day, "codes": codes}).fetchall()}

    mf_map = {r[0]: (_f(r[1]) * 1e4, _f(r[2]) * 1e4) for r in mf}  # 万元 -> 元
    agg = {
        l1: {"main": 0.0, "net": 0.0, "amount": 0.0, "pressure": 0.0,
             "up": 0, "down": 0, "n": 0, "chg_sum": 0.0, "chg_n": 0}
        for l1 in l1_names
    }
    for code, close, high, low, amount in dq:
        l1 = stock_l1.get(code)
        if not l1 or l1 not in agg:
            continue
        close, high, low, amount = _f(close), _f(high), _f(low), _f(amount)
        a = agg[l1]
        a["amount"] += amount
        a["n"] += 1
        pc = prev.get(code)
        if pc and close:
            chg = close / pc - 1
            a["chg_sum"] += chg
            a["chg_n"] += 1
            if chg > 0:
                a["up"] += 1
            elif chg < 0:
                a["down"] += 1
        clv = 0.0
        if high > low and close:
            clv = (2 * close - high - low) / (high - low)
            clv = max(-1.0, min(1.0, clv))
        a["pressure"] += amount * clv
        main, net = mf_map.get(code, (0.0, 0.0))
        a["main"] += main
        a["net"] += net

    rows = []
    for l1, a in agg.items():
        if a["n"] <= 0:
            continue
        rows.append({
            "d": day, "lvl": "l1", "sid": l1, "sname": l1_names.get(l1, l1),
            "main": round(a["main"], 2), "net": round(a["net"], 2),
            "amount": round(a["amount"], 2), "pressure": round(a["pressure"], 2),
            "chg": round(a["chg_sum"] / a["chg_n"] * 100, 2) if a["chg_n"] else None,
            "up": a["up"], "down": a["down"], "n": a["n"],
        })
    if not rows:
        return 0
    db.execute(text("""
        INSERT INTO sector_flow_daily
            (trade_date, level, sector_id, sector_name, main_net, net_mf, amount,
             pressure, chg_pct, up_count, down_count, stock_count)
        VALUES (:d, :lvl, :sid, :sname, :main, :net, :amount, :pressure, :chg, :up, :down, :n)
        ON CONFLICT (trade_date, level, sector_id) DO UPDATE SET
            sector_name=EXCLUDED.sector_name, main_net=EXCLUDED.main_net,
            net_mf=EXCLUDED.net_mf, amount=EXCLUDED.amount, pressure=EXCLUDED.pressure,
            chg_pct=EXCLUDED.chg_pct, up_count=EXCLUDED.up_count,
            down_count=EXCLUDED.down_count, stock_count=EXCLUDED.stock_count,
            updated_at=CURRENT_TIMESTAMP
    """), rows)
    return len(rows)


def build_range(db, start: _date, end: _date) -> int:
    dates = [r[0] for r in db.execute(text("""
        SELECT cal_date FROM trade_calendar
        WHERE is_trade_day=true AND cal_date BETWEEN :s AND :e
        ORDER BY cal_date
    """), {"s": start, "e": end}).fetchall()]
    if not dates:
        return 0
    stock_l1, l1_names, cache_date = load_hierarchy(db, end)
    if not stock_l1:
        logger.warning(f"[sector-flow] 无行业层级缓存（<= {end}）")
        return 0
    logger.info(f"[sector-flow] 回补 {dates[0]} ~ {dates[-1]}，{len(dates)} 天，层级 {cache_date}")
    total = 0
    for i, d in enumerate(dates, 1):
        n = build_day(db, d, stock_l1, l1_names)
        total += n
        if i % 5 == 0:
            db.commit()
            logger.info(f"[sector-flow] {i}/{len(dates)} 天，累计 {total} 行")
    db.commit()
    logger.info(f"[sector-flow] 完成，{total} 行")
    return total


def _latest_complete_flow_day(db) -> Optional[_date]:
    return db.execute(text("""
        SELECT trade_date FROM stock_moneyflow
        WHERE trade_date >= (SELECT MAX(trade_date) FROM stock_moneyflow) - INTERVAL '15 days'
        GROUP BY trade_date HAVING COUNT(*) >= 4000
        ORDER BY trade_date DESC LIMIT 1
    """)).scalar()


def ensure_recent(db, max_days: int = 5) -> int:
    """趋势接口按需补齐最近几天；历史大缺口用 CLI 回补。"""
    latest_data = _latest_complete_flow_day(db)
    if not latest_data:
        return 0
    latest_row = db.execute(text("SELECT MAX(trade_date) FROM sector_flow_daily")).scalar()
    if latest_row and latest_row >= latest_data:
        return 0
    if latest_row:
        start = latest_row + timedelta(days=1)
    else:
        start = latest_data - timedelta(days=max_days)
    # 最多补 max_days 个交易日，避免接口被大缺口拖住
    dates = [r[0] for r in db.execute(text("""
        SELECT cal_date FROM trade_calendar
        WHERE is_trade_day=true AND cal_date BETWEEN :s AND :e
        ORDER BY cal_date DESC LIMIT :n
    """), {"s": start, "e": latest_data, "n": max_days}).fetchall()]
    if not dates:
        return 0
    dates.sort()
    stock_l1, l1_names, cache_date = load_hierarchy(db, _date.today())
    if not stock_l1:
        return 0
    n = 0
    for d in dates:
        n += build_day(db, d, stock_l1, l1_names)
    db.commit()
    logger.info(f"[sector-flow] 按需补齐 {dates[0]} ~ {dates[-1]}，{n} 行")
    return n


def history(db, days: int = 60, level: str = "l1", sector: str = "") -> dict:
    ensure_recent(db, max_days=5)
    dates = [r[0] for r in db.execute(text("""
        SELECT DISTINCT trade_date FROM sector_flow_daily
        WHERE level=:lvl ORDER BY trade_date DESC LIMIT :n
    """), {"lvl": level, "n": days}).fetchall()]
    dates.reverse()
    if not dates:
        return {"dates": [], "sectors": [], "top": [], "message": "暂无日频资金流数据，请先回补"}
    rows = db.execute(text("""
        SELECT trade_date, sector_id, sector_name, main_net, net_mf, amount,
               pressure, chg_pct, up_count, down_count, stock_count
        FROM sector_flow_daily
        WHERE level=:lvl AND trade_date BETWEEN :start AND :end
        ORDER BY trade_date
    """), {"lvl": level, "start": dates[0], "end": dates[-1]}).fetchall()
    by_sector = {}
    for r in rows:
        sid = r[1]
        s = by_sector.setdefault(sid, {"id": sid, "name": r[2], "data": {}})
        s["name"] = r[2] or s["name"]
        s["data"][r[0]] = {
            "main": _f(r[3]), "net": _f(r[4]), "amount": _f(r[5]),
            "pressure": _f(r[6]), "chg": _f(r[7]) if r[7] is not None else None,
            "up": int(r[8] or 0), "down": int(r[9] or 0), "n": int(r[10] or 0),
        }
    # 每日排名（主力净流入降序，1=流入最大）
    ranks: dict[_date, dict[str, int]] = {}
    for d in dates:
        items = sorted(
            ((sid, s["data"].get(d, {}).get("main", 0.0)) for sid, s in by_sector.items()),
            key=lambda kv: -kv[1],
        )
        ranks[d] = {sid: i + 1 for i, (sid, _) in enumerate(items)}
    out = []
    for sid, s in by_sector.items():
        series = [s["data"].get(d, {}).get("main", 0.0) for d in dates]
        cum, run = [], 0.0
        for v in series:
            run += v
            cum.append(round(run, 2))
        last = s["data"].get(dates[-1], {})
        streak = 0
        sign = 0
        for v in reversed(series):
            if v == 0:
                break
            sg = 1 if v > 0 else -1
            if sign == 0:
                sign = sg
            if sg != sign:
                break
            streak += 1
        prev_date = dates[-2] if len(dates) > 1 else None
        r_today = ranks.get(dates[-1], {}).get(sid)
        r_prev = ranks.get(prev_date, {}).get(sid) if prev_date else None
        out.append({
            "id": sid, "name": s["name"],
            "series": [round(v, 2) for v in series],
            "cumulative": cum,
            "today": round(series[-1], 2),
            "sum5": round(sum(series[-5:]), 2),
            "sum20": round(sum(series[-20:]), 2),
            "streak": streak, "streak_sign": sign,
            "rank_today": r_today, "rank_prev": r_prev,
            "rank_change": (r_prev - r_today) if r_prev and r_today else None,
            "chg_pct": last.get("chg"),
            "up_count": last.get("up", 0), "down_count": last.get("down", 0),
            "amount": last.get("amount", 0.0),
        })
    out.sort(key=lambda x: -abs(x["sum20"]))
    top = [x["id"] for x in out[:8]]
    if sector and sector not in top:
        top = ([sector] + top)[:8]
    return {
        "dates": [d.isoformat() for d in dates],
        "sectors": out,
        "top": top,
        "level": level,
    }
