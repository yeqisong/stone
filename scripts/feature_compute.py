"""特征计算引擎 — KEPL 公式 → 特征值计算 → feature_values 表写入（v2.1 向量化重构）。

架构：
- feature_values 长格式表（不动态 ALTER 宽表列）
- KEPL 公式解析 → pandas groupby().transform() 向量化 → 批量写入
- 支持全量计算（指定日期范围）和增量计算（最近 N 天）

v2.1 变更：
- _evaluate_kepl_dataframe 改为 groupby().transform() 全 DataFrame 向量化（消除逐股票 for 循环）
- compute_feature 回写消除逐股票构造小 df → pd.concat 循环
- SQL 写入从 f-string 拼接改为参数化 executemany
- compute_all_features 全量 OHLCV 拉一次，复用
"""
import json
import numpy as np
import pandas as pd
from datetime import date, timedelta
from typing import List, Optional, Dict
from sqlalchemy import text
from loguru import logger
from strategy.indicators import sma, ema, rsi, macd, atr, bollinger_bands


# ── SQL 拉取 OHLCV 数据（供共享复用）──

def _fetch_ohlcv(db, target_entity: str, start_date: str = None, end_date: str = None,
                 stock_codes: list = None, columns: list = None) -> pd.DataFrame:
    """拉取 daily_quote / index_daily_quote → pandas DataFrame。

    columns: 指定拉取列（默认全部 OHLCV），如 ["close"] 只拉 close。
    """
    table = "daily_quote"
    code_col = "stock_code"
    if target_entity == "index":
        table = "index_daily_quote"
        code_col = "index_code"

    # 列选择
    all_ohlcv = ["open", "high", "low", "close", "volume", "amount"]
    if columns:
        sel_cols = [c for c in columns if c in all_ohlcv]
    else:
        sel_cols = all_ohlcv

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

    # JOIN stock_master 按 stock_type 过滤（避免指数/ETF 混入 stock 特征）
    join_clause = ""
    if target_entity in ("stock", "etf"):
        st_filter = "stock" if target_entity == "stock" else "etf"
        join_clause = f"JOIN stock_master sm ON {table}.stock_code = sm.stock_code AND sm.stock_type = '{st_filter}'"

    col_str = ", ".join(sel_cols)
    if target_entity == "index":
        sql = f"SELECT {code_col} as stock_code, trade_date, {col_str} FROM {table} {join_clause} WHERE {where} ORDER BY {code_col}, trade_date"
    else:
        sql = f"SELECT {table}.stock_code, {table}.trade_date, {col_str} FROM {table} {join_clause} WHERE {where} ORDER BY {table}.stock_code, {table}.trade_date"

    rows = db.execute(text(sql), params).fetchall()
    if not rows:
        return pd.DataFrame()

    cols = ["stock_code", "trade_date"] + sel_cols
    df = pd.DataFrame(rows, columns=cols)
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    # 瘦身：OHLCV 精度 float32 完全够用
    for col in sel_cols:
        if col in df.columns:
            df[col] = df[col].astype("float32")

    # stock_code 用 category 类型
    df["stock_code"] = df["stock_code"].astype("category")

    return df


# ── 批量写入（参数化）──

