#!/usr/bin/env python3
"""独立K线断点续传脚本 —— 批量插入 + 重试 + 请求间隔 + 退市股跳过。"""
import sys
import time
import random
sys.path.insert(0, '/Users/yeqisong/Desktop/项目/stone')
import baostock as bs
from app.db.connection import get_sync_db
from sqlalchemy import text

# ---------- 可调参数 ----------
DELAY_MIN = 0.2          # 每次请求最短间隔（秒）
DELAY_MAX = 0.5          # 每次请求最长间隔（秒）
RETRY_MAX = 4            # 单只股票最大重试次数
RETRY_BACKOFF = 3.0      # 重试退避基础秒数
BATCH_COMMIT = 30        # 每 N 只股票提交一次事务
CUTOFF_DATE = '2021-06-08'
# ------------------------------

def random_delay():
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))


def baostock_login():
    for attempt in range(5):
        lg = bs.login()
        if lg.error_code == '0':
            print(f'baostock 登录成功 (attempt {attempt+1})', flush=True)
            return True
        print(f'  登录失败 (attempt {attempt+1}): {lg.error_msg}', flush=True)
        time.sleep(5)
    return False


def fetch_kline(bs_code, start_date, end_date):
    """下载单只股票K线，返回 (rows_or_none, should_relogin)。"""
    for attempt in range(RETRY_MAX + 1):
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                'date,open,high,low,close,volume,amount,turn',
                start_date=start_date, end_date=end_date,
                frequency='d', adjustflag='3')

            if rs.error_code == '0':
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
                return (rows, False)

            msg = rs.error_msg
            if '未登录' in msg or 'login' in msg.lower():
                print(f'  {bs_code} 会话失效: {msg}', flush=True)
                return (None, True)

            if attempt < RETRY_MAX:
                wait = RETRY_BACKOFF ** (attempt + 1)
                print(f'  {bs_code} API错误(attempt {attempt+1}): {msg}，{wait:.0f}s后重试...', flush=True)
                time.sleep(wait)
                continue
            return (None, False)

        except Exception as e:
            msg = str(e)
            if '未登录' in msg or 'login' in msg.lower():
                print(f'  {bs_code} 会话失效异常: {e}', flush=True)
                return (None, True)

            if attempt < RETRY_MAX:
                wait = RETRY_BACKOFF ** (attempt + 1)
                print(f'  {bs_code} 网络异常(attempt {attempt+1}): {e}，{wait:.0f}s后重试...', flush=True)
                time.sleep(wait)
                continue
            return (None, False)

    return (None, False)


def batch_insert(db, batch):
    """批量插入 daily_quote 行。batch: [(td,ex,sc,sn,o,h,l,c,v,a,t), ...]"""
    if not batch:
        return 0
    # SQLite 支持最多 500 行 VALUES 参数，大了反而慢，分批插
    chunk_size = 200
    total = 0
    for start in range(0, len(batch), chunk_size):
        chunk = batch[start:start + chunk_size]
        placeholders = []
        params = {}
        for j, row in enumerate(chunk):
            i = start + j
            placeholders.append(
                f"(:td{i},:ex{i},:sc{i},:sn{i},:o{i},:h{i},:l{i},:c{i},:ch{i},:cq{i},:v{i},:a{i},:t{i})")
            params.update({
                f'td{i}': row[0], f'ex{i}': row[1], f'sc{i}': row[2], f'sn{i}': row[3],
                f'o{i}': row[4], f'h{i}': row[5], f'l{i}': row[6], f'c{i}': row[7],
                f'ch{i}': row[7], f'cq{i}': row[7],  # resume 脚本暂用不复权
                f'v{i}': row[8], f'a{i}': row[9], f't{i}': row[10],
            })
        sql = ("INSERT INTO daily_quote "
               "(trade_date,exchange,stock_code,stock_name,open,high,low,close,close_hfq,close_qfq,volume,amount,turnover) "
               "VALUES " + ",".join(placeholders) +
               " ON CONFLICT (stock_code, exchange, trade_date) DO NOTHING")
        try:
            db.execute(text(sql), params)
            total += len(chunk)
        except Exception as e:
            print(f'  批量插入异常: {e}', flush=True)
    return total


