"""
统一数据采集器 — baostock 单一数据源（生产级）。

baostock 数据更新时间表 (依据官方文档):
  17:30  日K线数据入库
  18:00  复权因子数据入库
  20:00  分钟K线数据入库

系统调度对齐:
  17:35  cron 触发数据采集 (日K已就绪)
  17:35  策略计算 (使用复权数据)
"""
import time
import random
import signal
from contextlib import contextmanager
import baostock as bs
import pandas as pd
from datetime import date, timedelta
from typing import Optional, List, Set, Dict
from sqlalchemy import text
from loguru import logger

from app.db.connection import get_sync_db
from app.config import settings
from crawler.progress import (
    load_progress, save_progress, mark_batch_completed,
    get_db_completed_stocks, init_progress, get_progress_summary,
)

# ── 常量 ──
DELAY_MIN = 0.3          # 请求最小间隔（秒）
DELAY_MAX = 0.7          # 请求最大间隔（秒）
RETRY_MAX = 4            # 单只股票最大重试次数
RETRY_BACKOFF = 3.0      # 指数退避基础秒
BATCH_COMMIT = 200       # 每日增量批量提交股数
CUTOFF_DATE = settings.CUTOFF_DATE  # 数据起始日期


class TimeoutError(Exception):
    """API 调用超时。"""
    pass


@contextmanager
def _with_timeout(seconds: int):
    """上下文管理器：在指定秒数后触发 SIGALRM 超时。"""
    def _handler(signum, frame):
        raise TimeoutError(f"操作超时 ({seconds}s)")
    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _random_delay():
    """随机延时，避免触发 baostock 限流。"""
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))


