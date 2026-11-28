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


def test_peg_routes_registered_under_existing_nodes():
    """PEG 两个端点的权限登记（v3.27 审查项 14：原先只靠前缀规则兜底、权限表里没有记录）。

    归属既有节点、不新增权限码：指数成分 PEG 截面归大盘节点，个股 PEG 历史带归个股
    研究。双角色：5 个默认角色都有这两个节点（有权通过）；没有这两个节点的自定义
    角色/支持会话则既看不到页面、调接口也是 403（由同一条规则保证）。
    """
    from app.auth.definitions import ROLE_PERMISSIONS, permission_by_code
    from app.auth.route_permissions import resolve_rule

    assert resolve_rule('GET', '/api/market/peg').permission == 'research.market.view'
    assert resolve_rule('GET', '/api/stock/600519/peg_history').permission == 'research.stock.view'
    for role in ('admin', 'operator', 'trader', 'analyst', 'viewer'):
        grants = {code for code, _scope, _support in ROLE_PERMISSIONS[role]}
        assert {'research.market.view', 'research.stock.view'} <= grants, role
    # 登记与实现一致：节点的 api 描述里必须写得下这两个端点
    assert '/market/peg' in permission_by_code('research.market.view')['api']
    assert '/peg_history' in permission_by_code('research.stock.view')['api']


def test_cross_section_membership_is_point_in_time():
    """as_of（PIT 截止日）必须约束成分股快照：否则历史截面混进未来的调入调出。

    PE / 一致预期 / 利润表都按 `<= asof` 取，成分股却取最新一期——as_of 就名不副实。
    这里只看查询本身（不连库）：成分股快照查询必须带 asof 上界。
    """
    import app.api.peg as api
    from datetime import datetime as _dt

    class Recorder:
        def __init__(self): self.sql = []
        def execute(self, stmt, params=None):
            self.sql.append((str(stmt), params))
            class _R:
                def scalar(self): return None
                def fetchall(self): return []
            return _R()
        def close(self): pass

    rec = Recorder()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(api, 'get_sync_db', lambda: rec)
        asof = _dt(2026, 9, 15).date()
        out = api._build_cross_section('000300.SH', asof, 0.5)
    first_sql, first_params = rec.sql[0]
    assert 'FROM index_weight' in first_sql
    assert 'trade_date <= :asof' in first_sql                      # 成分股按 asof 取
    assert first_params == {'idx': '000300.SH', 'asof': asof}
    assert out['items'] == [] and '快照' in out['note']            # asof 前没有快照 → 明说
    assert 'with_peg_full' in out['coverage']                      # 覆盖信息同形状


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
