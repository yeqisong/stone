"""盘中提醒纯逻辑单测（无网络；落库用 FakeDB）。

重点覆盖「错了会静默刷屏/漏报」的地方：
- 交易时段边界（午休/收盘/周末不评估）
- 规则优先级（同一只票一次 tick 只出一条；止损优先于急跌/新高）
- 日内高低首轮只建基线（启动时不误报历史高低点）
- N 分钟急涨急跌需要足够采样窗口（启动后前几分钟不误报）
- 冷却与单日/单轮上限（写入前过滤，而不是写完再删）
"""
from collections import deque
from datetime import date, datetime

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


class TestVwapBreak:
    def test_up_break_after_baseline(self):
        state = {}
        out = iw.evaluate_tick({'600519': _q(price=99.5, avg_price=100.0)},
                               state, {}, _rules(), NOW)
        assert out == []
        assert state['600519']['vwap_side'] == -1
        out = iw.evaluate_tick({'600519': _q(price=100.2, avg_price=100.0)},
                               state, {}, _rules(), NOW)
        assert [c['kind'] for c in out] == ['it_vwap_up']

    def test_down_break_after_baseline(self):
        state = {}
        iw.evaluate_tick({'600519': _q(price=100.5, avg_price=100.0)},
                         state, {}, _rules(), NOW)
        assert state['600519']['vwap_side'] == 1
        out = iw.evaluate_tick({'600519': _q(price=99.8, avg_price=100.0)},
                               state, {}, _rules(), NOW)
        assert [c['kind'] for c in out] == ['it_vwap_down']

    def test_buffer_filters_micro_cross(self):
        state = {}
        iw.evaluate_tick({'600519': _q(price=100.05, avg_price=100.0)},
                         state, {}, _rules(), NOW)
        out = iw.evaluate_tick({'600519': _q(price=100.08, avg_price=100.0)},
                               state, {}, _rules(), NOW)
        assert out == []
        # 走出缓冲后仍能从 0 侧触发
        out = iw.evaluate_tick({'600519': _q(price=100.15, avg_price=100.0)},
                               state, {}, _rules(), NOW)
        assert [c['kind'] for c in out] == ['it_vwap_up']


class TestOrBreak:
    T1 = datetime(2026, 11, 12, 9, 35)
    T2 = datetime(2026, 11, 12, 9, 40)
    T3 = datetime(2026, 11, 12, 9, 46)

    def _seed(self, state):
        iw.evaluate_tick({'600519': _q(price=100.0, high=101.0, low=99.0,
                                       avg_price=None)},
                         state, {}, _rules(rapid_move_pct=10), self.T1)
        iw.evaluate_tick({'600519': _q(price=100.0, high=101.5, low=98.5,
                                       avg_price=None)},
                         state, {}, _rules(rapid_move_pct=10), self.T2)
        assert state['600519']['or_ready'] is False

    def test_break_up_after_window(self):
        state = {}
        self._seed(state)
        out = iw.evaluate_tick({'600519': _q(price=101.8, high=101.8, low=98.5,
                                             avg_price=None)},
                               state, {}, _rules(rapid_move_pct=10), self.T3)
        assert [c['kind'] for c in out] == ['it_or_up']
        assert state['600519']['or_ready'] is True
        # 同一侧不重复触发
        out2 = iw.evaluate_tick({'600519': _q(price=101.9, high=101.9, low=98.5,
                                              avg_price=None)},
                                state, {}, _rules(rapid_move_pct=10), self.T3)
        assert [c['kind'] for c in out2 if c['kind'].startswith('it_or')] == []

    def test_break_down_after_window(self):
        state = {}
        self._seed(state)
        out = iw.evaluate_tick({'600519': _q(price=98.2, high=101.5, low=98.2,
                                             avg_price=None)},
                               state, {}, _rules(rapid_move_pct=10), self.T3)
        assert [c['kind'] for c in out] == ['it_or_down']

    def test_no_break_before_window_ends(self):
        state = {}
        iw.evaluate_tick({'600519': _q(price=100.0, high=101.0, low=99.0,
                                       avg_price=None)},
                         state, {}, _rules(rapid_move_pct=10), self.T1)
        out = iw.evaluate_tick({'600519': _q(price=101.8, high=101.8, low=99.0,
                                             avg_price=None)},
                               state, {}, _rules(rapid_move_pct=10), self.T2)
        assert [c for c in out if c['kind'].startswith('it_or')] == []

    def test_late_start_has_no_or_levels(self):
        state = {}
        out = iw.evaluate_tick({'600519': _q(price=101.8, high=101.8, low=99.0,
                                             avg_price=None)},
                               state, {}, _rules(rapid_move_pct=10), NOW)
        assert [c for c in out if c['kind'].startswith('it_or')] == []
        assert state['600519']['or_unavailable'] is True

    def test_or_accumulates_even_when_stop_fires(self):
        # 止损规则会 continue，但 OR 区间必须在规则前累计，否则前 15 分钟丢样本
        state = {}
        costs = {'600519': {'cost': 100.0}}
        out = iw.evaluate_tick({'600519': _q(price=90.0, high=101.0, low=89.0,
                                             avg_price=None)},
                               state, costs, _rules(rapid_move_pct=10), self.T1)
        assert [c['kind'] for c in out] == ['it_stop']
        assert state['600519']['or_high'] == 101.0
        assert state['600519']['or_low'] == 89.0


