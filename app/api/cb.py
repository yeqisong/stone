"""可转债 API：列表/策略/详情/历史 + 正股关联。

数据域独立（cb_basic/cb_daily/cb_share），不进 daily_quote/stock_master。
指标先全量计算一次写入 cb_metrics（JSONB 物化表），列表/概览/辅导/详情直接读表。
"""
import json
import threading
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.cb_analytics import (
    STRATEGY_LABELS,
    bond_ytm,
    build_strategy_pools,
    conversion_value,
    daily_returns,
    days_until,
    double_low,
    forced_redemption_days,
    linkage_label,
    linkage_metrics,
    period_return,
    premium_rate,
    price_percentile,
    pure_bond_premium,
    pure_bond_value,
    rolling_corr,
    score_bond,
    stock_to_force_pct,
    strategy_keys,
    turnover_pct,
    watch_candidates,
)
from app.db.connection import get_sync_db

router = APIRouter(tags=["cb"])


def _d(v):
    if v is None:
        return None
    return str(v)[:10]


def _f(v):
    try:
        if v is None or v == '':
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _conv_price_steps(db, bond_code):
    """转股价公告阶梯：[(asof_date, convert_price), ...] 升序。"""
    rows = db.execute(text("""
        SELECT asof_date, convert_price FROM cb_share
        WHERE bond_code=:c AND convert_price > 0
        ORDER BY asof_date
    """), {"c": bond_code}).fetchall()
    return [(_d(r[0]), _f(r[1])) for r in rows]


def _conv_price_on(steps, day, fallback):
    px = fallback
    for d, v in steps:
        if d <= day:
            px = v
        else:
            break
    return px


def _hist_close_map(rows):
    """rows: (trade_date, bond_code, close) → {bond_code: [(date, close), ...]}（升序）"""
    hist = defaultdict(list)
    for r in rows:
        hist[r[1]].append((_d(r[0]), _f(r[2])))
    for k in hist:
        hist[k].sort(key=lambda x: x[0])
    return hist


def _stock_metrics(rows, target_date=None):
    """rows: (trade_date, stock_code, close) 升序 → 每正股 {close,pct_chg,ma20,ma60,closes,close_on(date)}"""
    hist = defaultdict(list)
    for r in rows:
        hist[r[1]].append((_d(r[0]), _f(r[2])))
    out = {}
    for code, arr in hist.items():
        arr = [x for x in arr if x[1] and x[1] > 0]
        arr.sort(key=lambda x: x[0])
        if not arr:
            continue
        closes = [x[1] for x in arr]
        close = closes[-1]
        pct = (close / closes[-2] - 1) if len(closes) >= 2 else None
        ma20 = sum(closes[-20:]) / len(closes[-20:]) if closes else None
        ma60 = sum(closes[-60:]) / len(closes[-60:]) if closes else None
        close_on = None
        if target_date:
            for d, c in arr:
                if d <= target_date:
                    close_on = c
                else:
                    break
        out[code] = {
            'close': close, 'pct_chg': pct, 'ma20': ma20, 'ma60': ma60,
            'closes': closes[-30:],
            'close_on': close_on or (closes[-1] if arr else None),
        }
    return out


