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

# 字段注册表（v3.7）：基本面日度历史字段 → stock_fundamentals_history（report_date=交易日）。
# 公式引用这些名字时由 _fetch_ohlcv 自动从源表补充列（仅 stock 实体）；缺失自然为 NaN。
EXTRA_FIELD_SOURCES = {
    'pe_ttm': 'stock_fundamentals_history',
    'pb_mrq': 'stock_fundamentals_history',
    'roe': 'stock_fundamentals_history',
    'revenue_yoy': 'stock_fundamentals_history',
    'profit_yoy': 'stock_fundamentals_history',
    'ps_ttm': 'stock_fundamentals_history',
    'dv_ratio': 'stock_fundamentals_history',
    'dv_ttm': 'stock_fundamentals_history',
    'turnover_rate': 'stock_fundamentals_history',
    'volume_ratio': 'stock_fundamentals_history',
    'circ_mv': 'stock_fundamentals_history',
    'total_mv': 'stock_fundamentals_history',
}

# 拓展表字段注册（step2）：KEPL 可引用的跨表字段 → (来源表, 表内真实列名)
EXTRA_TABLE_FIELDS = {
    'stock_moneyflow': {'buy_lg_amt': 'buy_lg_amt', 'sell_lg_amt': 'sell_lg_amt',
                        'buy_elg_amt': 'buy_elg_amt', 'sell_elg_amt': 'sell_elg_amt',
                        'net_mf_amt': 'net_mf_amt'},
    'stock_margin_detail': {'margin_rzye': 'fin_amount', 'margin_rzmre': 'fin_buy_amount',
                            'margin_total': 'total_amount'},
}
_ALL_EXTRA_TABLE_COLS = {alias: tbl for tbl, m in EXTRA_TABLE_FIELDS.items() for alias in m}
_FIELD_RE = '|'.join(EXTRA_FIELD_SOURCES)

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
    if not sel_cols:
        # 公式只引用基本面注册表字段时，仍需 close 锚定主面板行集（交易日×股票）
        sel_cols = ["close"]

    conditions = []
    params = {}
    if start_date:
        conditions.append("trade_date >= :sd")
        params["sd"] = start_date
    if end_date:
        conditions.append("trade_date <= :ed")
        params["ed"] = end_date
    if stock_codes:
        conditions.append(f"{table}.stock_code = ANY(:codes)")
        params["codes"] = stock_codes
    if target_entity == "stock":
        conditions.append(f"{table}.exchange IN ('SSE','SZSE')")
    # 零价格行（停牌占位）不得进入因子面板：会毒化滚动窗口并把除法归一化炸到 1e12（M5 修复）
    conditions.append(f"{table}.close > 0")

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

    # 基本面字段补充（字段注册表）：stock_fundamentals_history 按区间拉取后左对齐
    extra_cols = [c for c in (columns or []) if c in EXTRA_FIELD_SOURCES]
    if extra_cols and target_entity == "stock" and not df.empty:
        col_str2 = ", ".join(extra_cols)
        erows = db.execute(text(
            f"SELECT stock_code, report_date AS trade_date, {col_str2} "
            f"FROM stock_fundamentals_history WHERE report_date BETWEEN :sd AND :ed"
        ), {"sd": params.get("sd", "1990-01-01"), "ed": params.get("ed", "2099-12-31")}).fetchall()
        if erows:
            emap = {(r[0], str(r[1])[:10]): tuple(float(x) if x is not None else None for x in r[2:])
                    for r in erows}
            codes_str = df["stock_code"].astype(str)
            dates_str = df["trade_date"].dt.strftime("%Y-%m-%d")
            for j, cname in enumerate(extra_cols):
                df[cname] = [emap.get((c, d), (None,) * len(extra_cols))[j]
                             for c, d in zip(codes_str, dates_str)]
                df[cname] = df[cname].astype("float64")  # circ_mv 十亿级，float32 会丢精度

    # 拓展表字段补充（资金流）：整表按日期区间拉取后左对齐（step2）
    for tbl, fmap in EXTRA_TABLE_FIELDS.items():
        wanted = [a for a in (columns or []) if a in fmap]
        if not wanted or target_entity != "stock" or df.empty:
            continue
        tbl_cols = ", ".join(fmap[a] for a in wanted)
        trows = db.execute(text(
            f"SELECT stock_code, trade_date, {tbl_cols} FROM {tbl} WHERE trade_date BETWEEN :sd AND :ed"
        ), {"sd": params.get("sd", "1990-01-01"), "ed": params.get("ed", "2099-12-31")}).fetchall()
        tmap = {(r[0], str(r[1])[:10]): tuple(float(x) if x is not None else None for x in r[2:])
                for r in trows}
        codes_str = df["stock_code"].astype(str)
        dates_str = df["trade_date"].dt.strftime("%Y-%m-%d")
        for j, alias in enumerate(wanted):
            df[alias] = [tmap.get((c, d), (None,) * len(wanted))[j]
                         for c, d in zip(codes_str, dates_str)]
            df[alias] = df[alias].astype("float64")

    return df


