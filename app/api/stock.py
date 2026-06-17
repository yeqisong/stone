"""个股详情 API — 最新策略信号 + 历史信号分页 + K线(含全部指标)。"""
import json
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.db.connection import get_sync_db
from strategy.indicators import bollinger_bands, rsi, macd

router = APIRouter(tags=["stock"])


@router.get("/stock/{code}/detail")
def get_stock_detail(code: str, type: str = Query(None, description="证券类型: stock/index/etf，不传则自动检测")):
    """获取个股/指数/ETF 详情：最新行情 + 最新交易日策略信号 + 历史信号概览。"""
    db = get_sync_db()
    try:
        # 判断证券类型（优先用前端传入的 type，否则自动检测）
        if type:
            stype = type
        else:
            stype = db.execute(text(
                "SELECT stock_type FROM stock_master WHERE stock_code=:c ORDER BY CASE stock_type WHEN 'stock' THEN 1 WHEN 'etf' THEN 2 WHEN 'index' THEN 3 END LIMIT 1"
            ), {"c": code}).scalar()

        # 从 stock_master 获取名称（权威来源）
        stock_name = db.execute(text(
            "SELECT stock_name FROM stock_master WHERE stock_code=:c AND stock_type=:t"
        ), {"c": code, "t": stype or 'stock'}).scalar() or ""

        if stype == 'index':
            result = db.execute(text("""
                SELECT index_code as stock_code, trade_date,
                       close, close as close_hfq, volume, 0 as turnover, amount
                FROM index_daily_quote WHERE index_code=:c ORDER BY trade_date DESC LIMIT 1
            """), {"c": code})
        else:
            result = db.execute(text("""
                SELECT stock_code, trade_date, close, close_hfq, volume, turnover, amount
                FROM daily_quote WHERE stock_code=:c ORDER BY trade_date DESC LIMIT 1
            """), {"c": code})

        quote = result.fetchone()
        if not quote:
            raise HTTPException(status_code=404, detail=f"未找到 {code}")

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
            "stock_code": code,
            "stock_name": stock_name,
            "latest_trade_date": str(quote.trade_date),
            "close": float(quote.close) if quote.close else 0,
            "close_hfq": float(quote.close_hfq) if quote.close_hfq else None,
            "volume": quote.volume,
            "turnover": float(quote.turnover) if quote.turnover else None,
            "amount": float(quote.amount) if quote.amount else None,
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
    type: str = Query(None, description="证券类型: stock/index/etf"),
):
    """获取 K线 + 技术指标。支持切换复权类型。支持个股/指数/ETF。"""
    db = get_sync_db()
    try:
        # 判断证券类型（优先用前端传入的 type）
        if type:
            stype = type
        else:
            stype = db.execute(text(
                "SELECT stock_type FROM stock_master WHERE stock_code=:c ORDER BY CASE stock_type WHEN 'stock' THEN 1 WHEN 'etf' THEN 2 WHEN 'index' THEN 3 END LIMIT 1"
            ), {"c": code}).scalar()

        if stype == 'index':
            # 指数：从 index_daily_quote 获取（无复权概念）
            result = db.execute(text("""
                SELECT trade_date, open, high, low, close, volume
                FROM index_daily_quote WHERE index_code=:c ORDER BY trade_date ASC
            """), {"c": code})
        else:
            # 个股/ETF：从 daily_quote 获取（支持复权）
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


# ── 信号效果追踪 ──

