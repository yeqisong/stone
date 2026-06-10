"""个股详情 API — 最新策略信号 + 历史信号分页 + K线(含全部指标)。"""
import json
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.db.connection import get_sync_db
from strategy.indicators import bollinger_bands, rsi, macd

router = APIRouter(tags=["stock"])


@router.get("/stock/{code}/detail")
def get_stock_detail(code: str):
    """获取个股详情：最新行情 + 最新交易日策略信号 + 历史信号概览。"""
    db = get_sync_db()
    try:
        result = db.execute(text("""
            SELECT stock_code, stock_name, trade_date, close, close_hfq, volume, turnover
            FROM daily_quote WHERE stock_code=:c ORDER BY trade_date DESC LIMIT 1
        """), {"c": code})
        quote = result.fetchone()
        if not quote:
            raise HTTPException(status_code=404, detail=f"未找到股票 {code}")

        # 最新信号日期
        result = db.execute(text(
            "SELECT MAX(signal_date) FROM signal_history WHERE stock_code=:c"
        ), {"c": code})
        latest_signal_date = result.scalar()

        # 最新交易日的信号（放在页首）
        latest_signals = []
        if latest_signal_date:
            result = db.execute(text("""
                SELECT signal_date, direction, strength, strategy_name, reason, price,
                       suggested_action, combined_signal, source_strategies, preference
                FROM signal_history WHERE stock_code=:c AND signal_date=:d
                ORDER BY combined_signal DESC, strength DESC
            """), {"c": code, "d": str(latest_signal_date)})
            seen = set()
            for r in result.fetchall():
                key = r.strategy_name
                if key in seen:
                    continue
                seen.add(key)
                src = r.source_strategies
                if isinstance(src, str):
                    try:
                        src = json.loads(src)
                    except (json.JSONDecodeError, TypeError):
                        src = [src] if src else []
                latest_signals.append({
                    "signal_date": str(r.signal_date), "direction": r.direction,
                    "strength": r.strength, "strategy_name": r.strategy_name,
                    "reason": r.reason, "price": float(r.price) if r.price else 0,
                    "suggested_action": r.suggested_action or "",
                    "combined_signal": r.combined_signal,
                    "source_strategies": src or [],
                    "preference": r.preference or "balanced",
                })

        # 历史信号总数（无信号日期时 = 0）
        history_count = 0
        if latest_signal_date:
            history_count = db.execute(text(
                "SELECT COUNT(*) FROM signal_history WHERE stock_code=:c AND signal_date!=:d"
            ), {"c": code, "d": str(latest_signal_date)}).scalar() or 0

        # 基本面数据
        fund_row = db.execute(text(
            "SELECT industry, pe_ttm, pb_mrq, roe, revenue_yoy, profit_yoy FROM stock_fundamentals WHERE stock_code=:c"
        ), {"c": code}).fetchone()
        fundamentals = {}
        if fund_row:
            fundamentals = {
                "industry": fund_row.industry or "",
                "pe_ttm": float(fund_row.pe_ttm) if fund_row.pe_ttm else None,
                "pb_mrq": float(fund_row.pb_mrq) if fund_row.pb_mrq else None,
                "roe": float(fund_row.roe) if fund_row.roe else None,
                "revenue_yoy": float(fund_row.revenue_yoy) if fund_row.revenue_yoy else None,
                "profit_yoy": float(fund_row.profit_yoy) if fund_row.profit_yoy else None,
            }

        return {
            "stock_code": quote.stock_code,
            "stock_name": quote.stock_name,
            "latest_trade_date": str(quote.trade_date),
            "close": float(quote.close) if quote.close else 0,
            "close_hfq": float(quote.close_hfq) if quote.close_hfq else None,
            "volume": quote.volume,
            "turnover": float(quote.turnover) if quote.turnover else None,
            "latest_signals": latest_signals,
            "history_count": history_count,
            "latest_signal_date": str(latest_signal_date) if latest_signal_date else None,
            "fundamentals": fundamentals,
        }
    finally:
        db.close()


