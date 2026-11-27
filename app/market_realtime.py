"""实时大盘聚合（实时大盘分析页 /api/market/realtime 的后端）。

数据源与频率：
- 宽度/成交额聚合：新浪全市场快照（复用 treemap_intraday.fetch_all_quotes，约 8-25s，
  0.08s/页），只留内存不落库——10 分钟 slot 落库口径仍归 treemap_intraday，互不干扰
- 指数分时：腾讯 minute/query 当日逐分钟（价+累计量额+昨收，crawler/adapters/
  tencent_adapter.minute_trend）。东财 push2his 的 trends2 已弃用：2026-11-27 晚实测
  对非命令行-curl 的 TLS 指纹差别对待（requests/curl_cffi 全超时、压缩流中断），
  详见 design/13「数据底座实测」
- 量能对比：新浪 5 分钟 K（sh000001/sz399106，600 根 ≈ 12 交易日，含 amount），
  今日/昨日/近4日同期累计曲线 + 全天额预估
- 涨停梯队：东财 push2ex 涨停池+炸板池；交易日 15:05 后幂等落库 zt_pool_daily
- 板块雷达：东财 push2 clist 行业板块（东财 2024 分类，一级+细分混排 ~496 个）
- 大盘资金分时：东财 push2 fflow/kline（secid2 聚合沪深两市，当日累计净流入）
- 情绪实时分：复用 market_sentiment.get（自带 60s 缓存，四成分实时口径）
- 当日情绪轨迹：stock_treemap_intraday slot 重放 + market_sentiment._verdict，
  above_count/index_count 传 0 → 三成分口径（VWAP 项缺省 10 分），与日频恐慌指数
  序列可比（v3.22 口径决策，详见 design/13）

并发与节流：refresh_all() 只在后台 daemon 线程跑（东财 9 请求经 em_get 全局闸门
≈ 12s/周期 + 新浪 sweep 8-25s + 腾讯 4 请求 <3s，周期 60s）；HTTP 端点只读内存
缓存 + slot 重放 SQL，不触发外部请求；缓存为空（后端刚启动/收盘后）时端点触发
一次后台刷新（treemap _ensure_async 同款页面按需模式）。收集窗口复用 treemap 的
09:25–15:05；收盘后 loop 只做日终落库检查，因此 15:05 后重启也能补当日 zt_pool_daily。
"""
from __future__ import annotations

import asyncio
import json
import statistics
import threading
import time
from datetime import date as _date, datetime, time as _time
from typing import Optional

import requests
from loguru import logger
from sqlalchemy import text

from app import market_sentiment
from app.db.connection import get_sync_db
from app.treemap_intraday import (
    _in_collect_window,
    _is_trade_day,
    _limit_pct,
    _now,
    fetch_all_quotes,
)
from crawler import em_quote
from crawler.adapters.tencent_adapter import minute_trend

# 分时展示的指数（腾讯 bare code；399106 深证综指只用于两市量能，不上图）
_TREND_INDICES = ["000001", "399001", "399006", "000300"]
# 量能：沪市全口径（上证综指）+ 深市全口径（深证综指），新浪 5 分钟 K
_TURNOVER_SINA = ["sh000001", "sz399106"]

_SINA_KLINE_URL = ("https://quotes.sina.cn/cn/api/json_v2.php/"
                   "CN_MarketDataService.getKLineData")

# 展示用涨停池上限（按 连板数 desc、封单额 desc 排序后截断；全量日终落库不截）
_POOL_CAP = 150

# ── 内存缓存 ────────────────────────────────────────────────────────────────
_CACHE: dict[str, dict] = {}   # key -> {"data":…, "ts": float, "date": str|None}
_LOCK = threading.Lock()
_SLOTS = {"date": None, "ts": 0.0, "data": None}   # slot 轨迹 60s 小缓存
_SLEEP_S = 60.0                                     # loop 周期（strategy_config 可调）
_REFRESH_LOCK = threading.Lock()
_LAST_REFRESH_TRY = [0.0]


def _cache_put(key: str, data, day: Optional[str] = None) -> None:
    with _LOCK:
        _CACHE[key] = {"data": data, "ts": time.time(), "date": day}