def _load_bundle(db, bond_codes=None):
    """一次拉齐 cb_basic + 最新行情/转股快照/正股行情与价格分位历史。

    bond_codes 非空时只加载指定转债（个股详情卡片/单债详情避免全量扫描）。
    """
    sql = """
        SELECT bond_code, ts_code, exchange, bond_short_name, bond_full_name,
               stk_code, stk_short_name, list_date, delist_date, maturity_date,
               conv_start_date, conv_end_date, conv_stop_date,
               first_conv_price, conv_price, issue_size, remain_size,
               coupon_rate, maturity, par, value_date, rate_clause
        FROM cb_basic
    """
    params = {}
    if bond_codes:
        sql += " WHERE bond_code = ANY(:bc)"
        params["bc"] = list(bond_codes)
    sql += " ORDER BY bond_code"
    basic_rows = db.execute(text(sql), params).fetchall()
    basics = {r[0]: dict(r._mapping) for r in basic_rows}
    if not basics:
        return {'basics': {}, 'quotes': {}, 'shares': {}, 'stocks': {}, 'price_hist': {}, 'asof': None}

    codes = list(basics.keys())
    quote_rows = db.execute(text("""
        SELECT DISTINCT ON (bond_code) bond_code, trade_date, ts_code, pre_close,
               open, high, low, close, change, pct_chg, vol, amount
        FROM cb_daily WHERE bond_code = ANY(:c)
        ORDER BY bond_code, trade_date DESC
    """), {"c": codes}).fetchall()
    quotes = {r[0]: dict(r._mapping) for r in quote_rows}

    share_rows = db.execute(text("""
        SELECT DISTINCT ON (bond_code) bond_code, asof_date, issue_size,
               convert_price_initial, convert_price, convert_ratio, acc_convert_ratio,
               remain_size, total_shares
        FROM cb_share WHERE bond_code = ANY(:c)
        ORDER BY bond_code, asof_date DESC
    """), {"c": codes}).fetchall()
    shares = {r[0]: dict(r._mapping) for r in share_rows}

    stk_codes = sorted({b.get('stk_code') for b in basics.values() if b.get('stk_code')})
    stocks = {}
    if stk_codes:
        stock_rows = db.execute(text("""
            WITH latest AS (SELECT MAX(trade_date) AS d FROM daily_quote)
            SELECT trade_date, stock_code, close
            FROM daily_quote, latest
            WHERE stock_code = ANY(:c) AND close > 0
              AND trade_date >= (SELECT d - INTERVAL '130 days' FROM latest)
            ORDER BY trade_date
        """), {"c": stk_codes}).fetchall()
        stocks = _stock_metrics(stock_rows)

    asof = max((str(q.get('trade_date'))[:10] for q in quotes.values()), default=None)
    hist_rows = []
    if asof:
        start = (date.fromisoformat(asof) - timedelta(days=400)).isoformat()
        hist_rows = db.execute(text("""
            SELECT trade_date, bond_code, close FROM cb_daily
            WHERE trade_date >= :s AND bond_code = ANY(:c) AND close > 0
            ORDER BY trade_date
        """), {"s": start, "c": codes}).fetchall()
    price_hist = {k: [c for _, c in v] for k, v in _hist_close_map(hist_rows).items()}
    return {'basics': basics, 'quotes': quotes, 'shares': shares,
            'stocks': stocks, 'price_hist': price_hist, 'asof': asof}


def _build_item(bundle, bond_code):
    b = bundle['basics'].get(bond_code)
    if not b:
        return None
    q = bundle['quotes'].get(bond_code) or {}
    s = bundle['shares'].get(bond_code) or {}
    stk = b.get('stk_code')
    sm = bundle['stocks'].get(stk) or {}
    conv_price = _f(s.get('convert_price')) or _f(b.get('conv_price'))
    price = _f(q.get('close'))
    if not price or price <= 0:
        price = _f(q.get('pre_close'))
    stock_close = sm.get('close_on') or sm.get('close')
    cv = conversion_value(conv_price, stock_close)
    prem = premium_rate(price, cv)
    dl = double_low(price, prem)
    remain = _f(s.get('remain_size')) or _f(b.get('remain_size'))
    days_m = days_until(b.get('maturity_date'))
    # 纯债价值必须按「剩余期限」折现；用债券总期限会严重低估临近到期债的债底
    rem_years = days_m / 365.25 if days_m and days_m > 0 else None
    pbv = pure_bond_value(_f(b.get('coupon_rate')), rem_years or _f(b.get('maturity')))
    pbd = pure_bond_premium(price, pbv)
    item = {
        'bond_code': bond_code,
        'ts_code': b.get('ts_code'),
        'exchange': b.get('exchange'),
        'bond_name': b.get('bond_short_name'),
        'bond_full_name': b.get('bond_full_name'),
        'stock_code': stk,
        'stock_name': b.get('stk_short_name'),
        'trade_date': _d(q.get('trade_date')),
        'price': price,
        'pre_close': _f(q.get('pre_close')),
        'pct_chg': _f(q.get('pct_chg')),
        'vol': _f(q.get('vol')),
        'amount_wan': _f(q.get('amount')),
        'conv_price': conv_price,
        'convert_ratio': _f(s.get('convert_ratio')),
        'acc_convert_ratio': _f(s.get('acc_convert_ratio')),
        'total_shares': _f(s.get('total_shares')),
        'conv_value': cv,
        'premium_rate': prem,
        'double_low': dl,
        'pure_bond_value': pbv,
        'pure_bond_premium': pbd,
        'remaining_size': remain,
        'turnover_pct': turnover_pct(q.get('amount'), remain),
        'issue_size': _f(b.get('issue_size')),
        'list_date': _d(b.get('list_date')),
        'delist_date': _d(b.get('delist_date')),
        'maturity_date': _d(b.get('maturity_date')),
        'conv_start_date': _d(b.get('conv_start_date')),
        'conv_end_date': _d(b.get('conv_end_date')),
        'conv_stop_date': _d(b.get('conv_stop_date')),
        'coupon_rate': _f(b.get('coupon_rate')),
        'maturity_years': _f(b.get('maturity')),
        'days_to_maturity': days_m,
        'days_to_conv_stop': days_until(b.get('conv_stop_date')),
        'days_to_delist': days_until(b.get('delist_date')),
        'stock_close': stock_close,
        'stock_pct_chg': sm.get('pct_chg'),
        'stock_ma20': sm.get('ma20'),
        'stock_ma60': sm.get('ma60'),
        'stock_to_force_pct': stock_to_force_pct(stock_close, conv_price),
        'price_percentile': price_percentile(price, bundle['price_hist'].get(bond_code)),
        'forced_redemption_days': forced_redemption_days(sm.get('closes'), conv_price),
    }
    item['strategies'] = strategy_keys(item)
    item.update(score_bond(item))
    return item