def main(start_date='2021-06-08', end_date='2026-06-07'):
    db = get_sync_db()

    if not baostock_login():
        print('FATAL: 无法登录 baostock，退出', flush=True)
        return

    # --- 获取 A 股列表，记录退市日期 ---
    rs = bs.query_stock_basic()
    stock_info = {}
    skipped = 0
    while rs.next():
        row = rs.get_row_data()
        sec_type = row[4] if len(row) > 4 else ''
        if sec_type != '1':
            continue
        raw_code = row[0]
        for prefix in ('sh.', 'sz.', 'bj.'):
            if raw_code.startswith(prefix):
                code = raw_code[len(prefix):]
                break
        else:
            code = raw_code
        if not (code.isdigit() and len(code) == 6):
            continue
        out_date = row[3] if row[3] else ''
        if out_date and out_date < start_date:
            skipped += 1
            continue
        stock_info[code] = {'name': row[1], 'out_date': out_date}

    print(f'全量A股: {len(stock_info) + skipped} (跳过{start_date}前退市{skipped}只)', flush=True)

    # --- 查询已下载 ---
    existing = {r[0] for r in db.execute(text('SELECT DISTINCT stock_code FROM daily_quote')).fetchall()}
    remaining = [c for c in stock_info if c not in existing]
    print(f'已下载: {len(existing)}, 待下载: {len(remaining)}', flush=True)

    if not remaining:
        print('所有股票已下载完成。', flush=True)
        db.close()
        bs.logout()
        return
    total_rows = new_stocks = new_empty = errors = 0
    batch_buffer = []  # 批量插入缓冲区

    start_time = time.time()

    i = 0
    while i < len(remaining):
        code = remaining[i]

        if code.startswith('6'):
            bs_code, ex = 'sh.' + code, 'SSE'
        elif code[0] in ('0', '3'):
            bs_code, ex = 'sz.' + code, 'SZSE'
        else:
            bs_code, ex = 'bj.' + code, 'BSE'

        random_delay()

        rows, need_relogin = fetch_kline(bs_code, start_date, end_date)

        if need_relogin:
            print(f'  会话失效，重新登录...', flush=True)
            bs.logout()
            time.sleep(3)
            if baostock_login():
                continue
            else:
                print('  重新登录失败，跳过', flush=True)
                errors += 1
                i += 1
                continue

        if rows is None:
            errors += 1
            i += 1
            continue

        # 构造批量插入数据
        name = stock_info[code]['name']
        for row in rows:
            try:
                batch_buffer.append((
                    row[0], ex, code, name,
                    float(row[1]), float(row[2]), float(row[3]), float(row[4]),
                    int(float(row[5])), float(row[6]),
                    float(row[7]) if row[7] else None,
                ))
            except (ValueError, IndexError):
                pass

        row_count = len(rows)
        total_rows += row_count
        if rows:
            new_stocks += 1
        else:
            new_empty += 1

        i += 1

        # 进度输出（每只股票都输出一行，简洁版）
        elapsed = time.time() - start_time
        rate = i / elapsed if elapsed > 0 else 0
        eta = (len(remaining) - i) / rate if rate > 0 else 0
        print(f'[{i}/{len(remaining)}] {code} {name} +{row_count}行 | '
              f'{rate:.1f}只/分钟 | 预计剩余{eta/60:.0f}分钟', flush=True)

        # 定期提交
        if i % BATCH_COMMIT == 0:
            n = batch_insert(db, batch_buffer)
            db.commit()
            batch_buffer.clear()
            elapsed = time.time() - start_time
            print(f'--- 提交: {i}只 | {total_rows:,}行累计 | {elapsed/60:.1f}分钟 ---', flush=True)

    # 最终提交
    if batch_buffer:
        batch_insert(db, batch_buffer)
    db.commit()

    r = db.execute(text('SELECT COUNT(DISTINCT stock_code) FROM daily_quote')).scalar()
    r2 = db.execute(text('SELECT COUNT(*) FROM daily_quote')).scalar()
    elapsed = time.time() - start_time
    print(f'\n===== 下载完成 ({elapsed/60:.1f}分钟) =====', flush=True)
    print(f'本次: {new_stocks}只有数据 + {new_empty}空 + {errors}错误', flush=True)
    print(f'新增: {total_rows:,}行', flush=True)
    print(f'数据库总计: {r}只 / {r2:,}行', flush=True)

    db.close()
    bs.logout()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='K线断点续传')
    parser.add_argument('--start', default='2021-06-08', help='起始日期')
    parser.add_argument('--end', default=None, help='结束日期(默认今天)')
    args = parser.parse_args()
    from datetime import date
    end = args.end or date.today().isoformat()
    main(args.start, end)
