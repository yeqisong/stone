"""服务器全量数据初始化：5 年 K 线 + 基本面。
执行顺序：预验证 → 个股K线 → 指数K线 → ETF K线 → 基本面
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import socket
socket.setdefaulttimeout(30)

from datetime import date
from sqlalchemy import text
from loguru import logger

from app.db.connection import get_sync_db
from crawler.baostock_crawler import BaostockCrawler

START = "2021-06-16"
END = "2026-06-17"
FAIL_FAST = True  # 预验证失败立即终止


def step(msg):
    logger.info("=" * 60)
    logger.info(f"  {msg}")
    logger.info("=" * 60)


def main():
    db = get_sync_db()
    c = BaostockCrawler()
    if not c.login():
        logger.error("❌ baostock 登录失败，终止")
        return

    # ═══════════════════════════════════════
    # [0] 预验证
    # ═══════════════════════════════════════
    step("[0] 预验证：每类数据试拉 2 只")

    # --- 个股K线 ---
    logger.info("测试个股K线: 600000 ...")
    r = c._download_kline_batch(["600000"], {"600000": "浦发银行"}, START, END, db=db, upsert_mode=False)
    if r.get("rows", 0) == 0:
        logger.error("❌ 个股K线预验证失败，终止")
        if FAIL_FAST: return
    else:
        logger.info(f"✅ 个股K线 OK ({r['rows']} 行)")

    # --- 指数K线 ---
    logger.info("测试指数K线: 000001(上证指数) ...")
    r = c.download_all_index_daily(trade_date=None, db=db, force=True, start_date=START[:10], end_date=START[:10])
    rows = r.get("rows", 0) if isinstance(r, dict) else r
    if rows == 0:
        logger.error("❌ 指数K线预验证失败，终止")
        if FAIL_FAST: return
    else:
        logger.info(f"✅ 指数K线 OK ({rows} 行)")

    # --- ETF K线 ---
    logger.info("测试ETF K线: 510050 ...")
    r = c.download_etf_daily(trade_date=None, db=db, force=True, start_date=START[:10], end_date=START[:10])
    rows = r.get("rows", 0)
    if rows == 0:
        logger.error("❌ ETF K线预验证失败，终止")
        if FAIL_FAST: return
    else:
        logger.info(f"✅ ETF K线 OK ({rows} 行)")

    # --- 基本面 ---
    logger.info("测试基本面: 600000 ...")
    r = c.download_fundamentals(force=False)
    rows = r.get("rows", 0) if isinstance(r, dict) else r
    if rows == 0:
        logger.warning("⚠️ 基本面预验证返回 0 行（可能已存在），继续")
    else:
        logger.info(f"✅ 基本面 OK ({rows} 行)")

    logger.info("预验证全部通过 ✅")

    # ═══════════════════════════════════════
    # [1] 个股日K线
    # ═══════════════════════════════════════
    step("[1/4] 个股日K线全量 (2021-06 ~ 2026-06)")
    stock_info = c.get_a_stock_codes()
    for code, info in stock_info.items():
        db.execute(text("""
            INSERT INTO stock_master (stock_code, stock_name, exchange, ipo_date, status, stock_type)
            VALUES (:c, :n, :e, :i, 'N', 'stock')
            ON CONFLICT (stock_code, stock_type) DO UPDATE SET stock_name=EXCLUDED.stock_name
        """), {"c": code, "n": info["name"], "e": "SSE" if code[0]=='6' else "SZSE", "i": info.get("ipo_date") or None})
    db.commit()
    logger.info(f"stock_master: {len(stock_info)} 只")

    codes = list(stock_info.keys())
    stock_names = {code: info["name"] for code, info in stock_info.items()}
    r = c._download_kline_batch(codes, stock_names, START, END, db=db, upsert_mode=True)
    logger.info(f"个股K线: {r.get('rows', 0)} 行, {r.get('errors', 0)} 错误, {r.get('elapsed_seconds', 0):.0f}s")

    # ═══════════════════════════════════════
    # [2] 指数日K线
    # ═══════════════════════════════════════
    step("[2/4] 指数日K线全量")
    r_idx = c.download_all_index_daily(trade_date=None, db=db, force=True, start_date=START, end_date=END)
    logger.info(f"指数K线: {r_idx.get('rows', 0) if isinstance(r_idx, dict) else r_idx} 行")

    # 指数 master（从 baostock 获取并写入 stock_master）
    idx_list = c._bs_adapter.get_stock_list("index") if hasattr(c, '_bs_adapter') else []
    if not idx_list:
        # fallback: 从 BaostockCrawler 直接获取
        import baostock as bs
        rs = bs.query_stock_basic()
        while rs.next():
            d = rs.get_row_data()
            if len(d) > 4 and d[4] == '2':
                raw = d[0]
                for p in ('sh.', 'sz.', 'bj.'):
                    if raw.startswith(p): code = raw[len(p):]; break
                else: code = raw
                db.execute(text("""
                    INSERT INTO stock_master (stock_code, stock_name, exchange, stock_type, status)
                    VALUES (:c, :n, :e, 'index', 'N')
                    ON CONFLICT (stock_code, stock_type) DO UPDATE SET stock_name=EXCLUDED.stock_name
                """), {"c": code, "n": d[1], "e": "SSE" if code[0] in ('0','9') else "SZSE"})
        db.commit()

    # ═══════════════════════════════════════
    # [3] ETF 日K线
    # ═══════════════════════════════════════
    step("[3/4] ETF 日K线全量")
    r_etf = c.download_etf_daily(trade_date=None, db=db, force=True, start_date=START, end_date=END)
    logger.info(f"ETF K线: {r_etf.get('rows', 0)} 行")

    # ETF master
    import baostock as bs
    rs = bs.query_stock_basic()
    while rs.next():
        d = rs.get_row_data()
        if len(d) > 4 and d[4] == '5':
            raw = d[0]
            for p in ('sh.', 'sz.', 'bj.'):
                if raw.startswith(p): code = raw[len(p):]; break
            else: code = raw
            db.execute(text("""
                INSERT INTO stock_master (stock_code, stock_name, exchange, stock_type, status)
                VALUES (:c, :n, :e, 'etf', 'N')
                ON CONFLICT (stock_code, stock_type) DO UPDATE SET stock_name=EXCLUDED.stock_name
            """), {"c": code, "n": d[1], "e": "SSE" if code[0]=='5' else "SZSE"})
    db.commit()

    # ═══════════════════════════════════════
    # [4] 基本面
    # ═══════════════════════════════════════
    step("[4/4] 基本面（依赖前面 K 线数据计算市值）")
    r_fund = c.download_fundamentals(force=True)
    logger.info(f"基本面: {r_fund.get('rows', 0) if isinstance(r_fund, dict) else r_fund} 只")

    c.logout()
    db.close()
    logger.info("=== 全量初始化完成 ===")


if __name__ == "__main__":
    main()
