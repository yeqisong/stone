"""雪球采集网络故障/封禁分类单测——传输层故障不进熔断监狱、adapter 自愈重建。"""
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from crawler.xueqiu import adapter as A
from crawler.xueqiu.guard import RatingGuard as XueqiuGuard


def _res(**kw):
    return A.FetchResult(**kw)


class TestAdapterConnSelfHeal(unittest.TestCase):
    def setUp(self):
        self.a = A.XueqiuAdapter.__new__(A.XueqiuAdapter)   # 绕过 __init__
        self.a._cookies = {'xq_a_token': 't'}
        self.rebuilds = []
        self.a._rebuild_session = lambda reason='': self.rebuilds.append(reason)

    def test_dns_error_rebuilds_and_retries(self):
        """连接类异常 → 重建会话 → 重试成功。"""
        calls = []
        sess = MagicMock()
        def get(url, **kw):
            calls.append(url)
            if len(calls) == 1:
                raise RuntimeError("Failed to perform, curl: (6) Could not resolve host")
            return MagicMock(status_code=200, headers={'content-type': 'application/json'},
                             **{'json.return_value': {'list': [], 'maxPage': 1}})
        sess.get = get
        self.a._session = sess
        with patch.object(A, 'to_xq_symbol', side_effect=lambda c: c), \
             patch.object(A.XueqiuAdapter, 'ready', return_value=True):
            r = self.a.fetch_page('600519')
        self.assertTrue(r.ok)
        self.assertEqual(len(self.rebuilds), 1)
        self.assertEqual(len(calls), 2)

    def test_dns_error_marked_net_when_retry_fails(self):
        sess = MagicMock()
        sess.get = MagicMock(side_effect=RuntimeError("curl: (6) Could not resolve host"))
        self.a._session = sess
        with patch.object(A.XueqiuAdapter, 'ready', return_value=True):
            r = self.a.fetch_page('600519')
        self.assertFalse(r.ok)
        self.assertTrue(r.net, "重试仍失败应标记为传输层故障")
        self.assertEqual(len(self.rebuilds), 1)

    def test_http_405_not_net(self):
        """405 是风控信号不是传输故障——不重建、不标 net。"""
        resp = MagicMock(status_code=405, headers={'content-type': 'text/html'})
        sess = MagicMock()
        sess.get = MagicMock(return_value=resp)
        self.a._session = sess
        with patch.object(A.XueqiuAdapter, 'ready', return_value=True):
            r = self.a.fetch_page('600519')
        self.assertFalse(r.ok)
        self.assertFalse(r.net)
        self.assertEqual(self.rebuilds, [])


class TestGuardClassify(unittest.TestCase):
    def _guard(self):
        g = XueqiuGuard.__new__(XueqiuGuard)
        g.stats = SimpleNamespace(requests=0, ok=0, challenged=0)
        g._window = []
        g._risk_streak = 0
        g.adapter = MagicMock()
        g._budget_available = lambda: True
        g._wash = lambda reason: None
        return g

    def test_net_failure_no_ban_streak(self):
        """传输层故障：不进熔断计数、直接返回给上层退避。"""
        g = self._guard()
        g.adapter.fetch_page = MagicMock(return_value=_res(net=True, error='req exc: curl (6)'))
        r = g.fetch('600519')
        self.assertTrue(r.net)
        self.assertEqual(g._risk_streak, 0)

    def test_ban_failure_counts_streak(self):
        """405 封禁类：连击 3 次触发熔断 RuntimeError。"""
        g = self._guard()
        g.adapter.fetch_page = MagicMock(return_value=_res(error='http 405 ct text/html'))
        with self.assertRaises(RuntimeError):
            for _ in range(3):
                g.fetch('600519')
        self.assertGreaterEqual(g._risk_streak, 3)

    def test_success_resets_streak(self):
        g = self._guard()
        g.adapter.fetch_page = MagicMock(return_value=_res(ok=True, items=[]))
        g._risk_streak = 2
        g.fetch('600519')
        self.assertEqual(g._risk_streak, 0)


if __name__ == '__main__':
    unittest.main()
