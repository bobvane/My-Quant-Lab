"""The feature catalogue: the features this engine actually computes.

Features are code — ``app/features/indicators.py`` and ``app/features/price_action.py``
compute the columns, and ``build_features`` decides which of them a frame gets. The
``features`` table that used to back ``GET /features`` was never written by anything,
so the endpoint answered "no features" for the whole life of the project while the
engine produced thirty of them (ADR-093).

This module therefore states the catalogue where it can be checked: ``FEATURE_CATALOGUE``
is compared against the engine's real output in both directions by
``backend/tests/test_feature_catalogue.py``. A feature that is computed but not listed,
or listed but not computed, turns that test red — the listing cannot quietly drift away
from the code it describes.

Not listed here: the extra indicator columns a DSL ``indicators`` block materialises
(``build_features(..., spec=...)`` names them by the strategy's own indicator ``id``),
because those belong to one strategy version rather than to the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.features.engine import FEATURE_VERSION

__all__ = [
    "FEATURE_CATALOGUE",
    "FeatureSpec",
    "catalogue_payload",
]


@dataclass(frozen=True)
class FeatureSpec:
    """One computed feature column.

    ``inputs`` names the OHLCV columns the feature reads and ``params`` the engine
    arguments that shape it. ``is_deterministic``/``lookahead_safe`` are properties of
    the implementation, asserted by the engine's own tests (a moving average that used
    the current bar would not be lookahead safe), not configuration.
    """

    name: str
    family: str
    description: str
    inputs: tuple[str, ...]
    params: dict[str, Any] = field(default_factory=dict)
    feature_version: str = FEATURE_VERSION
    is_deterministic: bool = True
    lookahead_safe: bool = True


_CLOSE = ("close",)
_OHLC = ("open", "high", "low", "close")


FEATURE_CATALOGUE: tuple[FeatureSpec, ...] = (
    # --- indicators (INDICATOR_VERSION) ------------------------------------- #
    FeatureSpec(
        name="ema20",
        family="indicator",
        description="Exponential moving average of close over 20 bars.",
        inputs=_CLOSE,
        params={"period": 20},
    ),
    FeatureSpec(
        name="ema50",
        family="indicator",
        description="Exponential moving average of close over 50 bars.",
        inputs=_CLOSE,
        params={"period": 50},
    ),
    FeatureSpec(
        name="sma20",
        family="indicator",
        description="Simple moving average of close over 20 bars.",
        inputs=_CLOSE,
        params={"period": 20},
    ),
    FeatureSpec(
        name="volume_sma_20",
        family="indicator",
        description="Simple moving average of volume over 20 bars.",
        inputs=("volume",),
        params={"period": 20},
    ),
    FeatureSpec(
        name="atr14",
        family="indicator",
        description="Average true range over 14 bars.",
        inputs=("high", "low", "close"),
        params={"period": 14},
    ),
    FeatureSpec(
        name="rsi14",
        family="indicator",
        description="Relative strength index over 14 bars.",
        inputs=_CLOSE,
        params={"period": 14},
    ),
    FeatureSpec(
        name="macd",
        family="indicator",
        description="MACD line: EMA(12) - EMA(26).",
        inputs=_CLOSE,
        params={"fast": 12, "slow": 26, "signal": 9},
    ),
    FeatureSpec(
        name="macd_signal",
        family="indicator",
        description="Signal line: EMA(9) of the MACD line.",
        inputs=_CLOSE,
        params={"fast": 12, "slow": 26, "signal": 9},
    ),
    FeatureSpec(
        name="macd_hist",
        family="indicator",
        description="MACD histogram: MACD line minus its signal line.",
        inputs=_CLOSE,
        params={"fast": 12, "slow": 26, "signal": 9},
    ),
    FeatureSpec(
        name="bb_middle",
        family="indicator",
        description="Bollinger band middle line (20-bar SMA of close).",
        inputs=_CLOSE,
        params={"period": 20},
    ),
    FeatureSpec(
        name="bb_upper",
        family="indicator",
        description="Bollinger band upper line (middle + 2 standard deviations).",
        inputs=_CLOSE,
        params={"period": 20, "deviations": 2},
    ),
    FeatureSpec(
        name="bb_lower",
        family="indicator",
        description="Bollinger band lower line (middle - 2 standard deviations).",
        inputs=_CLOSE,
        params={"period": 20, "deviations": 2},
    ),
    # --- price action (PA_FEATURE_VERSION) ---------------------------------- #
    FeatureSpec(
        name="atr",
        family="price_action",
        description="Average true range used to normalise the price-action features.",
        inputs=("high", "low", "close"),
        params={"period": 14},
    ),
    FeatureSpec(
        name="body_ratio",
        family="price_action",
        description="Absolute candle body divided by the bar range.",
        inputs=_OHLC,
        params={},
    ),
    FeatureSpec(
        name="upper_wick_ratio",
        family="price_action",
        description="Upper shadow divided by the bar range.",
        inputs=_OHLC,
        params={},
    ),
    FeatureSpec(
        name="lower_wick_ratio",
        family="price_action",
        description="Lower shadow divided by the bar range.",
        inputs=_OHLC,
        params={},
    ),
    FeatureSpec(
        name="close_position",
        family="price_action",
        description="Where the close sits inside the bar range: 0 = low, 1 = high.",
        inputs=_OHLC,
        params={},
    ),
    FeatureSpec(
        name="range_atr_ratio",
        family="price_action",
        description="Bar range relative to ATR: high values mean expansion.",
        inputs=("high", "low", "close"),
        params={"period": 14},
    ),
    FeatureSpec(
        name="overlap",
        family="price_action",
        description="The bar overlaps the previous bar's range.",
        inputs=("high", "low"),
        params={},
    ),
    FeatureSpec(
        name="inside_bar",
        family="price_action",
        description="The bar is fully contained in the previous bar.",
        inputs=("high", "low"),
        params={},
    ),
    FeatureSpec(
        name="outside_bar",
        family="price_action",
        description="The bar engulfs the previous bar.",
        inputs=("high", "low"),
        params={},
    ),
    FeatureSpec(
        name="inside_bar_sequence",
        family="price_action",
        description="Count of consecutive inside bars ending at this bar.",
        inputs=("high", "low"),
        params={},
    ),
    FeatureSpec(
        name="micro_double",
        family="price_action",
        description="Two consecutive same-direction bars with a small combined range.",
        inputs=_OHLC,
        params={"max_combined_range": 0.6},
    ),
    FeatureSpec(
        name="distance_to_ema",
        family="price_action",
        description="Close minus EMA, divided by the EMA.",
        inputs=_CLOSE,
        params={"period": 20},
    ),
    FeatureSpec(
        name="ema_relation",
        family="price_action",
        description="Sign of close minus EMA: -1, 0 or 1.",
        inputs=_CLOSE,
        params={"period": 20},
    ),
    FeatureSpec(
        name="ema_slope",
        family="price_action",
        description="Sign of the EMA's change since the previous bar: -1, 0 or 1.",
        inputs=_CLOSE,
        params={"period": 20},
    ),
    FeatureSpec(
        name="prior_high",
        family="price_action",
        description="Highest high of the previous 20 bars, current bar excluded.",
        inputs=("high",),
        params={"lookback": 20},
    ),
    FeatureSpec(
        name="prior_low",
        family="price_action",
        description="Lowest low of the previous 20 bars, current bar excluded.",
        inputs=("low",),
        params={"lookback": 20},
    ),
    FeatureSpec(
        name="breakout",
        family="price_action",
        description="Close above the previous 20-bar high.",
        inputs=("high", "close"),
        params={"lookback": 20},
    ),
    FeatureSpec(
        name="breakout_down",
        family="price_action",
        description="Close below the previous 20-bar low.",
        inputs=("low", "close"),
        params={"lookback": 20},
    ),
    FeatureSpec(
        name="breakout_follow_through",
        family="price_action",
        description="A breakout on the previous bar continued in its direction.",
        inputs=_OHLC,
        params={"lookback": 20},
    ),
    FeatureSpec(
        name="breakout_failure",
        family="price_action",
        description="A breakout on the previous bar closed back inside the range.",
        inputs=_OHLC,
        params={"lookback": 20},
    ),
)


def catalogue_payload() -> list[dict[str, Any]]:
    """The catalogue as the API serves it: sorted by name, no database ids."""

    return [
        {
            "name": spec.name,
            "feature_type": spec.family,
            "feature_version": spec.feature_version,
            "description": spec.description,
            "inputs": list(spec.inputs),
            "params": dict(spec.params),
            "is_deterministic": spec.is_deterministic,
            "lookahead_safe": spec.lookahead_safe,
        }
        for spec in sorted(FEATURE_CATALOGUE, key=lambda item: item.name)
    ]
