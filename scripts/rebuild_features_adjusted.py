#!/usr/bin/env python
"""按后复权口径重建特征（一次性迁移；默认只预览，不写库）。

背景：历史特征引擎用的是 daily_quote 原始 OHLCV，送转/分红日会产生假跳变；
本脚本用修复后的引擎（adjust_prices=True）重算受影响特征。执行前请：
1. 停止后端/DAG，避免 feature_compute 并发删除同一特征；
2. 备份数据库或至少确认 feature_values 可重建；
3. 执行 `--apply`，完成后设置 FEATURE_PRICE_ADJUST=true 并重训模型。

用法：
    venv/bin/python scripts/rebuild_features_adjusted.py --entity stock --start 2017-01-01
    venv/bin/python scripts/rebuild_features_adjusted.py --entity stock --start 2017-01-01 --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.db.connection import get_sync_db  # noqa: E402
from scripts.feature_compute import compute_all_features  # noqa: E402

_PRICE_RE = re.compile(r"\b(open|high|low|close|volume)\b", re.I)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entity", default="stock", choices=["stock", "etf", "index"])
    ap.add_argument("--start", default="2017-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--features", default="", help="逗号分隔；空=全部价格/量相关特征")
    ap.add_argument("--apply", action="store_true", help="真正写库；默认只打印计划")
    args = ap.parse_args()

    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT feature_name, formula FROM features
            WHERE target_entity=:e AND status='enabled'
            ORDER BY feature_name
        """), {"e": args.entity}).fetchall()
        if args.features:
            wanted = {x.strip() for x in args.features.split(",") if x.strip()}
            rows = [r for r in rows if r[0] in wanted]
        else:
            rows = [r for r in rows if _PRICE_RE.search(r[1] or "")]
        names = [r[0] for r in rows]
        print(f"entity={args.entity} range={args.start}..{args.end or 'today'} "
              f"features={len(names)} apply={args.apply}")
        for n in names:
            print("  -", n)
        if not args.apply:
            print("\n预览模式：未写库。确认后加 --apply。")
            return
        if not names:
            print("没有需要重算的特征")
            return

        def progress(done, total, fn, rows_so_far):
            print(f"[{done}/{total}] {fn}: cumulative rows={rows_so_far}", flush=True)

        r = compute_all_features(
            db, args.entity, args.start, args.end, names,
            progress_cb=progress, adjust_prices=True,
        )
        print("DONE", r)
    finally:
        db.close()


if __name__ == "__main__":
    main()
