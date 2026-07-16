"""TuShare 数据源适配器（第三备选源）。

日K线免费，限制：50次/分钟，8000次/天。
"""
import time
from datetime import date, timedelta
from typing import List, Optional, Dict
import pandas as pd
from loguru import logger

from crawler.adapters.base import (
    DataSourceAdapter, KlineRow, IndexKlineRow, FundamentalRow, StockInfo, code_to_exchange,
)

_LAST_CALL = 0.0
_RATE_LIMIT = 1.3  # 秒（50/min = 1.2s一次，留余量）


def _rl():
    global _LAST_CALL
    elapsed = time.time() - _LAST_CALL
    if elapsed < _RATE_LIMIT:
        time.sleep(_RATE_LIMIT - elapsed)
    _LAST_CALL = time.time()


class TuShareAdapter(DataSourceAdapter):
    name = "tushare"
    priority = 10  # 低于 baostock(20) 和 akshare(10)

    def __init__(self):
        import os
        import tushare as ts
        from app.config import settings
        os.environ["HOME"] = "/tmp"
        ts.set_token(settings.TUSHARE_TOKEN)
        self._pro = ts.pro_api()
        self._name_cache: Dict[str, str] = {}

    def _ensure_login(self):
        pass

    def _login(self) -> bool:
        return True

    def _logout(self):
        pass

    def check_health(self) -> bool:
        try:
            _rl()
            df = self._pro.daily(ts_code='000001.SZ', start_date='20260714', end_date='20260714')
            return df is not None and len(df) > 0
        except Exception:
            return False

    def get_stock_list(self, stock_type: str = "stock") -> List[StockInfo]:
        results = []
        try:
            if stock_type != "stock":
                return results
            # stock_basic 有1次/小时限制，优先尝试，失败降级到 daily
            _rl()
            try:
                df = self._pro.stock_basic(exchange='', list_status='L',
                    fields='ts_code,name,list_date,delist_date')
                use_daily = False
            except Exception:
                use_daily = True

            if use_daily:
                td = (date.today() - timedelta(days=3)).strftime("%Y%m%d")
                _rl()
                df = self._pro.daily(trade_date=td)

            if df is None or df.empty:
                return results
            seen = set()
            for _, r in df.iterrows():
                raw = str(r['ts_code'])
                code = raw.split('.')[0].zfill(6)
                if code in seen or not code.isdigit():
                    continue
                seen.add(code)
                ex = 'SSE' if raw.endswith('.SH') else ('SZSE' if raw.endswith('.SZ') else '')
                results.append(StockInfo(
                    stock_code=code, stock_name='', exchange=ex,
                    ipo_date=None, status='N', stock_type='stock'))
        except Exception as e:
            logger.warning(f"[tushare] get_stock_list 失败: {e}")
        return results

    def fetch_stock_kline(self, codes: List[str], start: str, end: str) -> List[KlineRow]:
        results = []

        # 智能分发：全市场+短区间→按日期，否则按股票
        from datetime import date as _dt
        sd = _dt.fromisoformat(start)
        ed = _dt.fromisoformat(end)
        day_count = (ed - sd).days

        if len(codes) > 500 and day_count <= 5:
            # 按日期循环（1 次/天取全市场）
            td = sd
            code_set = set(codes)
            while td <= ed:
                td_str = td.strftime("%Y%m%d")
                try:
                    _rl()
                    df = self._pro.daily(trade_date=td_str)
                    if df is not None and not df.empty:
                        for _, r in df.iterrows():
                            raw_code = str(r['ts_code'])
                            code = raw_code.split('.')[0].zfill(6)
                            if code not in code_set:
                                continue
                            ex = 'SSE' if raw_code.endswith('.SH') else 'SZSE'
                            results.append(KlineRow(
                                trade_date=str(r['trade_date']), stock_code=code,
                                stock_name='', exchange=ex,
                                open=float(r['open']), high=float(r['high']),
                                low=float(r['low']), close=float(r['close']),
                                close_hfq=float(r['close']),
                                volume=int(r['vol']) * 100 if pd.notna(r.get('vol')) else 0,
                                amount=float(r['amount']) * 1000 if pd.notna(r.get('amount')) else 0,
                                turnover=None,
                            ))
                except Exception as e:
                    logger.warning(f"[tushare] 日期 {td_str} K线失败: {e}")
                td += __import__('datetime').timedelta(days=1)
        else:
            # 按股票循环（跨年/少量股票）
            for c in codes:
                ex = '.SH' if c.startswith('6') else '.SZ'
                try:
                    _rl()
                    df = self._pro.daily(ts_code=f"{c}{ex}",
                                         start_date=start.replace('-', ''),
                                         end_date=end.replace('-', ''))
                    if df is None or df.empty:
                        continue
                    for _, r in df.iterrows():
                        results.append(KlineRow(
                            trade_date=str(r['trade_date']), stock_code=c,
                            stock_name='', exchange='SSE' if ex == '.SH' else 'SZSE',
                            open=float(r['open']), high=float(r['high']),
                            low=float(r['low']), close=float(r['close']),
                            close_hfq=float(r['close']),
                            volume=int(r['vol']) * 100 if pd.notna(r.get('vol')) else 0,
                            amount=float(r['amount']) * 1000 if pd.notna(r.get('amount')) else 0,
                            turnover=None,
                        ))
                except Exception as e:
                    logger.warning(f"[tushare] {c} K线失败: {e}")
        return results

    def fetch_index_kline(self, codes, start, end):
        results = []
        for c in codes:
            try:
                from datetime import date
                _rl()
                ex = '.SH' if c.startswith('0') or c.startswith('9') else '.SZ'
                df = self._pro.index_daily(ts_code=f"{c}{ex}",
                    start_date=start.replace('-',''), end_date=end.replace('-',''))
                if df is None or df.empty: continue
                for _, r in df.iterrows():
                    results.append(IndexKlineRow(
                        trade_date=str(r['trade_date']), index_code=c, index_name='',
                        open=float(r['open']), high=float(r['high']),
                        low=float(r['low']), close=float(r['close']),
                        volume=int(r['vol'])*100 if pd.notna(r.get('vol')) else 0,
                        amount=float(r['amount'])*1000 if pd.notna(r.get('amount')) else 0))
            except Exception as e:
                logger.warning(f"[tushare] 指数 {c} K线失败: {e}")
        return results

    def fetch_etf_kline(self, codes, start, end):
        results = []
        for c in codes:
            try:
                _rl()
                ex = '.SH' if c.startswith('5') else '.SZ'
                df = self._pro.fund_daily(ts_code=f"{c}{ex}",
                    start_date=start.replace('-',''), end_date=end.replace('-',''))
                if df is None or df.empty: continue
                code = c.zfill(6); ex2 = 'SSE' if c.startswith('5') else 'SZSE'
                for _, r in df.iterrows():
                    results.append(KlineRow(
                        trade_date=str(r['trade_date']), stock_code=code, stock_name='',
                        exchange=ex2, open=float(r['open']), high=float(r['high']),
                        low=float(r['low']), close=float(r['close']), close_hfq=float(r['close']),
                        volume=int(r['vol'])*100 if pd.notna(r.get('vol')) else 0,
                        amount=float(r['amount'])*1000 if pd.notna(r.get('amount')) else 0))
            except Exception as e:
                logger.warning(f"[tushare] ETF {c} K线失败: {e}")
        return results

    def fetch_fundamentals(self, codes: List[str]) -> List:
        return []

    def get_trade_calendar(self, start_year: int, end_year: int) -> List[dict]:
        return []