_SORT_FIELDS = {
    'score': 'score', 'price': 'price', 'premium': 'premium_rate',
    'double_low': 'double_low', 'pct_chg': 'pct_chg',
    'stock_pct_chg': 'stock_pct_chg', 'remaining_size': 'remaining_size',
    'turnover': 'turnover_pct', 'price_percentile': 'price_percentile',
    'maturity_date': 'maturity_date',
}


def _sort_items(items, sort: str, order: str):
    """按字段排序，None 永远放最后（方向无关）。"""
    key = _SORT_FIELDS.get(sort, 'score')
    is_str = key == 'maturity_date'
    def val(x):
        v = x.get(key)
        if is_str:
            return str(v or '')
        return _f(v)
    present = [x for x in items if (val(x) is not None if not is_str else bool(x.get(key)))]
    missing = [x for x in items if x not in present]
    present.sort(key=val, reverse=(order == 'desc'))
    return present + missing


def _active(item):
    dd = item.get('delist_date')
    remain = _f(item.get('remaining_size'))
    return (dd is None or dd >= date.today().isoformat()) and (remain is None or remain > 0)


def _load_items(db, only_active=True, bond_codes=None):
    bundle = _load_bundle(db, bond_codes=bond_codes)
    items = []
    for code in bundle['basics']:
        it = _build_item(bundle, code)
        if it and (not only_active or _active(it)):
            items.append(it)
    return items, bundle


def _filter_kw(items, kw):
    if not kw:
        return items
    kw = kw.lower()
    return [x for x in items if any(kw in str(x.get(k) or '').lower()
                                   for k in ('bond_code', 'ts_code', 'bond_name', 'bond_full_name',
                                             'stock_code', 'stock_name'))]


# ── 指标物化表 cb_metrics ─────────────────────────────────────────────
# 列表/概览/辅导/个股卡片/详情都读这张表；cb_daily 或 cb_basic 更新后自动重建。
# 约 1200 只 × 1 行，重建一次约 1-2 秒，远低于每次请求全量重算。

_metrics_refresh_lock = threading.Lock()


def _metrics_stale(db):
    """cb_metrics 为空，或落后于 cb_daily / cb_basic 时视为过期。"""
    row = db.execute(text("""
        SELECT (SELECT COUNT(*) FROM cb_metrics),
               (SELECT MAX(asof_date) FROM cb_metrics),
               (SELECT MAX(updated_at) FROM cb_metrics),
               (SELECT MAX(trade_date) FROM cb_daily),
               (SELECT MAX(updated_at) FROM cb_basic),
               (SELECT MAX(updated_at) FROM cb_share
                WHERE asof_date > COALESCE((SELECT MAX(asof_date) FROM cb_metrics),
                                           DATE '1900-01-01'))
    """)).fetchone()
    if not row or not row[0]:
        return True
    _, m_asof, m_upd, d_asof, b_upd, s_new_upd = row
    if d_asof and (not m_asof or m_asof < d_asof):
        return True
    if b_upd and (not m_upd or m_upd < b_upd):
        return True
    # 只关心「比物化表更新日期还新」的转股公告；历史回补不会反复触发重建
    if s_new_upd and (not m_upd or m_upd < s_new_upd):
        return True
    return False


