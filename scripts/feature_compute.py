"""特征计算引擎 — KEPL 公式 → 特征值计算 → feature_values 表写入（v2.0 重构 迭代 3.2）。

架构：
- feature_values 长格式表（不动态 ALTER 宽表列）
- KEPL 公式解析 → pandas 执行 → 批量写入
- 支持全量计算（指定日期范围）和增量计算（最近 N 天）
"""
import json
import pandas as pd
from datetime import date, timedelta
from typing import List, Optional, Dict
from sqlalchemy import text
from loguru import logger


def compute_feature(
    db,
    feature_name: str,
    formula: str,
    target_entity: str = "stock",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    stock_codes: Optional[List[str]] = None,
) -> Dict:
    """计算单个特征值并写入 feature_values 表。

    Args:
        db: SQLAlchemy 同步 session
        feature_name: 特征英文名
        formula: KEPL 公式表达式
        target_entity: stock/etf/index/global
        start_date/end_date: 日期范围（None = 全部历史）
        stock_codes: 限定股票代码列表（None = 全部）

    Returns:
        {"ok": True/False, "rows": N, "error": "..."}
    """
    try:
        import numpy as np
        # 1. 拉取 daily_quote 数据 → pandas DataFrame
        table = "daily_quote"
        if target_entity == "index":
            table = "index_daily_quote"

        conditions = []
        params = {}
        if start_date:
            conditions.append("trade_date >= :sd")
            params["sd"] = start_date
        if end_date:
            conditions.append("trade_date <= :ed")
            params["ed"] = end_date
        if stock_codes:
            conditions.append("stock_code = ANY(:codes)")
            params["codes"] = stock_codes
        if target_entity == "stock":
            conditions.append("exchange IN ('SSE','SZSE')")

        where = " AND ".join(conditions) if conditions else "1=1"

        if target_entity == "index":
            sql = f"SELECT index_code as stock_code, trade_date, open, high, low, close, volume, amount FROM {table} WHERE {where} ORDER BY index_code, trade_date"
        else:
            sql = f"SELECT stock_code, trade_date, open, high, low, close, volume, amount FROM {table} WHERE {where} ORDER BY stock_code, trade_date"

        rows = db.execute(text(sql), params).fetchall()
        if not rows:
            return {"ok": True, "rows": 0, "message": "无数据"}

        # 转换为 DataFrame
        cols = ["stock_code", "trade_date", "open", "high", "low", "close", "volume", "amount"]
        df = pd.DataFrame(rows, columns=cols)
        df["trade_date"] = pd.to_datetime(df["trade_date"])

        # 2. KEPL 公式 → pandas 执行计划
        result = _evaluate_kepl_dataframe(df, formula)

        if result is None:
            return {"ok": False, "error": "公式执行失败"}

        # 3. 向量化构造写入数据 + 分片提交（100只股票/批）
        total_rows = 0
        stocks = sorted(df["stock_code"].unique())
        batch_size = 100

        for batch_start in range(0, len(stocks), batch_size):
            batch_stocks = stocks[batch_start:batch_start + batch_size]
            batch_df = df[df["stock_code"].isin(batch_stocks)].copy()

            # 向量化：用 pd.concat 替代 iterrows
            rows_list = []
            for stock in batch_stocks:
                grp = batch_df[batch_df["stock_code"] == stock]
                vals = result.get(stock) if isinstance(result, dict) else result
                if vals is None or len(grp) == 0:
                    continue
                # 对齐：确保 vals 和 grp 长度一致
                if isinstance(vals, (pd.Series, list, np.ndarray)):
                    vals = np.array(vals).flatten()
                else:
                    vals = np.full(len(grp), vals)
                if len(vals) < len(grp):
                    vals = np.pad(vals, (0, len(grp) - len(vals)), constant_values=np.nan)

                mask = pd.notna(vals[:len(grp)])
                stock_rows = pd.DataFrame({
                    "feature_name": feature_name,
                    "stock_code": stock,
                    "trade_date": grp["trade_date"].dt.strftime("%Y-%m-%d").values,
                    "value": vals[:len(grp)],
                })
                rows_list.append(stock_rows[mask])

            if not rows_list:
                continue

            batch_insert = pd.concat(rows_list, ignore_index=True)
            total_rows += len(batch_insert)

            # 批量写入
            for chunk in _chunk(batch_insert.to_dict("records"), 500):
                values_clause = ", ".join(
                    f"('{d['feature_name']}', '{d['stock_code']}', '{d['trade_date']}', {d['value']})"
                    for d in chunk
                )
                db.execute(text(f"""
                    INSERT INTO feature_values (feature_name, stock_code, trade_date, value)
                    VALUES {values_clause}
                    ON CONFLICT (feature_name, stock_code, trade_date) DO UPDATE SET value = EXCLUDED.value
                """))
            db.commit()  # 每批提交，避免长事务

        return {"ok": True, "rows": total_rows}
    except Exception as e:
        logger.error(f"compute_feature({feature_name}): {e}")
        return {"ok": False, "error": str(e)}


