"""腾讯实时快照纯逻辑单测（无 DB / 无网络）。

覆盖三处「错了不会报错、只会静默显示错价」的地方：
- 证券前缀（指数 000300 必须走 sh，个股 000300 若存在才是 sz）
- 快照字段解析（金额单位万元→元、停牌 0 价退回昨收、涨跌缺失时反算）
- 交易时段判定（收盘后腾讯 timestamp 仍会走，不能只看 age 判实时）
"""
from datetime import datetime

import pytest

from crawler.adapters import tencent_adapter as ta
from app.api.stock import _quote_session


def _mk_line(symbol='sh600000', code='600000', over=None):
    """构造一行 88 字段的腾讯快照（字段位置与真实接口一致）。"""
    f = [''] * 88
    f[0] = '1'
    f[1] = '测试股'
    f[2] = code
    f[3] = '10.50'
    f[4] = '10.00'
    f[5] = '10.10'
    f[6] = '12345'
    f[7] = '6000'
    f[8] = '6345'
    for i in range(5):
        f[9 + 2 * i] = f'{10.49 - i * 0.01:.2f}'
        f[10 + 2 * i] = str(100 + i)
        f[19 + 2 * i] = f'{10.51 + i * 0.01:.2f}'
        f[20 + 2 * i] = str(200 + i)
    f[30] = '20261111103000'
    f[31] = '0.50'
    f[32] = '5.00'
    f[33] = '10.60'
    f[34] = '9.90'
    f[36] = '12345'
    f[37] = '1234.5'          # 万元
    f[38] = '1.23'
    f[39] = '15.60'
    f[43] = '7.00'
    f[44] = '100.50'
    f[45] = '200.50'
    f[46] = '3.20'
    f[47] = '11.00'
    f[48] = '9.00'
    f[49] = '1.80'
    f[51] = '10.20'
    for idx, val in (over or {}).items():
        f[idx] = val
    return f'v_{symbol}="' + '~'.join(f) + '";'


class _Resp:
    def __init__(self, text):
        self.content = text.encode('gbk')


class TestQuoteSymbol:
    def test_stock_prefix(self):
        assert ta._quote_symbol('600519') == 'sh600519'
        assert ta._quote_symbol('000001') == 'sz000001'
        assert ta._quote_symbol('300750') == 'sz300750'
        assert ta._quote_symbol('510300') == 'sh510300'
        assert ta._quote_symbol('159915') == 'sz159915'

    def test_index_prefix(self):
        assert ta._quote_symbol('000300', 'index') == 'sh000300'
        assert ta._quote_symbol('399006', 'index') == 'sz399006'
        assert ta._quote_symbol('899050', 'index') == 'bj899050'

    def test_bj_stock_supported_for_quote(self):
        # 快照支持北交所；分钟线不支持，两者不要互相推导
        assert ta._quote_symbol('830799') == 'bj830799'

    def test_invalid(self):
        with pytest.raises(ValueError):
            ta._quote_symbol('abc')


class TestParseQuoteLine:
    def test_full_fields(self):
        q = ta._parse_quote_line(_mk_line())
        assert q['symbol'] == 'sh600000'
        assert q['code'] == '600000'
        assert q['name'] == '测试股'
        assert q['price'] == 10.50
        assert q['prev_close'] == 10.00
        assert q['change'] == 0.50
        assert q['change_pct'] == 5.00
        assert q['volume'] == 12345
        assert q['amount'] == 1234.5 * 1e4
        assert q['turnover_pct'] == 1.23
        assert q['limit_up'] == 11.00
        assert q['limit_down'] == 9.00
        assert q['avg_price'] == 10.20
        assert q['timestamp'] == '2026-11-11 10:30:00'
        assert q['trade_date'] == '2026-11-11'
        assert len(q['bids']) == 5 and len(q['asks']) == 5
        assert q['bids'][0] == {'price': 10.49, 'volume': 100}
        assert q['asks'][0] == {'price': 10.51, 'volume': 200}

    def test_change_backfilled_when_source_omits(self):
        q = ta._parse_quote_line(_mk_line(over={31: '', 32: ''}))
        assert q['change'] == pytest.approx(0.50)
        assert q['change_pct'] == pytest.approx(5.00)

    def test_suspended_zero_price_falls_back_to_prev_close(self):
        q = ta._parse_quote_line(_mk_line(over={3: '0.00'}))
        assert q['price'] == 10.00

    def test_index_limit_prices_are_none(self):
        q = ta._parse_quote_line(_mk_line(symbol='sh000300', code='000300',
                                          over={47: '-1', 48: '-1'}))
        assert q['limit_up'] is None and q['limit_down'] is None

    def test_empty_and_short_lines(self):
        assert ta._parse_quote_line('') is None
        assert ta._parse_quote_line('v_sh600000="";') is None
        assert ta._parse_quote_line('v_sh600000="1~x";') is None