def _cache_get(key: str):
    with _LOCK:
        item = _CACHE.get(key)
        return item["data"] if item else None


def _cache_ts(key: str) -> Optional[float]:
    with _LOCK:
        item = _CACHE.get(key)
        return item["ts"] if item else None


def _cache_empty() -> bool:
    with _LOCK:
        return not _CACHE


def ensure_refresh_async() -> bool:
    """缓存为空（后端刚启动/收盘重启）时后台触发一次刷新；90s 内只试一次。

    treemap_intraday._ensure_async 同款页面按需模式：端点不阻塞，刷新完成后
    下次轮询自然拿到数据。返回是否真正触发了刷新。
    """
    with _REFRESH_LOCK:
        if time.time() - _LAST_REFRESH_TRY[0] < 90:
            return False
        _LAST_REFRESH_TRY[0] = time.time()

    def _run():
        try:
            refresh_all()
        except Exception as e:
            logger.warning(f"[market-rt] 按需刷新失败: {str(e)[:140]}")

    threading.Thread(target=_run, daemon=True, name="mrt-ondemand").start()
    return True


# ── 配置（strategy_config.market_realtime，缺省即启用 60s）─────────────────────

def read_config(db) -> dict:
    row = db.execute(text(
        "SELECT params, enabled FROM strategy_config WHERE strategy_name='market_realtime' "
        "ORDER BY id DESC LIMIT 1"
    )).fetchone()
    params = {}
    if row is not None and row[0]:
        try:
            params = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0])
        except Exception:
            params = {}
    enabled = bool(row[1]) if row is not None else True
    return {"enabled": enabled, "interval_s": int(params.get("interval_s") or 60)}


# ── 聚合纯函数（单测目标）───────────────────────────────────────────────────

def aggregate_breadth(quotes: dict[str, dict]) -> dict:
    """新浪全市场快照 → 宽度聚合（涨跌家数/中位数/涨跌停/两市成交额，元）。

    停牌或无有效行情（price<=0 / chg 缺失）的个股不进任何分母。
    涨跌停阈值复用 treemap_intraday._limit_pct 口径（300/301/688/689 → 19.8）。
    """
    up = down = flat = limit_up = limit_down = 0
    chgs: list[float] = []
    amount_sum = 0.0
    for code, q in quotes.items():
        price = q.get("price") or 0
        chg = q.get("chg")
        if price <= 0 or chg is None:
            continue
        chgs.append(chg)
        if chg > 0:
            up += 1
        elif chg < 0:
            down += 1
        else:
            flat += 1
        pct = _limit_pct(code)
        if chg >= pct:
            limit_up += 1
        elif chg <= -pct:
            limit_down += 1
        amount_sum += q.get("amount") or 0.0
    total = len(chgs)
    return {
        "total": total,
        "up": up, "down": down, "flat": flat,
        "up_ratio": round(up / total, 4) if total else None,
        "median": round(statistics.median(chgs), 3) if chgs else None,
        "limit_up": limit_up, "limit_down": limit_down,
        "amount": round(amount_sum, 2),   # 元
    }


def fetch_sina_min5(symbol: str, datalen: int = 600) -> list[dict]:
    """新浪 5 分钟 K（含 amount）：[{day:'YYYY-MM-DD HH:MM:SS', close, volume, amount}]。

    新浪与 treemap 同源直连（requests），600 根 ≈ 12 个交易日，量能同期对比用。
    """
    r = requests.get(
        _SINA_KLINE_URL,
        params={"symbol": symbol, "scale": "5", "ma": "no", "datalen": datalen},
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
        timeout=(3, 12),
    )
    r.raise_for_status()
    rows = r.json() or []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"新浪 5 分钟K 无数据: {symbol}")
    return rows


