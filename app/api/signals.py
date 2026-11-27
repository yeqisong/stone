"""买点扫描 API — 同步 session。"""
import json
from fastapi import APIRouter, Query, Depends
from sqlalchemy import text
from datetime import date

from app.auth.auth import Principal, get_principal
from app.api.pricing import hfq_to_raw_factors, raw_price
from app.db.connection import get_sync_db

router = APIRouter(tags=["signals"])


@router.get("/signals/personal")
def get_personal_signals(
    signal_date: str = Query(None, description="日期 YYYY-MM-DD，空=最近"),
    limit: int = Query(200, ge=1, le=1000),
    stock_code: str | None = Query(None, max_length=16,
                                   description="按股票过滤：个股详情页 K 线叠加个人卖出信号"),
    days: int | None = Query(None, ge=30, le=3650,
                             description="最近 N 天信号史，与 stock_code 搭配"),
    principal: Principal = Depends(get_principal),
):
    """当前用户个人卖出/个人信号（user_signal_history）。

    持仓卖出信号按用户生成、只落本表（不进全局 signal_history），所以个股详情页
    叠加 K 线时要走本端点（受 signal.personal.view 控制），不能混进全局信号端点。
    price 是后复权原值；real_price 是换算后的真实价（展示用），缺当日行情为 None。
    """
    db = get_sync_db()
    try:
        params = {"uid": principal.data_user_id, "n": limit}
        where = "user_id=:uid"
        if signal_date:
            where += " AND signal_date=:d"
            params["d"] = signal_date
        if stock_code:
            where += " AND stock_code=:c"
            params["c"] = stock_code
        if days:
            where += " AND signal_date >= CURRENT_DATE - :days * INTERVAL '1 day'"
            params["days"] = days
        rows = db.execute(text(f"""
            SELECT id, signal_date, stock_code, stock_name, direction, strength, price,
                   reason, model_version, predict_score, status, actual_return,
                   close_reason, created_at
            FROM user_signal_history
            WHERE {where}
            ORDER BY signal_date DESC, id DESC LIMIT :n
        """), params).fetchall()
        fac = hfq_to_raw_factors(db, [(r[2], r[1]) for r in rows])
        return {"items": [{
            "id": r[0], "signal_date": str(r[1]), "stock_code": r[2],
            "stock_name": r[3], "direction": r[4], "strength": r[5],
            "price": float(r[6]) if r[6] is not None else None,
            "real_price": raw_price(float(r[6]) if r[6] is not None else None,
                                    fac.get((r[2], str(r[1])[:10]))),
            "reason": r[7], "model_version": r[8],
            "predict_score": float(r[9]) if r[9] is not None else None,
            "status": r[10],
            "actual_return": float(r[11]) if r[11] is not None else None,
            "close_reason": r[12], "created_at": str(r[13])[:19] if r[13] else None,
        } for r in rows]}
    finally:
        db.close()


@router.get("/buy_signals")
def get_buy_signals(
    signal_date: str = Query(None, description="日期 YYYY-MM-DD，默认最近交易日"),
    top_n: int = Query(20, ge=1, le=1000),
    include_stale: bool = Query(False, description="true=包含旧版本的模型信号（默认只显示 ACTIVE 模型）"),
):
    """获取指定日期的买点扫描结果（仅融合信号）。默认过滤旧版本的 model_signal。"""
    db = get_sync_db()
    try:
        if signal_date is None:
            result = db.execute(text("SELECT MAX(signal_date) FROM signal_history"))
            max_date = result.scalar()
            signal_date = str(max_date) if max_date else str(date.today())

        params = {"d": signal_date, "n": top_n}
        model_filter = ""
        if not include_stale:
            # 只显示当前主模型（role=primary）的信号——备模型不上树图
            active_ver = db.execute(text(
                "SELECT version FROM model_versions WHERE status='ACTIVE' AND role='primary' "
                "ORDER BY activated_at DESC NULLS LAST, created_at DESC LIMIT 1"
            )).scalar()
            if active_ver:
                model_filter = "AND (s.strategy_name != 'model_signal' OR s.model_version = :av)"
                params["av"] = active_ver

        result = db.execute(text(f"""
            SELECT s.stock_code, s.stock_name, s.direction, s.strength,
                   s.reason, s.price, s.suggested_action,
                   s.source_strategies, s.preference,
                   s.predict_5d_return, s.predict_10d_return, s.predict_20d_return, s.predict_score,
                   q.close AS real_close
            FROM signal_history s
            LEFT JOIN daily_quote q
              ON q.stock_code = s.stock_code AND q.trade_date = s.signal_date
            WHERE s.signal_date = :d
              AND s.direction = 'buy'
              AND s.combined_signal = true
              {model_filter}
            ORDER BY COALESCE(s.predict_score, 0) DESC, s.strength DESC
            LIMIT :n
        """), params)
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
                # price 存后复权价（前瞻收益口径所需）；展示层用信号日真实收盘，避免与行情软件对不上
                "real_price": float(r.real_close) if r.real_close else None,
                "suggested_action": r.suggested_action or "",
                "source_strategies": source,
                "preference": r.preference or "balanced",
                "predict_5d": float(r.predict_5d_return) if r.predict_5d_return else None,
                "predict_10d": float(r.predict_10d_return) if r.predict_10d_return else None,
                "predict_20d": float(r.predict_20d_return) if r.predict_20d_return else None,
                "predict_score": float(r.predict_score) if r.predict_score else None,
            })

        result = db.execute(text(
            "SELECT COUNT(DISTINCT stock_code) FROM signal_history "
            "WHERE signal_date = :d AND direction = 'buy' AND combined_signal = true"
        ), {"d": signal_date})
        total = result.scalar() or 0

        # 扫描域口径 = A 股个股（model_signal 宽表实体），不含指数/ETF/变体码
        result = db.execute(text(
            "SELECT COUNT(*) FROM stock_master WHERE stock_type='stock' AND status='N'"))
        scanned = result.scalar() or 0

        # rank 标签模型（v15+）的 predict_* 是当日截面分位(0~1)而非收益率，
        # 前端需按分位渲染（否则 0.52 显示成 +52% 收益造成误读）
        predict_is_rank = False
        predict_is_prob = False
        if active_ver:
            try:
                _cfg = db.execute(text(
                    "SELECT config FROM model_versions WHERE version=:v"), {"v": active_ver}).scalar()
                _c = json.loads(_cfg) if isinstance(_cfg, str) else (_cfg or {})
                predict_is_rank = (_c.get('label_transform') == 'rank')
                predict_is_prob = (_c.get('label_transform') == 'top20')
            except Exception:
                pass
        return {
            "signal_date": signal_date,
            "scanned": scanned,
            "total_signals": total,
            "top_n": top_n,
            "predict_is_rank": predict_is_rank,
            "predict_is_prob": predict_is_prob,
            "signals": signals,
        }
    finally:
        db.close()
