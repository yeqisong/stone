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
from datetime import date as _date, datetime, time as _time, timedelta
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

# 刷新互斥与节拍（采集 loop 与页面按需刷新共用一把锁）：
# running=此刻有 refresh_all 在跑；last_start/last_done=最近一次刷新的起止时刻
_REFRESH_LOCK = threading.Lock()
_REFRESH: dict = {"running": False, "last_start": 0.0, "last_done": 0.0}
# 按需刷新的最小间隔：采集窗口内 loop 每 60s 刷，超过它还没动静说明 loop 没在工作；
# 窗口外 loop 不刷，页面触发的整刷（55 页新浪 + 9 次东财）至多 10 分钟一次
_ONDEMAND_GAP_IN_WINDOW_S = 180.0
_ONDEMAND_GAP_OUT_WINDOW_S = 600.0


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


def _refresh_try_begin() -> bool:
    """抢占刷新权：同一时刻最多一个 refresh_all 在跑（返回 False=已有刷新在跑）。"""
    with _REFRESH_LOCK:
        if _REFRESH["running"]:
            return False
        _REFRESH["running"] = True
        _REFRESH["last_start"] = time.time()
        return True


def _refresh_end() -> None:
    with _REFRESH_LOCK:
        _REFRESH["running"] = False
        _REFRESH["last_done"] = time.time()


def ensure_refresh_async(now: Optional[datetime] = None) -> bool:
    """采集 loop 覆盖不到的时候，由页面访问在后台补刷一次（不阻塞请求）。

    - 采集窗口内（交易日 09:25–15:05）由 market_realtime_loop 周期刷新，这里只在
      loop 超过 3 分钟没启动过刷新时才补（刚重启 / loop 卡死）；
    - 窗口外 loop 不刷新，这里补，但两次按需刷新至少间隔 10 分钟：某个源持续失败
      时，不会因为页面开着就反复整刷；
    - 已有刷新在跑时不重复触发。
    返回是否真正触发了刷新。
    """
    now = now or _now()
    min_gap = (_ONDEMAND_GAP_IN_WINDOW_S if _in_collect_window(now)
               else _ONDEMAND_GAP_OUT_WINDOW_S)
    with _REFRESH_LOCK:
        if _REFRESH["running"] or time.time() - _REFRESH["last_start"] < min_gap:
            return False

    def _run():
        db = get_sync_db()
        try:
            refresh_all(db)
        except Exception as e:
            logger.warning(f"[market-rt] 按需刷新失败: {str(e)[:140]}")
        finally:
            db.close()

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

# 东财当日涨停池从连续竞价开始后才算「今天的数据」；此前请求今天只会拿到上一交易日
_POOL_TODAY_FROM = _time(9, 31)


def _pool_trade_date(db, now: datetime) -> _date:
    """涨停池应当请求的交易日：今天已进入连续竞价用今天，否则回退到上一个交易日。

    东财 push2ex 对「尚无数据的日期」（盘前、休市日、未来日）静默返回最近一个
    交易日的池，返回体里的 qdate 恒为最新交易日、无法据此识别（2026-11-28 实测：
    请求 20261128 / 20261130 均返回 11-27 的 417 家）。所以只能请求确定已经有
    数据的日期，缓存里的 date 才是数据真实所属的交易日。
    """
    d = now.date()
    if not (_is_trade_day(db, d) and now.time() >= _POOL_TODAY_FROM):
        d -= timedelta(days=1)
    for _ in range(10):
        if _is_trade_day(db, d):
            return d
        d -= timedelta(days=1)
    return d


def refresh_all(db=None, now: Optional[datetime] = None) -> list[str]:
    """串行刷新全部数据源到内存缓存；单源失败不影响其他。返回成功 key 列表。

    db 缺省时自开一个短会话（按需刷新线程用），只用来查交易日历。
    采集 loop 与页面按需刷新都会调到这里：同一时刻只允许一个在跑，撞上的那个
    直接返回 []（不排队——下一拍 loop 自己会再刷）。
    """
    now = now or _now()
    if db is None:
        db = get_sync_db()
        try:
            pool_day = _pool_trade_date(db, now)
        finally:
            db.close()
    else:
        pool_day = _pool_trade_date(db, now)
    if not _refresh_try_begin():
        logger.info("[market-rt] 已有刷新在跑，跳过本次")
        return []
    try:
        return _refresh_sources(pool_day)
    finally:
        _refresh_end()


