"""大盘 PEG 面板 API — 指数成分截面 + 个股 PEG 历史。

- GET /api/market/peg：当前指数成分的 PE_TTM × G 散点数据（按行业着色/过滤）；
- GET /api/stock/{code}/peg_history：个股 PEG 历史 + 滚动 20/50/80 分位带 + 分位。

口径见 app/peg.py；数据来自 stock_fundamentals(_history) / stock_income /
stock_consensus_daily / index_weight，只读，不触发采集。
"""
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import bindparam, text

from app.db.connection import get_sync_db
from app.api.market import _cached, _num
from app.peg import (annual_np_for_fy, compute_g_cons, compute_g_hist,
                     compute_peg_series, peg as calc_peg, shrink, valid_peg)

router = APIRouter(tags=["peg"])

INDEX_NAMES = {
    '000001.SH': '上证指数', '000016.SH': '上证50', '000300.SH': '沪深300',
    '000905.SH': '中证500', '000852.SH': '中证1000',
    '399001.SZ': '深证成指', '399006.SZ': '创业板指',
}
VALID_INDEXES = set(INDEX_NAMES)

# 与 crawler.consensus 的 flags 位保持一致
FLAG_NEG_NP = 1
FLAG_PARTIAL = 2
FLAG_FEW_ORGS = 4
FLAG_NO_HIST = 8
FLAG_CONS_FALLBACK = 16
FLAG_NO_CONS = 32


def _parse_date(v, name='as_of'):
    if v is None:
        return date.today()
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise HTTPException(400, f'{name} 日期格式应为 YYYY-MM-DD')


def _rows_to_dicts(rows):
    return [dict(r._mapping) for r in rows]


def _expanding(sql: str):
    return text(sql).bindparams(bindparam('codes', expanding=True))


def _default_code(items: list):
    """默认选中：完整口径且 PEG≤1 时优先，否则取全表 PEG 最低。

    完整口径只有 1-2 只且估值很贵时（如创业板仅迈瑞 PEG 3.6），不应压过
    其他 PEG 0.3 的占位口径个股；PEG=1 是“增长覆盖估值”的自然分界。
    """
    if not items:
        return None
    full = [it for it in items if it['peg_basis'] == 'full']
    if full and full[0]['peg'] <= 1.0:
        return full[0]['code']
    return min(items, key=lambda x: x['peg'])['code']


