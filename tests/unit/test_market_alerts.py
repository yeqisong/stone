"""盘面异动告警 + 实时空仓闸门 单元测试（design/13 批次三）。

模式照搬 tests/unit/test_intraday_watch.py：_FakeDB 按 SQL 片段匹配 + 显式 now，
不出网、不连库；缓存指标用 market_realtime._cache_put 预置。
"""
import threading
import time
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from app import market_realtime as mrt
from app import buy_check as bc
from app.api.status import _alert_visible
from crawler import em_quote

NOW = datetime(2026, 11, 30, 10, 0, 0)   # 周一盘中
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class _Row:
    def __init__(self, value):
        self._v = value

    def scalar(self):
        return self._v

    def fetchone(self):
        return self._v

    def fetchall(self):
        return self._v


class FakeAlertDB:
    """按 SQL 片段匹配的假 DB（只服务 evaluate_market_alerts 的四类查询）。"""

    def __init__(self, kind_cooldown_hit=False, family_hit=False, today_count=0):
        self.kind_cooldown_hit = kind_cooldown_hit
        self.family_hit = family_hit
        self.today_count = today_count
        self.inserted = []

    def execute(self, sql, params=None):
        s = str(sql)
        if 'INSERT INTO risk_alerts' in s:
            self.inserted.append(params)
            return _Row((len(self.inserted), NOW))
        if 'count(*)' in s:
            return _Row(self.today_count)
        if "kind LIKE 'mk%'" in s:      # family 冷却
            return _Row(1 if self.family_hit else None)
        if 'AND kind=:k' in s:          # per-kind 冷却
            return _Row(1 if self.kind_cooldown_hit else None)
        if 'FROM strategy_config' in s:  # read_market_alerts → (params, enabled) 缺省启用
            return _Row((None, True))
        raise AssertionError(f'FakeAlertDB 未预期的 SQL: {s[:120]}')

    def commit(self):
        pass


def _seed_index(prices, day='20261130'):
    """预置指数分时缓存。date 用腾讯真实返回的 YYYYMMDD 格式。"""
    mrt._cache_put('trends', {'000001': {
        'symbol': 'sh000001', 'name': '上证指数', 'date': day,
        'preClose': prices[0], 'last': prices[-1],
        'points': [{'t': f'{9 + i // 60:02d}:{i % 60:02d}', 'price': p,
                    'cum_vol': 1.0, 'cum_amount': 1.0} for i, p in enumerate(prices)],
    }})


def _seed_pool(day='20261130', max_lbc=6):
    """预置涨停池缓存（fetch_zt_pools 的原始形状）。"""
    mrt._cache_put('zt', {
        'date': day,
        'pool': [{'code': '603960', 'name': '克来机电', 'lbc': max_lbc, 'fund': 4.7e7},
                 {'code': '000001', 'name': '某首板', 'lbc': 1, 'fund': 1e7}],
        'broken_pool': [],
    }, day)


def _reset():
    mrt._cache_put('trends', {})
    mrt._cache_put('breadth', {})
    mrt._cache_put('zt', {})
    mrt._mstate_reset(None)
    bc._gate_cache.update(ts=0.0, data=None)


def _kinds(db=None, now=NOW):
    """评估一次，返回落库的 kind 列表（按落库顺序）。"""
    return [x['kind'] for x in mrt.evaluate_market_alerts(db or FakeAlertDB(), now=now)]


