"""一致预期数据链路的纯逻辑单测（v3.27 审查项 15；此前这部分没有测试）。

三块：
- report_rc 节点的候选日规则（当天发布时点之前不请求）与抽样水位线；
- 适配器「拉取失败 ≠ 结果为空」：失败必须抛 SourceFetchError，空才是空；
- 均不出网、不连库（FakeDB 按 SQL 片段应答，tushare pro 用打桩对象）。
"""
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from crawler.adapters import tushare_adapter as ta
from crawler.adapters.tushare_quota import QuotaExhausted
from scripts.pipeline import (RC_PUBLISH_HOUR, _rc_pending_samples,
                              _rc_recent_days, _rc_sample_dates)


def _weekday(d) -> bool:
    """生产里的谓词是 lambda d: _is_trade_day(db, d)，d 是 ISO 字符串。"""
    return date.fromisoformat(str(d)[:10]).weekday() < 5


class TestRcRecentDays(unittest.TestCase):
    """日增量候选日：最近 3 个**自然日**里的交易日（周一往前数只有周一自己，
    周末的缺口靠周六/周日的回补流程覆盖）；当天发布时点之前不进候选。"""

    WED = date(2026, 12, 2)

    def test_before_publish_today_is_skipped(self):
        # 周三 02:30（回补流程命中配额重置的那次）：当天还没发布，只补前两天
        days = _rc_recent_days(self.WED, '2026-12-02', datetime(2026, 12, 2, 2, 30), _weekday)
        self.assertEqual(days, ['2026-12-01', '2026-11-30'])

    def test_after_publish_today_is_included(self):
        days = _rc_recent_days(self.WED, '2026-12-02',
                               datetime(2026, 12, 2, RC_PUBLISH_HOUR, 30), _weekday)
        self.assertEqual(days, ['2026-12-02', '2026-12-01', '2026-11-30'])

    def test_monday_only_covers_monday(self):
        days = _rc_recent_days(date(2026, 11, 30), '2026-11-30',
                               datetime(2026, 11, 30, 23, 0), _weekday)
        self.assertEqual(days, ['2026-11-30'])           # 周日/周六不是交易日

    def test_holiday_via_calendar_predicate(self):
        """交易日判定走调用方传入的谓词（trade_calendar），节假日不进候选。"""
        thu = date(2026, 10, 8)                           # 假期最后一天，往前 3 天都是假期
        holidays = {f'2026-10-{d:02d}' for d in range(1, 9)}
        is_td = lambda d: _weekday(d) and str(d)[:10] not in holidays   # noqa: E731
        self.assertEqual(_rc_recent_days(thu, '2026-10-08', datetime(2026, 10, 8, 22, 0), is_td), [])
        # 假期结束后的第一个交易日（10-09 周五）盘后：窗口内仍只有它自己
        self.assertEqual(
            _rc_recent_days(date(2026, 10, 9), '2026-10-09', datetime(2026, 10, 9, 23, 0), is_td),
            ['2026-10-09'])


class FakeRcDB:
    """只回答 report_rc 节点用到的三类查询：交易日历、已入库日期、已登记日期。"""

    def __init__(self, calendar, have=(), checked=()):
        self.calendar = [date.fromisoformat(d) if isinstance(d, str) else d for d in calendar]
        self.have, self.checked = set(have), set(checked)

    def execute(self, sql, params=None):
        s = str(sql)
        if 'FROM trade_calendar' in s:
            lo, hi = params['s'], params['e']
            rows = [(d,) for d in self.calendar if lo <= d.isoformat() <= hi]
        elif 'FROM stock_report_rc' in s:
            rows = [(d,) for d in sorted(self.have) if d >= params['s']]
        elif 'backfill_ext_checked' in s:
            rows = [(d,) for d in sorted(self.checked) if d >= params['s']]
        else:
            raise AssertionError(f'未预期的 SQL: {s[:80]}')

        class _R:
            def fetchall(self_inner):
                return rows
        return _R()