def _batch_insert(db, feature_name: str, df: pd.DataFrame):
    """将 df[["trade_date","stock_code","_value"]] 批量写入 feature_values。

    使用独立的 raw connection + COPY FROM STDIN，自管理 DELETE + COPY + COMMIT。
    COPY 流式写入不构建 SQL，任意行数不 OOM。
    db 参数仅用于获取 engine（不参与事务）。
    """
    from io import StringIO

    insert_df = df[["trade_date", "stock_code", "_value"]].copy()
    insert_df["_value"] = insert_df["_value"].apply(lambda v: None if pd.isna(v) else v)
    insert_df = insert_df.dropna(subset=["_value"])
    if insert_df.empty:
        return 0

    insert_df["trade_date"] = insert_df["trade_date"].dt.strftime("%Y-%m-%d")
    min_date = insert_df["trade_date"].min()
    max_date = insert_df["trade_date"].max()

    # 构建 CSV 缓冲区
    buf = StringIO()
    insert_df["feature_name"] = feature_name
    insert_df[["feature_name", "stock_code", "trade_date", "_value"]].to_csv(
        buf, header=False, index=False, na_rep="\\N"
    )
    buf.seek(0)

    # 独立连接：DELETE + COPY + COMMIT，不污染调用方 session 事务
    raw_conn = db.get_bind().raw_connection()
    cursor = raw_conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM feature_values WHERE feature_name = %s AND trade_date >= %s AND trade_date <= %s",
            (feature_name, min_date, max_date)
        )
        cursor.copy_expert(
            "COPY feature_values (feature_name, stock_code, trade_date, value) FROM STDIN WITH CSV",
            buf
        )
        raw_conn.commit()
    except Exception:
        raw_conn.rollback()
        raise
    finally:
        cursor.close()
        raw_conn.close()

    return len(insert_df)


# ── 公式 lookback 提取 ──

def _extract_lookback(formula: str) -> int:
    """从 KEPL 公式中提取所需的最短回看交易日数。

    用于扩展数据拉取范围：start_date - lookback × 1.5 自然日，确保
    rolling/ewm 等算子有足够的历史数据。

    返回 0 表示裸字段（close/volume 等）无需回看。
    """
    import re
    f = formula.strip()

    # ma(close, N) → N 个交易日
    m = re.match(r'^ma\(close,\s*(\d+)\)$', f)
    if m:
        return int(m.group(1))

    # pct_change(close, N) → N 个交易日
    m = re.match(r'^pct_change\(close,\s*(\d+)\)$', f)
    if m:
        return int(m.group(1))

    # ema(close, N) → 约需 2× span 才收敛
    m = re.match(r'^ema\(close,\s*(\d+)\)$', f)
    if m:
        return int(m.group(1)) * 2

    # rsi(close, N) → 约需 2× period 才稳定
    m = re.match(r'^rsi\(close,\s*(\d+)\)$', f)
    if m:
        return int(m.group(1)) * 2

    # atr(close, N) → N 个交易日
    m = re.match(r'^atr\(close,\s*(\d+)\)$', f)
    if m:
        return int(m.group(1))

    # boll_upper/mid/lower(close) → 固定 20
    if re.match(r'^boll_(upper|mid|lower)\(close\)$', f):
        return 20

    # dif/dea/macd_hist(close) → slow EMA 26 + signal 9
    if re.match(r'^(dif|dea|macd_hist)\(close\)$', f):
        return 26 + 9

    # 乖离率: (close - ma(close,N)) / ma(close,N) → N 个交易日
    m = re.match(r'^\(close\s*-\s*ma\(close,\s*(\d+)\)\)\s*/\s*ma\(close,\s*(\d+)\)$', f)
    if m:
        return max(int(m.group(1)), int(m.group(2)))

    # bare field: close, volume, etc. → 无需回看
    return 0


# ── 核心计算 ──

