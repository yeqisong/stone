"""持仓 API — 增删改查。使用同步 session 确保读写一致。"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, List

from app.db.connection import get_sync_db
from app.auth.auth import optional_auth, get_current_user

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

_watch_cache = {'ts': 0.0, 'payload': None}
_WATCH_TTL_S = 5
_risk_heat_cache = {'ts': 0.0, 'payload': None}
_RISK_HEAT_TTL_S = 5


@router.get("/portfolio/watch-window")
def get_watch_window(user: str = Depends(get_current_user)):
    """浮窗盯盘：全部持仓的最新价/涨跌 + 当日 1 分钟收盘迷你序列。

    一次批量腾讯快照 + 一次 stock_minute_bar 查询；供 Document PiP 悬浮窗 5s 轮询。
    """
    import time as _time
    from datetime import datetime, timedelta, timezone
    now = _time.time()
    if _watch_cache['payload'] and now - _watch_cache['ts'] < _WATCH_TTL_S:
        return _watch_cache['payload']
    db = get_sync_db()
    try:
        pos = db.execute(text("""
            SELECT stock_code, stock_name, quantity, cost_price
            FROM portfolio WHERE is_active=true AND quantity > 0
            ORDER BY stock_code
        """)).fetchall()
        codes = [r[0] for r in pos]
        sparks = {}
        if codes:
            brow = db.execute(text("""
                SELECT stock_code, bar_time, close FROM (
                    SELECT stock_code, bar_time, close,
                           row_number() OVER (PARTITION BY stock_code ORDER BY bar_time DESC) AS rn
                    FROM stock_minute_bar
                    WHERE stock_code = ANY(CAST(:codes AS varchar[]))
                      AND bar_time::date = CURRENT_DATE
                ) t WHERE rn <= 60 ORDER BY stock_code, bar_time
            """), {'codes': codes}).fetchall()
            for r in brow:
                sparks.setdefault(r[0], []).append(float(r[2]) if r[2] is not None else None)
    finally:
        db.close()
    if not codes:
        payload = {'asof': None, 'items': []}
        _watch_cache.update(ts=now, payload=payload)
        return payload
    try:
        from crawler.adapters.tencent_adapter import realtime_quotes
        raw = realtime_quotes(codes, 'stock')
    except Exception as e:
        from loguru import logger
        logger.warning(f"[watch-window] 快照失败: {str(e)[:120]}")
        return {'asof': None, 'items': [], 'error': '实时快照不可用'}
    qmap = {q.get('code'): q for q in raw.values() if q.get('code')}
    items = []
    for r in pos:
        code, name, qty, cost = r[0], r[1], r[2], float(r[3] or 0)
        q = qmap.get(code) or {}
        price = q.get('price')
        prev = q.get('prev_close')
        change = price - prev if price is not None and prev is not None else None
        pct = (change / prev * 100) if change is not None and prev else None
        items.append({
            'code': code, 'name': name, 'quantity': qty, 'cost_price': cost,
            'price': price, 'prev_close': prev, 'change': change,
            'change_pct': round(pct, 3) if pct is not None else None,
            'spark': sparks.get(code, []),
        })
    payload = {
        'asof': datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M:%S'),
        'items': items,
    }
    _watch_cache.update(ts=now, payload=payload)
    return payload


@router.get("/portfolio/risk-heat")
def get_portfolio_risk_heat(user: str = Depends(get_current_user)):
    """组合风险热度：单票/行业集中度、浮盈、今日盘中 critical 提醒。"""
    import time as _time
    from datetime import datetime, timedelta, timezone
    now = _time.time()
    if _risk_heat_cache['payload'] and now - _risk_heat_cache['ts'] < _RISK_HEAT_TTL_S:
        return _risk_heat_cache['payload']
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT p.stock_code, p.stock_name, p.quantity, p.cost_price, sm.industry_l1
            FROM portfolio p
            LEFT JOIN LATERAL (
                SELECT industry_l1 FROM stock_master
                WHERE stock_code=p.stock_code AND stock_type='stock' LIMIT 1
            ) sm ON true
            WHERE p.is_active=true AND p.quantity > 0
            ORDER BY p.stock_code
        """)).fetchall()
        codes = [r[0] for r in rows]
        fallback = {}
        if codes:
            for r in db.execute(text("""
                SELECT DISTINCT ON (stock_code) stock_code, close
                FROM daily_quote
                WHERE stock_code = ANY(CAST(:codes AS varchar[])) AND close > 0
                ORDER BY stock_code, trade_date DESC
            """), {'codes': codes}).fetchall():
                fallback[r[0]] = float(r[1])
        alert_row = db.execute(text("""
            SELECT COUNT(*), COUNT(*) FILTER (WHERE level='critical')
            FROM risk_alerts
            WHERE trade_date=CURRENT_DATE AND stock_code = ANY(CAST(:codes AS varchar[]))
              AND kind LIKE 'it%'
        """), {'codes': codes or ['']}).fetchone() if codes else (0, 0)
        from app.risk import get_risk_rules
        rules = get_risk_rules(db)
        industry_cap = float(rules.get('industry_cap_pct') or 0.45)
    finally:
        db.close()
    if not codes:
        payload = {'ok': True, 'positions': 0, 'asof': None}
        _risk_heat_cache.update(ts=now, payload=payload)
        return payload

    qmap = {}
    try:
        from crawler.adapters.tencent_adapter import realtime_quotes
        raw = realtime_quotes(codes, 'stock')
        qmap = {q.get('code'): q for q in raw.values() if q.get('code')}
    except Exception:
        pass
    positions = []
    for r in rows:
        code, name, qty, cost, industry = r[0], r[1], r[2], float(r[3] or 0), r[4] or '未分类'
        q = qmap.get(code) or {}
        price = q.get('price') or fallback.get(code) or cost
        mv = price * qty
        positions.append({'code': code, 'name': name, 'industry': industry,
                          'quantity': qty, 'price': price, 'market_value': mv})
    total_mv = sum(p['market_value'] for p in positions) or 1.0
    total_cost = sum(float(r[3] or 0) * r[2] for r in rows)
    pnl = total_mv - total_cost
    pnl_pct = pnl / total_cost if total_cost else None
    for p in positions:
        p['weight'] = p['market_value'] / total_mv
    top = sorted(positions, key=lambda p: -p['weight'])[0]
    by_ind = {}
    for p in positions:
        by_ind[p['industry']] = by_ind.get(p['industry'], 0.0) + p['market_value']
    top_ind, top_ind_mv = max(by_ind.items(), key=lambda kv: kv[1]) if by_ind else ('未分类', 0.0)
    top_ind_w = top_ind_mv / total_mv
    critical = int((alert_row or (0, 0))[1] or 0)
    total_alerts = int((alert_row or (0, 0))[0] or 0)

    flags = []
    if top['weight'] > 0.20:
        flags.append(f"单票集中：{top['name']} {top['weight']*100:.0f}%")
    if top_ind_w > industry_cap:
        flags.append(f"行业集中：{top_ind} {top_ind_w*100:.0f}%（上限 {industry_cap*100:.0f}%）")
    if critical:
        flags.append(f"今日 critical 提醒 {critical} 条")
    if pnl_pct is not None and pnl_pct < -0.10:
        flags.append(f"组合浮亏 {pnl_pct*100:.1f}%")

    score = 0.0
    score += min(30, max(0, (top['weight'] - 0.20) * 100))
    score += min(30, max(0, (top_ind_w - industry_cap) * 100))
    if pnl_pct is not None and pnl_pct < 0:
        score += min(25, abs(pnl_pct) * 50)
    score += min(15, critical * 5)
    score = round(min(100, score), 1)
    label = '高' if score >= 70 else ('中' if score >= 40 else '低')
    payload = {
        'ok': True, 'asof': datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M:%S'),
        'positions': len(positions), 'total_market_value': round(total_mv, 2),
        'total_cost': round(total_cost, 2), 'pnl': round(pnl, 2),
        'pnl_pct': round(pnl_pct, 6) if pnl_pct is not None else None,
        'top_position': {'code': top['code'], 'name': top['name'],
                         'weight': round(top['weight'], 4)},
        'top_industry': {'name': top_ind, 'weight': round(top_ind_w, 4)},
        'industry_cap_pct': industry_cap,
        'alerts_today': total_alerts, 'critical_today': critical,
        'flags': flags, 'score': score, 'label': label,
        'top_positions': [{'code': p['code'], 'name': p['name'],
                           'weight': round(p['weight'], 4),
                           'market_value': round(p['market_value'], 2)}
                          for p in sorted(positions, key=lambda p: -p['weight'])[:5]],
    }
    _risk_heat_cache.update(ts=now, payload=payload)
    return payload


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
                (SELECT dq.close FROM daily_quote dq
                 WHERE dq.stock_code = p.stock_code AND dq.close > 0
                 ORDER BY dq.trade_date DESC LIMIT 1),
                p.cost_price
            ) AS current_price,
            (SELECT sh.direction FROM signal_history sh
             WHERE sh.stock_code = p.stock_code
               AND sh.combined_signal = true
               AND sh.direction IN ('buy','sell')
               AND sh.signal_date = (SELECT MAX(cal_date) FROM trade_calendar WHERE is_trade_day = true AND cal_date <= CURRENT_DATE)
             LIMIT 1) AS signal_direction,
            (SELECT sh.signal_date FROM signal_history sh
             WHERE sh.stock_code = p.stock_code
               AND sh.combined_signal = true
               AND sh.direction IN ('buy','sell')
               AND sh.signal_date = (SELECT MAX(cal_date) FROM trade_calendar WHERE is_trade_day = true AND cal_date <= CURRENT_DATE)
             LIMIT 1) AS signal_date,
            (SELECT sh.strength FROM signal_history sh
             WHERE sh.stock_code = p.stock_code
               AND sh.combined_signal = true
               AND sh.direction IN ('buy','sell')
               AND sh.signal_date = (SELECT MAX(cal_date) FROM trade_calendar WHERE is_trade_day = true AND cal_date <= CURRENT_DATE)
             LIMIT 1) AS signal_strength
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
                "signal_direction": r.signal_direction or "",
                "signal_date": str(r.signal_date) if r.signal_date else "",
                "signal_strength": r.signal_strength or 0,
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
