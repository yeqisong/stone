"""后复权→真实价换算层单元测试（app/api/pricing.py）。

背景：纸面组合/信号的撮合价与信号价落库时是后复权口径，界面展示必须换算成
真实价，否则会把累计复权因子（爱尔眼科 66×）当成股价显示。本测试锁住换算
算式、缺行情的 None 语义，以及「不得回退成后复权原值」这条防误读规则。
"""
import pytest

from app.api.pricing import hfq_to_raw_factors, raw_price


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeDB:
    """只记录参数、返回预置行的假 DB（不连库，单测不依赖 PostgreSQL）。"""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.params = None

    def execute(self, stmt, params=None):
        self.params = params
        return _FakeResult(self.rows)


class TestRawPrice:
    def test_缩放保留滑点比例(self):
        # 拓普集团 2026-09-08：撮合价 107.8539(后复权) × (46.25/107.80) ≈ 46.27
        assert raw_price(107.8539, 46.25 / 107.80) == pytest.approx(46.2731, abs=1e-4)

    def test_爱尔眼科数量级修正(self):
        # 真实收盘 8.35 / 后复权 556.179：不换算会显示成 556 元的股票
        assert raw_price(556.4571, 8.35 / 556.179) == pytest.approx(8.3542, abs=1e-4)

    def test_缺因子回None不回退后复权值(self):
        assert raw_price(107.8539, None) is None
        assert raw_price(107.8539, 0) is None

    def test_价格为None(self):
        assert raw_price(None, 0.429) is None


class TestHfqToRawFactors:
    def test_按代码日期取值(self):
        db = _FakeDB([('601689', '2026-09-08', 46.25, 107.80),
                      ('300015', '2026-09-08', 8.35, 556.179)])
        fac = hfq_to_raw_factors(db, [('601689', '2026-09-08'), ('300015', '2026-09-08')])
        assert fac[('601689', '2026-09-08')] == pytest.approx(46.25 / 107.80)
        assert fac[('300015', '2026-09-08')] == pytest.approx(8.35 / 556.179)

    def test_去重并只查一次(self):
        db = _FakeDB([('601689', '2026-09-08', 46.25, 107.80)])
        fac = hfq_to_raw_factors(db, [('601689', '2026-09-08'), ('601689', '2026-09-08')])
        assert len(fac) == 1
        assert db.params['c'] == ['601689']          # 代码去重
        assert db.params['d'] == ['2026-09-08']      # 日期去重

    def test_时间戳归一为日期(self):
        db = _FakeDB([('601689', '2026-09-08', 46.25, 107.80)])
        fac = hfq_to_raw_factors(db, [('601689', '2026-09-08 00:00:00+08:00')])
        assert ('601689', '2026-09-08') in fac

    def test_缺行情的代码不出现在结果里(self):
        db = _FakeDB([('601689', '2026-09-08', 46.25, 107.80)])
        fac = hfq_to_raw_factors(db, [('601689', '2026-09-08'), ('999999', '2026-09-08')])
        assert ('999999', '2026-09-08') not in fac
        assert raw_price(100.0, fac.get(('999999', '2026-09-08'))) is None

    def test_空入参不查库(self):
        db = _FakeDB()
        assert hfq_to_raw_factors(db, []) == {}
        assert hfq_to_raw_factors(db, [(None, None), ('', '')]) == {}
        assert db.params is None