# ── 批量写入（参数化）──

def _batch_insert(db, feature_name: str, df: pd.DataFrame, stock_codes=None):
    """将 df[["trade_date","stock_code","_value"]] 批量写入 feature_values。

    使用独立的 raw connection + COPY FROM STDIN，自管理 DELETE + COPY + COMMIT。
    COPY 流式写入不构建 SQL，任意行数不 OOM。
    db 参数仅用于获取 engine（不参与事务）。
    stock_codes: 限定代码集时 DELETE 同步带代码过滤——否则按代码重算会清掉
    窗口内其它股票的同特征数据（2026-09-12 修复因子污染重算时发现）。
    """
    from io import StringIO

    insert_df = df[["trade_date", "stock_code", "_value"]].copy()
    # 通用防护：inf → NaN；|值| 超过 NUMERIC(18,6) 安全域的视为脏值剔除（M5）
    v = insert_df["_value"].astype(float)
    v = v.replace([np.inf, -np.inf], np.nan)
    insert_df["_value"] = v.where(v.abs() <= 1e9)
    insert_df["_value"] = insert_df["_value"].apply(lambda x: None if pd.isna(x) else x)
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
        if stock_codes:
            cursor.execute(
                "DELETE FROM feature_values WHERE feature_name = %s AND trade_date >= %s AND trade_date <= %s "
                "AND stock_code = ANY(%s)",
                (feature_name, min_date, max_date, list(stock_codes))
            )
        else:
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

    # 兜底：任意复合公式取其中最大数字参数作为回看天数（如 hhv(high,60)/close → 60；
    # 含 1e-12 防除零字面量时多估几天无害）。
    # 上限 500：单位换算常数（如 circ_mv/100000000 的 1 亿）不是滚动窗口，
    # 误抓会把拉取起点回推出 date 溢出（psycopg2 "date value out of range"）
    nums = [int(x) for x in re.findall(r'\d+', f) if int(x) <= 500]
    return max(nums) if nums else 0


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
        # 0. 从公式提取需要的原始字段（OHLCV + 基本面/拓展表注册表——动态生成单一事实源。
        #    2026-09-07 修复：此处原为手写硬编码名单，与 EXTRA_FIELD_SOURCES 漂移——
        #    roe/revenue_yoy/profit_yoy 加了拉取注册却不在提取名单，公式解析不到字段 → 0 行）
        _ohlcv_fields = {"close", "open", "high", "low", "volume", "amount"}
        _all_reg_fields = sorted(_ohlcv_fields | set(EXTRA_FIELD_SOURCES) | set(_ALL_EXTRA_TABLE_COLS))
        found = set(_re.findall(r'\b(' + '|'.join(_all_reg_fields) + r')\b', formula))
        ohlv_cols = sorted(_ohlcv_fields & found)
        extra_cols = sorted((set(EXTRA_FIELD_SOURCES) | set(_ALL_EXTRA_TABLE_COLS)) & found)
        needed_cols = ohlv_cols + extra_cols
        if not needed_cols:
            needed_cols = ["close"]
        # 内置 ATR 依赖 high/low（公式字面只写 close，但真实波幅需要最高/最低价）
        if _re.search(r'\batr\b', formula):
            needed_cols = sorted(set(needed_cols) | {"high", "low"})
        # neut 中性化依赖市值列（行业映射由算子内部经 db 查 stock_master）
        if _re.search(r'\bneut\b', formula):
            needed_cols = sorted(set(needed_cols) | {"circ_mv"})

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
                            "2000-01-01"
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

                    rows = _batch_insert(db, feature_name, df_chunk, stock_codes=stock_codes)
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
                "2000-01-01"
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

        total_rows = _batch_insert(db, feature_name, df, stock_codes=stock_codes)
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
        # 除零(前价=0)→inf；且极小价股 N 日涨幅可能远超 NUMERIC(18,6) → clip 到安全范围
        return df.groupby("stock_code", observed=True)["close"].transform(
            lambda x: x.pct_change(periods=n).replace([np.inf, -np.inf], np.nan).clip(-1e8, 1e8)
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
    # 除零→inf；极小价股 N 日涨幅可能远超 NUMERIC(18,6) → clip 到安全范围
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: x.pct_change(periods=n).replace([np.inf, -np.inf], np.nan).clip(-1e8, 1e8))

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