def _refresh_sources(pool_day: _date) -> list[str]:
    """真正去拉六个源（调用方已持有刷新权）。"""
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
    # 4. 涨停/炸板池（东财 push2ex）——请求「确定已有数据」的交易日，见 _pool_trade_date
    ymd = pool_day.strftime("%Y%m%d")
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


# ── 盘面异动告警（design/13 批次三）────────────────────────────────────────
# 指标全部来自内存缓存（refresh_all 产物），评估零新增外部请求；
# 冷却/上限 DB-backed（risk_alerts user_id IS NULL + kind mk_%），重启不重发。
# kind 预算 VARCHAR(20)：mk_idx_surge/mk_idx_drop/mk_breadth_up/mk_breadth_dn/
# mk_height/mk_panic/mk_strong。

DEFAULT_MARKET_ALERTS = {
    "enabled": True,
    "index_window_min": 15,    # 指数急变回看窗口（分钟）
    "index_move_pct": 1.2,     # 窗口涨跌触发阈值 %
    "index_buffer_pct": 0.4,   # 回到 ±buffer 内复位（滞回，允许同向重复触发）
    "breadth_high": 0.65,      # 涨占比上穿
    "breadth_low": 0.35,       # 涨占比下穿
    "breadth_buffer": 0.05,    # 滞回缓冲
    "height_min": 5,           # 连板高度告警阈值（当日首次）
    "cooldown_min": {          # per-kind 冷却（分钟）
        "mk_idx_surge": 120, "mk_idx_drop": 120,
        "mk_breadth_up": 180, "mk_breadth_dn": 180,
        "mk_height": 240, "mk_panic": 240, "mk_strong": 240,
    },
    "family_cooldown_min": 10, # 任意 mk% 之间的最小间隔（防风暴）
    "max_per_day": 15,
}
ALERT_CONFIG_KEY = "market_alerts"

_MK_LABEL = {
    "mk_idx_surge": "指数急涨", "mk_idx_drop": "指数急跌",
    "mk_breadth_up": "宽度转强", "mk_breadth_dn": "宽度转弱",
    "mk_height": "连板高度新高", "mk_panic": "盘面恐慌", "mk_strong": "盘面亢奋",
}

# 评估状态（单进程内存；跨重启靠 DB 冷却兜底；_cycle 串行调用无并发）
_MSTATE_KEYS = ("day", "idx_side", "breadth_zone", "breadth_seen",
                "height_day", "sent_level")
_mstate_lock = threading.Lock()
_mstate: dict = dict.fromkeys(_MSTATE_KEYS)


def _mstate_reset(day=None) -> None:
    """状态机复位到「当天尚未评估过」（跨日时由 evaluate 调用；测试用）。"""
    with _mstate_lock:
        _mstate.update(dict.fromkeys(_MSTATE_KEYS), day=day)


def read_market_alerts(db) -> dict:
    """strategy_config.market_alerts 读取（缺行 = 默认全开）。"""
    row = db.execute(text(
        "SELECT params, enabled FROM strategy_config WHERE strategy_name='market_alerts' "
        "ORDER BY id DESC LIMIT 1"
    )).fetchone()
    params = {}
    if row is not None and row[0]:
        try:
            params = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0])
        except Exception:
            params = {}
    enabled = bool(row[1]) if row is not None else True
    merged = {**DEFAULT_MARKET_ALERTS, **params}
    merged["cooldown_min"] = {**DEFAULT_MARKET_ALERTS["cooldown_min"],
                              **(params.get("cooldown_min") or {})}
    return {**merged, "enabled": enabled}


def _mk_recent(db, kind: Optional[str], minutes: float, day) -> bool:
    """近 minutes 分钟内同 kind（或任意 mk% 当 kind=None）是否已告警（DB 冷却）。"""
    cut = _now() - timedelta(minutes=minutes)
    if kind:
        row = db.execute(text(
            "SELECT 1 FROM risk_alerts WHERE user_id IS NULL AND kind=:k "
            "AND created_at >= :cut LIMIT 1"
        ), {"k": kind, "cut": cut}).scalar()
    else:
        row = db.execute(text(
            "SELECT 1 FROM risk_alerts WHERE user_id IS NULL AND kind LIKE 'mk%' "
            "AND created_at >= :cut LIMIT 1"
        ), {"cut": cut}).scalar()
    return row is not None


