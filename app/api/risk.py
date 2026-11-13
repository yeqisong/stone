"""风控规则 API（step3）：规则读写 + 手动评估 + 告警查询。

规则也随 DAG paper_portfolio 节点每日自动评估；
告警经 status.py WS 广播循环推送（type='risk_alert'），前端 Chrome 通知呈现。
"""
from fastapi import APIRouter, Query
from sqlalchemy import text

from app.risk import get_risk_rules, save_risk_rules, evaluate_risk_rules
from app.intraday_watch import get_intraday_rules, save_intraday_rules, KIND_LABEL

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/rules")
def read_rules():
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        return get_risk_rules(db)
    finally:
        db.close()


@router.post("/rules")
def write_rules(rules: dict, enabled: bool = True):
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        return save_risk_rules(db, rules, enabled)
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
def read_intraday_rules():
    """盘中提醒规则（strategy_config.intraday_rules）。"""
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        return get_intraday_rules(db)
    finally:
        db.close()


@router.post("/intraday-rules")
def write_intraday_rules(rules: dict, enabled: bool = True):
    """保存盘中提醒规则；enabled=false 时 watcher 下一轮休眠。"""
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        return save_intraday_rules(db, rules, enabled)
    finally:
        db.close()


@router.get("/signal-stats")
def signal_stats(days: int = Query(30, ge=1, le=365)):
    """盘中信号记分卡：按 kind 汇总方向调整后的胜率/收益/MFE/MAE。"""
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT kind, COUNT(*) AS n,
                   AVG(CASE WHEN hit THEN 1.0 ELSE 0.0 END) FILTER (WHERE hit IS NOT NULL) AS hit_rate,
                   AVG(ret_5m), AVG(ret_15m), AVG(ret_30m), AVG(ret_60m),
                   AVG(mfe), AVG(mae), MAX(direction)
            FROM intraday_signal_outcome
            WHERE trade_date >= CURRENT_DATE - :d * INTERVAL '1 day'
              AND status IN ('done', 'partial')
            GROUP BY kind
            ORDER BY COUNT(*) DESC, kind
        """), {'d': days}).fetchall()
        def f(v):
            return round(float(v), 6) if v is not None else None
        return {
            'days': days,
            'items': [{
                'kind': r[0], 'label': KIND_LABEL.get(r[0], r[0]),
                'n': int(r[1] or 0), 'hit_rate': f(r[2]),
                'ret_5m': f(r[3]), 'ret_15m': f(r[4]),
                'ret_30m': f(r[5]), 'ret_60m': f(r[6]),
                'mfe': f(r[7]), 'mae': f(r[8]), 'direction': int(r[9] or 0),
            } for r in rows],
        }
    finally:
        db.close()


@router.get("/signal-outcomes")
def signal_outcomes(code: str = Query(''), days: int = Query(30, ge=1, le=365),
                    limit: int = Query(50, ge=1, le=500)):
    """逐条信号结果（详情页/复盘用）；code 为空时返回全池。"""
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        where = ["trade_date >= CURRENT_DATE - :d * INTERVAL '1 day'"]
        params = {'d': days, 'n': limit}
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


@router.get("/alerts")
def list_alerts(limit: int = Query(50, ge=1, le=500), ack: bool = False,
                code: str = Query('', description='按个股过滤，空=全部'),
                intraday: bool = Query(False, description='只看盘中提醒 it*')):
    """告警列表；ack=true 时顺带全部确认。

    code/intraday 供详情页「盘中提醒」块使用；日频风控告警 stock_code 为 NULL，
    只会被「全部」查询看到。
    """
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        if ack:
            db.execute(text("UPDATE risk_alerts SET ack = true WHERE ack = false"))
            db.commit()
        where, params = ["1=1"], {"n": limit}
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