class TestRealtimeQuotes:
    def test_batch_and_amount_units(self, monkeypatch):
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            return _Resp(_mk_line() + '\n' + _mk_line('sz000001', '000001'))

        monkeypatch.setattr(ta.requests, 'get', fake_get)
        out = ta.realtime_quotes(['600000', '000001'])
        assert set(out) == {'sh600000', 'sz000001'}
        assert 'q=sh600000,sz000001' in calls[0]
        assert out['sz000001']['price'] == 10.50

    def test_host_fallback(self, monkeypatch):
        def fake_get(url, **kwargs):
            # 注意不能用 'qt.gtimg.cn' 判：sqt.gtimg.cn 也包含该子串
            if '//qt.gtimg.cn' in url:
                raise ta.requests.RequestException('down')
            return _Resp(_mk_line())

        monkeypatch.setattr(ta.requests, 'get', fake_get)
        assert ta.realtime_quote('600000')['symbol'] == 'sh600000'

    def test_all_hosts_fail_raises(self, monkeypatch):
        def fake_get(url, **kwargs):
            raise ta.requests.RequestException('down')

        monkeypatch.setattr(ta.requests, 'get', fake_get)
        with pytest.raises(RuntimeError):
            ta.realtime_quotes(['600000'])

    def test_missing_code_raises(self, monkeypatch):
        monkeypatch.setattr(ta.requests, 'get', lambda url, **kw: _Resp(_mk_line()))
        with pytest.raises(RuntimeError):
            ta.realtime_quote('000002')


class TestQuoteSession:
    def test_trading(self):
        assert _quote_session('2026-11-11 09:59:30',
                              datetime(2026, 11, 11, 10, 0, 0)) == ('盘中', True, 30)

    def test_auction(self):
        assert _quote_session('2026-11-11 09:20:00',
                              datetime(2026, 11, 11, 9, 20, 0)) == ('集合竞价', True, 0)

    def test_lunch_break(self):
        # 11:30 后腾讯 timestamp 可能继续走，但必须判非实时
        assert _quote_session('2026-11-11 11:30:00',
                              datetime(2026, 11, 11, 12, 0, 0)) == ('午间休市', False, 1800)

    def test_after_close(self):
        assert _quote_session('2026-11-11 15:00:00',
                              datetime(2026, 11, 11, 16, 0, 0)) == ('已收盘', False, 3600)

    def test_before_open(self):
        assert _quote_session('2026-11-11 08:00:00',
                              datetime(2026, 11, 11, 8, 0, 0)) == ('未开盘', False, 0)

    def test_previous_day(self):
        assert _quote_session('2026-11-10 15:00:00',
                              datetime(2026, 11, 11, 10, 0, 0))[0] == '已收盘'
        assert _quote_session('2026-11-10 10:00:00',
                              datetime(2026, 11, 11, 10, 0, 0))[0] == '非实时'

    def test_stale_during_session_is_not_live(self):
        assert _quote_session('2026-11-11 09:50:00',
                              datetime(2026, 11, 11, 10, 0, 0))[1] is False

    def test_bad_timestamp(self):
        assert _quote_session(None) == ('未知', False, None)
        assert _quote_session('bad') == ('未知', False, None)
