"""pytest 配置与共享 fixtures。"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


@pytest.fixture
def sample_daily_data():
    """生成 120 个交易日的模拟日线数据（上升趋势 + 波动）。"""
    dates = pd.date_range(end=datetime(2026, 6, 1), periods=120, freq='B')
    np.random.seed(42)

    # 基础上升趋势 + 随机波动
    base_price = 50.0
    trend = np.linspace(0, 30, 120)
    noise = np.random.randn(120) * 2
    close = base_price + trend + noise
    close = np.maximum(close, 10)  # 不低于 10

    # 生成 OHLC
    high = close + np.abs(np.random.randn(120) * 1.5)
    low = close - np.abs(np.random.randn(120) * 1.5)
    open_price = close - np.random.randn(120) * 0.5
    volume = np.random.randint(1000000, 10000000, 120)

    df = pd.DataFrame({
        'trade_date': dates,
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume,
        'stock_code': '000001',
        'stock_name': '测试股票',
    })
    return df


@pytest.fixture
def sample_downtrend_data():
    """生成下跌趋势数据。"""
    dates = pd.date_range(end=datetime(2026, 6, 1), periods=120, freq='B')
    np.random.seed(123)

    base_price = 80.0
    trend = np.linspace(0, -30, 120)
    noise = np.random.randn(120) * 2
    close = base_price + trend + noise
    close = np.maximum(close, 10)

    high = close + np.abs(np.random.randn(120) * 1.5)
    low = close - np.abs(np.random.randn(120) * 1.5)
    open_price = close - np.random.randn(120) * 0.5
    volume = np.random.randint(500000, 8000000, 120)

    df = pd.DataFrame({
        'trade_date': dates,
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume,
        'stock_code': '000002',
        'stock_name': '下跌测试股',
    })
    return df


@pytest.fixture
def short_data():
    """仅 10 个交易日的数据（新股不足场景）。"""
    dates = pd.date_range(end=datetime(2026, 6, 1), periods=10, freq='B')
    df = pd.DataFrame({
        'trade_date': dates,
        'open': np.random.randn(10) + 50,
        'high': np.random.randn(10) + 52,
        'low': np.random.randn(10) + 48,
        'close': np.random.randn(10) + 50,
        'volume': np.random.randint(1000000, 5000000, 10),
        'stock_code': '000003',
        'stock_name': '新股测试',
    })
    return df


@pytest.fixture
def divergence_data():
    """构造已知的底背离场景：价格创新低，成交量萎缩。"""
    dates = pd.date_range(end=datetime(2026, 6, 1), periods=60, freq='B')
    np.random.seed(99)

    # 价格先跌后反弹
    close = np.concatenate([
        np.linspace(60, 40, 40),   # 持续下跌
        np.linspace(40, 50, 20),   # 反弹
    ])

    # 成交量: 下跌过程中逐渐萎缩
    volume = np.concatenate([
        np.linspace(8000000, 2000000, 40),  # 缩量下跌
        np.linspace(2000000, 6000000, 20),  # 放量反弹
    ])

    high = close + np.abs(np.random.randn(60) * 1)
    low = close - np.abs(np.random.randn(60) * 1)
    open_price = close - np.random.randn(60) * 0.3

    df = pd.DataFrame({
        'trade_date': dates,
        'open': open_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume.astype(int),
        'stock_code': '000004',
        'stock_name': '背离测试',
    })
    return df
