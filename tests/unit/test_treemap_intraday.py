"""分时市值树图：快照字段映射与聚合的正确性。"""
from datetime import date

from app import treemap_intraday as ti


def _cache_rows():
    return [
        {"node_id": "银行", "parent": None, "name": "银行", "value": 0,
         "chg_pct": 0, "node_type": "l1", "detail": None},
        {"node_id": "银行-1", "parent": "银行", "name": "银行II", "value": 0,
         "chg_pct": 0, "node_type": "l2", "detail": None},
        {"node_id": "600000", "parent": "银行-1", "name": "浦发银行",
         "value": 100e8, "chg_pct": -5.0, "node_type": "stock", "detail": None},
    ]


def _snap(chg=2.0, high=11.1, low=10.7):
    return {"600000": {
        "stock_code": "600000", "price": 11.0, "chg_pct": chg,
        "mcap": 110e8, "amount": 1e8, "open": 10.8, "high": high, "low": low,
    }}


class TestBuild:
    def test_uses_snapshot_chg_pct_not_cache(self):
        built = ti._build(_cache_rows(), _snap(chg=2.0), {})
        stock = built["stocks"][0]
        assert stock["chg_pct"] == 2.0
        assert built["l1_out"][0]["chg_pct"] == 2.0

    def test_missing_snapshot_does_not_reuse_yesterday_change(self):
        built = ti._build(_cache_rows(), {}, {})
        stock = built["stocks"][0]
        assert stock["chg_pct"] == 0.0
        assert built["l1_out"][0]["chg_pct"] == 0.0
        # 市值仍可用缓存兜底
        assert stock["value"] == 100e8

    def test_snapshot_mcap_overrides_cache_value(self):
        built = ti._build(_cache_rows(), _snap(), {})
        assert built["stocks"][0]["value"] == 110e8


class TestSectorFlowFallback:
    def test_uses_snapshot_chg_pct_when_high_low_missing(self, monkeypatch):
        monkeypatch.setattr(ti, "_main_net_by_l1", lambda db, day, stock_l1: ({}, None))
        built = ti._build(_cache_rows(), _snap(high=None, low=None), {})
        flows = ti._sector_flows(None, _cache_rows(), _snap(high=None, low=None), {},
                                 built, date(2026, 11, 17))
        row = next(x for x in flows["day"] if x["id"] == "银行")
        # chg_pct=2.0 → clv=2/3，amount=1e8 → 压力约 +6667 万
        assert row["flow_day"] > 6e7
        assert row["chg_pct"] == 2.0
