"""风控规则 API（step3）：规则读写 + 手动评估 + 告警查询。

规则也随 DAG paper_portfolio 节点每日自动评估；
告警经 status.py WS 广播循环推送（type='risk_alert'），前端 Chrome 通知呈现。
"""
from fastapi import APIRouter, Query, Depends, HTTPException, Request
from sqlalchemy import text

from app.auth.auth import Principal, get_principal
from app.risk import (
    get_user_risk_rules,
    save_user_risk_rules,
    evaluate_risk_rules,
)
from app.intraday_watch import get_intraday_rules, save_intraday_rules, KIND_LABEL

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/rules")
def read_rules(principal: Principal = Depends(get_principal)):
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        return get_user_risk_rules(db, principal.data_user_id)
    finally:
        db.close()


@router.post("/rules")
def write_rules(rules: dict, enabled: bool = True,
                principal: Principal = Depends(get_principal)):
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        return save_user_risk_rules(db, principal.data_user_id, rules, enabled)
    finally:
        db.close()


@router.post("/evaluate")
def run_evaluate(trade_date: str = None):
    """手动触发一次规则评估。"""
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        return {"alerts": evaluate_risk_rules(db, trade_date)}
    finally:
        db.close()


@router.get("/intraday-rules")
def read_intraday_rules(principal: Principal = Depends(get_principal)):
    """个人盘中提醒规则（user_config.intraday_rules）。"""
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        return get_intraday_rules(db, principal.data_user_id)
    finally:
        db.close()


@router.post("/intraday-rules")
def write_intraday_rules(rules: dict, enabled: bool = True,
                         principal: Principal = Depends(get_principal)):
    """保存个人盘中提醒规则；enabled=false 时该用户下一轮休眠。"""
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        return save_intraday_rules(db, principal.data_user_id, rules, enabled)
    finally:
        db.close()


