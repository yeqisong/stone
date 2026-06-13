"""持仓 API — 增删改查。使用同步 session 确保读写一致。"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, List

from app.db.connection import get_sync_db
from app.auth.auth import optional_auth

router = APIRouter(tags=["portfolio"])


class AddPosition(BaseModel):
    stock_code: str
    quantity: int
    cost_price: float
    notes: str = ""


class UpdatePosition(BaseModel):
    quantity: Optional[int] = None
    cost_price: Optional[float] = None
    notes: Optional[str] = None


# ── 查（同步，确保读写同一连接看到最新数据）──

@router.get("/portfolio")
def get_portfolio():
    """获取当前持仓（含浮动盈亏）。"""
    db = get_sync_db()
    try:
        result = db.execute(text("""
        SELECT
            p.stock_code, p.stock_name, p.exchange,
            p.quantity, p.cost_price,
            p.created_at, p.updated_at, p.notes,
            COALESCE(
                (SELECT dq.close_hfq FROM daily_quote dq
                 WHERE dq.stock_code = p.stock_code
                 ORDER BY dq.trade_date DESC LIMIT 1),
                (SELECT dq.close FROM daily_quote dq
                 WHERE dq.stock_code = p.stock_code
                 ORDER BY dq.trade_date DESC LIMIT 1)
            ) AS current_price
        FROM portfolio p
        WHERE p.is_active = true
        ORDER BY p.updated_at DESC
    """))
        rows = result.fetchall()
        portfolio = []
        for r in rows:
            qty = r.quantity
            cost = float(r.cost_price)
            price = float(r.current_price) if r.current_price else cost
            pnl = (price - cost) * qty
            pnl_pct = ((price - cost) / cost * 100) if cost > 0 else 0
            portfolio.append({
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "exchange": r.exchange,
                "quantity": qty,
                "cost_price": cost,
                "current_price": round(price, 2),
                "market_value": round(price * qty, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "created_at": str(r.created_at),
                "updated_at": str(r.updated_at),
                "notes": r.notes or "",
            })
        total_value = sum(p["market_value"] for p in portfolio)
        total_pnl = sum(p["pnl"] for p in portfolio)
        return {
            "positions": portfolio,
            "total_value": round(total_value, 2),
            "total_pnl": round(total_pnl, 2),
            "count": len(portfolio),
            "_v": "sync-nullpool-v2",
        }
    finally:
        db.close()


# ── 增/改/删（同步 session）──

@router.post("/portfolio")
def add_position(body: AddPosition, user: str = Depends(optional_auth)):
    """新增持仓。已存在则加权平均更新。"""
    code = body.stock_code.zfill(6)
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(400, "无效的股票代码")

    db = get_sync_db()
    try:
        name = code
        ex = "SZSE" if code.startswith(("0", "3")) else ("SSE" if code.startswith("6") else "BSE")
        sm = db.execute(text("SELECT stock_name FROM stock_master WHERE stock_code=:c"), {"c": code})
        sm_row = sm.fetchone()
        if sm_row:
            name = sm_row[0]

        existing = db.execute(text(
            "SELECT id, quantity, cost_price FROM portfolio WHERE stock_code=:c AND is_active=true"
        ), {"c": code})
        row = existing.fetchone()
        if row:
            old_qty, old_cost = row[1], float(row[2])
            new_qty = old_qty + body.quantity
            new_cost = ((old_cost * old_qty) + (body.cost_price * body.quantity)) / new_qty
            db.execute(text("""
                UPDATE portfolio SET quantity=:q, cost_price=:c, notes=:n, updated_at=CURRENT_TIMESTAMP WHERE id=:id
            """), {"q": new_qty, "c": round(new_cost, 3), "n": body.notes, "id": row[0]})
            db.execute(text("""
                INSERT INTO portfolio_history (stock_code, stock_name, action, quantity_before, quantity_after, cost_before, cost_after)
                VALUES (:c, :n, 'add', :qb, :qa, :cb, :ca)
            """), {"c": code, "n": name, "qb": old_qty, "qa": new_qty, "cb": old_cost, "ca": round(new_cost, 3)})
        else:
            db.execute(text("""
                INSERT INTO portfolio (stock_code, stock_name, exchange, quantity, cost_price, notes)
                VALUES (:c, :n, :e, :q, :p, :nt)
            """), {"c": code, "n": name, "e": ex, "q": body.quantity, "p": body.cost_price, "nt": body.notes})

        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.put("/portfolio/{stock_code}")
def update_position(stock_code: str, body: UpdatePosition, user: str = Depends(optional_auth)):
    """更新持仓数量/成本/备注。"""
    db = get_sync_db()
    try:
        existing = db.execute(text(
            "SELECT id, quantity, cost_price FROM portfolio WHERE stock_code=:c AND is_active=true"
        ), {"c": stock_code})
        row = existing.fetchone()
        if not row:
            raise HTTPException(404, "持仓不存在")

        old_qty, old_cost = row[1], float(row[2])
        new_qty = body.quantity if body.quantity is not None else old_qty
        new_cost = body.cost_price if body.cost_price is not None else old_cost
        new_notes = body.notes if body.notes is not None else ""

        db.execute(text("""
            UPDATE portfolio SET quantity=:q, cost_price=:c, notes=:n, updated_at=CURRENT_TIMESTAMP WHERE id=:id
        """), {"q": new_qty, "c": round(new_cost, 3), "n": new_notes, "id": row[0]})

        db.execute(text("""
            INSERT INTO portfolio_history (stock_code, stock_name, action, quantity_before, quantity_after, cost_before, cost_after)
            SELECT stock_code, stock_name, 'update', :qb, :qa, :cb, :ca FROM portfolio WHERE id=:id
        """), {"qb": old_qty, "qa": new_qty, "cb": old_cost, "ca": round(new_cost, 3), "id": row[0]})

        db.commit()
        return {"ok": True, "updated": {"qty": new_qty, "cost": round(new_cost, 3)}}
    finally:
        db.close()


@router.delete("/portfolio/{stock_code}")
def delete_position(stock_code: str, user: str = Depends(optional_auth)):
    """删除持仓（软删除，设置 is_active=false）。"""
    db = get_sync_db()
    try:
        existing = db.execute(text(
            "SELECT id, quantity FROM portfolio WHERE stock_code=:c AND is_active=true"
        ), {"c": stock_code})
        row = existing.fetchone()
        if not row:
            raise HTTPException(404, "持仓不存在")

        db.execute(text("UPDATE portfolio SET is_active=false, updated_at=CURRENT_TIMESTAMP WHERE id=:id"), {"id": row[0]})
        db.execute(text("""
            INSERT INTO portfolio_history (stock_code, stock_name, action, quantity_before, quantity_after, cost_before, cost_after)
            SELECT stock_code, stock_name, 'delete', :q, 0, cost_price, cost_price FROM portfolio WHERE id=:id
        """), {"q": row[1], "id": row[0]})

        db.commit()
        return {"ok": True, "deleted": stock_code}
    finally:
        db.close()


@router.get("/portfolio/{stock_code}/history")
def get_position_history(stock_code: str):
    """获取指定持仓的加减仓历史记录。"""
    db = get_sync_db()
    try:
        result = db.execute(text("""
            SELECT action, quantity_before, quantity_after, cost_before, cost_after, created_at
            FROM portfolio_history WHERE stock_code = :c
            ORDER BY created_at DESC LIMIT 50
        """), {"c": stock_code})
        records = []
        for r in result.fetchall():
            records.append({
                "action": r[0],
                "qty_before": r[1],
                "qty_after": r[2],
                "cost_before": float(r[3]) if r[3] else 0,
                "cost_after": float(r[4]) if r[4] else 0,
                "created_at": str(r[5]) if r[5] else None,
            })

        current = db.execute(text("""
            SELECT stock_name, quantity, cost_price, notes
            FROM portfolio WHERE stock_code = :c AND is_active = true
        """), {"c": stock_code}).fetchone()

        position = None
        if current:
            position = {
                "stock_code": stock_code,
                "stock_name": current[0],
                "quantity": current[1],
                "cost_price": float(current[2]) if current[2] else 0,
                "notes": current[3] or "",
            }

        return {"records": records, "position": position}
    finally:
        db.close()
