"""盘中提醒纯逻辑单测（无网络；落库用 FakeDB）。

重点覆盖「错了会静默刷屏/漏报」的地方：
- 交易时段边界（午休/收盘/周末不评估）
- 规则优先级（同一只票一次 tick 只出一条；止损优先于急跌/新高）
- 日内高低首轮只建基线（启动时不误报历史高低点）
- N 分钟急涨急跌需要足够采样窗口（启动后前几分钟不误报）
- 冷却与单日/单轮上限（写入前过滤，而不是写完再删）
"""
from collections import deque
from datetime import datetime

from app import intraday_watch as iw

TODAY = '2026-11-12'
NOW = datetime(2026, 11, 12, 10, 0, 0)


def _q(code='600519', price=100.0, high=None, low=None, **over):
    q = {
        'code': code, 'name': '测试股', 'price': price, 'prev_close': 100.0,
        'open': 100.0,
        'high': price if high is None else high,
        'low': price if low is None else low,
        'change': price - 100.0, 'change_pct': (price / 100.0 - 1) * 100,
        'volume': 1000, 'amount': 1e6, 'turnover_pct': 1.0, 'volume_ratio': 1.0,
        'avg_price': price, 'limit_up': 110.0, 'limit_down': 90.0,
        'bids': [], 'asks': [],
        'timestamp': '2026-11-12 10:00:00', 'trade_date': TODAY,
    }
    q.update(over)
    return q


def _rules(**over):
    r = dict(iw.DEFAULT_CONFIG)
    r.update(over)
    return r


class TestSession:
    def test_in_session_boundaries(self):
        assert iw._in_session(datetime(2026, 11, 12, 9, 29)) is False
        assert iw._in_session(datetime(2026, 11, 12, 9, 30)) is True
        assert iw._in_session(datetime(2026, 11, 12, 11, 30)) is True
        assert iw._in_session(datetime(2026, 11, 12, 11, 31)) is False
        assert iw._in_session(datetime(2026, 11, 12, 12, 59)) is False
        assert iw._in_session(datetime(2026, 11, 12, 13, 0)) is True
        assert iw._in_session(datetime(2026, 11, 12, 15, 0)) is True
        assert iw._in_session(datetime(2026, 11, 12, 15, 1)) is False

    def test_weekend_closed(self):
        assert iw._in_session(datetime(2026, 11, 14, 10, 0)) is False

    def test_session_minutes(self):
        assert iw._session_minutes(datetime(2026, 11, 12, 9, 30)) == 0
        assert iw._session_minutes(datetime(2026, 11, 12, 10, 0)) == 30
        assert iw._session_minutes(datetime(2026, 11, 12, 11, 30)) == 120
        assert iw._session_minutes(datetime(2026, 11, 12, 13, 0)) == 120
        assert iw._session_minutes(datetime(2026, 11, 12, 15, 0)) == 240


