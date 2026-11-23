"""特征复权口径回归测试：送转日 OHLCV 不应出现假跳变。"""
import pandas as pd

from scripts.feature_compute import _adjust_ohlcv


def _df():
    return pd.DataFrame({
        "stock_code": ["000001", "000001"],
        "trade_date": pd.to_datetime(["2026-01-05", "2026-01-06"]),
        # 2 拆 1：原始价减半、后复权价连续；原始量翻倍
        "open": [100.0, 50.0],
        "high": [102.0, 51.0],
        "low": [99.0, 49.0],
        "close": [100.0, 50.0],
        "close_hfq": [100.0, 100.0],
        "volume": [1000, 2000],
        "amount": [100000.0, 100000.0],
    })


def test_adjusted_ohlcv_continuous_on_split():
    out = _adjust_ohlcv(_df(), "stock", ["open", "high", "low", "close", "volume"])
    assert out["close"].tolist() == [100.0, 100.0]
    assert out["open"].tolist() == [100.0, 100.0]
    assert out["high"].tolist() == [102.0, 102.0]
    assert out["low"].tolist() == [99.0, 98.0]
    assert out["volume"].tolist() == [1000.0, 1000.0]


def test_missing_hfq_falls_back_to_raw():
    df = _df()
    df["close_hfq"] = [None, 0.0]
    out = _adjust_ohlcv(df, "stock", ["close", "open", "volume"])
    assert out["close"].tolist() == [100.0, 50.0]
    assert out["open"].tolist() == [100.0, 50.0]
    assert out["volume"].tolist() == [1000.0, 2000.0]


def test_index_not_adjusted():
    df = _df()
    out = _adjust_ohlcv(df, "index", ["close", "open", "volume"])
    assert out["close"].tolist() == [100.0, 50.0]
    assert out["volume"].tolist() == [1000, 2000]
