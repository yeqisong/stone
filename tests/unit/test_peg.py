"""PEG 纯函数单测（CAGR / 收缩 / PIT / 滚动分位）。"""
from datetime import date, timedelta

import pytest

from app.peg import (annual_cagr, annual_np_for_fy, compute_g_cons,
                     compute_g_hist, compute_peg_series, enough_orgs, peg,
                     pick_hist_base, shrink, valid_peg)


def _annual(fy, np, ann=None):
    return {'end_date': date(fy, 12, 31),
            'ann_date': date.fromisoformat(ann or f'{fy + 1}-04-30'),
            'n_income_attr_p': np}


def test_annual_cagr():
    g = annual_cagr(200, 100, 5)
    assert g == pytest.approx(2 ** 0.2 - 1)
    assert annual_cagr(0, 100, 5) is None
    assert annual_cagr(100, -1, 5) is None
    assert annual_cagr(100, 50, 0) is None


def test_pick_hist_base_exact_and_fallback():
    rows = [_annual(2025, 200), _annual(2020, 100), _annual(2019, 90)]
    base = pick_hist_base(rows, '2026-11-01')
    assert base['base_fy'] == 2020 and base['years'] == 5
    assert base['latest_np'] == 200

    # 缺 2020 时用 2021（4 年）兜底
    rows2 = [_annual(2025, 200), _annual(2021, 120)]
    base2 = pick_hist_base(rows2, '2026-11-01')
    assert base2['base_fy'] == 2021 and base2['years'] == 4

    # 只有 3 年内历史 → 不接受
    rows3 = [_annual(2025, 200), _annual(2022, 150)]
    assert pick_hist_base(rows3, '2026-11-01') is None


def test_pick_hist_base_pit_and_negative():
    # 2025 年报 2026-04-30 才公告，2026-03 时最新仍是 2024 年报
    rows = [_annual(2025, 200), _annual(2024, 180),
            _annual(2020, 100), _annual(2019, 90)]
    base = pick_hist_base(rows, '2026-03-01')
    assert base['latest_fy'] == 2024 and base['base_fy'] == 2019
    assert pick_hist_base([_annual(2025, -5), _annual(2020, 100)],
                          '2026-11-01') is None


def test_g_cons_prefers_fy3_and_falls_back():
    snap = {'base_fy': 2025, 'fy1': 2026, 'fy2': 2027, 'fy3': 2028,
            'np_fy1': 220.0, 'np_fy2': 242.0, 'np_fy3': 266.2}
    g, h = compute_g_cons(snap, 200.0)
    assert h == 3 and g == pytest.approx((266.2 / 200) ** (1 / 3) - 1)

    snap2 = {'base_fy': 2025, 'fy1': 2026, 'fy2': 2027, 'fy3': 2028,
             'np_fy1': 220.0, 'np_fy2': 242.0, 'np_fy3': -1.0}
    g2, h2 = compute_g_cons(snap2, 200.0)
    assert h2 == 2 and g2 == pytest.approx((242.0 / 200) ** 0.5 - 1)

    # base_fy 与预测财年错位 → 不硬算
    assert compute_g_cons(snap, None) == (None, None)
    assert compute_g_cons({'base_fy': 2024, 'fy3': 2026, 'np_fy3': 100},
                          200.0) == (None, None)


def test_shrink_one_sided():
    assert shrink(0.30, 0.10, 0.5) == pytest.approx(0.20)
    # 一致预期低于历史 → 完全用历史，不向下压
    assert shrink(0.05, 0.10, 0.5) == pytest.approx(0.10)
    assert shrink(None, 0.10) == pytest.approx(0.10)
    assert shrink(0.20, None) == pytest.approx(0.20)
    assert shrink(None, None) is None


def test_peg_and_validity():
    assert peg(20, 0.20) == pytest.approx(1.0)
    assert peg(-1, 0.2) is None
    assert peg(20, -0.1) is None
    assert valid_peg(20, 0.2, 1.0, 3)
    assert not valid_peg(20, 0.2, 1.0, 2)      # 机构不足
    assert not valid_peg(20, 0.2, 12.0, 5)     # PEG 过高（异常）
    assert not valid_peg(20, 0.2, -1.0, 5)