def compute_feature(
    db,
    feature_name: str,
    formula: str,
    target_entity: str = "stock",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    stock_codes: Optional[List[str]] = None,
    df: Optional[pd.DataFrame] = None,
    chunk_days: int = 30,
    progress_cb = None,
) -> Dict:
    """计算单个特征值并写入 feature_values 表。

    大范围自动分片：按 chunk_days 天/片拆分，每片独立 fetch→compute→COPY→commit。
    单片失败不影响已完成片。

    Args:
        db: SQLAlchemy 同步 session（仅用于读取，不参与写入事务）
        feature_name: 特征英文名
        formula: KEPL 公式表达式
        target_entity: stock/etf/index/global
        start_date/end_date: 用户请求的日期范围（None = 全部历史）
        stock_codes: 限定股票代码列表（None = 全部）
        df: 可选复用 DataFrame（分片模式不适用）
        chunk_days: 每片自然日数（默认 30）
        progress_cb: fn(chunk_num, total_chunks, chunk_start, chunk_end, rows) 进度回调

    Returns:
        {"ok": True/False, "rows": N, "chunks": M, "error": "..."}
    """
    from datetime import datetime, timedelta
    import re as _re

    try:
        # 0. 从公式提取需要的 OHLCV 列
        _ohlcv_fields = {"close", "open", "high", "low", "volume", "amount"}
        needed_cols = sorted(_ohlcv_fields & set(_re.findall(r'\b(close|open|high|low|volume|amount)\b', formula)))
        if not needed_cols:
            needed_cols = ["close"]

        lookback = _extract_lookback(formula)

        # ── 分片逻辑 ──
        if start_date and end_date and chunk_days and chunk_days > 0:
            sd = datetime.strptime(start_date, "%Y-%m-%d")
            ed = datetime.strptime(end_date, "%Y-%m-%d")
            total_days = (ed - sd).days + 1

            if total_days > chunk_days:
                # 大范围：拆分为多个 chunk
                chunks = []
                cs = sd
                while cs <= ed:
                    ce = min(cs + timedelta(days=chunk_days - 1), ed)
                    chunks.append((cs.strftime("%Y-%m-%d"), ce.strftime("%Y-%m-%d")))
                    cs = ce + timedelta(days=1)

                total_rows = 0
                for i, (cs, ce) in enumerate(chunks):
                    # 扩展拉取范围（含 lookback × 2.0 自然日 + 保底 10 天）
                    # 确保分片边界有足够历史数据用于滚动窗口计算
                    fetch_sd = cs
                    if lookback > 0:
                        cs_dt = datetime.strptime(cs, "%Y-%m-%d")
                        margin = max(int(lookback * 2.0), 10)
                        fetch_sd = max(
                            (cs_dt - timedelta(days=margin)).strftime("%Y-%m-%d"),
                            "2020-01-01"
                        )

                    # fetch → compute → filter → write
                    df_chunk = _fetch_ohlcv(db, target_entity, fetch_sd, ce, stock_codes, columns=needed_cols)
                    if df_chunk.empty:
                        if progress_cb:
                            progress_cb(i + 1, len(chunks), cs, ce, 0)
                        continue

                    result = _evaluate_kepl_dataframe(df_chunk, formula, db)
                    if result is None:
                        return {"ok": False, "error": f"公式 '{formula}' 不支持或执行失败"}

                    df_chunk["_value"] = result
                    df_chunk = df_chunk[
                        (df_chunk["trade_date"] >= pd.Timestamp(cs)) &
                        (df_chunk["trade_date"] <= pd.Timestamp(ce))
                    ]

                    rows = _batch_insert(db, feature_name, df_chunk)
                    total_rows += rows

                    if progress_cb:
                        progress_cb(i + 1, len(chunks), cs, ce, rows)

                db.rollback()
                return {"ok": True, "rows": total_rows, "chunks": len(chunks)}

        # ── 小范围 / 不分片：单次 fetch → compute → write ──
        fetch_start = start_date
        if lookback > 0 and start_date:
            sd_dt = datetime.strptime(start_date, "%Y-%m-%d")
            margin = max(int(lookback * 2.0), 10)
            fetch_start = max(
                (sd_dt - timedelta(days=margin)).strftime("%Y-%m-%d"),
                "2020-01-01"
            )

        if df is None or df.empty:
            df = _fetch_ohlcv(db, target_entity, fetch_start, end_date, stock_codes, columns=needed_cols)

        if df.empty:
            return {"ok": True, "rows": 0, "message": "无数据"}

        df = df.reset_index(drop=True)
        result = _evaluate_kepl_dataframe(df, formula, db)
        if result is None:
            return {"ok": False, "error": f"公式 '{formula}' 不支持或执行失败"}

        df["_value"] = result
        if start_date:
            df = df[df["trade_date"] >= pd.Timestamp(start_date)]
        if end_date:
            df = df[df["trade_date"] <= pd.Timestamp(end_date)]

        total_rows = _batch_insert(db, feature_name, df)
        db.rollback()

        if progress_cb:
            progress_cb(1, 1, start_date or "", end_date or "", total_rows)

        return {"ok": True, "rows": total_rows}
    except Exception as e:
        logger.error(f"compute_feature({feature_name}): {e}")
        return {"ok": False, "error": str(e)}