@router.get("/stock/{code}/history")
def get_stock_history(
    code: str, page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=5, le=50),
):
    """获取个股历史策略信号（分页，不含最新交易日）。"""
    db = get_sync_db()
    try:
        result = db.execute(text(
            "SELECT MAX(signal_date) FROM signal_history WHERE stock_code=:c"
        ), {"c": code})
        latest_date = result.scalar()

        offset = (page - 1) * page_size
        if latest_date:
            result = db.execute(text("""
                SELECT signal_date, direction, strength, strategy_name, reason, price,
                       suggested_action, combined_signal, source_strategies, preference
                FROM signal_history WHERE stock_code=:c AND signal_date!=:d
                ORDER BY signal_date DESC
                LIMIT :l OFFSET :o
            """), {"c": code, "d": str(latest_date),
                   "l": page_size, "o": offset})
        else:
            result = db.execute(text("""
                SELECT signal_date, direction, strength, strategy_name, reason, price,
                       suggested_action, combined_signal, source_strategies, preference
                FROM signal_history WHERE stock_code=:c
                ORDER BY signal_date DESC
                LIMIT :l OFFSET :o
            """), {"c": code, "l": page_size, "o": offset})

        signals = []
        for r in result.fetchall():
            src = r.source_strategies
            if isinstance(src, str):
                try:
                    src = json.loads(src)
                except (json.JSONDecodeError, TypeError):
                    src = [src] if src else []
            signals.append({
                "signal_date": str(r.signal_date), "direction": r.direction,
                "strength": r.strength, "strategy_name": r.strategy_name,
                "reason": r.reason, "price": float(r.price) if r.price else 0,
                "suggested_action": r.suggested_action or "",
                "combined_signal": r.combined_signal,
                "source_strategies": src or [],
                "preference": r.preference or "balanced",
            })

        if latest_date:
            total = db.execute(text(
                "SELECT COUNT(*) FROM signal_history WHERE stock_code=:c AND signal_date!=:d"
            ), {"c": code, "d": str(latest_date)}).scalar() or 0
        else:
            total = db.execute(text(
                "SELECT COUNT(*) FROM signal_history WHERE stock_code=:c"
            ), {"c": code}).scalar() or 0

        return {
            "page": page, "page_size": page_size,
            "total": total, "total_pages": (total + page_size - 1) // page_size,
            "signals": signals,
        }
    finally:
        db.close()


@router.get("/stock/{code}/kline")
def get_stock_kline(
    code: str, days: int = Query(120, ge=30, le=500),
    adjust: str = Query("none", description="复权: none=不复权, qfq=前复权, hfq=后复权"),
):
    """获取 K线 + 技术指标。支持切换复权类型。"""
    db = get_sync_db()
    try:
        col = {"none": "close", "qfq": "close_qfq", "hfq": "close_hfq"}.get(adjust, "close")
        result = db.execute(text(f"""
            SELECT trade_date, open, high, low, COALESCE({col}, close) as close, volume
            FROM daily_quote WHERE stock_code=:c ORDER BY trade_date ASC
        """), {"c": code})
        rows = result.fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail=f"未找到 {code}")

        closes = pd.Series([float(r.close) for r in rows])
        dates = [str(r.trade_date) for r in rows]
        mid, upper, lower, bw = bollinger_bands(closes)
        rsi_vals = rsi(closes)
        dif, dea, macd_bar = macd(closes)

        kline = []
        for i, r in enumerate(rows):
            kline.append({
                "trade_date": dates[i],
                "open": float(r.open), "high": float(r.high),
                "low": float(r.low), "close": float(r.close),
                "volume": int(r.volume),
                "boll_mid": round(float(mid.iloc[i]), 2) if not pd.isna(mid.iloc[i]) else None,
                "boll_upper": round(float(upper.iloc[i]), 2) if not pd.isna(upper.iloc[i]) else None,
                "boll_lower": round(float(lower.iloc[i]), 2) if not pd.isna(lower.iloc[i]) else None,
                "rsi": round(float(rsi_vals.iloc[i]), 1) if not pd.isna(rsi_vals.iloc[i]) else None,
                "dif": round(float(dif.iloc[i]), 3) if not pd.isna(dif.iloc[i]) else None,
                "dea": round(float(dea.iloc[i]), 3) if not pd.isna(dea.iloc[i]) else None,
                "macd_bar": round(float(macd_bar.iloc[i]), 3) if not pd.isna(macd_bar.iloc[i]) else None,
            })
        return {"stock_code": code, "stock_name": rows[0].stock_name if hasattr(rows[0], 'stock_name') else code, "kline": kline}
    finally:
        db.close()
