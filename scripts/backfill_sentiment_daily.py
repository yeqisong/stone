#!/usr/bin/env python
"""市场情绪分历史回补（daily_quote 重算 → market_sentiment_daily，2000 起）。

实时情绪分（app/market_sentiment.py）只有当下值且依赖盘中快照，无法回补；
本脚本用与 _verdict 完全相同的成分从 daily_quote 重算历史序列：
- 上涨占比 / 中位数涨跌幅 / 涨跌停平衡 → 三成分评分
- VWAP 项（盘中实时概念）按 _verdict 缺省逻辑计 10 分——判级与评分直接复用
  _verdict(above_count=0, index_count=0)，公式单点维护，历史与增量可比

口径说明：
- 涨跌幅用 close_hfq 对 LAG 前收：除权日是"真实收益"（含分红），与腾讯
  快照的官方前收口径在除权日有细微差异，对中位数/涨跌停计数影响可忽略
- 涨跌停阈值按代码段近似（300/301/688/689 → 19.8%，其余 9.8%），与实时
  _limit_pct 一致；ST 5% 与 10cm/20cm 混杂属已知近似（design/11）
- 窗口分块时每块带上前一交易日供 LAG 边界（首日才有前收可比）

用法：
    venv/bin/python scripts/backfill_sentiment_daily.py                  # 2000-01-01 起
    venv/bin/python scripts/backfill_sentiment_daily.py --start 2015-01-01

纯库内重算，不消耗 tushare 配额。幂等 upsert，可重复执行。
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger
from sqlalchemy import bindparam, text

UPSERT = text("""
INSERT INTO market_sentiment_daily
    (trade_date, score, level, up_ratio, median, limit_up, limit_down,
     up, down, flat, total, score_rt, vwap_ratio)
VALUES (:d, :s, :lv, :ur, :md, :lu, :ld, :u, :dn, :fl, :tt, NULL, NULL)
ON CONFLICT (trade_date) DO UPDATE SET
    score=EXCLUDED.score, level=EXCLUDED.level, up_ratio=EXCLUDED.up_ratio,
    median=EXCLUDED.median, limit_up=EXCLUDED.limit_up, limit_down=EXCLUDED.limit_down,
    up=EXCLUDED.up, down=EXCLUDED.down, flat=EXCLUDED.flat, total=EXCLUDED.total
""")

CHUNK_SQL = text("""
    WITH q AS (
        SELECT d.trade_date, d.stock_code, d.close_hfq,
               LAG(d.close_hfq) OVER (PARTITION BY d.stock_code ORDER BY d.trade_date) AS prev,
               CASE WHEN substring(d.stock_code, 1, 3) IN ('300', '301', '688', '689')
                    THEN 19.8 ELSE 9.8 END AS lim
        FROM daily_quote d
        WHERE d.trade_date IN :dates
          AND d.close_hfq > 0
          AND d.stock_code IN (SELECT stock_code FROM stock_master WHERE stock_type='stock')
    )
    SELECT trade_date,
        COUNT(*) FILTER (WHERE close_hfq > prev) AS up,
        COUNT(*) FILTER (WHERE close_hfq < prev) AS down,
        COUNT(*) FILTER (WHERE close_hfq = prev) AS flat,
        COUNT(*) FILTER (WHERE close_hfq / prev - 1 >= lim / 100) AS limit_up,
        COUNT(*) FILTER (WHERE close_hfq / prev - 1 <= -lim / 100) AS limit_down,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY (close_hfq / prev - 1) * 100) AS median
    FROM q WHERE prev IS NOT NULL AND prev > 0
    GROUP BY trade_date ORDER BY trade_date
""").bindparams(bindparam('dates', expanding=True))


def fv(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def main():
    ap = argparse.ArgumentParser(description='市场情绪分历史回补')
    ap.add_argument('--start', default='2000-01-01', help='回补起始日（默认 2000-01-01，daily_quote 覆盖起点）')
    ap.add_argument('--end', default=None, help='回补结束日（默认今天）')
    ap.add_argument('--chunk', type=int, default=250, help='每块交易日数（默认 250）')
    args = ap.parse_args()

    from app.db.connection import get_sync_db
    from app.market_sentiment import _verdict

    start_d = date.fromisoformat(args.start)
    end_d = date.fromisoformat(args.end) if args.end else date.today()
    db = get_sync_db()
    total = 0
    try:
        # 全量交易日清单（含 --start 前一日作首块 LAG 边界）
        dates = [r[0] for r in db.execute(text("""
            SELECT DISTINCT trade_date FROM daily_quote
            WHERE trade_date >= :s - interval '10 days' AND trade_date <= :e
            ORDER BY trade_date
        """), {'s': start_d, 'e': end_d}).fetchall()]
        # 只处理 >= start 的块；边界日仅参与 LAG 不落库
        target = [d for d in dates if d >= start_d]
        logger.info(f'交易日 {len(target)} 天（{start_d} → {end_d}），分 {len(target) // args.chunk + 1} 块')
        for i in range(0, len(target), args.chunk):
            block = target[i:i + args.chunk]
            # LAG 边界：块首日的前一个交易日（不在 target 里也要带上）
            boundary = [d for d in dates if d < block[0]][-1:] if any(d < block[0] for d in dates) else []
            rows = db.execute(CHUNK_SQL, {'dates': boundary + block}).fetchall()
            params = []
            for r in rows:
                tot = int(r.up) + int(r.down) + int(r.flat)
                if not tot:
                    continue
                up_ratio = round(int(r.up) / tot, 6)
                level, score, _ = _verdict(up_ratio, fv(r.median),
                                           int(r.limit_up), int(r.limit_down), 0, 0)
                params.append({'d': r.trade_date, 's': score, 'lv': level,
                               'ur': up_ratio, 'md': fv(r.median),
                               'lu': int(r.limit_up), 'ld': int(r.limit_down),
                               'u': int(r.up), 'dn': int(r.down), 'fl': int(r.flat), 'tt': tot})
            if params:
                db.execute(UPSERT, params)  # executemany 批量
                db.commit()
            total += len(params)
            logger.info(f'  {block[0]} → {block[-1]}: {len(params)} 行（累计 {total}）')
    finally:
        db.close()

    logger.info(f'回补完成：{total} 行')
    return 0


if __name__ == '__main__':
    sys.exit(main())
