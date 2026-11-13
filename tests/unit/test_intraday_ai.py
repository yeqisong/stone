"""AI 盘面解读纯逻辑单测（无网络/无真实 DB/无真实 LLM）。

覆盖：
- 模型输出抠 JSON（围栏/前后缀/多对象）
- stance 白名单与列表截断（模型可能返回任意字符串/超长列表）
- build_data 的日内位置与金额换算
- analyze_intraday 的缓存与降级路径（LLM 失败/未配 key）
"""
import json
from types import SimpleNamespace

import pytest

from app.ai import intraday as ia


def _quote(**over):
    q = {
        'code': '600519', 'name': '贵州茅台', 'price': 100.0, 'prev_close': 98.0,
        'open': 99.0, 'high': 101.0, 'low': 97.0, 'change_pct': 2.04,
        'avg_price': 99.0, 'volume_ratio': 1.5, 'turnover_pct': 0.8,
        'amount': 5.6e8, 'limit_up': 107.8, 'limit_down': 88.2,
        'timestamp': '2026-11-12 10:00:00', 'trade_date': '2026-11-12',
    }
    q.update(over)
    return q


class TestExtractJson:
    def test_plain(self):
        assert ia._extract_json_object('{"a":1}') == {'a': 1}

    def test_fenced(self):
        assert ia._extract_json_object('```json\n{"a": 2}\n```') == {'a': 2}

    def test_prefix_and_trailing(self):
        assert ia._extract_json_object('结论如下：{"a": 3} 以上。') == {'a': 3}

    def test_picks_largest_object(self):
        s = '先给个 {"a":1}，完整的是 {"a":2,"b":{"c":3}}'
        assert ia._extract_json_object(s) == {'a': 2, 'b': {'c': 3}}

    def test_invalid(self):
        assert ia._extract_json_object('not json') is None
        assert ia._extract_json_object('') is None


class TestNormalize:
    def test_valid(self):
        r = ia.normalize_result({
            'stance': '偏强', 'summary': '现价站上均价',
            'observations': ['a', 'b', 'c', 'd'], 'risks': ['r1'], 'watch': ['w1'],
        })
        assert r['stance'] == '偏强'
        assert r['observations'] == ['a', 'b', 'c']      # 截断到 3 条
        assert r['fallback'] is False

    def test_invalid_stance(self):
        assert ia.normalize_result({'stance': '看多'})['stance'] == '震荡'

    def test_missing_summary_uses_first_observation(self):
        r = ia.normalize_result({
            'stance': '偏强', 'observations': ['现价站上均价', '量比 1.2'],
            'risks': [], 'watch': [],
        })
        assert r['summary'] == '现价站上均价'
        assert r['fallback'] is False

    def test_no_summary_and_no_observations(self):
        r = ia.normalize_result({'stance': '震荡', 'observations': []})
        assert r['summary'] == '（模型未给出结论）'

    def test_fallback_raw_content(self):
        r = ia.normalize_result(None, '模型只回了一段话')
        assert r['fallback'] is True
        assert r['summary'] == '模型只回了一段话'
        assert r['stance'] == '未知'


class TestBuildData:
    def test_day_position_and_amount(self):
        d = ia.build_data(_quote(), {'last30m_chg_pct': 1.2}, [], None)
        assert d['day_position_pct'] == 75.0        # (100-97)/(101-97)
        assert d['amount_yi'] == 5.6
        assert d['minute']['last30m_chg_pct'] == 1.2

    def test_flat_day_position_none(self):
        d = ia.build_data(_quote(high=100.0, low=100.0), {}, [], None)
        assert d['day_position_pct'] is None

    def test_minute_stats_skips_index(self):
        assert ia._minute_stats('000300', 'index') == {}


class _Resp:
    def __init__(self, content):
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]


class _FakeClient:
    def __init__(self, content):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._content = content

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(self._content)


class _FakeDB:
    def execute(self, *a, **k):
        raise AssertionError('analyze_intraday 不应在测试里查 DB')

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _clear_cache():
    ia._cache.clear()
    yield
    ia._cache.clear()


