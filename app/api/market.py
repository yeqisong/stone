"""大盘/市场详情 API — 市场总览、指数估值、全市场资金流、宽度、两融、宏观。

数据全部来自既有 DAG 采集链路落库的表（index_valuation / stock_moneyflow /
stock_margin_detail / macro_indicator / index_daily_quote / daily_quote），
本模块只做只读聚合，不触发任何采集。市场级图表数据变化频率低（日频），
统一走模块级 TTL 缓存。
"""
import math
import threading
import time

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import bindparam, text

from app.db.connection import get_sync_db

router = APIRouter(tags=["market"])

# 7 个主要指数（与 dag_task_macro_daily 的估值覆盖一致；库内 index_code 无后缀）
MAIN_INDEXES = ['000001.SH', '000016.SH', '000300.SH', '000905.SH',
                '000852.SH', '399001.SZ', '399006.SZ']
INDEX_NAMES = {
    '000001.SH': '上证指数', '000016.SH': '上证50', '000300.SH': '沪深300',
    '000905.SH': '中证500', '000852.SH': '中证1000',
    '399001.SZ': '深证成指', '399006.SZ': '创业板指',
}
BARE = {c: c.split('.')[0] for c in MAIN_INDEXES}

# ── 模块级 TTL 缓存（对齐 stock.py 手写缓存模式）+ 在途去重 ──
# 本模块冷查询 3~9s（百万行级聚合），无去重时并发 miss 会重复计算；
# 用 Event 让并发请求等首个计算者，30s 超时兜底自己算（防死等）。
_cache: dict = {}
_inflight: dict = {}
_inflight_lock = threading.Lock()


def _cached(key: tuple, ttl: int, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]

    with _inflight_lock:
        ev = _inflight.get(key)
        owner = ev is None
        if owner:
            ev = threading.Event()
            _inflight[key] = ev
    if not owner:
        ev.wait(timeout=30)
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        return fn()  # 计算者迟迟没写完（异常）→ 兜底自己算

    try:
        val = fn()
        _cache[key] = (time.time(), val)
        if len(_cache) > 200:
            _cache.clear()
        return val
    finally:
        with _inflight_lock:
            _inflight.pop(key, None)
        ev.set()