def test_annual_np_for_fy_revision():
    rows = [
        {'end_date': '2025-12-31', 'ann_date': '2026-04-30', 'n_income_attr_p': 200},
        {'end_date': '2025-12-31', 'ann_date': '2026-08-30', 'n_income_attr_p': 210},
    ]
    assert annual_np_for_fy(rows, '2026-06-01', 2025) == 200
    assert annual_np_for_fy(rows, '2026-09-01', 2025) == 210
    assert annual_np_for_fy(rows, '2026-09-01', 2024) is None


def test_peg_series_pit_and_rolling_band():
    start = date(2026, 1, 1)
    pe_rows = [(start + timedelta(days=i), 20.0) for i in range(600)]
    annuals = [_annual(2020, 100), _annual(2025, 200)]
    snaps = [{'asof_date': '2026-01-01', 'base_fy': 2025,
              'fy1': 2026, 'fy2': 2027, 'fy3': 2028,
              'np_fy1': 220.0, 'np_fy2': 242.0, 'np_fy3': 266.2,
              'org_count': 5, 'flags': 0}]
    out = compute_peg_series(pe_rows, annuals, snaps, window=250, min_periods=250)
    assert len(out) == 600
    # 年报公告前：无历史锚与 base_np，PEG 为空
    assert out[0]['peg'] is None
    # 2026-04-30 之后：G_hist/G_cons 均可用
    after = next(r for r in out if r['date'] >= '2026-05-01')
    assert after['g_hist'] == pytest.approx(2 ** 0.2 - 1)
    assert after['g_cons'] == pytest.approx((266.2 / 200) ** (1 / 3) - 1)
    assert after['peg'] is not None and 0 < after['peg'] < 2
    # 滚动 250 个有效 PEG 后出现分位带
    last = out[-1]
    assert last['band20'] == pytest.approx(last['peg'])
    assert last['band80'] == pytest.approx(last['peg'])
    assert last['peg_pct'] == pytest.approx(100.0)


def test_peg_series_drops_thin_consensus_like_cross_section():
    """同一只股票：机构数 <3 的一致预期在截面是「占位口径」，历史序列也必须退回仅 G_hist。"""
    start = date(2026, 6, 1)                       # 2025 年报已于 04-30 公告
    pe_rows = [(start + timedelta(days=i), 20.0) for i in range(10)]
    annuals = [_annual(2020, 100), _annual(2025, 200)]
    thin = {'asof_date': '2026-01-01', 'base_fy': 2025,
            'fy1': 2026, 'fy2': 2027, 'fy3': 2028,
            'np_fy1': 220.0, 'np_fy2': 242.0, 'np_fy3': 266.2, 'org_count': 1}
    thick = {**thin, 'org_count': 5}
    g_cons = (266.2 / 200) ** (1 / 3) - 1
    g_hist = 2 ** 0.2 - 1

    last = compute_peg_series(pe_rows, annuals, [thin])[-1]
    assert last['g_cons'] is None and last['mode'] == 'hist'      # 历史与截面同口径
    assert last['g'] == pytest.approx(g_hist) and last['peg'] == pytest.approx(peg(20.0, g_hist))
    assert not valid_peg(20.0, g_cons, 1.0, 1)                    # 截面同样不认

    ok = compute_peg_series(pe_rows, annuals, [thick])[-1]
    assert ok['mode'] == 'consensus' and ok['g_cons'] == pytest.approx(g_cons)
    assert valid_peg(20.0, g_cons, 1.0, 5)

    assert not enough_orgs('x') and not enough_orgs(2)
    assert enough_orgs(3) and enough_orgs(None)


def test_peg_series_winsorizes_extreme_peg_for_band():
    """近零增速产生的千倍 PEG 不应把 20-80 分位带拉爆（band/pct 截断在 10）。"""
    start = date(2026, 6, 1)
    pe_rows = [(start + timedelta(days=i), 1000.0 if i % 2 else 20.0)
               for i in range(600)]
    annuals = [_annual(2020, 100), _annual(2025, 200)]
    out = compute_peg_series(pe_rows, annuals, [], window=250, min_periods=250)
    raw = [r['peg'] for r in out if r['peg'] is not None]
    assert max(raw) > 50                      # 原始 PEG 仍返回
    last = out[-1]
    assert last['band80'] <= 10.0             # 分位带按 10 倍 winsorize
    assert last['band50'] <= 10.0
    assert last['peg_pct'] == pytest.approx(100.0)
