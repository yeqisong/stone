"""一致预期每日快照（PEG 数据底座第 2 层）。

数据链路：
  stock_report_rc（券商预测明细，万元→元）
      ↓ 近 180 天、Q4 预测、按 (股票, 机构, 分析师, 财年) 取最新一份
  stock_consensus_daily（每日宽表：FY1/2/3 归母净利中位数）

纯库内计算，不消耗 tushare 配额；历史快照由 scripts/backfill_consensus.py
按交易日逐日调用同一函数生成，保证 PIT 口径与增量一致。
"""
from datetime import date, timedelta
from typing import Optional

from loguru import logger
from sqlalchemy import text

from crawler.writers import batch_upsert_consensus_daily

# flags 位定义（API/前端可据此提示，不要随意改位序）
FLAG_NEG_NP = 1       # 中位数归母净利 <= 0（增速无意义）
FLAG_PARTIAL = 2      # FY2/FY3 预测缺失，无法算 2/3 年 CAGR
FLAG_FEW_ORGS = 4     # 参与机构 < 3，一致预期代表性弱


def _as_date_str(v) -> str:
    if isinstance(v, date):
        return v.isoformat()
    return str(v)[:10]


def compute_consensus_daily(db, asof_date, calc_ver: str = 'c1',
                            lookback_days: int = 180) -> int:
    """计算并写入 asof_date 的一致预期快照，返回写入股票数。

    规则：
    1. base_fy 取 fina_indicator 中 ann_date <= asof 的最新年报财年；
    2. 只用 report_date <= asof 且近 lookback_days 天的 Q4 预测（避免旧研报长期加权）；
    3. 同一机构/分析师对同一财年只保留最新一份，防止高频更新刷权重；
    4. 中位数聚合，记录机构数、研报数与 FY1 离散度。
    """
    asof = _as_date_str(asof_date)

    base_rows = db.execute(text("""
        WITH annual AS (
            SELECT stock_code, EXTRACT(year FROM end_date)::int AS base_fy,
                   end_date, 1 AS pri
            FROM stock_income
            WHERE EXTRACT(month FROM end_date) = 12 AND ann_date <= :asof
            UNION ALL
            SELECT stock_code, EXTRACT(year FROM end_date)::int AS base_fy,
                   end_date, 2 AS pri
            FROM fina_indicator
            WHERE EXTRACT(month FROM end_date) = 12
              AND COALESCE(ann_date, end_date) <= :asof
        )
        SELECT DISTINCT ON (stock_code) stock_code, base_fy
        FROM annual
        ORDER BY stock_code, base_fy DESC, pri
    """), {"asof": asof}).fetchall()
    base_map = {r[0]: int(r[1]) for r in base_rows if r[1] is not None}
    if not base_map:
        return 0

    agg_rows = db.execute(text("""
        WITH latest AS (
            SELECT DISTINCT ON (stock_code, org_name, author_name, fiscal_year)
                   stock_code, org_name, fiscal_year, np, eps
            FROM stock_report_rc
            WHERE report_date <= :asof
              AND report_date >= (CAST(:asof AS date) - make_interval(days => :lookback))
              AND quarter LIKE '%Q4'
              AND fiscal_year IS NOT NULL
            ORDER BY stock_code, org_name, author_name, fiscal_year,
                     report_date DESC, id DESC
        )
        SELECT stock_code, fiscal_year,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY np)  AS np_med,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY eps) AS eps_med,
               stddev_samp(np)                                  AS np_std,
               COUNT(DISTINCT NULLIF(org_name, ''))             AS org_count,
               COUNT(*)                                         AS report_count
        FROM latest
        GROUP BY stock_code, fiscal_year
    """), {"asof": asof, "lookback": int(lookback_days)}).fetchall()

    # (stock_code, fiscal_year) -> 聚合行
    by_code: dict = {}
    for r in agg_rows:
        by_code.setdefault(r[0], {})[int(r[1])] = r

    records = []
    for code, base_fy in base_map.items():
        fy_rows = by_code.get(code)
        if not fy_rows:
            continue
        fys = [base_fy + 1, base_fy + 2, base_fy + 3]
        # 至少要有一个预测财年落在 FY1-3 窗口内
        if not any(fy in fy_rows for fy in fys):
            continue

        vals = {}
        for fy in fys:
            r = fy_rows.get(fy)
            vals[fy] = r

        def _g(fy, idx):
            r = vals.get(fy)
            return r[idx] if r is not None else None

        np1, np2, np3 = (_g(fys[0], 2), _g(fys[1], 2), _g(fys[2], 2))
        eps1, eps2, eps3 = (_g(fys[0], 3), _g(fys[1], 3), _g(fys[2], 3))

        # 机构数/研报数：取有预测的财年中的最大值（同一批研报通常覆盖多个财年）
        org_count = max((int(vals[fy][5]) for fy in fys if vals.get(fy)), default=0)
        report_count = max((int(vals[fy][6]) for fy in fys if vals.get(fy)), default=0)

        # FY1 离散度；FY1 缺失时退回第一个有预测的财年
        disp_row = next((vals[fy] for fy in fys if vals.get(fy)), None)
        np_dispersion = None
        if disp_row is not None and disp_row[4] is not None and disp_row[2]:
            med = abs(float(disp_row[2]))
            if med > 0:
                np_dispersion = float(disp_row[4]) / med

        flags = 0
        if np1 is not None and np1 <= 0:
            flags |= FLAG_NEG_NP
        if np1 is None or np2 is None or np3 is None:
            flags |= FLAG_PARTIAL
        if org_count < 3:
            flags |= FLAG_FEW_ORGS

        records.append({
            "stock_code": code,
            "asof_date": asof,
            "base_fy": base_fy,
            "fy1": fys[0], "fy2": fys[1], "fy3": fys[2],
            "np_fy1": np1, "np_fy2": np2, "np_fy3": np3,
            "eps_fy1": eps1, "eps_fy2": eps2, "eps_fy3": eps3,
            "org_count": org_count,
            "report_count": report_count,
            "np_dispersion": np_dispersion,
            "flags": flags,
            "calc_ver": calc_ver,
        })

    if records:
        batch_upsert_consensus_daily(db, records)
    logger.info(f"[consensus] {asof}: base={len(base_map)} 有预测={len(records)}")
    return len(records)


def consensus_dates(db, start_date, end_date) -> list:
    """交易日轴（对齐 trade_calendar），供回补脚本逐日生成快照。"""
    rows = db.execute(text("""
        SELECT cal_date FROM trade_calendar
        WHERE cal_date BETWEEN :s AND :e AND is_trade_day = true
        ORDER BY cal_date
    """), {"s": _as_date_str(start_date), "e": _as_date_str(end_date)}).fetchall()
    return [r[0] for r in rows]
