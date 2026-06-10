"""买点扫描 API — 同步 session。"""
import json
from fastapi import APIRouter, Query
from sqlalchemy import text
from datetime import date

from app.db.connection import get_sync_db

router = APIRouter(tags=["signals"])


@router.get("/buy_signals")
def get_buy_signals(
    signal_date: str = Query(None, description="日期 YYYY-MM-DD，默认最近交易日"),
    top_n: int = Query(20, ge=1, le=100),
):
    """获取指定日期的买点扫描结果（仅融合信号）。"""
    db = get_sync_db()
    try:
        if signal_date is None:
            result = db.execute(text("SELECT MAX(signal_date) FROM signal_history"))
            max_date = result.scalar()
            signal_date = str(max_date) if max_date else str(date.today())

        result = db.execute(text("""
            SELECT stock_code, stock_name, direction, strength,
                   reason, price, suggested_action,
                   source_strategies, preference
            FROM signal_history
            WHERE signal_date = :d
              AND direction = 'buy'
              AND combined_signal = true
            ORDER BY strength DESC, stock_code
            LIMIT :n
        """), {"d": signal_date, "n": top_n})
        rows = result.fetchall()

        signals = []
        for r in rows:
            source = r.source_strategies
            if isinstance(source, str):
                try:
                    source = json.loads(source)
                except (json.JSONDecodeError, TypeError):
                    source = [source] if source else []
            elif source is None:
                source = []

            signals.append({
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "direction": r.direction,
                "strength": r.strength,
                "reason": r.reason,
                "price": float(r.price) if r.price else 0,
                "suggested_action": r.suggested_action or "",
                "source_strategies": source,
                "preference": r.preference or "balanced",
            })

        result = db.execute(text(
            "SELECT COUNT(DISTINCT stock_code) FROM signal_history WHERE signal_date = :d AND combined_signal = true"
        ), {"d": signal_date})
        total = result.scalar() or 0

        result = db.execute(text("SELECT COUNT(*) FROM stock_master WHERE status = 'N'"))
        scanned = result.scalar() or 0

        return {
            "signal_date": signal_date,
            "scanned": scanned,
            "total_signals": total,
            "top_n": top_n,
            "signals": signals,
        }
    finally:
        db.close()
