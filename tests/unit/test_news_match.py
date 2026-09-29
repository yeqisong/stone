"""资讯模块纯逻辑单测（无 DB、无网络）。

覆盖三处「错了不会报错、只会静默产出坏数据」的地方：
- 文本→个股匹配（长名优先/歧义名剔除/关注池限定）
- 内容哈希去重键
- 北京时间口径转换（巨潮毫秒时间戳在非东八区机器上会错一天）
"""
import datetime

from crawler.news import match as nm
from crawler.news import sources as ns


class TestNormalizeName:
    def test_strip_trailing_letter(self):
        # 万科A 与文本里的「万科」应能互命中
        assert nm.normalize_name('万科A') == '万科'
        assert nm.normalize_name('京东方A') == '京东方'

    def test_strip_parenthesis(self):
        assert nm.normalize_name('招商银行(香港)') == '招商银行'

    def test_plain_name_untouched(self):
        assert nm.normalize_name('贵州茅台') == '贵州茅台'

    def test_empty(self):
        assert nm.normalize_name('') == ''
        assert nm.normalize_name(None) == ''


class TestBuildNameIndex:
    def test_skips_too_short(self):
        idx = dict(nm.build_name_index([('000001', '中国'), ('600519', '贵州茅台')]))
        assert idx == {'贵州茅台': '600519'}

    def test_drops_ambiguous_names(self):
        # 同一归一化名对应多个代码 → 整体丢弃（宁可漏，不可错配）
        idx = nm.build_name_index([('000002', '万科A'), ('200002', '万科B')])
        assert idx == []

    def test_sorted_by_length_desc(self):
        idx = nm.build_name_index([('1', '平安银行'), ('2', '中国平安')])
        names = [n for n, _ in idx]
        assert names == sorted(names, key=len, reverse=True)


class TestExtractMatches:
    IDX = None

    @classmethod
    def setup_class(cls):
        cls.IDX = nm.build_name_index([
            ('600519', '贵州茅台'), ('000001', '平安银行'), ('601318', '中国平安'),
            ('300274', '阳光电源'),
        ])

    def test_basic_hit(self):
        assert nm.extract_matches('贵州茅台股价拉升翻红', self.IDX) == ['600519']

    def test_longest_first_prevents_partial_overlap(self):
        # 「中国平安」与「平安银行」共享「平安」，必须先吃长名
        got = nm.extract_matches('中国平安与平安银行同日公告', self.IDX)
        assert set(got) == {'601318', '000001'}

    def test_placeholder_prevents_double_count(self):
        # 命中后片段被抹除，不会因重复出现而重复计数
        got = nm.extract_matches('中国平安中国平安中国平安', self.IDX)
        assert got == ['601318']

    def test_no_hit(self):
        assert nm.extract_matches('央行开展逆回购操作', self.IDX) == []

    def test_empty_inputs(self):
        assert nm.extract_matches('', self.IDX) == []
        assert nm.extract_matches('贵州茅台', []) == []

    def test_limit(self):
        idx = nm.build_name_index([('600519', '贵州茅台'), ('000001', '平安银行')])
        assert len(nm.extract_matches('贵州茅台和平安银行', idx, limit=1)) == 1


class TestContentHash:
    def test_deterministic(self):
        assert ns.content_hash('标题', '正文') == ns.content_hash('标题', '正文')

    def test_title_and_content_both_count(self):
        assert ns.content_hash('标题', '正文') != ns.content_hash('标题', '正文2')
        assert ns.content_hash('标题', '正文') != ns.content_hash('标题2', '正文')

    def test_whitespace_trimmed(self):
        assert ns.content_hash(' 标题 ', '正文') == ns.content_hash('标题', '正文')

    def test_none_safe(self):
        assert len(ns.content_hash(None, None)) == 20

    def test_length(self):
        assert len(ns.content_hash('a', 'b')) == 20


class TestBeijingTime:
    def test_ms_to_dt_is_beijing(self):
        # 1786723200000ms = 2026-08-15 00:00:00 +08:00（巨潮公告时间多落在当地 0 点）
        # 若按主机本地时区（如 UTC）换算会得到 08-14，公告被归到前一天
        dt = ns.ms_to_dt(1786723200000)
        assert dt.tzinfo is not None
        assert dt.utcoffset() == datetime.timedelta(hours=8)
        assert (dt.year, dt.month, dt.day) == (2026, 8, 15)

    def test_ts_to_dt_is_beijing(self):
        dt = ns.ts_to_dt(1786723200)
        assert dt.utcoffset() == datetime.timedelta(hours=8)
        assert (dt.year, dt.month, dt.day) == (2026, 8, 15)

    def test_parse_dt_naive_string_as_beijing(self):
        dt = ns.parse_dt('2026-09-29 10:29:16')
        assert dt.utcoffset() == datetime.timedelta(hours=8)
        assert (dt.hour, dt.minute, dt.second) == (10, 29, 16)

    def test_parse_dt_without_seconds(self):
        assert ns.parse_dt('2026-09-29 10:29') is not None

    def test_bad_inputs(self):
        assert ns.ts_to_dt(None) is None
        assert ns.ts_to_dt('abc') is None
        assert ns.ms_to_dt(None) is None
        assert ns.parse_dt('') is None
        assert ns.parse_dt('not-a-date') is None


class TestNormCode:
    def test_variants(self):
        assert ns.norm_code('sh600487') == '600487'
        assert ns.norm_code('0.300207') == '300207'
        assert ns.norm_code('600519.SH') == '600519'
        assert ns.norm_code('sz301152') == '301152'

    def test_invalid(self):
        assert ns.norm_code('BK0433') is None
        assert ns.norm_code(None) is None
        assert ns.norm_code('') is None


class TestCleanHtml:
    def test_strip_tags_and_entities(self):
        got = ns.clean_html('<p>贵州茅台&amp;五粮液</p><script>x=1</script>')
        assert got == '贵州茅台&五粮液'

    def test_keeps_hash_and_dollar(self):
        # 与雪球 clean_text 有意不同：新闻正文里的 # 与 $ 是正常字符，不剥
        assert ns.clean_html('涨$100#标签') == '涨$100#标签'

    def test_collapse_blank_lines(self):
        assert ns.clean_html('a\n\n\n  b  ') == 'a\nb'

    def test_limit(self):
        assert len(ns.clean_html('x' * 100, limit=10)) == 10

    def test_empty(self):
        assert ns.clean_html('') == ''
