"""快讯备源状态语义单测——主源正常时备源待命不误报停摆。"""
import unittest
from unittest.mock import MagicMock, patch

from crawler.news import collect
from crawler.news import sources as nsrc


class TestStandbyState(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.writes = []
        # cls 成功、备源不应被尝试
        self.fetch_calls = []
        self.orig = {}
        for name, fn, label in nsrc.FLASH_SOURCES:
            self.orig[name] = fn

    def tearDown(self):
        for name, fn, label in nsrc.FLASH_SOURCES:
            setattr(nsrc, name if False else fn.__name__ if hasattr(fn, '__name__') else '', fn)

    def test_cls_success_marks_standby(self):
        """主源 cls 成功：备源 state 写入「主源正常，本轮未启用」且不触 last_fetch。"""
        calls = []
        def fake_cls(cursor, pages):
            calls.append('cls')
            return [{'source_id': '1', 'title': 't', 'content': 'c',
                     'published_at': __import__('datetime').datetime(2026, 10, 31, 12, 0),
                     'url': '', 'source_id': '1'}], '1690000000', ''
        def fake_should_not_run(cursor, pages):
            calls.append('backup')
            return [], '', ''
        flash = [('cls', fake_cls, '财联社'),
                 ('em724', fake_should_not_run, '东财7×24'),
                 ('wscn', fake_should_not_run, '华尔街见闻')]
        with patch.object(nsrc, 'FLASH_SOURCES', flash), \
             patch.object(collect, 'flush', return_value=0), \
             patch.object(collect, 'state_get', side_effect=lambda db, s: {
                 'cursor': '', 'last_published_at': None, 'last_fetched_at': None,
                 'last_ok': True, 'fail_streak': 0, 'note': ''}), \
             patch.object(collect, 'state_set',
                          side_effect=lambda db, s, **kw: self.writes.append((s, kw))):
            r = collect.collect_flash(MagicMock(), {'max_pages': 1}, {}, [])
        self.assertEqual(r, 'cls')
        self.assertEqual(calls, ['cls'], "备源不应被尝试")
        standby = [(s, kw) for s, kw in self.writes if s in ('em724', 'wscn')]
        self.assertEqual(len(standby), 2)
        for s, kw in standby:
            self.assertEqual(kw['note'], '主源正常，本轮未启用')
            self.assertFalse(kw['touch_fetched'], "备源待命不应触 last_fetch")


if __name__ == '__main__':
    unittest.main()