class TestRules:
    def test_stop_loss(self):
        out = iw.evaluate_tick({'600519': _q(price=90.0)},
                               {}, {'600519': {'name': '贵州茅台', 'cost': 100.0}},
                               _rules(stop_loss_pct=8.0), NOW)
        assert len(out) == 1
        assert out[0]['kind'] == 'it_stop'
        assert out[0]['level'] == 'critical'
        assert '回撤 -10.00%' in out[0]['body']

    def test_take_profit(self):
        out = iw.evaluate_tick({'600519': _q(price=125.0)},
                               {}, {'600519': {'name': '贵州茅台', 'cost': 100.0}},
                               _rules(take_profit_pct=20.0), NOW)
        assert len(out) == 1 and out[0]['kind'] == 'it_take'
        assert '盈利 +25.00%' in out[0]['body']

    def test_limit_up_state_only_fires_once(self):
        state = {'600519': {'day': TODAY, 'high': 109.6, 'low': 100.0,
                            'vol_hot': False, 'limit_near': None,
                            'samples': deque()}}
        q = _q(price=109.6, high=109.6)
        out = iw.evaluate_tick({'600519': q}, state, {}, _rules(), NOW)
        assert [c['kind'] for c in out] == ['it_limit_up']
        out2 = iw.evaluate_tick({'600519': q}, state, {}, _rules(), NOW)
        assert out2 == []                 # 已在涨停附近，不重复报
        # 离开涨停附近后状态复位，其他规则恢复
        q2 = _q(price=105.0, high=109.6)
        iw.evaluate_tick({'600519': q2}, state, {}, _rules(), NOW)
        assert state['600519']['limit_near'] is None

    def test_rapid_surge_needs_window(self):
        now_ts = NOW.timestamp()
        state = {'600519': {'day': TODAY, 'high': 103.0, 'low': 100.0,
                            'vol_hot': False, 'limit_near': None,
                            'samples': deque([(now_ts - 300, 100.0)])}}
        out = iw.evaluate_tick({'600519': _q(price=103.0, high=103.0)},
                               state, {}, _rules(rapid_move_pct=2.0), NOW)
        assert [c['kind'] for c in out] == ['it_surge']

    def test_rapid_move_no_alert_when_window_too_short(self):
        now_ts = NOW.timestamp()
        state = {'600519': {'day': TODAY, 'high': 103.0, 'low': 100.0,
                            'vol_hot': False, 'limit_near': None,
                            'samples': deque([(now_ts - 60, 100.0)])}}
        out = iw.evaluate_tick({'600519': _q(price=103.0, high=103.0)},
                               state, {}, _rules(rapid_move_pct=2.0), NOW)
        assert out == []

    def test_rapid_drop(self):
        now_ts = NOW.timestamp()
        state = {'600519': {'day': TODAY, 'high': 100.0, 'low': 97.0,
                            'vol_hot': False, 'limit_near': None,
                            'samples': deque([(now_ts - 300, 100.0)])}}
        out = iw.evaluate_tick({'600519': _q(price=97.0, high=100.0, low=97.0)},
                               state, {}, _rules(rapid_move_pct=2.0), NOW)
        assert [c['kind'] for c in out] == ['it_drop']
        assert out[0]['level'] == 'critical'

    def test_new_high_on_first_tick_is_baseline_only(self):
        state = {}
        out = iw.evaluate_tick({'600519': _q(price=103.0, high=103.0)},
                               state, {}, _rules(), NOW)
        assert out == []
        assert state['600519']['high'] == 103.0

    def test_new_high_after_baseline(self):
        state = {'600519': {'day': TODAY, 'high': 100.0, 'low': 99.0,
                            'vol_hot': False, 'limit_near': None,
                            'samples': deque()}}
        out = iw.evaluate_tick({'600519': _q(price=103.0, high=103.0)},
                               state, {}, _rules(), NOW)
        assert [c['kind'] for c in out] == ['it_high']
        assert state['600519']['high'] == 103.0

    def test_micro_new_high_is_filtered(self):
        # 开盘 0.01 元级的新高不报，基线也不动；累计到 0.2% 才触发
        state = {'600519': {'day': TODAY, 'high': 100.0, 'low': 99.0,
                            'vol_hot': False, 'limit_near': None,
                            'samples': deque()}}
        out = iw.evaluate_tick({'600519': _q(price=100.1, high=100.1)},
                               state, {}, _rules(high_low_min_pct=0.2), NOW)
        assert out == []
        assert state['600519']['high'] == 100.0
        out2 = iw.evaluate_tick({'600519': _q(price=100.3, high=100.3)},
                                state, {}, _rules(high_low_min_pct=0.2), NOW)
        assert [c['kind'] for c in out2] == ['it_high']

    def test_volume_spike_only_on_cross(self):
        state = {}
        q = _q(price=100.0, volume_ratio=2.5)
        out = iw.evaluate_tick({'600519': q}, state, {}, _rules(volume_ratio=2.0), NOW)
        assert [c['kind'] for c in out] == ['it_vol']
        out2 = iw.evaluate_tick({'600519': q}, state, {}, _rules(volume_ratio=2.0), NOW)
        assert out2 == []

    def test_volume_spike_waits_15_minutes(self):
        early = datetime(2026, 11, 12, 9, 40)
        out = iw.evaluate_tick({'600519': _q(volume_ratio=3.0)},
                               {}, {}, _rules(volume_ratio=2.0,
                                              volume_ratio_min_minutes=15), early)
        assert out == []

    def test_one_alert_per_code_per_tick(self):
        # 同时满足止损 + 逼近跌停 + 急跌 → 只出优先级最高的止损
        now_ts = NOW.timestamp()
        state = {'600519': {'day': TODAY, 'high': 100.0, 'low': 90.0,
                            'vol_hot': False, 'limit_near': None,
                            'samples': deque([(now_ts - 300, 100.0)])}}
        out = iw.evaluate_tick({'600519': _q(price=90.0, high=100.0, low=90.0)},
                               state, {'600519': {'name': '测试股', 'cost': 100.0}},
                               _rules(), NOW)
        assert [c['kind'] for c in out] == ['it_stop']

    def test_skips_non_today_quote(self):
        out = iw.evaluate_tick({'600519': _q(price=90.0, trade_date='2026-11-11')},
                               {}, {'600519': {'cost': 100.0}}, _rules(), NOW)
        assert out == []