def _builtin_std(data: pd.Series, df: pd.DataFrame, n: int) -> pd.Series:
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: x.rolling(window=int(n), min_periods=int(n)).std())


def _builtin_max2(data: pd.Series, df: pd.DataFrame, other) -> pd.Series:
    """max2(a, b)：逐元素取大（KBAR 组 Greater 语义；b 为字段序列或标量）。"""
    other = other.astype(float) if hasattr(other, 'astype') else float(other)
    return pd.Series(np.maximum(data.astype(float), other), index=data.index)


def _builtin_min2(data: pd.Series, df: pd.DataFrame, other) -> pd.Series:
    other = other.astype(float) if hasattr(other, 'astype') else float(other)
    return pd.Series(np.minimum(data.astype(float), other), index=data.index)


# ── Alpha158 移植算子（design/05 M5）──
# 语义对齐 Qlib expr：Ref=滞后（负偏移=未来，显式防 shift 方向 bug）；
# hhv/llv=窗口最高/最低（min_periods=window，未满窗为 NaN——与 Qlib 全窗语义一致）；
# imax/imin=窗口内距极值的天数（0=当日即极值，Qlib IdxMax 口径）；回归系按窗口内时间 0..n-1。

def _builtin_ref(data: pd.Series, df: pd.DataFrame, n: int) -> pd.Series:
    return data.groupby(df["stock_code"], observed=True).shift(int(n))


def _builtin_hhv(data: pd.Series, df: pd.DataFrame, n: int) -> pd.Series:
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: x.rolling(window=int(n), min_periods=int(n)).max())


def _builtin_llv(data: pd.Series, df: pd.DataFrame, n: int) -> pd.Series:
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: x.rolling(window=int(n), min_periods=int(n)).min())


def _builtin_ts_quantile(data: pd.Series, df: pd.DataFrame, n: int, q: float) -> pd.Series:
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: x.rolling(window=int(n), min_periods=int(n)).quantile(float(q)))


def _builtin_ts_rank(data: pd.Series, df: pd.DataFrame, n: int) -> pd.Series:
    """当前值在窗口内的分位（0~1），Qlib Rank 口径。"""
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: x.rolling(window=int(n), min_periods=int(n)).apply(
            lambda a: float((a <= a[-1]).mean()), raw=True))


