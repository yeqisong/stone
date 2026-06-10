#!/usr/bin/env python3
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
            # 北交所/其他：尝试 sz 前缀
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
        """
        获取全量A股代码及基础信息（仅 type=1，排除指数/可转债/ETF）。

        Returns:
            {code: {name, ipo_date, out_date, status}, ...}
        """
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
            # row: [code, name, ipoDate, outDate, type, status]
            sec_type = row[4] if len(row) > 4 else ''
            if sec_type != '1':       # 仅 A 股，排除指数/可转债/ETF
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
            # 在数据起始日期前已退市的跳过
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
        """获取全量A股代码列表（兼容旧接口）。"""
        return list(self.get_a_stock_codes().keys())

    # ── 带重试的K线下载 ──

    def _fetch_kline(self, bs_code: str, start_date: str, end_date: str) -> tuple:
        """
        下载单只股票K线数据，带指数退避重试 + 会话失效检测。

        Returns:
            (rows_or_None, need_relogin: bool)
        """
        for attempt in range(RETRY_MAX + 1):
            try:
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,open,high,low,close,volume,amount,turn",
                    start_date=start_date, end_date=end_date,
                    frequency="d", adjustflag="3")

                if rs.error_code == '0':
                    rows = []
                    while rs.next():
                        rows.append(rs.get_row_data())
                    return (rows, False)

                msg = rs.error_msg
                # 会话失效 → 立即通知上层重新登录
                if '未登录' in msg or 'login' in msg.lower():
                    logger.warning(f"  {bs_code} 会话失效: {msg}")
                    return (None, True)

                if attempt < RETRY_MAX:
                    wait = RETRY_BACKOFF ** (attempt + 1)
                    logger.debug(f"  {bs_code} API错误(attempt {attempt+1}): {msg}，{wait:.0f}s后重试")
                    time.sleep(wait)
                    continue
                logger.warning(f"  {bs_code} API最终失败: {msg}")
                return (None, False)

            except Exception as e:
                msg = str(e)
                if '未登录' in msg or 'login' in msg.lower():
                    logger.warning(f"  {bs_code} 会话失效异常: {e}")
                    return (None, True)

                if attempt < RETRY_MAX:
                    wait = RETRY_BACKOFF ** (attempt + 1)
                    logger.debug(f"  {bs_code} 网络异常(attempt {attempt+1}): {e}，{wait:.0f}s后重试")
                    time.sleep(wait)
                    continue
                logger.warning(f"  {bs_code} 最终网络失败: {e}")
                return (None, False)

        return (None, False)

    # ── 批量插入 ──

    @staticmethod
    def _batch_insert_rows(db, rows_batch: list) -> int:
        """
        批量插入 daily_quote 行（chunked INSERT OR IGNORE）。
        rows_batch: [(trade_date, exchange, stock_code, stock_name,
                       open, high, low, close, volume, amount, turnover), ...]
        """
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
                    f'ch{idx}': row[7],  # close_hfq = close (每日增量无复权)
                    f'cq{idx}': row[7],  # close_qfq = close
                    f'v{idx}': row[8], f'a{idx}': row[9],
                    f't{idx}': row[10],
                })
            sql = (
                "INSERT INTO daily_quote "
                "(trade_date,exchange,stock_code,stock_name,"
                "open,high,low,close,close_hfq,close_qfq,"
                "volume,amount,turnover) "
                "VALUES " + ",".join(placeholders))
            from app.db.connection import is_sqlite
            if is_sqlite():
                sql = sql.replace("INSERT INTO", "INSERT OR IGNORE INTO")
            else:
                sql += " ON CONFLICT (stock_code, exchange, trade_date) DO NOTHING"
            try:
                db.execute(text(sql), params)
                total += len(chunk)
            except Exception as e:
                logger.error(f"批量插入异常: {e}")
        return total

    # ═══════════════════════════════════════════════════
    #  每日增量更新（生产级）
    # ═══════════════════════════════════════════════════

    def download_daily_update(self, trade_date: date = None, db=None) -> dict:
        """
        每日增量更新 — 下载当日日K线数据（生产级）。

        特性:
        - 仅查询 A 股（type=1），排除指数/可转债/ETF
        - 随机请求间隔，避免限流
        - 指数退避重试 + 会话失效自动重登录
        - 批量 INSERT（200行/批）
        - 自动从 stock_master 填充 stock_name
        - 失败记录写入 failed_downloads 表，供补采使用
        - 详细返回状态

        Returns:
            {rows, stocks, errors, skipped, failed_codes, elapsed_seconds}
        """
        if trade_date is None:
            trade_date = date.today()
        date_str = trade_date.isoformat()

        if not self.login():
            return {"rows": 0, "stocks": 0, "errors": 0, "skipped": 0,
                    "failed_codes": [], "elapsed_seconds": 0, "fatal": "login_failed"}

        if db is None:
            db = get_sync_db()
        own_db = (db is None)
        if own_db:
            db = get_sync_db()

        start_time = time.time()
        logger.info(f"=== 每日增量更新 {date_str} ===")

        # ── 1. 获取A股列表 ──
        stock_info = self.get_a_stock_codes()
        if not stock_info:
            logger.error("无法获取股票列表")
            return {"rows": 0, "stocks": 0, "errors": 0, "skipped": 0,
                    "failed_codes": [], "elapsed_seconds": time.time() - start_time,
                    "fatal": "no_stock_list"}

        all_codes = list(stock_info.keys())
        logger.info(f"  候选A股: {len(all_codes)} 只")

        # ── 2. 查询已有当日数据的股票（跳过） ──
        existing = set()
        try:
            rows = db.execute(text(
                "SELECT DISTINCT stock_code FROM daily_quote WHERE trade_date = :d"
            ), {"d": date_str}).fetchall()
            existing = {r[0] for r in rows}
        except Exception as e:
            logger.warning(f"查询已有数据失败: {e}")

        # 跳过今日尚未上市的股票
        not_listed_yet = {c for c, info in stock_info.items()
                          if info['ipo_date'] and info['ipo_date'] > date_str}

        remaining = [c for c in all_codes if c not in existing and c not in not_listed_yet]
        skipped = len(all_codes) - len(remaining)
        logger.info(f"  已覆盖: {len(existing)}, 未上市: {len(not_listed_yet)}, "
                     f"跳过: {skipped}, 待下载: {len(remaining)}")

        if not remaining:
            logger.info("  所有股票已覆盖今日数据，无需下载")
            return {"rows": 0, "stocks": 0, "errors": 0, "skipped": skipped,
                    "failed_codes": [], "elapsed_seconds": time.time() - start_time}

        # ── 3. 逐只下载当日数据 ──
        total_rows = 0
        errors = 0
        failed_codes = []
        insert_buffer = []
        processed = 0

        i = 0
        while i < len(remaining):
            code = remaining[i]
            bs_code = self._bs_code(code)
            ex = self._exchange(code)
            name = stock_info[code]['name']

            _random_delay()

            rows, need_relogin = self._fetch_kline(bs_code, date_str, date_str)

            # 会话失效 → 立即重登录，重试同一只股票
            if need_relogin:
                logger.warning("  会话失效，重新登录...")
                self.logout()
                time.sleep(3)
                if self.login():
                    continue  # 重试同一只，i 不变
                else:
                    logger.error("  重新登录失败，跳过该股票")
                    errors += 1
                    failed_codes.append(code)
                    self._record_failure(db, ex, trade_date, "daily_kline",
                                         "relogin_failed", code)
                    i += 1
                    continue

            if rows is None:
                errors += 1
                failed_codes.append(code)
                self._record_failure(db, ex, trade_date, "daily_kline",
                                     "api_error", code)
                i += 1
                continue

            # 过滤：仅保留当日数据
            for row in rows:
                if row[0] != date_str:
                    continue
                try:
                    insert_buffer.append((
                        row[0], ex, code, name,
                        float(row[1]), float(row[2]), float(row[3]), float(row[4]),
                        int(float(row[5])), float(row[6]),
                        float(row[7]) if row[7] else None,
                    ))
                except (ValueError, IndexError):
                    pass

            total_rows += len(rows)
            processed += 1
            i += 1

            # 批量提交（每日增量单只1行，500只提交一次）
            if len(insert_buffer) >= 500:
                self._batch_insert_rows(db, insert_buffer)
                db.commit()
                insert_buffer.clear()

            # 日志输出
            if processed % 500 == 0:
                elapsed = time.time() - start_time
                pct = 100 * i / len(remaining)
                logger.info(f"  进度: {i}/{len(remaining)} ({pct:.1f}%) | "
                            f"+{total_rows}行 | 错误{errors} | {elapsed:.0f}秒")

        # ── 4. 最终提交 ──
        if insert_buffer:
            self._batch_insert_rows(db, insert_buffer)
        db.commit()

        elapsed = time.time() - start_time
        logger.info(f"每日更新完成: {processed}只有数据, +{total_rows}行, "
                     f"错误{errors}, 跳过{skipped}, 耗时{elapsed:.0f}秒")

        if failed_codes:
            logger.warning(f"  失败股票 ({len(failed_codes)}): {failed_codes[:20]}...")

        return {
            "rows": total_rows,
            "stocks": processed,
            "errors": errors,
            "skipped": skipped,
            "failed_codes": failed_codes,
            "elapsed_seconds": elapsed,
        }

    # ── 记录失败 ──

    @staticmethod
    def _record_failure(db, exchange: str, trade_date: date, data_type: str,
                         error_msg: str, stock_code: str = None):
        """记录下载失败到 failed_downloads 表。"""
        try:
            db.execute(text("""
                INSERT INTO failed_downloads
                (exchange, trade_date, data_type, error_msg, retry_count, status)
                VALUES (:ex, :td, :dt, :msg, 0, 'pending')
            """), {
                "ex": exchange, "td": trade_date,
                "dt": data_type, "msg": f"{stock_code}: {error_msg}"[:500],
            })
        except Exception:
            pass

    # ═══════════════════════════════════════════════════
    #  全量历史下载（生产级）
    # ═══════════════════════════════════════════════════

    def download_all_stocks(self, start_date: str = "2021-01-01",
                            end_date: str = None, db=None,
                            skip_existing: bool = True) -> dict:
        """
        下载全量A股历史日K线数据。支持断点续传。

        Returns:
            {total_rows, stocks, new_stocks, skipped, errors}
        """
        if end_date is None:
            end_date = date.today().isoformat()

        if not self.login():
            return {"total_rows": 0, "stocks": 0, "new_stocks": 0,
                    "skipped": 0, "errors": 0, "error": "login_failed"}

        if db is None:
            db = get_sync_db()

        # ── 获取A股列表 ──
        stock_info = self.get_a_stock_codes()
        if not stock_info:
            return {"total_rows": 0, "stocks": 0, "new_stocks": 0,
                    "skipped": 0, "errors": 0, "error": "no_stock_list"}

        all_codes = list(stock_info.keys())

        # ── 跳过已有数据的股票 ──
        skip_set: Set[str] = set()
        if skip_existing:
            db_skip = get_db_completed_stocks(db, "daily_quote")
            skip_set |= db_skip
            logger.info(f"  DB中已有数据: {len(db_skip)} 只，跳过")

        remaining = [c for c in all_codes if c not in skip_set]
        skipped = len(all_codes) - len(remaining)
        logger.info(f"全量下载: 总计 {len(all_codes)} 只, 跳过 {skipped} 只, "
                     f"待下载 {len(remaining)} 只")
        logger.info(f"  日期范围: {start_date} ~ {end_date}")

        init_progress(len(all_codes), start_date, end_date, "daily_kline")

        total_rows = 0
        errors = 0
        new_stocks = 0
        batch_codes = []

        for i, code in enumerate(remaining):
            bs_code = self._bs_code(code)
            ex = self._exchange(code)
            name = stock_info[code]['name']

            _random_delay()

            rows, need_relogin = self._fetch_kline(bs_code, start_date, end_date)

            if need_relogin:
                logger.warning("  会话失效，重新登录...")
                self.logout()
                time.sleep(3)
                if self.login():
                    # 重试同一只
                    rows, need_relogin = self._fetch_kline(bs_code, start_date, end_date)
                    if need_relogin or rows is None:
                        errors += 1
                        continue
                else:
                    errors += 1
                    continue

            if rows is None:
                errors += 1
                continue

            if not rows:
                continue

            # 批量插入
            insert_batch = []
            for row in rows:
                try:
                    insert_batch.append((
                        row[0], ex, code, name,
                        float(row[1]), float(row[2]), float(row[3]), float(row[4]),
                        int(float(row[5])), float(row[6]),
                        float(row[7]) if row[7] else None,
                    ))
                except (ValueError, IndexError):
                    pass

            self._batch_insert_rows(db, insert_batch)
            total_rows += len(rows)
            new_stocks += 1
            batch_codes.append(code)

            # 每 100 只提交
            if (i + 1) % 100 == 0:
                db.commit()
                mark_batch_completed(batch_codes, "daily_kline")
                batch_codes.clear()
                pct = (i + 1) / len(remaining) * 100
                logger.info(f"  进度: {skipped + i + 1}/{len(all_codes)} ({pct:.1f}%) "
                            f"| {total_rows:,}行 | 剩余 {len(remaining) - i - 1} 只")

        # 最后一批
        db.commit()
        if batch_codes:
            mark_batch_completed(batch_codes, "daily_kline")

        logger.info(f"下载完成: {total_rows:,} 行, 新增 {new_stocks} 只, "
                     f"跳过 {skipped} 只, 失败 {errors} 只")
        return {
            "total_rows": total_rows,
            "stocks": len(all_codes),
            "new_stocks": new_stocks,
            "skipped": skipped,
            "errors": errors,
        }

    # ── 按指定日期下载（补采用） ──

    def download_for_date(self, trade_date: date, db=None) -> dict:
        """
        补采指定交易日的数据。与 download_daily_update 逻辑相同，
        但不跳过已有数据的股票（强制覆盖/补充）。

        Returns: 同 download_daily_update
        """
        return self.download_daily_update(trade_date, db)

    # ═══════════════════════════════════════════════════
    #  股票基础信息
    # ═══════════════════════════════════════════════════

    def get_stock_basic_info(self) -> pd.DataFrame:
        """获取股票基础信息（代码、名称、上市日期、交易所、类型）。"""
        # 需要从 baostock 原始数据获取 type 信息（get_a_stock_codes 只返回 type=1）
        # 因此这里改用 query_stock_basic 原始数据
        self.login()
        data = []
        rs = bs.query_stock_basic()
        if rs.error_code == '0':
            while rs.next():
                row = rs.get_row_data()
                sec_type = row[4] if len(row) > 4 else ''
                raw_code = row[0]
                for prefix in ('sh.', 'sz.', 'bj.'):
                    if raw_code.startswith(prefix):
                        code = raw_code[len(prefix):]
                        break
                else:
                    code = raw_code
                if not (code.isdigit() and len(code) == 6):
                    continue

                # 类型映射
                type_names = {'1': 'stock', '2': 'index', '4': 'bond', '5': 'etf'}
                stock_type = type_names.get(sec_type, 'other')

                data.append({
                    "stock_code": code,
                    "stock_name": row[1],
                    "exchange": self._exchange(code),
                    "ipo_date": row[2] if len(row) > 2 and row[2] else None,
                    "status": "N" if row[5] == '1' else "D",
                    "stock_type": stock_type,
                })
        return pd.DataFrame(data)

    # ═══════════════════════════════════════════════════
    #  基本面下载
    # ═══════════════════════════════════════════════════

    def _get_latest_fundamentals(self, bs_code: str) -> dict:
        """
        获取单只股票最新基本面数据。智能季度回退。
        Returns: {roe, revenue_yoy, profit_yoy}
        """
        today = date.today()
        candidates = [
            (today.year - 1, 4),       # 去年年报
            (today.year, 1),           # 今年Q1
        ]

        result = {"roe": None, "revenue_yoy": None, "profit_yoy": None}

        for year, quarter in candidates:
            if result["roe"] is None:
                try:
                    rs = bs.query_profit_data(code=bs_code, year=year, quarter=quarter)
                    if rs.error_code == '0':
                        while rs.next():
                            rr = rs.get_row_data()
                            if len(rr) > 3 and rr[3]:
                                try:
                                    result["roe"] = float(rr[3]) * 100
                                except (ValueError, TypeError):
                                    pass
                except Exception:
                    pass

            if result["revenue_yoy"] is None or result["profit_yoy"] is None:
                try:
                    rs = bs.query_growth_data(code=bs_code, year=year, quarter=quarter)
                    if rs.error_code == '0':
                        while rs.next():
                            rr = rs.get_row_data()
                            if result["revenue_yoy"] is None and len(rr) > 7 and rr[7]:
                                try:
                                    result["revenue_yoy"] = float(rr[7]) * 100
                                except (ValueError, TypeError):
                                    pass
                            if result["profit_yoy"] is None and len(rr) > 5 and rr[5]:
                                try:
                                    result["profit_yoy"] = float(rr[5]) * 100
                                except (ValueError, TypeError):
                                    pass
                except Exception:
                    pass

            if all(v is not None for v in result.values()):
                break

        return result

    def download_fundamentals(self, codes: List[str] = None, db=None,
                              skip_existing: bool = False,
                              skip_pe_pb: bool = True) -> int:
        """下载基本面数据(PE/PB/行业/ROE/增长率)。"""
        self.login()
        if db is None:
            db = get_sync_db()

        if codes is None:
            codes = self.get_all_stock_codes()

        if skip_existing:
            existing = get_db_completed_stocks(db, "stock_fundamentals")
            try:
                has_roe = db.execute(text(
                    "SELECT DISTINCT stock_code FROM stock_fundamentals WHERE roe IS NOT NULL"
                )).fetchall()
                has_roe_set = {r[0] for r in has_roe}
                codes = [c for c in codes if c not in existing or c not in has_roe_set]
            except Exception:
                codes = [c for c in codes if c not in existing]
            logger.info(f"  跳过 {len(existing)} 只已有基本面数据，待下载 {len(codes)} 只")

        total = len(codes)
        logger.info(f"基本面下载: {total} 只")

        # 行业缓存
        industry_map = {}
        try:
            rs_i = bs.query_stock_industry()
            if rs_i.error_code == '0':
                while rs_i.next():
                    r = rs_i.get_row_data()
                    if len(r) >= 4:
                        c = r[1].replace("sz.", "").replace("sh.", "")
                        industry_map[c] = r[3]
            logger.info(f"  行业数据: {len(industry_map)} 条")
        except Exception:
            logger.warning("  行业数据获取失败，将跳过行业字段")

        init_progress(total, section="fundamentals")

        has_pe_set: Set[str] = set()
        if skip_pe_pb:
            try:
                rows = db.execute(text(
                    "SELECT DISTINCT stock_code FROM stock_fundamentals "
                    "WHERE pe_ttm IS NOT NULL"
                )).fetchall()
                has_pe_set = {r[0] for r in rows}
                logger.info(f"  已有PE数据: {len(has_pe_set)} 只，跳过PE/PB查询")
            except Exception:
                pass

        updated = 0
        pe_missing = 0
        profit_missing = 0
        growth_missing = 0
        batch_codes = []

        for i, code in enumerate(codes):
            try:
                bs_code = self._bs_code(code)

                pe, pb = None, None
                if not skip_pe_pb or code not in has_pe_set:
                    try:
                        rs = bs.query_history_k_data_plus(bs_code,
                            "date,close,peTTM,pbMRQ",
                            start_date=(date.today() - timedelta(days=7)).isoformat(),
                            end_date=date.today().isoformat(),
                            frequency="d")
                        while rs.next():
                            r = rs.get_row_data()
                            try:
                                if r[2] and r[2] != '0.000000' and r[2] != '':
                                    pe = float(r[2])
                                if r[3] and r[3] != '0.000000' and r[3] != '':
                                    pb = float(r[3])
                            except (ValueError, TypeError):
                                pass
                    except Exception:
                        pass
                    if pe is None:
                        pe_missing += 1
                else:
                    try:
                        row = db.execute(text(
                            "SELECT pe_ttm, pb_mrq FROM stock_fundamentals WHERE stock_code=:c"
                        ), {"c": code}).fetchone()
                        if row:
                            pe, pb = row[0], row[1]
                    except Exception:
                        pass

                fund = self._get_latest_fundamentals(bs_code)
                roe = fund["roe"]
                rev_yoy = fund["revenue_yoy"]
                prf_yoy = fund["profit_yoy"]
                if roe is None:
                    profit_missing += 1
                if rev_yoy is None and prf_yoy is None:
                    growth_missing += 1

                ind = industry_map.get(code, "")
                from app.db.connection import is_sqlite as _is_sql
                if _is_sql():
                    db.execute(text("""
                        INSERT OR REPLACE INTO stock_fundamentals
                        (stock_code, stock_name, industry, pe_ttm, pb_mrq,
                         roe, revenue_yoy, profit_yoy, updated_at)
                        VALUES (:c, '', :i, :pe, :pb, :roe, :ry, :py, CURRENT_TIMESTAMP)
                    """), {"c": code, "i": ind, "pe": pe, "pb": pb,
                           "roe": roe, "ry": rev_yoy, "py": prf_yoy})
                else:
                    db.execute(text("""
                        INSERT INTO stock_fundamentals
                        (stock_code, stock_name, industry, pe_ttm, pb_mrq,
                         roe, revenue_yoy, profit_yoy, updated_at)
                        VALUES (:c, '', :i, :pe, :pb, :roe, :ry, :py, CURRENT_TIMESTAMP)
                        ON CONFLICT (stock_code) DO UPDATE SET
                        stock_name=EXCLUDED.stock_name, industry=EXCLUDED.industry,
                        pe_ttm=EXCLUDED.pe_ttm, pb_mrq=EXCLUDED.pb_mrq,
                        roe=EXCLUDED.roe, revenue_yoy=EXCLUDED.revenue_yoy,
                        profit_yoy=EXCLUDED.profit_yoy, updated_at=CURRENT_TIMESTAMP
                    """), {"c": code, "i": ind, "pe": pe, "pb": pb,
                           "roe": roe, "ry": rev_yoy, "py": prf_yoy})
                updated += 1
                batch_codes.append(code)

                if (i + 1) % 200 == 0:
                    db.commit()
                    mark_batch_completed(batch_codes, "fundamentals")
                    batch_codes.clear()
                    pct = (i + 1) / total * 100
                    logger.info(f"  基本面进度: {i+1}/{total} ({pct:.1f}%) "
                                f"| PE缺失:{pe_missing} ROE缺失:{profit_missing} 增长缺失:{growth_missing}")
                    time.sleep(0.3)

            except Exception as e:
                logger.warning(f"  {code} 基本面下载失败: {e}")

        db.commit()
        if batch_codes:
            mark_batch_completed(batch_codes, "fundamentals")

        logger.info(f"基本面下载完成: {updated} 只 | "
                     f"PE缺失:{pe_missing}, ROE缺失:{profit_missing}, 增长缺失:{growth_missing}")
        return updated


    def download_index_daily(self, trade_date: str, db=None) -> int:
        """下载指定日期的主要指数日K线数据。"""
        import time
        from loguru import logger
        from app.db.connection import is_sqlite as _is_sql

        if not self.login():
            return 0
        if db is None:
            from app.db.connection import get_sync_db
            db = get_sync_db()

        INDEX_CODES = [
            ('sh.000001', '上证指数'), ('sz.399001', '深证成指'),
            ('sz.399006', '创业板指'), ('sh.000688', '科创50'),
            ('sh.000300', '沪深300'), ('sh.000016', '上证50'),
            ('sh.000905', '中证500'), ('sh.000852', '中证1000'),
        ]
        fields = 'date,code,open,high,low,close,volume,amount'
        total = 0
        for bs_code, name in INDEX_CODES:
            try:
                rs = self._bs.query_history_k_data_plus(
                    bs_code, fields, start_date=trade_date, end_date=trade_date,
                    frequency='d', adjustflag='3')
                while rs.next():
                    d = rs.get_row_data()
                    if d[0] != trade_date:
                        continue
                    ex = 'SSE' if bs_code.startswith('sh.') else 'SZSE'
                    index_code = bs_code.split('.')[1]
                    if _is_sql():
                        db.execute(text(
                            "INSERT OR REPLACE INTO index_daily_quote "
                            "(trade_date, index_code, index_name, open, high, low, close, volume, amount) "
                            "VALUES (:d,:c,:n,:o,:h,:l,:cl,:v,:a)"
                        ), {"d": d[0], "c": index_code, "n": name,
                            "o": float(d[2]) if d[2] else 0,
                            "h": float(d[3]) if d[3] else 0,
                            "l": float(d[4]) if d[4] else 0,
                            "cl": float(d[5]) if d[5] else 0,
                            "v": int(float(d[6])) if d[6] else 0,
                            "a": float(d[7]) if d[7] else 0})
                    else:
                        db.execute(text(
                            "INSERT INTO index_daily_quote "
                            "(trade_date, index_code, index_name, open, high, low, close, volume, amount) "
                            "VALUES (:d,:c,:n,:o,:h,:l,:cl,:v,:a) "
                            "ON CONFLICT (trade_date, index_code) DO UPDATE SET "
                            "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
                            "close=EXCLUDED.close, volume=EXCLUDED.volume, amount=EXCLUDED.amount"
                        ), {"d": d[0], "c": index_code, "n": name,
                            "o": float(d[2]) if d[2] else 0,
                            "h": float(d[3]) if d[3] else 0,
                            "l": float(d[4]) if d[4] else 0,
                            "cl": float(d[5]) if d[5] else 0,
                            "v": int(float(d[6])) if d[6] else 0,
                            "a": float(d[7]) if d[7] else 0})
                    total += 1
                time.sleep(0.3)
            except Exception as e:
                logger.warning(f"  指数 {bs_code} 下载失败: {e}")
        db.commit()
        logger.info(f"指数日线: {trade_date} -> {total} 条")
        return total



    def download_all_index_daily(self, trade_date: str, db=None) -> int:
        """下载全量指数日K线（type=2），写入 index_daily_quote 表。"""
        import time
        from loguru import logger
        from app.db.connection import is_sqlite as _is_sql
        if not self.login():
            return 0
        if db is None:
            from app.db.connection import get_sync_db
            db = get_sync_db()

        rs = bs.query_stock_basic()
        index_codes = []
        while rs.next():
            d = rs.get_row_data()
            if len(d) > 4 and d[4] == '2':  # 类型 '2' = 指数
                raw = d[0]
                for p in ('sh.', 'sz.', 'bj.'):
                    if raw.startswith(p):
                        code = raw[len(p):]
                        break
                else:
                    code = raw
                index_codes.append((raw, code, d[1]))

        logger.info(f"指数列表: {len(index_codes)} 只")
        fields = 'date,code,open,high,low,close,volume,amount'
        total = 0
        for bs_code, index_code, name in index_codes:
            try:
                rs2 = self._bs.query_history_k_data_plus(
                    bs_code, fields, start_date=trade_date, end_date=trade_date,
                    frequency='d', adjustflag='3')
                while rs2.next():
                    d = rs2.get_row_data()
                    if d[0] != trade_date:
                        continue
                    if _is_sql():
                        db.execute(text(
                            "INSERT OR REPLACE INTO index_daily_quote "
                            "(trade_date, index_code, index_name, open, high, low, close, volume, amount) "
                            "VALUES (:d,:c,:n,:o,:h,:l,:cl,:v,:a)"
                        ), {"d": d[0], "c": index_code, "n": name,
                            "o": float(d[2]) if d[2] else 0,
                            "h": float(d[3]) if d[3] else 0,
                            "l": float(d[4]) if d[4] else 0,
                            "cl": float(d[5]) if d[5] else 0,
                            "v": int(float(d[6])) if d[6] else 0,
                            "a": float(d[7]) if d[7] else 0})
                    else:
                        db.execute(text(
                            "INSERT INTO index_daily_quote "
                            "(trade_date, index_code, index_name, open, high, low, close, volume, amount) "
                            "VALUES (:d,:c,:n,:o,:h,:l,:cl,:v,:a) "
                            "ON CONFLICT (trade_date, index_code) DO UPDATE SET "
                            "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
                            "close=EXCLUDED.close, volume=EXCLUDED.volume, amount=EXCLUDED.amount"
                        ), {"d": d[0], "c": index_code, "n": name,
                            "o": float(d[2]) if d[2] else 0,
                            "h": float(d[3]) if d[3] else 0,
                            "l": float(d[4]) if d[4] else 0,
                            "cl": float(d[5]) if d[5] else 0,
                            "v": int(float(d[6])) if d[6] else 0,
                            "a": float(d[7]) if d[7] else 0})
                    total += 1
                time.sleep(0.2)
            except Exception as e:
                logger.warning(f"  指数 {bs_code} 失败: {e}")
        db.commit()
        logger.info(f"指数下载完成: {trade_date} -> {total} 条")
        return total

    def download_etf_daily(self, trade_date: str, db=None) -> dict:
        """下载 ETF 日K线（type=5），写入 daily_quote 表。"""
        import time
        from loguru import logger
        from app.db.connection import is_sqlite as _is_sql
        if not self.login():
            return {"rows": 0, "errors": 0}
        if db is None:
            from app.db.connection import get_sync_db
            db = get_sync_db()

        rs = bs.query_stock_basic()
        etf_list = []
        while rs.next():
            d = rs.get_row_data()
            if len(d) > 4 and d[4] == '5':  # 类型 '5' = ETF
                raw = d[0]
                for p in ('sh.', 'sz.', 'bj.'):
                    if raw.startswith(p):
                        code = raw[len(p):]
                        ex = {'sh.': 'SSE', 'sz.': 'SZSE', 'bj.': 'BSE'}[p]
                        break
                else:
                    code, ex = raw, 'SSE'
                etf_list.append((raw, code, d[1], ex))

        logger.info(f"ETF 列表: {len(etf_list)} 只")

        existing = set()
        try:
            rows = db.execute(text(
                "SELECT DISTINCT stock_code FROM daily_quote WHERE trade_date=:d"
            ), {"d": trade_date}).fetchall()
            existing = {r[0] for r in rows}
        except Exception:
            pass

        fields = 'date,open,high,low,close,volume,amount,turn'
        total_rows = 0
        errors = 0
        insert_buffer = []
        for bs_code, code, name, ex in etf_list:
            if code in existing:
                continue
            try:
                rs2 = self._bs.query_history_k_data_plus(
                    bs_code, fields, start_date=trade_date, end_date=trade_date,
                    frequency='d', adjustflag='3')
                while rs2.next():
                    d = rs2.get_row_data()
                    if d[0] != trade_date:
                        continue
                    insert_buffer.append((
                        d[0], ex, code, name,
                        float(d[1]) if d[1] else 0,
                        float(d[2]) if d[2] else 0,
                        float(d[3]) if d[3] else 0,
                        float(d[4]) if d[4] else 0,
                        int(float(d[5])) if d[5] else 0,
                        float(d[6]) if d[6] else 0,
                        float(d[7]) if d[7] else None,
                    ))
                    total_rows += 1
                time.sleep(0.2)
            except Exception as e:
                errors += 1
                logger.warning(f"  ETF {bs_code} 失败: {e}")

            if len(insert_buffer) >= 200:
                from crawler.baostock_crawler import BaostockCrawler as _BC
                _BC._batch_insert_rows(db, insert_buffer)
                insert_buffer = []

        if insert_buffer:
            from crawler.baostock_crawler import BaostockCrawler as _BC
            _BC._batch_insert_rows(db, insert_buffer)

        db.commit()
        logger.info(f"ETF 下载完成: {trade_date} -> {total_rows} 条, {errors} 错误")
        return {"rows": total_rows, "errors": errors}



# ── 便捷函数 ──

def download_history(start: str = "2021-01-01", end: str = None):
    """下载全量历史数据。"""
    c = BaostockCrawler()
    result = c.download_all_stocks(start, end)
    c.logout()
    return result


def download_daily(date_str: str = None):
    """每日增量下载。"""
    c = BaostockCrawler()
    d = date.fromisoformat(date_str) if date_str else date.today()
    result = c.download_daily_update(d)
    c.logout()
    return result