def _evaluate_kepl_dataframe(df: pd.DataFrame, formula: str, db=None) -> Optional[pd.Series]:
    """将 KEPL 公式翻译为 pandas 向量化操作。

    db: SQLAlchemy session，供自定义函数从 functions 表加载源码（可选）。

    全部通过 groupby().transform() + strategy/indicators.py 执行，
    零 Python per-stock 循环。
    """
    import re

    f = formula.strip()

    # ── 均线: ma(close, N) ──（groupby rolling 替代 transform lambda，Cython 加速 8x）
    m = re.match(r'^ma\(close,\s*(\d+)\)$', f)
    if m:
        window = int(m.group(1))
        r = df.groupby("stock_code", sort=False)["close"].rolling(window=window, min_periods=1).mean()
        r.index = r.index.droplevel(0)
        return r

    # ── 指数均线: ema(close, N) ──（ewm 无 groupby 优化，保留 transform）
    m = re.match(r'^ema\(close,\s*(\d+)\)$', f)
    if m:
        span = int(m.group(1))
        return df.groupby("stock_code", observed=True)["close"].transform(
            lambda x: x.ewm(span=span, adjust=False, min_periods=1).mean()
        )

    # ── RSI: rsi(close, N) ──
    m = re.match(r'^rsi\(close,\s*(\d+)\)$', f)
    if m:
        period = int(m.group(1))
        return df.groupby("stock_code", observed=True)["close"].transform(
            lambda x: rsi(x, period)
        )

    # ── 涨跌幅: pct_change(close, N) ──
    m = re.match(r'^pct_change\(close,\s*(\d+)\)$', f)
    if m:
        n = int(m.group(1))
        return df.groupby("stock_code", observed=True)["close"].transform(
            lambda x: x.pct_change(periods=n)
        )

    # ── 布林带: boll_upper/mid/lower(close) ──
    m = re.match(r'^boll_(upper|mid|lower)\(close\)$', f)
    if m:
        band = m.group(1)
        return df.groupby("stock_code", observed=True)["close"].transform(
            lambda x: _boll_band(x, band)
        )

    # ── MACD: dif/dea/macd_hist(close) ──
    m = re.match(r'^(dif|dea|macd_hist)\(close\)$', f)
    if m:
        fn = m.group(1)
        return df.groupby("stock_code", observed=True)["close"].transform(
            lambda x: _macd_line(x, fn)
        )

    # ── ATR: atr(close, N) ── 需要 high/low/close 三列
    m = re.match(r'^atr\(close,\s*(\d+)\)$', f)
    if m:
        period = int(m.group(1))
        # groupby().apply 返回的 index 与原始 df 对齐
        result = df.groupby("stock_code", observed=True, group_keys=False).apply(
            lambda grp: _atr_vectorized(grp, period), include_groups=False
        )
        # apply 可能返回 Series 或 DataFrame，统一为与 df 等长的 Series
        if isinstance(result, pd.DataFrame):
            result = result.iloc[:, 0]
        return result.reset_index(drop=True) if isinstance(result.index, pd.MultiIndex) else result

    # ── 乖离率: (close - ma(close,N)) / ma(close,N) ──
    m = re.match(r'^\(close\s*-\s*ma\(close,\s*(\d+)\)\)\s*/\s*ma\(close,\s*(\d+)\)$', f)
    if m:
        w1, w2 = int(m.group(1)), int(m.group(2))
        window = max(w1, w2)
        ma_v = df.groupby("stock_code", observed=True)["close"].transform(
            lambda x: x.rolling(window=window, min_periods=1).mean()
        )
        return (df["close"] - ma_v) / ma_v.replace(0, 1e-10)

    # ── 裸字段: close, volume, open, high, low, amount ──
    if f in df.columns:
        return df[f]

    # ── AST 驱动执行（v2.2: 兜底，支持嵌套函数调用、算术表达式、自定义函数）──
    from app.kepl.parser import parse_to_tree, FieldRef, NumLit, FuncCall, BinOp
    tree = parse_to_tree(f)
    if tree is not None:
        result = _execute_ast(df, tree, db)
        if result is not None:
            return result

    return None