@router.get("/signal-stats")
def signal_stats(days: int = Query(30, ge=1, le=365),
                 principal: Principal = Depends(get_principal)):
    """盘中信号记分卡：按 kind 汇总方向调整后的胜率/收益/MFE/MAE。"""
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT kind, COUNT(*) AS n,
                   AVG(CASE WHEN hit THEN 1.0 ELSE 0.0 END) FILTER (WHERE hit IS NOT NULL) AS hit_rate,
                   AVG(ret_5m), AVG(ret_15m), AVG(ret_30m), AVG(ret_60m),
                   AVG(mfe), AVG(mae), MIN(trade_date), MAX(trade_date), MAX(direction)
            FROM intraday_signal_outcome
            WHERE user_id = :uid
              AND trade_date >= CURRENT_DATE - :d * INTERVAL '1 day'
              AND status IN ('done', 'partial')
            GROUP BY kind
            ORDER BY COUNT(*) DESC, kind
        """), {'d': days, 'uid': principal.data_user_id}).fetchall()
        # 实际样本区间：卡片写「近 30 日」但系统可能只积累了几天，
        # 不标出来会被误读成 30 天的统计结论。
        span = db.execute(text("""
            SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT trade_date)
            FROM intraday_signal_outcome
            WHERE user_id = :uid
              AND trade_date >= CURRENT_DATE - :d * INTERVAL '1 day'
              AND status IN ('done', 'partial')
        """), {'d': days, 'uid': principal.data_user_id}).fetchone()
        def f(v):
            return round(float(v), 6) if v is not None else None
        # 事件 AI 观点验证：把 stance 与 30m「原始方向收益」对齐
        ai_rows = db.execute(text("""
            SELECT stance, COUNT(*) AS n, AVG(raw_ret) AS avg_raw,
                   AVG(CASE
                       WHEN stance = '偏强' AND raw_ret > 0.002 THEN 1.0
                       WHEN stance = '偏弱' AND raw_ret < -0.002 THEN 1.0
                       WHEN stance = '震荡' AND ABS(raw_ret) <= 0.005 THEN 1.0
                       ELSE 0.0 END) AS hit_rate
            FROM (
                SELECT e.result->>'stance' AS stance,
                       CASE WHEN o.direction = 0 THEN o.ret_30m
                            ELSE o.ret_30m * o.direction END AS raw_ret
                FROM intraday_ai_event e
                JOIN intraday_signal_outcome o ON o.alert_id = e.alert_id
                WHERE e.status='done' AND o.ret_30m IS NOT NULL
                  AND o.user_id = :uid AND e.user_id = :uid
                  AND o.trade_date >= CURRENT_DATE - :d * INTERVAL '1 day'
                  AND e.result->>'stance' IS NOT NULL
            ) t
            GROUP BY stance
            ORDER BY COUNT(*) DESC
        """), {'d': days, 'uid': principal.data_user_id}).fetchall()
        return {
            'days': days,
            'sample_from': str(span[0]) if span and span[0] else None,
            'sample_to': str(span[1]) if span and span[1] else None,
            'sample_days': int(span[2] or 0) if span else 0,
            'items': [{
                'kind': r[0], 'label': KIND_LABEL.get(r[0], r[0]),
                'n': int(r[1] or 0), 'hit_rate': f(r[2]),
                'ret_5m': f(r[3]), 'ret_15m': f(r[4]),
                'ret_30m': f(r[5]), 'ret_60m': f(r[6]),
                'mfe': f(r[7]), 'mae': f(r[8]), 'direction': int(r[11] or 0),
                'first_date': str(r[9]) if r[9] else None,
                'last_date': str(r[10]) if r[10] else None,
            } for r in rows],
            'ai_items': [{
                'stance': r[0], 'n': int(r[1] or 0),
                'avg_raw_30m': f(r[2]), 'hit_rate': f(r[3]),
            } for r in ai_rows],
        }
    finally:
        db.close()


@router.get("/signal-outcomes")
def signal_outcomes(code: str = Query(''), days: int = Query(30, ge=1, le=365),
                    limit: int = Query(50, ge=1, le=500),
                    principal: Principal = Depends(get_principal)):
    """逐条信号结果（详情页/复盘用）；code 为空时返回全池。"""
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        where = ["user_id = :uid",
                 "trade_date >= CURRENT_DATE - :d * INTERVAL '1 day'"]
        params = {'d': days, 'n': limit, 'uid': principal.data_user_id}
        if code:
            where.append("stock_code = :c")
            params['c'] = code
        rows = db.execute(text(
            "SELECT alert_id, stock_code, kind, trade_date, alert_time, entry_price, "
            "direction, ret_5m, ret_15m, ret_30m, ret_60m, mfe, mae, hit, status "
            f"FROM intraday_signal_outcome WHERE {' AND '.join(where)} "
            "ORDER BY alert_time DESC LIMIT :n"
        ), params).fetchall()
        def f(v):
            return round(float(v), 6) if v is not None else None
        return {'days': days, 'items': [{
            'alert_id': r[0], 'stock_code': r[1], 'kind': r[2],
            'label': KIND_LABEL.get(r[2], r[2]), 'trade_date': str(r[3]),
            'alert_time': str(r[4])[:19], 'entry_price': f(r[5]),
            'direction': r[6], 'ret_5m': f(r[7]), 'ret_15m': f(r[8]),
            'ret_30m': f(r[9]), 'ret_60m': f(r[10]), 'mfe': f(r[11]),
            'mae': f(r[12]), 'hit': r[13], 'status': r[14],
        } for r in rows]}
    finally:
        db.close()


def _ack_alerts(db, principal: Principal, scope: str) -> int:
    if scope == "model":
        if not principal.can("model.alerts.ack"):
            raise HTTPException(403, "无权限: model.alerts.ack")
        n = db.execute(text("""
            UPDATE risk_alerts SET ack=true, ack_by=:actor, ack_at=CURRENT_TIMESTAMP
            WHERE ack=false AND user_id IS NULL
        """), {"actor": principal.user_id}).rowcount or 0
    else:
        if not principal.can("risk.alerts.ack"):
            raise HTTPException(403, "无权限: risk.alerts.ack")
        n = db.execute(text("""
            UPDATE risk_alerts SET ack=true, ack_by=:actor, ack_at=CURRENT_TIMESTAMP
            WHERE ack=false AND user_id=:uid
        """), {"actor": principal.user_id, "uid": principal.data_user_id}).rowcount or 0
    db.commit()
    return int(n)


@router.get("/alerts")
def list_alerts(limit: int = Query(50, ge=1, le=500), ack: bool = False,
                code: str = Query('', description='按个股过滤，空=全部'),
                intraday: bool = Query(False, description='只看盘中提醒 it*'),
                scope: str = Query('', description='model=模型/系统告警；空=个人告警'),
                principal: Principal = Depends(get_principal)):
    """告警列表；scope=model 查看模型/系统告警，否则只看自己的告警。"""
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        if ack:
            _ack_alerts(db, principal, scope)
        where, params = ["1=1"], {"n": limit}
        if scope == "model":
            if not principal.can("model.alerts.view"):
                raise HTTPException(403, "无权限: model.alerts.view")
            where.append("user_id IS NULL")
        else:
            where.append("user_id = :uid")
            params["uid"] = principal.data_user_id
        if code:
            where.append("stock_code = :code")
            params["code"] = code
        if intraday:
            where.append("kind LIKE 'it%'")
        rows = db.execute(text(
            "SELECT id, trade_date, kind, stock_code, level, title, body, ack, created_at "
            f"FROM risk_alerts WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT :n"
        ), params).fetchall()
        return [{"id": r[0], "trade_date": str(r[1]), "kind": r[2], "stock_code": r[3],
                 "level": r[4], "title": r[5], "body": r[6], "ack": r[7],
                 "created_at": str(r[8])[:19]}
                for r in rows]
    finally:
        db.close()


@router.post("/alerts/ack")
def ack_alerts(scope: str = Query('', description='model=模型/系统告警；空=个人告警'),
               principal: Principal = Depends(get_principal)):
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        return {"ok": True, "updated": _ack_alerts(db, principal, scope)}
    finally:
        db.close()
