"""东财实时行情抓取（实时大盘分析页专用，crawler 层唯一新增东财行情出口）。

接口均为东方财富公开行情接口（非官方、无 SLA），全部经 crawler.news.sources.em_get()
统一串行限流出口（1.2s + 抖动/请求；实测风控线 5/s、并发≥10、200/min）。

**只走 push2 / push2ex 域**（2026-11-27 实测对 curl_cffi 稳定）；push2his 域已弃用——
实测晚间开始对非命令行-curl 的 TLS 指纹差别对待（requests/curl_cffi 全超时、压缩流
中断），指数分时/量能改走腾讯 minute/query（crawler/adapters/tencent_adapter.py）+
新浪 5 分钟 K（app/market_realtime.py），两者生产已长期使用。

实测口径（2026-11-27，样本存 tests/fixtures/em_*.json）：
- getTopicZTPool / getTopicZBPool：p(价格×1000)、fund(封单额，元)、fbt/lbt(HHMMSS int)、
  zbc(炸板次数)、lbc(连板数)、zttj(几天几板)；**历史日期可用**（日终回补依赖）
- push2 clist 行业板块(fs=m:90+t:2)：2024 改版后一级与细分混排共 ~496 个，pz 锁 100 行/页，
  f62 主力净流入(元)、f104/f105 板块内涨/跌家数、f128/f140/f141 领涨股
- fflow/kline/get 分钟资金流：**必须走 push2 域**（push2his 返回空），klt=1 lmt=0，
  secid=1.000001 & secid2=0.399001 聚合沪深两市，f52-f56 均为当日累计净流入(元)

全市场宽度/成交额不用东财（clist 分页被锁 100 行/页，56 页走 em 闸门 ≈ 70s 不可行），
改复用 app.treemap_intraday.fetch_all_quotes（新浪 hs_a，约 8s）。
"""
from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger

from crawler.news.sources import em_get

_REFERER = {"Referer": "https://quote.eastmoney.com/"}


def _loads_json(text: str) -> dict:
    """东财响应解析：兼容纯 JSON 与 SSE `data: {...}` 流（取首个 data 非空行）。"""
    t = str(text).strip()
    if not t.startswith("data:"):
        obj = json.loads(t)
    else:
        obj = None
        for line in t.splitlines():
            s = line.strip()
            if not s.startswith("data:"):
                continue
            body = s[5:].strip()
            if not body or body == "null":
                continue
            try:
                cand = json.loads(body)
            except json.JSONDecodeError:
                continue
            if isinstance(cand, dict) and cand.get("data"):
                obj = cand
                break
    if not isinstance(obj, dict) or not obj.get("data"):
        raise RuntimeError("东财接口无数据")
    return obj


def _int(v: Any) -> Optional[int]:
    if v is None or v == "-" or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _num(v: Any) -> Optional[float]:
    if v is None or v == "-" or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN → None


def _hhmmss(v: Any) -> Optional[str]:
    """92500 → '09:25:00'；None/非法 → None。"""
    n = _int(v)
    if n is None or n <= 0:
        return None
    return f"{n // 10000:02d}:{n // 100 % 100:02d}:{n % 100:02d}"


# ── 涨停池 / 炸板池（push2ex，历史日期可用）─────────────────────────────────────

_ZT_UT = "7eea3edcaed734bea9cbfc24409ed989"  # 东财公开页固定 token（akshare 同款）


_ZT_PAGESIZE = 1000   # 实测上限（2026-11-27 涨停 417 家时 pagesize=300 被截断）


def _fetch_pool(kind: str, date_yyyymmdd: str) -> list[dict]:
    """kind: ZTPool(涨停) / ZBPool(炸板)。空数据返回 []（当日无涨停是合法状态）。"""
    url = f"https://push2ex.eastmoney.com/getTopic{kind}"
    params = {
        "ut": _ZT_UT, "dpt": "wz.ztzt",
        "Pageindex": 0, "pagesize": _ZT_PAGESIZE,
        "sort": "fbt:asc", "date": str(date_yyyymmdd),
    }
    r = em_get(url, params=params, headers=_REFERER, timeout=(5, 20))
    obj = json.loads(r.text)  # push2ex 返回纯 JSON
    pool = (obj.get("data") or {}).get("pool") or []
    if len(pool) >= _ZT_PAGESIZE:
        # 千股涨停级别才会碰到；接口不支持翻页，只能留痕让人知道数字偏小
        logger.warning(f"[em-quote] {kind} {date_yyyymmdd} 返回 {len(pool)} 行，达到分页上限，可能被截断")
    return _parse_pool_rows(pool)


def _zttj(v: Any) -> str:
    """涨停统计 {'days': m, 'ct': n} → 'm天n板'；其他形态原样字符串化。"""
    if isinstance(v, dict):
        d, c = v.get("days"), v.get("ct")
        if d is not None and c is not None:
            return f"{d}天{c}板"
        return ""
    return str(v) if v else ""