# ── AST 执行引擎（v2.2）──

# 内置函数注册表：函数名 → (data_series, *params, df) → Series
# data_series 可能是 df[col]（FieldRef）或上游计算结果（嵌套调用）。
# 所有函数通过 df["stock_code"] 做 groupby().transform() 确保按股计算。

def _builtin_ma(data: pd.Series, df: pd.DataFrame, window: int) -> pd.Series:
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: x.rolling(window=window, min_periods=1).mean())

def _builtin_ema(data: pd.Series, df: pd.DataFrame, span: int) -> pd.Series:
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: x.ewm(span=span, adjust=False, min_periods=1).mean())

def _builtin_rsi(data: pd.Series, df: pd.DataFrame, period: int) -> pd.Series:
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: rsi(x, period))

def _builtin_pct_change(data: pd.Series, df: pd.DataFrame, n: int) -> pd.Series:
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: x.pct_change(periods=n))

def _builtin_boll_upper(data: pd.Series, df: pd.DataFrame) -> pd.Series:
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: _boll_band(x, 'upper'))

def _builtin_boll_mid(data: pd.Series, df: pd.DataFrame) -> pd.Series:
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: _boll_band(x, 'mid'))

def _builtin_boll_lower(data: pd.Series, df: pd.DataFrame) -> pd.Series:
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: _boll_band(x, 'lower'))

def _builtin_dif(data: pd.Series, df: pd.DataFrame) -> pd.Series:
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: _macd_line(x, 'dif'))

def _builtin_dea(data: pd.Series, df: pd.DataFrame) -> pd.Series:
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: _macd_line(x, 'dea'))

def _builtin_macd_hist(data: pd.Series, df: pd.DataFrame) -> pd.Series:
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: _macd_line(x, 'macd_hist'))

def _builtin_atr(data: pd.Series, df: pd.DataFrame, period: int) -> pd.Series:
    return _exec_atr(df, period)


_BUILTIN_REGISTRY = {
    'ma': _builtin_ma,
    'ema': _builtin_ema,
    'rsi': _builtin_rsi,
    'pct_change': _builtin_pct_change,
    'boll_upper': _builtin_boll_upper,
    'boll_mid': _builtin_boll_mid,
    'boll_lower': _builtin_boll_lower,
    'dif': _builtin_dif,
    'dea': _builtin_dea,
    'macd_hist': _builtin_macd_hist,
    'atr': _builtin_atr,
}

# 自定义函数缓存：{func_name: callable}
_custom_fn_cache: dict = {}


def _load_custom_function(db, func_name: str):
    """从 functions 表加载自定义函数源码 → RestrictedPython 编译 → 缓存。

    返回 callable(df_series, *params) → pd.Series，或 None。
    """
    if func_name in _custom_fn_cache:
        return _custom_fn_cache[func_name]

    if db is None:
        return None

    try:
        from RestrictedPython import compile_restricted, safe_globals
        from sqlalchemy import text

        row = db.execute(text(
            "SELECT source_code FROM functions WHERE name = :n AND status = 'published'"
        ), {"n": func_name}).fetchone()
        if not row:
            return None

        source_code = row[0]
        byte_code = compile_restricted(source_code, '<custom_fn>', 'exec')

        local_env = {}
        exec(byte_code, safe_globals, local_env)

        if func_name not in local_env:
            return None

        fn = local_env[func_name]
        _custom_fn_cache[func_name] = fn
        return fn
    except Exception:
        return None


