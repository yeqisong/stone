#!/usr/bin/env python3
"""
数据导入脚本。将 history_download.py 导出的 CSV 文件导入到数据库中。
部署时执行一次即可。

用法:
  python crawler/import_data.py --dir data/exports/csv_2026-06-06
  python crawler/import_data.py --dir data/exports/csv_2026-06-06 --db postgresql://...
"""
import argparse
import pandas as pd
from pathlib import Path
from sqlalchemy import text

from app.db.connection import get_sync_db
from app.db.schema import init_db

IMPORT_ORDER = [
    "trade_calendar",
    "stock_master",
    "daily_quote",
    "corporate_actions",
    "strategy_config",
]


def import_csv_to_db(csv_dir: str):
    """将 CSV 文件导入数据库。"""
    db = get_sync_db()

    # 先建表
    init_db(db)

    for table in IMPORT_ORDER:
        csv_path = Path(csv_dir) / f"{table}.csv"
        if not csv_path.exists():
            print(f"  跳过 {table}.csv (文件不存在)")
            continue

        print(f"  导入 {table}.csv ...")
        df = pd.read_csv(csv_path)

        if df.empty:
            print(f"    空文件，跳过")
            continue

        # 批量插入
        columns = list(df.columns)
        placeholders = ",".join([f":{c}" for c in columns])
        col_names = ",".join(columns)

        count = 0
        for _, row in df.iterrows():
            try:
                values = {c: row[c] if pd.notna(row[c]) else None for c in columns}
                db.execute(text(
                    f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
                ), values)
                count += 1
            except Exception as e:
                if count == 0:  # 只打印第一次错误
                    print(f"    警告: {e}")
                continue

            if count % 10000 == 0:
                db.commit()
                print(f"      {count} ...")

        db.commit()
        print(f"    {table}: {count}/{len(df)} 行")

    db.close()
    print("导入完成")


def main():
    parser = argparse.ArgumentParser(description="数据导入")
    parser.add_argument("--dir", required=True, help="CSV 文件目录")
    args = parser.parse_args()

    csv_dir = Path(args.dir)
    if not csv_dir.exists():
        print(f"错误: 目录不存在 {csv_dir}")
        return

    print(f"从 {csv_dir} 导入数据...")
    import_csv_to_db(str(csv_dir))


if __name__ == "__main__":
    main()