class _Result:
    def __init__(self, rows=None, scalar_val=None):
        self.rows = rows or []
        self.scalar_val = scalar_val

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def scalar(self):
        return self.scalar_val


class _FakeDB:
    def __init__(self, count=0, existing=None):
        self.count = count
        self.existing = set(existing or [])
        self.inserts = []
        self.checks = []
        self.committed = False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if 'count(*)' in sql:
            return _Result(scalar_val=self.count)
        if sql.strip().startswith('SELECT 1'):
            self.checks.append(dict(params))
            key = (params['c'], params['k'])
            return _Result(rows=[(1,)] if key in self.existing else [])
        if sql.strip().startswith('INSERT'):
            self.inserts.append(dict(params))
            return _Result()
        return _Result()

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class TestPersist:
    def _cand(self, code, kind, title='x'):
        return {'stock_code': code, 'kind': kind, 'level': 'warn',
                'title': title, 'body': 'b'}

    def test_cooldown_skips_existing(self, monkeypatch):
        fake = _FakeDB(count=0, existing={('600519', 'it_stop')})
        monkeypatch.setattr(iw, 'get_sync_db', lambda: fake)
        n = iw._persist([self._cand('600519', 'it_stop'),
                         self._cand('000001', 'it_high')],
                        _rules(max_per_tick=3), NOW)
        assert n == 1
        assert fake.inserts[0]['c'] == '000001'
        assert fake.committed is True

    def test_daily_cap(self, monkeypatch):
        fake = _FakeDB(count=30)
        monkeypatch.setattr(iw, 'get_sync_db', lambda: fake)
        assert iw._persist([self._cand('600519', 'it_high')],
                           _rules(max_alerts_per_day=30), NOW) == 0
        assert fake.inserts == []

    def test_tick_cap_sorted_by_priority(self, monkeypatch):
        fake = _FakeDB(count=0)
        monkeypatch.setattr(iw, 'get_sync_db', lambda: fake)
        cands = [self._cand('600000', 'it_vol'),
                 self._cand('600001', 'it_high'),
                 self._cand('600002', 'it_stop'),
                 self._cand('600003', 'it_surge')]
        assert iw._persist(cands, _rules(max_per_tick=2), NOW) == 2
        assert [i['k'] for i in fake.inserts] == ['it_stop', 'it_surge']

    def test_stop_cooldown_uses_240_minutes(self, monkeypatch):
        fake = _FakeDB(count=0)
        monkeypatch.setattr(iw, 'get_sync_db', lambda: fake)
        iw._persist([self._cand('600519', 'it_stop')], _rules(), NOW)
        assert fake.checks[0]['cut'] == NOW.replace(hour=6, minute=0)
