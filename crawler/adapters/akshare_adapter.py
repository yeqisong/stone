"""AKShare 数据源适配器（首选源）。

数据来自东方财富公开接口，通过 AKShare 库调用。
完全免费，无需注册/Token。

参考文档：docs/akshare/api-reference.md
"""
import time
from datetime import date, timedelta
from typing import List, Optional, Dict

import akshare as ak
import pandas as pd
from loguru import logger

from crawler.adapters.base import (
    DataSourceAdapter, KlineRow, IndexKlineRow,
    FundamentalRow, StockInfo, code_to_exchange,
)

# ── 配置 ──
REQUEST_INTERVAL = 0.8  # 请求间隔（秒），防东方财富反爬


class AKShareAdapter(DataSourceAdapter):
    """AKShare 数据源适配器（首选源，priority=10）。"""

    name = "akshare"
    priority = 10

    def __init__(self):
        self._name_cache: Optional[Dict[str, str]] = None  # code → name

    # ── 工具函数 ──

    @staticmethod
    def _to_ak_date(date_str: str) -> str:
        """'2024-01-02' → '20240102'（AKShare 参数格式）"""
        return date_str.replace("-", "")

    @staticmethod
    def _index_symbol(code: str) -> str:
        """6位指数代码 → AKShare 格式 'sh000001' / 'sz399001'"""
        if code.startswith('0') or code.startswith('9'):
            return f"sh{code}"
        elif code.startswith('3'):
            return f"sz{code}"
        return f"sh{code}"

    def _get_name_cache(self) -> Dict[str, str]:
        """延迟加载股票名称缓存。"""
        if self._name_cache is None:
            try:
                df = ak.stock_info_a_code_name()
                self._name_cache = dict(zip(df["code"].astype(str).str.zfill(6), df["name"]))
                logger.info(f"[akshare] 名称缓存加载: {len(self._name_cache)} 只")
            except Exception as e:
                logger.warning(f"[akshare] 名称缓存加载失败: {e}")
                self._name_cache = {}
        return self._name_cache

    def _get_name(self, code: str) -> str:
        cache = self._get_name_cache()
        return cache.get(code, "")

    @staticmethod
    def _delay():
        time.sleep(REQUEST_INTERVAL)

    # ── DataSourceAdapter 接口 ──

    def check_health(self) -> bool:
        """轻量健康检查：尝试获取 000001 最近 1 天数据。"""
        try:
            today = date.today()
            start = (today - timedelta(days=7)).strftime("%Y%m%d")
            end = today.strftime("%Y%m%d")
            df = ak.stock_zh_a_hist(
                symbol="000001", period="daily",
                start_date=start, end_date=end, adjust=""
            )
            return df is not None and len(df) > 0
        except Exception:
            return False

    def fetch_stock_kline(self, codes: List[str], start: str, end: str) -> List[KlineRow]:
        """拉取个股日K线（双次调用：不复权 + 后复权）。"""
        ak_start = self._to_ak_date(start)
        ak_end = self._to_ak_date(end)
        results = []

        for code in codes:
            try:
                # 第1次：不复权 OHLCV
                df_raw = ak.stock_zh_a_hist(
                    symbol=code, period="daily",
                    start_date=ak_start, end_date=ak_end, adjust=""
                )
                if df_raw is None or df_raw.empty:
                    self._delay()
                    continue

                # 第2次：后复权收盘价
                df_hfq = ak.stock_zh_a_hist(
                    symbol=code, period="daily",
                    start_date=ak_start, end_date=ak_end, adjust="hfq"
                )
                hfq_map = {}
                if df_hfq is not None and not df_hfq.empty:
                    hfq_map = dict(zip(
                        df_hfq["日期"].astype(str),
                        df_hfq["收盘"]
                    ))

                name = self._get_name(code)
                exchange = code_to_exchange(code)

                for _, r in df_raw.iterrows():
                    trade_date = str(r["日期"])
                    close_raw = float(r["收盘"])
                    results.append(KlineRow(
                        trade_date=trade_date,
                        stock_code=code,
                        stock_name=name,
                        exchange=exchange,
                        open=float(r["开盘"]),
                        high=float(r["最高"]),
                        low=float(r["最低"]),
                        close=close_raw,
                        close_hfq=float(hfq_map.get(trade_date, close_raw)),
                        volume=int(r["成交量"]) * 100,    # ⚠️ 手→股
                        amount=float(r["成交额"]),
                        turnover=float(r["换手率"]) if pd.notna(r.get("换手率")) else None,
                    ))
            except Exception as e:
                logger.warning(f"[akshare] 个股 {code} K线失败: {e}")
            self._delay()

        return results

    def fetch_etf_kline(self, codes: List[str], start: str, end: str) -> List[KlineRow]:
        """拉取 ETF 日K线（双次调用：不复权 + 后复权）。"""
        ak_start = self._to_ak_date(start)
        ak_end = self._to_ak_date(end)
        results = []

        for code in codes:
            try:
                df_raw = ak.fund_etf_hist_em(
                    symbol=code, period="daily",
                    start_date=ak_start, end_date=ak_end, adjust=""
                )
                if df_raw is None or df_raw.empty:
                    self._delay()
                    continue

                df_hfq = ak.fund_etf_hist_em(
                    symbol=code, period="daily",
                    start_date=ak_start, end_date=ak_end, adjust="hfq"
                )
                hfq_map = {}
                if df_hfq is not None and not df_hfq.empty:
                    hfq_map = dict(zip(
                        df_hfq["日期"].astype(str),
                        df_hfq["收盘"]
                    ))

                exchange = code_to_exchange(code)

                for _, r in df_raw.iterrows():
                    trade_date = str(r["日期"])
                    close_raw = float(r["收盘"])
                    results.append(KlineRow(
                        trade_date=trade_date,
                        stock_code=code,
                        stock_name="",  # ETF 名称由调用方补充
                        exchange=exchange,
                        open=float(r["开盘"]),
                        high=float(r["最高"]),
                        low=float(r["最低"]),
                        close=close_raw,
                        close_hfq=float(hfq_map.get(trade_date, close_raw)),
                        volume=int(r["成交量"]) * 100,    # ⚠️ 手→股
                        amount=float(r["成交额"]),
                        turnover=float(r["换手率"]) if pd.notna(r.get("换手率")) else None,
                    ))
            except Exception as e:
                logger.warning(f"[akshare] ETF {code} K线失败: {e}")
            self._delay()

        return results

    def fetch_index_kline(self, codes: List[str], start: str, end: str) -> List[IndexKlineRow]:
        """拉取指数日K线。"""
        ak_start = self._to_ak_date(start)
        ak_end = self._to_ak_date(end)
        results = []

        for code in codes:
            symbol = self._index_symbol(code)
            try:
                df = ak.stock_zh_index_daily_em(
                    symbol=symbol,
                    start_date=ak_start,
                    end_date=ak_end
                )
                if df is None or df.empty:
                    self._delay()
                    continue

                for _, r in df.iterrows():
                    results.append(IndexKlineRow(
                        trade_date=str(r["date"]),
                        index_code=code,
                        index_name="",  # 由调用方补充
                        open=float(r["open"]),
                        high=float(r["high"]),
                        low=float(r["low"]),
                        close=float(r["close"]),
                        volume=int(r["volume"]) if pd.notna(r.get("volume")) else 0,
                        amount=float(r["amount"]) if pd.notna(r.get("amount")) else 0,
                    ))
            except Exception as e:
                logger.warning(f"[akshare] 指数 {code} K线失败: {e}")
            self._delay()

        return results

    def fetch_fundamentals(self, codes: List[str]) -> List[FundamentalRow]:
        """拉取基本面数据（PE/PB/总市值）。

        注意：AKShare stock_a_indicator_lg 不含 ROE/营收增长/净利增长，
        这些字段保留为 None，由 Manager 的混合策略从 Baostock 补充。
        """
        results = []
        for code in codes:
            row = FundamentalRow(
                stock_code=code,
                stock_name=self._get_name(code),
            )
            try:
                df = ak.stock_a_indicator_lg(symbol=code)
                if df is not None and not df.empty:
                    latest = df.iloc[-1]  # 取最新一行
                    if pd.notna(latest.get("pe_ttm")):
                        row.pe_ttm = float(latest["pe_ttm"])
                    if pd.notna(latest.get("pb")):
                        row.pb_mrq = float(latest["pb"])
                    if pd.notna(latest.get("total_mv")):
                        row.market_cap = int(float(latest["total_mv"]) * 10000)  # ⚠️ 万元→元
            except Exception as e:
                logger.warning(f"[akshare] 基本面 {code} 失败: {e}")
            results.append(row)
            self._delay()
        return results

    def get_stock_list(self, stock_type: str = "stock") -> List[StockInfo]:
        """获取 A 股列表。

        注意：stock_info_a_code_name 仅返回股票，不含指数/ETF。
        指数和 ETF 列表建议使用 Baostock（更完整）。
        """
        if stock_type != "stock":
            logger.info(f"[akshare] get_stock_list({stock_type}) 不支持，返回空列表")
            return []
        try:
            df = ak.stock_info_a_code_name()
            results = []
            for _, r in df.iterrows():
                code = str(r["code"]).zfill(6)
                results.append(StockInfo(
                    stock_code=code,
                    stock_name=str(r["name"]),
                    exchange=code_to_exchange(code),
                    stock_type="stock",
                ))
            return results
        except Exception as e:
            logger.warning(f"[akshare] get_stock_list 失败: {e}")
            return []

    def get_trade_calendar(self, start_year: int, end_year: int) -> List[dict]:
        """获取交易日历。

        AKShare 只返回交易日列表，需自行补全非交易日。
        生成 start_year-01-01 至 end_year-12-31 的完整日历。
        """
        try:
            df = ak.tool_trade_date_hist_sina()
            if df is None or df.empty:
                return []

            # 提取交易日集合
            trade_dates = set()
            for _, r in df.iterrows():
                d = str(r["trade_date"])[:10]  # 确保格式 "YYYY-MM-DD"
                trade_dates.add(d)

            # 生成完整日历
            results = []
            current = date(start_year, 1, 1)
            end = date(end_year, 12, 31)
            while current <= end:
                d_str = current.isoformat()
                results.append({
                    "cal_date": d_str,
                    "is_trade_day": d_str in trade_dates,
                    "exchange": "SSE",
                })
                current += timedelta(days=1)
            return results
        except Exception as e:
            logger.warning(f"[akshare] get_trade_calendar 失败: {e}")
            return []
