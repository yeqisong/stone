"""盘面异动告警 + 实时空仓闸门 单元测试（design/13 批次三）。

模式照搬 tests/unit/test_intraday_watch.py：_FakeDB 按 SQL 片段匹配 + 显式 now，
不出网、不连库；缓存指标用 market_realtime._cache_put 预置。
"""
import unittest
from datetime import datetime

from app import market_realtime as mrt
from app import buy_check as bc
from app.api.status import _alert_visible

NOW = datetime(2026, 11, 30, 10, 0, 0)   # 周一盘中


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


def _seed_index(prices):
    mrt._cache_put('trends', {'000001': {
        'symbol': 'sh000001', 'name': '上证指数', 'date': '2026-11-30',
        'preClose': prices[0], 'last': prices[-1],
        'points': [{'t': f'{9 + i // 60:02d}:{i % 60:02d}', 'price': p,
                    'cum_vol': 1.0, 'cum_amount': 1.0} for i, p in enumerate(prices)],
    }})


def _reset():
    mrt._cache_put('trends', {})
    mrt._cache_put('breadth', {})
    mrt._cache_put('zt', {})
    with mrt._mstate_lock:
        mrt._mstate.update(day=None, idx_side=None, breadth_zone=None,
                           height_day=None, sent_level=None)
    bc._gate_cache.update(ts=0.0, data=None)


class TestMarketAlertEvaluate(unittest.TestCase):
    """四类告警的状态机 / 滞回 / 冷却 / 上限。"""

    def setUp(self):
        _reset()

    def _eval(self, db=None, now=NOW):
        return mrt.evaluate_market_alerts(db or FakeAlertDB(), now=now)

    def test_disabled(self):
        db = FakeAlertDB()
        orig = mrt.read_market_alerts
        mrt.read_market_alerts = lambda d: {**mrt.DEFAULT_MARKET_ALERTS, 'enabled': False}
        try:
            self.assertEqual(self._eval(db), [])
            self.assertEqual(db.inserted, [])
        finally:
            mrt.read_market_alerts = orig

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
        """宽度上穿 0.65 触发；中位带不重复；下穿 0.35 触发反向。"""
        mrt._cache_put('breadth', {'up_ratio': 0.70, 'up': 5200, 'down': 170})
        out = self._eval(FakeAlertDB())
        self.assertTrue(any(x['kind'] == 'mk_breadth_up' for x in out))
        mrt._cache_put('breadth', {'up_ratio': 0.55, 'up': 4000, 'down': 1000})  # 中位带 → 复位
        self._eval(FakeAlertDB())
        mrt._cache_put('breadth', {'up_ratio': 0.30, 'up': 1500, 'down': 3800})
        out = self._eval(FakeAlertDB())
        self.assertTrue(any(x['kind'] == 'mk_breadth_dn' for x in out))

    def test_height_once_per_day(self):
        mrt._cache_put('zt', {'max_lbc': 6, 'limit_up': 417, 'broken': 42,
                              'ladder': [{'lbc': 6, 'n': 1, 'names': ['克来机电'], 'codes': ['603960']}]})
        out = self._eval(FakeAlertDB())
        self.assertTrue(any(x['kind'] == 'mk_height' for x in out))
        # 再评：当日已报 → 不重复
        self._eval(FakeAlertDB())
        with mrt._mstate_lock:
            self.assertEqual(mrt._mstate['height_day'], NOW.date())

    def test_sentiment_baseline_then_panic(self):
        """首轮只建基线；跨入 panic 报 critical。"""
        mrt.market_sentiment.get = lambda db, use_cache=True: {'level': 'neutral', 'score': 55}
        self._eval(FakeAlertDB())                      # 建基线 sent_level='neutral'
        mrt.market_sentiment.get = lambda db, use_cache=True: {'level': 'panic', 'score': 18}
        out = self._eval(FakeAlertDB())
        self.assertTrue(any(x['kind'] == 'mk_panic' for x in out))
        self.assertEqual([x for x in out if x['kind'] == 'mk_panic'][0]['level'], 'critical')

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


class TestLiveGate(unittest.TestCase):
    """三源组合取最严级别（mock 三源，use_cache=False 绕过缓存）。"""

    def setUp(self):
        bc._gate_cache.update(ts=0.0, data=None)
        self._orig = (bc.market_gate, bc.regime_gated_today, bc.market_sentiment.get)

    def tearDown(self):
        bc.market_gate, bc.regime_gated_today, bc.market_sentiment.get = self._orig
        bc._gate_cache.update(ts=0.0, data=None)

    def _patch(self, mg_level='open', gated=False, s_level='neutral', s_score=55):
        bc.market_gate = lambda db: {'level': mg_level, 'pass': mg_level != 'closed', 'reason': 'mock 市场闸门'}
        bc.regime_gated_today = lambda db, today=None: (gated, {'enabled': gated, 'ma_window': 20, 'max_skip_days': 2})
        bc.market_sentiment.get = lambda db, use_cache=True: {
            'level': s_level, 'score': s_score, 'stale': False,
            'advice': {'entry': '按买点检查', 'stop': '止损正常执行'}}

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


class TestAlertVisible(unittest.TestCase):
    """WS 谓词：mk% 无归属行广播全员；其余无归属行需 model.alerts.view。"""

    class _WS:
        def __init__(self, perms=(), roles=()):
            self._perms, self._roles = set(perms), set(roles)

    def _meta_ws(self, perms=(), roles=()):
        import app.api.status as st
        ws = object()
        st._ws_meta[ws] = {'user_id': 7, 'permissions': set(perms), 'roles': set(roles)}
        return ws

    def tearDown(self):
        import app.api.status as st
        st._ws_meta.clear()

    def test_owned_row_only_owner(self):
        import app.api.status as st
        ws = self._meta_ws()
        other = self._meta_ws()
        self.assertTrue(_alert_visible(7, 'it_stop', 7, ws))
        self.assertFalse(_alert_visible(7, 'it_stop', 8, other))

    def test_market_row_broadcast_all(self):
        ws = self._meta_ws()                # 无任何权限
        self.assertTrue(_alert_visible(None, 'mk_idx_surge', 7, ws))
        self.assertTrue(_alert_visible(None, 'mk_panic', 9, ws))

    def test_model_row_needs_perm(self):
        ws_plain = self._meta_ws()
        ws_model = self._meta_ws(perms=('model.alerts.view',))
        self.assertFalse(_alert_visible(None, 'model_stale', 7, ws_plain))
        self.assertTrue(_alert_visible(None, 'model_stale', 7, ws_model))


if __name__ == '__main__':
    unittest.main()