class TestMarketAlertEvaluate(unittest.TestCase):
    """四类告警的状态机 / 滞回 / 冷却 / 上限。"""

    def setUp(self):
        _reset()
        # 情绪源打桩：规则 4 读 market_sentiment.get，不打桩会真的去请求腾讯快照
        self.senti = {'level': 'neutral', 'score': 55}
        patcher = mock.patch.object(mrt.market_sentiment, 'get',
                                    lambda db, use_cache=True: dict(self.senti))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _eval(self, db=None, now=NOW):
        return mrt.evaluate_market_alerts(db or FakeAlertDB(), now=now)

    def test_disabled(self):
        db = FakeAlertDB()
        with mock.patch.object(mrt, 'read_market_alerts',
                               lambda d: {**mrt.DEFAULT_MARKET_ALERTS, 'enabled': False}):
            self.assertEqual(self._eval(db), [])
        self.assertEqual(db.inserted, [])

    def test_index_surge_then_no_repeat(self):
        """急涨触发一次；同向持续不重复（状态机）。"""
        _seed_index([100.0] * 5 + [101.5] * 11)   # 末点 vs 15 分钟前 +1.5% ≥ 1.2
        db = FakeAlertDB()
        out = self._eval(db)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['kind'], 'mk_idx_surge')
        self.assertEqual(out[0]['level'], 'warn')
        self.assertEqual(db.inserted[0]['k'], 'mk_idx_surge')
        # 再评：side 仍 'up' → 不重复
        self.assertEqual(self._eval(db), [])
        with mrt._mstate_lock:
            self.assertEqual(mrt._mstate['idx_side'], 'up')

    def test_index_reset_and_refire(self):
        """回落进缓冲区复位 side，再急涨可再触发（同向重复靠复位）。"""
        _seed_index([100.0] * 5 + [101.5] * 11)
        self._eval(FakeAlertDB())                     # 触发，side='up'
        _seed_index([100.0] * 15 + [100.2])           # +0.2%，|chg|≤0.4 → 复位 None
        self._eval(FakeAlertDB())
        with mrt._mstate_lock:
            self.assertIsNone(mrt._mstate['idx_side'])
        _seed_index([100.0] * 5 + [98.5] * 11)        # -1.5% → 急跌
        out = self._eval(FakeAlertDB())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['kind'], 'mk_idx_drop')

    def test_breadth_hysteresis(self):
        """宽度：中性起步 → 上穿 0.65 触发 → 回到中位带复位 → 再上穿再触发 → 下穿 0.35 触发反向。"""
        def breadth(r):
            mrt._cache_put('breadth', {'up_ratio': r, 'up': int(r * 5000), 'down': int((1 - r) * 5000)})
        breadth(0.55)
        self.assertEqual(_kinds(), [])                              # 基线：中性
        breadth(0.70)
        self.assertEqual(_kinds(), ['mk_breadth_up'])
        breadth(0.58)                                               # 0.60~0.65 滞回带：保持 high，不重复
        self.assertEqual(_kinds(), [])
        breadth(0.55)                                               # 回到中位带 → 复位
        self.assertEqual(_kinds(), [])
        breadth(0.70)
        self.assertEqual(_kinds(), ['mk_breadth_up'])
        breadth(0.30)
        self.assertEqual(_kinds(), ['mk_breadth_dn'])

    def test_breadth_first_observation_is_baseline(self):
        """重启后/开盘第一眼就在高位，不是「盘中转强」：首轮只建基线。"""
        mrt._cache_put('breadth', {'up_ratio': 0.92, 'up': 5000, 'down': 400})
        self.assertEqual(_kinds(), [])
        with mrt._mstate_lock:
            self.assertEqual(mrt._mstate['breadth_zone'], 'high')
            self.assertTrue(mrt._mstate['breadth_seen'])
        # 滞回带内继续高位：不报
        mrt._cache_put('breadth', {'up_ratio': 0.64, 'up': 3500, 'down': 1900})
        self.assertEqual(_kinds(), [])
        # 回到中位带再上穿 → 这才是转折
        mrt._cache_put('breadth', {'up_ratio': 0.50, 'up': 2700, 'down': 2700})
        self.assertEqual(_kinds(), [])
        mrt._cache_put('breadth', {'up_ratio': 0.72, 'up': 3900, 'down': 1500})
        self.assertEqual(_kinds(), ['mk_breadth_up'])

    def test_breadth_low_first_then_cross_high(self):
        """首轮在低位建基线后，中位带 → high 的上穿照常报（基线标记独立于 zone 值）。"""
        mrt._cache_put('breadth', {'up_ratio': 0.20, 'up': 1100, 'down': 4300})
        self.assertEqual(_kinds(), [])
        mrt._cache_put('breadth', {'up_ratio': 0.50, 'up': 2700, 'down': 2700})
        self.assertEqual(_kinds(), [])
        mrt._cache_put('breadth', {'up_ratio': 0.70, 'up': 3800, 'down': 1600})
        self.assertEqual(_kinds(), ['mk_breadth_up'])

    def test_height_once_per_day(self):
        """缓存是 fetch_zt_pools 的原始形状（pool/broken_pool 列表）；当日只报一次。"""
        raw = {'date': '20261130',
               'pool': [{'code': '603960', 'name': '克来机电', 'lbc': 6, 'fund': 4.7e7},
                        {'code': '000001', 'name': '某首板', 'lbc': 1, 'fund': 1e7}],
               'broken_pool': [{'code': '000002', 'name': '某炸板', 'lbc': 1}]}
        mrt._cache_put('zt', raw, '20261130')
        db = FakeAlertDB()
        out = self._eval(db)
        self.assertEqual([x['kind'] for x in out], ['mk_height'])
        self.assertEqual(db.inserted[0]['k'], 'mk_height')
        self.assertIn('克来机电', out[0]['body'])
        self.assertIn('涨停 2 家 / 炸板 1 家', out[0]['body'])
        # 再评：当日已报 → 不重复
        self.assertEqual(self._eval(FakeAlertDB()), [])
        with mrt._mstate_lock:
            self.assertEqual(mrt._mstate['height_day'], NOW.date())

    def test_height_from_real_adapter_shape(self):
        """形状回归：真实 fetch_zt_pools 解析（em_get 换成 fixture 文本）→ 缓存 → 触发。

        v3.27 审查项：评估曾直接读原始池的 max_lbc（恒 None，规则永不触发），而旧单测
        往缓存塞的是 zt_summary 的形状所以一直是绿的。这里把「缓存里到底存什么」
        用真实 adapter 固定住——缓存形状一变，本用例先红。
        """
        zt_text, zb_text = _read_fixture('em_ztpool.json'), _read_fixture('em_zbpool.json')

        class _Resp:
            def __init__(self, t):
                self.text = t

        def fake_em_get(url, **kw):
            return _Resp(zb_text if 'ZBPool' in url else zt_text)

        with mock.patch.object(em_quote, 'em_get', fake_em_get):
            raw = em_quote.fetch_zt_pools('20261127')      # fixture 是 2026-11-27 实测样本
        self.assertEqual(set(raw), {'date', 'pool', 'broken_pool'})
        mrt._cache_put('zt', raw, '20261127')
        db = FakeAlertDB()
        out = mrt.evaluate_market_alerts(db, now=datetime(2026, 11, 27, 10, 0))
        self.assertEqual([x['kind'] for x in out], ['mk_height'])
        self.assertIn('连板高度 6 板', out[0]['title'])
        self.assertEqual(db.inserted[0]['k'], 'mk_height')

    def test_sentiment_baseline_then_panic(self):
        """首轮只建基线；跨入 panic 报 critical。"""
        self.assertEqual(_kinds(), [])                              # 建基线 neutral
        self.senti.update(level='panic', score=18)
        out = self._eval(FakeAlertDB())
        self.assertEqual([x['kind'] for x in out], ['mk_panic'])
        self.assertEqual(out[0]['level'], 'critical')

    def test_kind_cooldown_suppresses(self):
        """per-kind 冷却命中 → 不落库。"""
        _seed_index([100.0] * 5 + [101.5] * 11)
        db = FakeAlertDB(kind_cooldown_hit=True)
        self.assertEqual(self._eval(db), [])
        self.assertEqual(db.inserted, [])

    def test_family_cooldown_suppresses(self):
        _seed_index([100.0] * 5 + [101.5] * 11)
        db = FakeAlertDB(family_hit=True)
        self.assertEqual(self._eval(db), [])

    def test_daily_cap_suppresses(self):
        _seed_index([100.0] * 5 + [101.5] * 11)
        db = FakeAlertDB(today_count=15)
        self.assertEqual(self._eval(db), [])
        self.assertEqual(db.inserted, [])

    # ── v3.27 审查项 2：数据日期 / 时段门控 ───────────────────────────────

    def test_no_eval_outside_continuous_session(self):
        """盘前/午休/收盘后不评估：采集窗口 09:25 就开始，但那不是盘中异动。"""
        _seed_pool()
        for t in ((9, 27), (12, 0), (15, 1)):
            db = FakeAlertDB()
            self.assertEqual(self._eval(db, now=datetime(2026, 11, 30, *t)), [], t)
            self.assertEqual(db.inserted, [], t)
        # 连续竞价内才评估（09:30:00 起 session 即为 open；09:30 那一分钟池子尚未
        # 切到今天，由 test_prev_day_data_never_alerts 的日期门控兜住）
        out = self._eval(FakeAlertDB(), now=datetime(2026, 11, 30, 9, 31))
        self.assertEqual([x['kind'] for x in out], ['mk_height'])

    def test_prev_day_data_never_alerts(self):
        """周一开盘后缓存里还是周五的数据（东财/腾讯盘前回退）→ 对应规则不评估。"""
        _seed_index([100.0] * 5 + [101.5] * 11, day='20261127')   # 周五尾盘急涨
        _seed_pool(day='20261127')                                  # 周五的 6 板
        db = FakeAlertDB()
        self.assertEqual(self._eval(db), [])
        self.assertEqual(db.inserted, [])
        # 状态机不能被旧数据污染：换成今天的数据后照常触发
        _seed_index([100.0] * 5 + [101.5] * 11)
        _seed_pool()
        out = self._eval(FakeAlertDB())
        self.assertEqual(sorted(x['kind'] for x in out), ['mk_height', 'mk_idx_surge'])

    def test_stale_sentiment_not_alerted(self):
        """情绪源的宽度快照还是上一交易日（stale，开盘后首个 slot 落库前）→ 不报跨级。"""
        self._eval(FakeAlertDB())                       # 建基线 neutral
        self.senti.update(level='panic', score=18, stale=True)
        self.assertEqual(self._eval(FakeAlertDB()), [])
        self.senti['stale'] = False
        out = self._eval(FakeAlertDB())
        self.assertEqual([x['kind'] for x in out], ['mk_panic'])

    # ── v3.27 审查项 3：被冷却/上限拦下的信号不丢 ────────────────────────

    def test_suppressed_panic_is_retried(self):
        """宽度转弱刚报过，3 分钟后情绪进 panic 被 family 冷却拦下 → 冷却过后必须补报。"""
        mrt._cache_put('breadth', {'up_ratio': 0.55, 'up': 2700, 'down': 2700})
        self.assertEqual(_kinds(), [])                          # 宽度基线 + 情绪基线
        mrt._cache_put('breadth', {'up_ratio': 0.30, 'up': 1600, 'down': 4200})
        self.senti.update(level='panic', score=18)
        # family 冷却命中：宽度转弱、盘面恐慌都发不出去
        self.assertEqual(_kinds(FakeAlertDB(family_hit=True)), [])
        with mrt._mstate_lock:
            self.assertIsNone(mrt._mstate['breadth_zone'])     # 状态没被前移
            self.assertEqual(mrt._mstate['sent_level'], 'neutral')
        # 冷却过后两条都补报（critical 的恐慌不能丢）
        self.assertEqual(sorted(_kinds()), ['mk_breadth_dn', 'mk_panic'])
        with mrt._mstate_lock:
            self.assertEqual(mrt._mstate['breadth_zone'], 'low')
            self.assertEqual(mrt._mstate['sent_level'], 'panic')

    def test_suppressed_height_keeps_daily_slot(self):
        """连板高度被拦下 → 不占「当日一次」的名额，下个周期补报；落库后才占。"""
        _seed_pool()
        self.assertEqual(_kinds(FakeAlertDB(family_hit=True)), [])
        with mrt._mstate_lock:
            self.assertIsNone(mrt._mstate['height_day'])
        self.assertEqual(_kinds(), ['mk_height'])
        self.assertEqual(_kinds(), [])

    def test_suppressed_index_surge_is_retried(self):
        _seed_index([100.0] * 5 + [101.5] * 11)
        self.assertEqual(_kinds(FakeAlertDB(kind_cooldown_hit=True)), [])
        with mrt._mstate_lock:
            self.assertIsNone(mrt._mstate['idx_side'])
        self.assertEqual(_kinds(), ['mk_idx_surge'])

    def test_daily_cap_keeps_state_for_retry(self):
        _seed_pool()
        self.assertEqual(_kinds(FakeAlertDB(today_count=15)), [])
        with mrt._mstate_lock:
            self.assertIsNone(mrt._mstate['height_day'])
        self.assertEqual(_kinds(FakeAlertDB(today_count=2)), ['mk_height'])

    def test_sentiment_leaving_alertable_level_moves_on(self):
        """恐慌还没补报盘面就转弱（不告警的档位）：状态照常前移，不补报过时的恐慌。"""
        self.assertEqual(_kinds(), [])                          # 情绪基线 neutral
        self.senti.update(level='panic', score=18)
        self.assertEqual(_kinds(FakeAlertDB(family_hit=True)), [])
        self.senti.update(level='weak', score=40)
        self.assertEqual(_kinds(), [])
        with mrt._mstate_lock:
            self.assertEqual(mrt._mstate['sent_level'], 'weak')