def refresh_cb_metrics(db=None, force=False):
    """全量重建可转债指标物化表（幂等；DAG cb_daily 之后与过期时调用）。"""
    own_db = db is None
    db = db or get_sync_db()
    try:
        with _metrics_refresh_lock:
            # 跨进程互斥（多 worker 时线程锁不够）；拿到锁后重新判过期
            db.execute(text("SELECT pg_advisory_xact_lock(20261121)"))
            if not force and not _metrics_stale(db):
                db.commit()
                return {'count': 0, 'asof': None, 'skipped': True}
            items, bundle = _load_items(db, only_active=False)
            asof = bundle.get('asof')
            payloads = [
                {'c': it['bond_code'], 'a': asof,
                 'p': json.dumps(it, ensure_ascii=False, default=str)}
                for it in items
            ]
            # 单事务 DELETE+INSERT：读请求在提交前仍看到旧快照，不会读到空表
            db.execute(text("DELETE FROM cb_metrics"))
            if payloads:
                db.execute(text("""
                    INSERT INTO cb_metrics (bond_code, asof_date, payload, updated_at)
                    VALUES (:c, :a, CAST(:p AS JSONB), CURRENT_TIMESTAMP)
                """), payloads)
            db.commit()
            return {'count': len(payloads), 'asof': asof, 'skipped': False}
    except Exception:
        db.rollback()
        raise
    finally:
        if own_db:
            db.close()


def _cached_items(db, only_active=True):
    """从物化表读取指标项；过期则重建后读取。返回 (items, asof)。"""
    if _metrics_stale(db):
        refresh_cb_metrics(db)
    rows = db.execute(text("""
        SELECT payload, asof_date FROM cb_metrics ORDER BY bond_code
    """)).fetchall()
    items = []
    asof = None
    for r in rows:
        p = r[0]
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except (TypeError, ValueError):
                continue
        if isinstance(p, dict):
            items.append(p)
            if asof is None and r[1] is not None:
                asof = _d(r[1])
    if only_active:
        items = [x for x in items if _active(x)]
    return items, asof


def _load_payload(row):
    p = row[0] if row else None
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except (TypeError, ValueError):
            return None
    return p if isinstance(p, dict) else None


def _cached_one(db, bond_code):
    if _metrics_stale(db):
        refresh_cb_metrics(db)
    row = db.execute(text("""
        SELECT payload, asof_date FROM cb_metrics WHERE bond_code=:c
    """), {"c": bond_code}).fetchone()
    if not row:
        return None, None
    return _load_payload(row), _d(row[1])


def _cached_by_stock(db, stock_code):
    if _metrics_stale(db):
        refresh_cb_metrics(db)
    rows = db.execute(text("""
        SELECT payload FROM cb_metrics WHERE payload->>'stock_code'=:c
        ORDER BY bond_code
    """), {"c": stock_code}).fetchall()
    return [x for x in (_load_payload(r) for r in rows) if x]