class BaostockCrawler:
    """baostock 统一数据采集器（生产级）。"""

    def __init__(self):
        self._logged_in = False

    # ── 登录/登出 ──

    def login(self) -> bool:
        """登录 baostock，带重试。返回是否成功。"""
        import socket
        if socket.getdefaulttimeout() is None:
            socket.setdefaulttimeout(30)  # 防止 baostock 连接 hang 住
        if self._logged_in:
            return True
        for attempt in range(5):
            lg = bs.login()
            if lg.error_code == '0':
                self._logged_in = True
                return True
            logger.warning(f"baostock 登录失败 (attempt {attempt+1}): {lg.error_msg}")
            time.sleep(5)
        logger.error("baostock 登录彻底失败")
        return False

    def logout(self):
        if self._logged_in:
            bs.logout()
            self._logged_in = False

    # ── 代码转换 ──

    @staticmethod
    def _bs_code(code: str) -> str:
        """A股代码 → baostock 代码格式。"""
        if code.startswith('6'):
            return f"sh.{code}"
        elif code[0] in ('0', '3'):
            return f"sz.{code}"
        else:
            return f"sz.{code}"

    @staticmethod
    def _exchange(code: str) -> str:
        if code.startswith('6'):
            return "SSE"
        if code.startswith(('4', '8', '9')):
            return "BSE"
        return "SZSE"

    # ── 获取A股列表（仅 type=1） ──

    def get_a_stock_codes(self) -> Dict[str, dict]:
        """获取全量A股代码及基础信息（仅 type=1）。"""
        if not self._logged_in:
            self.login()
        rs = bs.query_stock_basic()
        if rs.error_code != '0':
            logger.error(f"query_stock_basic 失败: {rs.error_msg}")
            return {}
        stock_info = {}
        skipped_non_stock = 0
        skipped_delisted = 0
        while rs.next():
            row = rs.get_row_data()
            sec_type = row[4] if len(row) > 4 else ''
            if sec_type != '1':
                skipped_non_stock += 1
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
            out_date = row[3] if len(row) > 3 and row[3] else ''
            if out_date and out_date < CUTOFF_DATE:
                skipped_delisted += 1
                continue
            stock_info[code] = {
                'name': row[1],
                'ipo_date': row[2] if len(row) > 2 and row[2] else '',
                'out_date': out_date,
                'status': row[5] if len(row) > 5 else '1',
            }
        logger.info(f"A股列表: {len(stock_info)} 只 (跳过非A股{skipped_non_stock}, "
                     f"{CUTOFF_DATE}前退市{skipped_delisted})")
        return stock_info

    def get_all_stock_codes(self) -> List[str]:
        return list(self.get_a_stock_codes().keys())

    # ── 带重试的K线下载 ──

    def _fetch_kline(self, bs_code: str, start_date: str, end_date: str) -> tuple:
        """下载 K 线数据，返回 (rows, need_relogin)。

        每只股票调用两次 API：
        - adjustflag="3"（不复权）→ OHLCV + turnover
        - adjustflag="1"（后复权）→ close_hfq
        合并后每行: [date, open, high, low, close, volume, amount, turn, close_hfq]
        """
        for attempt in range(RETRY_MAX + 1):
            try:
                # 第1次：不复权 OHLCV
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,open,high,low,close,volume,amount,turn",
                    start_date=start_date, end_date=end_date,
                    frequency="d", adjustflag="3")
                if rs.error_code == '0':
                    raw_rows = []
                    while rs.next():
                        row = rs.get_row_data()
                        if len(row) >= 8 and row[0]:  # 过滤空行/残缺行
                            raw_rows.append(row)
                    # 第2次：后复权 close
                    rs_hfq = bs.query_history_k_data_plus(
                        bs_code, "date,close",
                        start_date=start_date, end_date=end_date,
                        frequency="d", adjustflag="1")
                    hfq_map = {}
                    if rs_hfq.error_code == '0':
                        while rs_hfq.next():
                            d = rs_hfq.get_row_data()
                            if len(d) >= 2 and d[0] and d[1]:  # 过滤残缺行
                                hfq_map[d[0]] = d[1]  # date → 后复权close
                    # 合并：每行追加 close_hfq
                    rows = []
                    for r in raw_rows:
                        close_hfq = hfq_map.get(r[0], r[4])  # 无后复权则用不复权
                        rows.append(r + [close_hfq])
                    return (rows, False)
                msg = rs.error_msg
                if '未登录' in msg or 'login' in msg.lower():
                    logger.warning(f"  {bs_code} 会话失效")
                    return (None, True)
                if attempt < RETRY_MAX:
                    wait = RETRY_BACKOFF ** (attempt + 1)
                    logger.debug(f"  {bs_code} API错误(attempt {attempt+1}): {msg}")
                    time.sleep(wait)
                    continue
                logger.warning(f"  {bs_code} API最终失败: {msg}")
                return (None, False)
            except Exception as e:
                msg = str(e)
                if '未登录' in msg or 'login' in msg.lower():
                    logger.warning(f"  {bs_code} 会话失效: {e}")
                    return (None, True)
                # 超时 = 会话退化/连接挂起，不重试（重试也会超时）
                if 'timed out' in msg.lower() or 'timeout' in msg.lower():
                    logger.debug(f"  {bs_code} 连接超时，跳过")
                    return ([], False)
                if attempt < RETRY_MAX:
                    wait = RETRY_BACKOFF ** (attempt + 1)
                    logger.debug(f"  {bs_code} 网络异常(attempt {attempt+1}): {e}")
                    time.sleep(wait)
                    continue
                logger.warning(f"  {bs_code} 最终网络失败: {e}")
                return (None, False)
        return (None, False)

    # ── 批量插入 ──

    @staticmethod
    def _batch_insert_rows(db, rows_batch: list, upsert: bool = True) -> int:
        if not rows_batch:
            return 0
        chunk_size = 200
        total = 0
        for start in range(0, len(rows_batch), chunk_size):
            chunk = rows_batch[start:start + chunk_size]
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
                    f'ch{idx}': row[8], f'cq{idx}': row[7],  # hfq=后复权, qfq=暂用不复权
                    f'v{idx}': row[9], f'a{idx}': row[10],
                    f't{idx}': row[11],
                })
            sql = (
                "INSERT INTO daily_quote "
                "(trade_date,exchange,stock_code,stock_name,"
                "open,high,low,close,close_hfq,close_qfq,"
                "volume,amount,turnover,is_suspended) "
                "VALUES " + ",".join(placeholders)[:-1] + ",false)")
            if upsert:
                sql += (" ON CONFLICT (stock_code, exchange, trade_date) DO UPDATE SET "
                       "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
                       "close=EXCLUDED.close, "
                       "close_hfq=CASE WHEN EXCLUDED.close_hfq IS NULL OR EXCLUDED.close_hfq = 0 THEN daily_quote.close_hfq ELSE EXCLUDED.close_hfq END, "
                       "close_qfq=EXCLUDED.close_qfq, volume=EXCLUDED.volume, "
                       "amount=EXCLUDED.amount, turnover=EXCLUDED.turnover, "
                       "is_suspended=false")
            else:
                sql += " ON CONFLICT (stock_code, exchange, trade_date) DO NOTHING"
            try:
                db.execute(text(sql), params)
                total += len(chunk)
            except Exception as e:
                logger.error(f"批量插入异常: {e}")
        return total

    # ── 统一下载核心 ──

    def _download_kline_batch(self, codes: list, stock_names: dict,
                                start_date: str, end_date: str,
                                db, upsert_mode: bool,
                                date_str_filter: str = None) -> dict:
        """串行下载 K 线数据（baostock 非线程安全，禁止并发调用）。

        重要：baostock 内部使用单一 TCP 连接，多线程并发会导致数据串扰
        （如 600104 返回 600105 的数据）。必须逐只串行调用。

        策略：
        - 逐只股票串行调用 baostock API
        - 每 200 只主动刷新会话（防止会话退化）
        - 连续 20 只空结果时提前刷新会话
        - 超时直接跳过（不重试退化的会话）
        - 批量写入 DB（每 500 行一次 commit）
        """
        SESSION_REFRESH = 200  # 每 N 只股票刷新一次会话
        REFRESH_PAUSE = 2      # 刷新间隔秒数

        total_rows = 0; errors = 0; failed_codes = []
        processed = 0; insert_buffer = []
        empty_streak = 0  # 连续空结果计数
        start_time = time.time()

        for i, code in enumerate(codes):
            # ── 定期刷新会话（防退化）──
            if i > 0 and i % SESSION_REFRESH == 0:
                if insert_buffer:
                    self._batch_insert_rows(db, insert_buffer, upsert=upsert_mode)
                    db.commit(); insert_buffer.clear()
                self.logout(); time.sleep(REFRESH_PAUSE)
                if not self.login():
                    logger.error(f"  会话刷新失败 (第 {i} 只)，终止")
                    break
                empty_streak = 0
                logger.info(f"  进度: {processed}/{len(codes)} | +{total_rows}行 | "
                           f"错误{errors} | {time.time()-start_time:.0f}s")

            # ── 连续空结果过多 = 会话退化，提前刷新 ──
            if empty_streak >= 20:
                logger.warning(f"  连续 {empty_streak} 只空结果，提前刷新会话")
                if insert_buffer:
                    self._batch_insert_rows(db, insert_buffer, upsert=upsert_mode)
                    db.commit(); insert_buffer.clear()
                self.logout(); time.sleep(REFRESH_PAUSE)
                if not self.login():
                    logger.error("  会话刷新失败，终止")
                    break
                empty_streak = 0

            # ── 串行拉取单只股票 ──
            bs_code = self._bs_code(code)
            _random_delay()
            try:
                rows, need_relogin = self._fetch_kline(bs_code, start_date, end_date)
            except Exception as e:
                errors += 1; failed_codes.append(code)
                processed += 1
                continue

            if need_relogin:
                self.logout(); time.sleep(REFRESH_PAUSE)
                if self.login():
                    try:
                        rows, need_relogin = self._fetch_kline(bs_code, start_date, end_date)
                    except Exception:
                        rows = None
                if rows is None or need_relogin:
                    errors += 1; failed_codes.append(code)
                    processed += 1
                    continue

            if rows is None:
                errors += 1; failed_codes.append(code)
                processed += 1
                continue

            if len(rows) == 0:
                empty_streak += 1
                processed += 1
                continue

            # ── 有数据，重置空结果计数，写入缓冲 ──
            empty_streak = 0
            ex = self._exchange(code)
            name = stock_names.get(code, code)
            for row in rows:
                if date_str_filter and row[0] != date_str_filter:
                    continue
                try:
                    close_hfq = float(row[8]) if len(row) > 8 and row[8] else float(row[4])
                    insert_buffer.append((
                        row[0], ex, code, name,
                        float(row[1]), float(row[2]), float(row[3]), float(row[4]),
                        close_hfq,
                        int(float(row[5])), float(row[6]),
                        float(row[7]) if row[7] else None,
                    ))
                    total_rows += 1
                except (ValueError, IndexError):
                    pass
            processed += 1

            # ── 批量写入 DB ──
            if len(insert_buffer) >= 500:
                self._batch_insert_rows(db, insert_buffer, upsert=upsert_mode)
                db.commit(); insert_buffer.clear()

        # 写入剩余
        if insert_buffer:
            self._batch_insert_rows(db, insert_buffer, upsert=upsert_mode)
            db.commit()

        elapsed = time.time() - start_time
        logger.info(f"  完成: {total_rows}行 | {processed}/{len(codes)}只 | "
                   f"错误{errors} | {elapsed:.0f}秒")
        return {"rows": total_rows, "stocks": processed, "errors": errors,
                "failed_codes": failed_codes, "elapsed_seconds": elapsed}

    # ═══════════════════════════════════════════════════
    #  每日增量更新（A股日K线）
    # ═══════════════════════════════════════════════════

    def download_daily_update(self, trade_date: date = None, db=None, force: bool = False) -> dict:
        if trade_date is None:
            trade_date = date.today()
        date_str = trade_date.isoformat()
        if not self.login():
            return {"rows": 0, "fatal": "login_failed"}
        if db is None:
            db = get_sync_db()
        start_time = time.time()
        logger.info(f"=== 每日增量更新 {date_str} ===")

        a_stock_set = set(self.get_a_stock_codes().keys())
        logger.info(f"  A股总数: {len(a_stock_set)} 只")
        try:
            rs = bs.query_all_stock(date_str)
            if rs.error_code != '0':
                return {"rows":0,"fatal":"no_stock_list"}
            all_codes = []; stock_names = {}
            while rs.next():
                row = rs.get_row_data()
                if not row or len(row) < 3: continue
                raw = row[0]
                for p in ('sh.', 'sz.', 'bj.'):
                    if raw.startswith(p):
                        code = raw[len(p):]; break
                else:
                    code = raw
                if code in a_stock_set and row[1] == '1':
                    all_codes.append(code)
                    stock_names[code] = row[2]
        except Exception as e:
            return {"rows":0,"fatal":"query_all_stock_error"}

        logger.info(f"  {date_str} A股交易: {len(all_codes)} 只")
        if force:
            remaining = all_codes; skipped = 0
        else:
            existing = set()
            try:
                rows = db.execute(text("SELECT DISTINCT stock_code FROM daily_quote WHERE trade_date=:d"), {"d": date_str}).fetchall()
                existing = {r[0] for r in rows}
            except: pass
            remaining = [c for c in all_codes if c not in existing]
            skipped = len(all_codes) - len(remaining)
        logger.info(f"  待下载: {len(remaining)} 只 (force={force})")

        result = self._download_kline_batch(
            codes=remaining, stock_names=stock_names,
            start_date=date_str, end_date=date_str,
            db=db, upsert_mode=True, date_str_filter=date_str)
        result["skipped"] = skipped

        if result["errors"] > len(remaining) * 0.8 and result["errors"] > 10:
            logger.warning("  大量错误，尝试重新登录补采...")
            self.logout(); time.sleep(3)
            if self.login():
                retry = self._download_kline_batch(
                    codes=result["failed_codes"], stock_names=stock_names,
                    start_date=date_str, end_date=date_str,
                    db=db, upsert_mode=True, date_str_filter=date_str)
                result["rows"] += retry["rows"]

        elapsed = time.time() - start_time
        logger.info(f"每日更新完成: {result['stocks']}只 +{result['rows']}行 错误{result['errors']} {elapsed:.0f}s")
        return result

    # ── 记录失败 ──

    @staticmethod
    def _record_failure(db, exchange, trade_date, data_type, error_msg, stock_code=None):
        try:
            db.execute(text("INSERT INTO failed_downloads (exchange,trade_date,data_type,error_msg,retry_count,status) VALUES (:ex,:td,:dt,:msg,0,'pending')"),
                       {"ex": exchange, "td": trade_date, "dt": data_type, "msg": f"{stock_code}: {error_msg}"[:500]})
        except: pass

    # ═══════════════════════════════════════════════════
    #  全量历史下载（A股日K线）
    # ═══════════════════════════════════════════════════

    def download_all_stocks(self, start_date: str = "2021-01-01", end_date: str = None,
                            db=None, skip_existing: bool = True) -> dict:
        if end_date is None:
            end_date = date.today().isoformat()
        if not self.login():
            return {"error": "login_failed"}
        if db is None:
            db = get_sync_db()
        stock_info = self.get_a_stock_codes()
        if not stock_info:
            return {"error": "no_stock_list"}
        all_codes = list(stock_info.keys())
        skip_set = set()
        if skip_existing:
            db_skip = get_db_completed_stocks(db, "daily_quote")
            skip_set |= db_skip
        remaining = [c for c in all_codes if c not in skip_set]
        skipped = len(all_codes) - len(remaining)
        logger.info(f"全量下载: {len(all_codes)}只 跳过{skipped} 待下载{len(remaining)} | {start_date}~{end_date}")
        init_progress(len(all_codes), start_date, end_date, "daily_kline")
        stock_names = {c: stock_info[c]['name'] for c in remaining}
        result = self._download_kline_batch(
            codes=remaining, stock_names=stock_names,
            start_date=start_date, end_date=end_date,
            db=db, upsert_mode=True, date_str_filter=None)
        db.commit()
        mark_batch_completed(remaining, "daily_kline")
        logger.info(f"全量下载完成: {result['rows']}行 新增{result['stocks']} 失败{result['errors']}")
        return {"total_rows": result["rows"], "stocks": len(all_codes),
                "new_stocks": result["stocks"], "skipped": skipped, "errors": result["errors"]}

    # ═══════════════════════════════════════════════════
    #  指数日K线
    # ═══════════════════════════════════════════════════

    def download_all_index_daily(self, trade_date: str = None, db=None,
                                   force: bool = True,
                                   start_date: str = None, end_date: str = None) -> dict:
        """下载全量指数日K线，支持单日/多日 + 覆盖/跳过。"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        if not self.login():
            return {"rows": 0, "errors": 0}
        if db is None:
            db = get_sync_db()
        sd = trade_date or start_date
        ed = trade_date or end_date
        if not sd or not ed:
            return {"rows": 0, "errors": 0}
        is_single = (sd == ed)
        label = sd if is_single else f"{sd}~{ed}"

        rs = bs.query_stock_basic()
        items = []
        while rs.next():
            d = rs.get_row_data()
            if len(d) > 4 and d[4] == '2':
                raw = d[0]
                for p in ('sh.', 'sz.', 'bj.'):
                    if raw.startswith(p):
                        code = raw[len(p):]; break
                else:
                    code = raw
                items.append((raw, code, d[1]))

        if not force:
            before = len(items)
            try:
                rows = db.execute(text("SELECT DISTINCT index_code FROM index_daily_quote WHERE trade_date BETWEEN :s AND :e"),
                                 {"s": sd, "e": ed}).fetchall()
                exist = {r[0] for r in rows}
                items = [i for i in items if i[1] not in exist]
                logger.info(f"  跳过 {before - len(items)} 只")
            except: pass

        logger.info(f"指数 {label}: {len(items)} 只")
        total = 0
        lock = threading.Lock()

        def fetch(item):
            bs_code, icode, name = item
            try:
                r = bs.query_history_k_data_plus(bs_code, 'date,code,open,high,low,close,volume,amount',
                    start_date=sd, end_date=ed, frequency='d', adjustflag='3')
                rows = []
                while r.next():
                    rows.append(r.get_row_data())
                return (item, rows, None)
            except Exception as e:
                return (item, None, str(e))

        # baostock 单一 TCP 连接非线程安全 → 串行拉取
        for item in items:
            (bs_code, icode, name), rows, err = fetch(item)
            if err:
                logger.warning(f"指数拉取失败 {icode}: {err}")
                continue
            if not rows: continue
            for d in rows:
                if is_single and d[0] != sd: continue
                try:
                    with lock:
                        db.execute(text(
                            "INSERT INTO index_daily_quote "
                            "(trade_date,index_code,index_name,open,high,low,close,volume,amount) "
                            "VALUES (:d,:c,:n,:o,:h,:l,:cl,:v,:a) "
                            "ON CONFLICT (trade_date,index_code) DO UPDATE SET "
                            "open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,"
                            "close=EXCLUDED.close,volume=EXCLUDED.volume,amount=EXCLUDED.amount"),
                            {"d":d[0],"c":icode,"n":name,
                             "o":float(d[2]) if d[2] else 0,"h":float(d[3]) if d[3] else 0,
                             "l":float(d[4]) if d[4] else 0,"cl":float(d[5]) if d[5] else 0,
                             "v":int(float(d[6])) if d[6] else 0,"a":float(d[7]) if d[7] else 0})
                        total += 1
                except:
                    db.rollback()
                    logger.warning(f"指数插入失败 {label}, 回滚本批次")
        db.commit()
        logger.info(f"指数下载完成 {label}: {total} 条")
        return {"rows": total, "errors": 0}

    # ═══════════════════════════════════════════════════
    #  ETF日K线
    # ═══════════════════════════════════════════════════

    def download_etf_daily(self, trade_date: str = None, db=None,
                            force: bool = True,
                            start_date: str = None, end_date: str = None) -> dict:
        """
        下载 ETF 日K线（type=5），支持单日/多日 + 覆盖/跳过。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        if not self.login():
            return {"rows": 0}
        if db is None:
            db = get_sync_db()
        sd = trade_date or start_date
        ed = trade_date or end_date
        if not sd or not ed:
            return {"rows": 0}
        is_single = (sd == ed)
        label = sd if is_single else f"{sd}~{ed}"

        rs = bs.query_stock_basic()
        items = []
        while rs.next():
            d = rs.get_row_data()
            if len(d) > 4 and d[4] == '5':
                raw = d[0]
                for p in ('sh.', 'sz.', 'bj.'):
                    if raw.startswith(p):
                        code = raw[len(p):]
                        ex = {'sh.': 'SSE', 'sz.': 'SZSE', 'bj.': 'BSE'}[p]
                        break
                else:
                    code, ex = raw, 'SSE'
                items.append((raw, code, d[1], ex))

        if not force:
            before = len(items)
            try:
                rows = db.execute(text(
                    "SELECT DISTINCT d.stock_code FROM daily_quote d JOIN stock_master s ON s.stock_code=d.stock_code "
                    "WHERE d.trade_date BETWEEN :s AND :e AND s.stock_type='etf'"),
                    {"s": sd, "e": ed}).fetchall()
                exist = {r[0] for r in rows}
                items = [i for i in items if i[1] not in exist]
                logger.info(f"  跳过 {before - len(items)} 只")
            except: pass

        logger.info(f"ETF {label}: {len(items)} 只")
        total = 0; errors = 0
        buf = []; lock = threading.Lock()

        def fetch(item):
            bs_code, code, name, ex = item
            try:
                # 不复权 OHLCV
                r = bs.query_history_k_data_plus(bs_code, 'date,open,high,low,close,volume,amount,turn',
                    start_date=sd, end_date=ed, frequency='d', adjustflag='3')
                raw_rows = []
                while r.next(): raw_rows.append(r.get_row_data())
                # 后复权 close
                r2 = bs.query_history_k_data_plus(bs_code, 'date,close',
                    start_date=sd, end_date=ed, frequency='d', adjustflag='1')
                hfq = {}
                if r2.error_code == '0':
                    while r2.next():
                        d2 = r2.get_row_data()
                        hfq[d2[0]] = d2[1]
                rows = [r + [hfq.get(r[0], r[4])] for r in raw_rows]
                return (item, rows, None)
            except Exception as e:
                return (item, None, str(e))

        # baostock 单一 TCP 连接非线程安全 → 串行拉取
        for item in items:
            (bs_code, code, name, ex), rows, err = fetch(item)
            if err or not rows:
                errors += 1; continue
            for d in rows:
                if is_single and d[0] != sd: continue
                try:
                    close_hfq = float(d[8]) if len(d) > 8 and d[8] else float(d[4]) if d[4] else 0
                    with lock:
                        buf.append((d[0], ex, code, name,
                            float(d[1]) if d[1] else 0, float(d[2]) if d[2] else 0,
                            float(d[3]) if d[3] else 0, float(d[4]) if d[4] else 0,
                            close_hfq,
                            int(float(d[5])) if d[5] else 0, float(d[6]) if d[6] else 0,
                            float(d[7]) if d[7] else None))
                        total += 1
                except: pass
            if len(buf) >= 500:
                with lock:
                    self._batch_insert_rows(db, buf)
                    db.commit(); buf.clear()
        if buf:
            self._batch_insert_rows(db, buf)
        db.commit()
        logger.info(f"ETF下载完成 {label}: +{total}条 {errors}错误")
        return {"rows": total, "errors": errors}

    # ═══════════════════════════════════════════════════
    #  股票基础信息
    # ═══════════════════════════════════════════════════

    def get_stock_basic_info(self) -> pd.DataFrame:
        """获取全量证券基础信息（含指数/ETF/债券类型）。"""
        self.login()
        data = []
        rs = bs.query_stock_basic()
        if rs.error_code == '0':
            while rs.next():
                row = rs.get_row_data()
                if not row or len(row) < 2: continue
                sec_type = row[4] if len(row) > 4 else ''
                raw_code = row[0]
                for p in ('sh.', 'sz.', 'bj.'):
                    if raw_code.startswith(p):
                        code = raw_code[len(p):]; break
                else: code = raw_code
                if not (code.isdigit() and len(code) == 6): continue
                tmap = {'1': 'stock', '2': 'index', '4': 'bond', '5': 'etf'}
                data.append({"stock_code": code, "stock_name": row[1],
                    "exchange": self._exchange(code),
                    "ipo_date": row[2] if len(row) > 2 and row[2] else None,
                    "status": "N" if row[5] == '1' else "D",
                    "stock_type": tmap.get(sec_type, 'other')})
        return pd.DataFrame(data)

    # ═══════════════════════════════════════════════════
    #  基本面
    # ═══════════════════════════════════════════════════

    def _get_latest_fundamentals(self, bs_code: str) -> dict:
        today = date.today()
        candidates = [(today.year - 1, 4), (today.year, 1)]
        result = {"roe": None, "revenue_yoy": None, "profit_yoy": None}
        for year, quarter in candidates:
            if result["roe"] is None:
                try:
                    r = bs.query_profit_data(code=bs_code, year=year, quarter=quarter)
                    if r.error_code == '0':
                        while r.next():
                            rr = r.get_row_data()
                            if len(rr) > 3 and rr[3]:
                                try: result["roe"] = float(rr[3]) * 100
                                except: pass
                except: pass
            if result["revenue_yoy"] is None or result["profit_yoy"] is None:
                try:
                    r = bs.query_growth_data(code=bs_code, year=year, quarter=quarter)
                    if r.error_code == '0':
                        while r.next():
                            rr = r.get_row_data()
                            if result["revenue_yoy"] is None and len(rr) > 7 and rr[7]:
                                try: result["revenue_yoy"] = float(rr[7]) * 100
                                except: pass
                            if result["profit_yoy"] is None and len(rr) > 5 and rr[5]:
                                try: result["profit_yoy"] = float(rr[5]) * 100
                                except: pass
                except: pass
            if all(v is not None for v in result.values()):
                break
        return result

    def download_fundamentals(self, codes: List[str] = None, db=None,
                              force: bool = True, progress_cb=None) -> dict:
        """
        下载基本面(PE/PB/ROE/营收/净利/行业/股本/市值)，并行 + upsert。
        total_shares = volume / (turn% / 100), market_cap = close x total_shares。
        """
        from concurrent.futures import ThreadPoolExecutor
        import threading

        self.login()
        if db is None:
            db = get_sync_db()
        if codes is None:
            codes = self.get_all_stock_codes()
        if not force:
            try:
                rows = db.execute(text("SELECT DISTINCT stock_code FROM stock_fundamentals")).fetchall()
                exist = {r[0] for r in rows}
                codes = [c for c in codes if c not in exist]
                logger.info(f"  跳过 {len(exist)} 只已有，待下载 {len(codes)} 只")
            except: pass

        logger.info(f"基本面下载: {len(codes)} 只")

        # 行业缓存（一次性）
        industry_map = {}
        try:
            r = bs.query_stock_industry()
            if r.error_code == '0':
                while r.next():
                    rr = r.get_row_data()
                    if rr and len(rr) >= 4:
                        c = rr[1].replace("sz.", "").replace("sh.", "")
                        industry_map[c] = rr[3]
        except: pass

        updated = 0; pe_missing = 0; profit_missing = 0; growth_missing = 0
        lock = threading.Lock()

        def fetch_one(code: str) -> dict:
            bs_code = self._bs_code(code)
            res = {"code":code,"pe":None,"pb":None,"roe":None,"rev":None,"prf":None,"ts":None,"mc":None}
            try:
                r = bs.query_history_k_data_plus(bs_code, "date,close,volume,peTTM,pbMRQ,turn",
                    start_date=(date.today()-timedelta(days=7)).isoformat(),
                    end_date=date.today().isoformat(), frequency="d")
                while r.next():
                    rr = r.get_row_data()
                    if not rr or len(rr) < 6: continue
                    try:
                        if rr[3] and rr[3] != '0.000000': res["pe"] = float(rr[3])
                        if rr[4] and rr[4] != '0.000000': res["pb"] = float(rr[4])
                        if rr[2] and rr[5] and rr[5] != '':
                            vol = float(rr[2]); turn = float(rr[5]); close = float(rr[1])
                            if turn > 0 and close > 0:
                                ts = vol / (turn / 100)
                                res["ts"] = round(ts); res["mc"] = round(close * ts)
                    except: pass
            except: pass
            fund = self._get_latest_fundamentals(bs_code)
            res["roe"]=fund["roe"]; res["rev"]=fund["revenue_yoy"]; res["prf"]=fund["profit_yoy"]
            return res

        # baostock 内部为单一 TCP 连接，多线程并发会导致数据串扰 → 串行拉取
        for i, code in enumerate(codes):
            res = fetch_one(code)
            if res["pe"] is None: pe_missing += 1
            if res["roe"] is None: profit_missing += 1
            if res["rev"] is None and res["prf"] is None: growth_missing += 1
            ind = industry_map.get(res["code"], "")
            try:
                with lock:
                    db.execute(text("INSERT INTO stock_fundamentals (stock_code,trade_date,pe_ttm,pb_mrq,industry,roe,revenue_yoy,profit_yoy,total_shares,market_cap,updated_at) VALUES (:c,CURRENT_DATE,:pe,:pb,:ind,:roe,:rev,:prf,:ts,:mc,CURRENT_TIMESTAMP) ON CONFLICT (stock_code, trade_date) DO UPDATE SET pe_ttm=EXCLUDED.pe_ttm,pb_mrq=EXCLUDED.pb_mrq,industry=EXCLUDED.industry,roe=EXCLUDED.roe,revenue_yoy=EXCLUDED.revenue_yoy,profit_yoy=EXCLUDED.profit_yoy,total_shares=EXCLUDED.total_shares,market_cap=EXCLUDED.market_cap,updated_at=CURRENT_TIMESTAMP"),
                            {"c":res["code"],"pe":res["pe"],"pb":res["pb"],"ind":ind,"roe":res["roe"],"rev":res["rev"],"prf":res["prf"],"ts":res["ts"],"mc":res["mc"]})
                    updated += 1
                    # 每 200 只报告一次进度
                    if updated % 200 == 0 and progress_cb:
                        progress_cb(updated)
            except Exception as e:
                logger.warning(f"  基本面入库 {res['code']} 失败: {e}")
        if progress_cb: progress_cb(updated)
        db.commit()
        errors = pe_missing + profit_missing + growth_missing
        logger.info(f"基本面完成: {updated}只 PE缺{pe_missing} ROE缺{profit_missing} 增长缺{growth_missing}")
        return {"rows": updated, "errors": errors}

    def download_fundamentals_history(self, codes: List[str] = None) -> int:
        """下载基本面历史数据（按季度），写入 stock_fundamentals_history。"""
        self.login()
        db = get_sync_db()
        if codes is None:
            codes = self.get_all_stock_codes()
        quarters = [(2024, 1), (2024, 2), (2024, 3), (2024, 4), (2025, 1), (2025, 2), (2025, 3), (2025, 4), (2026, 1)]
        total = 0
        for code in codes:
            bs_code = self._bs_code(code)
            for year, q in quarters:
                try:
                    r = bs.query_profit_data(code=bs_code, year=year, quarter=q)
                    if r.error_code == '0' and r.next():
                        d = r.get_row_data()
                        report_date = f"{year}-{q*3:02d}-01"
                        pe_ttm, pb, roe = None, None, None
                        if len(d) > 3 and d[3]: pe_ttm = float(d[3])
                        if len(d) > 4 and d[4]: pb = float(d[4])
                        if len(d) > 5 and d[5]: roe = float(d[5]) * 100
                        db.execute(text("""
                            INSERT INTO stock_fundamentals_history (stock_code, report_date, pe_ttm, pb_mrq, roe)
                            VALUES (:c,:d,:pe,:pb,:roe)
                            ON CONFLICT (stock_code, report_date) DO UPDATE SET pe_ttm=EXCLUDED.pe_ttm, pb_mrq=EXCLUDED.pb_mrq, roe=EXCLUDED.roe
                        """), {"c":code,"d":report_date,"pe":pe_ttm,"pb":pb,"roe":roe})
                        total += 1
                except: pass
        db.commit()
        db.close()
        return total


def download_history(start: str = "2021-01-01", end: str = None):
    c = BaostockCrawler()
    result = c.download_all_stocks(start, end)
    c.logout()
    return result


def download_daily(date_str: str = None):
    c = BaostockCrawler()
    d = date.fromisoformat(date_str) if date_str else date.today()
    result = c.download_daily_update(d)
    c.logout()
    return result
