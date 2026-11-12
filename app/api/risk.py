"""风控规则 API（step3）：规则读写 + 手动评估 + 告警查询。

规则也随 DAG paper_portfolio 节点每日自动评估；
告警经 status.py WS 广播循环推送（type='risk_alert'），前端 Chrome 通知呈现。
"""
from fastapi import APIRouter, Query
from sqlalchemy import text

from app.risk import get_risk_rules, save_risk_rules, evaluate_risk_rules
from app.intraday_watch import get_intraday_rules, save_intraday_rules

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
