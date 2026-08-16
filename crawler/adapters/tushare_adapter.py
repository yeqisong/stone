"""TuShare 数据源适配器（主数据源，v3.2 架构简化）。

设计原则（2026-08 改造）：
- tushare 是唯一主源：daily / daily_basic / adj_factor / index_daily / fund_daily 均按交易日
  全市场拉取（各 1 次调用/天），天然覆盖退市股历史数据、停牌股自然缺失（行业标准）。
- 每次 API 调用经 TushareQuota.consume() 计数限流（50次/分, 8000次/天），
  配额耗尽抛 QuotaExhausted，由任务层提示用户。
- 交易日历来自本地 trade_calendar 表（baostock 唯一源同步），适配器不直接调 tushare 日历。
"""
import time
from datetime import date, timedelta
from typing import List, Optional, Dict
import pandas as pd
from loguru import logger

from crawler.adapters.base import (
    DataSourceAdapter, KlineRow, IndexKlineRow, FundamentalRow, StockInfo, code_to_exchange,
)
from crawler.adapters.tushare_quota import TushareQuota, QuotaExhausted

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
    priority = 5  # 主源

    def __init__(self):
        import tushare as ts
        from app.config import settings
        ts.set_token(settings.TUSHARE_TOKEN)
        self._pro = ts.pro_api()
        self._name_cache: Dict[str, str] = {}
        self.quota = TushareQuota.get()

    def _ensure_login(self):
        pass

    def _login(self) -> bool:
        return True

    def _logout(self):
        pass

    def check_health(self) -> bool:
        try:
            from datetime import timedelta
            td = (date.today() - timedelta(days=2)).strftime("%Y%m%d")
            _rl()
            self.quota.consume()
            df = self._pro.daily(ts_code='000001.SZ', start_date=td, end_date=td)
            return df is not None and len(df) > 0
        except QuotaExhausted:
            raise
        except Exception:
            return False

    # ── 本地交易日历（baostock 同步，唯一源）──

    @staticmethod
    def _trade_days(start: str, end: str) -> List[date]:
        """从本地 trade_calendar 查 [start, end] 区间交易日（倒序=最新优先）。"""
        from app.db.connection import get_sync_db
        from sqlalchemy import text
        db = get_sync_db()
        try:
            rows = db.execute(text(
                "SELECT DISTINCT cal_date FROM trade_calendar "
                "WHERE cal_date BETWEEN :s AND :e AND is_trade_day = true "
                "ORDER BY cal_date DESC"
            ), {"s": start, "e": end}).fetchall()
            return [r[0] for r in rows]
        except Exception as e:
            logger.warning(f"[tushare] 交易日历查询失败: {e}")
            return []
        finally:
            db.close()

    def get_stock_list(self, stock_type: str = "stock") -> List[StockInfo]:
        results = []
        try:
            if stock_type != "stock":
                return results
            # stock_basic 有1次/小时限制，返回空时降级到 daily
            df = None
            try:
                self.quota.consume()
                df = self._pro.stock_basic(exchange='', list_status='L',
                    fields='ts_code,name,list_date,delist_date,exchange,is_hs,act_name,area,industry')
            except QuotaExhausted:
                raise
            except Exception:
                pass

            if df is None or df.empty:
                # 降级到 daily（查最近交易日）
                from datetime import date, timedelta
                for back in range(1, 10):
                    td = (date.today() - timedelta(days=back)).strftime("%Y%m%d")
                    try:
                        self.quota.consume()
                        df = self._pro.daily(trade_date=td)
                        if df is not None and not df.empty:
                            break
                    except QuotaExhausted:
                        raise
                    except Exception:
                        continue
                else:
                    return results  # 10天都无数据，放弃

            seen = set()
            from_stock_basic = 'list_date' in df.columns
            for _, r in df.iterrows():
                raw = str(r['ts_code'])
                code = raw.split('.')[0].zfill(6)
                if code in seen or not code.isdigit():
                    continue
                seen.add(code)
                ex = 'SSE' if raw.endswith('.SH') else ('SZSE' if raw.endswith('.SZ') else '')
                if not from_stock_basic:
                    results.append(StockInfo(
                        stock_code=code, stock_name='', exchange=ex,
                        ipo_date=None, status='N', stock_type='stock'))
                else:
                    results.append(StockInfo(
                        stock_code=code, stock_name=str(r.get('name','')),
                        exchange=ex,
                        ipo_date=str(r['list_date']) if pd.notna(r.get('list_date')) else None,
                        status='D' if pd.notna(r.get('delist_date')) else 'N',
                        stock_type='stock',
                        delist_date=str(r['delist_date']) if pd.notna(r.get('delist_date')) else None,
                        is_hs=str(r.get('is_hs','')) if pd.notna(r.get('is_hs')) else None,
                        act_name=str(r.get('act_name','')) if pd.notna(r.get('act_name')) else None,
                        area=str(r.get('area','')) if pd.notna(r.get('area')) else None,
                        industry=str(r.get('industry','')) if pd.notna(r.get('industry')) else None))
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] get_stock_list 失败: {e}")
        return results

    @staticmethod
    def _ts_code(code: str) -> str:
        """股票代码 → tushare ts_code（含北交所 .BJ）。"""
        if code.startswith(('4', '8', '9', '92')):
            return f"{code}.BJ"
        return f"{code}.{'SH' if code.startswith('6') else 'SZ'}"

    # ── 按交易日全市场拉取：个股/ETF 日K线 ──

    def fetch_stock_kline(self, codes: List[str], start: str, end: str) -> List[KlineRow]:
        """按交易日逐日拉取全市场个股日K线（daily + daily_basic + adj_factor 各 1 次/天）。

        按日期拉天然覆盖退市股历史（退市前有交易即有数据）、停牌股自然缺失。
        codes 为兼容参数（可为空=全市场；非空则过滤）。
        """
        results = []
        code_set = set(codes) if codes else None
        for td in self._trade_days(start, end):
            td_str = td.strftime("%Y%m%d")
            try:
                self.quota.consume()
                df = self._pro.daily(trade_date=td_str)
                if df is None or df.empty:
                    continue
                # 换手率（daily_basic 按日全市场）
                self.quota.consume()
                try:
                    basic = self._pro.daily_basic(trade_date=td_str,
                        fields='ts_code,turnover_rate')
                except Exception as e:
                    logger.warning(f"[tushare] daily_basic {td_str} 失败: {e}")
                    basic = None
                # 复权因子（adj_factor 按日全市场；需积分，失败时 close_hfq=close）
                self.quota.consume()
                try:
                    adj = self._pro.adj_factor(trade_date=td_str)
                except Exception as e:
                    logger.warning(f"[tushare] adj_factor {td_str} 失败(close_hfq=close): {e}")
                    adj = None
                tr_map = {}
                if basic is not None and not basic.empty:
                    for _, r in basic.iterrows():
                        tr_map[str(r['ts_code']).split('.')[0].zfill(6)] = (
                            float(r['turnover_rate']) if pd.notna(r.get('turnover_rate')) else None)
                adj_map = {}
                if adj is not None and not adj.empty:
                    for _, r in adj.iterrows():
                        adj_map[str(r['ts_code']).split('.')[0].zfill(6)] = (
                            float(r['adj_factor']) if pd.notna(r.get('adj_factor')) else 1.0)
                for _, r in df.iterrows():
                    raw_code = str(r['ts_code'])
                    code = raw_code.split('.')[0].zfill(6)
                    if code_set is not None and code not in code_set:
                        continue
                    close = float(r['close'])
                    results.append(KlineRow(
                        trade_date=str(r['trade_date']), stock_code=code,
                        stock_name='', exchange=code_to_exchange(code),
                        open=float(r['open']), high=float(r['high']),
                        low=float(r['low']), close=close,
                        close_hfq=close * adj_map.get(code, 1.0),
                        volume=int(r['vol']) * 100 if pd.notna(r.get('vol')) else 0,
                        amount=float(r['amount']) * 1000 if pd.notna(r.get('amount')) else 0,
                        turnover=tr_map.get(code)))
            except QuotaExhausted:
                raise
            except Exception as e:
                logger.warning(f"[tushare] {td_str} K线失败: {e}")
        return results

    # ── 指数日K线（按交易日全市场）──

    def fetch_index_kline(self, codes, start, end):
        results = []
        code_set = set(codes) if codes else None
        for td in self._trade_days(start, end):
            td_str = td.strftime("%Y%m%d")
            try:
                self.quota.consume()
                df = self._pro.index_daily(trade_date=td_str)
                if df is not None and not df.empty:
                    for _, r in df.iterrows():
                        raw = str(r['ts_code'])
                        c = raw.split('.')[0].zfill(6)
                        if code_set is not None and c not in code_set:
                            continue
                        results.append(IndexKlineRow(
                            trade_date=str(r['trade_date']), index_code=c, index_name='',
                            open=float(r['open']), high=float(r['high']),
                            low=float(r['low']), close=float(r['close']),
                            volume=int(r['vol'])*100 if pd.notna(r.get('vol')) else 0,
                            amount=float(r['amount'])*1000 if pd.notna(r.get('amount')) else 0))
            except QuotaExhausted:
                raise
            except Exception as e:
                logger.warning(f"[tushare] 指数 {td_str} 失败: {e}")
        return results

    # ── ETF 日K线（按交易日全市场；复权因子 tushare 需 fund_adj 高积分 → close_hfq 由 baostock 补充）──

    def fetch_etf_kline(self, codes, start, end):
        results = []
        code_set = set(codes) if codes else None
        for td in self._trade_days(start, end):
            td_str = td.strftime("%Y%m%d")
            try:
                self.quota.consume()
                df = self._pro.fund_daily(trade_date=td_str)
                if df is not None and not df.empty:
                    for _, r in df.iterrows():
                        raw = str(r['ts_code'])
                        c = raw.split('.')[0].zfill(6)
                        if code_set is not None and c not in code_set:
                            continue
                        ex2 = 'SSE' if c.startswith('5') else 'SZSE'
                        results.append(KlineRow(
                            trade_date=str(r['trade_date']), stock_code=c, stock_name='',
                            exchange=ex2, open=float(r['open']), high=float(r['high']),
                            low=float(r['low']), close=float(r['close']), close_hfq=float(r['close']),
                            volume=int(r['vol'])*100 if pd.notna(r.get('vol')) else 0,
                            amount=float(r['amount'])*1000 if pd.notna(r.get('amount')) else 0))
            except QuotaExhausted:
                raise
            except Exception as e:
                logger.warning(f"[tushare] ETF {td_str} 失败: {e}")
        return results

    def fetch_fundamentals(self, codes: List[str]) -> List[FundamentalRow]:
        """用 daily_basic 按日获取全市场基本面（1 次/天）。"""
        results = []
        from datetime import date as _dt2, timedelta
        # 取最近交易日（从本地日历倒查，周一自动回退上周五）
        tds = self._trade_days((_dt2.today() - timedelta(days=10)).isoformat(),
                               _dt2.today().isoformat())
        if not tds:
            return results
        td = tds[0].strftime("%Y%m%d")
        code_set = set(codes) if codes else None
        try:
            self.quota.consume()
            df = self._pro.daily_basic(trade_date=td,
                fields='ts_code,trade_date,total_mv,circ_mv,total_share,float_share,free_share,pe_ttm,pe,pb,ps,ps_ttm,dv_ratio,dv_ttm,turnover_rate,volume_ratio,limit_status')
            if df is None or df.empty:
                return results
            for _, r in df.iterrows():
                raw_code = str(r['ts_code'])
                code = raw_code.split('.')[0].zfill(6)
                if code_set is not None and code not in code_set:
                    continue
                results.append(FundamentalRow(
                    stock_code=code, stock_name='',
                    trade_date=str(r.get('trade_date','')),
                    pe_ttm=float(r['pe_ttm']) if pd.notna(r.get('pe_ttm')) else None,
                    pe=float(r['pe']) if pd.notna(r.get('pe')) else None,
                    pb_mrq=float(r['pb']) if pd.notna(r.get('pb')) else None,
                    ps=float(r['ps']) if pd.notna(r.get('ps')) else None,
                    ps_ttm=float(r['ps_ttm']) if pd.notna(r.get('ps_ttm')) else None,
                    dv_ratio=float(r['dv_ratio']) if pd.notna(r.get('dv_ratio')) else None,
                    dv_ttm=float(r['dv_ttm']) if pd.notna(r.get('dv_ttm')) else None,
                    turnover_rate=float(r['turnover_rate']) if pd.notna(r.get('turnover_rate')) else None,
                    volume_ratio=float(r['volume_ratio']) if pd.notna(r.get('volume_ratio')) else None,
                    total_shares=int(r['total_share'] * 10000) if pd.notna(r.get('total_share')) else None,
                    float_share=int(r['float_share'] * 10000) if pd.notna(r.get('float_share')) else None,
                    free_share=int(r['free_share'] * 10000) if pd.notna(r.get('free_share')) else None,
                    market_cap=int(r['total_mv'] * 10000) if pd.notna(r.get('total_mv')) else None,
                    circ_mv=int(r['circ_mv'] * 10000) if pd.notna(r.get('circ_mv')) else None,
                    limit_status=int(r['limit_status']) if pd.notna(r.get('limit_status')) else None,
                ))
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] fundamentals {td} 失败: {e}")
        return results

    def fetch_top_list(self, trade_date: str) -> List[dict]:
        """龙虎榜。"""
        from datetime import date
        td = trade_date.replace('-', '')[:8]
        try:
            self.quota.consume()
            df = self._pro.top_list(trade_date=td)
            result = []
            if df is not None and not df.empty:
                for _, r in df.iterrows():
                    result.append({
                        "trade_date": str(r.get('trade_date',''))[:10],
                        "stock_code": str(r.get('ts_code','')).split('.')[0].zfill(6),
                        "stock_name": str(r.get('name','')),
                        "close": float(r.get('close',0)),
                        "pct_chg": float(r.get('pct_chg',0)),
                        "turnover_ratio": float(r.get('turnover_ratio',0)) if pd.notna(r.get('turnover_ratio')) else None,
                        "total_amount": float(r.get('amount',0)) if pd.notna(r.get('amount')) else None,
                        "buy_amount": float(r.get('buy',0)) if pd.notna(r.get('buy')) else None,
                        "sell_amount": float(r.get('sell',0)) if pd.notna(r.get('sell')) else None,
                        "net_amount": float(r.get('net_amount',0)) if pd.notna(r.get('net_amount')) else None,
                        "reason": str(r.get('reason','')),
                    })
            return result
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] top_list {td} 失败: {e}")
            return []

    def fetch_moneyflow(self, trade_date: str) -> List[dict]:
        """资金流向。"""
        td = trade_date.replace('-', '')[:8]
        try:
            self.quota.consume()
            df = self._pro.moneyflow(trade_date=td)
            result = []
            if df is not None and not df.empty:
                for _, r in df.iterrows():
                    result.append({
                        "trade_date": str(r.get('trade_date',''))[:10],
                        "stock_code": str(r.get('ts_code','')).split('.')[0].zfill(6),
                        "stock_name": str(r.get('name','')),
                        "buy_lg_amt": float(r.get('buy_lg_amt',0)) if pd.notna(r.get('buy_lg_amt')) else None,
                        "sell_lg_amt": float(r.get('sell_lg_amt',0)) if pd.notna(r.get('sell_lg_amt')) else None,
                        "buy_md_amt": float(r.get('buy_md_amt',0)) if pd.notna(r.get('buy_md_amt')) else None,
                        "sell_md_amt": float(r.get('sell_md_amt',0)) if pd.notna(r.get('sell_md_amt')) else None,
                        "buy_sm_amt": float(r.get('buy_sm_amt',0)) if pd.notna(r.get('buy_sm_amt')) else None,
                        "sell_sm_amt": float(r.get('sell_sm_amt',0)) if pd.notna(r.get('sell_sm_amt')) else None,
                        "net_mf_amt": float(r.get('net_mf_amt',0)) if pd.notna(r.get('net_mf_amt')) else None,
                    })
            return result
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] moneyflow {td} 失败: {e}")
            return []

    def fetch_hk_hold(self, trade_date: str) -> List[dict]:
        """沪深港通持股。"""
        td = trade_date.replace('-', '')[:8]
        try:
            self.quota.consume()
            df = self._pro.hk_hold(trade_date=td)
            result = []
            if df is not None and not df.empty:
                for _, r in df.iterrows():
                    result.append({
                        "trade_date": str(r.get('trade_date',''))[:10],
                        "stock_code": str(r.get('ts_code','')).split('.')[0].zfill(6),
                        "stock_name": str(r.get('name','')),
                        "vol": int(r.get('vol',0)) if pd.notna(r.get('vol')) else 0,
                        "amount": float(r.get('amount',0)) if pd.notna(r.get('amount')) else None,
                        "hold_ratio": float(r.get('ratio',0)) if pd.notna(r.get('ratio')) else None,
                    })
            return result
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] hk_hold {td} 失败: {e}")
            return []

    def fetch_margin_detail(self, trade_date: str) -> List[dict]:
        """融资融券明细。"""
        td = trade_date.replace('-', '')[:8]
        try:
            self.quota.consume()
            df = self._pro.margin_detail(trade_date=td)
            result = []
            if df is not None and not df.empty:
                for _, r in df.iterrows():
                    result.append({
                        "trade_date": str(r.get('trade_date',''))[:10],
                        "stock_code": str(r.get('ts_code','')).split('.')[0].zfill(6),
                        "stock_name": str(r.get('name','')),
                        "fin_amount": float(r.get('fin_amount',0)) if pd.notna(r.get('fin_amount')) else None,
                        "fin_buy_amount": float(r.get('fin_buy_amount',0)) if pd.notna(r.get('fin_buy_amount')) else None,
                        "sec_amount": float(r.get('sec_amount',0)) if pd.notna(r.get('sec_amount')) else None,
                        "sec_sell_amount": float(r.get('sec_sell_amount',0)) if pd.notna(r.get('sec_sell_amount')) else None,
                        "total_amount": float(r.get('total_amount',0)) if pd.notna(r.get('total_amount')) else None,
                    })
            return result
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] margin_detail {td} 失败: {e}")
            return []

    def fetch_holder_number(self, stock_code: str) -> List[dict]:
        """股东人数变化。"""
        try:
            self.quota.consume()
            df = self._pro.stk_holdernumber(ts_code=stock_code)
            result = []
            if df is not None and not df.empty:
                for _, r in df.iterrows():
                    result.append({
                        "stock_code": str(r.get('ts_code','')).split('.')[0].zfill(6),
                        "end_date": str(r.get('end_date',''))[:10],
                        "holder_num": int(r.get('holder_num',0)) if pd.notna(r.get('holder_num')) else None,
                        "change_pct": float(r.get('change_pct',0)) if pd.notna(r.get('change_pct')) else None,
                    })
            return result
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] holder_number {stock_code} 失败: {e}")
            return []

    def fetch_company(self, ts_code: str) -> Optional[dict]:
        """查询上市公司基本信息。"""
        try:
            self.quota.consume()
            df = self._pro.stock_company(ts_code=ts_code,
                fields='ts_code,reg_capital,employees,main_business')
            if df is not None and not df.empty:
                r = df.iloc[0]
                return {
                    "reg_capital": float(r['reg_capital']) if pd.notna(r.get('reg_capital')) else None,
                    "employees": int(r['employees']) if pd.notna(r.get('employees')) else None,
                    "main_business": str(r.get('main_business',''))[:200] if pd.notna(r.get('main_business')) else None,
                }
        except QuotaExhausted:
            raise
        except Exception:
            pass
        return None

    def get_trade_calendar(self, start_year: int, end_year: int) -> List[dict]:
        """交易日历统一由 baostock 同步（trade_calendar.py），此处不实现。"""
        return []
