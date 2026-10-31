"""新闻采集 HTTP 会话自愈单测——连接类异常重建 Session 并重试，业务异常不重建。"""
import unittest
from unittest.mock import patch, MagicMock

from crawler.news import sources


class TestSessionSelfHeal(unittest.TestCase):
    def setUp(self):
        self.rebuilds = []
        self.orig_rebuild = sources._rebuild_session
        sources._rebuild_session = lambda reason='': self.rebuilds.append(reason)

    def tearDown(self):
        sources._rebuild_session = self.orig_rebuild

    def test_conn_error_triggers_rebuild_and_retry(self):
        calls = []
        def fake_get(url, **kw):
            calls.append(url)
            if len(calls) == 1:
                raise RuntimeError("Failed to perform, curl: (6) Could not resolve host")
            return "OK"
        fake = MagicMock()
        fake.get = fake_get
        with patch.object(sources, '_SESSION', fake):
            r = sources._session_request('GET', 'https://x/test')
        self.assertEqual(r, "OK")
        self.assertEqual(len(calls), 2, "重建后应立即重试一次")
        self.assertEqual(len(self.rebuilds), 1)
        self.assertIn('curl: (6)', self.rebuilds[0])

    def test_business_error_no_rebuild(self):
        """HTTP 返回的业务错误（如 code!=20000 的 RuntimeError 由上层抛）不算连接故障。"""
        fake = MagicMock()
        fake.get.return_value = "resp"
        with patch.object(sources, '_SESSION', fake):
            r = sources._session_request('GET', 'https://x/test')
        self.assertEqual(r, "resp")
        self.assertEqual(self.rebuilds, [])

    def test_second_failure_raises(self):
        def fake_get(url, **kw):
            raise RuntimeError("DNSError: no resolve")
        fake = MagicMock()
        fake.get = fake_get
        with patch.object(sources, '_SESSION', fake):
            with self.assertRaises(RuntimeError):
                sources._session_request('GET', 'https://x/test')
        self.assertEqual(len(self.rebuilds), 1)

    def test_post_path_rebuilds_too(self):
        calls = []
        fake = MagicMock()
        def fake_post(url, **kw):
            calls.append(url)
            if len(calls) == 1:
                raise RuntimeError("curl: (7) Failed to connect")
            return "OK"
        fake.post = fake_post
        with patch.object(sources, '_SESSION', fake):
            r = sources._session_request('POST', 'https://x/p', data={'a': 1})
        self.assertEqual(r, "OK")
        self.assertEqual(len(self.rebuilds), 1)


if __name__ == '__main__':
    unittest.main()
