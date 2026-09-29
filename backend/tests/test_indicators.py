"""Deterministic indicator tests (golden values + edge cases)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.features.indicators import (
    atr,
    bollinger_bands,
    ema,
    macd,
    rolling_max,
    rsi,
    sma,
    true_range,
)


def test_sma_matches_manual_mean() -> None:
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = sma(series, 3)
    assert np.isnan(result.iloc[0]) and np.isnan(result.iloc[1])
    assert result.iloc[2] == pytest.approx(2.0)
    assert result.iloc[4] == pytest.approx(4.0)


def test_ema_is_deterministic_and_seeded_by_sma() -> None:
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    first = ema(series, 3)
    second = ema(series, 3)
    pd.testing.assert_series_equal(first, second)
    # first defined value equals the SMA of the first window
    assert first.iloc[2] == pytest.approx(2.0)


def test_rsi_all_gains_is_100() -> None:
    series = pd.Series(np.arange(1, 40, dtype=float))
    result = rsi(series, 14).dropna()
    assert result.iloc[-1] == pytest.approx(100.0)


def test_rsi_flat_series_is_neutral() -> None:
    series = pd.Series([50.0] * 60)
    result = rsi(series, 14).dropna()
    assert result.iloc[-1] == pytest.approx(50.0)


def test_true_range_uses_previous_close() -> None:
    high = pd.Series([10.0, 12.0])
    low = pd.Series([8.0, 9.0])
    close = pd.Series([9.0, 11.0])
    tr = true_range(high, low, close)
    assert tr.iloc[0] == pytest.approx(2.0)
    # bar 1: max(3.0, |12-9|, |9-9|) = 3.0
    assert tr.iloc[1] == pytest.approx(3.0)


def test_atr_is_positive_and_defined_after_warmup() -> None:
    rng = np.random.default_rng(7)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 100)))
    high = close + 1.0
    low = close - 1.0
    result = atr(high, low, close, 14)
    assert result.isna().sum() == 13
    assert (result.dropna() > 0).all()


def test_macd_columns_present() -> None:
    rng = np.random.default_rng(3)
    series = pd.Series(100 + np.cumsum(rng.normal(0, 1, 200)))
    frame = macd(series)
    assert set(frame.columns) == {"macd", "macd_signal", "macd_hist"}
    assert frame["macd_hist"].notna().sum() > 0


def test_bollinger_bands_ordering() -> None:
    rng = np.random.default_rng(11)
    series = pd.Series(100 + np.cumsum(rng.normal(0, 1, 120)))
    bands = bollinger_bands(series, 20, 2.0).dropna()
    assert (bands["bb_upper"] >= bands["bb_middle"]).all()
    assert (bands["bb_middle"] >= bands["bb_lower"]).all()


def test_rolling_max_excludes_current_bar() -> None:
    series = pd.Series([1.0, 5.0, 2.0, 3.0])
    result = rolling_max(series, 2)
    # bar 3 uses bars 1..2 -> max(5, 2) = 5, not the current 3
    assert result.iloc[2] == pytest.approx(5.0)
    assert result.iloc[3] == pytest.approx(5.0)


def test_indicators_reject_invalid_period() -> None:
    series = pd.Series([1.0, 2.0])
    with pytest.raises(ValueError):
        sma(series, 0)
    with pytest.raises(ValueError):
        bollinger_bands(series, 1)
