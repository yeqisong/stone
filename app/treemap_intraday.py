"""分时市值树图。

数据链路：
- 层级/行业归属/股票池：stock_treemap_cache（最近一个 <= 交易日的 mcap 快照）
- 实时价格/涨跌/市值/成交额：新浪全市场快照（分页 100 只，约 55 页）
- 每 10 分钟一个 slot 落库 stock_treemap_intraday；页面按需触发后台拉取，
  不阻塞 HTTP 请求；同一 slot 只拉一次，失败 2 分钟后才重试。

返回结构与 /api/treemap_data 一致（children 树），额外带：
- breadth：涨跌家数/中位数/成交额/涨跌停（近似）/领涨领跌行业
- slots：当日已生成的快照时间
- refreshing：后台正在生成新快照
- 每个节点 detail.d10：近 10 分钟涨跌幅（%）
"""
from __future__ import annotations

import json
import math
import statistics
import threading
import time
from collections import defaultdict
from datetime import date as _date, datetime, time as _time, timedelta, timezone
from typing import Any, Optional

import requests
from loguru import logger
from sqlalchemy import text

from app.db.connection import get_sync_db

_CST = timezone(timedelta(hours=8))
_SINA_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeData")
_PAGE = 100
_FETCH_LOCK = threading.Lock()
_FETCHING: set[tuple[str, str]] = set()
_LAST_TRY: dict[tuple[str, str], float] = {}
_RETRY_SECONDS = 120


def _now() -> datetime:
    return datetime.now(_CST)


def _today() -> _date:
    return _now().date()