# ── 跨股票函数注册表 ──

_CROSS_SECTIONAL_BUILTIN = {'avg', 'sum', 'max', 'min', 'rank', 'quantile', 'zscore'}

# 跨股票内置实现：func(series) → scalar，框架负责 iterate stock + filter
def _cs_avg(series: pd.Series) -> float:
    return series.mean()

def _cs_sum(series: pd.Series) -> float:
    return series.sum()

def _cs_max(series: pd.Series) -> float:
    return series.max()

def _cs_min(series: pd.Series) -> float:
    return series.min()

def _cs_rank(series: pd.Series) -> float:
    """返回当前值在序列中的分位（0~1），框架会传入包含/不包含自身的数据。"""
    return 0.0  # rank 需要上下文（当前值），暂由框架层的 exclude_self 语义覆盖

_CROSS_SECTIONAL_IMPL = {
    'avg': _cs_avg, 'sum': _cs_sum, 'max': _cs_max, 'min': _cs_min,
}

# ── 执行引擎 ──

def _execute_ast(df: pd.DataFrame, node, db=None) -> Optional[pd.Series]:
    """递归执行 KepLAST 表达式树。

    两条执行路径：
    - 时间序列函数 (ma/ema/rsi/...): groupby().transform() 逐股隔离
    - 跨股票函数 (avg/sum/max/min/rank/zscore): 框架迭代股票 + 可选 exclude_self
    """
    from app.kepl.parser import FieldRef, NumLit, BoolLit, FuncCall, BinOp

    if isinstance(node, FieldRef):
        if node.name in df.columns:
            return df[node.name]
        return None

    if isinstance(node, NumLit):
        return node.value

    if isinstance(node, BoolLit):
        return node.value

    if isinstance(node, FuncCall):
        args = [_execute_ast(df, a, db) for a in node.args]
        if any(a is None for a in args):
            return None

        data_series = args[0] if args else None
        params = args[1:] if len(args) > 1 else []

        # ── 路径 1: 跨股票函数（avg/sum/max/min 等）──
        if node.name in _CROSS_SECTIONAL_BUILTIN:
            exclude_self = params[0] if params and isinstance(params[0], bool) else False
            fn = _CROSS_SECTIONAL_IMPL.get(node.name)
            if fn is None:
                return None
            return _execute_cross_sectional(df, data_series, fn, exclude_self)

        # ── 路径 2: 内置时间序列函数 ──
        if node.name in _BUILTIN_REGISTRY:
            try:
                # float → int 自动转换（rolling(window) 需要 int）
                clean_params = [int(p) if isinstance(p, float) and p == int(p) else p for p in params]
                return _BUILTIN_REGISTRY[node.name](data_series, df, *clean_params)
            except Exception:
                return None

        # ── 路径 3: 自定义函数 ──
        custom_fn = _custom_fn_cache.get(node.name)
        if custom_fn is None and db is not None:
            custom_fn = _load_custom_function(db, node.name)

        if custom_fn is not None:
            try:
                clean_params = [int(p) if isinstance(p, float) and p == int(p) else p for p in params]
                grouper = df["stock_code"]
                return data_series.groupby(grouper, observed=True).transform(
                    lambda x: custom_fn(x, *clean_params)
                )
            except Exception:
                return None

        return None

    if isinstance(node, BinOp):
        left = _execute_ast(df, node.left, db)
        right = _execute_ast(df, node.right, db)
        if left is None or right is None:
            return None

        ls = isinstance(left, pd.Series)
        rs = isinstance(right, pd.Series)

        if node.op == '+':
            return left + right if ls else right + left
        elif node.op == '-':
            return left - right if ls else right - left
        elif node.op == '*':
            return left * right if ls else right * left
        elif node.op == '/':
            if rs:
                return left / right.replace(0, 1e-10)
            return left / right if right != 0 else left / 1e-10

    return None