def _build_cross_section(index_code: str, asof: date, lam: float) -> dict:
    """计算指数成分截面（未按行业过滤），供缓存与缓存上层过滤复用。

    成分股取 asof 当日已知的最新一期快照（index_weight 为月末快照）。PIT 截止日
    必须约束成分股：PE / 一致预期 / 利润表都是 `<= asof`，成分股若取最新一期，
    历史 as_of 就混进了未来的调入调出（幸存者偏差）。
    """
    db = get_sync_db()
    try:
        snap = db.execute(text(
            "SELECT MAX(trade_date) FROM index_weight "
            "WHERE index_code=:idx AND trade_date <= :asof"),
            {"idx": index_code, "asof": asof}).scalar()
        if not snap:
            return {'as_of': asof.isoformat(), 'index': {
                        'code': index_code, 'name': INDEX_NAMES[index_code],
                        'snapshot_date': None},
                    'params': {'lam': lam, 'g_hist_years': 5, 'g_cons_horizon': 3},
                    'coverage': {'members': 0, 'with_pe': 0, 'with_consensus': 0,
                                 'with_peg': 0, 'with_peg_full': 0},
                    'industries': [], 'default_code': None, 'items': [],
                    'valuation_date': None, 'consensus_date': None,
                    'note': (f'{asof.isoformat()} 之前没有指数成分快照'
                             if asof < date.today()
                             else '指数成分快照缺失（先跑 index_weight 节点）')}

        members = _rows_to_dicts(db.execute(text("""
            SELECT w.stock_code,
                   COALESCE(NULLIF(m.industry_l1, ''), NULLIF(m.industry, ''),
                            '其他') AS industry_l1,
                   m.stock_name
            FROM index_weight w
            LEFT JOIN stock_master m
                   ON m.stock_code = w.stock_code AND m.stock_type = 'stock'
            WHERE w.index_code = :idx AND w.trade_date = :d
            ORDER BY w.stock_code
        """), {'idx': index_code, 'd': snap}).fetchall())
        codes = [m['stock_code'] for m in members]
        if not codes:
            return {'as_of': asof.isoformat(), 'index': {
                        'code': index_code, 'name': INDEX_NAMES[index_code],
                        'snapshot_date': str(snap)},
                    'params': {'lam': lam, 'g_hist_years': 5, 'g_cons_horizon': 3},
                    'coverage': {'members': 0, 'with_pe': 0, 'with_consensus': 0,
                                 'with_peg': 0, 'with_peg_full': 0},
                    'industries': [], 'default_code': None, 'items': [],
                    'valuation_date': None, 'consensus_date': None}

        pe_map = {r['stock_code']: r for r in _rows_to_dicts(db.execute(text("""
            SELECT c.stock_code, h.report_date AS trade_date,
                   h.pe_ttm, h.total_mv AS market_cap
            FROM unnest(CAST(:codes AS text[])) AS c(stock_code)
            CROSS JOIN LATERAL (
                SELECT report_date, pe_ttm, total_mv
                FROM stock_fundamentals_history
                WHERE stock_code = c.stock_code AND report_date <= :asof
                  AND pe_ttm IS NOT NULL
                ORDER BY report_date DESC
                LIMIT 1
            ) h
        """), {'codes': codes, 'asof': asof}).fetchall())}

        cons_map = {r['stock_code']: r for r in _rows_to_dicts(db.execute(_expanding("""
            SELECT DISTINCT ON (stock_code)
                   stock_code, asof_date, base_fy, fy1, fy2, fy3,
                   np_fy1, np_fy2, np_fy3, org_count, report_count,
                   np_dispersion, flags
            FROM stock_consensus_daily
            WHERE stock_code IN :codes AND asof_date <= :asof
            ORDER BY stock_code, asof_date DESC
        """), {'codes': codes, 'asof': asof}).fetchall())}

        income_by_code: dict = {}
        for r in _rows_to_dicts(db.execute(_expanding("""
            SELECT stock_code, end_date, ann_date, n_income_attr_p
            FROM stock_income
            WHERE stock_code IN :codes AND report_type = '1'
              AND EXTRACT(month FROM end_date) = 12
              AND ann_date <= :asof
            ORDER BY stock_code, end_date, ann_date
        """), {'codes': codes, 'asof': asof}).fetchall()):
            income_by_code.setdefault(r['stock_code'], []).append(r)
    finally:
        db.close()

    industries: dict = {}
    items = []
    for m in members:
        code = m['stock_code']
        ind = m['industry_l1'] or '其他'
        industries[ind] = industries.get(ind, 0) + 1

        pe_row = pe_map.get(code)
        cons = cons_map.get(code)
        annuals = income_by_code.get(code, [])
        pe_ttm = _num(pe_row['pe_ttm']) if pe_row else None

        g_hist, _, hist_years = compute_g_hist(annuals, asof)
        base_np = annual_np_for_fy(annuals, asof, cons['base_fy']) if cons else None
        g_cons, horizon = compute_g_cons(cons, base_np)
        g = shrink(g_cons, g_hist, lam)
        pv = calc_peg(pe_ttm, g)
        org_count = int(cons['org_count'] or 0) if cons else 0
        full = valid_peg(pe_ttm, g, pv, org_count)
        # 一致预期历史回补期（report_rc 限 10 次/天）允许 G_hist 单独成 PEG 占位；
        # 与完整口径分开展示（peg_basis），默认选中优先完整口径。占位口径不使用
        # 机构数不足的 g_cons，避免把“未达 3 家”的预期混进历史口径。
        hist_pv = calc_peg(pe_ttm, g_hist)
        hist_only = (not full and pe_ttm is not None and pe_ttm > 0
                     and g_hist is not None and g_hist > 0
                     and hist_pv is not None and 0 < hist_pv <= 10)
        if not (full or hist_only):
            continue
        if not full:
            g, pv = g_hist, hist_pv

        flags = int(cons['flags'] or 0) if cons else 0
        if g_hist is None:
            flags |= FLAG_NO_HIST
        if g_cons is None:
            flags |= FLAG_NO_CONS
        elif horizon and horizon < 3:
            flags |= FLAG_CONS_FALLBACK
        source = 'consensus' if full else 'income'
        items.append({
            'code': code,
            'name': m['stock_name'],
            'industry_l1': ind,
            'pe_ttm': _num(pe_ttm, 2),
            'g_cons': _num(g_cons, 4),
            'g_hist': _num(g_hist, 4),
            'g': _num(g, 4),
            'peg': _num(pv, 2),
            'peg_pct': None,
            'peg_basis': 'full' if full else 'hist',
            'org_count': org_count,
            'market_cap': _num(pe_row['market_cap']) if pe_row else None,
            'flags': flags,
            'growth_source': source,
            'g_cons_horizon': horizon,
            'hist_years': hist_years,
            'pe_date': str(pe_row['trade_date']) if pe_row else None,
            'consensus_date': str(cons['asof_date']) if cons else None,
        })

    # 截面 PEG 分位（越低越好）：先按 PEG 排序算分位，再按「完整口径优先」展示
    n = len(items)
    for i, it in enumerate(sorted(items, key=lambda x: x['peg'])):
        it['peg_pct'] = round(i / (n - 1) * 100, 1) if n > 1 else 50.0
    items.sort(key=lambda x: (0 if x['peg_basis'] == 'full' else 1, x['peg']))

    valuation_date = max((str(r['trade_date']) for r in pe_map.values()), default=None)
    consensus_date = max((str(r['asof_date']) for r in cons_map.values()), default=None)
    return {
        'as_of': valuation_date or asof.isoformat(),
        'index': {'code': index_code, 'name': INDEX_NAMES[index_code],
                  'snapshot_date': str(snap)},
        'params': {'lam': lam, 'g_hist_years': 5, 'g_cons_horizon': 3},
        'coverage': {
            'members': len(members),
            'with_pe': sum(1 for m in members if m['stock_code'] in pe_map),
            'with_consensus': sum(1 for m in members if m['stock_code'] in cons_map),
            'with_peg': len(items),
            'with_peg_full': sum(1 for it in items if it['peg_basis'] == 'full'),
        },
        'industries': [{'name': k, 'count': v}
                       for k, v in sorted(industries.items(), key=lambda kv: -kv[1])],
        'default_code': _default_code(items),
        'items': items,
        'valuation_date': valuation_date,
        'consensus_date': consensus_date,
    }


