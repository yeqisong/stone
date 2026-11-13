"""盘中事件 AI 解读 worker（高信号提醒共用一条事件总线）。

链路：
    intraday_watch._persist 命中高信号提醒
      → enqueue_ai_event（内存队列，不阻塞行情/提醒）
      → 单 worker 线程调 analyze_intraday(event=...)
      → 结果写 intraday_ai_event（JSONB）
      → status.py WS 循环推 type='intraday_ai'
      → 详情页 AI 卡自动展开 + Chrome 通知

约束（参数都在 strategy_config.intraday_rules，可经 /api/risk/intraday-rules 调整）：
- ai_event_kinds：默认解读止损/止盈/涨跌停/急涨急跌，以及 VWAP、开盘区间、
  昨高昨低的突破（新高/新低/量比不解读）；
- ai_event_cooldown_min：同股冷却，默认 15 分钟（同一只票 15 分钟内只解读一次）；
- ai_event_max_per_day：全局单日上限，默认 10 条；
- worker 单线程串行：不会并发打 LLM，也不会阻塞 10s 一轮的行情 watcher；
- 队列满/失败只影响 AI，不影响 risk_alerts 落库与 WS 提醒。
"""
import json
import queue
import threading
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import text

from app.db.connection import get_sync_db

_QUEUE: queue.Queue = queue.Queue(maxsize=100)
_worker: threading.Thread | None = None
_stop = threading.Event()


def _bj_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def enqueue_ai_event(item: dict) -> bool:
    """非阻塞入队；队列满时丢弃 AI（提醒本身已落库，不受影响）。"""
    try:
        _QUEUE.put_nowait(item)
        return True
    except queue.Full:
        logger.warning(f"[intraday-ai] 队列已满，丢弃事件 alert_id={item.get('alert_id')}")
        return False


def start_ai_event_worker():
    global _worker
    if _worker and _worker.is_alive():
        return
    _stop.clear()
    _worker = threading.Thread(target=_loop, name='intraday-ai-event', daemon=True)
    _worker.start()
    logger.info("[intraday-ai] 事件 AI worker 启动")


def stop_ai_event_worker():
    _stop.set()
    try:
        _QUEUE.put_nowait(None)     # 唤醒阻塞在 get 的 worker
    except queue.Full:
        pass


def _loop():
    while not _stop.is_set():
        try:
            item = _QUEUE.get(timeout=1)
        except queue.Empty:
            continue
        if item is None:
            _QUEUE.task_done()
            break
        try:
            process_event(item)
        except Exception as e:
            logger.warning(f"[intraday-ai] 事件处理异常: {str(e)[:160]}")
        finally:
            _QUEUE.task_done()


def _get_rules(db) -> dict:
    from app.intraday_watch import get_intraday_rules
    return get_intraday_rules(db)


def _skip_reason(db, alert_id: int, code: str, kind: str, now: datetime, rules: dict):
    """返回跳过原因；None 表示可以解读。"""
    if not rules.get('ai_event_enabled', True):
        return 'disabled'
    if kind not in (rules.get('ai_event_kinds') or []):
        return 'kind'
    if db.execute(text(
        "SELECT 1 FROM intraday_ai_event WHERE alert_id=:a LIMIT 1"
    ), {'a': alert_id}).fetchone():
        return 'already'
    cooldown = max(1, int(rules.get('ai_event_cooldown_min') or 15))
    cutoff = now - timedelta(minutes=cooldown)
    if db.execute(text(
        "SELECT 1 FROM intraday_ai_event WHERE stock_code=:c AND created_at >= :cut LIMIT 1"
    ), {'c': code, 'cut': cutoff}).fetchone():
        return 'cooldown'
    max_day = max(0, int(rules.get('ai_event_max_per_day') or 10))
    today_count = db.execute(text(
        "SELECT count(*) FROM intraday_ai_event WHERE created_at::date = :d"
    ), {'d': now.date()}).scalar() or 0
    if today_count >= max_day:
        return 'daily_cap'
    return None


def process_event(item: dict):
    """处理一条 AI 事件。可独立调用（测试注入 fake DB / fake analyze）。"""
    alert_id = item.get('alert_id')
    if not alert_id:
        return
    now = _bj_now()
    db = get_sync_db()
    try:
        row = db.execute(text(
            "SELECT id, trade_date, kind, stock_code, title, body "
            "FROM risk_alerts WHERE id=:id"
        ), {'id': alert_id}).fetchone()
        if not row:
            return
        _, trade_date, kind, code, title, body = row
        rules = _get_rules(db)
        reason = _skip_reason(db, alert_id, code, kind, now, rules)
        if reason:
            logger.info(f"[intraday-ai] 跳过 {code} {kind}: {reason}")
            return
    finally:
        db.close()

    event = {
        'alert_id': alert_id,
        'kind': kind,
        'title': title,
        'body': body,
        'trade_date': str(trade_date) if trade_date else None,
    }
    try:
        from app.ai.intraday import analyze_intraday
        result = analyze_intraday(code, None, refresh=True, event=event)
    except Exception as e:
        result = {'ok': False, 'error': f'AI 调用异常: {str(e)[:120]}'}

    status = 'done' if result.get('ok') else 'failed'
    finished = _bj_now()            # created_at/finished_at 都是完成时刻
    db = get_sync_db()
    try:
        db.execute(text("""
            INSERT INTO intraday_ai_event
            (alert_id, stock_code, kind, status, result, error, created_at, finished_at)
            VALUES (:a, :c, :k, :s, CAST(:r AS jsonb), :e, :ts, :ts)
            ON CONFLICT (alert_id) DO NOTHING
        """), {
            'a': alert_id, 'c': code, 'k': kind, 's': status,
            'r': json.dumps(result, ensure_ascii=False) if result.get('ok') else None,
            'e': result.get('error') if not result.get('ok') else None,
            'ts': finished,
        })
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"[intraday-ai] 结果落库失败: {str(e)[:160]}")
    finally:
        db.close()
    logger.info(f"[intraday-ai] {code} {kind} -> {status}")
