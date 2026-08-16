"""Baostock 数据源补充器（v3.2 架构简化）。

角色定位（不再是独立数据源，仅补 tushare 缺失字段）：
- fetch_fundamentals_extra: 补 stock_fundamentals 的 roe/revenue_yoy/profit_yoy
  （tushare daily_basic 无此 3 字段，需 fina_indicator 更高积分）
- fetch_etf_hfq: 补 daily_quote 的 ETF 后复权 close_hfq
  （tushare fund_adj 接口积分要求高）
- get_trade_calendar: 交易日历唯一同步源（trade_calendar.py 使用）

其余 K 线/列表/基本面主数据全部由 tushare 提供。
baostock 不可用时：主字段照常入库（tushare），缺口留待状态页补数修复。
"""
import time
import random
from typing import List, Optional, Dict

import baostock as bs
from loguru import logger

from crawler.adapters.base import (
    DataSourceAdapter, KlineRow, IndexKlineRow,
    FundamentalRow, StockInfo,
)

DELAY_MIN = 0.3
DELAY_MAX = 0.7


class BaostockAdapter(DataSourceAdapter):
    """Baostock 字段补充器（单 TCP 连接，必须串行调用）。"""

    name = "baostock"
    priority = 20  # 补充器（不再参与主数据源 fallback）

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

    def check_health(self) -> bool:
        try:
            lg = bs.login()
            ok = lg.error_code == '0'
            if ok:
                bs.logout()
                self._logged_in = False
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

    # ── 补充 1：基本面 ROE/营收/净利 ──

    def fetch_fundamentals_extra(self, codes: List[str]) -> List[FundamentalRow]:
        """补充 roe/revenue_yoy/profit_yoy（baostock 季度财报）。

        Returns:
            每只股票一个 FundamentalRow，仅这 3 个字段有值，其余为 None。
            由调用方按 COALESCE 语义合并进 stock_fundamentals。
        """
        from datetime import date
        self._ensure_login()
        results = []
        today = date.today()
        candidates = [(today.year - 1, 4), (today.year, 1)]
        for code in codes:
            bs_code = self._bs_code(code)
            res = {"roe": None, "revenue_yoy": None, "profit_yoy": None}
            try:
                for year, quarter in candidates:
                    if res["roe"] is None:
                        try:
                            r = bs.query_profit_data(code=bs_code, year=year, quarter=quarter)
                            if r.error_code == '0':
                                while r.next():
                                    rr = r.get_row_data()
                                    if len(rr) > 3 and rr[3]:
                                        try: res["roe"] = float(rr[3]) * 100
                                        except: pass
                        except: pass
                    if res["revenue_yoy"] is None or res["profit_yoy"] is None:
                        try:
                            r = bs.query_growth_data(code=bs_code, year=year, quarter=quarter)
                            if r.error_code == '0':
                                while r.next():
                                    rr = r.get_row_data()
                                    if res["revenue_yoy"] is None and len(rr) > 7 and rr[7]:
                                        try: res["revenue_yoy"] = float(rr[7]) * 100
                                        except: pass
                                    if res["profit_yoy"] is None and len(rr) > 5 and rr[5]:
                                        try: res["profit_yoy"] = float(rr[5]) * 100
                                        except: pass
                        except: pass
                    if all(v is not None for v in res.values()):
                        break
            except Exception as e:
                logger.warning(f"[baostock] 基本面补充 {code} 失败: {e}")
            results.append(FundamentalRow(
                stock_code=code, stock_name='',
                roe=res["roe"], revenue_yoy=res["revenue_yoy"], profit_yoy=res["profit_yoy"]))
            self._delay()
        return results

    # ── 补充 2：ETF 后复权收盘价 ──

    def fetch_etf_hfq(self, codes: List[str], start: str, end: str) -> Dict[str, Dict[str, float]]:
        """补充 ETF 后复权 close（adjustflag=1）。

        Returns:
            {(code, trade_date): close_hfq}
        """
        self._ensure_login()
        result: Dict[str, Dict[str, float]] = {}
        for code in codes:
            bs_code = self._bs_etf_code(code)
            try:
                r = bs.query_history_k_data_plus(
                    bs_code, "date,close",
                    start_date=start, end_date=end,
                    frequency="d", adjustflag="1")
                if r.error_code == '0':
                    while r.next():
                        rr = r.get_row_data()
                        if len(rr) >= 2 and rr[1]:
                            result[(code, rr[0])] = float(rr[1])
            except Exception as e:
                logger.warning(f"[baostock] ETF 复权补充 {code} 失败: {e}")
            self._delay()
        return result

    # ── 接口兼容（主数据由 tushare 提供，此处不实现）──

    def fetch_stock_kline(self, codes, start, end) -> List[KlineRow]:
        return []

    def fetch_etf_kline(self, codes, start, end) -> List[KlineRow]:
        return []

    def fetch_index_kline(self, codes, start, end) -> List[IndexKlineRow]:
        return []

    def fetch_fundamentals(self, codes, year=None, quarter=None) -> List[FundamentalRow]:
        return []

    def get_stock_list(self, stock_type: str = "stock") -> List[StockInfo]:
        return []

    def get_trade_calendar(self, start_year: int, end_year: int) -> List[dict]:
        """交易日历唯一同步源（trade_calendar.py 调用）。"""
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
        try:
            self._logout()
        except Exception:
            pass
