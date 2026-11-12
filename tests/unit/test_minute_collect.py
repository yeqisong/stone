"""P3 分钟 bar 合成单测（纯内存，无 DB/网络）。

重点：
- 同一分钟多快照合并成 OHLC，分钟切换才闭合上一根
- 成交量/成交额用「下一分钟首个累计值 - 本分钟首个累计值」计算
- 分钟缺口 >1 直接丢弃，避免把跨分钟的量价记到一根 bar
- 非当日快照（盘前/停牌）不建 bar
"""
from datetime import datetime

import pytest

from app import intraday_watch as iw

TODAY = '2026-11-12'


def _q(code='600519', price=10.0, volume=1000, amount=1_000_000.0, **over):
    q = {
        'code': code, 'price': price, 'trade_date': TODAY,
        'volume': volume, 'amount': amount, 'avg_price': price,
        'volume_ratio': 1.0,
    }
    q.update(over)
    return q


@pytest.fixture(autouse=True)
def _clear_bars():
    iw._bar_state.clear()
    yield
    iw._bar_state.clear()


class TestCollectBars:
    def test_ohlc_and_volume_delta(self):
        t0 = datetime(2026, 11, 12, 9, 30, 5)
        assert iw._collect_bars({'600519': _q(price=10.0, volume=1000)}, t0) == []
        iw._collect_bars({'600519': _q(price=10.1, volume=1200)}, t0.replace(second=15))
        iw._collect_bars({'600519': _q(price=9.9, volume=1300)}, t0.replace(second=55))
        rows = iw._collect_bars({'600519': _q(price=10.05, volume=1400,
                                              amount=1_400_000.0)},
                                datetime(2026, 11, 12, 9, 31, 5))
        assert len(rows) == 1
        b = rows[0]
        assert b['bar_time'] == datetime(2026, 11, 12, 9, 30)
        assert (b['open'], b['high'], b['low'], b['close']) == (10.0, 10.1, 9.9, 9.9)
        assert b['volume'] == 400                  # 1400 - 1000
        assert b['amount'] == 400_000.0            # 1.4e6 - 1e6
        assert b['snapshots'] == 3
        # 09:31 新 bar 已建立
        assert iw._bar_state['600519']['bar_time'] == datetime(2026, 11, 12, 9, 31)

    def test_gap_discards_stale_bar(self):
        iw._collect_bars({'600519': _q()}, datetime(2026, 11, 12, 9, 30, 5))
        rows = iw._collect_bars({'600519': _q(price=10.2, volume=2000)},
                                datetime(2026, 11, 12, 9, 33, 5))
        assert rows == []                          # 缺口 3 分钟，09:30 那根丢弃
        assert iw._bar_state['600519']['bar_time'] == datetime(2026, 11, 12, 9, 33)

    def test_non_today_quote_ignored(self):
        rows = iw._collect_bars({'600519': _q(trade_date='2026-11-11')},
                                datetime(2026, 11, 12, 9, 30, 5))
        assert rows == []
        assert iw._bar_state == {}

    def test_missing_volume_yields_none(self):
        iw._collect_bars({'600519': _q(volume=None, amount=None)},
                         datetime(2026, 11, 12, 9, 30, 5))
        rows = iw._collect_bars({'600519': _q(price=10.1, volume=None, amount=None)},
                                datetime(2026, 11, 12, 9, 31, 5))
        assert rows[0]['volume'] is None and rows[0]['amount'] is None

    def test_flush_closes_pending_and_clears(self):
        iw._collect_bars({'600519': _q(price=10.0, volume=1000)},
                         datetime(2026, 11, 12, 14, 59, 5))
        iw._collect_bars({'600519': _q(price=10.2, volume=1100)},
                         datetime(2026, 11, 12, 14, 59, 55))
        rows = iw._flush_bars()
        assert len(rows) == 1
        r = rows[0]
        assert r['bar_time'] == datetime(2026, 11, 12, 14, 59)
        assert (r['open'], r['high'], r['low'], r['close']) == (10.0, 10.2, 10.0, 10.2)
        assert r['volume'] == 100                  # 1100 - 1000
        assert iw._bar_state == {}
