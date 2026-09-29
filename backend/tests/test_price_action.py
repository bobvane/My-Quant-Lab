"""Price action feature tests, including the critical no-lookahead checks."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.features.engine import build_features, feature_input_hash
from app.features.price_action import (
    add_price_action_features,
    body_ratio,
    breakout,
    close_position,
    inside_bar,
    lower_wick_ratio,
    outside_bar,
    upper_wick_ratio,
)


def _frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(rows), freq="D", tz="UTC")
    records = [
        {"open": o, "high": h, "low": low, "close": c, "volume": 1.0} for o, h, low, c in rows
    ]
    return pd.DataFrame(records, index=index)


def test_body_ratio_full_range_candle() -> None:
    frame = _frame([(10.0, 12.0, 8.0, 11.0)])
    assert body_ratio(frame).iloc[0] == pytest.approx(1.0 / 4.0)


def test_wick_ratios_sum_to_one_minus_body() -> None:
    frame = _frame([(10.0, 12.0, 8.0, 11.0)])
    body = body_ratio(frame).iloc[0]
    upper = upper_wick_ratio(frame).iloc[0]
    lower = lower_wick_ratio(frame).iloc[0]
    assert body + upper + lower == pytest.approx(1.0)


def test_close_position_boundaries() -> None:
    at_high = _frame([(10.0, 12.0, 8.0, 12.0)])
    at_low = _frame([(10.0, 12.0, 8.0, 8.0)])
    assert close_position(at_high).iloc[0] == pytest.approx(1.0)
    assert close_position(at_low).iloc[0] == pytest.approx(0.0)


def test_zero_range_candle_is_handled() -> None:
    frame = _frame([(10.0, 10.0, 10.0, 10.0)])
    assert body_ratio(frame).iloc[0] == 0.0
    assert close_position(frame).iloc[0] == pytest.approx(0.5)


def test_inside_and_outside_bar() -> None:
    frame = _frame([(10, 12, 8, 11), (10, 11, 9, 10.5), (9, 14, 7, 13)])
    assert bool(inside_bar(frame).iloc[1]) is True
    assert bool(outside_bar(frame).iloc[2]) is True
    assert bool(inside_bar(frame).iloc[0]) is False


def test_breakout_reference_never_includes_current_bar() -> None:
    """A new high must not be treated as its own breakout level."""

    frame = _frame([(10, 10, 10, 10)] * 5 + [(10, 50, 10, 50)])
    bo = breakout(frame, lookback=5)
    # the huge candle is a breakout, but the reference level must be 10 (old bars)
    assert bool(bo["breakout_up"].iloc[-1]) is True
    assert bo["prior_high"].iloc[-1] == pytest.approx(10.0)


def test_build_features_lookahead_regression(sample_bars: pd.DataFrame) -> None:
    """Truncating the series must not change previously computed values."""

    full = build_features(sample_bars).frame
    truncated = build_features(sample_bars.iloc[:-10]).frame
    columns = ["ema20", "ema50", "atr14", "rsi14", "macd", "bb_upper", "body_ratio"]
    left = full[columns].iloc[:-10]
    right = truncated[columns]
    pd.testing.assert_frame_equal(left, right, check_exact=False, rtol=1e-9)


def test_build_features_requires_ohlcv() -> None:
    with pytest.raises(ValueError, match="missing columns"):
        build_features(pd.DataFrame({"close": [1.0, 2.0]}))


def test_feature_version_recorded(sample_bars: pd.DataFrame) -> None:
    feature_frame = build_features(sample_bars)
    assert "ind" in feature_frame.feature_version and "pa" in feature_frame.feature_version
    assert feature_frame.warmup_bars >= 26


def test_feature_input_hash_is_stable(sample_bars: pd.DataFrame) -> None:
    assert feature_input_hash(sample_bars) == feature_input_hash(sample_bars)
    assert feature_input_hash(sample_bars) != feature_input_hash(sample_bars.iloc[:-1])


def test_add_price_action_features_keeps_index(sample_bars: pd.DataFrame) -> None:
    out = add_price_action_features(sample_bars)
    assert out.index.equals(sample_bars.index)
    for column in ("body_ratio", "breakout", "close_position", "overlap"):
        assert column in out.columns
    assert not np.isnan(out["body_ratio"]).any()
