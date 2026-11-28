"""PEG 计算（纯函数，无 DB 依赖）。

口径（已与用户确认）：
- 归母净利口径：历史用 stock_income.n_income_attr_p，前瞻用 report_rc.np 中位数；
- G_hist = 最近年报相对 5 年前年报的归母净利 CAGR（PIT：ann_date <= asof）；
- G_cons = 一致预期 FY1→FY3 归母净利 CAGR（不足 3 年回退 2/1 年）；
- G = G_hist + λ × max(G_cons − G_hist, 0)，λ=0.5（单边收缩，只抑制乐观外推）；
- PEG = PE_TTM / (G × 100)。

所有函数对负/零基数返回 None，由上层决定展示 N/A 或 flag，绝不静默算出一个
看似正常的负数 PEG。
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

import numpy as np
import pandas as pd


def _to_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _fv(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def annual_cagr(latest: Optional[float], base: Optional[float],
                years: Optional[int]) -> Optional[float]:
    """(latest/base)^(1/years) - 1；负/零基数或年数 <1 返回 None。"""
    latest, base, years = _fv(latest), _fv(base), int(years or 0)
    if latest is None or base is None or latest <= 0 or base <= 0 or years < 1:
        return None
    return (latest / base) ** (1.0 / years) - 1.0


def _pit_annuals(annual_rows: Iterable[dict], asof) -> list:
    """按 PIT 整理年报：ann_date <= asof，同一财年保留最新公告版本。

    返回按 fiscal_year 升序的 [{fy, np, end_date, ann_date}]。
    """
    limit = _to_date(asof)
    if limit is None:
        return []
    by_fy = {}
    for r in annual_rows or []:
        end = _to_date(r.get('end_date'))
        ann = _to_date(r.get('ann_date'))
        npv = _fv(r.get('n_income_attr_p'))
        if end is None or ann is None or ann > limit or npv is None:
            continue
        fy = end.year
        prev = by_fy.get(fy)
        # 同一财年可能有修订版：取 ann_date 最新的一份
        if prev is None or ann >= prev['ann_date']:
            by_fy[fy] = {'fy': fy, 'np': npv, 'end_date': end, 'ann_date': ann}
    return [by_fy[k] for k in sorted(by_fy)]


def pick_hist_base(annual_rows: Iterable[dict], asof, years: int = 5) -> Optional[dict]:
    """选最近年报与目标基年（默认 5 年前），返回 CAGR 所需的基期信息。

    精确 5 年缺失时退而求其次用 4 年或 6 年（真实跨度 >=3 年才接受），
    避免大量次新股因单年缺失直接失去 PEG。
    """
    annuals = _pit_annuals(annual_rows, asof)
    if not annuals:
        return None
    latest = annuals[-1]
    if latest['np'] <= 0:
        return None
    target = latest['fy'] - int(years)
    candidates = {r['fy']: r for r in annuals if r['fy'] < latest['fy'] and r['np'] > 0}
    chosen = candidates.get(target)
    if chosen is None:
        for fy in (target + 1, target - 1):
            if fy in candidates:
                chosen = candidates[fy]
                break
    if chosen is None:
        return None
    span = latest['fy'] - chosen['fy']
    if span < 3:
        return None
    return {
        'base_fy': chosen['fy'], 'base_np': chosen['np'],
        'years': span, 'latest_fy': latest['fy'], 'latest_np': latest['np'],
        'source': 'income',
    }


def compute_g_hist(annual_rows: Iterable[dict], asof,
                   years: int = 5) -> tuple[Optional[float], Optional[str], Optional[int]]:
    """历史归母净利 CAGR，返回 (g_hist, source, 实际年数)。"""
    base = pick_hist_base(annual_rows, asof, years)
    if not base:
        return None, None, None
    g = annual_cagr(base['latest_np'], base['base_np'], base['years'])
    return g, base['source'], base['years']


def annual_np_for_fy(annual_rows: Iterable[dict], asof,
                     fy) -> Optional[float]:
    """PIT 取指定财年的归母净利（同财年多版本取 ann_date 最新）。"""
    try:
        fy = int(fy)
    except (TypeError, ValueError):
        return None
    for r in _pit_annuals(annual_rows, asof):
        if r['fy'] == fy:
            return r['np']
    return None


def compute_g_cons(snapshot: Optional[dict],
                   base_np: Optional[float]) -> tuple[Optional[float], Optional[int]]:
    """一致预期归母净利 CAGR：FY3 优先，不足回退 FY2/FY1。

    snapshot 需含 base_fy/fy1..fy3/np_fy1..np_fy3（stock_consensus_daily 一行）。
    base_np 是 snapshot.base_fy 对应年报的归母净利（PIT）。
    """
    if not snapshot:
        return None, None
    base_fy = snapshot.get('base_fy')
    base_np = _fv(base_np)
    if base_fy is None or base_np is None or base_np <= 0:
        return None, None
    for h in (3, 2, 1):
        fy = snapshot.get(f'fy{h}')
        npf = _fv(snapshot.get(f'np_fy{h}'))
        if fy is None or npf is None or npf <= 0:
            continue
        if int(fy) != int(base_fy) + h:
            continue
        return (npf / base_np) ** (1.0 / h) - 1.0, h
    return None, None


def shrink(g_cons: Optional[float], g_hist: Optional[float],
           lam: float = 0.5) -> Optional[float]:
    """单边收缩：G = G_hist + λ×max(G_cons−G_hist, 0)。

    - 一致预期低于历史：完全采信历史（不向下收缩，避免双重保守）；
    - 缺历史锚：退化为 G_cons（调用方通过 source 标记提示）；
    - 缺一致预期：用历史值；
    - 两者都缺：None。
    """
    gc, gh = _fv(g_cons), _fv(g_hist)
    if gh is None:
        return gc
    if gc is None:
        return gh
    return gh + float(lam) * max(gc - gh, 0.0)


def peg(pe_ttm: Optional[float], g: Optional[float]) -> Optional[float]:
    """PEG = PE_TTM / (G×100)，G 为小数（0.15 = 15%）。"""
    pe, gv = _fv(pe_ttm), _fv(g)
    if pe is None or gv is None or pe <= 0 or gv <= 0:
        return None
    return pe / (gv * 100.0)


def valid_peg(pe_ttm: Optional[float], g: Optional[float],
              peg_value: Optional[float], org_count: Optional[int]) -> bool:
    """散点是否进入“有效 PEG”集合（默认选中/分位统计同用此口径）。"""
    pe, gv, pv = _fv(pe_ttm), _fv(g), _fv(peg_value)
    try:
        orgs = int(org_count or 0)
    except (TypeError, ValueError):
        orgs = 0
    return (pe is not None and pe > 0 and gv is not None and gv > 0
            and pv is not None and 0 < pv <= 10 and orgs >= 3)


# 历史带/分位的 winsorize 上限：G 接近 0 时 PEG → ∞（实测中证300 内最极端
# 单日 8883 倍），不截断会把 20-80 分位带压成一条线。原始 peg 仍逐日返回，
# 只影响 band/pct 的统计口径；与散点 valid_peg 的 10 倍上限一致。
PEG_WINSOR_MAX = 10.0


def _rolling_percentile(s: pd.Series, window: int, min_periods: int) -> pd.Series:
    """当前值在滚动窗口内的分位（0-100，含当前值；NaN 跳过）。

    与逐窗口 (valid <= cur).mean() 等价：rolling.rank(method='max', pct=True)
    把并列值计为最高名次，正好对应 <= 的计数；向量化后 2700 点从 ~1.8s
    降到毫秒级（曾是大盘 PEG 历史接口的主要耗时）。
    """
    cnt = s.rolling(window, min_periods=min_periods).count()
    pct = s.rolling(window, min_periods=min_periods).rank(
        method='max', pct=True) * 100.0
    pct[cnt < 2] = np.nan
    return pct


def compute_peg_series(pe_rows: Iterable[tuple], annual_rows: Iterable[dict],
                       snapshots: Iterable[dict], lam: float = 0.5,
                       window: int = 1250, min_periods: int = 250) -> list:
    """逐日计算 PE/G/PEG 及 PEG 自身滚动分位带。

    参数：
      pe_rows: [(trade_date, pe_ttm)]，按日期升序；允许 PE 缺失（None）。
      annual_rows: stock_income 年报（end_date/ann_date/n_income_attr_p）。
      snapshots: stock_consensus_daily 行（asof_date/base_fy/fy1..np_fy3/org_count），
                 按 asof_date 升序。
      window/min_periods: 滚动窗口（默认 1250 交易日 ≈ 5 年，至少 250 个有效值）。

    band/pct 用 PEG 的 10 倍 winsorize 序列计算（PEG 原始值照常返回），
    避免近零增速产生的千倍离群值把分位带压平。

    返回逐日 [{date, pe_ttm, g_hist, g_cons, g, peg, band20, band50, band80,
              peg_pct, ng_hist, ng_cons, org_count}]；band/pct 在样本不足时为 None。
    """
    anns = []
    for r in annual_rows or []:
        end, ann = _to_date(r.get('end_date')), _to_date(r.get('ann_date'))
        npv = _fv(r.get('n_income_attr_p'))
        if end and ann and npv is not None:
            anns.append({'fy': end.year, 'np': npv, 'ann_date': ann})
    anns.sort(key=lambda x: x['ann_date'])
    snaps = sorted((s for s in (snapshots or []) if _to_date(s.get('asof_date'))),
                   key=lambda s: _to_date(s['asof_date']))

    out = []
    ai = si = 0
    by_fy: dict = {}
    latest_fy = None
    snap = None
    for td, pe_ttm in pe_rows or []:
        t = _to_date(td)
        if t is None:
            continue
        while ai < len(anns) and anns[ai]['ann_date'] <= t:
            a = anns[ai]
            by_fy[a['fy']] = a
            if latest_fy is None or a['fy'] > latest_fy:
                latest_fy = a['fy']
            ai += 1
        while si < len(snaps) and _to_date(snaps[si]['asof_date']) <= t:
            snap = snaps[si]
            si += 1

        g_hist = g_cons = g = None
        ng_hist = ng_cons = None
        base = None
        if latest_fy is not None:
            base = pick_hist_base(
                [{'end_date': date(fy, 12, 31), 'ann_date': r['ann_date'],
                  'n_income_attr_p': r['np']} for fy, r in by_fy.items()], t)
            if base:
                g_hist = annual_cagr(base['latest_np'], base['base_np'], base['years'])
        if snap:
            s_base_np = None
            s_fy = snap.get('base_fy')
            if s_fy is not None and int(s_fy) in by_fy:
                s_base_np = by_fy[int(s_fy)]['np']
            g_cons, ng_cons = compute_g_cons(snap, s_base_np)
            if g_cons is None:
                ng_cons = None
        g = shrink(g_cons, g_hist, lam)
        pv = peg(pe_ttm, g)
        pv_hist = peg(pe_ttm, g_hist)
        mode = ('consensus' if g_cons is not None
                else ('hist' if g_hist is not None else None))
        out.append({
            'date': t.isoformat(),
            'pe_ttm': _fv(pe_ttm),
            'g_hist': g_hist,
            'g_cons': g_cons,
            'g': g,
            'peg': pv,
            'peg_hist': pv_hist,
            'mode': mode,
            'band20': None, 'band50': None, 'band80': None, 'peg_pct': None,
            'ng_hist': base['years'] if (latest_fy is not None and base) else None,
            'ng_cons': ng_cons,
            'org_count': snap.get('org_count') if snap else None,
        })

    if not out:
        return out
    s = pd.Series([r['peg'] for r in out], dtype='float64')
    s_band = s.clip(upper=PEG_WINSOR_MAX)
    for key, q in (('band20', 0.2), ('band50', 0.5), ('band80', 0.8)):
        vals = s_band.rolling(window, min_periods=min_periods).quantile(q)
        for i, v in enumerate(vals.tolist()):
            out[i][key] = None if (v is None or v != v) else float(v)
    pct = _rolling_percentile(s_band, window, min_periods)
    for i, v in enumerate(pct.tolist()):
        out[i]['peg_pct'] = None if (v is None or v != v) else float(v)
    return out
