"""实时大盘聚合单元测试（design/13）。

纯函数 + fixture 回放，不出网、不连库。
fixtures 是 2026-11-27 实测接口样本（见 design/13 §数据底座实测）：
- em_*.json：东财 push2/push2ex 域（push2his 指纹墙已弃用，见模块文档）
- tx_minute.json：腾讯 minute/query 当日分时
- sina_min5.json：新浪 5 分钟 K（量能对比）
"""
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from crawler import em_quote
from crawler.adapters.tencent_adapter import _parse_minute_payload
from app.market_realtime import (
    aggregate_breadth,
    estimate_turnover,
    zt_summary,
    _session_of,
    _merge_min5_buckets,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class TestEmQuoteParsers(unittest.TestCase):
    """东财接口解析器：fixture 回放（SSE data: 前缀 + 纯 JSON 两种形态）。"""

    def test_sse_fflow_parse(self):
        """fflow（push2 域）SSE 流解析：240 分钟行。"""
        obj = em_quote._loads_json(_fixture("em_fflow.json"))
        klines = obj["data"]["klines"]
        self.assertEqual(len(klines), 240)
        parts = klines[-1].split(",")
        self.assertEqual(len(parts), 6)
        # 收盘累计主力净流入为有限数值
        self.assertIsInstance(float(parts[1]), float)

    def test_fflow_rows_carry_data_date(self):
        """资金流行带 date：盘前/休市日东财返回的是上一交易日整段，要能识别新旧。"""
        class _Resp:
            def __init__(self, t):
                self.text = t

        with mock.patch.object(em_quote, 'em_get', lambda *a, **k: _Resp(_fixture('em_fflow.json'))):
            rows = em_quote.fetch_market_fflow()
        self.assertEqual({r['date'] for r in rows}, {'2026-11-27'})
        self.assertEqual((rows[0]['t'], rows[-1]['t']), ('09:31', '15:00'))
        self.assertTrue(all(r['main'] is not None for r in rows))

    def test_sse_skips_null_heartbeat_lines(self):
        """SSE 流中的 data:null 心跳行应被跳过，取首个 data 非空行。"""
        text = 'data: null\n\ndata: {"rc":0,"data":{"klines":["x"]}}\n\n'
        obj = em_quote._loads_json(text)
        self.assertEqual(obj["data"]["klines"], ["x"])

    def test_plain_json_boards_parse(self):
        """clist 行业板块是纯 JSON（无 data: 前缀）。"""
        obj = em_quote._loads_json(_fixture("em_boards.json"))
        rows = obj["data"]["diff"]
        self.assertTrue(rows)
        self.assertIn("f62", rows[0])

    def test_zt_pool_fixture_shape(self):
        """涨停池字段映射口径（p×1000、fbt HHMMSS、lbc/zbc int）。"""
        raw = json.loads(_fixture("em_ztpool.json"))
        pool = (raw.get("data") or {}).get("pool") or []
        self.assertTrue(pool)
        it = pool[0]
        self.assertAlmostEqual(em_quote._num(it["p"]) / 1000, it["p"] / 1000)
        self.assertTrue(em_quote._hhmmss(it["fbt"]).startswith(("0", "1")))
        self.assertIsNone(em_quote._hhmmss(0))
        self.assertIsNone(em_quote._hhmmss(None))


class TestTencentMinuteParse(unittest.TestCase):
    """腾讯 minute/query 解析（tx_minute.json 为 2026-11-27 上证实测样本）。"""

    def setUp(self):
        self.payload = json.loads(_fixture("tx_minute.json"))

    def test_parse_points(self):
        out = _parse_minute_payload(self.payload, "sh000001")
        self.assertEqual(out["symbol"], "sh000001")
        self.assertTrue(out["name"])            # 指数名称（上证指数）
        # 数据所属交易日（YYYYMMDD）：盘前/休市日腾讯返回上一交易日整段，靠它识别
        self.assertEqual(out["date"], "20261127")
        self.assertIsNotNone(out["preClose"])   # 昨收
        self.assertTrue(out["points"])
        first = out["points"][0]
        self.assertEqual(first["t"], "09:30")
        self.assertGreater(first["price"], 0)
        self.assertGreater(first["cum_amount"], 0)  # 累计额（元）
        # 累计额单调不减
        cums = [p["cum_amount"] for p in out["points"]]
        self.assertTrue(all(a <= b for a, b in zip(cums, cums[1:])))

    def test_parse_empty(self):
        out = _parse_minute_payload({"data": {}}, "sh000001")
        self.assertEqual(out["points"], [])
        self.assertIsNone(out["preClose"])


class TestMergeMin5(unittest.TestCase):
    """新浪 5 分钟行多市场对齐相加 → 按日分组的桶序列。"""

    def test_merge(self):
        rows_sh = [{"day": "2026-11-27 09:35:00", "amount": "100", "volume": "1"},
                   {"day": "2026-11-27 09:40:00", "amount": "200", "volume": "2"}]
        rows_sz = [{"day": "2026-11-27 09:35:00", "amount": "50", "volume": "3"}]
        merged = _merge_min5_buckets([rows_sh, rows_sz])
        self.assertEqual(list(merged.keys()), ["2026-11-27"])
        buckets = merged["2026-11-27"]
        self.assertEqual([b["t"] for b in buckets], ["09:35", "09:40"])
        self.assertAlmostEqual(buckets[0]["amount"], 150.0)
        self.assertAlmostEqual(buckets[1]["amount"], 200.0)  # 深市缺桶 → 0 加
        self.assertAlmostEqual(buckets[0]["volume"], 4.0)


class TestAggregateBreadth(unittest.TestCase):
    """宽度聚合：涨跌停阈值按代码前缀（300/688 → 19.8），停牌不进分母。"""

    def test_thresholds_and_suspended(self):
        quotes = {
            "600000": {"price": 10.0, "chg": 5.0, "amount": 1e8},   # 普通涨
            "300001": {"price": 20.0, "chg": 19.9, "amount": 2e8},  # 创业板 19.8 涨停
            "688001": {"price": 30.0, "chg": 19.7, "amount": 5e7},  # 科创 19.7 未到 19.8
            "000003": {"price": 5.0, "chg": -9.8, "amount": 3e8},   # 主板跌停
            "000002": {"price": 0, "chg": None, "amount": 0},       # 停牌不进分母
        }
        b = aggregate_breadth(quotes)
        self.assertEqual(b["total"], 4)
        self.assertEqual(b["up"], 3)
        self.assertEqual(b["down"], 1)
        self.assertEqual(b["flat"], 0)
        self.assertEqual(b["limit_up"], 1)   # 只有 300001
        self.assertEqual(b["limit_down"], 1)
        self.assertAlmostEqual(b["up_ratio"], 0.75)
        self.assertAlmostEqual(b["amount"], 1e8 + 2e8 + 5e7 + 3e8)
        self.assertAlmostEqual(b["median"], (5.0 + 19.7) / 2)  # 偶数个取中间对均值

    def test_empty(self):
        b = aggregate_breadth({})
        self.assertEqual(b["total"], 0)
        self.assertIsNone(b["median"])
        self.assertIsNone(b["up_ratio"])


class TestEstimateTurnover(unittest.TestCase):
    """两市成交额预估（新浪 5 分钟口径）：同期比例 + 数据不足返回 None。"""

    @staticmethod
    def _merged(days, per_day, base=1e9):
        """构造沪+深合并桶：days 个交易日，每天 per_day 桶，每桶 base 元。"""
        return {d: [{"t": f"{9 + i // 12:02d}:{(i % 12) * 5:02d}",
                     "amount": base, "volume": 1.0} for i in range(per_day)]
                for d in days}

    def test_insufficient_days(self):
        self.assertIsNone(estimate_turnover(self._merged(["2026-11-27"], 3)))

    def test_prev_same_basis(self):
        days = ["2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27"]
        t = estimate_turnover(self._merged(days, 10))
        self.assertIsNotNone(t)
        self.assertEqual(t["date"], "2026-11-27")
        self.assertEqual(t["est_basis"], "prev_same")
        # 每桶恒定 base → 同期==全天 → est == 当前累计
        self.assertAlmostEqual(t["est_full"], t["today_cum"], delta=1.0)
        self.assertAlmostEqual(t["yday_same"], t["today_cum"], delta=1.0)
        self.assertEqual(len(t["series"]), 10)
        self.assertTrue(t["series"][0]["t"].startswith("09:"))

    def test_partial_day_ratio(self):
        """当日只收了半程桶 → est ≈ 2× 当前累计。"""
        days = ["2026-11-26", "2026-11-27"]
        m = self._merged(days, 10)
        m["2026-11-27"] = m["2026-11-27"][:5]   # 当日只到半程
        t = estimate_turnover(m)
        self.assertEqual(t["est_basis"], "prev_same")
        self.assertAlmostEqual(t["est_full"], t["today_cum"] * 2, delta=1.0)


class TestZtSummary(unittest.TestCase):
    """梯队摘要：连板高度/炸板率/天梯排序/池截断。"""

    def test_summary(self):
        zt = {
            "date": "20261127",
            "pool": [
                {"code": "1", "name": "A", "lbc": 6, "fund": 1e8},
                {"code": "2", "name": "B", "lbc": 2, "fund": 5e8},
                {"code": "3", "name": "C", "lbc": 2, "fund": 6e8},
                {"code": "4", "name": "D", "lbc": 1, "fund": 2e8},
            ],
            "broken_pool": [{"code": "9", "name": "X"}],
        }
        s = zt_summary(zt)
        self.assertEqual(s["limit_up"], 4)
        self.assertEqual(s["broken"], 1)
        self.assertAlmostEqual(s["broken_rate"], 0.2)
        self.assertEqual(s["max_lbc"], 6)
        ladder = s["ladder"]
        self.assertEqual([x["lbc"] for x in ladder], [6, 2, 1])  # 高板在前
        rung2 = next(x for x in ladder if x["lbc"] == 2)
        self.assertEqual(rung2["names"][0], "C")  # 同板位按封单额排序
        self.assertEqual(s["pool"][0]["code"], "1")  # 池按连板数 desc

    def test_empty(self):
        s = zt_summary({"pool": [], "broken_pool": []})
        self.assertEqual(s["limit_up"], 0)
        self.assertIsNone(s["max_lbc"])
        self.assertIsNone(s["broken_rate"])


class TestSession(unittest.TestCase):
    """交易时段判定。"""

    def _dt(self, h, m):
        return datetime(2026, 11, 27, h, m)  # 周五

    def test_boundaries(self):
        self.assertEqual(_session_of(self._dt(9, 10), True), "closed")
        self.assertEqual(_session_of(self._dt(9, 20), True), "pre")
        self.assertEqual(_session_of(self._dt(10, 0), True), "open")
        self.assertEqual(_session_of(self._dt(12, 0), True), "lunch")
        self.assertEqual(_session_of(self._dt(14, 0), True), "open")
        self.assertEqual(_session_of(self._dt(15, 1), True), "post")
        self.assertEqual(_session_of(self._dt(10, 0), False), "closed")


class TestThreeComponentVerdict(unittest.TestCase):
    """slot 轨迹的三成分口径：index_count=0 时 VWAP 项缺省 10 分（与日频序列可比）。"""

    def test_three_component_score(self):
        from app.market_sentiment import _verdict
        # 全平局场景：up_ratio=0.5, median=0, 无涨跌停 → 40×0.5 + 20×0.5 + 20×0.5 + 10 = 50
        level, score, _ = _verdict(0.5, 0.0, 0, 0, 0, 0)
        self.assertAlmostEqual(score, 50.0)
        # 与日频口径同一函数，缺失 VWAP 固定贡献 10 分
        _, score2, _ = _verdict(0.5, 0.0, 0, 0, 0, 0)
        self.assertEqual(score, score2)


if __name__ == "__main__":
    unittest.main()
