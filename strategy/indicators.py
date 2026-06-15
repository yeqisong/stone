"""技术指标计算工具。不依赖 TA-Lib 也能运行（纯 numpy/pandas 实现）。"""
import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """简单移动平均。"""
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """指数移动平均。"""
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """相对强弱指标 (RSI)。"""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_val = 100 - (100 / (1 + rs))
    return rsi_val


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD 指标。返回 (DIF, DEA, MACD柱)。"""
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    dif = ema_fast - ema_slow
    dea = ema(dif, signal)
    macd_bar = 2 * (dif - dea)
    return dif, dea, macd_bar


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """平均真实波幅 (ATR)。"""
    high, low, close = df['high'], df['low'], df['close']
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def bollinger_bands(close: pd.Series, period: int = 20, std_mult: float = 2.0):
    """
    布林带。返回 (middle, upper, lower, bandwidth)。
    bandwidth = (upper - lower) / middle
    """
    middle = sma(close, period)
    std = close.rolling(window=period, min_periods=period).std()
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    bandwidth = (upper - lower) / middle.replace(0, np.nan)
    return middle, upper, lower, bandwidth


def aggregate_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """
    将日线聚合为周线。
    输入 df 需包含: trade_date, open, high, low, close, volume
    trade_date 应为 datetime 类型。
    """
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df['week'] = df['trade_date'].dt.isocalendar().year.astype(str) + '-W' + \
                 df['trade_date'].dt.isocalendar().week.astype(str).str.zfill(2)
    df = df.sort_values('trade_date')

    weekly = df.groupby('week').agg(
        trade_date=('trade_date', 'last'),
        open=('open', 'first'),
        high=('high', 'max'),
        low=('low', 'min'),
        close=('close', 'last'),
        volume=('volume', 'sum'),
    ).reset_index(drop=True)

    weekly['trade_date'] = pd.to_datetime(weekly['trade_date'])
    return weekly.sort_values('trade_date')


def aggregate_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """将日线聚合为月线。"""
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df['month'] = df['trade_date'].dt.to_period('M')

    monthly = df.groupby('month').agg(
        trade_date=('trade_date', 'last'),
        open=('open', 'first'),
        high=('high', 'max'),
        low=('low', 'min'),
        close=('close', 'last'),
        volume=('volume', 'sum'),
    ).reset_index(drop=True)

    monthly['trade_date'] = pd.to_datetime(monthly['trade_date'])
    return monthly.sort_values('trade_date')


def obv(df: pd.DataFrame) -> pd.Series:
    """能量潮 (On-Balance Volume)。

    若当日收盘 > 前日收盘，OBV = 前日OBV + 当日成交量
    若当日收盘 < 前日收盘，OBV = 前日OBV - 当日成交量
    若相等，OBV 不变。
    """
    close = df['close'].values
    volume = df['volume'].values
    obv_vals = np.zeros(len(close))
    for i in range(1, len(close)):
        if close[i] > close[i - 1]:
            obv_vals[i] = obv_vals[i - 1] + volume[i]
        elif close[i] < close[i - 1]:
            obv_vals[i] = obv_vals[i - 1] - volume[i]
        else:
            obv_vals[i] = obv_vals[i - 1]
    return pd.Series(obv_vals, index=df.index)