def _mk_today_count(db, day) -> int:
    return int(db.execute(text(
        "SELECT count(*) FROM risk_alerts WHERE user_id IS NULL "
        "AND trade_date=:d AND kind LIKE 'mk%'"
    ), {"d": day}).scalar() or 0)


def _mk_insert(db, now: datetime, kind: str, level: str, title: str, body: str) -> dict:
    row = db.execute(text(
        "INSERT INTO risk_alerts "
        "(user_id, trade_date, kind, stock_code, level, title, body, created_at) "
        "VALUES (NULL, :d, :k, NULL, :lv, :t, :b, :ts) RETURNING id, created_at"
    ), {"d": now.date(), "k": kind, "lv": level, "t": title[:100], "b": body,
        "ts": now}).fetchone()
    return {"id": row[0], "kind": kind, "level": level, "title": title[:100],
            "body": body, "created_at": str(row[1])[:19] if row[1] else None}


def evaluate_market_alerts(db, now: Optional[datetime] = None) -> list[dict]:
    """盘面异动评估（交易窗口内每周期调用）；返回本次落库的告警列表。

    状态机惯例照搬 intraday_watch：side 状态切换报一次 + 缓冲复位（指数），
    zone 切换 + 滞回（宽度），当日一次（高度），跨级（情绪）。
    两条通用规则（v3.27 审查项 3）：
    - 状态只在告警**落库后**前移。被冷却/当日上限拦下的信号不丢：状态留在原处，
      下个周期条件仍满足就重试；
    - 当天首轮观测只建基线、不报（宽度、情绪）：重启后或开盘第一分钟看到的
      「已经在高位」不是盘中转折。
    """
    now = now or _now()
    day = now.date()
    cfg = read_market_alerts(db)
    if not cfg.get("enabled", True):
        return []
    # 只在连续竞价时段评估：采集窗口从 09:25 起，但 09:25–09:30 拿到的是集合竞价
    # 或上一交易日的回退值，不是盘中异动（_cycle 已保证 day 是交易日）
    if _session_of(now, trade_day=True) != "open":
        return []
    ymd = now.strftime("%Y%m%d")
    with _mstate_lock:
        if _mstate.get("day") != day:   # 跨日重置状态机
            _mstate.update(dict.fromkeys(_MSTATE_KEYS), day=day)

    out: list[dict] = []

    def fire(kind: str, level: str, title: str, body: str) -> bool:
        """过冷却/上限后落库，返回是否落库（调用方据此决定是否前移状态）。"""
        if _mk_today_count(db, day) >= int(cfg.get("max_per_day", 15)):
            return False
        cd = int((cfg.get("cooldown_min") or {}).get(kind, 60))
        if _mk_recent(db, kind, cd, day):
            return False
        if _mk_recent(db, None, int(cfg.get("family_cooldown_min", 10)), day):
            return False
        out.append(_mk_insert(db, now, kind, level, title, body))
        return True

    # 1. 指数急涨/急跌（上证综指，trends 缓存分钟序列，side 切换 + 滞回）
    #    分时必须就是今天的：腾讯盘前/休市日返回的是上一交易日的整段分时
    trends = _cache_get("trends") or {}
    idx_tr = trends.get("000001") or {}
    idx_points = idx_tr.get("points") or []
    win = int(cfg.get("index_window_min", 15))
    move = float(cfg.get("index_move_pct", 1.2))
    buf = float(cfg.get("index_buffer_pct", 0.4))
    if str(idx_tr.get("date") or "") == ymd and len(idx_points) > win:
        cur = idx_points[-1].get("price")
        base = idx_points[-1 - win].get("price")
        if cur and base:
            chg = (cur / base - 1) * 100
            with _mstate_lock:
                prev = _mstate.get("idx_side")
            # 阈值外进 up/down；回到 ±buffer 内复位；两者之间保持原状态
            side = ("up" if chg >= move else "down" if chg <= -move else
                    None if abs(chg) <= buf else prev)
            if side in ("up", "down") and side != prev:
                kind = "mk_idx_surge" if side == "up" else "mk_idx_drop"
                bd = _cache_get("breadth") or {}
                sent = fire(kind, "warn",
                            f"上证指数 {win}分钟{('急涨' if side == 'up' else '急跌')} {chg:+.2f}%",
                            f"现价 {cur:.2f}，{win} 分钟 {chg:+.2f}%（阈值 ±{move}%）；"
                            f"两市涨 {bd.get('up', '—')} / 跌 {bd.get('down', '—')}")
                if sent:
                    with _mstate_lock:
                        _mstate["idx_side"] = side
            else:
                with _mstate_lock:
                    _mstate["idx_side"] = side

    # 2. 宽度反转（涨占比 zone 切换 + 滞回；当天首轮只建基线）
    breadth = _cache_get("breadth") or {}
    up_ratio = breadth.get("up_ratio")
    hi = float(cfg.get("breadth_high", 0.65))
    lo = float(cfg.get("breadth_low", 0.35))
    bbuf = float(cfg.get("breadth_buffer", 0.05))
    if up_ratio is not None:
        with _mstate_lock:
            prev_zone = _mstate.get("breadth_zone")
            seen = bool(_mstate.get("breadth_seen"))
        zone = ("high" if up_ratio >= hi else "low" if up_ratio <= lo else
                None if (lo + bbuf) < up_ratio < (hi - bbuf) else prev_zone)
        # 首轮只建基线；之后进入 high/low 且与之前不同 → 报一次
        if seen and zone in ("high", "low") and zone != prev_zone:
            up, dn = breadth.get("up", "—"), breadth.get("down", "—")
            if zone == "high":
                sent = fire("mk_breadth_up", "warn",
                            f"宽度转强：涨占比 {up_ratio * 100:.1f}%",
                            f"全市场涨 {up} / 跌 {dn}，涨占比上穿 {hi * 100:.0f}%")
            else:
                sent = fire("mk_breadth_dn", "warn",
                            f"宽度转弱：涨占比 {up_ratio * 100:.1f}%",
                            f"全市场涨 {up} / 跌 {dn}，涨占比下穿 {lo * 100:.0f}%")
            if not sent:
                zone = prev_zone      # 没落库：状态不前移，下个周期重试
        with _mstate_lock:
            _mstate["breadth_zone"] = zone
            _mstate["breadth_seen"] = True

    # 3. 连板高度新高（当日首次；没落库则不占当日名额，下个周期重试）
    # zt 缓存存的是 em_quote.fetch_zt_pools 的原始形状 {date, pool, broken_pool}；
    # max_lbc/ladder/limit_up/broken 只存在于 zt_summary() 的输出里（v3.27 审查：
    # 曾直接读原始池导致本规则恒不触发）。
    zt_raw = _cache_get("zt")
    zs = zt_summary(zt_raw) if zt_raw else {}
    max_lbc = zs.get("max_lbc")
    hmin = int(cfg.get("height_min", 5))
    # 池必须就是今天的（_pool_trade_date 保证缓存 date 是数据真实所属日）
    if str((zt_raw or {}).get("date") or "") == ymd and max_lbc and max_lbc >= hmin:
        with _mstate_lock:
            already = _mstate.get("height_day") == day
        if not already:
            ladder = zs.get("ladder") or []
            top_names = next((x.get("names") for x in ladder if x.get("lbc") == max_lbc), [])
            if fire("mk_height", "warn",
                    f"连板高度 {max_lbc} 板（情绪周期新高）",
                    f"当日最高连板 {max_lbc} 板（阈值 ≥{hmin}）：{('、'.join(top_names[:4]) or '—')}；"
                    f"涨停 {zs.get('limit_up', '—')} 家 / 炸板 {zs.get('broken', '—')} 家"):
                with _mstate_lock:
                    _mstate["height_day"] = day

    # 4. 情绪跨级（当天首轮只建基线；进 panic=重要 / 进 strong 提示；跨到其他档只更新状态）
    try:
        senti = market_sentiment.get(db)
    except Exception as e:
        logger.warning(f"[market-rt] 告警情绪源失败: {str(e)[:120]}")
        senti = {}
    s_level = senti.get("level")
    # stale=宽度快照还是上一交易日的（开盘后首个 slot 落库前），不是今天的盘面
    if s_level and not senti.get("stale"):
        with _mstate_lock:
            prev_level = _mstate.get("sent_level")
        if s_level != prev_level:
            score = senti.get("score")
            advance = True
            if prev_level is not None and s_level == "panic":
                advance = fire(
                    "mk_panic", "critical", f"盘面恐慌：情绪分 {score}",
                    f"实时情绪进入恐慌（{score}/100）；{((senti.get('advice') or {}).get('entry') or '')}"
                    f"；涨 {breadth.get('up', '—')} / 跌 {breadth.get('down', '—')}")
            elif prev_level is not None and s_level == "strong":
                advance = fire("mk_strong", "warn", f"盘面亢奋：情绪分 {score}",
                               f"实时情绪进入强势（{score}/100）；可按买点检查，不追高")
            if advance:   # 没落库则不前移：盘面还在 panic 时下个周期重试
                with _mstate_lock:
                    _mstate["sent_level"] = s_level

    if out:
        db.commit()
        logger.info(f"[market-rt] 盘面异动 {len(out)} 条：{[x['title'] for x in out]}")
    return out