class FakeCalDB:
    """只回答交易日历查询（treemap_intraday._is_trade_day 的 SQL）。"""

    def __init__(self, holidays=()):
        self.holidays = {date.fromisoformat(d) for d in holidays}

    def execute(self, sql, params=None):
        assert 'FROM trade_calendar' in str(sql), str(sql)[:80]
        d = params['d']
        return _Row((d.weekday() < 5 and d not in self.holidays,))


class TestPoolTradeDate(unittest.TestCase):
    """涨停池请求日期：只请求确定已有数据的交易日（东财对无数据日期静默回退）。"""

    def test_weekend_falls_back_to_friday(self):
        self.assertEqual(mrt._pool_trade_date(FakeCalDB(), datetime(2026, 11, 28, 10, 0)),
                         date(2026, 11, 27))            # 周六
        self.assertEqual(mrt._pool_trade_date(FakeCalDB(), datetime(2026, 11, 29, 20, 0)),
                         date(2026, 11, 27))            # 周日

    def test_preopen_falls_back_until_0931(self):
        db = FakeCalDB()
        self.assertEqual(mrt._pool_trade_date(db, datetime(2026, 11, 30, 9, 26)),
                         date(2026, 11, 27))            # 周一集合竞价
        self.assertEqual(mrt._pool_trade_date(db, datetime(2026, 11, 30, 9, 30)),
                         date(2026, 11, 27))
        self.assertEqual(mrt._pool_trade_date(db, datetime(2026, 11, 30, 9, 31)),
                         date(2026, 11, 30))            # 连续竞价开始

    def test_after_close_same_day(self):
        # 15:05 日终归档时请求的必须是当天，否则「缓存不是当日」的判断没有意义
        self.assertEqual(mrt._pool_trade_date(FakeCalDB(), datetime(2026, 11, 30, 15, 6)),
                         date(2026, 11, 30))
        self.assertEqual(mrt._pool_trade_date(FakeCalDB(), datetime(2026, 11, 30, 23, 59)),
                         date(2026, 11, 30))

    def test_holidays_walk_back(self):
        db = FakeCalDB(holidays=['2026-10-01', '2026-10-02', '2026-10-05',
                                 '2026-10-06', '2026-10-07', '2026-10-08'])
        self.assertEqual(mrt._pool_trade_date(db, datetime(2026, 10, 1, 10, 0)),
                         date(2026, 9, 30))             # 国庆当天 → 9-30
        self.assertEqual(mrt._pool_trade_date(db, datetime(2026, 10, 9, 9, 20)),
                         date(2026, 9, 30))             # 节后首个交易日盘前