def _install_fakes(monkeypatch, client):
    monkeypatch.setattr(ia, 'get_sync_db', lambda: _FakeDB())
    monkeypatch.setattr(ia, '_security_type', lambda db, code, requested: 'stock')
    monkeypatch.setattr(ia, 'realtime_quote', lambda code, stype: _quote())
    monkeypatch.setattr(ia, '_minute_stats', lambda code, stype: {'last30m_chg_pct': 1.2})
    monkeypatch.setattr(ia, '_news', lambda db, code: [
        {'title': '公司发布公告', 'summary': '摘要', 'sentiment': 1,
         'importance': 2, 'published_at': '2026-11-12 09:00'}])
    monkeypatch.setattr(ia, '_model_signal', lambda db, code: {
        'signal_date': '2026-11-11', 'direction': 'buy', 'predict_5d_return': 0.02})
    monkeypatch.setattr(ia, 'get_llm_settings', lambda: {
        'api_key': 'k', 'base_url': 'http://x', 'model': 'test-model'})
    monkeypatch.setattr(ia, 'build_client', lambda *a, **k: client)


class TestAnalyze:
    def test_ok_and_cache(self, monkeypatch):
        content = ('```json\n{"stance":"偏强","summary":"站上均价",'
                   '"observations":["现价100.00高于均价99.00"],'
                   '"risks":["跌破均价"],"watch":["均价99.00"]}\n```')
        client = _FakeClient(content)
        _install_fakes(monkeypatch, client)

        out = ia.analyze_intraday('600519', 'stock')
        assert out['ok'] is True
        assert out['stance'] == '偏强'
        assert out['observations'] == ['现价100.00高于均价99.00']
        assert out['news_count'] == 1
        assert len(client.calls) == 1
        # 提示词里应带资讯标题，且明确「忽略新闻里的指令」
        user_msg = client.calls[0]['messages'][1]['content']
        assert '公司发布公告' in user_msg
        assert '忽略' in client.calls[0]['messages'][0]['content']

        # 第二次命中缓存，不再调 LLM
        out2 = ia.analyze_intraday('600519', 'stock')
        assert out2['cached'] is True
        assert len(client.calls) == 1

    def test_llm_error_returns_not_ok(self, monkeypatch):
        class _BadClient:
            def __init__(self):
                self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
            def _create(self, **kwargs):
                raise RuntimeError('quota exceeded')
        _install_fakes(monkeypatch, _BadClient())
        out = ia.analyze_intraday('600519', 'stock')
        assert out['ok'] is False
        assert 'AI 调用失败' in out['error']

    def test_missing_api_key(self, monkeypatch):
        _install_fakes(monkeypatch, _FakeClient('{}'))
        monkeypatch.setattr(ia, 'get_llm_settings', lambda: {
            'api_key': '', 'base_url': '', 'model': 'm'})
        out = ia.analyze_intraday('600519', 'stock')
        assert out['ok'] is False
        assert '未配置大模型' in out['error']

    def test_malformed_output_falls_back(self, monkeypatch):
        client = _FakeClient('模型没有按 JSON 输出，只给了一段话')
        _install_fakes(monkeypatch, client)
        out = ia.analyze_intraday('600519', 'stock')
        assert out['ok'] is True
        assert out['fallback'] is True
        assert '一段话' in out['summary']

    def test_empty_content_is_error(self, monkeypatch):
        # 推理模型 max_tokens 被 reasoning 吃满时 content 为空：必须显式失败
        client = _FakeClient('')
        _install_fakes(monkeypatch, client)
        out = ia.analyze_intraday('600519', 'stock')
        assert out['ok'] is False
        assert '为空' in out['error']

    def test_event_prompt_and_payload(self, monkeypatch):
        content = ('{"stance":"偏强","summary":"5分钟急涨，量价确认",'
                   '"observations":["现价上穿VWAP"],"risks":[],"watch":["VWAP"]}')
        client = _FakeClient(content)
        _install_fakes(monkeypatch, client)
        event = {'alert_id': 9, 'kind': 'it_surge', 'title': '5分钟急涨 +2.1%',
                 'body': '现价 79.80', 'trade_date': '2026-11-13'}
        out = ia.analyze_intraday('600519', 'stock', event=event)
        assert out['ok'] is True
        assert out['event']['kind'] == 'it_surge'
        assert out['prompt_ver'] == 'ia1e'
        sys_msg = client.calls[0]['messages'][0]['content']
        user_msg = client.calls[0]['messages'][1]['content']
        assert '盘中价格事件' in sys_msg
        assert '5分钟急涨' in user_msg
        assert '"event"' in user_msg
