#!/usr/bin/env python3
"""
宕机补采脚本。

检测 daily_quote 中的缺失交易日，逐个调用 BaostockCrawler.download_daily_update()
补采缺失数据。

逻辑:
  1. 查询 daily_quote 中 MAX(trade_date)
  2. 从该日期+1 遍历到最近交易日
  3. 用交易日历判断哪些是缺失的交易日
  4. 对每个缺失日调用 download_daily_update()
  5. 自动重试单个日期（最多3次，间隔递增）
  6. 上限 max_days 天，超出告警

用法:
  python -m crawler.catch_up           # 默认补采最多10天
  python -m crawler.catch_up --max-days 30 --force  # 补采30天
"""
import argparse
import sys
import time
from datetime import date, timedelta
from sqlalchemy import text
from loguru import logger

sys.path.insert(0, '.')
from app.db.connection import get_sync_db
from crawler.baostock_crawler import BaostockCrawler
from crawler.trade_calendar import get_last_trade_date, is_trade_day


def catch_up(max_days: int = 10, force: bool = False) -> dict:
    """
    补采缺失数据。

    Args:
        max_days: 最多补采天数，超出告警并仅补采最近 N 天
        force: 强制补采所有缺失日期（忽略上限）

    Returns:
        {missing_count, caught_up, failed_dates, skipped_dates}
    """
    db = get_sync_db()

    try:
        today = date.today()
        last_trade = get_last_trade_date(db)

        # ── 1. 查询数据库中最新的行情日期 ──
        result = db.execute(text("SELECT MAX(trade_date) FROM daily_quote"))
        latest_in_db = result.scalar()

        if latest_in_db is None:
            logger.error("数据库无行情数据，请先执行历史数据初始化")
            return {"missing_count": 0, "caught_up": 0,
                    "failed_dates": [], "skipped_dates": [],
                    "error": "empty_db"}

        if isinstance(latest_in_db, str):
            latest_in_db = date.fromisoformat(latest_in_db)
        elif not isinstance(latest_in_db, date):
            latest_in_db = date.fromisoformat(str(latest_in_db))

        # ── 2. 收集缺失的交易日 ──
        missing = []
        check_date = latest_in_db + timedelta(days=1)
        while check_date <= last_trade:
            if is_trade_day(check_date, db):
                missing.append(check_date)
            check_date += timedelta(days=1)

        original_count = len(missing)

        if not missing:
            logger.info("无需补采，数据已是最新。")
            return {"missing_count": 0, "caught_up": 0,
                    "failed_dates": [], "skipped_dates": []}

        # ── 3. 上限检查 ──
        skipped_dates = []
        if not force and len(missing) > max_days:
            logger.warning(f"缺失 {len(missing)} 个交易日，超过上限 {max_days} 天。"
                           f"将仅补采最近 {max_days} 天。")
            skipped_dates = [d.isoformat() for d in missing[:-max_days]]
            missing = missing[-max_days:]

        logger.info(f"开始补采 {len(missing)} 个交易日 (原始缺失 {original_count})...")
        for d in missing:
            logger.info(f"  缺失: {d.isoformat()}")

        # ── 4. 逐个日期补采（复用同一个 BaostockCrawler 会话）──
        caught_up = 0
        failed_dates = []

        crawler = BaostockCrawler()
        try:
            for trade_date in missing:
                logger.info(f"--- 补采 {trade_date} ---")

                # 每个日期最多重试 3 次
                success = False
                for attempt in range(3):
                    try:
                        result = crawler.download_daily_update(trade_date, db=db)
                        if result.get("fatal"):
                            logger.error(f"  {trade_date} 补采致命错误: {result['fatal']}")
                            time.sleep(30)
                            continue

                        rows = result.get("rows", 0)
                        errors = result.get("errors", 0)
                        logger.info(f"  {trade_date} 完成: +{rows}行, {errors}错误 "
                                    f"(attempt {attempt+1})")

                        if errors == 0 or rows > 0:
                            success = True
                            break
                        else:
                            logger.warning(f"  {trade_date} 全部失败，等待后重试...")
                            time.sleep(60)

                    except Exception as e:
                        logger.error(f"  {trade_date} 补采异常 (attempt {attempt+1}): {e}")
                        time.sleep(60)

                if success:
                    caught_up += 1
                else:
                    failed_dates.append(trade_date.isoformat())
                    logger.error(f"  {trade_date} 补采彻底失败！")

                # 日期之间短暂休息
                if len(missing) > 1:
                    time.sleep(5)
        finally:
            crawler.logout()

        # ── 5. 结果汇总 ──
        logger.info(f"补采完成: 成功 {caught_up}/{len(missing)} 天, "
                     f"失败 {len(failed_dates)}, 跳过 {len(skipped_dates)}")

        if failed_dates:
            logger.error(f"失败日期: {failed_dates}")
            logger.error(f"补数失败超过阈值 ({failed_count}/{total_attempts})，需人工介入")

        return {
            "missing_count": original_count,
            "caught_up": caught_up,
            "failed_dates": failed_dates,
            "skipped_dates": skipped_dates,
        }

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="宕机补采脚本")
    parser.add_argument("--max-days", type=int, default=10,
                        help="补采上限天数 (默认10)")
    parser.add_argument("--force", action="store_true",
                        help="强制补采所有缺失日期（忽略上限）")
    args = parser.parse_args()

    result = catch_up(args.max_days, args.force)
    sys.exit(1 if result.get("failed_dates") else 0)