class TestDataDateAndStale(unittest.TestCase):
    """响应的 data_date / stale：数据真实属于哪天，能不能当成当前盘面。"""

    def test_data_date_takes_latest_dated_source(self):
        trends = {'000001': {'date': '20261127'}, '399001': {'date': '20261127'}}
        zt = {'date': '20261127'}
        self.assertEqual(mrt._data_date(trends, zt, {'2026-11-26': [], '2026-11-27': []},
                                        [{'date': '2026-11-27'}]), '2026-11-27')
        # 某一源落后不拖累整体（取最新）
        self.assertEqual(mrt._data_date({'000001': {'date': '20261127'}}, zt, {},
                                        [{'date': '2026-11-30'}]), '2026-11-30')
        # 全部无法判定
        self.assertIsNone(mrt._data_date({}, None, {}, []))
        self.assertIsNone(mrt._data_date({'000001': {'date': 'n/a'}}, None, {}, []))

    def test_stale_flag(self):
        T = 1_000_000.0
        # 休市：显示上一交易日数据是预期行为，不算 stale
        self.assertFalse(mrt._stale_flag('closed', False, [T], False, T + 3600))
        # 交易日盘前/盘中/收盘后，数据还是上一交易日 → stale
        for sess in ('pre', 'open', 'lunch', 'post'):
            self.assertTrue(mrt._stale_flag(sess, False, [T], False, T + 60), sess)
        # 采集窗口内超过 3 个周期未更新
        self.assertTrue(mrt._stale_flag('open', True, [T], True, T + 181))
        self.assertFalse(mrt._stale_flag('open', True, [T], True, T + 100))
        # 缺源
        self.assertTrue(mrt._stale_flag('post', False, [], True, T))