def _num(v: Any) -> Optional[float]:
    if v is None or v == "-" or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _slot_of(dt: datetime) -> Optional[str]:
    """当前 10 分钟 slot；未开盘返回 None。"""
    t = dt.time()
    if t < _time(9, 30):
        return None
    if t <= _time(11, 30):
        mins = int((dt - dt.replace(hour=9, minute=30, second=0, microsecond=0)).total_seconds() // 60)
        mins = min(mins, 120)
        slot = dt.replace(hour=9, minute=30, second=0, microsecond=0) + timedelta(minutes=10 * (mins // 10))
        return slot.strftime("%H:%M")
    if t < _time(13, 0):
        return "11:30"
    if t <= _time(15, 0):
        mins = int((dt - dt.replace(hour=13, minute=0, second=0, microsecond=0)).total_seconds() // 60)
        mins = min(mins, 120)
        slot = dt.replace(hour=13, minute=0, second=0, microsecond=0) + timedelta(minutes=10 * (mins // 10))
        return slot.strftime("%H:%M")
    return "15:00"


def _fetch_page(page: int) -> list[dict]:
    params = {
        "page": page, "num": _PAGE, "sort": "symbol", "asc": 1,
        "node": "hs_a", "symbol": "", "_s_r_a": "page",
    }
    r = requests.get(
        _SINA_URL, params=params,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
        timeout=(5, 12),
    )
    r.raise_for_status()
    arr = r.json() or []
    return arr if isinstance(arr, list) else []


def fetch_all_quotes() -> dict[str, dict]:
    """全市场快照 {code: {price,chg,mcap,amount,name}}。新浪分页，约 55 页。"""
    out: dict[str, dict] = {}
    failed = 0
    for page in range(1, 80):
        try:
            arr = _fetch_page(page)
        except Exception as e:
            failed += 1
            logger.warning(f"[treemap-intraday] 新浪第 {page} 页失败: {str(e)[:100]}")
            if failed > 8:
                raise RuntimeError("新浪全市场快照失败页过多")
            time.sleep(0.3)
            continue
        if not arr:
            break
        for it in arr:
            code = str(it.get("code") or "").strip()
            if not code:
                continue
            mcap_wan = _num(it.get("mktcap"))
            out[code.zfill(6)] = {
                "name": it.get("name") or code,
                "price": _num(it.get("trade")) or None,
                "chg": _num(it.get("changepercent")),
                # 新浪 mktcap 单位：万元；缓存 value 单位：元
                "mcap": (mcap_wan * 1e4) if mcap_wan else None,
                "amount": _num(it.get("amount")),
                "turnover": _num(it.get("turnoverratio")),
                "open": _num(it.get("open")),
                "high": _num(it.get("high")),
                "low": _num(it.get("low")),
                "prev_close": _num(it.get("settlement")),
            }
        if len(arr) < _PAGE:
            break
        time.sleep(0.08)
    return out


def _cache_rows(db, day: _date) -> tuple[Optional[_date], list[dict]]:
    d = db.execute(text("""
        SELECT MAX(trade_date) FROM stock_treemap_cache
        WHERE metric='mcap' AND trade_date <= :d
    """), {"d": day}).scalar()
    if not d:
        return None, []
    rows = db.execute(text("""
        SELECT parent, node_id, name, value, chg_pct, node_type, detail
        FROM stock_treemap_cache
        WHERE trade_date=:d AND metric='mcap'
    """), {"d": d}).mappings().fetchall()
    return d, [dict(r) for r in rows]


def _stock_codes(db, day: _date) -> list[str]:
    d = db.execute(text("""
        SELECT MAX(trade_date) FROM stock_treemap_cache
        WHERE metric='mcap' AND trade_date <= :d
    """), {"d": day}).scalar()
    if not d:
        return []
    rows = db.execute(text("""
        SELECT node_id FROM stock_treemap_cache
        WHERE trade_date=:d AND metric='mcap' AND node_type='stock'
    """), {"d": d}).fetchall()
    return [r[0] for r in rows]


def _store_snapshot(db, day: _date, slot: str, quotes: dict[str, dict]) -> int:
    codes = _stock_codes(db, day)
    if not codes:
        return 0
    rows = []
    for code in codes:
        q = quotes.get(code)
        if not q:
            continue
        rows.append({
            "d": day, "s": slot, "c": code,
            "p": q.get("price"), "chg": q.get("chg"),
            "m": q.get("mcap"), "a": q.get("amount"),
            "o": q.get("open"), "h": q.get("high"), "l": q.get("low"),
        })
    if not rows:
        return 0
    db.execute(text("""
        INSERT INTO stock_treemap_intraday
            (trade_date, slot, stock_code, price, chg_pct, mcap, amount, open, high, low)
        VALUES (:d, :s, :c, :p, :chg, :m, :a, :o, :h, :l)
        ON CONFLICT (trade_date, slot, stock_code) DO UPDATE SET
            price=EXCLUDED.price, chg_pct=EXCLUDED.chg_pct,
            mcap=EXCLUDED.mcap, amount=EXCLUDED.amount,
            open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low
    """), rows)
    # 只保留最近 5 个自然日的分时快照
    db.execute(text("""
        DELETE FROM stock_treemap_intraday WHERE trade_date < :cut
    """), {"cut": day - timedelta(days=5)})
    db.commit()
    return len(rows)


def _ensure_async(day: _date, slot: str) -> bool:
    key = (day.isoformat(), slot)
    now = time.time()
    with _FETCH_LOCK:
        if key in _FETCHING:
            return False
        if now - _LAST_TRY.get(key, 0) < _RETRY_SECONDS:
            return False
        _FETCHING.add(key)
        _LAST_TRY[key] = now

    def run():
        db = None
        try:
            t0 = time.time()
            quotes = fetch_all_quotes()
            db = get_sync_db()
            n = _store_snapshot(db, day, slot, quotes)
            logger.info(f"[treemap-intraday] {day} {slot} 快照 {n} 只，耗时 {time.time()-t0:.1f}s")
        except Exception as e:
            logger.warning(f"[treemap-intraday] {day} {slot} 采集失败: {str(e)[:160]}")
        finally:
            if db is not None:
                db.close()
            with _FETCH_LOCK:
                _FETCHING.discard(key)

    threading.Thread(target=run, daemon=True, name=f"tm-intraday-{slot}").start()
    return True


def _snapshot(db, day: _date, slot: str) -> dict[str, dict]:
    rows = db.execute(text("""
        SELECT stock_code, price, chg_pct, mcap, amount, open, high, low
        FROM stock_treemap_intraday WHERE trade_date=:d AND slot=:s
    """), {"d": day, "s": slot}).mappings().fetchall()
    return {r["stock_code"]: dict(r) for r in rows}


def _prev_snapshot(db, day: _date, slot: str) -> tuple[dict[str, dict], Optional[str]]:
    prev = db.execute(text("""
        SELECT MAX(slot) FROM stock_treemap_intraday
        WHERE trade_date=:d AND slot < :s
    """), {"d": day, "s": slot}).scalar()
    if not prev:
        return {}, None
    rows = db.execute(text("""
        SELECT stock_code, price, amount FROM stock_treemap_intraday
        WHERE trade_date=:d AND slot=:s
    """), {"d": day, "s": prev}).mappings().fetchall()
    return {r["stock_code"]: dict(r) for r in rows}, prev


def _slot_minutes(slot: Optional[str]) -> Optional[int]:
    try:
        h, m = str(slot).split(":")[:2]
        return int(h) * 60 + int(m)
    except (TypeError, ValueError):
        return None


def _slot_gap_min(prev: Optional[str], cur: Optional[str]) -> Optional[int]:
    """两个快照之间的交易分钟数（跨午休不把 90 分钟算进去）。"""
    a, b = _slot_minutes(prev), _slot_minutes(cur)
    if a is None or b is None or b < a:
        return None
    gap = b - a
    if a <= 11 * 60 + 30 < b:
        gap -= 90
    return max(0, gap)


def _in_collect_window(dt: datetime) -> bool:
    t = dt.time()
    return (_time(9, 25) <= t <= _time(11, 35)
            or _time(12, 55) <= t <= _time(15, 5))


async def intraday_collect_loop():
    """交易时段每 30s 检查当前 10 分钟 slot；缺失就后台拉全市场。

    这样即使页面没开，也会连续生成 09:30…15:00 的快照，d10/资金流不会跨 30 分钟。
    """
    import asyncio
    logger.info("[treemap-intraday] 10分钟快照采集任务启动")
    while True:
        try:
            now = _now()
            if _in_collect_window(now):
                db = get_sync_db()
                try:
                    day = now.date()
                    if _is_trade_day(db, day):
                        slot = _slot_of(now)
                        if slot:
                            exists = db.execute(text("""
                                SELECT 1 FROM stock_treemap_intraday
                                WHERE trade_date=:d AND slot=:s LIMIT 1
                            """), {"d": day, "s": slot}).scalar()
                            if not exists:
                                _ensure_async(day, slot)
                finally:
                    db.close()
        except Exception as e:
            logger.warning(f"[treemap-intraday] 自动采集检查失败: {str(e)[:160]}")
        await asyncio.sleep(30)


def _is_trade_day(db, day: _date) -> bool:
    row = db.execute(text("""
        SELECT is_trade_day FROM trade_calendar WHERE cal_date=:d LIMIT 1
    """), {"d": day}).fetchone()
    if row is not None:
        return bool(row[0])
    return day.weekday() < 5


def _detail(raw) -> dict:
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _limit_pct(code: str) -> float:
    return 19.8 if code.startswith(("300", "301", "688", "689")) else 9.8


def _build(cache_rows: list[dict], snap: dict[str, dict], prev_snap: dict[str, dict]) -> dict:
    by_id = {r["node_id"]: r for r in cache_rows}
    stocks: list[dict] = []
    for r in cache_rows:
        if r["node_type"] != "stock":
            continue
        code = r["node_id"]
        s = snap.get(code)
        s = s or {}
        value = _num(s.get("mcap")) or float(r["value"] or 0)
        # _snapshot() 的列名是 chg_pct，不是 chg；读错会把日线缓存里的昨日涨跌当成今日
        chg = _num(s.get("chg_pct"))
        price = _num(s.get("price"))
        if chg is None:
            # 快照缺失/停牌：按 0 处理，不能回填昨日涨跌
            chg = 0.0
        prev_price = _num((prev_snap.get(code) or {}).get("price"))
        d10 = ((price / prev_price - 1) * 100) if price and prev_price else 0.0
        detail = _detail(r.get("detail"))
        detail["d10"] = round(d10, 2)
        stocks.append({
            "id": code, "name": r["name"] or code, "value": round(value, 2),
            "chg_pct": round(chg, 2), "type": "stock", "detail": detail,
            "parent": r["parent"], "amount": _num(s.get("amount")) or 0.0,
        })

    # 二级行业聚合
    l2_map: dict[str, dict] = {}
    for s in stocks:
        l2 = by_id.get(s["parent"])
        if not l2 or l2["node_type"] != "l2":
            continue
        node = l2_map.setdefault(s["parent"], {
            "id": s["parent"], "name": l2["name"], "value": 0.0,
            "w": 0.0, "d10w": 0.0, "type": "l2", "children": [],
            "parent": l2["parent"],
        })
        node["value"] += s["value"]
        node["w"] += s["value"] * s["chg_pct"]
        node["d10w"] += s["value"] * s["detail"].get("d10", 0)
        node["children"].append(s)
    for node in l2_map.values():
        v = node["value"] or 1.0
        node["chg_pct"] = round(node["w"] / v, 2)
        node["detail"] = {"d10": round(node["d10w"] / v, 2)}
        node.pop("w", None)
        node.pop("d10w", None)

    # 一级行业聚合
    l1_map: dict[str, dict] = {}
    for l2 in l2_map.values():
        l1 = by_id.get(l2.get("parent"))
        if not l1 or l1["node_type"] != "l1":
            continue
        node = l1_map.setdefault(l1["node_id"], {
            "id": l1["node_id"], "name": l1["name"], "value": 0.0,
            "w": 0.0, "d10w": 0.0, "type": "l1", "children_l2": [],
        })
        node["value"] += l2["value"]
        node["w"] += l2["value"] * l2["chg_pct"]
        node["d10w"] += l2["value"] * l2["detail"].get("d10", 0)
        node["children_l2"].append(l2)
    for node in l1_map.values():
        v = node["value"] or 1.0
        node["chg_pct"] = round(node["w"] / v, 2)
        node["detail"] = {"d10": round(node["d10w"] / v, 2)}
        node.pop("w", None)
        node.pop("d10w", None)

    # 全量视图：l1 → 个股（扁平，与日线 API 一致）
    l1_out = []
    for l1 in sorted(l1_map.values(), key=lambda x: -x["value"]):
        all_stocks = []
        for l2 in l1["children_l2"]:
            all_stocks.extend(l2["children"])
        l1_out.append({
            "id": l1["id"], "name": l1["name"], "value": round(l1["value"], 2),
            "chg_pct": l1["chg_pct"], "type": "l1", "detail": l1["detail"],
            "children": sorted(all_stocks, key=lambda x: -x["value"]),
        })
    return {"l1_out": l1_out, "l1_map": l1_map, "l2_map": l2_map, "stocks": stocks}


def _breadth(stocks: list[dict], l1_map: dict[str, dict], snap: dict[str, dict]) -> dict:
    chgs = [s["chg_pct"] for s in stocks if s["chg_pct"] is not None]
    up = sum(1 for x in chgs if x > 0)
    down = sum(1 for x in chgs if x < 0)
    flat = len(chgs) - up - down
    limit_up = sum(1 for s in stocks if s["chg_pct"] >= _limit_pct(s["id"]))
    limit_down = sum(1 for s in stocks if s["chg_pct"] <= -_limit_pct(s["id"]))
    amount = sum(_num(v.get("amount")) or 0 for v in snap.values())
    l1s = sorted(l1_map.values(), key=lambda x: x["chg_pct"], reverse=True)
    return {
        "total": len(stocks),
        "up": up, "down": down, "flat": flat,
        "median": round(statistics.median(chgs), 2) if chgs else 0,
        "amount": round(amount, 2),
        "limit_up": limit_up, "limit_down": limit_down,
        "top_l1": [{"id": x["id"], "name": x["name"], "chg_pct": x["chg_pct"]} for x in l1s[:3]],
        "bottom_l1": [{"id": x["id"], "name": x["name"], "chg_pct": x["chg_pct"]} for x in l1s[-3:][::-1]],
    }


def _main_net_by_l1(db, day: _date, stock_l1: dict[str, str]) -> tuple[dict[str, float], Optional[_date]]:
    """tushare 主力净流入（大单+特大单）按一级行业汇总，单位元。

    这是收盘后数据（moneyflow DAG 节点），不是盘中实时；返回实际数据日期，
    前端据此标注“收盘后/上一交易日”，避免和实时压力估算混淆。
    """
    d = db.execute(text(
        "SELECT MAX(trade_date) FROM stock_moneyflow WHERE trade_date <= :d"
    ), {"d": day}).scalar()
    if not d:
        return {}, None
    rows = db.execute(text("""
        SELECT stock_code,
               COALESCE(buy_lg_amt,0) + COALESCE(buy_elg_amt,0)
               - COALESCE(sell_lg_amt,0) - COALESCE(sell_elg_amt,0)
        FROM stock_moneyflow WHERE trade_date=:d
    """), {"d": d}).fetchall()
    out: dict[str, float] = defaultdict(float)
    for code, main_wan in rows:
        l1 = stock_l1.get(code)
        if l1:
            out[l1] += float(main_wan or 0) * 1e4  # 万元 -> 元
    return dict(out), d


def _sector_flows(db, cache_rows: list[dict], snap: dict[str, dict],
                  prev_snap: dict[str, dict], built: dict, day: _date) -> dict:
    """行业资金流。

    - main：tushare 主力净流入（大单+特大单，收盘后，单位元）
    - day：Σ 成交额 × CLV（收盘在当日振幅中的位置，[-1,1]）——实时压力估算
    - m10：Σ 相邻两档成交额增量 × 价格方向——近10分钟压力估算
    绝对压力值是“买卖压力强度”，不是净流出金额；UI 分档标注。
    """
    by_id = {r["node_id"]: r for r in cache_rows}
    stock_l1: dict[str, str] = {}
    for r in cache_rows:
        if r["node_type"] != "stock":
            continue
        p = by_id.get(r["parent"])
        if not p:
            continue
        if p["node_type"] == "l1":
            stock_l1[r["node_id"]] = p["node_id"]
        elif p["node_type"] == "l2":
            l1 = by_id.get(p["parent"])
            if l1 and l1["node_type"] == "l1":
                stock_l1[r["node_id"]] = l1["node_id"]

    main_map, main_date = _main_net_by_l1(db, day, stock_l1)
    flows: dict[str, dict] = {}
    for l1id, node in built["l1_map"].items():
        flows[l1id] = {"id": l1id, "name": node["name"], "flow_day": 0.0,
                       "flow_10m": 0.0, "amount": 0.0, "chg_pct": node["chg_pct"], "n": 0,
                       "main_net": round(main_map.get(l1id, 0.0), 2)}
    for code, s in snap.items():
        l1id = stock_l1.get(code)
        if not l1id or l1id not in flows:
            continue
        f = flows[l1id]
        amount = _num(s.get("amount")) or 0.0
        price = _num(s.get("price"))
        high = _num(s.get("high"))
        low = _num(s.get("low"))
        chg = _num(s.get("chg_pct")) or 0.0
        if high and low and high > low and price:
            clv = (2 * price - high - low) / (high - low)
        else:
            clv = chg / 3.0
        clv = max(-1.0, min(1.0, clv))
        f["flow_day"] += amount * clv
        f["amount"] += amount
        f["n"] += 1
        prev = prev_snap.get(code) or {}
        pamt = _num(prev.get("amount"))
        pprice = _num(prev.get("price"))
        if pamt and pprice and amount > pamt and price:
            dret = price / pprice - 1
            direction = max(-1.0, min(1.0, dret / 0.005))
            f["flow_10m"] += (amount - pamt) * direction

    def _rows(key):
        rows = sorted((dict(x) for x in flows.values()), key=lambda x: -x[key])
        for x in rows:
            x["flow_day"] = round(x["flow_day"], 2)
            x["flow_10m"] = round(x["flow_10m"], 2)
            x["amount"] = round(x["amount"], 2)
        return rows

    return {"day": _rows("flow_day"), "m10": _rows("flow_10m"),
            "has_prev": bool(prev_snap),
            "main_date": main_date.isoformat() if main_date else None}


def get_tree(db, trade_date: str, parent: str = "", slot: str = "latest") -> dict:
    try:
        day = datetime.strptime(trade_date, "%Y-%m-%d").date()
    except Exception:
        day = _today()
    slots = [r[0] for r in db.execute(text("""
        SELECT DISTINCT slot FROM stock_treemap_intraday
        WHERE trade_date=:d ORDER BY slot
    """), {"d": day}).fetchall()]

    now = _now()
    target = None
    if slot and slot != "latest":
        target = slot
    elif day == _today():
        target = _slot_of(now)
    elif slots:
        target = slots[-1]

    refreshing = False
    if target and target not in slots and day == _today() and _is_trade_day(db, day):
        if _slot_of(now) is not None:
            _ensure_async(day, target)
            # 正在拉取（或刚触发）都要告诉前端“生成中”，否则轮询会误显示“暂无”
            with _FETCH_LOCK:
                refreshing = (day.isoformat(), target) in _FETCHING

    effective = target if target in slots else (slots[-1] if slots else None)
    cache_day, cache_rows = _cache_rows(db, day)
    if not effective or not cache_rows:
        return {
            "trade_date": day.isoformat(), "slot": effective, "target_slot": target,
            "slots": slots, "refreshing": refreshing, "quote_ok": bool(effective),
            "children": [], "breadth": None, "sector_flows": None,
            "slot_gap_min": None,
            "message": ("正在生成首张分时快照…" if refreshing else
                        "该日期暂无分时快照（仅保留最近 5 个交易日）"),
        }

    snap = _snapshot(db, day, effective)
    prev, prev_slot = _prev_snapshot(db, day, effective)
    gap_min = _slot_gap_min(prev_slot, effective)
    built = _build(cache_rows, snap, prev)
    breadth = _breadth(built["stocks"], built["l1_map"], snap)
    breadth["asof"] = effective
    breadth["cache_date"] = cache_day.isoformat() if cache_day else None
    breadth["slot_gap_min"] = gap_min
    sector_flows = _sector_flows(db, cache_rows, snap, prev, built, day)
    sector_flows["slot"] = effective
    sector_flows["slot_gap_min"] = gap_min

    if not parent:
        children = built["l1_out"]
    else:
        l2 = built["l2_map"].get(parent)
        l1 = built["l1_map"].get(parent)
        if l2:
            children = sorted(l2["children"], key=lambda x: -x["value"])
        elif l1:
            children = sorted(l1["children_l2"], key=lambda x: -x["value"])
        else:
            children = []

    return {
        "trade_date": day.isoformat(), "slot": effective, "target_slot": target,
        "slots": slots, "refreshing": refreshing, "quote_ok": bool(snap),
        "children": children, "breadth": breadth,
        "sector_flows": sector_flows, "slot_gap_min": gap_min,
    }
