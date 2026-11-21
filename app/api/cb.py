"""可转债 API：列表/策略/详情/历史 + 正股关联。

数据域独立（cb_basic/cb_daily/cb_share），不进 daily_quote/stock_master。
列表在 Python 内计算转股溢价/双低/评分（约 500 只规模），无需物化表。
"""
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.cb_analytics import (
    STRATEGY_LABELS,
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


def _load_bundle(db):
    """一次拉齐 cb_basic + 最新行情/转股快照/正股行情与价格分位历史。"""
    basic_rows = db.execute(text("""
        SELECT bond_code, ts_code, exchange, bond_short_name, bond_full_name,
               stk_code, stk_short_name, list_date, delist_date, maturity_date,
               conv_start_date, conv_end_date, conv_stop_date,
               first_conv_price, conv_price, issue_size, remain_size,
               coupon_rate, maturity, par, value_date, rate_clause
        FROM cb_basic ORDER BY bond_code
    """)).fetchall()
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
        SELECT bond_code, asof_date, issue_size, convert_price_initial, convert_price,
               convert_ratio, acc_convert_ratio, remain_size, total_shares
        FROM cb_share WHERE bond_code = ANY(:c)
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
    pbv = pure_bond_value(_f(b.get('coupon_rate')), _f(b.get('maturity')))
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
        'days_to_maturity': days_until(b.get('maturity_date')),
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


def _load_items(db, only_active=True):
    bundle = _load_bundle(db)
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


@router.get("/cb/overview")
def cb_overview():
    db = get_sync_db()
    try:
        items, bundle = _load_items(db, only_active=True)
        prices = [x['price'] for x in items if x.get('price')]
        prem = [x['premium_rate'] for x in items if x.get('premium_rate') is not None]
        pools = build_strategy_pools(items)
        return {
            'ok': True,
            'asof': bundle.get('asof'),
            'total': len(bundle['basics']),
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
        items, bundle = _load_items(db, only_active=only_active)
        items = _filter_kw(items, kw)
        if strategy and strategy in STRATEGY_LABELS:
            items = [x for x in items if strategy in x.get('strategies', [])]
        key = _SORT_FIELDS.get(sort, 'score')
        items = _sort_items(items, key, order)
        total = len(items)
        start = (page - 1) * page_size
        return {
            'asof': bundle.get('asof'),
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
        items, bundle = _load_items(db, only_active=True)
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
            'asof': bundle.get('asof'),
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
        items, _ = _load_items(db, only_active=False)
        rows = [x for x in items if x.get('stock_code') == code]
        return {'ok': True, 'items': rows}
    finally:
        db.close()


@router.get("/cb/{bond_code}")
def cb_detail(bond_code: str, days: int = Query(250, ge=30, le=1000)):
    db = get_sync_db()
    try:
        items, bundle = _load_items(db, only_active=False)
        item = next((x for x in items if x['bond_code'] == bond_code), None)
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
        history = []
        for r in hist:
            d = _d(r[0])
            sc = stock_close_by_date.get(d)
            cv = conversion_value(item.get('conv_price'), sc)
            prem = premium_rate(_f(r[1]), cv)
            history.append({
                'date': d, 'price': _f(r[1]), 'stock_close': sc,
                'conv_value': cv, 'premium_rate': prem,
            })
        item['history'] = history
        return {'ok': True, 'asof': bundle.get('asof'), 'item': item}
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
        return {'ok': True, 'data': [
            {
                'date': _d(r[0]), 'price': _f(r[1]),
                'stock_close': stock_close_by_date.get(_d(r[0])),
                'conv_value': conversion_value(conv_price, stock_close_by_date.get(_d(r[0]))),
                'premium_rate': premium_rate(_f(r[1]),
                                             conversion_value(conv_price, stock_close_by_date.get(_d(r[0])))),
            } for r in hist_rows]}
    finally:
        db.close()


@router.get("/stock/{code}/cb-linkage")
def stock_cb_linkage(code: str, days: int = Query(500, ge=60, le=1000)):
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