class TestRcSampleWatermark(unittest.TestCase):
    """抽样日轴与水位线：每 20 个交易日一天；已入库/已登记的不再待补。"""

    def setUp(self):
        # 2026 年的交易日（去掉周末），约 260 天
        d, out = date(2026, 1, 1), []
        while len(out) < 130:
            if d.weekday() < 5:
                out.append(d)
            d += timedelta(days=1)
        self.calendar = out

    def test_sample_axis_every_20_trading_days(self):
        axis = _rc_sample_dates(FakeRcDB(self.calendar), '2026-12-31')
        self.assertEqual(axis[:3], [self.calendar[0].isoformat(),
                                    self.calendar[20].isoformat(),
                                    self.calendar[40].isoformat()])

    def test_pending_excludes_have_and_checked(self):
        axis = _rc_sample_dates(FakeRcDB(self.calendar), '2026-12-31')
        db = FakeRcDB(self.calendar, have={axis[1], axis[3]}, checked={axis[2]})
        self.assertEqual(_rc_pending_samples(db, '2026-12-31'), [axis[0]] + axis[4:])

    def test_nothing_pending(self):
        axis = _rc_sample_dates(FakeRcDB(self.calendar), '2026-12-31')
        db = FakeRcDB(self.calendar, have=axis)
        self.assertEqual(_rc_pending_samples(db, '2026-12-31'), [])


def _stub_adapter(pro):
    """绕过 __init__（会 import tushare / 设 token）造一个只带打桩依赖的适配器。"""
    a = ta.TuShareAdapter.__new__(ta.TuShareAdapter)
    a._pro = pro
    a.quota = mock.Mock()                  # consume() 空操作
    a._ts_code = lambda c: c + '.SZ'
    a._norm_date = lambda v: v
    return a


class TestFetchFailureIsNotEmpty(unittest.TestCase):
    """适配器：拉取失败抛 SourceFetchError；「真的没有」才返回空列表。"""

    def test_report_rc_failure_raises(self):
        pro = mock.Mock()
        pro.report_rc.side_effect = RuntimeError('Connection reset by peer')
        a = _stub_adapter(pro)
        with mock.patch.object(ta, '_report_rc_gate', lambda: None):
            with self.assertRaises(ta.SourceFetchError) as ctx:
                a.fetch_report_rc('2026-11-20')
        self.assertIn('Connection reset', str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)

    def test_report_rc_rate_limit_stays_quota_exhausted(self):
        pro = mock.Mock()
        pro.report_rc.side_effect = Exception('抱歉，您访问接口(report_rc)频率超限(10次/天)')
        a = _stub_adapter(pro)
        with mock.patch.object(ta, '_report_rc_gate', lambda: None):
            with self.assertRaises(QuotaExhausted):
                a.fetch_report_rc('2026-11-20')

    def test_report_rc_genuinely_empty_returns_empty(self):
        pro = mock.Mock()
        pro.report_rc.return_value = object()          # 空 DataFrame 的替身
        a = _stub_adapter(pro)
        a._rows = lambda df, mapping: []
        with mock.patch.object(ta, '_report_rc_gate', lambda: None):
            self.assertEqual(a.fetch_report_rc('2026-11-20'), [])

    def test_income_failure_raises(self):
        pro = mock.Mock()
        pro.income.side_effect = RuntimeError('remote disconnected')
        a = _stub_adapter(pro)
        with mock.patch.object(ta, '_rl', lambda: None):
            with self.assertRaises(ta.SourceFetchError):
                a.fetch_income_by_code('000001')

    def test_income_rows_without_dates_are_dropped_but_not_an_error(self):
        pro = mock.Mock()
        pro.income.return_value = object()
        a = _stub_adapter(pro)
        a._rows = lambda df, mapping: [
            {'stock_code': '000001.SZ', 'end_date': '2025-12-31', 'ann_date': '2026-04-30',
             'report_type': '1'},
            {'stock_code': '000001.SZ', 'end_date': None, 'ann_date': '2026-04-30',
             'report_type': '1'},                       # 缺报告期 → 丢
            {'stock_code': '000001.SZ', 'end_date': '2025-09-30', 'ann_date': None,
             'report_type': '1'},                       # 缺公告日 → 无法 PIT，丢
        ]
        with mock.patch.object(ta, '_rl', lambda: None):
            rows = a.fetch_income_by_code('000001')
        self.assertEqual([r['end_date'] for r in rows], ['2025-12-31'])


if __name__ == '__main__':
    unittest.main()
