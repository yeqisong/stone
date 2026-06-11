#!/usr/bin/env python3
"""
每日数据采集与策略计算（生产级入口）。

baostock 在 17:30 完成日K线入库，cron 在 17:35 触发。
采集成功 → 自动触发策略计算 → 输出信号。

设计:
  1. 交易日检查
  2. 补采缺失交易日 (catch_up)
  3. 采集当日数据 (download_daily_update, 带重试)
  4. 策略计算 (StrategyEngine)
  5. 写入 system_metrics 供监控
"""
import sys
import json
import time
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from sqlalchemy import text
from loguru import logger

sys.path.insert(0, '.')
from app.db.connection import get_sync_db
from crawler.baostock_crawler import BaostockCrawler
from crawler.catch_up import catch_up
from crawler.data_loader import StrategyDataLoader
from strategy.engine import StrategyEngine


def main():
    today = date.today()
    start_time = datetime.now()
    logger.info(f"=== 每日数据采集与策略计算 {today} ===")

    # ── 0. 交易日历自动续期（检查未来 2 年是否有数据，若无则从 baostock 同步） ──
    logger.info("[0/5] 交易日历检查...")
    db = get_sync_db()
    try:
        from crawler.trade_calendar import ensure_calendar_updated
        if ensure_calendar_updated(db):
            logger.info("交易日历已自动扩展")
    finally:
        db.close()

    # ── 1. 交易日检查 ──
    logger.info("[1/5] 交易日检查...")
    db = get_sync_db()
    try:
        from crawler.trade_calendar import is_trade_day
        if not is_trade_day(today, db):
            logger.info(f"{today} 非交易日，跳过")
            return
    finally:
        db.close()

    # ── 2. 补采缺失 ──
    logger.info("[2/5] 补采缺失交易日...")
    catch_up_result = catch_up(max_days=10)
    logger.info(f"  补采: 缺失{catch_up_result['missing_count']}天, "
                f"成功{catch_up_result['caught_up']}天, "
                f"失败{len(catch_up_result.get('failed_dates', []))}天")

    # ── 3. 采集当日数据 ──
    logger.info("[3/5] 采集当日数据 (baostock)...")
    dl_status = "ok"
    dl_detail = ""
    dl_rows = 0
    dl_errors = 0
    dl_index = 0
    dl_etf = 0

    crawler = BaostockCrawler()
    try:
        result = crawler.download_daily_update(today)
        dl_rows = result.get("rows", 0)
        dl_errors = result.get("errors", 0)

        if result.get("fatal"):
            dl_status = "error"
            dl_detail = f"FATAL: {result['fatal']}"
            logger.error(f"数据采集致命错误: {result['fatal']}")
        elif dl_errors > 0:
            dl_status = "warn"
            dl_detail = (f"+{dl_rows}行, {result['stocks']}只, "
                         f"{dl_errors}错误, {result['skipped']}跳过, "
                         f"耗时{result['elapsed_seconds']:.0f}秒")
            logger.warning(f"数据采集部分成功: {dl_detail}")
        else:
            dl_detail = (f"+{dl_rows}行, {result['stocks']}只, "
                         f"{result['skipped']}跳过, "
                         f"耗时{result['elapsed_seconds']:.0f}秒")
            logger.info(f"数据采集完成: {dl_detail}")

    except Exception as e:
        dl_status = "error"
        dl_detail = str(e)[:200]
        logger.error(f"数据采集异常: {e}")
    finally:
        crawler.logout()

    # ── 3b. 采集指数数据 ──
    if dl_status != "error":
        logger.info("[3b/5] 采集指数数据...")
        idx_crawler = BaostockCrawler()
        try:
            today_str = today.isoformat()
            dl_index = idx_crawler.download_all_index_daily(today_str)
            logger.info(f"  指数: {dl_index} 条")
        except Exception as e:
            logger.warning(f"指数下载失败: {e}")
        finally:
            idx_crawler.logout()

    # ── 3c. 采集 ETF 数据 ──
    if dl_status != "error":
        logger.info("[3c/5] 采集 ETF 数据...")
        etf_crawler = BaostockCrawler()
        try:
            today_str = today.isoformat()
            etf_result = etf_crawler.download_etf_daily(today_str)
            dl_etf = etf_result.get("rows", 0)
            logger.info(f"  ETF: {dl_etf} 条")
        except Exception as e:
            logger.warning(f"ETF下载失败: {e}")
        finally:
            etf_crawler.logout()

    # 写入 system_metrics
    db = get_sync_db()
    try:
        db.execute(text(
            "INSERT INTO system_metrics (metric_name, metric_value, status, detail) "
            "VALUES ('daily_download', :v, :s, :d)"
        ), {"v": dl_rows, "s": dl_status, "d": dl_detail[:500]})
        db.commit()
    finally:
        db.close()

    if dl_status == "error":
        logger.error("采集失败，跳过策略计算")
        return

    # ── 触发 DAG 流水线（daily_update→kline/index/etf/fund→treemap/strategy→stats）──
    try:
        from scripts.pipeline import dag
        dag.run("daily_update", trade_date=str(today))
    except Exception as e:
        logger.warning(f"DAG 流水线触发失败: {e}")
    return

    # ── 4. 策略计算（以下保留旧代码作为 fallback，DAG 接管后删除）──
    logger.info("[4/5] 加载策略配置...")
    db = get_sync_db()
    try:
        result = db.execute(text(
            "SELECT strategy_name, enabled, params FROM strategy_config"
        ))
        configs = {}
        for r in result.fetchall():
            params = r.params
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except (json.JSONDecodeError, TypeError):
                    params = {}
            configs[r.strategy_name] = {"enabled": r.enabled, "params": params}
        pref_mode = configs.get("global_preference", {}).get("params", {}).get("mode", "balanced")
        logger.info(f"  偏好: {pref_mode}")
    finally:
        db.close()

    logger.info(f"[5/5] 策略计算...")
    loader = StrategyDataLoader()
    engine = StrategyEngine()
    engine.set_preference(pref_mode)

    try:
        # ── 5a. 批量加载全市场数据（一次大查询替代 5000 次小查询）──
        signal_start = time.time()
        logger.info(f"  批量加载全市场行情数据...")
        all_data = loader.load_all_stocks_data()
        logger.info(f"  已加载 {len(all_data)} 只股票数据")

        # ── 5b. 并行策略计算 ──
        logger.info(f"  并行策略计算 (max_workers={min(8, os.cpu_count() or 4)})...")
        max_workers = min(8, os.cpu_count() or 4, len(all_data) or 1)

        def compute_one(code: str) -> list:
            """单只股票策略计算（纯 pandas，无需 DB 连接）。"""
            df = all_data[code]
            name = str(df.iloc[-1].get("stock_name", code))
            return engine.run_one_stock(df, code, name, today.isoformat(), configs)

        all_signals = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(compute_one, code): code for code in all_data}
            for future in as_completed(futures):
                try:
                    all_signals.extend(future.result())
                except Exception as e:
                    logger.error(f"  股票 {futures[future]} 策略计算异常: {e}")

        logger.info(f"  策略计算完成，共 {len(all_signals)} 信号")

        # ── 5c. 统一写入 DB ──
        db = get_sync_db()
        total = 0
        try:
            insert_sql = text("""
                INSERT INTO signal_history
                (signal_date, stock_code, stock_name, direction, strength,
                 strategy_name, reason, price, suggested_action, preference,
                 combined_signal, source_strategies, params_snapshot)
                VALUES (:sd,:sc,:sn,:d,:st,:sn2,:r,:p,:sa,:pr,:cs,:ss,:ps)
            """)
            for s in all_signals:
                src = json.dumps(s.source_strategies) if s.source_strategies else None
                ps = getattr(s, 'params_snapshot', '') or ''
                db.execute(insert_sql, {
                    "sd": s.signal_date, "sc": s.stock_code, "sn": s.stock_name,
                    "d": s.direction, "st": s.strength, "sn2": s.strategy_name,
                    "r": s.reason, "p": s.price, "sa": s.suggested_action,
                    "pr": s.preference, "cs": s.combined_signal, "ss": src,
                    "ps": ps,
                })
                total += 1
                if total % 500 == 0:
                    db.commit()
            db.commit()
        finally:
            db.close()

        # ── 5d. 统计与指标写入 ──
        db = get_sync_db()
        try:
            r = db.execute(text(
                "SELECT COUNT(*) FROM signal_history "
                "WHERE signal_date=:d AND combined_signal=true AND direction='buy'"
            ), {"d": today.isoformat()})
            buy_count = r.scalar() or 0
            strategy_elapsed = time.time() - signal_start
            logger.info(f"策略计算完成: {total}信号, {buy_count}买入信号, 耗时{strategy_elapsed:.0f}秒")

            db.execute(text(
                "INSERT INTO system_metrics (metric_name, metric_value, status, detail) "
                "VALUES ('daily_strategy', :v, :s, :d)"
            ), {"v": buy_count, "s": "ok",
                "d": json.dumps({"total_signals": total, "buy_signals": buy_count,
                                  "scanned": len(all_data), "elapsed_seconds": round(strategy_elapsed),
                                  "preference": pref_mode}, ensure_ascii=False)})
            db.commit()
        finally:
            db.close()
    finally:
        loader.close()

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"=== 全部完成 耗时{elapsed:.0f}秒 ===")


if __name__ == "__main__":
    main()