class TestRefreshPoolDate(unittest.TestCase):
    """refresh_all 向东财请求的涨停池日期（外部源全部打桩，只看接线）。"""

    def _refresh(self, now):
        requested = []

        def fake_pools(ymd):
            requested.append(ymd)
            return {'date': ymd, 'pool': [], 'broken_pool': []}

        with mock.patch.object(mrt.em_quote, 'fetch_zt_pools', fake_pools), \
                mock.patch.object(mrt, 'fetch_all_quotes', lambda: {}), \
                mock.patch.object(mrt, 'minute_trend', lambda *a, **k: {'points': []}), \
                mock.patch.object(mrt, 'fetch_sina_min5', lambda *a, **k: []), \
                mock.patch.object(mrt.em_quote, 'fetch_board_list', lambda: []), \
                mock.patch.object(mrt.em_quote, 'fetch_market_fflow', lambda: []):
            mrt.refresh_all(FakeCalDB(), now=now)
        try:
            return requested[0], mrt._cache_get('zt')['date']
        finally:
            mrt._cache_put('zt', {})

    def test_requests_only_dates_that_have_data(self):
        # (时刻, 期望请求的交易日) —— 周一 2026-11-30 开盘前只能拿到周五的池
        cases = [
            (datetime(2026, 11, 28, 10, 0), '20261127'),   # 周六
            (datetime(2026, 11, 30, 9, 26), '20261127'),   # 周一集合竞价（采集窗口内）
            (datetime(2026, 11, 30, 9, 31), '20261130'),   # 连续竞价开始
            (datetime(2026, 11, 30, 15, 6), '20261130'),   # 日终归档依赖此时缓存=当天
        ]
        for now, want in cases:
            with self.subTest(now=now):
                self.assertEqual(self._refresh(now), (want, want))