def _evaluate_kepl_dataframe(df: pd.DataFrame, formula: str):
    """将 KEPL 公式翻译为 pandas 操作并执行。

    支持的公式模式：
    - bare field: close, volume (直接返回列)
    - function call: ma(close, 5), rsi(close, 14)
    - arithmetic: (close - ma(close,5)) / ma(close,5)
    - cross-sectional: avg(stock.pe) — 暂未实现

    返回：与输入 df 等长的 pd.Series，或按 stock_code 分组的 dict
    """
    import re

    # 特殊处理：ma(close, N)
    m = re.match(r'^ma\(close,\s*(\d+)\)$', formula.strip())
    if m:
        window = int(m.group(1))
        result = {}
        for stock, grp in df.groupby("stock_code"):
            result[stock] = grp["close"].rolling(window=window, min_periods=1).mean().values
        return result

    # ema(close, N)
    m = re.match(r'^ema\(close,\s*(\d+)\)$', formula.strip())
    if m:
        span = int(m.group(1))
        result = {}
        for stock, grp in df.groupby("stock_code"):
            result[stock] = grp["close"].ewm(span=span, adjust=False).mean().values
        return result

    # rsi(close, N)
    m = re.match(r'^rsi\(close,\s*(\d+)\)$', formula.strip())
    if m:
        window = int(m.group(1))
        result = {}
        for stock, grp in df.groupby("stock_code"):
            delta = grp["close"].diff()
            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)
            avg_gain = gain.rolling(window=window, min_periods=1).mean()
            avg_loss = loss.rolling(window=window, min_periods=1).mean()
            rs = avg_gain / avg_loss.replace(0, 1e-10)
            result[stock] = (100 - 100 / (1 + rs)).values
        return result

    # pct_change(close, N)
    m = re.match(r'^pct_change\(close,\s*(\d+)\)$', formula.strip())
    if m:
        n = int(m.group(1))
        result = {}
        for stock, grp in df.groupby("stock_code"):
            result[stock] = grp["close"].pct_change(periods=n).values
        return result

    # boll_upper/mid/lower(close)
    m = re.match(r'^boll_(upper|mid|lower)\(close\)$', formula.strip())
    if m:
        band = m.group(1)
        result = {}
        for stock, grp in df.groupby("stock_code"):
            ma = grp["close"].rolling(window=20, min_periods=1).mean()
            std = grp["close"].rolling(window=20, min_periods=1).std().fillna(0)
            if band == "upper":
                result[stock] = (ma + 2 * std).values
            elif band == "lower":
                result[stock] = (ma - 2 * std).values
            else:
                result[stock] = ma.values
        return result

    # MACD 系列: dif/dea/macd_hist(close)
    m = re.match(r'^(dif|dea|macd_hist)\(close\)$', formula.strip())
    if m:
        fn = m.group(1)
        result = {}
        for stock, grp in df.groupby("stock_code"):
            ema12 = grp["close"].ewm(span=12, adjust=False).mean()
            ema26 = grp["close"].ewm(span=26, adjust=False).mean()
            dif = ema12 - ema26
            dea = dif.ewm(span=9, adjust=False).mean()
            if fn == "dif":
                result[stock] = dif.values
            elif fn == "dea":
                result[stock] = dea.values
            else:
                result[stock] = (dif - dea).values * 2
        return result

    # atr(close, 14) — simplified (uses high-low range as proxy)
    m = re.match(r'^atr\(close,\s*(\d+)\)$', formula.strip())
    if m:
        window = int(m.group(1))
        result = {}
        for stock, grp in df.groupby("stock_code"):
            tr = (grp["high"] - grp["low"]).rolling(window=window, min_periods=1).mean()
            result[stock] = tr.values
        return result

    # Arithmetic: (close - ma(close,N)) / ma(close,N) — bias
    m = re.match(r'^\(close\s*-\s*ma\(close,\s*(\d+)\)\)\s*/\s*ma\(close,\s*(\d+)\)$', formula.strip())
    if m:
        w1, w2 = int(m.group(1)), int(m.group(2))
        result = {}
        for stock, grp in df.groupby("stock_code"):
            ma_v = grp["close"].rolling(window=w1, min_periods=1).mean()
            result[stock] = ((grp["close"] - ma_v) / ma_v.replace(0, 1e-10)).values
        return result

    # bare field: close, volume, etc.
    if formula.strip() in df.columns.tolist():
        col = formula.strip()
        result = {}
        for stock, grp in df.groupby("stock_code"):
            result[stock] = grp[col].values
        return result

    return None


def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def compute_all_features(
    db,
    target_entity: str = "stock",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    feature_names: Optional[List[str]] = None,
) -> Dict:
    """批量计算所有已启用特征（或指定特征列表）并写入 feature_values。"""
    rows = db.execute(text(
        "SELECT feature_name, formula FROM features WHERE target_entity = :ent AND status = 'enabled'"
        + (" AND feature_name = ANY(:names)" if feature_names else "")
    ), {"ent": target_entity, **({"names": feature_names} if feature_names else {})}).fetchall()

    results = []
    for r in rows:
        fn, formula = r[0], r[1]
        res = compute_feature(db, fn, formula, target_entity, start_date, end_date)
        results.append({"feature": fn, **res})

    total_rows = sum(r.get("rows", 0) for r in results)
    errors = [r for r in results if not r.get("ok")]
    return {"ok": len(errors) == 0, "features": len(results), "rows": total_rows, "errors": errors}