def _num(v, nd=None):
    """NaN/Inf → None（JSON 不能序列化 NaN），可选保留位数。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or math.isinf(f):
        return None
    return round(f, nd) if nd is not None else f


def _expanding(stmt: str, name: str):
    return text(stmt).bindparams(bindparam(name, expanding=True))


# 最近 N 个 distinct 交易日：`SELECT DISTINCT trade_date ... LIMIT n` 在千万行表上
# 是全索引扫描 + 去重（实测 5s+），递归 CTE 沿索引跳跃取值（skip-scan）毫秒级。
# 末支取不到更早日期返回 NULL 行，外层 WHERE 过滤并令递归终止。
_RECENT_DATES = """
WITH RECURSIVE dd AS (
    (SELECT {col} AS trade_date FROM {table} ORDER BY {col} DESC LIMIT 1)
    UNION ALL
    SELECT (SELECT {col} FROM {table} WHERE {col} < dd.trade_date
            ORDER BY {col} DESC LIMIT 1)
    FROM dd WHERE dd.trade_date IS NOT NULL
)
SELECT trade_date FROM dd WHERE trade_date IS NOT NULL
ORDER BY trade_date DESC LIMIT :n
"""


# ── 1. 全市场总览 ──

@router.get("/market/overview")
def get_market_overview():
    """市场总览：情绪仪表 + 7 指数行情/估值/分位 + 当日资金/两融/宏观快照。"""
    def _load():
        db = get_sync_db()
        try:
            # 情绪仪表（宽度/涨跌停/中位数，进程内自带 60s 缓存）
            from app.market_sentiment import get as market_sentiment
            try:
                senti = market_sentiment(db)
            except Exception:
                senti = None

            # 7 指数最新两日收盘（算当日涨跌）
            since = db.execute(text(
                "SELECT max(trade_date) - interval '14 days' FROM index_daily_quote"
            )).scalar()
            rows = db.execute(_expanding("""
                SELECT * FROM (
                    SELECT index_code, index_name, trade_date, close,
                           row_number() OVER (PARTITION BY index_code ORDER BY trade_date DESC) AS rn
                    FROM index_daily_quote
                    WHERE index_code IN :codes AND trade_date >= :since
                ) t WHERE rn <= 2 ORDER BY index_code, trade_date DESC
            """, 'codes'), {"codes": [BARE[c] for c in MAIN_INDEXES], "since": since}).fetchall()
            cur, prev = {}, {}
            for r in rows:
                tgt = cur if r.rn == 1 else prev
                tgt[r.index_code] = r

            # 估值最新值 + 全历史分位（index_valuation 仅 ~3.7 万行，窗口函数全扫很便宜）
            vrows = db.execute(_expanding("""
                WITH allv AS (
                    SELECT ts_code, trade_date, pe_ttm, pb, turnover_rate,
                           percent_rank() OVER (PARTITION BY ts_code ORDER BY pe_ttm) AS pe_pct,
                           percent_rank() OVER (PARTITION BY ts_code ORDER BY pb) AS pb_pct
                    FROM index_valuation
                    WHERE ts_code IN :codes AND pe_ttm IS NOT NULL AND pb IS NOT NULL
                ),
                latest AS (SELECT ts_code, max(trade_date) AS d FROM allv GROUP BY ts_code)
                SELECT a.ts_code, a.pe_ttm, a.pb, a.turnover_rate, a.pe_pct, a.pb_pct
                FROM allv a JOIN latest l ON a.ts_code = l.ts_code AND a.trade_date = l.d
            """, 'codes'), {"codes": MAIN_INDEXES}).fetchall()
            vals = {v.ts_code: v for v in vrows}

            indices = []
            for c in MAIN_INDEXES:
                a, p = cur.get(BARE[c]), prev.get(BARE[c])
                v = vals.get(c)
                indices.append({
                    "ts_code": c,
                    "code": BARE[c],
                    "name": INDEX_NAMES[c] or (getattr(a, 'index_name', '') if a else ''),
                    "trade_date": str(a.trade_date) if a else None,
                    "close": _num(a.close, 2) if a else None,
                    "chg_pct": _num((a.close / p.close - 1) * 100, 2)
                               if (a and p and p.close) else None,
                    "pe_ttm": _num(v.pe_ttm, 2) if v else None,
                    "pb": _num(v.pb, 2) if v else None,
                    "turnover_rate": _num(v.turnover_rate, 2) if v else None,
                    "pe_pct": _num(v.pe_pct * 100, 1) if v else None,
                    "pb_pct": _num(v.pb_pct * 100, 1) if v else None,
                })

            # 当日全市场主力净流入（最后两个聚合日）
            mf = db.execute(text("""
                SELECT trade_date,
                       SUM(COALESCE(buy_elg_amt,0)+COALESCE(buy_lg_amt,0)
                           -COALESCE(sell_elg_amt,0)-COALESCE(sell_lg_amt,0)) * 1e4 AS net_main,
                       SUM(COALESCE(net_mf_amt,0)) * 1e4 AS net_total
                FROM stock_moneyflow
                WHERE trade_date IN ({recent_dates})
                GROUP BY trade_date ORDER BY trade_date DESC
            """.format(recent_dates=_RECENT_DATES.format(col='trade_date', table='stock_moneyflow'))),
                {"n": 2}).fetchall()
            mfl = None
            if mf:
                t = mf[0]
                p = mf[1] if len(mf) > 1 else None
                mfl = {
                    "date": str(t.trade_date),
                    "net_main": _num(t.net_main),
                    "net_total": _num(t.net_total),
                    "net_main_prev": _num(p.net_main) if p else None,
                }

            # 两融余额最新值
            mg = db.execute(text("""
                SELECT trade_date, SUM(fin_amount) AS fin, SUM(total_amount) AS total
                FROM stock_margin_detail
                WHERE trade_date IN ({recent_dates})
                GROUP BY trade_date
            """.format(recent_dates=_RECENT_DATES.format(col='trade_date', table='stock_margin_detail'))),
                {"n": 1}).fetchone()
            margin = {
                "date": str(mg.trade_date),
                "fin_amount": _num(mg.fin),
                "total_amount": _num(mg.total),
            } if mg else None

            # 宏观最新值
            mrows = db.execute(text("""
                SELECT DISTINCT ON (indicator_code) indicator_code, period_date, value
                FROM macro_indicator ORDER BY indicator_code, period_date DESC
            """)).fetchall()
            macro = {r.indicator_code: {"date": str(r.period_date), "value": _num(r.value, 3)}
                     for r in mrows}

            return {"indices": indices, "moneyflow": mfl,
                    "margin": margin, "macro": macro, "sentiment": senti}
        finally:
            db.close()

    return _cached(("overview",), 60, _load)


# ── 2. 单指数估值时序 ──

@router.get("/market/valuation/{ts_code}")
def get_market_valuation(ts_code: str, days: int = Query(3000, ge=30, le=10000)):
    """单指数 PE/PB/换手率时序 + 全历史 30/70 分位（画分位线用）。"""
    if ts_code not in MAIN_INDEXES:
        raise HTTPException(status_code=404, detail=f"不支持的指数 {ts_code}")

    def _load():
        db = get_sync_db()
        try:
            rows = db.execute(text("""
                SELECT trade_date, pe, pe_ttm, pb, turnover_rate, total_mv, float_mv
                FROM index_valuation WHERE ts_code=:c
                ORDER BY trade_date DESC LIMIT :days
            """), {"c": ts_code, "days": days}).fetchall()
            data = [{
                "date": str(r.trade_date),
                "pe": _num(r.pe, 2), "pe_ttm": _num(r.pe_ttm, 2), "pb": _num(r.pb, 3),
                "turnover_rate": _num(r.turnover_rate, 3),
                "total_mv": _num(r.total_mv), "float_mv": _num(r.float_mv),
            } for r in reversed(rows)]

            # 分位在返回窗口内计算（默认 days=3000 覆盖全部历史；开窗 SQL 的
            # percent_rank 逐行返回不好取"最新值的分位"，Python 侧算更直白）
            pes = sorted(d["pe_ttm"] for d in data if d["pe_ttm"] is not None)
            pbs = sorted(d["pb"] for d in data if d["pb"] is not None)

            def _pctl(arr, q):
                if not arr:
                    return None
                i = q * (len(arr) - 1)
                lo = int(i)
                hi = min(lo + 1, len(arr) - 1)
                return arr[lo] + (arr[hi] - arr[lo]) * (i - lo)

            def _rank(arr, v):
                if not arr or v is None:
                    return None
                return 100.0 * sum(1 for x in arr if x < v) / len(arr)

            latest = data[-1] if data else {}
            return {
                "ts_code": ts_code,
                "name": INDEX_NAMES.get(ts_code, ts_code),
                "data": data,
                "pct": {
                    "p30_pe": round(_pctl(pes, 0.3), 2) if pes else None,
                    "p70_pe": round(_pctl(pes, 0.7), 2) if pes else None,
                    "p30_pb": round(_pctl(pbs, 0.3), 3) if pbs else None,
                    "p70_pb": round(_pctl(pbs, 0.7), 3) if pbs else None,
                    "pe_pct": round(_rank(pes, latest.get("pe_ttm")), 1) if pes else None,
                    "pb_pct": round(_rank(pbs, latest.get("pb")), 1) if pbs else None,
                },
            }
        finally:
            db.close()

    return _cached(("valuation", ts_code, days), 600, _load)


# ── 3. 全市场每日资金流聚合 ──

@router.get("/market/moneyflow")
def get_market_moneyflow(days: int = Query(1000, ge=30, le=4600)):
    """全市场逐日资金流合计。tushare moneyflow 单位为万元，×1e4 统一为元
    （口径对齐 app/sector_flow.py；注意 /api/stock/{c}/moneyflow 返回的是原始万元）。"""
    def _load():
        db = get_sync_db()
        try:
            rows = db.execute(text("""
                SELECT trade_date,
                       SUM(COALESCE(buy_elg_amt,0)+COALESCE(buy_lg_amt,0)
                           -COALESCE(sell_elg_amt,0)-COALESCE(sell_lg_amt,0)) * 1e4 AS net_main,
                       SUM(COALESCE(net_mf_amt,0)) * 1e4 AS net_total,
                       SUM(COALESCE(buy_elg_amt,0)+COALESCE(buy_lg_amt,0)
                           +COALESCE(buy_md_amt,0)+COALESCE(buy_sm_amt,0)) * 1e4 AS buy_all,
                       SUM(COALESCE(sell_elg_amt,0)+COALESCE(sell_lg_amt,0)
                           +COALESCE(sell_md_amt,0)+COALESCE(sell_sm_amt,0)) * 1e4 AS sell_all,
                       COUNT(*) AS stocks
                FROM stock_moneyflow
                WHERE trade_date IN ({recent_dates})
                GROUP BY trade_date ORDER BY trade_date
            """.format(recent_dates=_RECENT_DATES.format(col='trade_date', table='stock_moneyflow'))),
                {"n": days}).fetchall()
            data = [{
                "date": str(r.trade_date),
                "net_main": _num(r.net_main),
                "net_total": _num(r.net_total),
                "buy_all": _num(r.buy_all),
                "sell_all": _num(r.sell_all),
                "stocks": int(r.stocks),
            } for r in rows]
            # 前端累计线
            cum = 0.0
            for r in data:
                cum += (r["net_main"] or 0)
                r["cum_main"] = round(cum, 2)
            return {"data": data}
        finally:
            db.close()

    return _cached(("moneyflow", days), 300, _load)


# ── 4. 市场宽度（涨跌家数 / 涨跌停近似）──

@router.get("/market/breadth")
def get_market_breadth(days: int = Query(250, ge=30, le=1200)):
    """逐日上涨/下跌/平盘家数 + 涨跌停近似计数（pct ≥9.8% / ≤-9.8%，10cm 与
    20cm 板混杂口径，只看相对强度别当精确涨跌停）。个股用 close_hfq 对前一
    有效收盘，ETF/转债经 stock_master 过滤；窗口比请求多取 1 个交易日供 LAG。"""
    def _load():
        db = get_sync_db()
        try:
            rows = db.execute(text("""
                WITH dd AS (
                    {recent_dates}
                ),
                q AS (
                    SELECT d.trade_date, d.stock_code, d.close_hfq,
                           LAG(d.close_hfq) OVER (PARTITION BY d.stock_code ORDER BY d.trade_date) AS prev
                    FROM daily_quote d
                    WHERE d.trade_date IN (SELECT trade_date FROM dd)
                      AND d.close_hfq > 0
                      AND d.stock_code IN (SELECT stock_code FROM stock_master WHERE stock_type='stock')
                )
                SELECT trade_date,
                    COUNT(*) FILTER (WHERE close_hfq > prev) AS up,
                    COUNT(*) FILTER (WHERE close_hfq < prev) AS down,
                    COUNT(*) FILTER (WHERE close_hfq = prev) AS flat,
                    COUNT(*) FILTER (WHERE close_hfq / prev - 1 >= 0.098) AS limit_up,
                    COUNT(*) FILTER (WHERE close_hfq / prev - 1 <= -0.098) AS limit_down
                FROM q WHERE prev IS NOT NULL AND prev > 0
                GROUP BY trade_date ORDER BY trade_date
            """.format(recent_dates=_RECENT_DATES.format(col='trade_date', table='daily_quote'))),
                {"n": days + 1}).fetchall()
            data = [{
                "date": str(r.trade_date),
                "up": int(r.up), "down": int(r.down), "flat": int(r.flat),
                "limit_up": int(r.limit_up), "limit_down": int(r.limit_down),
            } for r in rows]
            return {"data": data}
        finally:
            db.close()

    return _cached(("breadth", days), 300, _load)


# ── 5. 全市场两融余额 ──

@router.get("/market/margin")
def get_market_margin(days: int = Query(1000, ge=30, le=4000)):
    """全市场逐日两融余额合计（fin_amount=rzye，单位元；数据 2014-01 起）。

    残缺日防御：交易所披露延迟/上游缺失时当日样本骤降（如 2026-11-20 只有
    2019 行 vs 邻日 4491），聚合值与邻日不可比，折线出"假跳崖"——行数低于
    前后邻日 70% 的日剔除（邻日比较而非全局中位数：上市公司数逐年增长，
    全局阈值会误伤早年天然低行数的日子），趋势线保持连续。"""

    def _load():
        db = get_sync_db()
        try:
            rows = db.execute(text("""
                SELECT trade_date,
                       SUM(fin_amount) AS fin,
                       SUM(sec_amount) AS sec,
                       SUM(total_amount) AS total,
                       COUNT(*) AS n
                FROM stock_margin_detail
                WHERE trade_date IN ({recent_dates})
                GROUP BY trade_date ORDER BY trade_date
            """.format(recent_dates=_RECENT_DATES.format(col='trade_date', table='stock_margin_detail'))),
                {"n": days}).fetchall()
            # 邻日比较（全局中位数会误伤"上市公司数逐年增长"的结构性漂移：
            # 早年行数天然低）。当日行数 < 前后邻日 70% 视为披露残缺，剔除。
            ns = [int(r.n) for r in rows]
            keep = []
            for i, r in enumerate(rows):
                ok = True
                if i > 0 and ns[i] < ns[i - 1] * 0.7:
                    ok = False
                if i + 1 < len(rows) and ns[i] < ns[i + 1] * 0.7:
                    ok = False
                if ok:
                    keep.append(r)
            data = [{
                "date": str(r.trade_date),
                "fin": _num(r.fin), "sec": _num(r.sec), "total": _num(r.total),
            } for r in keep]
            return {"data": data}
        finally:
            db.close()

    return _cached(("margin", days), 300, _load)


# ── 6. 宏观指标时序 ──

@router.get("/market/macro")
def get_market_macro():
    """宏观指标时序（cpi/ppi/m0/m1/m2/shibor_on/gdp，value=同比或利率）。"""
    def _load():
        db = get_sync_db()
        try:
            rows = db.execute(text("""
                SELECT indicator_code, period_date, value, yoy
                FROM macro_indicator ORDER BY indicator_code, period_date
            """)).fetchall()
            series = {}
            for r in rows:
                series.setdefault(r.indicator_code, []).append({
                    "date": str(r.period_date),
                    "value": _num(r.value, 4), "yoy": _num(r.yoy, 4),
                })
            return {"series": series}
        finally:
            db.close()

    return _cached(("macro",), 600, _load)


# ── 7. 市场情绪分日度序列（恐慌指数趋势，v3.22）──

@router.get("/market/sentiment-history")
def get_market_sentiment_history(days: int = Query(1000, ge=30, le=6500)):
    """情绪分日度序列（三成分口径 2000 起，design/11 §口径决策）。
    score=序列主分（历史与增量可比）；score_rt=当日实时四成分参考分（仅节点落库日有值）。"""
    def _load():
        db = get_sync_db()
        try:
            rows = db.execute(text("""
                SELECT trade_date, score, level, up_ratio, median, limit_up, limit_down,
                       up, down, flat, total, score_rt, vwap_ratio
                FROM market_sentiment_daily ORDER BY trade_date DESC LIMIT :d
            """), {"d": days}).fetchall()
            data = [{
                "date": str(r.trade_date), "score": _num(r.score, 1), "level": r.level,
                "up_ratio": _num(r.up_ratio, 4), "median": _num(r.median, 2),
                "limit_up": int(r.limit_up or 0), "limit_down": int(r.limit_down or 0),
                "up": int(r.up or 0), "down": int(r.down or 0), "total": int(r.total or 0),
                "score_rt": _num(r.score_rt, 1), "vwap_ratio": _num(r.vwap_ratio, 4),
            } for r in reversed(rows)]
            return {"data": data}
        finally:
            db.close()

    return _cached(("sentiment-history", days), 300, _load)