def _roll_reg(data: pd.Series, df: pd.DataFrame, n: int, which: str) -> pd.Series:
    """窗口线性回归（时间轴 0..n-1）：slope 斜率 / rsquare R² / resi 当日残差。"""
    def _apply(a):
        t = np.arange(len(a), dtype=float)
        tm = t.mean()
        var_t = ((t - tm) ** 2).sum()
        if var_t < 1e-12:
            return 0.0
        ym = a.mean()
        beta = ((t - tm) * (a - ym)).sum() / var_t
        if which == 'slope':
            return beta
        pred = beta * (t - tm) + ym
        if which == 'resi':
            return float(a[-1] - pred[-1])
        ss_res = float(((a - pred) ** 2).sum())
        ss_tot = float(((a - ym) ** 2).sum())
        return 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: x.rolling(window=int(n), min_periods=int(n)).apply(_apply, raw=True))


def _builtin_slope(data: pd.Series, df: pd.DataFrame, n: int) -> pd.Series:
    return _roll_reg(data, df, int(n), 'slope')


def _builtin_rsquare(data: pd.Series, df: pd.DataFrame, n: int) -> pd.Series:
    return _roll_reg(data, df, int(n), 'rsquare')


def _builtin_resi(data: pd.Series, df: pd.DataFrame, n: int) -> pd.Series:
    return _roll_reg(data, df, int(n), 'resi')


def _builtin_imax(data: pd.Series, df: pd.DataFrame, n: int) -> pd.Series:
    """距窗口最高值的天数（0=当日即最高），Qlib IdxMax 口径。"""
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: x.rolling(window=int(n), min_periods=int(n)).apply(
            lambda a: float(len(a) - 1 - int(np.argmax(a))), raw=True))


def _builtin_imin(data: pd.Series, df: pd.DataFrame, n: int) -> pd.Series:
    return data.groupby(df["stock_code"], observed=True).transform(
        lambda x: x.rolling(window=int(n), min_periods=int(n)).apply(
            lambda a: float(len(a) - 1 - int(np.argmin(a))), raw=True))


def _builtin_ts_corr(data: pd.Series, df: pd.DataFrame, other, n: int) -> pd.Series:
    """ts_corr(x, y, n)：两序列滚动相关（x=data 求值结果，y=第二参数序列）。"""
    other = other.astype(float)

    def _corr(a, b):
        if len(a) < 2:
            return np.nan
        sa, sb = a.std(), b.std()  # 样本标准差（ddof=1）
        if sa < 1e-12 or sb < 1e-12:
            return np.nan
        cov = float(((a - a.mean()) * (b - b.mean())).sum() / (len(a) - 1))
        return cov / (sa * sb)

    a = data.astype(float)
    return a.groupby(df["stock_code"], observed=True).transform(
        lambda x: x.rolling(window=int(n), min_periods=int(n)).apply(
            lambda w: _corr(w, other.loc[w.index]), raw=False))


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
    # Alpha158 移植算子（M5）
    'ref': _builtin_ref,
    'hhv': _builtin_hhv,
    'llv': _builtin_llv,
    'ts_quantile': _builtin_ts_quantile,
    'ts_rank': _builtin_ts_rank,
    'slope': _builtin_slope,
    'rsquare': _builtin_rsquare,
    'resi': _builtin_resi,
    'imax': _builtin_imax,
    'imin': _builtin_imin,
    'ts_corr': _builtin_ts_corr,
    'std': _builtin_std,
    'max2': _builtin_max2,
    'min2': _builtin_min2,
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

_CROSS_SECTIONAL_BUILTIN = {'avg', 'sum', 'max', 'min', 'rank', 'quantile', 'zscore', 'neut'}


# ── 截面中性化（neut）：对 [1, log1p(circ_mv), 行业哑变量] 逐日回归取残差 ──
# 去除市值/行业风格暴露，残差即"纯因子"。行业映射来自 stock_master.industry_l1（模块级缓存）。
_INDUSTRY_CACHE: dict = {}

