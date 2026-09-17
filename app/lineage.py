"""数据血缘台账：数据/模型变更事件的旁路记录（只存元数据，MB 级）。

回答两类问题：
  1. 「这份数据/这个模型经历过什么」——factor_heal / 零价修复 / 特征重算 /
     复评覆盖 / 训练 / 滚动重训，全部留痕（时间、范围、量级、指标头条）。
  2. 「模型训练后数据又被改过吗」（model_data_drift）——2026-09 的教训：
     v15.0 训练后因子修复改了 test 窗数据，存档指标与实际数据口径脱节，
     只能靠人工回忆。现在一条查询就能给出「训练之后发生的数据事件」清单。

设计约束：**旁路观测，绝不影响主链路**——写台账失败只记日志，不抛异常、
不回滚主流程事务（log_event 自带独立 commit）。
"""
import json

from loguru import logger
from sqlalchemy import text

# 会改变数据口径的事件类型（drift 判定用）；train/eval_rerun/rolling_retrain
# 是模型侧事件，不属于数据漂移
DATA_MUTATION_TYPES = ('factor_heal', 'zero_price_repair', 'feature_recompute', 'backfill')


def log_event(db, event_type: str, target: str, scope: str = '', detail: dict = None) -> bool:
    """血缘事件落账。失败不抛——台账是旁路，绝不拖垮主链路。

    Args:
        db: sync SQLAlchemy session（借用主链路的连接，独立 commit）
        event_type: factor_heal / zero_price_repair / feature_recompute /
                    eval_rerun / train / rolling_retrain / backfill
        target: 作用对象，如 'daily_quote.close_hfq'、'v15.0'、'feature_values'
        scope: 范围摘要，如 '2026-06-22~2026-09-14 933 只'
        detail: 结构化明细（行数/边界日期/指标头条等，JSON 兼容值）
    """
    try:
        db.execute(text(
            "INSERT INTO data_lineage (event_type, target, scope, detail) "
            "VALUES (:t, :g, :s, CAST(:d AS jsonb))"),
            {"t": event_type, "g": target, "s": scope or '',
             "d": json.dumps(detail or {}, ensure_ascii=False, default=str)})
        db.commit()
        return True
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning(f'[lineage] 事件落账失败（不影响主链路）: {event_type}/{target}: {e}')
        return False


def get_events(db, event_type: str = None, target: str = None, limit: int = 100):
    """近期血缘事件（新→旧）。"""
    sql = ("SELECT id, event_type, target, scope, detail, created_at FROM data_lineage")
    conds, params = [], {'lim': min(int(limit), 1000)}
    if event_type:
        conds.append("event_type = :et")
        params['et'] = event_type
    if target:
        conds.append("target = :tg")
        params['tg'] = target
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC LIMIT :lim"
    rows = db.execute(text(sql), params).fetchall()
    return [{'id': r[0], 'event_type': r[1], 'target': r[2], 'scope': r[3],
             'detail': (r[4] if isinstance(r[4], dict) else json.loads(r[4] or '{}')),
             'created_at': str(r[5])} for r in rows]


def model_data_drift(db, version: str):
    """模型训练后发生的数据变更事件（数据漂移清单）。

    返回 {version, trained_at, has_drift, events: [...]}。事件按时间正序，
    每条带 overlaps_train_window（启发式：detail 里含 window/日期范围时与
    训练窗比较；无范围信息按 True 保守处理——宁报勿漏）。
    """
    row = db.execute(text(
        "SELECT trained_at, config FROM model_versions WHERE version=:v"), {"v": version}).fetchone()
    if not row:
        return None
    trained_at, cfg = row[0], (row[1] if isinstance(row[1], dict) else json.loads(row[1] or '{}'))
    train_start = str(cfg.get('train_start') or '')[:10]
    if not trained_at:
        return {'version': version, 'trained_at': None, 'has_drift': False, 'events': []}
    rows = db.execute(text(
        "SELECT event_type, target, scope, detail, created_at FROM data_lineage "
        "WHERE event_type = ANY(:ts) AND created_at > :t ORDER BY id"),
        {"ts": list(DATA_MUTATION_TYPES), "t": trained_at}).fetchall()

    def _dates(ev):
        """从事件 detail/scope 里启发式抽 (lo, hi) 日期对，抽不到返回 None。"""
        import re
        blob = json.dumps(ev.get('detail') or {}, ensure_ascii=False) + (ev.get('scope') or '')
        m = re.findall(r'20\d{2}-\d{2}-\d{2}', blob)
        return (min(m), max(m)) if m else None

    events = []
    for et, tg, sc, dt, ca in rows:
        ev = {'event_type': et, 'target': tg, 'scope': sc,
              'detail': (dt if isinstance(dt, dict) else json.loads(dt or '{}')),
              'created_at': str(ca)}
        win = _dates(ev)
        # 有明确窗口且完全在训练窗之前 → 不重叠；否则保守按可能重叠
        ev['overlaps_train_window'] = True if (not win or not train_start) else not (win[1] < train_start)
        events.append(ev)
    return {'version': version, 'trained_at': str(trained_at),
            'train_start': train_start or None,
            'has_drift': bool(events), 'events': events}
