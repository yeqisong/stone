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
        """健康检查：取最近已收盘交易日（排除今天，tushare 当日数据 15:00 后才就绪），重试 3 次。

        tushare 限流时可能静默返回空，故失败重试确认。
        """
        from datetime import date, timedelta
        today = date.today()
        # 最近已收盘交易日 = (today-10, today-1)；凌晨/当日早间取昨日，避免拿到未就绪的当日数据
        tds = self._trade_days((today - timedelta(days=10)).isoformat(),
                               (today - timedelta(days=1)).isoformat())
        if not tds:
            return False
        td = tds[0].strftime("%Y%m%d")
        for attempt in range(3):
            try:
                _rl()
                self.quota.consume()
                df = self._pro.daily(ts_code='000001.SZ', start_date=td, end_date=td)
                if df is not None and len(df) > 0:
                    return True
                if attempt < 2:
                    time.sleep(2)  # 限流/瞬时空返回，稍后重试
            except QuotaExhausted:
                raise
            except Exception:
                if attempt < 2:
                    time.sleep(2)
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
                # 补退市股（list_status='D' 含 delist_date），供退市日期维护
                # 退市股可能超单次 6000 行限制，用 offset 分页拉全
                page_n = 0
                while True:
                    self.quota.consume()
                    df_d = self._pro.stock_basic(exchange='', list_status='D',
                        offset=page_n * 6000, limit=6000,
                        fields='ts_code,name,list_date,delist_date,exchange,is_hs,act_name,area,industry')
                    if df_d is None or df_d.empty:
                        break
                    df = pd.concat([df, df_d], ignore_index=True)
                    page_n += 1
                    if len(df_d) < 6000:
                        break
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
                adj_ok = adj is not None and not adj.empty
                if adj_ok:
                    for _, r in adj.iterrows():
                        adj_map[str(r['ts_code']).split('.')[0].zfill(6)] = (
                            float(r['adj_factor']) if pd.notna(r.get('adj_factor')) else 1.0)
                for _, r in df.iterrows():
                    raw_code = str(r['ts_code'])
                    code = raw_code.split('.')[0].zfill(6)
                    if code_set is not None and code not in code_set:
                        continue
                    close = float(r['close'])
                    # adj_factor 整体失败时 close_hfq 传 None（保留库内旧值）而非 close×1.0：
                    # 后者会经 UPSERT 覆盖掉此前正确的复权价，且当日因 80% 完整度阈值
                    # 判完成不再重拉，错误值永久化（v3.7 审查 P1）
                    results.append(KlineRow(
                        trade_date=str(r['trade_date']), stock_code=code,
                        stock_name='', exchange=code_to_exchange(code),
                        open=float(r['open']), high=float(r['high']),
                        low=float(r['low']), close=close,
                        close_hfq=close * adj_map.get(code, 1.0) if adj_ok else None,
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

    # ── ETF 日K线（按交易日全市场；复权因子同步 fund_adj(trade_date) 一次调用带上，
    #    close_hfq = close × adj_factor 自源头写入，替代原 baostock 补充（2026-09-05 切换）──

    def fetch_etf_kline(self, codes, start, end):
        results = []
        code_set = set(codes) if codes else None
        for td in self._trade_days(start, end):
            td_str = td.strftime("%Y%m%d")
            try:
                self.quota.consume()
                df = self._pro.fund_daily(trade_date=td_str)
                # 当日全市场复权因子：1 次调用，每日增量成本 +1 配额
                factors = {}
                try:
                    self.quota.consume()
                    fadj = self._pro.fund_adj(trade_date=td_str)
                    for _, fr in (fadj if fadj is not None and not fadj.empty else pd.DataFrame()).iterrows():
                        fc = str(fr['ts_code']).split('.')[0]
                        if len(fc) == 6 and pd.notna(fr.get('adj_factor')):
                            factors[fc.zfill(6)] = float(fr['adj_factor'])
                except QuotaExhausted:
                    raise
                except Exception as e:
                    logger.warning(f"[tushare] fund_adj {td_str} 失败: {e}")
                if df is not None and not df.empty:
                    for _, r in df.iterrows():
                        raw = str(r['ts_code'])
                        c = raw.split('.')[0]
                        # tushare 历史数据偶发 7 位脏代码（如 1618111），非合法标的且超 varchar(6)，跳过
                        if len(c) != 6:
                            continue
                        c = c.zfill(6)
                        if code_set is not None and c not in code_set:
                            continue
                        ex2 = 'SSE' if c.startswith('5') else 'SZSE'
                        # 复权价自源头计算；无因子的（脏码/漏调）传 None 走 UPSERT 空值保护，
                        # 缺口留给存量补充（_supplement_etf_hfq，tushare fund_adj 按代码优先）
                        factor = factors.get(c)
                        close_hfq = float(r['close']) * factor if factor else None
                        results.append(KlineRow(
                            trade_date=str(r['trade_date']), stock_code=c, stock_name='',
                            exchange=ex2, open=float(r['open']), high=float(r['high']),
                            low=float(r['low']), close=float(r['close']), close_hfq=close_hfq,
                            volume=int(r['vol'])*100 if pd.notna(r.get('vol')) else 0,
                            amount=float(r['amount'])*1000 if pd.notna(r.get('amount')) else 0))
            except QuotaExhausted:
                raise
            except Exception as e:
                logger.warning(f"[tushare] ETF {td_str} 失败: {e}")
        return results

    def fetch_etf_adj_history(self, ts_code: str, start: str, end: str):
        """单只 ETF 全历史复权因子（存量回补用）：{trade_date: adj_factor}，1 配额/只。"""
        td = start.replace('-', '')[:8]
        de = end.replace('-', '')[:8]
        try:
            self.quota.consume()
            df = self._pro.fund_adj(ts_code=ts_code, start_date=td, end_date=de)
            out = {}
            if df is not None and not df.empty:
                for _, r in df.iterrows():
                    if pd.notna(r.get('adj_factor')):
                        out[str(r['trade_date'])] = float(r['adj_factor'])
            return out
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] fund_adj {ts_code} 失败: {e}")
            return None

    def fetch_holder_history(self, ts_code: str, start: str, end: str):
        """股东户数（stk_holdernumber，公告制）单只全历史，1 配额/只。"""
        td = start.replace('-', '')[:8]
        de = end.replace('-', '')[:8]
        try:
            self.quota.consume()
            df = self._pro.stk_holdernumber(ts_code=ts_code, start_date=td, end_date=de)
            rows = self._rows(df, {"end_date": "end_date", "holder_num": "holder_num"})
            for r in rows:
                r["stock_code"] = ts_code.split('.')[0].zfill(6)
            return rows
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] stk_holdernumber {ts_code} 失败: {e}")
            return []

    def fetch_fundamentals(self, codes: List[str], trade_date: str = None) -> List[FundamentalRow]:
        """用 daily_basic 按日获取全市场基本面（1 次/天）。

        Args:
            codes: 过滤用（可空=全市场）
            trade_date: 指定交易日 YYYY-MM-DD（PE 历史回填用，缺省取最近交易日）
        """
        results = []
        from datetime import date as _dt2, timedelta
        if trade_date:
            tds = [__import__('datetime').date.fromisoformat(trade_date)]
        else:
            # 取最近交易日（本地日历倒查，倒序=最新优先）；
            # 当日数据可能未生成（盘中/补数场景），需逐日回退到有数据的交易日
            tds = self._trade_days((_dt2.today() - timedelta(days=10)).isoformat(),
                                   _dt2.today().isoformat())
        if not tds:
            return results
        code_set = set(codes) if codes else None
        for td_candidate in tds[:3]:
            td = td_candidate.strftime("%Y%m%d")
            try:
                self.quota.consume()
                df = self._pro.daily_basic(trade_date=td,
                    fields='ts_code,trade_date,total_mv,circ_mv,total_share,float_share,free_share,pe_ttm,pe,pb,ps,ps_ttm,dv_ratio,dv_ttm,turnover_rate,volume_ratio,limit_status')
                if df is None or df.empty:
                    logger.info(f"[tushare] fundamentals {td} 无数据，回退前一交易日")
                    continue
                break
            except QuotaExhausted:
                raise
            except Exception as e:
                logger.warning(f"[tushare] fundamentals {td} 失败: {e}")
                continue
        else:
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
                    # tushare 真实列为 *_amount 风格（lg=大单, elg=特大单, md=中单, sm=小单）
                    def _f(*keys):
                        for k in keys:
                            val = r.get(k)
                            if val is not None and pd.notna(val):
                                return float(val)
                        return None
                    result.append({
                        "trade_date": str(r.get('trade_date',''))[:10],
                        "stock_code": str(r.get('ts_code','')).split('.')[0].zfill(6),
                        "stock_name": str(r.get('name','')),
                        "buy_lg_amt": _f('buy_lg_amount', 'buy_lg_amt'),
                        "sell_lg_amt": _f('sell_lg_amount', 'sell_lg_amt'),
                        "buy_elg_amt": _f('buy_elg_amount'),
                        "sell_elg_amt": _f('sell_elg_amount'),
                        "buy_md_amt": _f('buy_md_amount', 'buy_md_amt'),
                        "sell_md_amt": _f('sell_md_amount', 'sell_md_amt'),
                        "buy_sm_amt": _f('buy_sm_amount', 'buy_sm_amt'),
                        "sell_sm_amt": _f('sell_sm_amount', 'sell_sm_amt'),
                        "net_mf_amt": _f('net_mf_amount', 'net_mf_amt'),
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
        """查询单只上市公司基本信息（reg_capital/employees/main_business）。"""
        return self.fetch_company_batch([ts_code]).get(ts_code)

    def fetch_company_batch(self, codes: List[str]) -> Dict[str, dict]:
        """逐只批量查询公司基本信息（stock_company 需单项调用）。

        Returns:
            {stock_code: {"reg_capital", "employees", "main_business"}}
        """
        result: Dict[str, dict] = {}
        for code in codes:
            try:
                self.quota.consume()
                df = self._pro.stock_company(
                    ts_code=self._ts_code(code) if code.startswith(('4', '8', '9')) else f"{code}.{'SH' if code.startswith('6') else 'SZ'}",
                    fields='ts_code,reg_capital,employees,main_business')
                if df is not None and not df.empty:
                    r = df.iloc[0]
                    result[code] = {
                        "reg_capital": float(r['reg_capital']) if pd.notna(r.get('reg_capital')) else None,
                        "employees": int(r['employees']) if pd.notna(r.get('employees')) else None,
                        "main_business": str(r.get('main_business', ''))[:200] if pd.notna(r.get('main_business')) else None,
                    }
            except QuotaExhausted:
                raise
            except Exception:
                pass
        return result

    def get_trade_calendar(self, start_year: int, end_year: int) -> List[dict]:
        """交易日历统一由 baostock 同步（trade_calendar.py），此处不实现。"""
        return []

    def fetch_sw_industry(self) -> Dict[str, Dict[str, str]]:
        """拉取申万行业层级（index_member_all，需 2000 积分）。

        Returns:
            {stock_code: {"l1": "汽车", "l2": "摩托车及其他"}}
        """
        result: Dict[str, Dict[str, str]] = {}
        offset = 0
        page_size = 3000
        try:
            while True:
                self.quota.consume()
                df = self._pro.index_member_all(offset=offset, limit=page_size)
                if df is None or df.empty:
                    break
                for _, r in df.iterrows():
                    code = str(r.get('ts_code', '')).split('.')[0].zfill(6)
                    if not code.isdigit():
                        continue
                    result[code] = {
                        'l1': str(r.get('l1_name') or '') if pd.notna(r.get('l1_name')) else '',
                        'l2': str(r.get('l2_name') or '') if pd.notna(r.get('l2_name')) else '',
                    }
                if len(df) < page_size:
                    break
                offset += page_size
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] fetch_sw_industry 失败: {e}")
        return result

    # ── 拓展数据接入（step2 扩展：13 接口，字段映射按 tushare 真实返回列）──

    def _rows(self, df, mapping):
        """通用行映射：df + {输出键: tushare 列} → list[dict]，缺列/NaN → None。"""
        out = []
        if df is None or df.empty:
            return out
        for _, r in df.iterrows():
            d = {}
            for k, col in mapping.items():
                v = r.get(col)
                if v is None or pd.isna(v):
                    d[k] = None
                elif isinstance(v, str):
                    d[k] = v
                else:
                    d[k] = float(v)
            out.append(d)
        return out

    def fetch_top_list(self, trade_date: str) -> list:
        """龙虎榜（每日榜单，同票可因多个 reason 上榜）。"""
        td = trade_date.replace('-', '')[:8]
        try:
            self.quota.consume()
            df = self._pro.top_list(trade_date=td)
            rows = self._rows(df, {
                "trade_date": "trade_date", "stock_code": "ts_code", "stock_name": "name",
                "close": "close", "pct_chg": "pct_change", "turnover_ratio": "turnover_rate",
                "total_amount": "amount", "buy_amount": "l_buy", "sell_amount": "l_sell",
                "net_amount": "net_amount", "reason": "reason"})
            for r in rows:
                r["trade_date"] = td
                r["stock_code"] = (r.get("stock_code") or '').split('.')[0].zfill(6)
            return rows
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] top_list {td} 失败: {e}")
            return []

    def fetch_margin_detail_ext(self, trade_date: str) -> list:
        """两融明细（全标的）。列名沿用 stock_margin_detail 表：fin_amount=rzye 等。"""
        td = trade_date.replace('-', '')[:8]
        try:
            self.quota.consume()
            df = self._pro.margin_detail(trade_date=td)
            rows = self._rows(df, {
                "trade_date": "trade_date", "stock_code": "ts_code", "stock_name": "name",
                "fin_amount": "rzye", "fin_buy_amount": "rzmre",
                "sec_amount": "rqye", "sec_sell_amount": "rqmcl", "total_amount": "rzrqye"})
            for r in rows:
                r["trade_date"] = td
                r["stock_code"] = (r.get("stock_code") or '').split('.')[0].zfill(6)
            return rows
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] margin_detail {td} 失败: {e}")
            return []

    def fetch_moneyflow_hsgt(self, trade_date: str) -> list:
        """沪深港通资金流向（北向整体，每日一行）。"""
        td = trade_date.replace('-', '')[:8]
        try:
            self.quota.consume()
            df = self._pro.moneyflow_hsgt(start_date=td, end_date=td)
            rows = self._rows(df, {
                "trade_date": "trade_date", "ggt_ss": "ggt_ss", "ggt_sz": "ggt_sz",
                "hgt": "hgt", "sgt": "sgt", "north_money": "north_money"})
            for r in rows:
                r["trade_date"] = td
            return rows
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] moneyflow_hsgt {td} 失败: {e}")
            return []

    def fetch_block_trade(self, trade_date: str) -> list:
        """大宗交易。"""
        td = trade_date.replace('-', '')[:8]
        try:
            self.quota.consume()
            df = self._pro.block_trade(trade_date=td)
            rows = self._rows(df, {
                "trade_date": "trade_date", "stock_code": "ts_code", "price": "price",
                "vol": "amount", "amount": "turnover", "buyer": "buyer", "seller": "seller"})
            for r in rows:
                r["trade_date"] = td
                r["stock_code"] = (r.get("stock_code") or '').split('.')[0].zfill(6)
            return rows
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] block_trade {td} 失败: {e}")
            return []

    def fetch_share_float(self, trade_date: str) -> list:
        """限售解禁（按解禁日扫描）。"""
        td = trade_date.replace('-', '')[:8]
        try:
            self.quota.consume()
            df = self._pro.share_float(trade_date=td)
            rows = self._rows(df, {
                "stock_code": "ts_code", "ann_date": "ann_date", "float_date": "float_date",
                "holder_name": "holder_name", "shares": "shares",
                "float_ratio": "float_ratio", "holder_type": "holder_type"})
            for r in rows:
                r["stock_code"] = (r.get("stock_code") or '').split('.')[0].zfill(6)
                r["holder_name"] = r.get("holder_name") or ''
                for k in ('ann_date', 'float_date'):
                    if r.get(k):
                        r[k] = str(r[k])[:10]
            return rows
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] share_float {td} 失败: {e}")
            return []

    def fetch_repurchase(self, trade_date: str) -> list:
        """回购（按公告日扫描）。"""
        td = trade_date.replace('-', '')[:8]
        try:
            self.quota.consume()
            df = self._pro.repurchase(ann_date=td)
            rows = self._rows(df, {
                "stock_code": "ts_code", "ann_date": "ann_date", "end_date": "end_date",
                "proc": "proc", "vol": "vol", "amount": "amount",
                "high_limit": "high_limit", "low_limit": "low_limit"})
            for r in rows:
                r["stock_code"] = (r.get("stock_code") or '').split('.')[0].zfill(6)
                r["vol"] = r.get("vol") if r.get("vol") is not None else -1
                r["amount"] = r.get("amount") if r.get("amount") is not None else -1
                for k in ('ann_date', 'end_date'):
                    if r.get(k):
                        r[k] = str(r[k])[:10]
            return rows
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] repurchase {td} 失败: {e}")
            return []

    def fetch_dividend(self, trade_date: str) -> list:
        """分红送转（按除权除息日扫描，2000 积分下不支持纯 period 全市场查询）。"""
        td = trade_date.replace('-', '')[:8]
        try:
            self.quota.consume()
            df = self._pro.dividend(ex_date=td)
            rows = self._rows(df, {
                "stock_code": "ts_code", "end_date": "end_date", "div_proc": "div_proc",
                "ann_date": "ann_date", "stk_div": "stk_div", "cash_div": "cash_div",
                "cash_div_tax": "cash_div_tax", "record_date": "record_date",
                "ex_date": "ex_date", "pay_date": "pay_date", "base_share": "base_share"})
            for r in rows:
                r["stock_code"] = (r.get("stock_code") or '').split('.')[0].zfill(6)
                for k in ('end_date', 'ann_date', 'record_date', 'ex_date', 'pay_date'):
                    if r.get(k):
                        r[k] = str(r[k])[:10]
            return rows
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] dividend ex={td} 失败: {e}")
            return []

    def fetch_forecast(self, trade_date: str) -> list:
        """业绩预告（按公告日扫描）。"""
        td = trade_date.replace('-', '')[:8]
        try:
            self.quota.consume()
            df = self._pro.forecast(ann_date=td)
            rows = self._rows(df, {
                "stock_code": "ts_code", "ann_date": "ann_date", "end_date": "end_date",
                "type": "type", "p_change_min": "p_change_min", "p_change_max": "p_change_max",
                "net_profit_min": "net_profit_min", "net_profit_max": "net_profit_max",
                "last_parent_net": "last_parent_net", "reason": "reason"})
            for r in rows:
                r["stock_code"] = (r.get("stock_code") or '').split('.')[0].zfill(6)
                r["ann_date"] = td
                if r.get("end_date"):
                    r["end_date"] = str(r["end_date"])[:10]
            return rows
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] forecast {td} 失败: {e}")
            return []

    def fetch_express(self, trade_date: str) -> list:
        """业绩快报（按公告日扫描）。"""
        td = trade_date.replace('-', '')[:8]
        try:
            self.quota.consume()
            df = self._pro.express(ann_date=td)
            rows = self._rows(df, {
                "stock_code": "ts_code", "ann_date": "ann_date", "end_date": "end_date",
                "revenue": "revenue", "or_yoy": "or_yoy", "netprofit": "netprofit",
                "yoy_net_profit": "yoy_net_profit", "bps": "bps", "total_assets": "total_assets"})
            for r in rows:
                r["stock_code"] = (r.get("stock_code") or '').split('.')[0].zfill(6)
                r["ann_date"] = td
                if r.get("end_date"):
                    r["end_date"] = str(r["end_date"])[:10]
            return rows
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] express {td} 失败: {e}")
            return []

    def fetch_income_ann(self, trade_date: str) -> list:
        """按公告日扫描利润表——仅用于发现当日披露财报的股票（ts_code + end_date）。"""
        td = trade_date.replace('-', '')[:8]
        try:
            self.quota.consume()
            df = self._pro.income(ann_date=td)
            out = []
            if df is not None and not df.empty:
                seen = set()
                for _, r in df.iterrows():
                    code = str(r.get('ts_code', '')).split('.')[0].zfill(6)
                    end = str(r.get('end_date', ''))[:10]
                    if code not in seen:
                        seen.add(code)
                        out.append({"stock_code": code, "end_date": end})
            return out
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] income ann={td} 失败: {e}")
            return []

    def fetch_fina_indicator(self, ts_code: str, period: str = None) -> list:
        """财务指标精选字段（period=None 返回该票全部报告期历史——代码轮换回补用）。"""
        try:
            # 兼容裸 6 位代码：tushare 必须带交易所后缀，否则静默返回空
            if '.' not in ts_code:
                ts_code = self._ts_code(ts_code)
            self.quota.consume()
            df = (self._pro.fina_indicator(ts_code=ts_code, period=period)
                  if period else self._pro.fina_indicator(ts_code=ts_code))
            rows = self._rows(df, {
                "stock_code": "ts_code", "ann_date": "ann_date", "end_date": "end_date",
                "eps": "eps", "eps_ttm": "eps_ttm", "bps": "bps",
                "roe": "roe", "roe_waa": "roe_waa", "roe_dt": "roe_dt", "roa": "roa",
                "grossprofit_margin": "grossprofit_margin", "netprofit_margin": "netprofit_margin",
                "ocf_to_or": "ocf_to_or", "ocfps": "ocfps", "profit_dedt": "profit_dedt",
                "debt_to_assets": "debt_to_assets", "current_ratio": "current_ratio",
                "quick_ratio": "quick_ratio", "invturn": "invturn", "ar_turn": "ar_turn",
                "assets_turn": "assets_turn", "netprofit_yoy": "netprofit_yoy",
                "netprofit_2yoy": "netprofit_2yoy", "or_yoy": "or_yoy", "or_2yoy": "or_2yoy"})
            for r in rows:
                r["stock_code"] = ts_code.split('.')[0].zfill(6)
                if period:
                    r["end_date"] = period
                elif r.get("end_date"):
                    r["end_date"] = str(r["end_date"])[:10]
                if r.get("ann_date"):
                    r["ann_date"] = str(r["ann_date"])[:10]
            return rows
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] fina_indicator {ts_code} {period} 失败: {e}")
            return []

    def fetch_index_weight(self, index_code: str, start_date: str, end_date: str) -> list:
        """指数成分权重（月频快照）。"""
        sd, ed = start_date.replace('-', '')[:8], end_date.replace('-', '')[:8]
        try:
            self.quota.consume()
            df = self._pro.index_weight(index_code=index_code, start_date=sd, end_date=ed)
            rows = self._rows(df, {
                "index_code": "index_code", "trade_date": "trade_date",
                "stock_code": "con_code", "weight": "weight"})
            for r in rows:
                r["stock_code"] = (r.get("stock_code") or '').split('.')[0].zfill(6)
                if r.get("trade_date"):
                    r["trade_date"] = str(r["trade_date"])[:10]
            return rows
        except QuotaExhausted:
            raise
        except Exception as e:
            logger.warning(f"[tushare] index_weight {index_code} 失败: {e}")
            return []