def _load_industry_map(db) -> dict:
    if _INDUSTRY_CACHE:
        return _INDUSTRY_CACHE
    try:
        rows = db.execute(text(
            "SELECT DISTINCT ON (stock_code) stock_code, industry_l1 FROM stock_master "
            "WHERE industry_l1 IS NOT NULL AND industry_l1 <> '' AND stock_type = 'stock' "
            "ORDER BY stock_code, (stock_type = 'stock') DESC"
        )).fetchall()
        _INDUSTRY_CACHE.update({r[0]: r[1] for r in rows})
    except Exception:
        try:
            db.rollback()   # 查询失败不能毒化 session 后续事务
        except Exception:
            pass
    return _INDUSTRY_CACHE


def _neut_cross_sectional(data_series: pd.Series, df: pd.DataFrame, db=None) -> pd.Series:
    """市值+行业中性化：残差 = 因子值 − 回归拟合值（逐交易日截面 OLS）。

    因子值/circ_mv 缺失的行保持 NaN；行业缺失时退化为纯市值中性化。
    对齐约定：data_series 与 df 同行序（positional），残差按原行位置回填。"""
    v = pd.to_numeric(data_series, errors='coerce')
    if 'circ_mv' in df.columns:
        mv_vals = pd.to_numeric(df['circ_mv'], errors='coerce').values
    else:
        mv_vals = np.full(len(df), np.nan)   # 无市值列 → 全 NaN，退化为仅行业中性/原值
    base = pd.DataFrame({
        'dt': df['trade_date'].values,
        'v': v.values,
        'mv': np.log1p(mv_vals),
    })
    base = base.dropna(subset=['v', 'mv'])
    if base.empty:
        return data_series
    # dropna 保留的 index 标签 = 原始 df 行位置（单调递增），用作残差回填坐标
    keep_pos = base.index.to_numpy()

    ind_map = _load_industry_map(db)
    if ind_map:
        base['ind'] = df['stock_code'].map(ind_map).reindex(keep_pos).values
        dummies = pd.get_dummies(base['ind'], prefix='i', drop_first=True, dtype=float)
    else:
        dummies = pd.DataFrame(index=base.index)

    # 设计矩阵按 base 行序构建；逐日切片做 OLS（日期内全零哑变量列裁剪防共线）
    X_all = np.column_stack([np.ones(len(base)), base['mv'].values] +
                            ([dummies.values] if len(dummies.columns) else []))
    resid_keep = base['v'].values.astype(float).copy()
    for _, grp in base.groupby('dt', sort=False):
        labels = grp.index.to_numpy()
        local = np.searchsorted(keep_pos, labels)
        X = X_all[local]
        X = X[:, ~np.all(X == 0, axis=0)]          # 日期内全零哑变量列裁剪（防共线）
        if X.shape[0] <= X.shape[1] + 10:          # 样本太少不做回归，保留原值
            continue
        y = grp['v'].values.astype(float)
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid_keep[local] = y - X @ beta

    out = pd.Series(np.nan, index=data_series.index)
    out.iloc[keep_pos] = resid_keep
    return out


