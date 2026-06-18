"""Baostock 数据源适配器。

从现有 BaostockCrawler 提取核心拉取逻辑，实现 DataSourceAdapter 接口。
保留原有重试机制（4次 + 指数退避 + relogin 检测）。
"""
import time
import random
from typing import List, Optional, Dict, Tuple

import baostock as bs
from loguru import logger

from crawler.adapters.base import (
    DataSourceAdapter, KlineRow, IndexKlineRow,
    FundamentalRow, StockInfo, code_to_exchange,
)

# ── 常量（与原 BaostockCrawler 保持一致）──
DELAY_MIN = 0.3
DELAY_MAX = 0.7
RETRY_MAX = 4
RETRY_BACKOFF = 3.0


class BaostockAdapter(DataSourceAdapter):
    """Baostock 数据源适配器（备选源，priority=20）。"""

    name = "baostock"
    priority = 20

    def __init__(self):
        self._logged_in = False

    # ── 登录 / 登出 ──

    def _login(self) -> bool:
        if self._logged_in:
            return True
        for attempt in range(5):
            lg = bs.login()
            if lg.error_code == '0':
                self._logged_in = True
                return True
            logger.warning(f"[baostock] 登录失败 (attempt {attempt+1}): {lg.error_msg}")
            time.sleep(5)
        logger.error("[baostock] 登录彻底失败")
        return False

    def _logout(self):
        if self._logged_in:
            bs.logout()
            self._logged_in = False

    def _ensure_login(self):
        if not self._logged_in:
            if not self._login():
                raise RuntimeError("baostock 登录失败")

    # ── DataSourceAdapter 接口：健康检查 ──

    def check_health(self) -> bool:
        try:
            lg = bs.login()
            ok = lg.error_code == '0'
            if ok:
                bs.logout()
            return ok
        except Exception:
            return False

    # ── 工具函数 ──

    @staticmethod
    def _bs_code(code: str) -> str:
        """A股股票代码 → baostock 格式。6xx→sh, 其他→sz"""
        if code.startswith('6'):
            return f"sh.{code}"
        return f"sz.{code}"

    @staticmethod
    def _bs_index_code(code: str) -> str:
        """指数代码 → baostock 格式。000xxx→sh(上交所), 399xxx→sz(深交所)"""
        if code.startswith('0'):
            return f"sh.{code}"
        elif code.startswith('3'):
            return f"sz.{code}"
        return f"sh.{code}"

    @staticmethod
    def _bs_etf_code(code: str) -> str:
        """ETF代码 → baostock 格式。5xxxxx→sh(沪市ETF), 1xxxxx→sz(深市ETF)"""
        if code.startswith('5'):
            return f"sh.{code}"
        elif code.startswith('1'):
            return f"sz.{code}"
        return f"sh.{code}"

    @staticmethod
    def _delay():
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    # ── 内部：单只股票K线拉取（含重试）──

    def _fetch_kline_single(self, code: str, start: str, end: str,
                            bs_code_override: str = None) -> Optional[List[list]]:
        """拉取单只股票/ETF K线，返回原始行列表或 None。

        每行格式: [date, open, high, low, close, volume, amount, turn, close_hfq]
        含 RETRY_MAX 次重试 + relogin 检测。

        Args:
            bs_code_override: 如指定则直接使用（用于 ETF 代码映射）
        """
        bs_code = bs_code_override or self._bs_code(code)
        for attempt in range(RETRY_MAX + 1):
            try:
                # 第1次 API：不复权 OHLCV
                rs = bs.query_history_k_data_plus(
                    bs_code, "date,open,high,low,close,volume,amount,turn",
                    start_date=start, end_date=end,
                    frequency="d", adjustflag="3")
                if rs.error_code != '0':
                    msg = rs.error_msg
                    if '未登录' in msg or 'login' in msg.lower():
                        self._logged_in = False
                        self._ensure_login()
                        continue
                    if attempt < RETRY_MAX:
                        time.sleep(RETRY_BACKOFF ** (attempt + 1))
                        continue
                    return None

                raw_rows = []
                while rs.next():
                    row = rs.get_row_data()
                    if len(row) >= 8 and row[0]:  # 过滤空行/残缺行
                        raw_rows.append(row)
                if not raw_rows:
                    return []

                # 第2次 API：后复权 close
                rs_hfq = bs.query_history_k_data_plus(
                    bs_code, "date,close",
                    start_date=start, end_date=end,
                    frequency="d", adjustflag="1")
                hfq_map = {}
                if rs_hfq.error_code == '0':
                    while rs_hfq.next():
                        d = rs_hfq.get_row_data()
                        if len(d) >= 2 and d[0] and d[1]:  # 过滤残缺行
                            hfq_map[d[0]] = d[1]

                # 合并
                rows = []
                for r in raw_rows:
                    close_hfq = hfq_map.get(r[0], r[4])
                    rows.append(r + [close_hfq])
                return rows

            except Exception as e:
                msg = str(e)
                if '未登录' in msg or 'login' in msg.lower():
                    self._logged_in = False
                    self._ensure_login()
                    continue
                if attempt < RETRY_MAX:
                    time.sleep(RETRY_BACKOFF ** (attempt + 1))
                    continue
                logger.warning(f"[baostock] {code} K线最终失败: {e}")
                return None
        return None

    # ── DataSourceAdapter 接口实现 ──

    def fetch_stock_kline(self, codes: List[str], start: str, end: str) -> List[KlineRow]:
        self._ensure_login()
        results = []
        for code in codes:
            raw = self._fetch_kline_single(code, start, end)
            if not raw:
                self._delay()
                continue
            exchange = code_to_exchange(code)
            for r in raw:
                try:
                    results.append(KlineRow(
                        trade_date=r[0],
                        stock_code=code,
                        stock_name="",  # 由调用方补充或从 stock_master 查
                        exchange=exchange,
                        open=float(r[1]) if r[1] else 0,
                        high=float(r[2]) if r[2] else 0,
                        low=float(r[3]) if r[3] else 0,
                        close=float(r[4]) if r[4] else 0,
                        close_hfq=float(r[8]) if r[8] else float(r[4]) if r[4] else 0,
                        volume=int(float(r[5])) if r[5] else 0,
                        amount=float(r[6]) if r[6] else 0,
                        turnover=float(r[7]) if r[7] else None,
                    ))
                except (ValueError, IndexError):
                    continue
            self._delay()
        return results

    def fetch_etf_kline(self, codes: List[str], start: str, end: str) -> List[KlineRow]:
        """拉取 ETF K线（使用 ETF 专用代码映射）。"""
        self._ensure_login()
        results = []
        for code in codes:
            raw = self._fetch_kline_single(code, start, end,
                                           bs_code_override=self._bs_etf_code(code))
            if not raw:
                self._delay()
                continue
            exchange = code_to_exchange(code)
            for r in raw:
                try:
                    results.append(KlineRow(
                        trade_date=r[0],
                        stock_code=code,
                        stock_name="",
                        exchange=exchange,
                        open=float(r[1]) if r[1] else 0,
                        high=float(r[2]) if r[2] else 0,
                        low=float(r[3]) if r[3] else 0,
                        close=float(r[4]) if r[4] else 0,
                        close_hfq=float(r[8]) if r[8] else float(r[4]) if r[4] else 0,
                        volume=int(float(r[5])) if r[5] else 0,
                        amount=float(r[6]) if r[6] else 0,
                        turnover=float(r[7]) if r[7] else None,
                    ))
                except (ValueError, IndexError):
                    continue
            self._delay()
        return results

    def fetch_index_kline(self, codes: List[str], start: str, end: str) -> List[IndexKlineRow]:
        self._ensure_login()
        results = []
        for code in codes:
            bs_code = self._bs_index_code(code)
            rows = self._fetch_index_single(code, bs_code, start, end)
            if not rows:
                self._delay()
                continue
            for d in rows:
                try:
                    results.append(IndexKlineRow(
                        trade_date=d[0],
                        index_code=code,
                        index_name="",
                        open=float(d[1]) if d[1] else 0,
                        high=float(d[2]) if d[2] else 0,
                        low=float(d[3]) if d[3] else 0,
                        close=float(d[4]) if d[4] else 0,
                        volume=int(float(d[5])) if d[5] else 0,
                        amount=float(d[6]) if d[6] else 0,
                    ))
                except (ValueError, IndexError):
                    continue
            self._delay()
        return results

    def _fetch_index_single(self, code: str, bs_code: str,
                             start: str, end: str) -> Optional[List[list]]:
        """拉取单只指数 K 线，含重试 + relogin 检测。
        返回原始行列表 [date, open, high, low, close, volume, amount] 或 None。
        """
        for attempt in range(RETRY_MAX + 1):
            try:
                rs = bs.query_history_k_data_plus(
                    bs_code, "date,open,high,low,close,volume,amount",
                    start_date=start, end_date=end,
                    frequency="d", adjustflag="3")
                if rs.error_code != '0':
                    msg = rs.error_msg
                    if '未登录' in msg or 'login' in msg.lower():
                        self._logged_in = False
                        self._ensure_login()
                        continue
                    if attempt < RETRY_MAX:
                        time.sleep(RETRY_BACKOFF ** (attempt + 1))
                        continue
                    logger.warning(f"[baostock] 指数 {code} ({bs_code}) 最终失败: {msg}")
                    return None

                raw_rows = []
                while rs.next():
                    row = rs.get_row_data()
                    if len(row) >= 7 and row[0]:
                        raw_rows.append(row)
                if not raw_rows:
                    return []
                return raw_rows

            except Exception as e:
                msg = str(e)
                if '未登录' in msg or 'login' in msg.lower():
                    self._logged_in = False
                    self._ensure_login()
                    continue
                if attempt < RETRY_MAX:
                    time.sleep(RETRY_BACKOFF ** (attempt + 1))
                    continue
                logger.warning(f"[baostock] 指数 {code} ({bs_code}) 最终异常: {e}")
                return None
        return None

    def fetch_fundamentals(self, codes: List[str], year: int = None,
                            quarter: int = None) -> List[FundamentalRow]:
        """拉取基本面数据。默认当前季度，可指定历史季度。"""
        self._ensure_login()
        from datetime import date
        results = []
        now = date.today()
        if year is None:
            year = now.year
        if quarter is None:
            quarter = (now.month - 1) // 3 + 1

        # 预加载行业映射（一次 API 调用，缓存给所有股票使用）
        industry_map = {}
        try:
            rs = bs.query_stock_industry()
            if rs.error_code == '0':
                while rs.next():
                    d = rs.get_row_data()
                    if d and len(d) >= 2:
                        # baostock 返回格式: [updateDate, code, code_name, industry, industry_type, ...]
                        raw_code = d[1] if len(d) > 1 else ''
                        ind_name = d[3] if len(d) > 3 else ''
                        for prefix in ('sh.', 'sz.', 'bj.'):
                            if raw_code.startswith(prefix):
                                code_6 = raw_code[len(prefix):]
                                industry_map[code_6] = ind_name
                                break
        except Exception:
            pass

        for code in codes:
            bs_code = self._bs_code(code)
            row = FundamentalRow(stock_code=code, stock_name="",
                                 industry=industry_map.get(code, None))
            # ROE / revenue_yoy / profit_yoy → 季度财报（需试多个季度）
            # 数据依赖：季度财报只在季度结束后才可用
            # 当前季度未结束时回退到前一季度
            candidates = [(year, quarter), (year, quarter - 1)]
            if quarter == 1:
                candidates = [(year, 1), (year - 1, 4)]
            for y, q in candidates:
                if row.roe is None:
                    try:
                        rs = bs.query_profit_data(code=bs_code, year=y, quarter=q)
                        if rs.error_code == '0' and rs.next():
                            d = rs.get_row_data()
                            if len(d) > 3 and d[3]:
                                row.roe = float(d[3]) * 100  # 小数 → %
                    except Exception:
                        pass
                if row.revenue_yoy is None or row.profit_yoy is None:
                    try:
                        rs = bs.query_growth_data(code=bs_code, year=y, quarter=q)
                        if rs.error_code == '0' and rs.next():
                            d = rs.get_row_data()
                            # d[0]=code, d[1]=publish_date, d[2]=report_date,
                            # d[3]=营业收入同比增长率, d[4]=净利润同比增长率
                            if len(d) > 3 and d[3]:
                                row.revenue_yoy = float(d[3]) * 100
                            if len(d) > 4 and d[4]:
                                row.profit_yoy = float(d[4]) * 100
                    except Exception:
                        pass
                if row.roe is not None and row.revenue_yoy is not None:
                    break
            # PE/PB → 取指定季度末日或最近有效值（含重试）
            from datetime import date as dt_date
            if year != now.year or quarter != ((now.month - 1) // 3 + 1):
                # 历史季度：用季度末日查询 PE/PB
                q_end = _quarter_end_date(year, quarter)
                pe, pb = self._fetch_pe_pb(bs_code, q_end)
            else:
                pe, pb = self._fetch_pe_pb(bs_code)
            row.pe_ttm = pe
            row.pb_mrq = pb
            results.append(row)
            self._delay()
        return results

    def _fetch_pe_pb(self, bs_code: str, end_date: str = None) -> tuple:
        """拉取 PE(TTM)/PB(MRQ)，含重试 + login 检测。
        返回 (pe_ttm, pb_mrq)，无数据返回 (None, None)。
        """
        from datetime import date as dt_date
        if end_date is None:
            today = dt_date.today()
            end_d = today.isoformat()
            start_d = (today - __import__('datetime').timedelta(days=5)).isoformat()
        else:
            end_d = end_date
            # 往前取 5 天窗口，确保有交易日数据
            start_d = (dt_date.fromisoformat(end_date) - __import__('datetime').timedelta(days=5)).isoformat()

        for attempt in range(RETRY_MAX + 1):
            try:
                rs = bs.query_history_k_data_plus(
                    bs_code, "date,peTTM,pbMRQ",
                    start_date=start_d, end_date=end_d,
                    frequency="d", adjustflag="3")
                if rs.error_code != '0':
                    msg = rs.error_msg
                    if '未登录' in msg or 'login' in msg.lower():
                        self._logged_in = False
                        self._ensure_login()
                        continue
                    if attempt < RETRY_MAX:
                        time.sleep(RETRY_BACKOFF ** (attempt + 1))
                        continue
                    return None, None

                last = None
                while rs.next():
                    last = rs.get_row_data()
                if last and len(last) >= 3:
                    pe = float(last[1]) if last[1] else None
                    pb = float(last[2]) if last[2] else None
                    return pe, pb
                return None, None
            except Exception as e:
                msg = str(e)
                if '未登录' in msg or 'login' in msg.lower():
                    self._logged_in = False
                    self._ensure_login()
                    continue
                if attempt < RETRY_MAX:
                    time.sleep(RETRY_BACKOFF ** (attempt + 1))
                    continue
                return None, None
        return None, None

    def get_stock_list(self, stock_type: str = "stock") -> List[StockInfo]:
        self._ensure_login()
        type_map = {"stock": "1", "index": "2", "etf": "5"}
        target_type = type_map.get(stock_type, "1")
        rs = bs.query_stock_basic()
        if rs.error_code != '0':
            raise RuntimeError(f"query_stock_basic 失败: {rs.error_msg}")
        results = []
        while rs.next():
            row = rs.get_row_data()
            sec_type = row[4] if len(row) > 4 else ''
            if sec_type != target_type:
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
            results.append(StockInfo(
                stock_code=code,
                stock_name=row[1] if len(row) > 1 else "",
                exchange=code_to_exchange(code),
                ipo_date=row[2] if len(row) > 2 and row[2] else None,
                status="D" if (len(row) > 3 and row[3]) else "N",
                stock_type=stock_type,
            ))
        return results

    def get_trade_calendar(self, start_year: int, end_year: int) -> List[dict]:
        self._ensure_login()
        results = []
        for year in range(start_year, end_year + 1):
            rs = bs.query_trade_dates(
                start_date=f"{year}-01-01",
                end_date=f"{year}-12-31")
            if rs.error_code != '0':
                continue
            while rs.next():
                d = rs.get_row_data()
                results.append({
                    "cal_date": d[0],
                    "is_trade_day": d[1] == '1',
                    "exchange": "SSE",
                })
        return results

    def __del__(self):
        """析构时自动登出。"""
        try:
            self._logout()
        except Exception:
            pass


def _quarter_end_date(year: int, quarter: int) -> str:
    end_month_day = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
    return f"{year}-{end_month_day[quarter]}"