def _merge_min5_buckets(rows_list: list[list[dict]]) -> dict[str, list[dict]]:
    """多市场新浪 5 分钟行按桶对齐相加 → {date: [{t, amount, volume}]}。

    不同市场桶数一致（同一交易时段），缺失桶按 0 处理；t 取 'HH:MM'。
    """
    by_date: dict[str, dict[str, dict]] = {}
    for rows in rows_list:
        for r in rows:
            d, t = r["day"].split(" ")
            bucket = by_date.setdefault(d, {})
            cur = bucket.setdefault(t[:5], {"t": t[:5], "amount": 0.0, "volume": 0.0})
            cur["amount"] += float(r.get("amount") or 0)
            cur["volume"] += float(r.get("volume") or 0)
    return {d: list(buckets.values()) for d, buckets in sorted(by_date.items())}


def estimate_turnover(min5_merged: dict[str, list[dict]]) -> Optional[dict]:
    """两市成交额与全天预估（新浪 5 分钟 amount 逐段累计，沪+深合计）。

    est_full = 当前累计 × (近4日日均全天额 / 近4日同期均值)；同期缺失退化为
    线性外推（累计 × 48 / 已收桶数）。
    """
    days = sorted(min5_merged.keys())
    if len(days) < 2:
        return None
    today = days[-1]
    prev = days[:-1][-4:]
    if not prev:
        return None

    def cum(day: str) -> list[float]:
        out, s = [], 0.0
        for b in min5_merged[day]:
            s += b["amount"]
            out.append(s)
        return out

    today_c = cum(today)
    prev_cs = [cum(d) for d in prev]
    prev_cs = [c for c in prev_cs if c]
    idx = len(today_c) - 1
    yday_c = prev_cs[-1]
    avg_prev_same = statistics.mean(c[min(idx, len(c) - 1)] for c in prev_cs)
    avg_prev_full = statistics.mean(c[-1] for c in prev_cs)
    today_cum = today_c[-1]
    est, est_basis = None, None
    if avg_prev_same and avg_prev_same > 0:
        est, est_basis = today_cum * avg_prev_full / avg_prev_same, "prev_same"
    else:
        buckets = min(len(today_c), 48) or 48
        est, est_basis = today_cum * 48 / buckets, "linear"

    labels = [b["t"] for b in min5_merged[today]]
    series = [
        {"t": labels[i] if i < len(labels) else f"#{i}",
         "today": round(today_c[i], 2),
         "yday": round(yday_c[i], 2) if yday_c and i < len(yday_c) else None,
         "avg_prev": round(statistics.mean(c[i] for c in prev_cs if i < len(c)), 2)}
        for i in range(len(today_c))
    ]
    return {
        "date": today,
        "asof": labels[-1] if labels else None,
        "today_cum": round(today_cum, 2),
        "yday_same": round(yday_c[min(idx, len(yday_c) - 1)], 2) if yday_c else None,
        "avg_prev_same": round(avg_prev_same, 2),
        "avg_prev_full": round(avg_prev_full, 2),
        "est_full": round(est, 2) if est else None,
        "est_basis": est_basis,
        "series": series,
    }


def zt_summary(zt: dict) -> dict:
    """涨停池 → 梯队摘要（家数/炸板率/连板高度/天梯）。"""
    pool = zt.get("pool") or []
    broken = zt.get("broken_pool") or []
    lu, brk = len(pool), len(broken)
    rate = round(brk / (lu + brk), 4) if (lu + brk) else None
    max_lbc = max((p.get("lbc") or 1) for p in pool) if pool else None
    by_lbc: dict[int, list[dict]] = {}
    for p in pool:
        by_lbc.setdefault(p.get("lbc") or 1, []).append(p)
    ladder = []
    for lbc in sorted(by_lbc, reverse=True):
        members = sorted(by_lbc[lbc], key=lambda x: x.get("fund") or 0, reverse=True)
        ladder.append({
            "lbc": lbc, "n": len(members),
            "names": [m.get("name") for m in members[:6]],
            "codes": [m.get("code") for m in members[:6]],
        })
    pool_sorted = sorted(pool, key=lambda x: (x.get("lbc") or 1, x.get("fund") or 0),
                         reverse=True)
    return {
        "date": zt.get("date"),
        "limit_up": lu, "broken": brk, "broken_rate": rate, "max_lbc": max_lbc,
        "ladder": ladder,
        "pool": pool_sorted[:_POOL_CAP],
        "pool_truncated": len(pool_sorted) > _POOL_CAP,
    }


