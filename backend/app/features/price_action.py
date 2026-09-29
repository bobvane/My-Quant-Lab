"""Price Action (PA) features.

All features are deterministic and lookahead-safe. Reference levels
(``prior_high``/``prior_low``) always use bars **before** the current one.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.features.indicators import atr, ema

__all__ = [
    "add_price_action_features",
    "body_ratio",
    "breakout",
    "breakout_failure",
    "breakout_follow_through",
    "close_position",
    "ema_relation",
    "inside_bar",
    "lower_wick_ratio",
    "overlap",
    "outside_bar",
    "range_atr_ratio",
    "upper_wick_ratio",
    "distance_to_ema",
]

PA_FEATURE_VERSION = "1.0.0"

_EPS = 1e-12


def _body(frame: pd.DataFrame) -> pd.Series:
    return (frame["close"] - frame["open"]).abs()


def _range(frame: pd.DataFrame) -> pd.Series:
    return (frame["high"] - frame["low"]).replace(0.0, np.nan)


def body_ratio(frame: pd.DataFrame) -> pd.Series:
    """Absolute candle body divided by the full range."""

    return (_body(frame) / _range(frame)).fillna(0.0)


def upper_wick_ratio(frame: pd.DataFrame) -> pd.Series:
    """Upper shadow divided by the full range."""

    upper = frame["high"] - frame[["open", "close"]].max(axis=1)
    return (upper / _range(frame)).fillna(0.0)


def lower_wick_ratio(frame: pd.DataFrame) -> pd.Series:
    """Lower shadow divided by the full range."""

    lower = frame[["open", "close"]].min(axis=1) - frame["low"]
    return (lower / _range(frame)).fillna(0.0)


def close_position(frame: pd.DataFrame) -> pd.Series:
    """Where the close sits inside the range: 0 = low, 1 = high."""

    rng = _range(frame)
    return ((frame["close"] - frame["low"]) / rng).fillna(0.5)


def range_atr_ratio(frame: pd.DataFrame, atr_period: int = 14) -> pd.Series:
    """Current range relative to ATR: high values = expansion."""

    atr_series = atr(frame["high"], frame["low"], frame["close"], atr_period)
    return (frame["high"] - frame["low"]) / atr_series.replace(0.0, np.nan)


def ema_relation(frame: pd.DataFrame, ema_period: int = 20) -> pd.DataFrame:
    """Position of price relative to the EMA plus the EMA slope direction."""

    ema_line = ema(frame["close"], ema_period)
    slope = ema_line.diff()
    return pd.DataFrame(
        {
            f"ema{ema_period}": ema_line,
            "ema_relation": np.sign(frame["close"] - ema_line),
            "ema_slope": np.sign(slope),
            "distance_to_ema": (frame["close"] - ema_line) / ema_line.replace(0.0, np.nan),
        },
        index=frame.index,
    )


def distance_to_ema(frame: pd.DataFrame, ema_period: int = 20) -> pd.Series:
    """Normalised distance between close and EMA."""

    ema_line = ema(frame["close"], ema_period)
    return (frame["close"] - ema_line) / ema_line.replace(0.0, np.nan)


def overlap(frame: pd.DataFrame) -> pd.Series:
    """True when the current candle overlaps the previous candle body/range."""

    prev_high = frame["high"].shift(1)
    prev_low = frame["low"].shift(1)
    return (frame["low"] <= prev_high) & (frame["high"] >= prev_low)


def inside_bar(frame: pd.DataFrame) -> pd.Series:
    """Current bar is fully contained in the previous bar."""

    return (frame["high"] <= frame["high"].shift(1)) & (frame["low"] >= frame["low"].shift(1))


def outside_bar(frame: pd.DataFrame) -> pd.Series:
    """Current bar engulfs the previous bar."""

    return (frame["high"] > frame["high"].shift(1)) & (frame["low"] < frame["low"].shift(1))


def inside_bar_sequence(frame: pd.DataFrame) -> pd.Series:
    """Consecutive inside-bar count ending at the current bar."""

    flags = inside_bar(frame).fillna(False).to_numpy(dtype=bool)
    out = np.zeros(len(flags), dtype=int)
    run = 0
    for idx, flag in enumerate(flags):
        run = run + 1 if flag else 0
        out[idx] = run
    return pd.Series(out, index=frame.index, name="inside_bar_sequence")


def micro_double(frame: pd.DataFrame) -> pd.Series:
    """Two consecutive same-direction bars with a small combined range."""

    direction = np.sign(frame["close"] - frame["open"]).fillna(0.0)
    same_direction = (direction != 0) & (direction == direction.shift(1))
    combined = (_body(frame) + _body(frame.shift(1))) / _range(frame).replace(0.0, np.nan)
    return same_direction & (combined < 0.6)


def breakout(frame: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    """Breakout of the previous ``lookback`` bars.

    Returns up/down flags plus the reference levels. The current bar's own high
    is never used as the reference level.
    """

    prior_high = frame["high"].shift(1).rolling(window=lookback, min_periods=lookback).max()
    prior_low = frame["low"].shift(1).rolling(window=lookback, min_periods=lookback).min()
    return pd.DataFrame(
        {
            "prior_high": prior_high,
            "prior_low": prior_low,
            "breakout_up": frame["close"] > prior_high,
            "breakout_down": frame["close"] < prior_low,
        },
        index=frame.index,
    )


def breakout_follow_through(frame: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """Breakout confirmed by continuation in the next bar."""

    bo = breakout(frame, lookback)
    continuation_up = frame["close"] > frame["open"]
    continuation_down = frame["close"] < frame["open"]
    return (bo["breakout_up"].shift(1).fillna(False) & continuation_up) | (
        bo["breakout_down"].shift(1).fillna(False) & continuation_down
    )


def breakout_failure(frame: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """Breakout that closed back inside the previous range."""

    bo = breakout(frame, lookback)
    failed_up = bo["breakout_up"].fillna(False) & (frame["close"] < bo["prior_high"])
    failed_down = bo["breakout_down"].fillna(False) & (frame["close"] > bo["prior_low"])
    return failed_up | failed_down


def add_price_action_features(
    frame: pd.DataFrame,
    *,
    atr_period: int = 14,
    ema_period: int = 20,
    breakout_lookback: int = 20,
) -> pd.DataFrame:
    """Return a copy of ``frame`` enriched with all V1 PA features."""

    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")

    out = frame.copy()
    out["body_ratio"] = body_ratio(out)
    out["upper_wick_ratio"] = upper_wick_ratio(out)
    out["lower_wick_ratio"] = lower_wick_ratio(out)
    out["close_position"] = close_position(out)
    out["range_atr_ratio"] = range_atr_ratio(out, atr_period)
    out["atr"] = atr(out["high"], out["low"], out["close"], atr_period)
    out["overlap"] = overlap(out)
    out["inside_bar"] = inside_bar(out)
    out["outside_bar"] = outside_bar(out)
    out["inside_bar_sequence"] = inside_bar_sequence(out)
    out["micro_double"] = micro_double(out)
    out["distance_to_ema"] = distance_to_ema(out, ema_period)

    relation = ema_relation(out, ema_period)
    out[f"ema{ema_period}"] = relation[f"ema{ema_period}"]
    out["ema_relation"] = relation["ema_relation"]
    out["ema_slope"] = relation["ema_slope"]

    bo = breakout(out, breakout_lookback)
    out["prior_high"] = bo["prior_high"]
    out["prior_low"] = bo["prior_low"]
    out["breakout"] = bo["breakout_up"]
    out["breakout_down"] = bo["breakout_down"]
    out["breakout_follow_through"] = breakout_follow_through(out, breakout_lookback)
    out["breakout_failure"] = breakout_failure(out, breakout_lookback)
    return out
