#!/usr/bin/env python
"""修复历史零价行：停牌日被写成 close=0（应"无行"，或至少沿用上一收盘）。

背景（2026-09-11 定位）：2026 年 7 月有 1055 行停牌股被写成 close=0/close_hfq=0，
这些行经 feature_values → 宽表进入回测，使持仓在停牌日被按 0 计价，净值出现
-34% / 次日 +57% 的成对伪跳变，把 ACTIVE 模型 v15.0 的 max_dd 从真实水平撑到 47%。
写入端已加护栏（crawler/writers.py 丢弃非正收盘价），本脚本处理**存量**。

做法：把零价行的 OHLC 全部置为"该股上一次有效收盘"（后复权同理），保留
is_suspended=true 标记。幂等：重复执行不会再有 close<=0 的行。

用法：
    venv/bin/python scripts/repair_zero_prices.py           # 只体检，不改
    venv/bin/python scripts/repair_zero_prices.py --apply   # 执行修复
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from app.db.connection import get_sync_db

# 个股/ETF：carry-forward 上一有效 close 与 close_hfq
_SQL_DAILY = """
WITH bad AS (
    SELECT dq.id,
           (SELECT p.close FROM daily_quote p
             WHERE p.stock_code = dq.stock_code AND p.trade_date < dq.trade_date AND p.close > 0
             ORDER BY p.trade_date DESC LIMIT 1) AS pc,
           (SELECT p.close_hfq FROM daily_quote p
             WHERE p.stock_code = dq.stock_code AND p.trade_date < dq.trade_date AND p.close_hfq > 0
             ORDER BY p.trade_date DESC LIMIT 1) AS ph
      FROM daily_quote dq
     WHERE dq.close <= 0
)
UPDATE daily_quote q
   SET close = b.pc, close_qfq = b.pc, close_hfq = COALESCE(b.ph, b.pc),
       open = b.pc, high = b.pc, low = b.pc, is_suspended = true
  FROM bad b
 WHERE q.id = b.id AND b.pc IS NOT NULL
"""

# 指数表无 close_hfq 列；且 PK 为 (trade_date, index_code)，逐行相关子查询会退化成
# 千万级全表扫描 × 3.4 万次 → 改用一次窗口扫描定位"该指数的上一有效收盘日"再回连
_SQL_INDEX = """
WITH s AS (
    SELECT trade_date, index_code,
           max(trade_date) FILTER (WHERE close > 0) OVER w AS last_ok_date
      FROM index_daily_quote
    WINDOW w AS (PARTITION BY index_code ORDER BY trade_date
                 ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
)
UPDATE index_daily_quote q
   SET close = p.close, open = p.close, high = p.close, low = p.close, is_suspended = true
  FROM s JOIN index_daily_quote p
    ON p.trade_date = s.last_ok_date AND p.index_code = s.index_code
 WHERE q.trade_date = s.trade_date AND q.index_code = s.index_code
   AND q.close <= 0 AND s.last_ok_date IS NOT NULL
"""


def _count(db, sql):
    return db.execute(text(sql)).scalar() or 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='真正执行修复（默认只体检）')
    args = ap.parse_args()

    db = get_sync_db()
    try:
        for name, table, sql in (('daily_quote', 'daily_quote', _SQL_DAILY),
                                 ('index_daily_quote', 'index_daily_quote', _SQL_INDEX)):
            before = _count(db, f"SELECT count(*) FROM {table} WHERE close <= 0")
            if not before:
                print(f'[{name}] 无零价行，跳过')
                continue
            # 无法 carry-forward 的（该股没有更早的有效收盘）单独列出
            orphan = _count(db, f"SELECT count(*) FROM {table} q WHERE q.close <= 0 AND NOT EXISTS ("
                                f"SELECT 1 FROM {table} p WHERE p.{'stock_code' if table == 'daily_quote' else 'index_code'}"
                                f" = q.{'stock_code' if table == 'daily_quote' else 'index_code'}"
                                f" AND p.trade_date < q.trade_date AND p.close > 0)")
            print(f'[{name}] 零价行 {before} 条（其中 {orphan} 条无更早有效收盘，无法修复）')
            if args.apply:
                res = db.execute(text(sql))
                db.commit()
                after = _count(db, f"SELECT count(*) FROM {table} WHERE close <= 0")
                print(f'[{name}] 已修复 {res.rowcount} 行，剩余零价行 {after} 条')
                # 血缘台账（旁路落账，失败不影响修复结果）
                try:
                    from app.lineage import log_event
                    log_event(db, 'zero_price_repair', f'{table}.close',
                              scope=f'全表 carry-forward 修复', detail={'rows': res.rowcount})
                except Exception:
                    pass
            else:
                print(f'[{name}] 试运行模式，未改动（加 --apply 执行）')
    finally:
        db.close()


if __name__ == '__main__':
    main()