def senti_track_from_slots(db, trade_date) -> list[dict]:
    """stock_treemap_intraday 逐 slot 重放三成分情绪分（与日频恐慌指数序列可比）。

    涨跌停阈值按代码前缀区分（300/301/688/689 → 19.8），与 treemap_intraday 一致。
    """
    rows = db.execute(text("""
        SELECT slot,
               COUNT(*) AS total,
               SUM(CASE WHEN chg_pct > 0 THEN 1 ELSE 0 END) AS up,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY chg_pct) AS median,
               SUM(CASE WHEN chg_pct >= (CASE WHEN stock_code ~ '^(300|301|688|689)'
                                              THEN 19.8 ELSE 9.8 END) THEN 1 ELSE 0 END) AS limit_up,
               SUM(CASE WHEN chg_pct <= -(CASE WHEN stock_code ~ '^(300|301|688|689)'
                                               THEN 19.8 ELSE 9.8 END) THEN 1 ELSE 0 END) AS limit_down
        FROM stock_treemap_intraday
        WHERE trade_date = :d
        GROUP BY slot ORDER BY slot
    """), {"d": trade_date}).mappings().fetchall()
    out: list[dict] = []
    for r in rows:
        total = int(r["total"] or 0)
        if not total:
            continue
        up_ratio = float(r["up"] or 0) / total
        median = float(r["median"]) if r["median"] is not None else None
        lu, ld = int(r["limit_up"] or 0), int(r["limit_down"] or 0)
        level, score, _ = market_sentiment._verdict(up_ratio, median, lu, ld, 0, 0)
        out.append({
            "slot": r["slot"], "score": score, "level": level,
            "up_ratio": round(up_ratio, 4), "median": median,
            "limit_up": lu, "limit_down": ld,
        })
    return out


# ── 日终落库（幂等）────────────────────────────────────────────────────────

def archive_zt_daily(db, now: Optional[datetime] = None) -> bool:
    """交易日 15:05 后把当日涨停梯队写 zt_pool_daily（已有当日行则跳过）。

    优先用内存缓存；缓存不是当日（如 16:00 重启）则一次性补拉 push2ex 历史接口。
    """
    now = now or _now()
    day = now.date()
    if now.time() < _time(15, 5):
        return False
    if not _is_trade_day(db, day):
        return False
    ymd = day.strftime("%Y%m%d")
    exists = db.execute(text(
        "SELECT 1 FROM zt_pool_daily WHERE trade_date=:d"
    ), {"d": day}).scalar()
    if exists:
        return False
    zt = _cache_get("zt")
    if not zt or zt.get("date") != ymd:
        try:
            zt = em_quote.fetch_zt_pools(ymd)
            _cache_put("zt", zt, ymd)
        except Exception as e:
            logger.warning(f"[market-rt] 日终涨停池补拉失败: {str(e)[:140]}")
            return False
    pool = zt.get("pool") or []
    broken = zt.get("broken_pool") or []
    lu, brk = len(pool), len(broken)
    breadth = _cache_get("breadth")
    ld = breadth.get("limit_down") if isinstance(breadth, dict) else None
    db.execute(text("""
        INSERT INTO zt_pool_daily
            (trade_date, limit_up, limit_down, broken, broken_rate, max_lbc, pool, broken_pool)
        VALUES (:d, :lu, :ld, :brk, :rate, :maxlbc, CAST(:pool AS jsonb), CAST(:bp AS jsonb))
        ON CONFLICT (trade_date) DO NOTHING
    """), {
        "d": day, "lu": lu, "ld": ld, "brk": brk,
        "rate": round(brk / (lu + brk), 4) if (lu + brk) else None,
        "maxlbc": max((p.get("lbc") or 1) for p in pool) if pool else None,
        "pool": json.dumps(pool, ensure_ascii=False),
        "bp": json.dumps(broken, ensure_ascii=False),
    })
    db.commit()
    logger.info(f"[market-rt] 日终梯队落库 {day} 涨停{lu} 炸板{brk}")
    return True


