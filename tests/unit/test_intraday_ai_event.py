"""事件 AI worker 单测（无 DB / 无 LLM / 无线程）。

覆盖「错了会重复烧 token 或该解读时不解读」的路径：
- 只在 ai_event_kinds 内、且同股冷却/单日上限未触发时调用 LLM
- 同一 alert_id 只解读一次
- done/failed 都落 intraday_ai_event，failed 保留 error
"""
import json
from datetime import date, datetime

import pytest

from app import intraday_ai as iai


_MISSING = object()


class _Res:
    def __init__(self, rows=None, scalar=None):
        self.rows = rows or []
        self.scalar_val = scalar

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def scalar(self):
        return self.scalar_val


class _DB:
    def __init__(self, rules=None, alert=_MISSING, existing_alert=False,
                 cooldown=False, ai_count=0):
        self.rules = rules if rules is not None else {
            'ai_event_enabled': True,
            'ai_event_kinds': ['it_stop', 'it_surge', 'it_drop'],
            'ai_event_cooldown_min': 15,
            'ai_event_max_per_day': 10,
        }
        self.alert = (1, date(2026, 11, 13), 'it_surge', '600519',
                      '贵州茅台 盘中急涨', '5分钟 +2.1%') if alert is _MISSING else alert
        self.existing_alert = existing_alert
        self.cooldown = cooldown
        self.ai_count = ai_count
        self.inserts = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if 'FROM strategy_config' in sql:
            return _Res(rows=[(json.dumps(self.rules, ensure_ascii=False), True)])
        if 'FROM risk_alerts WHERE id' in sql:
            return _Res(rows=[self.alert] if self.alert else [])
        if 'FROM intraday_ai_event WHERE alert_id' in sql:
            return _Res(rows=[(1,)] if self.existing_alert else [])
        if 'count(*)' in sql:
            return _Res(scalar=self.ai_count)
        if 'FROM intraday_ai_event WHERE stock_code' in sql:
            return _Res(rows=[(1,)] if self.cooldown else [])
        if sql.strip().startswith('INSERT'):
            self.inserts.append(dict(params))
            return _Res()
        return _Res()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def fake_analyze(monkeypatch):
    calls = []

    def _fake(code, security_type=None, refresh=False, event=None):
        calls.append({'code': code, 'event': event, 'refresh': refresh})
        return {'ok': True, 'name': '贵州茅台', 'stance': '偏强',
                'summary': '5分钟急涨，量价确认'}

    import app.ai.intraday as ai_mod
    monkeypatch.setattr(ai_mod, 'analyze_intraday', _fake)
    return calls


class TestProcessEvent:
    def _install(self, monkeypatch, db):
        monkeypatch.setattr(iai, 'get_sync_db', lambda: db)
        return db

    def test_done_inserts_result(self, monkeypatch, fake_analyze):
        db = self._install(monkeypatch, _DB())
        iai.process_event({'alert_id': 1})
        assert len(fake_analyze) == 1
        assert fake_analyze[0]['event']['kind'] == 'it_surge'
        assert fake_analyze[0]['refresh'] is True
        assert len(db.inserts) == 1
        ins = db.inserts[0]
        assert ins['s'] == 'done'
        assert ins['c'] == '600519'
        assert json.loads(ins['r'])['summary'] == '5分钟急涨，量价确认'

    def test_failed_keeps_error(self, monkeypatch):
        db = self._install(monkeypatch, _DB())
        import app.ai.intraday as ai_mod
        monkeypatch.setattr(ai_mod, 'analyze_intraday',
                            lambda *a, **k: {'ok': False, 'error': 'quota exceeded'})
        iai.process_event({'alert_id': 1})
        ins = db.inserts[0]
        assert ins['s'] == 'failed'
        assert ins['r'] is None
        assert 'quota' in ins['e']

    def test_skip_cooldown(self, monkeypatch, fake_analyze):
        db = self._install(monkeypatch, _DB(cooldown=True))
        iai.process_event({'alert_id': 1})
        assert fake_analyze == []
        assert db.inserts == []

    def test_skip_daily_cap(self, monkeypatch, fake_analyze):
        db = self._install(monkeypatch, _DB(ai_count=10))
        iai.process_event({'alert_id': 1})
        assert fake_analyze == []
        assert db.inserts == []

    def test_skip_kind_not_in_config(self, monkeypatch, fake_analyze):
        db = self._install(monkeypatch, _DB(rules={
            'ai_event_enabled': True, 'ai_event_kinds': ['it_stop'],
            'ai_event_cooldown_min': 15, 'ai_event_max_per_day': 10,
        }))
        iai.process_event({'alert_id': 1})
        assert fake_analyze == []
        assert db.inserts == []

    def test_skip_disabled(self, monkeypatch, fake_analyze):
        db = self._install(monkeypatch, _DB(rules={
            'ai_event_enabled': False, 'ai_event_kinds': ['it_surge'],
            'ai_event_cooldown_min': 15, 'ai_event_max_per_day': 10,
        }))
        iai.process_event({'alert_id': 1})
        assert fake_analyze == []
        assert db.inserts == []

    def test_skip_same_alert(self, monkeypatch, fake_analyze):
        db = self._install(monkeypatch, _DB(existing_alert=True))
        iai.process_event({'alert_id': 1})
        assert fake_analyze == []
        assert db.inserts == []

    def test_new_level_kind_goes_through_same_bus(self, monkeypatch, fake_analyze):
        db = self._install(monkeypatch, _DB(
            alert=(1, date(2026, 11, 13), 'it_or_up', '600519',
                   '贵州茅台 突破开盘区间', '现价 101.80，区间 98.50-101.50'),
            rules={'ai_event_enabled': True, 'ai_event_kinds': ['it_or_up'],
                   'ai_event_cooldown_min': 15, 'ai_event_max_per_day': 10},
        ))
        iai.process_event({'alert_id': 1})
        assert len(fake_analyze) == 1
        assert fake_analyze[0]['event']['kind'] == 'it_or_up'
        assert db.inserts[0]['k'] == 'it_or_up'

    def test_missing_alert(self, monkeypatch, fake_analyze):
        db = self._install(monkeypatch, _DB(alert=None))
        iai.process_event({'alert_id': 1})
        assert fake_analyze == []
        assert db.inserts == []

    def test_no_alert_id(self, monkeypatch, fake_analyze):
        self._install(monkeypatch, _DB())
        iai.process_event({})
        assert fake_analyze == []
