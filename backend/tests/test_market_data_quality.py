"""Bar quality classification (docs/11, docs/15 Phase 1)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.data.market_data_repo import assess_bars_quality


def _bars(dates, close: float = 100.0) -> pd.DataFrame:
    index = pd.DatetimeIndex(dates)
    n = len(index)
    return pd.DataFrame(
        {
            "open": np.full(n, close),
            "high": np.full(n, close),
            "low": np.full(n, close),
            "close": np.full(n, close),
            "volume": np.full(n, 1000.0),
        },
        index=index,
    )


def test_regular_daily_bars_are_valid() -> None:
    dates = pd.date_range("2024-01-01", periods=60, freq="D", tz="UTC")
    status, details = assess_bars_quality(_bars(dates))
    assert status == "valid"
    assert details["gaps"] == 0 and details["invalid_bars"] == 0


def test_large_gap_marks_partial() -> None:
    first = pd.date_range("2024-01-01", periods=40, freq="D", tz="UTC")
    second = pd.date_range("2024-03-01", periods=40, freq="D", tz="UTC")
    frame = _bars(first.append(second))
    status, details = assess_bars_quality(frame)
    assert status == "partial"
    assert details["gaps"] == 1


def test_high_below_low_is_invalid() -> None:
    dates = pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC")
    frame = _bars(dates)
    frame.iloc[3, frame.columns.get_loc("high")] = 1.0
    status, details = assess_bars_quality(frame)
    assert status == "invalid"
    assert details["invalid_bars"] == 1


def test_non_positive_price_is_invalid() -> None:
    dates = pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC")
    frame = _bars(dates)
    frame.iloc[2, frame.columns.get_loc("close")] = 0.0
    status, _details = assess_bars_quality(frame)
    assert status == "invalid"


def test_empty_frame_is_unknown() -> None:
    status, details = assess_bars_quality(pd.DataFrame(columns=["open", "high", "low", "close"]))
    assert status == "unknown"
    assert details["bars"] == 0


def test_regular_weekly_bars_are_not_called_partial() -> None:
    """周线的两根 K 线本来就隔 7 天：按日线的 5 天标准会把它误判成有洞（ADR-215）。"""

    dates = pd.date_range("2024-01-01", periods=8, freq="7D", tz="UTC")
    status, details = assess_bars_quality(_bars(dates), timeframe="1w")

    assert status == "valid"
    assert details["gaps"] == 0
    assert details["gap_threshold_days"] == 10
    # 同一份数据按日线口径仍然算有洞 —— 门槛是跟着周期走的，不是把标准降下来。
    assert assess_bars_quality(_bars(dates), timeframe="1d")[0] == "partial"


def test_a_missed_week_is_still_a_gap() -> None:
    first = pd.date_range("2024-01-01", periods=6, freq="7D", tz="UTC")
    second = pd.date_range("2024-03-01", periods=6, freq="7D", tz="UTC")
    status, details = assess_bars_quality(_bars(first.append(second)), timeframe="1w")

    assert status == "partial"
    assert details["gaps"] == 1


def test_an_explicit_threshold_still_wins_over_the_timeframe() -> None:
    dates = pd.date_range("2024-01-01", periods=3, freq="7D", tz="UTC")

    status, details = assess_bars_quality(_bars(dates), timeframe="1w", gap_threshold_days=1)

    assert status == "partial"
    assert details["gap_threshold_days"] == 1