# ── 后台刷新 ───────────────────────────────────────────────────────────────

def refresh_all() -> list[str]:
    """串行刷新全部数据源到内存缓存；单源失败不影响其他。返回成功 key 列表。"""
    ok: list[str] = []
    t0 = time.time()
    # 1. 新浪全市场 → 宽度
    try:
        _cache_put("breadth", aggregate_breadth(fetch_all_quotes()))
        ok.append("breadth")
    except Exception as e:
        logger.warning(f"[market-rt] 全市场宽度失败: {str(e)[:140]}")
    # 2. 指数分时 ×4（腾讯，快）
    try:
        trends = {c: minute_trend(c, security_type="index") for c in _TREND_INDICES}
        _cache_put("trends", trends)
        ok.append("trends")
    except Exception as e:
        logger.warning(f"[market-rt] 指数分时失败: {str(e)[:140]}")
    # 3. 量能：新浪 5 分钟 K（沪综指+深综指）
    try:
        rows_list = [fetch_sina_min5(sym) for sym in _TURNOVER_SINA]
        _cache_put("min5", _merge_min5_buckets(rows_list))
        ok.append("min5")
    except Exception as e:
        logger.warning(f"[market-rt] 量能 5 分钟线失败: {str(e)[:140]}")
    # 4. 涨停/炸板池（东财 push2ex）
    ymd = _now().strftime("%Y%m%d")
    try:
        _cache_put("zt", em_quote.fetch_zt_pools(ymd), ymd)
        ok.append("zt")
    except Exception as e:
        logger.warning(f"[market-rt] 涨停池失败: {str(e)[:140]}")
    # 5. 行业板块（东财 push2 clist）
    try:
        _cache_put("boards", em_quote.fetch_board_list())
        ok.append("boards")
    except Exception as e:
        logger.warning(f"[market-rt] 行业板块失败: {str(e)[:140]}")
    # 6. 大盘资金分时（东财 push2 fflow）
    try:
        _cache_put("fflow", em_quote.fetch_market_fflow())
        ok.append("fflow")
    except Exception as e:
        logger.warning(f"[market-rt] 大盘资金失败: {str(e)[:140]}")
    logger.info(f"[market-rt] 刷新完成 {ok} 耗时 {time.time() - t0:.1f}s")
    return ok


def _cycle(now: Optional[datetime] = None) -> None:
    """后台线程单周期：交易窗口内刷新缓存 + 幂等日终落库。"""
    now = now or _now()
    db = get_sync_db()
    try:
        cfg = read_config(db)
        global _SLEEP_S
        _SLEEP_S = max(30, int(cfg.get("interval_s") or 60))
        if not cfg.get("enabled", True):
            return
        if not _is_trade_day(db, now.date()):
            return
        if _in_collect_window(now):
            refresh_all()
        archive_zt_daily(db, now)
    finally:
        db.close()


async def market_realtime_loop():
    """交易时段周期刷新（daemon 线程内做同步 HTTP/DB）；收盘后只做落库检查。"""
    logger.info("[market-rt] 实时大盘采集任务启动")
    while True:
        try:
            await asyncio.to_thread(_cycle)
        except Exception as e:
            logger.warning(f"[market-rt] 周期异常: {str(e)[:160]}")
        await asyncio.sleep(_SLEEP_S)


# ── 端点装配 ───────────────────────────────────────────────────────────────

_SESSION_LABEL = {
    "pre": "集合竞价", "open": "交易中", "lunch": "午间休市",
    "post": "已收盘", "closed": "休市",
}


def _session_of(now: datetime, trade_day: bool) -> str:
    if not trade_day:
        return "closed"
    t = now.time()
    if t < _time(9, 15):
        return "closed"
    if t < _time(9, 30):
        return "pre"
    if t <= _time(11, 30):
        return "open"
    if t < _time(13, 0):
        return "lunch"
    if t <= _time(15, 0):
        return "open"
    return "post"


def _slots_track(db, day: _date) -> list[dict]:
    now = time.time()
    if _SLOTS["date"] == day and now - _SLOTS["ts"] < 60:
        return _SLOTS["data"]
    data = senti_track_from_slots(db, day)
    _SLOTS.update(date=day, ts=now, data=data)
    return data


