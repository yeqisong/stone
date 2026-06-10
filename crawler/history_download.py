#!/usr/bin/env python3
"""
历史数据预下载 (baostock)。
在部署前运行，一次性下载全量数据，导出 CSV 供服务器导入。
baostock 约 25 只/分钟，全量 5500 只约 3.5 小时。
支持断点续传：中断后重新运行自动跳过已下载的股票。

用法:
  python crawler/history_download.py                        # 下载全量(5年K线+基本面)
  python crawler/history_download.py --days 365             # 仅最近1年
  python crawler/history_download.py --fundamentals-only    # 仅下载基本面
  python crawler/history_download.py --no-skip              # 不跳过已有数据(重新下载)
  python crawler/history_download.py --no-fundamentals      # 仅K线，不下载基本面
  python crawler/history_download.py --export-only          # 仅导出已有CSV
"""
import argparse
from datetime import date, timedelta
from pathlib import Path

from crawler.orchestrator import download_full_history, download_fundamentals_only
from crawler.import_data import import_csv_to_db
from app.db.connection import get_sync_db
from sqlalchemy import text
import pandas as pd

EXPORT_DIR = Path("data/exports")


def export_csv_files(output_dir: str = None):
    """导出数据库各表为CSV。"""
    if output_dir is None:
        output_dir = str(EXPORT_DIR / f"csv_{date.today().isoformat()}")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    db = get_sync_db()
    tables = ["trade_calendar", "stock_master", "daily_quote",
              "strategy_config", "signal_history"]

    for table in tables:
        try:
            result = db.execute(text(f"SELECT * FROM {table}"))
            rows = result.fetchall()
            if not rows:
                print(f"  {table}: 空，跳过")
                continue
            df = pd.DataFrame(rows, columns=result.keys())
            path = out / f"{table}.csv"
            df.to_csv(path, index=False)
            print(f"  {path.name}: {len(df):,} 行")
        except Exception as e:
            print(f"  {table}: 跳过 ({e})")

    db.close()
    print(f"\n导出完成: {out}")
    return out


def main():
    parser = argparse.ArgumentParser(
        description="历史数据预下载 (baostock) — 支持断点续传")
    parser.add_argument("--days", type=int, default=0,
                        help="下载最近N天 (0=5年全量)")
    parser.add_argument("--export-only", action="store_true",
                        help="仅导出CSV")
    parser.add_argument("--fundamentals-only", action="store_true",
                        help="仅下载基本面数据(PE/PB/ROE/增长率)")
    parser.add_argument("--no-skip", action="store_true",
                        help="不跳过已有数据，强制重新下载")
    parser.add_argument("--no-fundamentals", action="store_true",
                        help="仅下载日K线，跳过基本面")
    parser.add_argument("--no-skip-pe", action="store_true",
                        help="重新下载所有 PE/PB（默认跳过已有 PE 数据以加速）")
    args = parser.parse_args()

    if args.export_only:
        print("仅导出模式")
        export_csv_files()
        return

    skip_existing = not args.no_skip

    # ── 仅基本面模式 ──
    if args.fundamentals_only:
        print("===== 仅基本面下载 =====")
        skip_pe_pb = not args.no_skip_pe
        print(f"跳过PE/PB: {'是' if skip_pe_pb else '否'}（加速ROE/增长率下载）")
        count = download_fundamentals_only(skip_existing=skip_existing,
                                           skip_pe_pb=skip_pe_pb)
        print(f"基本面完成: {count} 只")
        return

    # ── 全量模式 (K线 + 可选基本面) ──
    end = date.today()
    start = end - timedelta(days=args.days if args.days > 0 else 365 * 5)

    print(f"baostock 全量下载: {start} ~ {end}")
    print(f"断点续传: {'开启' if skip_existing else '关闭'}")
    print(f"基本面: {'开启' if not args.no_fundamentals else '关闭'}")
    result = download_full_history(
        start.isoformat(), end.isoformat(),
        skip_existing=skip_existing,
        include_fundamentals=not args.no_fundamentals,
    )
    print(f"完成: {result}")

    # 导出CSV
    print("\n导出CSV...")
    export_csv_files()


if __name__ == "__main__":
    main()
