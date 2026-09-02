"""风控规则 API（step3）：规则读写 + 手动评估 + 告警查询。

规则也随 DAG paper_portfolio 节点每日自动评估；
告警经 status.py WS 广播循环推送（type='risk_alert'），前端 Chrome 通知呈现。
"""
from fastapi import APIRouter, Query
from sqlalchemy import text

from app.risk import get_risk_rules, save_risk_rules, evaluate_risk_rules

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


@router.get("/alerts")
def list_alerts(limit: int = Query(50, ge=1, le=500), ack: bool = False):
    """告警列表；ack=true 时顺带全部确认。"""
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        if ack:
            db.execute(text("UPDATE risk_alerts SET ack = true WHERE ack = false"))
            db.commit()
        rows = db.execute(text(
            "SELECT id, trade_date, kind, level, title, body, ack, created_at "
            "FROM risk_alerts ORDER BY id DESC LIMIT :n"
        ), {"n": limit}).fetchall()
        return [{"id": r[0], "trade_date": str(r[1]), "kind": r[2], "level": r[3],
                 "title": r[4], "body": r[5], "ack": r[6], "created_at": str(r[7])[:19]}
                for r in rows]
    finally:
        db.close()