def _execute_cross_sectional(df: pd.DataFrame, data_series: pd.Series,
                              fn, exclude_self: bool) -> pd.Series:
    """跨股票函数执行：框架迭代每只股票，可选排除自身后调用 fn。

    Args:
        df: 全量 DataFrame（含 stock_code 列）
        data_series: 已求值的数据列（如 df['close']）
        fn: series → scalar 函数
        exclude_self: True 表示每次传入"除当前股票以外"的数据

    Returns:
        与 df 等长的 pd.Series，每只股票分配一个标量值。
    """
    import numpy as np
    result = pd.Series(np.nan, index=df.index, dtype='float64')
    stocks = df["stock_code"].unique()

    if not exclude_self:
        # 不排除自身：所有股票同值
        val = fn(data_series.dropna())
        result[:] = val
        return result

    # 排除自身：每只股票计算"其他股票"的聚合值
    for stock in stocks:
        mask_self = df["stock_code"] == stock
        mask_others = df["stock_code"] != stock
        other_data = data_series[mask_others].dropna()
        if len(other_data) > 0:
            val = fn(other_data)
            result[mask_self] = val

    return result


# ── 辅助函数（供 transform lambda 调用，每次处理单只股票）──
# 全部委托给 strategy/indicators.py 的向量化函数，确保计算逻辑一致。


def _boll_band(close: pd.Series, band: str) -> pd.Series:
    """单股票布林带提取，委托 bollinger_bands()。"""
    mid, upper, lower, _ = bollinger_bands(close)
    if band == "upper":
        return upper
    elif band == "lower":
        return lower
    return mid


def _macd_line(close: pd.Series, fn: str) -> pd.Series:
    """单股票 MACD 系列提取，委托 macd()。"""
    dif, dea, hist = macd(close)
    if fn == "dif":
        return dif
    elif fn == "dea":
        return dea
    return hist


def _exec_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """ATR 的 groupby().apply() 包装，返回与 df 等长的 Series。"""
    result = df.groupby("stock_code", observed=True, group_keys=False).apply(
        lambda grp: _atr_vectorized(grp, period), include_groups=False
    )
    if isinstance(result, pd.DataFrame):
        result = result.iloc[:, 0]
    # apply 可能返回 MultiIndex，reset 为与 df 同 index
    if isinstance(result.index, pd.MultiIndex):
        result = result.reset_index(drop=True)
    return result


def _atr_vectorized(grp: pd.DataFrame, period: int) -> pd.Series:
    """单股票 ATR 计算，委托 atr()。grp 需含 high/low/close 列。"""
    return atr(grp, period)


# ── 批量计算 ──

def compute_all_features(
    db,
    target_entity: str = "stock",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    feature_names: Optional[List[str]] = None,
) -> Dict:
    """批量计算所有已启用特征（或指定特征列表）并写入 feature_values。

    v2.1: 全量 OHLCV 只拉一次，各特征复用 DataFrame。
    """
    rows = db.execute(text(
        "SELECT feature_name, formula FROM features WHERE target_entity = :ent AND status = 'enabled'"
        + (" AND feature_name = ANY(:names)" if feature_names else "")
    ), {"ent": target_entity, **({"names": feature_names} if feature_names else {})}).fetchall()

    if not rows:
        return {"ok": True, "features": 0, "rows": 0, "errors": []}

    # 拉一次全量 OHLCV，所有特征复用
    df = _fetch_ohlcv(db, target_entity, start_date, end_date)

    results = []
    for r in rows:
        fn, formula = r[0], r[1]
        # 跳过 DB commit（每特征单独 commit），由 compute_feature 内部处理
        res = compute_feature(db, fn, formula, target_entity, start_date, end_date, df=df)
        results.append({"feature": fn, **res})

    total_rows = sum(r.get("rows", 0) for r in results)
    errors = [r for r in results if not r.get("ok")]
    return {"ok": len(errors) == 0, "features": len(results), "rows": total_rows, "errors": errors}
