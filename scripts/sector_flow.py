"""行业资金流日频回补/刷新 CLI。

用法：
    venv/bin/python -m scripts.sector_flow --days 120
    venv/bin/python -m scripts.sector_flow --start 2026-06-01 --end 2026-11-16

数据源：daily_quote（日线，amount 元）+ stock_moneyflow（主力净流入，万元）
       + stock_treemap_cache（行业归属，最近快照套用）
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

from sqlalchemy import text

from app.db.connection import get_sync_db
from app.sector_flow import _latest_complete_flow_day, build_range


def main():
    ap = argparse.ArgumentParser(description="行业资金流日频回补")
    ap.add_argument("--days", type=int, default=120, help="最近 N 个交易日（默认 120）")
    ap.add_argument("--start", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--end", help="结束日期 YYYY-MM-DD")
    args = ap.parse_args()

    db = get_sync_db()
    try:
        if args.start and args.end:
            start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
        else:
            end = date.fromisoformat(args.end) if args.end else _latest_complete_flow_day(db)
            if not end:
                print("找不到可用的资金流日期")
                return
            dates = [r[0] for r in db.execute(text("""
                SELECT cal_date FROM trade_calendar
                WHERE is_trade_day=true AND cal_date <= :e
                ORDER BY cal_date DESC LIMIT :n
            """), {"e": end, "n": max(1, args.days)}).fetchall()]
            if not dates:
                print("交易日历为空")
                return
            start = min(dates)
        print(f"回补 {start} ~ {end}")
        n = build_range(db, start, end)
        print(f"完成：{n} 行")
    finally:
        db.close()


if __name__ == "__main__":
    main()