class TestPrevLevelBreak:
    PREV = {'600519': {'high': 101.0, 'low': 99.0, 'date': '2026-11-11'}}

    def test_prev_high_break(self):
        state = {}
        out = iw.evaluate_tick({'600519': _q(price=100.0)}, state, {},
                               _rules(high_low_min_pct=10), NOW, self.PREV)
        assert out == []
        assert state['600519']['prev_side'] == 0
        out = iw.evaluate_tick({'600519': _q(price=101.2)}, state, {},
                               _rules(high_low_min_pct=10), NOW, self.PREV)
        assert [c['kind'] for c in out] == ['it_prev_high']

    def test_prev_low_break(self):
        state = {}
        iw.evaluate_tick({'600519': _q(price=100.0)}, state, {},
                         _rules(high_low_min_pct=10), NOW, self.PREV)
        out = iw.evaluate_tick({'600519': _q(price=98.8)}, state, {},
                               _rules(high_low_min_pct=10), NOW, self.PREV)
        assert [c['kind'] for c in out] == ['it_prev_low']

    def test_inside_prev_range_no_alert(self):
        state = {}
        iw.evaluate_tick({'600519': _q(price=100.0)}, state, {},
                         _rules(high_low_min_pct=10), NOW, self.PREV)
        out = iw.evaluate_tick({'600519': _q(price=100.5)}, state, {},
                               _rules(high_low_min_pct=10), NOW, self.PREV)
        assert out == []

    def test_first_tick_above_prev_high_is_baseline_only(self):
        state = {}
        out = iw.evaluate_tick({'600519': _q(price=101.2)}, state, {},
                               _rules(high_low_min_pct=10), NOW, self.PREV)
        assert out == []
        assert state['600519']['prev_side'] == 1

    def test_ex_rights_scales_prev_levels(self):
        adj = iw._adjust_prev_levels(
            {'high': 200.0, 'low': 180.0, 'close': 190.0, 'date': '2026-11-11'},
            95.0)   # 2:1 除权
        assert adj['high'] == 100.0
        assert adj['low'] == 90.0
        assert adj['high_raw'] == 200.0
        assert round(adj['adjust_ratio'], 4) == 0.5


class _Result:
    def __init__(self, rows=None, scalar_val=None):
        self.rows = rows or []
        self.scalar_val = scalar_val

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def scalar(self):
        return self.scalar_val


class _FakeDB:
    def __init__(self, count=0, existing=None, stock_seen=False, family_seen=False):
        self.count = count
        self.existing = set(existing or [])
        self.stock_seen = stock_seen
        self.family_seen = family_seen
        self.inserts = []
        self.checks = []
        self.stock_checks = []
        self.family_checks = []
        self.committed = False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if 'count(*)' in sql:
            return _Result(scalar_val=self.count)
        if 'kind = ANY' in sql:
            self.family_checks.append(dict(params))
            return _Result(rows=[(1,)] if self.family_seen else [])
        if 'kind LIKE' in sql and 'stock_code=:c' in sql:
            self.stock_checks.append(dict(params))
            return _Result(rows=[(1,)] if self.stock_seen else [])
        if sql.strip().startswith('SELECT 1'):
            self.checks.append(dict(params))
            key = (params['c'], params['k'])
            return _Result(rows=[(1,)] if key in self.existing else [])
        if sql.strip().startswith('INSERT'):
            self.inserts.append(dict(params))
            return _Result(rows=[(9000 + len(self.inserts),)])
        return _Result()

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class _PrevDB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def execute(self, stmt, params=None):
        self.calls += 1
        return _Result(rows=self.rows)


