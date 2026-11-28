#!/usr/bin/env python
"""涨停梯队历史回补（东财 push2ex 历史日期 → zt_pool_daily）。

实时大盘页的涨停梯队是盘中内存态，日终由 app/market_realtime.archive_zt_daily
在交易日 15:05 后落一档；本脚本补**上线之前**的历史，把连板高度/炸板率
历史曲线一次铺满。

数据源：东财 push2ex getTopicZTPool/getTopicZBPool 带历史 date 参数（2026-11-27
实测可用，样本见 tests/fixtures/em_ztpool_hist.json），经 em_get 全局串行闸门
（1.2s+抖动，2 请求/交易日，250 日 ≈ 11 分钟）。注意东财历史池的最早可回补深度
没有官方承诺，过旧日期可能空池——空池按当日无涨停合法入库（limit_up=0），
但与交易日历核对后异常空的日子会跳过并告警，防把接口故障写成历史。

用法：
    venv/bin/python scripts/backfill_zt_pool.py --start 2026-01-01
    venv/bin/python scripts/backfill_zt_pool.py --start 2026-11-01 --end 2026-11-20

幂等 upsert（ON CONFLICT 重写），可重复执行；不消耗 tushare 配额。
"""
import argparse
import json
import sys
from datetime import date, datetime as _dtm, time as _time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger
from sqlalchemy import text

from crawler import em_quote
from app.db.connection import get_sync_db
from app.market_realtime import zt_summary

_CST = timezone(timedelta(hours=8))

UPSERT = text("""
INSERT INTO zt_pool_daily
    (trade_date, limit_up, limit_down, broken, broken_rate, max_lbc, pool, broken_pool)
VALUES (:d, :lu, NULL, :brk, :rate, :maxlbc, CAST(:pool AS jsonb), CAST(:bp AS jsonb))
ON CONFLICT (trade_date) DO UPDATE SET
    limit_up=EXCLUDED.limit_up, broken=EXCLUDED.broken, broken_rate=EXCLUDED.broken_rate,
    max_lbc=EXCLUDED.max_lbc, pool=EXCLUDED.pool, broken_pool=EXCLUDED.broken_pool,
    created_at=CURRENT_TIMESTAMP
""")


def main():
    ap = argparse.ArgumentParser(description="涨停梯队历史回补（东财 push2ex）")
    ap.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD")
    ap.add_argument("--end", default="", help="结束日期（缺省=今天）")
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    # 「今天」按北京时间算（主机时区不保证）；东财对尚未有数据的日期会静默回退成
    # 最近一个交易日的池，所以当天收盘前不能回补当天
    now = _dtm.now(_CST)
    end = date.fromisoformat(args.end) if args.end else now.date()
    db = get_sync_db()
    ok = skip = 0
    try:
        day = start
        while day <= end:
            if day == now.date() and now.time() < _time(15, 5):
                logger.warning(f"[backfill-zt] {day} 尚未收盘，涨停池不是终值，跳过当天")
                break
            # 只处理交易日（trade_calendar 优先，缺失回退工作日）
            row = db.execute(text(
                "SELECT is_trade_day FROM trade_calendar WHERE cal_date=:d LIMIT 1"
            ), {"d": day}).fetchone()
            is_td = bool(row[0]) if row is not None else day.weekday() < 5
            if not is_td:
                day += timedelta(days=1)
                continue

            ymd = day.strftime("%Y%m%d")
            try:
                zt = em_quote.fetch_zt_pools(ymd)
            except Exception as e:
                logger.warning(f"[backfill-zt] {day} 拉取失败跳过: {str(e)[:140]}")
                day += timedelta(days=1)
                continue
            pool, broken = zt.get("pool") or [], zt.get("broken_pool") or []
            if not pool and not broken:
                # 正常交易日全空池 → 东财该日期无数据（超出可回补深度），不写
                logger.warning(f"[backfill-zt] {day} 空池（接口无该日数据），跳过")
                skip += 1
                day += timedelta(days=1)
                continue

            s = zt_summary(zt)
            db.execute(UPSERT, {
                "d": day, "lu": s["limit_up"], "brk": s["broken"],
                "rate": s["broken_rate"], "maxlbc": s["max_lbc"],
                "pool": json.dumps(zt.get("pool") or [], ensure_ascii=False),
                "bp": json.dumps(zt.get("broken_pool") or [], ensure_ascii=False),
            })
            db.commit()
            ok += 1
            if ok % 20 == 0:
                logger.info(f"[backfill-zt] 进度 {day}（成功 {ok} 跳过 {skip}）")
            day += timedelta(days=1)
    finally:
        db.close()
    logger.info(f"[backfill-zt] 完成：成功 {ok} 天，空池跳过 {skip} 天")


if __name__ == "__main__":
    main()
