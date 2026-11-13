"""盘中信号结果追踪单测（无 DB/网络）：方向调整、horizon、MFE/MAE、状态。"""
from datetime import date, datetime
from types import SimpleNamespace

from app import intraday_outcome as io


class _DB:
    def __init__(self, bars=None):
        self.bars = bars or []
        self.inserts = []
        self.commits = 0

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if 'FROM stock_minute_bar' in sql:
            return _Res(rows=self.bars)
        if 'INSERT INTO intraday_signal_outcome' in sql:
            self.inserts.append(dict(params))
            return _Res()
        return _Res()

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


class _Res:
    def __init__(self, rows=None):
        self.rows = rows or []

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def scalar(self):
        return None


def _alert(kind='it_surge', created=datetime(2026, 11, 13, 9, 30, 13),
           trade_date=date(2026, 11, 13), body='现价 100.00，5 分钟前 98.00，涨幅 +2.04%',
           trigger=None):
    return SimpleNamespace(id=1, stock_code='600519', kind=kind, trade_date=trade_date,
                           created_at=created, body=body, trigger_price=trigger)


def _bars(rows):
    return [(datetime(2026, 11, 13, h, m), high, low, close) for h, m, high, low, close in rows]


class TestPriceDirection:
    def test_price_from_trigger_and_body(self):
        assert io._price(12.34, '现价 9.99') == 12.34
        assert io._price(None, '现价 9.99，涨幅') == 9.99
        assert io._price(None, '无价格') is None

    def test_direction_map(self):
        assert io._direction('it_surge') == 1
        assert io._direction('it_vwap_down') == -1
        assert io._direction('it_stop') == 0


class TestEvaluateOne:
    def test_long_full_horizons(self):
        bars = _bars([
            (9, 30, 101, 99, 100), (9, 35, 103, 100, 102),
            (9, 45, 104, 102, 103), (10, 0, 105, 103, 104),
            (10, 30, 106, 104, 105),
        ])
        db = _DB(bars)
        status = io.evaluate_one(db, _alert(), datetime(2026, 11, 13, 10, 31))
        ins = db.inserts[-1]
        assert status == 'done' and ins['s'] == 'done'
        assert round(ins['r5'], 4) == 0.02
        assert round(ins['r15'], 4) == 0.03
        assert round(ins['r30'], 4) == 0.04
        assert round(ins['r60'], 4) == 0.05
        assert round(ins['mfe'], 4) == 0.06
        assert round(ins['mae'], 4) == -0.01
        assert ins['hit'] is True

    def test_short_direction_adjusted(self):
        bars = _bars([
            (9, 30, 100, 98, 99), (9, 35, 99, 97, 98),
            (9, 45, 98, 96, 97), (10, 0, 97, 95, 96),
            (10, 30, 96, 94, 95),
        ])
        db = _DB(bars)
        io.evaluate_one(db, _alert('it_drop'), datetime(2026, 11, 13, 10, 31))
        ins = db.inserts[-1]
        assert ins['dir'] == -1
        assert round(ins['r5'], 4) == 0.02       # 100→98，做空有利 +2%
        assert round(ins['mfe'], 4) == 0.06      # 最低 94
        assert round(ins['mae'], 4) == 0.0       # 最高 100
        assert ins['hit'] is True

    def test_partial_waits_for_horizons(self):
        bars = _bars([(9, 30, 101, 99, 100), (9, 35, 102, 100, 101)])
        db = _DB(bars)
        status = io.evaluate_one(db, _alert(), datetime(2026, 11, 13, 9, 40))
        ins = db.inserts[-1]
        assert status == 'partial' and ins['s'] == 'partial'
        assert ins['r5'] is not None
        assert ins['r15'] is None and ins['r30'] is None and ins['r60'] is None
        assert ins['hit'] is None

    def test_neutral_has_no_hit(self):
        bars = _bars([(9, 30, 101, 99, 100), (9, 35, 102, 100, 101),
                      (9, 45, 102, 100, 101), (10, 0, 102, 100, 101),
                      (10, 30, 102, 100, 101)])
        db = _DB(bars)
        io.evaluate_one(db, _alert('it_stop'), datetime(2026, 11, 13, 10, 31))
        assert db.inserts[-1]['hit'] is None

    def test_no_bars_today_pending_old_no_data(self):
        db = _DB([])
        st = io.evaluate_one(db, _alert(), datetime(2026, 11, 13, 10, 0))
        assert st == 'pending'
        st2 = io.evaluate_one(db, _alert(trade_date=date(2026, 11, 10)),
                              datetime(2026, 11, 13, 10, 0))
        assert st2 == 'no_data'