@router.get("/cb/overview")
def cb_overview():
    db = get_sync_db()
    try:
        items, asof = _cached_items(db, only_active=True)
        total = db.execute(text("SELECT COUNT(*) FROM cb_basic")).scalar() or 0
        prices = [x['price'] for x in items if x.get('price')]
        prem = [x['premium_rate'] for x in items if x.get('premium_rate') is not None]
        pools = build_strategy_pools(items)
        return {
            'ok': True,
            'asof': asof,
            'total': total,
            'active': len(items),
            'median_price': sorted(prices)[len(prices)//2] if prices else None,
            'median_premium_pct': (sorted(prem)[len(prem)//2] * 100 if prem else None),
            'strategy_counts': {k: len(v) for k, v in pools.items()},
            'strategy_labels': STRATEGY_LABELS,
            'top': sorted(items, key=lambda x: (_f(x.get('score')) or -1) * -1)[:10],
        }
    finally:
        db.close()


@router.get("/cb/list")
def cb_list(
    kw: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    sort: str = Query('score'),
    order: str = Query('desc', description='desc/asc'),
    strategy: str = Query(None),
    only_active: bool = Query(True),
):
    db = get_sync_db()
    try:
        items, asof = _cached_items(db, only_active=only_active)
        items = _filter_kw(items, kw)
        if strategy and strategy in STRATEGY_LABELS:
            items = [x for x in items if strategy in x.get('strategies', [])]
        key = _SORT_FIELDS.get(sort, 'score')
        items = _sort_items(items, key, order)
        total = len(items)
        start = (page - 1) * page_size
        return {
            'asof': asof,
            'total': total, 'page': page, 'page_size': page_size,
            'items': items[start:start + page_size],
        }
    finally:
        db.close()


@router.get("/cb/coach")
def cb_coach():
    """规则化辅导侧栏：市场概况 + 投资要领建议 + 监控池（可关注/风险）。"""
    db = get_sync_db()
    try:
        items, asof = _cached_items(db, only_active=True)
        pools = build_strategy_pools(items)
        watch = watch_candidates(items, limit=6)
        prices = [x['price'] for x in items if x.get('price')]
        prem = [x['premium_rate'] for x in items if x.get('premium_rate') is not None]
        med_prem = (sorted(prem)[len(prem)//2] if prem else None)

        if med_prem is None:
            suggestion = '暂无足够数据，先运行可转债采集或补历史。'
        elif med_prem >= 0.6:
            suggestion = '整体溢价偏高，偏防守；优先看双低/债性池，进攻债必须要求正股强度。'
        elif med_prem >= 0.4:
            suggestion = '溢价中性；进攻债看低溢价+正股动量，防守债看双低/纯债保护。'
        else:
            suggestion = '整体溢价偏低，进攻机会更多；仍要避开高价位与临近强赎/到期标的。'

        return {
            'ok': True,
            'asof': asof,
            'active': len(items),
            'median_price': (sorted(prices)[len(prices)//2] if prices else None),
            'median_premium_pct': med_prem * 100 if med_prem is not None else None,
            'strategy_counts': {k: len(v) for k, v in pools.items()},
            'suggestion': suggestion,
            'focus': watch['focus'],
            'risk': watch['risk'],
        }
    finally:
        db.close()


@router.get("/stock/{code}/convertible-bonds")
def stock_convertible_bonds(code: str):
    db = get_sync_db()
    try:
        # 绝大多数个股没有转债：先用索引查代码；有债再从物化表按 JSONB 过滤
        has_bond = db.execute(text(
            "SELECT 1 FROM cb_basic WHERE stk_code=:c LIMIT 1"), {"c": code}).fetchone()
        if not has_bond:
            return {'ok': True, 'items': []}
        items = [x for x in _cached_by_stock(db, code)
                 if x.get('price') is not None and _active(x)]
        return {'ok': True, 'items': items}
    finally:
        db.close()


@router.get("/cb/{bond_code}")
def cb_detail(bond_code: str, days: int = Query(250, ge=30, le=1000)):
    db = get_sync_db()
    try:
        item, asof = _cached_one(db, bond_code)
        if not item:
            raise HTTPException(404, f'未找到可转债 {bond_code}')
        hist_rows = db.execute(text("""
            SELECT trade_date, close FROM cb_daily
            WHERE bond_code=:c AND close > 0 ORDER BY trade_date DESC LIMIT :d
        """), {"c": bond_code, "d": days}).fetchall()
        hist = list(reversed(hist_rows))
        stock_close_by_date = {}
        if item.get('stock_code'):
            sm_rows = db.execute(text("""
                SELECT trade_date, close FROM daily_quote
                WHERE stock_code=:c AND close > 0 ORDER BY trade_date DESC LIMIT :d
            """), {"c": item['stock_code'], "d": days}).fetchall()
            stock_close_by_date = {_d(r[0]): _f(r[1]) for r in reversed(sm_rows)}
        cp_steps = _conv_price_steps(db, bond_code)
        history = []
        for r in hist:
            d = _d(r[0])
            sc = stock_close_by_date.get(d)
            cp = _conv_price_on(cp_steps, d, item.get('conv_price'))
            cv = conversion_value(cp, sc)
            prem = premium_rate(_f(r[1]), cv)
            history.append({
                'date': d, 'price': _f(r[1]), 'stock_close': sc,
                'conv_value': cv, 'premium_rate': prem,
            })
        item['history'] = history
        return {'ok': True, 'asof': asof, 'item': item}
    finally:
        db.close()


@router.get("/cb/{bond_code}/daily")
def cb_daily_history(bond_code: str, days: int = Query(250, ge=30, le=1000)):
    db = get_sync_db()
    try:
        item_row = db.execute(text("""
            SELECT bond_code, stk_code, conv_price FROM cb_basic WHERE bond_code=:c
        """), {"c": bond_code}).fetchone()
        if not item_row:
            raise HTTPException(404, f'未找到可转债 {bond_code}')
        hist_rows = db.execute(text("""
            SELECT trade_date, close FROM cb_daily
            WHERE bond_code=:c AND close > 0 ORDER BY trade_date DESC LIMIT :d
        """), {"c": bond_code, "d": days}).fetchall()
        hist_rows = list(reversed(hist_rows))
        stock_rows = db.execute(text("""
            SELECT trade_date, close FROM daily_quote
            WHERE stock_code=:c AND close > 0 ORDER BY trade_date DESC LIMIT :d
        """), {"c": item_row[1], "d": days}).fetchall()
        stock_close_by_date = {_d(r[0]): _f(r[1]) for r in reversed(stock_rows)}
        conv_price = _f(item_row[2])
        cp_steps = _conv_price_steps(db, bond_code)
        out = []
        for r in hist_rows:
            day = _d(r[0])
            sc = stock_close_by_date.get(day)
            cv = conversion_value(_conv_price_on(cp_steps, day, conv_price), sc)
            out.append({
                'date': day, 'price': _f(r[1]),
                'stock_close': sc,
                'conv_value': cv,
                'premium_rate': premium_rate(_f(r[1]), cv),
            })
        return {'ok': True, 'data': out}
    finally:
        db.close()


@router.get("/stock/{code}/cb-linkage")
def stock_cb_linkage(code: str, days: int = Query(500, ge=60, le=5000)):
    """正股 × 关联转债联动分析：对齐日线、收益率、相关系数/滚动相关/溢价。"""
    db = get_sync_db()
    try:
        basics = db.execute(text("""
            SELECT bond_code, ts_code, bond_short_name, conv_price,
                   list_date, delist_date, maturity_date, issue_size, remain_size
            FROM cb_basic WHERE stk_code=:c ORDER BY bond_code
        """), {"c": code}).fetchall()
        if not basics:
            raise HTTPException(404, f'未找到 {code} 关联的可转债')
        bond_codes = [r[0] for r in basics]

        stock_name_row = db.execute(text("""
            SELECT stock_name FROM stock_master
            WHERE stock_code=:c ORDER BY CASE stock_type WHEN 'stock' THEN 1 ELSE 2 END LIMIT 1
        """), {"c": code}).fetchone()

        cb_rows = db.execute(text("""
            SELECT trade_date, bond_code, close FROM cb_daily
            WHERE bond_code = ANY(:c) AND close > 0
            ORDER BY trade_date DESC LIMIT :d
        """), {"c": bond_codes, "d": min(days * len(bond_codes), 20000)}).fetchall()
        stock_rows = db.execute(text("""
            SELECT trade_date, close FROM daily_quote
            WHERE stock_code=:c AND close > 0
            ORDER BY trade_date DESC LIMIT :d
        """), {"c": code, "d": days}).fetchall()

        cb_by_date = {}
        for r in reversed(cb_rows):
            cb_by_date.setdefault(_d(r[0]), {})[r[1]] = _f(r[2])
        stock_by_date = {_d(r[0]): _f(r[1]) for r in reversed(stock_rows)}
        dates = sorted(set(cb_by_date) & set(stock_by_date))[-days:]

        latest_rows = db.execute(text("""
            SELECT DISTINCT ON (bond_code) bond_code, trade_date, close, pct_chg
            FROM cb_daily WHERE bond_code = ANY(:c) AND close > 0
            ORDER BY bond_code, trade_date DESC
        """), {"c": bond_codes}).fetchall()
        latest = {r[0]: {'price': _f(r[2]), 'pct_chg': _f(r[3])} for r in latest_rows}

        bonds = []
        for b in basics:
            bc = b[0]
            conv_price = _f(b[3])
            if not dates:
                continue
            stock_prices = [stock_by_date[d] for d in dates]
            cb_prices = [cb_by_date[d].get(bc) for d in dates]
            pairs = [(d, sp, cp) for d, sp, cp in zip(dates, stock_prices, cb_prices)
                     if sp and sp > 0 and cp and cp > 0]
            if len(pairs) < 30:
                continue
            d = [p[0] for p in pairs]
            sp = [p[1] for p in pairs]
            cp = [p[2] for p in pairs]
            sr = daily_returns(sp)
            cr = daily_returns(cp)
            cv = [conversion_value(conv_price, x) for x in sp]
            prem = [premium_rate(cp[i], cv[i]) for i in range(len(cp))]
            s0, c0 = sp[0], cp[0]
            stock_idx = [x / s0 * 100 if s0 else None for x in sp]
            cb_idx = [x / c0 * 100 if c0 else None for x in cp]
            excess = [None if (sr[i] is None or cr[i] is None) else cr[i] - sr[i]
                      for i in range(len(sr))]
            metrics = linkage_metrics(sr, cr)
            prem_vals = [x for x in prem if x is not None]
            ret = {}
            for n in (5, 20, 60):
                sr_n = period_return(sp, n)
                cr_n = period_return(cp, n)
                ret[str(n)] = {
                    'stock': sr_n, 'cb': cr_n,
                    'excess': (cr_n - sr_n) if (sr_n is not None and cr_n is not None) else None,
                }
            bonds.append({
                'bond_code': bc,
                'bond_name': b[2],
                'conv_price': conv_price,
                'list_date': _d(b[4]),
                'delist_date': _d(b[5]),
                'maturity_date': _d(b[6]),
                'issue_size': _f(b[7]),
                'remain_size': _f(b[8]),
                'latest': latest.get(bc, {}),
                'metrics': {
                    **metrics,
                    'returns': ret,
                    'premium': {
                        'current': prem_vals[-1] if prem_vals else None,
                        'median': (sorted(prem_vals)[len(prem_vals)//2] if prem_vals else None),
                        'min': min(prem_vals) if prem_vals else None,
                        'max': max(prem_vals) if prem_vals else None,
                    },
                    'days': len(d),
                    'start_date': d[0],
                    'end_date': d[-1],
                },
                'label': linkage_label(metrics.get('corr_full'),
                                       ret['20']['excess'] if ret['20'] else None),
                'series': {
                    'dates': d,
                    'stock_close': sp,
                    'cb_close': cp,
                    'stock_idx': stock_idx,
                    'cb_idx': cb_idx,
                    'stock_ret': sr,
                    'cb_ret': cr,
                    'excess_ret': excess,
                    'conv_value': cv,
                    'premium_rate': prem,
                    'rolling_corr': rolling_corr(sr, cr, 20),
                },
            })
        if not bonds:
            raise HTTPException(404, f'{code} 关联转债的对齐样本不足')
        bonds.sort(key=lambda x: (x['metrics']['corr_full'] is None,
                                  -(x['metrics']['corr_full'] or -9)))
        return {
            'ok': True,
            'stock_code': code,
            'stock_name': stock_name_row[0] if stock_name_row else code,
            'days': days,
            'date_range': [dates[0], dates[-1]] if dates else [None, None],
            'bonds': bonds,
        }
    finally:
        db.close()

def _ret_stats(vals):
    """收益率样本统计：中位数/均值/胜率/分位。"""
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)

    def q(p):
        return s[min(n - 1, max(0, int(round(p * (n - 1)))))]

    return {
        'samples': n,
        'median': q(0.5),
        'mean': sum(s) / n,
        'win_rate': sum(1 for v in s if v > 0) / n,
        'p25': q(0.25),
        'p75': q(0.75),
        'min': s[0],
        'max': s[-1],
    }


@router.get("/cb/{bond_code}/expected-return")
def cb_expected_return(bond_code: str, lookback: int = Query(1500, ge=120, le=5000)):
    """买入预期收益测算（规则近似，不构成投资建议）。

    - 债性底：纯债价值 vs 现价 → 下行保护与债底修复年化
    - 强赎情景：正股涨到转股价×130% 时，转债按 130 元收敛 → 上行空间
    - 正股 ±10%/±20% 情景：转股价值联动，假设溢价率不变且不低于纯债价值
    - 历史实证：该债历史任一时点买入，持有 20/60/120 个交易日的前瞻收益分布，
      并给出与当前价格/溢价相近的子样本（相似估值环境）
    """
    db = get_sync_db()
    try:
        item, _ = _cached_one(db, bond_code)
        if not item:
            raise HTTPException(404, f'未找到可转债 {bond_code}')
        price = _f(item.get('price'))
        conv_price = _f(item.get('conv_price'))
        stock_close = _f(item.get('stock_close'))
        cv = _f(item.get('conv_value'))
        prem = _f(item.get('premium_rate'))
        pbv = _f(item.get('pure_bond_value'))
        days = item.get('days_to_maturity')
        if not price or price <= 0 or not conv_price or not stock_close:
            raise HTTPException(422, f'{bond_code} 缺少现价/转股价/正股行情，无法测算')
        years = (days / 365.25) if days and days > 0 else _f(item.get('maturity_years'))

        floor_ret = (pbv / price - 1) if pbv else None
        ytm = bond_ytm(price, _f(item.get('coupon_rate')), years)
        carry = None
        if pbv and years and years > 0:
            carry = (pbv / price) ** (1 / years) - 1
        force_stock = conv_price * 1.3
        stock_gain_to_force = force_stock / stock_close - 1
        upside_force = 130 / price - 1
        cushion = (1 - cv / price) if cv else None

        scenarios = []
        for m in (-0.2, -0.1, 0.1, 0.2):
            new_cv = conversion_value(conv_price, stock_close * (1 + m))
            est = new_cv * (1 + (prem or 0))
            if pbv:
                est = max(est, pbv)
            scenarios.append({
                'stock_move': m,
                'stock_price': stock_close * (1 + m),
                'conv_value': new_cv,
                'est_price': est,
                'return': est / price - 1,
            })

        # 历史前瞻收益：按当前转股价近似（更早日期用当时公告的转股价），
        # cb_share 的 asof_date 是公告日，用它做阶梯函数。
        cb_rows = db.execute(text("""
            SELECT trade_date, close FROM cb_daily
            WHERE bond_code=:c AND close > 0
            ORDER BY trade_date DESC LIMIT :n
        """), {"c": bond_code, "n": lookback}).fetchall()
        cb_hist = [( _d(r[0]), _f(r[1]) ) for r in reversed(cb_rows)]
        stock_by_date = {}
        if item.get('stock_code'):
            stock_rows = db.execute(text("""
                SELECT trade_date, close FROM daily_quote
                WHERE stock_code=:c AND close > 0
                ORDER BY trade_date DESC LIMIT :n
            """), {"c": item['stock_code'], "n": lookback}).fetchall()
            stock_by_date = {_d(r[0]): _f(r[1]) for r in reversed(stock_rows)}
        cp_steps = _conv_price_steps(db, bond_code)
        entries = []
        for day, cp in cb_hist:
            sc = stock_by_date.get(day)
            cpx = _conv_price_on(cp_steps, day, conv_price)
            prem_i = premium_rate(cp, conversion_value(cpx, sc)) if sc else None
            entries.append((day, cp, prem_i))

        windows = {}
        for h in (20, 60, 120):
            all_vals, sim_vals = [], []
            for i in range(len(cb_hist) - h):
                day, cp, prem_i = entries[i]
                fut = cb_hist[i + h][1]
                if not cp or not fut:
                    continue
                ret = fut / cp - 1
                all_vals.append(ret)
                if prem is not None and prem_i is not None:
                    if (abs(cp / price - 1) <= 0.15 and abs(prem_i - prem) <= 0.15):
                        sim_vals.append(ret)
            windows[str(h)] = {
                'all': _ret_stats(all_vals),
                'similar': _ret_stats(sim_vals),
            }

        return {
            'ok': True,
            'bond_code': bond_code,
            'asof': item.get('trade_date'),
            'price': price,
            'conv_price': conv_price,
            'stock_close': stock_close,
            'conv_value': cv,
            'premium_rate': prem,
            'pure_bond_value': pbv,
            'remaining_years': years,
            'floor_return': floor_ret,
            'floor_carry_annual': carry,
            'ytm_approx': ytm,
            'premium_cushion': cushion,
            'force_stock_price': force_stock,
            'stock_gain_to_force': stock_gain_to_force,
            'cb_price_at_force': 130.0,
            'upside_to_force': upside_force,
            'risk_reward': (upside_force / abs(floor_ret)
                            if floor_ret and floor_ret < 0 and upside_force > 0 else None),
            'scenarios': scenarios,
            'history': {
                'lookback_rows': len(cb_hist),
                'start_date': cb_hist[0][0] if cb_hist else None,
                'end_date': cb_hist[-1][0] if cb_hist else None,
                'windows': windows,
            },
            'notes': [
                '转股价值 = 100 ÷ 转股价 × 正股现价；溢价率 = 转债价 ÷ 转股价值 − 1。',
                '纯债价值 = 票面利息与面值按 3.5% 折现的债底（近似，不含信用风险）。',
                '强赎情景按正股达到转股价×130%、转债向 130 元收敛估算；实际强赎公告与溢价波动会影响结果。',
                '正股情景假设溢价率不变、且转债价不低于纯债价值，仅用于理解涨跌弹性。',
                '历史收益为该债历史上任一时点买入并持有 N 个交易日的收益分布（重叠样本）；'
                '相似样本 = 当前价格 ±15% 且溢价率 ±15 个百分点内的历史时点。',
                '以上为规则测算，不构成投资建议。',
            ],
        }
    finally:
        db.close()


@router.get("/cb/{bond_code}/share-history")
def cb_share_history(bond_code: str, days: int = Query(1000, ge=30, le=5000)):
    """转股比例 / 剩余规模历史（cb_share 按日历史）。"""
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT asof_date, convert_price, convert_ratio, acc_convert_ratio, remain_size
            FROM cb_share WHERE bond_code=:c
            ORDER BY asof_date DESC LIMIT :d
        """), {"c": bond_code, "d": days}).fetchall()
        if not rows:
            raise HTTPException(404, f'未找到可转债 {bond_code} 的转股历史')
        data = [
            {
                'date': _d(r[0]),
                'convert_price': _f(r[1]),
                'convert_ratio': _f(r[2]),
                'acc_convert_ratio': _f(r[3]),
                'remain_size': _f(r[4]),
            } for r in reversed(rows)
        ]
        return {'ok': True, 'data': data}
    finally:
        db.close()
