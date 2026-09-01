"""一次性修复：回填 daily_quote / index_daily_quote 的空名称。

背景：tushare daily / index_daily / fund_daily 接口不返回名称，
写入端一直写 ''，导致 1844 万行 daily_quote、2494 万行 index_daily_quote 名称为空。

数据源：
- daily_quote 个股/ETF → stock_master（证券主档，stock_basic/fund 同步）
- daily_quote 场内基金（无主档 907 个代码）→ tushare fund_basic
- index_daily_quote → tushare index_basic

写入端防复发见 crawler/writers.py（空名不覆盖 + 主档补全）。
"""
import os
import sys
import time

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from loguru import logger

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

ENG = create_engine(os.getenv("DATABASE_URL_SYNC"))

# ── 1) stock_master 回填 daily_quote（占位名排除：stock_master 中 index 行名称是代码本身）──
def backfill_daily_from_master():
    t0 = time.time()
    with ENG.begin() as c:
        r = c.execute(text("""
            UPDATE daily_quote dq SET stock_name = sm.stock_name
            FROM stock_master sm
            WHERE dq.stock_name = ''
              AND dq.stock_code = sm.stock_code
              AND dq.exchange = sm.exchange
              AND sm.stock_name <> '' AND sm.stock_name <> sm.stock_code
        """))
        logger.info(f"[names] daily_quote←stock_master 回填 {r.rowcount} 行，{time.time()-t0:.0f}s")

# ── 2) fund_basic 回填 daily_quote 无主档的场内基金 ──
def backfill_daily_from_fund_basic():
    import tushare as ts
    pro = ts.pro_api(os.getenv("TUSHARE_TOKEN"))
    df = pro.fund_basic(market="E")
    codes, names = [], []
    for _, r in df.iterrows():
        raw = str(r["ts_code"])
        if "." not in raw:
            continue
        code, suf = raw.split(".")
        ex = "SSE" if suf == "SH" else ("SZSE" if suf == "SZ" else "")
        codes.append(code.zfill(6)); names.append(str(r["name"])[:30])  # daily_quote.stock_name VARCHAR(30)
    t0 = time.time()
    with ENG.begin() as c:
        r = c.execute(text("""
            UPDATE daily_quote dq SET stock_name = v.nm
            FROM (SELECT unnest(:codes) AS code, unnest(:names) AS nm) v
            WHERE dq.stock_name = '' AND dq.stock_code = v.code
        """), {"codes": codes, "names": names})
        logger.info(f"[names] daily_quote←fund_basic 回填 {r.rowcount} 行，{time.time()-t0:.0f}s")

# ── 3) index_basic 回填 index_daily_quote ──
def backfill_index_from_index_basic():
    import tushare as ts
    pro = ts.pro_api(os.getenv("TUSHARE_TOKEN"))
    # index_basic 单次上限 8000 行，offset 翻页拉全量（全量约 1.26 万）
    frames = []
    offset = 0
    while True:
        df = pro.index_basic(offset=offset, limit=8000)
        if df is None or df.empty:
            break
        frames.append(df)
        if len(df) < 8000:
            break
        offset += 8000
    import pandas as pd
    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts_code"])
    codes, names = [], []
    for _, r in df.iterrows():
        raw = str(r["ts_code"])
        if "." not in raw:
            continue
        code, suf = raw.split(".")
        ex = "SSE" if suf == "SH" else ("SZSE" if suf == "SZ" else "")
        codes.append(code.zfill(6)); names.append(str(r["name"])[:60])  # index_daily_quote.index_name VARCHAR(64)
    t0 = time.time()
    with ENG.begin() as c:
        r = c.execute(text("""
            UPDATE index_daily_quote dq SET index_name = v.nm
            FROM (SELECT unnest(:codes) AS code, unnest(:names) AS nm) v
            WHERE dq.index_name = '' AND dq.index_code = v.code
        """), {"codes": codes, "names": names})
        logger.info(f"[names] index_daily_quote←index_basic 回填 {r.rowcount} 行，{time.time()-t0:.0f}s")

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "master"):
        backfill_daily_from_master()
    if which in ("all", "fund"):
        backfill_daily_from_fund_basic()
    if which in ("all", "index"):
        backfill_index_from_index_basic()
    with ENG.connect() as c:
        r = c.execute(text("SELECT COUNT(*) FROM daily_quote WHERE stock_name=''")).fetchone()
        r2 = c.execute(text("SELECT COUNT(*) FROM index_daily_quote WHERE index_name=''")).fetchone()
        logger.info(f"[names] 剩余空名：daily_quote={r[0]}，index_daily_quote={r2[0]}")
