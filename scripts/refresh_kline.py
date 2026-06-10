#!/usr/bin/env python3
"""
全量重刷历史日K线数据。

从 baostock 重新下载全部股票的历史日K线（后复权 adjustflag='3'），
覆盖 DB 中已有的数据。用于修复初始数据导入时的价格错误。

用法:
  # 生产服务器 Docker 中执行
  docker compose exec app python3 scripts/refresh_kline.py

  # 本地开发环境
  python3 scripts/refresh_kline.py --workers 4
"""
import sys, time, json, os
from datetime import date, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.connection import get_sync_db
from app.db.connection import is_sqlite as _is_sql
from sqlalchemy import text
from loguru import logger


def refresh_all(start_date="2021-01-01", end_date=None, workers=1):
    """全量重刷历史数据。逐个股票下载并 UPSERT。"""
    import baostock as bs

    if end_date is None:
        end_date = date.today().isoformat()

    db = get_sync_db()
    lg = bs.login()
    if lg.error_code != '0':
        logger.error(f"baostock 登录失败: {lg.error_msg}")
        return

    try:
        # 1. 获取全部 A 股列表
        rs = bs.query_stock_basic()
        codes = []
        while rs.next():
            d = rs.get_row_data()
            sec_type = d[4] if len(d) > 4 else ''
            if sec_type != '1':
                continue
            raw = d[0]
            for prefix in ('sh.', 'sz.', 'bj.'):
                if raw.startswith(prefix):
                    code = raw[len(prefix):]
                    break
            else:
                code = raw
            if code.isdigit() and len(code) == 6:
                codes.append((raw, code, d[1]))
        logger.info(f"待刷新: {len(codes)} 只股票")

        # 2. 逐只下载并 UPSERT
        total_updated = 0
        total_errors = 0
        fields = 'date,code,open,high,low,close,volume,amount,turn'

        for i, (bs_code, stock_code, stock_name) in enumerate(codes):
            if i > 0 and i % 100 == 0:
                logger.info(f"  进度: {i}/{len(codes)}, 已更新 {total_updated} 行")

            # 判断交易所
            ex = 'SSE' if bs_code.startswith('sh.') else 'SZSE' if bs_code.startswith('sz.') else 'BSE'

            try:
                rs2 = bs.query_history_k_data_plus(
                    bs_code, fields,
                    start_date=start_date, end_date=end_date,
                    frequency='d', adjustflag='3')

                batch = []
                while rs2.next():
                    d = rs2.get_row_data()
                    if not d[0]:  # 空日期跳过
                        continue
                    try:
                        batch.append((
                            d[0], ex, stock_code, stock_name,
                            float(d[2]) if d[2] else 0,
                            float(d[3]) if d[3] else 0,
                            float(d[4]) if d[4] else 0,
                            float(d[5]) if d[5] else 0,
                            int(float(d[6])) if d[6] else 0,
                            float(d[7]) if d[7] else 0,
                            float(d[8]) if d[8] else None,
                        ))
                    except (ValueError, IndexError):
                        continue

                if not batch:
                    continue

                # 批量 UPSERT
                chunk_size = 200
                for start in range(0, len(batch), chunk_size):
                    chunk = batch[start:start + chunk_size]
                    placeholders = []
                    params = {}
                    for j, row in enumerate(chunk):
                        idx = start + j
                        placeholders.append(
                            f"(:td{idx},:ex{idx},:sc{idx},:sn{idx},"
                            f":o{idx},:h{idx},:l{idx},:c{idx},:ch{idx},:cq{idx},"
                            f":v{idx},:a{idx},:t{idx})")
                        params.update({
                            f'td{idx}': row[0], f'ex{idx}': row[1],
                            f'sc{idx}': row[2], f'sn{idx}': row[3],
                            f'o{idx}': row[4], f'h{idx}': row[5],
                            f'l{idx}': row[6], f'c{idx}': row[7],
                            f'ch{idx}': row[7],  # close_hfq = 后复权 close
                            f'cq{idx}': row[7],  # close_qfq = 后复权 close
                            f'v{idx}': row[8], f'a{idx}': row[9],
                            f't{idx}': row[10],
                        })

                    sql = (
                        "INSERT INTO daily_quote "
                        "(trade_date,exchange,stock_code,stock_name,"
                        "open,high,low,close,close_hfq,close_qfq,"
                        "volume,amount,turnover) VALUES " +
                        ",".join(placeholders))
                    if _is_sql():
                        sql = sql.replace("INSERT INTO", "INSERT OR REPLACE INTO")
                    else:
                        sql += " ON CONFLICT (stock_code, exchange, trade_date) DO UPDATE SET " \
                               "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, " \
                               "close=EXCLUDED.close, close_hfq=EXCLUDED.close_hfq, " \
                               "close_qfq=EXCLUDED.close_qfq, " \
                               "volume=EXCLUDED.volume, amount=EXCLUDED.amount, " \
                               "turnover=EXCLUDED.turnover"

                    try:
                        db.execute(text(sql), params)
                        total_updated += len(chunk)
                    except Exception as e:
                        logger.warning(f"  {stock_code} 批量插入失败: {e}")

                time.sleep(0.15)  # baostock 限流
                db.commit()

            except Exception as e:
                logger.warning(f"  {stock_code} 下载失败: {e}")
                total_errors += 1

        elapsed = time.time() - time_start
        logger.info(f"完成! 更新 {total_updated} 行, 错误 {total_errors}, 耗时 {elapsed:.0f}s")

    finally:
        bs.logout()
        db.close()


if __name__ == '__main__':
    time_start = time.time()
    refresh_all(start_date="2021-01-01")
