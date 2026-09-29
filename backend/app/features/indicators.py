"""Deterministic technical indicators.

Every function here is a pure function of its inputs: same input series always
produces the same output, with no lookahead (``iloc[i]`` never depends on
``iloc[i+1:]``). Warm-up periods are ``NaN`` and callers must drop them before
trading.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "atr",
    "bollinger_bands",
    "ema",
    "macd",
    "rolling_max",
    "rolling_min",
    "rsi",
    "sma",
    "true_range",
]

INDICATOR_VERSION = "1.0.0"


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""

    if period < 1:
        raise ValueError("period must be >= 1")
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average, explicitly seeded with the SMA of the first window.

    The explicit loop (instead of ``ewm``) guarantees the documented seeding
    behaviour and identical results across pandas versions, which matters because
    backtest results must be reproducible.
    """

    if period < 1:
        raise ValueError("period must be >= 1")

    values = series.to_numpy(dtype=float)
    out = np.full(len(values), np.nan, dtype=float)
    defined = np.flatnonzero(~np.isnan(values))
    if len(defined) < period:
        return pd.Series(out, index=series.index, name=series.name)

    start = int(defined[period - 1])
    out[start] = float(np.mean(values[defined[:period]]))
    alpha = 2.0 / (period + 1.0)
    for i in range(start + 1, len(values)):
        if np.isnan(values[i]):
            out[i] = out[i - 1]
        else:
            out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return pd.Series(out, index=series.index, name=series.name)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Wilder's true range."""

    prev_close = close.shift(1)
    ranges = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1)
    return ranges.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average true range using Wilder smoothing."""

    if period < 1:
        raise ValueError("period must be >= 1")
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's relative strength index (0-100)."""

    if period < 1:
        raise ValueError("period must be >= 1")
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 -> RSI 100; both zero -> 50 (flat market)
    out = out.where(avg_loss.ne(0.0) | avg_gain.isna(), 100.0)
    out = out.where(~(avg_loss.eq(0.0) & avg_gain.eq(0.0)), 50.0)
    return out


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD line, signal line and histogram."""

    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame(
        {
            "macd": macd_line,
            "macd_signal": signal_line,
            "macd_hist": macd_line - signal_line,
        }
    )


def bollinger_bands(series: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """Bollinger bands (middle / upper / lower)."""

    if period < 2:
        raise ValueError("period must be >= 2")
    middle = series.rolling(window=period, min_periods=period).mean()
    std = series.rolling(window=period, min_periods=period).std(ddof=0)
    return pd.DataFrame(
        {
            "bb_middle": middle,
            "bb_upper": middle + num_std * std,
            "bb_lower": middle - num_std * std,
        }
    )


def rolling_max(series: pd.Series, period: int) -> pd.Series:
    """Highest value of the previous ``period`` bars (current bar excluded)."""

    return series.shift(1).rolling(window=period, min_periods=period).max()


def rolling_min(series: pd.Series, period: int) -> pd.Series:
    """Lowest value of the previous ``period`` bars (current bar excluded)."""

    return series.shift(1).rolling(window=period, min_periods=period).min()
