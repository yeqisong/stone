"""一致预期快照 SQL 聚合的集成测试（v3.27 审查项 15；此前 compute_consensus_daily 没有测试）。

在 stone_test 里写入合成研报/利润表行，验证 crawler.consensus.compute_consensus_daily
的口径：机构去重取最新一份、180 天回看、中位数聚合、机构数与 flags。
不出网、不消耗 tushare 配额；用例自清理。
"""
import pytest
from sqlalchemy import text

from app.db.connection import get_sync_db
from crawler.consensus import (FLAG_FEW_ORGS, FLAG_NEG_NP, FLAG_PARTIAL,
                               compute_consensus_daily)

ASOF = '2026-11-20'
CODES = ['990001', '990002', '990003', '990004', '990005']


def _annual(code, fy=2025, np_=1000.0):
    return {'stock_code': code, 'end_date': f'{fy}-12-31', 'ann_date': f'{fy + 1}-04-30',
            'report_type': '1', 'n_income_attr_p': np_}


def _rc(code, org, fy, np_, day='2026-11-03', quarter=None):
    """一条券商预测：同一 (org, 分析师, 财年) 只保留 report_date 最新的一份。"""
    return {'stock_code': code, 'report_date': day, 'org_name': org,
            'author_name': f'{org}分析师', 'quarter': quarter or f'{fy}Q4',
            'fiscal_year': None if quarter and 'Q4' not in quarter else fy, 'np': np_}


def _cleanup(db):
    for t in ('stock_consensus_daily', 'stock_report_rc', 'stock_income'):
        db.execute(text(f"DELETE FROM {t} WHERE stock_code = ANY(:c)"), {'c': CODES})
    db.commit()


def _rows(db):
    return {r[0]: r for r in db.execute(text("""
        SELECT stock_code, base_fy, fy1, fy2, fy3, np_fy1, np_fy2, np_fy3,
               org_count, report_count, np_dispersion, flags
        FROM stock_consensus_daily WHERE asof_date=:d AND stock_code = ANY(:c)
    """), {'d': ASOF, 'c': CODES}).fetchall()}


@pytest.mark.asyncio
async def test_compute_consensus_daily_sql_semantics():
    db = get_sync_db()
    try:
        _cleanup(db)
        annuals = [_annual(c) for c in CODES]
        reports = [
            # 990001：A 的旧报告（10-15）应被同 (org, 分析师, 财年) 的新报告（11-01）取代
            _rc('990001', 'A', 2026, 999.0, day='2026-10-15'),
            _rc('990001', 'A', 2026, 120.0, day='2026-11-01'),
            _rc('990001', 'B', 2026, 130.0, day='2026-11-02'),
            _rc('990001', 'C', 2026, 140.0, day='2026-11-03'),     # → 中位 130，3 家
            # 990002：只有 2 家 → FLAG_FEW_ORGS
            _rc('990002', 'A', 2026, 120.0), _rc('990002', 'B', 2026, 130.0),
            # 990003：同机构先后两份（120 → 125），只算最新 → 中位 130
            _rc('990003', 'A', 2026, 120.0, day='2026-11-01'),
            _rc('990003', 'A', 2026, 125.0, day='2026-11-05'),
            _rc('990003', 'B', 2026, 130.0), _rc('990003', 'C', 2026, 140.0),
            # 990004：C 的报告在 180 天回看窗口之外（2025-01-05）→ 不计，剩 2 家
            _rc('990004', 'A', 2026, 120.0), _rc('990004', 'B', 2026, 130.0),
            _rc('990004', 'C', 2026, 140.0, day='2025-01-05'),
            # 990005：只有非 Q4 预测 → 落不进 FY1-3 窗口 → 不生成快照
            _rc('990005', 'A', 2026, 500.0, quarter='2026Q3'),
        ]
        db.execute(text("""
            INSERT INTO stock_income (stock_code, end_date, ann_date, report_type, n_income_attr_p)
            VALUES (:stock_code, :end_date, :ann_date, :report_type, :n_income_attr_p)
        """), annuals)
        db.execute(text("""
            INSERT INTO stock_report_rc (stock_code, report_date, org_name, author_name,
                                         quarter, fiscal_year, np)
            VALUES (:stock_code, :report_date, :org_name, :author_name, :quarter, :fiscal_year, :np)
        """), reports)
        db.commit()

        n = compute_consensus_daily(db, ASOF)
        assert n == 4                                   # 990005 没有快照
        rows = _rows(db)
        assert set(rows) == {'990001', '990002', '990003', '990004'}

        r = rows['990001']
        assert r[1:4] == (2025, 2026, 2027) and r[6] is None and r[7] is None   # base_fy / fy1..3
        assert float(r[5]) == pytest.approx(130.0)     # np_fy1 中位数（旧报告 999 不参与）
        assert r[8] == 3 and r[9] == 3                 # org_count / report_count
        assert float(r[10]) == pytest.approx(10.0 / 130.0, rel=1e-6)   # 样本标准差 10 / 中位 130
        assert r[11] == FLAG_PARTIAL                    # 只有 FY1 → 不完整；机构够、净利为正

        r = rows['990002']
        assert r[8] == 2 and r[11] == FLAG_PARTIAL | FLAG_FEW_ORGS

        r = rows['990003']
        assert float(r[5]) == pytest.approx(130.0) and r[8] == 3 and r[9] == 3   # 120 被 replaced

        r = rows['990004']
        assert r[8] == 2 and r[11] == FLAG_PARTIAL | FLAG_FEW_ORGS   # 窗口外的报告不计

        # 幂等：重算覆盖同一批行，不翻倍
        assert compute_consensus_daily(db, ASOF) == 4
        assert db.execute(text(
            "SELECT count(*) FROM stock_consensus_daily WHERE asof_date=:d AND stock_code = ANY(:c)"
        ), {'d': ASOF, 'c': CODES}).scalar() == 4
    finally:
        _cleanup(db)
        db.close()


@pytest.mark.asyncio
async def test_compute_consensus_daily_negative_np_flag():
    """FY1 中位数 ≤ 0 → FLAG_NEG_NP（增速无意义，下游不算 G_cons）。"""
    db = get_sync_db()
    try:
        _cleanup(db)
        db.execute(text("""
            INSERT INTO stock_income (stock_code, end_date, ann_date, report_type, n_income_attr_p)
            VALUES ('990001', '2025-12-31', '2026-04-30', '1', 1000.0)
        """))
        db.execute(text("""
            INSERT INTO stock_report_rc (stock_code, report_date, org_name, author_name, quarter, fiscal_year, np)
            VALUES ('990001', '2026-11-03', 'A', 'A分析师', '2026Q4', 2026, -50.0),
                   ('990001', '2026-11-03', 'B', 'B分析师', '2026Q4', 2026, -80.0),
                   ('990001', '2026-11-03', 'C', 'C分析师', '2026Q4', 2026, -20.0)
        """))
        db.commit()
        assert compute_consensus_daily(db, ASOF) == 1
        row = _rows(db)['990001']
        assert float(row[5]) == pytest.approx(-50.0)
        assert row[11] == FLAG_PARTIAL | FLAG_NEG_NP       # 3 家机构 → 不标 FEW_ORGS
    finally:
        _cleanup(db)
        db.close()