class TestPrevLevelsLoad:
    def test_load_and_cache_same_day(self):
        iw._prev_cache.update(day=None, key=None, levels={})
        db = _PrevDB([('600519', 101.0, 99.0, 100.0, date(2026, 11, 11))])
        out = iw._load_prev_levels(db, ['600519'], date(2026, 11, 12))
        assert out['600519'] == {'high': 101.0, 'low': 99.0, 'close': 100.0,
                                 'date': '2026-11-11'}
        out2 = iw._load_prev_levels(db, ['600519'], date(2026, 11, 12))
        assert out2 == out
        assert db.calls == 1

    def test_code_pool_change_refreshes(self):
        iw._prev_cache.update(day=None, key=None, levels={})
        db = _PrevDB([('600519', 101.0, 99.0, 100.0, date(2026, 11, 11))])
        iw._load_prev_levels(db, ['600519'], date(2026, 11, 12))
        iw._load_prev_levels(db, ['600519', '000001'], date(2026, 11, 12))
        assert db.calls == 2


class TestAiEventKinds:
    def test_default_includes_level_breaks(self):
        kinds = set(iw.DEFAULT_CONFIG['ai_event_kinds'])
        assert {'it_vwap_up', 'it_vwap_down', 'it_or_up', 'it_or_down',
                'it_prev_high', 'it_prev_low'} <= kinds

class TestPersist:
    def _cand(self, code, kind, title='x', level='warn'):
        return {'stock_code': code, 'kind': kind, 'level': level,
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

    def test_daily_cap_does_not_block_critical(self, monkeypatch):
        fake = _FakeDB(count=30)
        monkeypatch.setattr(iw, 'get_sync_db', lambda: fake)
        assert iw._persist([self._cand('600519', 'it_stop', level='critical')],
                           _rules(max_alerts_per_day=30), NOW) == 1
        assert fake.inserts[0]['k'] == 'it_stop'

    def test_stock_cooldown_skips_noise_kind(self, monkeypatch):
        fake = _FakeDB(count=0, stock_seen=True)
        monkeypatch.setattr(iw, 'get_sync_db', lambda: fake)
        assert iw._persist([self._cand('600519', 'it_vwap_up')],
                           _rules(), NOW) == 0
        assert fake.inserts == []

    def test_stock_cooldown_allows_high_signal(self, monkeypatch):
        fake = _FakeDB(count=0, stock_seen=True)
        monkeypatch.setattr(iw, 'get_sync_db', lambda: fake)
        assert iw._persist([self._cand('600519', 'it_stop')],
                           _rules(), NOW) == 1
        assert fake.inserts[0]['k'] == 'it_stop'

    def test_family_cooldown_skips_vwap_reverse(self, monkeypatch):
        fake = _FakeDB(count=0, family_seen=True)
        monkeypatch.setattr(iw, 'get_sync_db', lambda: fake)
        assert iw._persist([self._cand('600519', 'it_vwap_down')],
                           _rules(), NOW) == 0
        assert fake.inserts == []
        assert fake.family_checks[0]['ks'] == ['it_vwap_up', 'it_vwap_down']

    def test_stock_cooldown_can_be_disabled(self, monkeypatch):
        fake = _FakeDB(count=0, stock_seen=True)
        monkeypatch.setattr(iw, 'get_sync_db', lambda: fake)
        assert iw._persist([self._cand('600519', 'it_vwap_up')],
                           _rules(stock_cooldown_min=0), NOW) == 1

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

    def test_enqueues_ai_only_for_high_signal_kinds(self, monkeypatch):
        fake = _FakeDB(count=0)
        monkeypatch.setattr(iw, 'get_sync_db', lambda: fake)
        from app import intraday_ai as iai
        got = []
        monkeypatch.setattr(iai, 'enqueue_ai_event',
                            lambda item: got.append(item) or True)
        rules = _rules(ai_event_enabled=True,
                       ai_event_kinds=['it_stop', 'it_surge'])
        iw._persist([self._cand('600519', 'it_stop'),
                     self._cand('000001', 'it_high')], rules, NOW)
        # it_high 不在 ai_event_kinds，不触发 AI
        assert [x['kind'] for x in got] == ['it_stop']
        assert got[0]['alert_id'] == 9001

    def test_level_break_enqueues_ai(self, monkeypatch):
        fake = _FakeDB(count=0)
        monkeypatch.setattr(iw, 'get_sync_db', lambda: fake)
        from app import intraday_ai as iai
        got = []
        monkeypatch.setattr(iai, 'enqueue_ai_event',
                            lambda item: got.append(item) or True)
        iw._persist([self._cand('600519', 'it_or_up')],
                    _rules(ai_event_enabled=True,
                           ai_event_kinds=['it_or_up']), NOW)
        assert [x['kind'] for x in got] == ['it_or_up']