@router.get('/market/peg')
def market_peg(index_code: str = Query('000300.SH'),
               industry_l1: str = Query(None),
               lam: float = Query(0.5, ge=0.0, le=1.0),
               as_of: str = Query(None, description='PIT 截止日 YYYY-MM-DD')):
    if index_code not in VALID_INDEXES:
        raise HTTPException(400, f'不支持的指数 {index_code}，可选：{sorted(VALID_INDEXES)}')
    limit = _parse_date(as_of)
    payload = _cached(('market_peg', index_code, limit.isoformat(), round(lam, 4)),
                      300, lambda: _build_cross_section(index_code, limit, lam))
    # 行业过滤在缓存之外做，industries 始终基于未过滤全集
    items = payload['items']
    if industry_l1:
        items = [it for it in items if it['industry_l1'] == industry_l1]
    else:
        items = list(items)
    return {**payload, 'items': items,
            'default_code': _default_code(items),
            'filter': {'industry_l1': industry_l1 or None}}


@router.get('/stock/{code}/peg_history')
def stock_peg_history(code: str,
                      years: int = Query(5, ge=1, le=15),
                      lam: float = Query(0.5, ge=0.0, le=1.0),
                      as_of: str = Query(None, description='PIT 截止日 YYYY-MM-DD')):
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(400, '股票代码应为 6 位数字')
    end = _parse_date(as_of)
    # 多取 6 年做滚动窗口预热（窗口 1250 交易日 ≈ 5 年），展示只保留请求年数
    start = end - timedelta(days=365 * (years + 6))
    snap_start = start - timedelta(days=200)

    db = get_sync_db()
    try:
        master = db.execute(text("""
            SELECT stock_code, stock_name,
                   COALESCE(NULLIF(industry_l1, ''), NULLIF(industry, ''), '其他') AS industry_l1
            FROM stock_master WHERE stock_code=:c AND stock_type='stock' LIMIT 1
        """), {'c': code}).fetchone()
        pe_rows = db.execute(text("""
            SELECT report_date AS trade_date, pe_ttm
            FROM stock_fundamentals_history
            WHERE stock_code=:c AND pe_ttm IS NOT NULL
              AND report_date BETWEEN :s AND :e
            ORDER BY report_date
        """), {'c': code, 's': start, 'e': end}).fetchall()
        if not pe_rows:
            display_start = end - timedelta(days=365 * years)
            return {'code': code, 'name': master[1] if master else None,
                    'industry_l1': master[2] if master else None,
                    'data': [], 'latest': None,
                    'reason': '该股无 PE_TTM 历史（stock_fundamentals_history 未覆盖）',
                    # meta 与正常分支同形状（计数为 0），前端不必区分两种结构
                    'meta': {'start': start.isoformat(), 'end': end.isoformat(),
                             'display_start': display_start.isoformat(),
                             'years': years, 'lam': lam, 'window': 1250, 'min_periods': 250,
                             'points': 0, 'valid_peg': 0, 'consensus_points': 0,
                             'hist_points': 0, 'fallback_points': 0, 'first_consensus': None,
                             'org_median': None, 'org_min': None}}
        annuals = _rows_to_dicts(db.execute(text("""
            SELECT end_date, ann_date, n_income_attr_p
            FROM stock_income
            WHERE stock_code=:c AND report_type='1'
              AND EXTRACT(month FROM end_date) = 12
              AND ann_date <= :e
            ORDER BY end_date, ann_date
        """), {'c': code, 'e': end}).fetchall())
        snaps = _rows_to_dicts(db.execute(text("""
            SELECT asof_date, base_fy, fy1, fy2, fy3,
                   np_fy1, np_fy2, np_fy3, org_count, flags
            FROM stock_consensus_daily
            WHERE stock_code=:c AND asof_date BETWEEN :s AND :e
            ORDER BY asof_date
        """), {'c': code, 's': snap_start, 'e': end}).fetchall())
    finally:
        db.close()

    series = compute_peg_series(
        [(r[0], r[1]) for r in pe_rows], annuals, snaps, lam=lam)
    display_start = end - timedelta(days=365 * years)
    data = [r for r in series if r['date'] >= display_start.isoformat()]
    latest = next((r for r in reversed(data) if r['peg'] is not None), None)
    if latest is None:
        latest = next((r for r in reversed(series) if r['peg'] is not None), None)

    def _fmt(r):
        return {**r,
                'pe_ttm': _num(r['pe_ttm'], 2),
                'g_hist': _num(r['g_hist'], 4),
                'g_cons': _num(r['g_cons'], 4),
                'g': _num(r['g'], 4),
                'peg': _num(r['peg'], 2),
                'peg_hist': _num(r['peg_hist'], 2),
                'band20': _num(r['band20'], 2),
                'band50': _num(r['band50'], 2),
                'band80': _num(r['band80'], 2),
                'peg_pct': _num(r['peg_pct'], 1)}

    # 一致预期口径各点的机构数：历史快照是每 20 个交易日抽样回补出来的（report_rc
    # 每天 10 次配额），180 天窗口里只有 6~7 个发布日，org_count 系统性偏低——
    # 前端要把它说出来，否则「一致预期」三个字会让人以为是完整券商覆盖
    org_counts = sorted(int(r['org_count']) for r in data
                        if r.get('g_cons') is not None and r.get('org_count') is not None)

    return {
        'code': code,
        'name': master[1] if master else None,
        'industry_l1': master[2] if master else None,
        'data': [_fmt(r) for r in data],
        'latest': _fmt(latest) if latest else None,
        'meta': {
            'start': start.isoformat(), 'end': end.isoformat(),
            'display_start': display_start.isoformat(),
            'years': years, 'lam': lam, 'window': 1250, 'min_periods': 250,
            'points': len(data),
            'valid_peg': sum(1 for r in data if r['peg'] is not None),
            'consensus_points': sum(1 for r in data if r['g_cons'] is not None),
            'hist_points': sum(1 for r in data if r['g_hist'] is not None),
            'fallback_points': sum(1 for r in data if r.get('mode') == 'hist'),
            'first_consensus': next((r['date'] for r in data
                                     if r.get('mode') == 'consensus'), None),
            'org_median': org_counts[len(org_counts) // 2] if org_counts else None,
            'org_min': org_counts[0] if org_counts else None,
        },
    }
