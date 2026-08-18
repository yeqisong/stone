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
                       open, high, low, close, close as close_hfq, volume, 0 as turnover, amount
                FROM index_daily_quote WHERE index_code=:c ORDER BY trade_date DESC LIMIT 1
            """), {"c": code})
        else:
            result = db.execute(text("""
                SELECT stock_code, trade_date, open, high, low, close, close_hfq, volume, turnover, amount
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

        # 基本面全字段（daily_basic 全部 + 申万行业 + 公司信息）
        fund_row = db.execute(text("""
            SELECT sf.pe_ttm, sf.pe, sf.pb_mrq, sf.ps, sf.ps_ttm, sf.roe,
                   sf.revenue_yoy, sf.profit_yoy, sf.dv_ratio, sf.dv_ttm,
                   sf.turnover_rate, sf.volume_ratio, sf.market_cap, sf.circ_mv,
                   sf.total_shares, sf.float_share, sf.free_share, sf.limit_status,
                   sm.industry_l1, sm.industry_l2,
                   sm.reg_capital, sm.employees, sm.main_business
            FROM stock_fundamentals sf
            LEFT JOIN (SELECT DISTINCT ON (stock_code) stock_code, industry_l1, industry_l2,
                              reg_capital, employees, main_business
                       FROM stock_master WHERE stock_type='stock') sm
                   ON sm.stock_code = sf.stock_code
            WHERE sf.stock_code = :c
        """), {"c": code}).fetchone()
        fundamentals = {}
        if fund_row:
            def F(i):
                try:
                    v = fund_row[i]
                    return float(v) if v is not None else None
                except (ValueError, TypeError):
                    return None
            l1 = fund_row[18] or ""
            l2 = fund_row[19] or ""
            fundamentals = {
                "industry": (l1 + " > " + l2) if (l1 and l2) else (l1 or l2),
                "industry_l1": l1, "industry_l2": l2,
                "pe_ttm": F(0), "pe": F(1), "pb_mrq": F(2), "ps": F(3), "ps_ttm": F(4),
                "roe": F(5), "revenue_yoy": F(6), "profit_yoy": F(7),
                "dv_ratio": F(8), "dv_ttm": F(9), "turnover_rate": F(10),
                "volume_ratio": F(11), "market_cap": F(12), "circ_mv": F(13),
                "total_shares": F(14), "float_share": F(15), "free_share": F(16),
                "limit_status": int(fund_row[17]) if fund_row[17] is not None else None,
                "reg_capital": float(fund_row[20]) if fund_row[20] else None,
                "employees": int(fund_row[21]) if fund_row[21] else None,
                "main_business": (fund_row[22] or "") if fund_row[22] else "",
            }

        return {
            "stock_code": code,
            "stock_name": stock_name,
            "latest_trade_date": str(quote.trade_date),
            "open": float(quote.open) if quote.open else 0,
            "high": float(quote.high) if quote.high else 0,
            "low": float(quote.low) if quote.low else 0,
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
            # 指数：从 index_daily_quote 获取（无复权概念），days 限制最近 N 个交易日
            result = db.execute(text("""
                SELECT trade_date, open, high, low, close, volume, amount, turnover
                FROM index_daily_quote WHERE index_code=:c
                ORDER BY trade_date DESC LIMIT :days
            """), {"c": code, "days": days})
            rows = list(reversed(result.fetchall()))
        else:
            # 个股/ETF：从 daily_quote 获取（支持复权），days 限制最近 N 个交易日
            # 列名来自白名单映射，安全；用两层 SQL 避免 f-string 注入风险
            col = {"none": "close", "qfq": "close_qfq", "hfq": "close_hfq"}.get(adjust, "close")
            col_param = f"COALESCE({col}, close)"  # col 仅来自白名单 dict，安全
            result = db.execute(text(f"""
                SELECT trade_date, open, high, low, {col_param} as close, volume, amount, turnover
                FROM daily_quote WHERE stock_code=:c
                ORDER BY trade_date DESC LIMIT :days
            """), {"c": code, "days": days})
            rows = list(reversed(result.fetchall()))
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
                "amount": float(r.amount) if getattr(r, 'amount', None) else None,
                "turnover": float(r.turnover) if getattr(r, 'turnover', None) else None,
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
        min_date = "CURRENT_DATE - :days * INTERVAL '1 day'"

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
        """), {"days": days}).fetchone()

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
        """), {"days": days}).fetchall()
        daily_trend = [{"date": str(r[0]), "signals": r[1], "win_rate": round(float(r[2]) if r[2] else 0, 3)} for r in daily]

        # 收益分布
        dist = db.execute(text(f"""
            SELECT width_bucket(actual_return, -0.15, 0.15, 10) as bucket, COUNT(*)
            FROM signal_history
            WHERE strategy_name='model_signal' AND status='closed'
              AND actual_return IS NOT NULL AND signal_date >= {min_date}
            GROUP BY bucket ORDER BY bucket
        """), {"days": days}).fetchall()
        buckets = [round(-0.15 + 0.03 * i, 2) for i in range(11)]
        counts = [0] * 11
        for r in dist:
            if r[0] and 1 <= r[0] <= 11:
                counts[r[0] - 1] = r[1]
        distribution = {"buckets": [f"{b:.0%}" for b in buckets], "counts": counts}

        # 按行业（申万二级，替代已废弃的 fundamentals.industry）
        industry = db.execute(text(f"""
            SELECT sm.industry_l2,
                   COUNT(*) as signals,
                   COUNT(*) FILTER (WHERE sh.status='closed' AND sh.actual_return > 0) * 1.0 /
                     NULLIF(COUNT(*) FILTER (WHERE sh.status='closed'), 0) as win_rate,
                   AVG(sh.actual_return) FILTER (WHERE sh.status='closed') as avg_ret
            FROM signal_history sh
            LEFT JOIN stock_master sm ON sm.stock_code = sh.stock_code AND sm.stock_type = 'stock'
            WHERE sh.strategy_name='model_signal' AND sh.signal_date >= {min_date}
            GROUP BY sm.industry_l2 HAVING COUNT(*) >= 5
            ORDER BY signals DESC LIMIT 15
        """), {"days": days}).fetchall()
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
        """), {"days": days}).fetchall()
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
            report_date = str(r[0])[:10]  # 完整日期：日度行显示交易日，季度行显示报告期
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