def _parse_pool_rows(pool: list) -> list[dict]:
    """push2ex pool 行 → 标准化 dict（纯函数：fixture 直接喂，不经网络）。

    字段口径（2026-11-27 实测）：p/ztp 是价格×1000；fund 封单额（元）；fbt/lbt 是
    HHMMSS 整数；zttj={days,ct}；炸板池没有 lbc/fund，lbc 记 1（冲击首板未封）。
    """
    out: list[dict] = []
    for it in pool or []:
        p, ztp = _num(it.get("p")), _num(it.get("ztp"))
        out.append({
            "code": str(it.get("c") or "").zfill(6),
            "name": it.get("n") or "",
            "price": p / 1000 if p else None,
            "chg_pct": _num(it.get("zdp")),
            "amount": _num(it.get("amount")),
            "ltsz": _num(it.get("ltsz")),
            "turnover": _num(it.get("hs")),
            "lbc": _int(it.get("lbc")) or 1,       # 炸板池无 lbc → 1（语义：冲击首板未封）
            "fbt": _hhmmss(it.get("fbt")),
            "lbt": _hhmmss(it.get("lbt")),
            "fund": _num(it.get("fund")),          # 封单额（元），炸板池无
            "zbc": _int(it.get("zbc")) or 0,
            "hybk": it.get("hybk") or "",
            "zttj": _zttj(it.get("zttj")),         # 几天几板，如 "5天3板"
            "zt_price": ztp / 1000 if ztp else None,
        })
    return out


def fetch_zt_pools(date_yyyymmdd: str) -> dict:
    """涨停池 + 炸板池（2 次 em_get）。返回 {pool, broken_pool, date}。

    date 是**请求**的日期。东财对尚无数据的日期（盘前/休市日/未来日）会静默返回
    最近一个交易日的池，调用方必须只请求确定已有数据的日期（见
    app/market_realtime._pool_trade_date）。
    """
    return {
        "date": str(date_yyyymmdd),
        "pool": _fetch_pool("ZTPool", date_yyyymmdd),
        "broken_pool": _fetch_pool("ZBPool", date_yyyymmdd),
    }


# ── 行业板块（push2 clist，东财 2024 改版后一级+细分混排 ~496 个，pz 锁 100 行/页）──

def fetch_board_list() -> list[dict]:
    """行业板块：[{code,name,price,chg_pct,main_net,up,down,leader,leader_code}]。"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    out: list[dict] = []
    for pn in range(1, 10):  # 496 个 / 100 每页 ≈ 5 页，留余量
        params = {
            "pn": pn, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f62", "fs": "m:90+t:2",
            "fields": "f12,f14,f2,f3,f62,f104,f105,f128,f140,f141",
        }
        r = em_get(url, params=params, headers=_REFERER, timeout=(5, 20))
        data = json.loads(r.text).get("data") or {}
        rows = data.get("diff") or []
        for it in rows:
            m = _int(it.get("f141"))
            leader_code = str(it.get("f140") or "").zfill(6) or None
            out.append({
                "code": str(it.get("f12") or ""),
                "name": it.get("f14") or "",
                "price": _num(it.get("f2")),
                "chg_pct": _num(it.get("f3")),
                "main_net": _num(it.get("f62")),     # 元
                "up": _int(it.get("f104")) or 0,
                "down": _int(it.get("f105")) or 0,
                "leader": it.get("f128") or "",
                "leader_code": f"{leader_code}.{'SH' if m == 1 else 'SZ'}" if leader_code else None,
            })
        total = _int(data.get("total")) or 0
        if pn * 100 >= total or not rows:
            break
    if not out:
        raise RuntimeError("东财行业板块列表为空")
    return out


# ── 大盘主力资金分时（push2 域 fflow/kline，secid2 聚合沪深两市）────────────────

def fetch_market_fflow() -> list[dict]:
    """沪深两市主力资金分钟累计：[{date, t, main, small, mid, big, huge}]（元，当日累计）。

    注意域名是 push2（push2his 对该接口返回空 klines）；klt=1 lmt=0 取当日全量。
    盘前/休市日返回的是上一交易日的整段，date 字段如实带出供调用方判断新旧。
    """
    url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
    params = {
        "secid": "1.000001", "secid2": "0.399001",
        "klt": 1, "lmt": 0,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56",
    }
    r = em_get(url, params=params, headers=_REFERER, timeout=(5, 20))
    klines = _loads_json(r.text)["data"].get("klines") or []
    out: list[dict] = []
    for raw in klines:
        parts = str(raw).split(",")
        if len(parts) < 6:
            continue
        day, _, hm = parts[0].partition(" ")
        out.append({
            "date": day,
            "t": hm,
            "main": _num(parts[1]),   # 主力 = 大单 + 超大单
            "small": _num(parts[2]),
            "mid": _num(parts[3]),
            "big": _num(parts[4]),
            "huge": _num(parts[5]),
        })
    return out
