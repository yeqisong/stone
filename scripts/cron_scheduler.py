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
                # 到期判定：上次执行时刻在最近 70s 内（扫描周期 60s + 余量）。
                # 用 dag_flows.last_run_at 做幂等：同一 cron 周期只触发一次，
                # 避免扫描延迟/重复扫描导致同一流程触发两次
                if prev and (now - prev).total_seconds() < 70:
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
        while True:
            try:
                _check_and_trigger()
            except Exception as e:
                logger.error(f"[cron] 循环异常: {e}")
            time.sleep(60)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    logger.info("[cron] 调度器线程已创建")