def alerts_today(db, day=None, limit: int = 20) -> list[dict]:
    """当日盘面异动列表（mk%，端点 15s TTL 缓存内廉价）。"""
    day = day or _now().date()
    rows = db.execute(text(
        "SELECT id, kind, level, title, body, created_at FROM risk_alerts "
        "WHERE user_id IS NULL AND trade_date=:d AND kind LIKE 'mk%' "
        "ORDER BY id DESC LIMIT :n"
    ), {"d": day, "n": int(limit)}).fetchall()
    return [{"id": r[0], "kind": r[1], "level": r[2], "title": r[3],
             "body": r[4], "time": str(r[5])[-8:-3] if r[5] else None} for r in rows]


def _cycle(now: Optional[datetime] = None) -> None:
    """后台线程单周期：交易窗口内刷新缓存 + 异动评估 + 幂等日终落库。"""
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
            try:
                evaluate_market_alerts(db, now)
            except Exception as e:
                logger.warning(f"[market-rt] 异动评估失败: {str(e)[:160]}")
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


def _all_slots() -> list[str]:
    """当日完整的 10 分钟 slot 轴：09:30…11:30、13:00…15:00（与 treemap_intraday._slot_of 一致）。"""
    out: list[str] = []
    for hour, minute, n in ((9, 30, 13), (13, 0, 13)):
        for k in range(n):
            t = datetime(2000, 1, 1, hour, minute) + timedelta(minutes=10 * k)
            out.append(t.strftime("%H:%M"))
    return out