def neutralize_columns(df: pd.DataFrame, cols: list, db=None) -> pd.DataFrame:
    """宽表多列特征的截面中性化入口（训练/推理预处理，与 KEPL neut 算子同内核）。

    要求 df 已含 circ_mv 列（调用方负责 merge）；原地更新并返回 df。"""
    for c in cols:
        if c in df.columns:
            df[c] = _neut_cross_sectional(df[c], df, db)
    return df



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
    """保留占位（rank 已由执行引擎按截面分位实现，此处不注册避免误用）。"""
    return 0.0

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
            # rank: 同交易日所有股票中该值的截面分位（0~1），逐行计算
            if node.name == 'rank':
                tmp = pd.DataFrame({'dt': df['trade_date'], 'v': data_series})
                return tmp.groupby('dt')['v'].rank(pct=True)
            # zscore: 同交易日的 z 标准化
            if node.name == 'zscore':
                tmp = pd.DataFrame({'dt': df['trade_date'], 'v': data_series})
                g = tmp.groupby('dt')['v']
                std = g.transform('std').replace(0, 1e-10)
                return (data_series - g.transform('mean')) / std
            # neut: 市值+行业中性化（残差），需 df 含 circ_mv（needed_cols 检测已保证）
            if node.name == 'neut':
                return _neut_cross_sectional(data_series, df, db)
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

        rs = isinstance(right, pd.Series)

        if node.op == '+':
            return left + right
        elif node.op == '-':
            # 减法不满足交换律：必须按 AST 顺序（原实现标量在左时算成 right-left，
            # 形如 100-rsi(close,14) 的公式符号翻转静默入库）
            return left - right
        elif node.op == '*':
            return left * right
        elif node.op == '/':
            if rs:
                # 分母为 0 → NaN（0 价停牌行防护；1e-10 放大替换会把合法分子炸成 1e10 级）
                return left / right.replace(0, np.nan)
            if isinstance(right, pd.Series):
                return left / right
            return left / right if right != 0 else left * np.nan

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
    progress_cb=None,
) -> Dict:
    """批量计算所有已启用特征（或指定特征列表）并写入 feature_values。

    v2.1: 全量 OHLCV 只拉一次，各特征复用 DataFrame。
    progress_cb: fn(done, total, feature_name, rows_so_far) —— 每个特征算完回调一次，
                 用于 DAG 节点上报心跳（否则长跑期间 heartbeat_at 不更新会被看门狗误杀）。
    """
    rows = db.execute(text(
        "SELECT feature_name, formula FROM features WHERE target_entity = :ent AND status = 'enabled'"
        + (" AND feature_name = ANY(:names)" if feature_names else "")
    ), {"ent": target_entity, **({"names": feature_names} if feature_names else {})}).fetchall()

    if not rows:
        return {"ok": True, "features": 0, "rows": 0, "errors": []}

    # 拉一次全量 OHLCV，所有特征复用
    # 按所有特征的最大 lookback 扩展起始日：增量计算时滚动窗口需要历史数据，
    # 否则 ma/rsi/boll 等窗口只有部分数据，同一日期的特征值会随每次重算漂移
    max_lb = max((_extract_lookback(f) for _, f in rows), default=0)
    fetch_start = start_date
    if max_lb > 0 and start_date:
        from datetime import datetime as _dtp, timedelta as _tdp
        margin = max(int(max_lb * 2.0), 10)
        fetch_start = (_dtp.strptime(start_date, "%Y-%m-%d") - _tdp(days=margin)).strftime("%Y-%m-%d")
    # 2026-09-07 修复：共享拉取必须携带全部公式引用字段的并集——原实现不传 columns，
    # 共享 df 只有 OHLCV，凡公式引用基本面/资金流字段的特征在夜间节点全部失败
    # （ep_ttm/bp_mrq/size_inv 等 11 个，手动单算正常因单算路径自检测字段）
    import re as _re0
    _base = {"close", "open", "high", "low", "volume", "amount"}
    _all_fields = sorted(_base | set(EXTRA_FIELD_SOURCES) | set(_ALL_EXTRA_TABLE_COLS))
    _union = set(_base)
    for _, f in rows:
        _union |= set(_re0.findall(r'\b(' + '|'.join(_all_fields) + r')\b', f))
        if _re0.search(r'\batr\b', f):
            _union |= {"high", "low"}
        if _re0.search(r'\bneut\b', f):
            _union |= {"circ_mv"}
    df = _fetch_ohlcv(db, target_entity, fetch_start, end_date, columns=sorted(_union))

    results = []
    total_features = len(rows)
    for i, r in enumerate(rows, 1):
        fn, formula = r[0], r[1]
        # 跳过 DB commit（每特征单独 commit），由 compute_feature 内部处理
        res = compute_feature(db, fn, formula, target_entity, start_date, end_date, df=df)
        results.append({"feature": fn, **res})
        if progress_cb:
            try:
                progress_cb(i, total_features, fn, sum(x.get("rows", 0) for x in results))
            except Exception:
                pass  # 进度上报失败绝不影响计算

    total_rows = sum(r.get("rows", 0) for r in results)
    errors = [r for r in results if not r.get("ok")]
    return {"ok": len(errors) == 0, "features": len(results), "rows": total_rows, "errors": errors}
