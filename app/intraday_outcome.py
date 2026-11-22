"""盘中信号结果追踪（P0 交易员闭环）。

每条 it_* 提醒落库后，按 5/15/30/60 分钟计算方向调整后的收益、MFE/MAE 与是否命中，
写入 intraday_signal_outcome。数据源只用已有的 stock_minute_bar，不新增外部请求。

口径：
- direction=1 看涨（急涨/上穿/突破），-1 看跌（急跌/下破/跌破），0 中性（止损/止盈/量比）。
- ret/mfe/mae 均为「信号方向有利为正」的比例；止损等中性信号不做方向调整。
- hit：direction != 0 且 30m（缺失时退 60m/15m）方向收益 >= 0.2%（覆盖双边成本）。
- 未走完 60m 且未收盘 → partial；无分钟数据 → no_data；否则 done。
"""
import re
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from loguru import logger
from sqlalchemy import text

from app.db.connection import get_sync_db

DIRECTION = {
    'it_surge': 1, 'it_vwap_up': 1, 'it_or_up': 1, 'it_prev_high': 1,
    'it_drop': -1, 'it_vwap_down': -1, 'it_or_down': -1, 'it_prev_low': -1,
    'it_limit_down': -1,
}
HORIZONS = (('ret_5m', 5), ('ret_15m', 15), ('ret_30m', 30), ('ret_60m', 60))
COST_PCT = 0.002
MAX_AGE_DAYS = 3
_last_run = 0.0
THROTTLE_S = 60


def _bj_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def _price(trigger, body) -> float | None:
    try:
        if trigger is not None:
            v = float(trigger)
            if v > 0:
                return v
    except (TypeError, ValueError):
        pass
    m = re.search(r'现价\s*([0-9]+(?:\.[0-9]+)?)', body or '')
    return float(m.group(1)) if m else None


def _direction(kind: str) -> int:
    return DIRECTION.get(kind, 0)


def _upsert(db, alert, entry, direction, rets, mfe, mae, hit, status, now):
    db.execute(text("""
        INSERT INTO intraday_signal_outcome
        (user_id, alert_id, stock_code, kind, trade_date, alert_time, entry_price, direction,
         ret_5m, ret_15m, ret_30m, ret_60m, mfe, mae, hit, status, evaluated_at)
        VALUES (:uid, :a, :c, :k, :d, :t, :p, :dir,
                :r5, :r15, :r30, :r60, :mfe, :mae, :hit, :s, :now)
        ON CONFLICT (alert_id) DO UPDATE SET
            entry_price=EXCLUDED.entry_price, direction=EXCLUDED.direction,
            ret_5m=EXCLUDED.ret_5m, ret_15m=EXCLUDED.ret_15m,
            ret_30m=EXCLUDED.ret_30m, ret_60m=EXCLUDED.ret_60m,
            mfe=EXCLUDED.mfe, mae=EXCLUDED.mae, hit=EXCLUDED.hit,
            status=EXCLUDED.status, evaluated_at=EXCLUDED.evaluated_at
    """), {
        'uid': getattr(alert, 'user_id', None),
        'a': alert.id, 'c': alert.stock_code, 'k': alert.kind,
        'd': alert.trade_date, 't': alert.created_at, 'p': entry, 'dir': direction,
        'r5': rets.get('ret_5m'), 'r15': rets.get('ret_15m'),
        'r30': rets.get('ret_30m'), 'r60': rets.get('ret_60m'),
        'mfe': mfe, 'mae': mae, 'hit': hit, 's': status, 'now': now,
    })