@router.get("/signal/stats")
def get_signal_stats(days: int = Query(90, ge=30, le=365)):
    """信号效果统计：胜率、平均收益、趋势、行业分布。"""
    db = get_sync_db()
    try:
        min_date = f"CURRENT_DATE - INTERVAL '{days} days'"

        # 总览
        overview = db.execute(text(f"""
            SELECT COUNT(*) as total,
                   COUNT(*) FILTER (WHERE status='closed') as closed,
                   COUNT(*) FILTER (WHERE status IS NULL) as open_sigs,
                   COUNT(*) FILTER (WHERE status='closed' AND actual_return > 0) as wins,
                   AVG(actual_return) FILTER (WHERE status='closed') as avg_ret,
                   AVG(forward_5d_return) as avg_f5d,
                   AVG(forward_10d_return) as avg_f10d,
                   AVG(forward_20d_return) as avg_f20d
            FROM signal_history
            WHERE strategy_name='model_signal' AND signal_date >= {min_date}
        """)).fetchone()

        total = overview.total or 0
        closed = overview.closed or 0
        wins = overview.wins or 0

        # 每日趋势
        daily = db.execute(text(f"""
            SELECT signal_date,
                   COUNT(*) as cnt,
                   COUNT(*) FILTER (WHERE status='closed' AND actual_return > 0) * 1.0 /
                     NULLIF(COUNT(*) FILTER (WHERE status='closed'), 0) as win_rate
            FROM signal_history
            WHERE strategy_name='model_signal' AND signal_date >= {min_date}
            GROUP BY signal_date ORDER BY signal_date
        """)).fetchall()
        daily_trend = [{"date": str(r[0]), "signals": r[1], "win_rate": round(float(r[2]) if r[2] else 0, 3)} for r in daily]

        # 收益分布
        dist = db.execute(text(f"""
            SELECT width_bucket(actual_return, -0.15, 0.15, 10) as bucket, COUNT(*)
            FROM signal_history
            WHERE strategy_name='model_signal' AND status='closed'
              AND actual_return IS NOT NULL AND signal_date >= {min_date}
            GROUP BY bucket ORDER BY bucket
        """)).fetchall()
        buckets = [round(-0.15 + 0.03 * i, 2) for i in range(11)]
        counts = [0] * 11
        for r in dist:
            if r[0] and 1 <= r[0] <= 11:
                counts[r[0] - 1] = r[1]
        distribution = {"buckets": [f"{b:.0%}" for b in buckets], "counts": counts}

        # 按行业
        industry = db.execute(text(f"""
            SELECT sf.industry,
                   COUNT(*) as signals,
                   COUNT(*) FILTER (WHERE sh.status='closed' AND sh.actual_return > 0) * 1.0 /
                     NULLIF(COUNT(*) FILTER (WHERE sh.status='closed'), 0) as win_rate,
                   AVG(sh.actual_return) FILTER (WHERE sh.status='closed') as avg_ret
            FROM signal_history sh
            LEFT JOIN stock_fundamentals sf ON sf.stock_code = sh.stock_code
            WHERE sh.strategy_name='model_signal' AND sh.signal_date >= {min_date}
            GROUP BY sf.industry HAVING COUNT(*) >= 5
            ORDER BY signals DESC LIMIT 15
        """)).fetchall()
        by_industry = [{"industry": r[0] or "未分类", "signals": r[1],
                        "win_rate": round(float(r[2]) if r[2] else 0, 3),
                        "avg_return": round(float(r[3]) if r[3] else 0, 4)} for r in industry]

        # Top 个股
        top_stocks = db.execute(text(f"""
            SELECT sh.stock_code, sh.stock_name,
                   COUNT(*) as signals,
                   COUNT(*) FILTER (WHERE sh.status='closed' AND sh.actual_return > 0) * 1.0 /
                     NULLIF(COUNT(*) FILTER (WHERE sh.status='closed'), 0) as win_rate,
                   AVG(sh.actual_return) FILTER (WHERE sh.status='closed') as avg_ret
            FROM signal_history sh
            WHERE sh.strategy_name='model_signal' AND sh.signal_date >= {min_date}
            GROUP BY sh.stock_code, sh.stock_name HAVING COUNT(*) >= 3
            ORDER BY signals DESC LIMIT 20
        """)).fetchall()
        top = [{"stock_code": r[0], "stock_name": r[1], "signals": r[2],
                "win_rate": round(float(r[3]) if r[3] else 0, 3),
                "avg_return": round(float(r[4]) if r[4] else 0, 4)} for r in top_stocks]

        return {
            "days": days,
            "overview": {
                "total": total, "closed": closed, "open": overview.open_sigs or 0,
                "wins": wins,
                "win_rate": round(wins / max(closed, 1), 3),
                "avg_return": round(float(overview.avg_ret) if overview.avg_ret else 0, 4),
                "avg_forward_5d": round(float(overview.avg_f5d) if overview.avg_f5d else 0, 4),
                "avg_forward_10d": round(float(overview.avg_f10d) if overview.avg_f10d else 0, 4),
                "avg_forward_20d": round(float(overview.avg_f20d) if overview.avg_f20d else 0, 4),
            },
            "daily_trend": daily_trend,
            "return_distribution": distribution,
            "by_industry": by_industry,
            "top_stocks": top,
        }
    finally:
        db.close()


# ── PE 历史数据（从本地 stock_fundamentals_history 表读取） ──

@router.get("/stock/{code}/pe_history")
def get_stock_pe_history(code: str):
    """获取个股历史 PE/PB/ROE 时序数据（从本地表读取）。"""
    db = get_sync_db()
    try:
        result = db.execute(text("""
            SELECT report_date, pe_ttm, pb_mrq, roe
            FROM stock_fundamentals_history
            WHERE stock_code = :c
            ORDER BY report_date ASC
        """), {"c": code})
        rows = result.fetchall()
        if not rows:
            return {"stock_code": code, "pe_data": []}

        all_data = []
        pe_vals = []
        for r in rows:
            pe = float(r[1]) if r[1] else None
            pb = float(r[2]) if r[2] else None
            roe = float(r[3]) if r[3] else None
            if pe: pe_vals.append(pe)
            report_date = str(r[0])[:7]  # "2024-12-01" → "2024-12"
            all_data.append({"date": report_date, "pe_ttm": pe, "pb_mrq": pb, "roe": roe})

        # 计算分位数
        import numpy as np
        if pe_vals:
            arr = np.array(pe_vals)
            for d in all_data:
                if d["pe_ttm"]:
                    pct = np.sum(arr <= d["pe_ttm"]) / len(arr) * 100
                    d["pe_percentile"] = round(pct, 1)

        return {"stock_code": code, "data": all_data}
    finally:
        db.close()