class TestRefreshMutexAndOndemand(unittest.TestCase):
    """v3.27 审查项 7：同一时刻只允许一个刷新；按需刷新让位给采集 loop 且有节流。"""

    def setUp(self):
        mrt._REFRESH.update(running=False, last_start=0.0, last_done=0.0)

    def tearDown(self):
        mrt._REFRESH.update(running=False, last_start=0.0, last_done=0.0)

    def test_reentrant_call_is_skipped(self):
        calls = []

        def fake(pool_day):
            calls.append(pool_day)
            # 刷新进行中又来一次（模拟采集 loop 与按需线程撞车）：必须被挡住
            return mrt.refresh_all(FakeCalDB(), now=NOW)

        with mock.patch.object(mrt, '_refresh_sources', fake):
            self.assertEqual(mrt.refresh_all(FakeCalDB(), now=NOW), [])
        self.assertEqual(len(calls), 1)
        self.assertFalse(mrt._REFRESH['running'])

    def test_concurrent_refresh_runs_once(self):
        started, release = threading.Event(), threading.Event()
        runs, outer = [], []

        def fake(pool_day):
            runs.append(1)
            started.set()
            release.wait(5)
            return ['breadth']

        with mock.patch.object(mrt, '_refresh_sources', fake):
            t1 = threading.Thread(target=lambda: mrt.refresh_all(FakeCalDB(), now=NOW))
            t1.start()
            try:
                self.assertTrue(started.wait(5))
                outer.append(mrt.refresh_all(FakeCalDB(), now=NOW))    # 撞上正在跑的刷新
            finally:
                release.set()
                t1.join(5)
        self.assertEqual(len(runs), 1)
        self.assertEqual(outer, [[]])
        self.assertFalse(mrt._REFRESH['running'])

    def test_flag_released_when_sources_raise(self):
        with mock.patch.object(mrt, '_refresh_sources', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                mrt.refresh_all(FakeCalDB(), now=NOW)
        self.assertFalse(mrt._REFRESH['running'])    # 否则一次异常之后永远不再刷新

    @staticmethod
    def _ondemand(now, last_start_age, running=False):
        """last_start_age=距上次刷新启动过了多久（10**9 模拟进程启动后从未刷新）。"""
        mrt._REFRESH.update(running=running, last_start=time.time() - last_start_age)
        with mock.patch.object(mrt.threading, 'Thread') as thread:
            fired = mrt.ensure_refresh_async(now)
        return fired, thread.called

    def test_ondemand_yields_to_loop_in_window(self):
        mon = datetime(2026, 11, 30, 10, 0)        # 周一盘中：采集 loop 每 60s 刷
        self.assertEqual(self._ondemand(mon, 100), (False, False))        # loop 健康 → 不补
        self.assertEqual(self._ondemand(mon, 10 ** 9), (True, True))       # 刚重启 → 补
        self.assertEqual(self._ondemand(mon, 400), (True, True))          # loop 卡死 → 补

    def test_ondemand_throttled_out_of_window(self):
        sat = datetime(2026, 11, 28, 10, 0)        # 周六：loop 不刷，只能靠按需
        self.assertEqual(self._ondemand(sat, 10 ** 9), (True, True))      # 重启后第一次
        self.assertEqual(self._ondemand(sat, 120), (False, False))        # 某源一直失败也不连发
        self.assertEqual(self._ondemand(sat, 700), (True, True))          # 10 分钟后允许再试
        self.assertEqual(self._ondemand(sat, 700, running=True), (False, False))


class TestLiveGate(unittest.TestCase):
    """三源组合取最严级别（三源全部打桩，use_cache=False 绕过缓存）。"""

    def setUp(self):
        bc._gate_cache.update(ts=0.0, data=None)
        self.addCleanup(bc._gate_cache.update, ts=0.0, data=None)
        self._patch()

    def _patch(self, mg_level='open', gated=False, s_level='neutral', s_score=55, s_stale=False):
        self.senti = {'level': s_level, 'score': s_score, 'stale': s_stale,
                      'advice': {'entry': '按买点检查', 'stop': '止损正常执行'}}
        # patch.object + addCleanup：用例中途失败也一定还原，不污染同进程后续测试
        # （bc.market_sentiment 就是 app.market_sentiment 模块本身，裸赋值会泄漏出去）
        stubs = (
            (bc, 'market_gate',
             lambda db: {'level': mg_level, 'pass': mg_level != 'closed', 'reason': 'mock 市场闸门'}),
            (bc, 'regime_gated_today',
             lambda db, today=None: (gated, {'enabled': gated, 'ma_window': 20, 'max_skip_days': 2})),
            (bc.market_sentiment, 'get', lambda db, use_cache=True: dict(self.senti)),
        )
        for obj, name, val in stubs:
            patcher = mock.patch.object(obj, name, val)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_all_open(self):
        self._patch()
        g = bc.live_gate(None, use_cache=False)
        self.assertEqual(g['level'], 'open')
        self.assertTrue(g['pass'])

    def test_panic_wins(self):
        """盘面 panic → 整体 closed（即使市场闸门 open）。"""
        self._patch(mg_level='open', gated=False, s_level='panic', s_score=15)
        g = bc.live_gate(None, use_cache=False)
        self.assertEqual(g['level'], 'closed')
        self.assertFalse(g['pass'])
        self.assertTrue(any('恐慌' in r for r in g['reasons']))

    def test_regime_gated_wins(self):
        self._patch(mg_level='caution', gated=True)
        g = bc.live_gate(None, use_cache=False)
        self.assertEqual(g['level'], 'closed')
        self.assertTrue(any('regime' in r for r in g['reasons']))

    def test_weak_caution(self):
        self._patch(s_level='weak', s_score=40)
        g = bc.live_gate(None, use_cache=False)
        self.assertEqual(g['level'], 'caution')
        self.assertTrue(g['pass'])

    def test_market_closed_wins_over_weak(self):
        self._patch(mg_level='closed', s_level='weak')
        g = bc.live_gate(None, use_cache=False)
        self.assertEqual(g['level'], 'closed')

    # ── v3.27 审查项 5：缺一路数据不能说成完整的「放行」 ───────────────────

    def test_stale_sentiment_does_not_close_gate(self):
        """上一交易日收盘的恐慌不是今天的盘面：不参与定级，理由里说明，建议不照搬。"""
        self._patch(mg_level='open', s_level='panic', s_score=15)
        self.senti['stale'] = True
        g = bc.live_gate(None, use_cache=False)
        self.assertEqual(g['level'], 'open')
        self.assertIsNone(g['advice_entry'])          # 「禁止新开仓」是昨天的建议
        self.assertTrue(any('上一交易日收盘口径' in r for r in g['reasons']))
        self.assertTrue(g['sources']['sentiment']['stale'])
        # 同一盘面，快照切到今天后才关闸
        self.senti['stale'] = False
        self.assertEqual(bc.live_gate(None, use_cache=False)['level'], 'closed')

    def test_sentiment_source_down_is_disclosed(self):
        """情绪源挂了：结论照常给出，但必须写明不含实时盘面。"""
        self._patch(mg_level='open')

        def boom(db, use_cache=True):
            raise RuntimeError('tencent down')

        with mock.patch.object(bc.market_sentiment, 'get', boom):
            g = bc.live_gate(None, use_cache=False)
        self.assertEqual(g['level'], 'open')
        self.assertTrue(any('情绪源不可用' in r for r in g['reasons']))
        self.assertIsNone(g['sources']['sentiment']['level'])


class TestSafeLiveGate(unittest.TestCase):
    """端点兜底：闸门算不出来必须是「不知道」，不能兜成「放行」。"""

    def test_failure_is_unknown_not_open(self):
        with mock.patch.object(bc, 'live_gate', side_effect=RuntimeError('db down')):
            g = mrt._safe_live_gate(None)
        self.assertEqual(g['level'], 'unknown')
        self.assertIsNone(g['pass'])                  # 不给出 bool：以后接到买入候选时不能被当成 True
        self.assertEqual(g['label'], '不可用')
        self.assertIn('db down', g['reasons'][0])

    def test_pass_through(self):
        ok = {'level': 'open', 'pass': True, 'label': '放行', 'reasons': ['x'],
              'advice_entry': None, 'sources': {}}
        with mock.patch.object(bc, 'live_gate', return_value=ok):
            self.assertIs(mrt._safe_live_gate(None), ok)


class TestSentimentEndpointDoesNotMutateCache(unittest.TestCase):
    """v3.27 审查项 8：/api/market_sentiment 不能往 market_sentiment 的缓存 dict 里写。

    get() 返回的是模块缓存里的同一个对象，/api/market/overview 和 intraday_watch 的
    上下文也在读它；原地加 gate 等于把本端点的附加字段塞给所有消费者。
    """

    def _call(self, senti, gate):
        import app.api.treemap as api
        shared = dict(senti)                           # 模拟模块缓存里的那个对象
        fake_db = mock.Mock()
        gate_stub = ({'side_effect': gate} if isinstance(gate, Exception)
                     else {'return_value': gate})
        with mock.patch.object(api, 'get_sync_db', return_value=fake_db), \
                mock.patch.object(mrt.market_sentiment, 'get', lambda db, use_cache=True: shared), \
                mock.patch.object(bc, 'live_gate', **gate_stub):
            resp = api.get_market_sentiment()
        return shared, resp, fake_db

    def test_gate_is_attached_to_a_copy(self):
        senti = {'ok': True, 'level': 'neutral', 'score': 55, 'advice': {'entry': 'x'}}
        gate = {'level': 'caution', 'pass': True, 'label': '警戒', 'reasons': ['r'],
                'advice_entry': None, 'sources': {}}
        shared, resp, fake_db = self._call(senti, gate)
        self.assertEqual(resp, {**senti, 'gate': gate})
        self.assertIsNot(resp, shared)                 # 不是同一个对象
        self.assertEqual(shared, senti)                # 缓存对象原样
        fake_db.close.assert_called_once()

    def test_gate_failure_is_unknown_not_silent_none(self):
        shared, resp, _ = self._call({'ok': True}, RuntimeError('db down'))
        self.assertEqual(resp['gate']['level'], 'unknown')
        self.assertEqual(shared, {'ok': True})


class TestBeijingToday(unittest.TestCase):
    """v3.27 审查项 10：「今天是否禁买」不能依赖主机时区的自然日。"""

    def test_bj_today_crosses_midnight_in_beijing_not_on_host(self):
        from datetime import timezone, timedelta
        from app import regime_gate as rg
        utc = timezone.utc
        instant = datetime(2026, 11, 29, 17, 30, tzinfo=utc)     # 北京时间已是 11-30 01:30

        class FrozenDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

        self.assertEqual(instant.date(), date(2026, 11, 29))    # UTC 主机的「今天」
        with mock.patch.object(rg, '_dtm', FrozenDT):
            self.assertEqual(rg.bj_today(), date(2026, 11, 30))  # 北京口径的「今天」

    def test_regime_gated_today_defaults_to_beijing_day(self):
        from app import regime_gate as rg
        seen = {}

        def fake_cfg(db):
            return {'enabled': True, 'ma_window': 20, 'max_skip_days': 2}

        def fake_gates(db, dates, cfg):
            seen['dates'] = list(dates)
            return set(dates)

        with mock.patch.object(rg, 'get_active_regime_cfg', fake_cfg), \
                mock.patch.object(rg, 'compute_regime_gates', fake_gates), \
                mock.patch.object(rg, 'bj_today', lambda: date(2026, 11, 30)):
            gated, cfg = rg.regime_gated_today(None)
        self.assertTrue(gated)
        self.assertEqual(seen['dates'], ['2026-11-30'])


class TestAlertVisible(unittest.TestCase):
    """WS 谓词：mk% 无归属行需 research.market.view；其余无归属行需 model.alerts.view。"""

    def _meta_ws(self, user_id=7, perms=(), roles=()):
        import app.api.status as st
        ws = object()
        st._ws_meta[ws] = {'user_id': user_id, 'permissions': set(perms), 'roles': set(roles)}
        return ws

    def tearDown(self):
        import app.api.status as st
        st._ws_meta.clear()

    def test_owned_row_only_owner(self):
        ws = self._meta_ws()
        other = self._meta_ws(user_id=8)
        self.assertTrue(_alert_visible(7, 'it_stop', 7, ws))
        self.assertFalse(_alert_visible(7, 'it_stop', 8, other))

    def test_market_row_needs_market_view(self):
        """双角色：有 research.market.view 的连接收得到 mk%；没有的收不到。

        实现必须和权限表登记一致——#/kr 子页、/api/market/* 都归 research.market.view，
        没这个权限的角色/支持会话不该收到一条点进去会被重定向的通知。
        """
        from app.auth.route_permissions import resolve_rule
        no_market = self._meta_ws(perms=('research.stock.view', 'risk.alerts.view'))
        has_market = self._meta_ws(perms=('research.market.view',))
        admin = self._meta_ws(roles=('admin',))
        for ws, want in ((no_market, False), (has_market, True), (admin, True)):
            self.assertEqual(_alert_visible(None, 'mk_idx_surge', 7, ws), want)
            self.assertEqual(_alert_visible(None, 'mk_panic', 9, ws), want)
        # 权限表登记：页面与接口同属一个节点，所以广播门槛用它才自洽
        self.assertEqual(resolve_rule('GET', '/api/market/realtime').permission,
                         'research.market.view')
        self.assertEqual(resolve_rule('GET', '/api/market_sentiment').permission,
                         'research.treemap.view')

    def test_model_row_needs_perm(self):
        ws_plain = self._meta_ws()
        ws_model = self._meta_ws(perms=('model.alerts.view',))
        ws_market_only = self._meta_ws(perms=('research.market.view',))
        self.assertFalse(_alert_visible(None, 'model_stale', 7, ws_plain))
        self.assertFalse(_alert_visible(None, 'model_stale', 7, ws_market_only))
        self.assertTrue(_alert_visible(None, 'model_stale', 7, ws_model))


if __name__ == '__main__':
    unittest.main()