def get_realtime(db) -> dict:
    """装配端点响应；全部读内存缓存 + 库内 slot 重放，不阻塞等待外部请求。

    缓存为空（后端刚启动/收盘重启）时后台触发一次按需刷新（refreshing=true），
    前端下个轮询周期自然拿到数据。
    """
    now = _now()
    day = now.date()
    db_trade_day = _is_trade_day(db, day)
    session = _session_of(now, db_trade_day)

    breadth = _cache_get("breadth")
    trends = _cache_get("trends") or {}
    min5 = _cache_get("min5") or {}
    zt = _cache_get("zt")
    boards = _cache_get("boards") or []
    fflow = _cache_get("fflow") or []

    # stale：交易窗口内核心源缺失或超 3 个周期未更新
    core_ts = [t for t in (_cache_ts(k) for k in ("breadth", "trends", "zt")) if t]
    in_window = _in_collect_window(now) and db_trade_day
    if in_window:
        stale = (not core_ts) or (time.time() - max(core_ts) > 180)
    else:
        stale = (not core_ts)
    refreshing = False
    if not (_cache_get("breadth") and _cache_get("trends") and _cache_get("zt")
            and _cache_get("boards") and _cache_get("fflow") and _cache_get("min5")):
        # 任一核心源缺失（后端刚启动/收盘重启只补了单源）→ 后台整刷一次；
        # ensure_refresh_async 自带 90s 节流，不会随轮询连发
        refreshing = ensure_refresh_async()

    # 指数分时（腾讯当日数据；昨收取 minute_trend.preClose）
    indices_trend = []
    for c in _TREND_INDICES:
        tr = trends.get(c) or {}
        points = tr.get("points") or []
        pc = tr.get("preClose")
        last_px = tr.get("last") or (points[-1].get("price") if points else None)
        indices_trend.append({
            "code": c,
            "name": tr.get("name") or c,
            "preClose": pc,
            "last": last_px,
            "chg_pct": (round(((last_px / pc) - 1) * 100, 2)
                        if last_px and pc else None),
            "trend": [{"t": p.get("t"), "price": p.get("price")} for p in points],
        })

    try:
        senti = market_sentiment.get(db)
    except Exception as e:
        logger.warning(f"[market-rt] 实时情绪失败: {str(e)[:120]}")
        senti = {}

    asof = time.strftime("%H:%M:%S", time.localtime(max(core_ts))) if core_ts else None
    return {
        "ok": True,
        "date": day.isoformat(),
        "session": session,
        "session_label": _SESSION_LABEL[session],
        "asof": asof,
        "stale": stale,
        "refreshing": refreshing,
        "breadth": breadth,
        "senti": {k: senti[k] for k in ("score", "label", "level", "advice", "stale", "asof")
                  if k in senti},
        "senti_track": _slots_track(db, day),
        "indices_trend": indices_trend,
        "turnover": estimate_turnover(min5),
        "zt": zt_summary(zt) if zt else None,
        "boards": sorted(boards, key=lambda b: b.get("chg_pct") or 0, reverse=True),
        "fflow": {
            "series": [{"t": r.get("t"), "main": r.get("main"),
                        "big": r.get("big"), "huge": r.get("huge")} for r in fflow],
            "latest": (fflow[-1].get("main") if fflow else None),
        },
    }


def zt_history(db, days: int = 120) -> dict:
    """连板高度/涨跌停家数/炸板率历史（zt_pool_daily，日终快照）。"""
    rows = db.execute(text("""
        SELECT trade_date, limit_up, limit_down, broken, broken_rate, max_lbc
        FROM zt_pool_daily ORDER BY trade_date DESC LIMIT :n
    """), {"n": int(days)}).fetchall()
    out = []
    for r in reversed(rows):
        out.append({
            "date": str(r[0]), "limit_up": r[1], "limit_down": r[2],
            "broken": r[3], "broken_rate": float(r[4]) if r[4] is not None else None,
            "max_lbc": r[5],
        })
    return {"days": out, "count": len(out)}