def evaluate_one(db, alert, now: datetime) -> str:
    """评估一条提醒；返回 status。alert 需含 id/stock_code/kind/trade_date/created_at/body/trigger_price。"""
    entry = _price(alert.trigger_price, alert.body)
    if not entry:
        _upsert(db, alert, None, _direction(alert.kind), {}, None, None, None, 'no_data', now)
        return 'no_data'

    alert_minute = alert.created_at.replace(second=0, microsecond=0)
    rows = db.execute(text("""
        SELECT bar_time, high, low, close
        FROM stock_minute_bar
        WHERE stock_code=:c AND bar_time::date=:d AND bar_time >= :t
        ORDER BY bar_time
    """), {'c': alert.stock_code, 'd': alert.trade_date, 't': alert_minute}).fetchall()

    if not rows:
        # 当天盘中还没采到 bar：先挂 pending，等 watcher 后续补；日后再也没有就 no_data
        done_day = (alert.trade_date < now.date()
                    or now.hour * 60 + now.minute >= 15 * 60 + 10)
        status = 'no_data' if done_day else 'pending'
        _upsert(db, alert, entry, _direction(alert.kind), {}, None, None, None, status, now)
        return status

    last_t = rows[-1][0]
    session_end = (alert.trade_date < now.date()
                   or now.hour * 60 + now.minute >= 15 * 60
                   or (last_t.hour * 60 + last_t.minute) >= 14 * 60 + 57)
    direction = _direction(alert.kind)

    rets = {}
    for key, minutes in HORIZONS:
        target = alert_minute + timedelta(minutes=minutes)
        bar = next((b for b in rows if b[0] >= target), None)
        if bar is None and session_end:
            bar = rows[-1]
        if bar is None:
            rets[key] = None
            continue
        raw = float(bar[3]) / entry - 1
        rets[key] = round(raw if direction >= 0 else -raw, 6)

    highs = [float(b[1]) for b in rows if b[1] is not None]
    lows = [float(b[2]) for b in rows if b[2] is not None]
    if highs and lows:
        if direction < 0:
            mfe = round(1 - min(lows) / entry, 6)
            mae = round(1 - max(highs) / entry, 6)
        else:
            mfe = round(max(highs) / entry - 1, 6)
            mae = round(min(lows) / entry - 1, 6)
    else:
        mfe = mae = None

    # 命中优先看 30m，其次 60m/15m；中性信号不算命中
    base = rets.get('ret_30m')
    if base is None:
        base = rets.get('ret_60m')
    if base is None:
        base = rets.get('ret_15m')
    hit = None if (direction == 0 or base is None) else (base >= COST_PCT)

    max_target = alert_minute + timedelta(minutes=60)
    status = 'done' if (session_end or last_t >= max_target) else 'partial'
    _upsert(db, alert, entry, direction, rets, mfe, mae, hit, status, now)
    return status


def evaluate_pending(limit: int = 200) -> dict:
    now = _bj_now()
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    db = get_sync_db()
    stats = {'done': 0, 'partial': 0, 'pending': 0, 'no_data': 0, 'error': 0}
    try:
        rows = db.execute(text("""
            SELECT a.id, a.user_id, a.stock_code, a.kind, a.trade_date, a.created_at,
                   a.body, a.trigger_price, o.id, o.status
            FROM risk_alerts a
            LEFT JOIN intraday_signal_outcome o ON o.alert_id = a.id
            WHERE a.stock_code IS NOT NULL AND a.kind LIKE 'it%'
              AND a.created_at >= :cut
              AND (o.id IS NULL OR o.status IN ('pending', 'partial'))
            ORDER BY a.id
            LIMIT :n
        """), {'cut': cutoff, 'n': limit}).fetchall()
        for r in rows:
            alert = SimpleNamespace(
                id=r[0], user_id=r[1], stock_code=r[2], kind=r[3], trade_date=r[4],
                created_at=r[5], body=r[6], trigger_price=r[7],
            )
            try:
                status = evaluate_one(db, alert, now)
                stats[status] = stats.get(status, 0) + 1
            except Exception as e:
                stats['error'] += 1
                logger.debug(f"[outcome] alert={r[0]} 评估失败: {str(e)[:120]}")
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"[outcome] 批量评估失败: {str(e)[:160]}")
    finally:
        db.close()
    return stats


def maybe_evaluate_outcomes(force: bool = False) -> dict:
    """60s 节流入口；watcher 每轮调用，不阻塞行情。"""
    global _last_run
    now = time.monotonic()
    if not force and now - _last_run < THROTTLE_S:
        return {}
    _last_run = now
    return evaluate_pending()