_EMPTY_SLOT: dict = {"score": None, "level": None, "up_ratio": None, "median": None,
                     "limit_up": None, "limit_down": None}


def _fill_slot_gaps(rows: list[dict]) -> list[dict]:
    """把当日缺失的 slot 补成空点（score=None）。

    树图采集器在个别时段会漏 slot（2026-11-27 10:00–11:00 整段缺失，其余四天都是
    26 个）。前端是类目轴，不补空点的话 09:50 会直接连到 11:10，70 分钟的缺口看不出来。
    """
    by = {r["slot"]: r for r in rows}
    return [by.get(s, {**_EMPTY_SLOT, "slot": s}) for s in _all_slots()]


def _slots_track(db, day: _date) -> list[dict]:
    now = time.time()
    if _SLOTS["date"] == day and now - _SLOTS["ts"] < 60:
        return _SLOTS["data"]
    data = _fill_slot_gaps(senti_track_from_slots(db, day))
    _SLOTS.update(date=day, ts=now, data=data)
    return data


def _iso8(v) -> Optional[str]:
    """'20261127' → '2026-11-27'；其他形态返回 None。"""
    s = str(v or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 and s.isdigit() else None


def _data_date(trends: dict, zt, min5: dict, fflow: list) -> Optional[str]:
    """缓存数据真实所属的交易日（各源取最新；None=无法判定）。

    盘前和休市日，腾讯分时、东财涨停池/资金流都会回退成上一交易日的整段数据，
    不带这个字段前端就只能把缓存刷新时间当成数据时间。
    """
    cands = [_iso8(tr.get("date")) for tr in (trends or {}).values()]
    cands.append(_iso8((zt or {}).get("date")))
    if min5:
        cands.append(sorted(min5.keys())[-1])
    if fflow:
        cands.append(fflow[-1].get("date"))
    cands = [c for c in cands if c]
    return max(cands) if cands else None


def _stale_flag(session: str, in_window: bool, core_ts: list, data_is_today: bool,
                now_ts: float) -> bool:
    """数据能否当成「当前盘面」：缺源 / 窗口内超 3 个周期未更新 / 非休市时段但数据
    不是今天的。休市时显示上一交易日数据是预期行为，不算 stale。"""
    if not core_ts:
        return True
    if in_window and now_ts - max(core_ts) > 180:
        return True
    return (not data_is_today) and session != "closed"


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

    data_date = _data_date(trends, zt, min5, fflow)
    # 情绪轨迹跟随数据所属交易日：休市日/盘前其他卡片显示的是上一交易日，轨迹不能空白
    track_day = (_date.fromisoformat(data_date)
                 if data_date and data_date < day.isoformat() else day)
    # stale：① 核心源缺失；② 交易窗口内超 3 个周期未更新；③ 非休市时段但数据还是
    # 上一交易日的（盘前回退、刷新一直失败沿用旧缓存）
    core_ts = [t for t in (_cache_ts(k) for k in ("breadth", "trends", "zt")) if t]
    in_window = _in_collect_window(now) and db_trade_day
    stale = _stale_flag(session, in_window, core_ts, data_date == day.isoformat(),
                        time.time())
    refreshing = False
    if not (_cache_get("breadth") and _cache_get("trends") and _cache_get("zt")
            and _cache_get("boards") and _cache_get("fflow") and _cache_get("min5")):
        # 任一核心源缺失（后端刚启动/收盘重启只补了单源）→ 后台整刷一次；
        # ensure_refresh_async 自带互斥与节流（窗口内让位给采集 loop，窗口外 10 分钟一次）
        refreshing = ensure_refresh_async(now)

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
        # 数据真实所属交易日（≠ date 即「显示的是上一交易日」，前端必须标出来）
        "data_date": data_date,
        "session": session,
        "session_label": _SESSION_LABEL[session],
        "asof": asof,
        "stale": stale,
        "refreshing": refreshing,
        "breadth": breadth,
        "senti": {k: senti[k] for k in ("score", "label", "level", "advice", "stale", "asof")
                  if k in senti},
        "senti_track": _slots_track(db, track_day),
        "track_date": track_day.isoformat(),
        "indices_trend": indices_trend,
        "turnover": estimate_turnover(min5),
        "zt": zt_summary(zt) if zt else None,
        "boards": sorted(boards, key=lambda b: b.get("chg_pct") or 0, reverse=True),
        "fflow": {
            "series": [{"t": r.get("t"), "main": r.get("main"),
                        "big": r.get("big"), "huge": r.get("huge")} for r in fflow],
            "date": (fflow[-1].get("date") if fflow else None),
            "latest": (fflow[-1].get("main") if fflow else None),
        },
        # 实时空仓闸门（三源组合，design/13 批次三；buy_check.live_gate 自带 60s 缓存）
        "gate": _safe_live_gate(db),
        "alerts_today": alerts_today(db),
    }


def _safe_live_gate(db) -> dict:
    """live_gate 的兜底：算不出来就是「不知道」，绝不能兜成「放行」。

    现在只影响展示；design/13 记的升级路径是把它接到买入候选——到那时 fail-open
    就是实盘风险，所以现在就返回独立的 unknown（pass=None），不给出 bool。
    """
    try:
        from app.buy_check import live_gate
        return live_gate(db)
    except Exception as e:
        logger.warning(f"[market-rt] live_gate 失败: {str(e)[:120]}")
        return {"level": "unknown", "pass": None, "label": "不可用",
                "reasons": [f"闸门计算失败，结论未知: {str(e)[:80]}"], "advice_entry": None,
                "sources": {}}


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
