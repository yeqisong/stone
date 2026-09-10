"""
Cron 调度器 — 每分钟扫描 dag_flows，触发到期的定时流程。

在 `app/main.py` 的 `lifespan` 末尾调用 `start_cron_scheduler()` 即可启动。
"""
import time
import threading
from datetime import datetime
from loguru import logger


def _check_and_trigger():
    """扫描所有已发布的流程，检查 cron 表达式是否到期。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    from app.api.dag_flows import _execute_flow_internal

    db = get_sync_db()
    try:
        now = datetime.now()
        flows = db.execute(text(
            "SELECT id, flow_name, cron_expr FROM dag_flows WHERE status='published' AND cron_expr IS NOT NULL AND cron_expr != ''"
        )).fetchall()

        for f in flows:
            try:
                cron = croniter(f[2], now)
                prev = cron.get_prev(datetime)
                # 到期判定：当前时间距上一 cron 槽位 24h 内即触发（重启补跑）。
                # last_run_at 幂等保证同一槽位只触发一次：last_run_at >= 槽位 → 跳过。
                # 2026-09-03 修复：原 70s 窗口导致后端在触发时刻宕机/重启时流程被
                # 永久跳过（当日 00:05 错过即全天无数据采集），放宽为 24h 内补跑。
                if prev and (now - prev).total_seconds() < 86400:
                    last_run = db.execute(text(
                        "SELECT last_run_at FROM dag_flows WHERE id=:id"
                    ), {"id": f[0]}).scalar()
                    if last_run and last_run >= prev:
                        continue  # 该 cron 周期已触发过
                    logger.info(f"[cron] 触发 {f[1]} (id={f[0]})")
                    result = _execute_flow_internal(f[0], {"trade_date": now.strftime("%Y-%m-%d")})
                    db.execute(text(
                        "UPDATE dag_flows SET last_run_at = CURRENT_TIMESTAMP WHERE id=:id"
                    ), {"id": f[0]})
                    db.commit()
                    if result.get("error"):
                        logger.warning(f"[cron] {f[1]} 触发失败: {result.get('error')}")
            except Exception as e:
                logger.warning(f"[cron] {f[1]} cron 解析失败 (expr={f[2]}): {e}")
    except Exception as e:
        logger.error(f"[cron] 扫描异常: {e}")
    finally:
        db.close()


def start_cron_scheduler():
    """以后台线程启动 cron 调度器。"""
    try:
        global croniter
        from croniter import croniter
    except ImportError:
        logger.warning("[cron] croniter 未安装，定时调度不可用。运行: pip install croniter")
        return

    def _loop():
        logger.info("[cron] 调度器已启动 (每 60 秒扫描)")
        from crawler.adapters.tushare_quota import TushareQuota
        from app.db.connection import get_sync_db
        while True:
            try:
                _check_and_trigger()
            except Exception as e:
                logger.error(f"[cron] 循环异常: {e}")
            # 顺带落盘 tushare 配额（单行 upsert，开销可忽略）：进程重启后 load() 才能接上当日计数
            try:
                _qdb = get_sync_db()
                try:
                    TushareQuota.get().persist(_qdb)
                finally:
                    _qdb.close()
            except Exception as e:
                logger.warning(f"[cron] tushare 配额落盘失败: {e}")
            time.sleep(60)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    logger.info("[cron] 调度器线程已创建")
