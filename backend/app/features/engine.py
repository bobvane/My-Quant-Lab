"""Feature engine: build a deterministic feature frame from OHLCV bars."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pandas as pd

from app.features.indicators import INDICATOR_VERSION, atr, bollinger_bands, ema, macd, rsi, sma
from app.features.price_action import PA_FEATURE_VERSION, add_price_action_features

if TYPE_CHECKING:  # avoids a features -> strategies runtime import
    from app.strategies.dsl import StrategySpec

__all__ = [
    "FEATURE_VERSION",
    "FeatureFrame",
    "build_features",
    "feature_input_hash",
]

FEATURE_VERSION = f"ind{INDICATOR_VERSION}+pa{PA_FEATURE_VERSION}"

OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class FeatureFrame:
    """Features plus the metadata required to prove no-lookahead behaviour."""

    frame: pd.DataFrame
    feature_version: str
    warmup_bars: int

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.frame)

    @property
    def columns(self) -> list[str]:
        return list(self.frame.columns)

    def ready(self) -> pd.DataFrame:
        """Rows where every indicator has left its warm-up period."""

        return self.frame.dropna(subset=["ema20", "ema50", "atr14", "rsi14"])


def build_features(
    bars: pd.DataFrame,
    *,
    spec: StrategySpec | None = None,
    ema_periods: tuple[int, ...] = (20, 50),
    sma_periods: tuple[int, ...] = (20,),
    atr_period: int = 14,
    rsi_period: int = 14,
    macd_params: tuple[int, int, int] = (12, 26, 9),
    bollinger_period: int = 20,
    breakout_lookback: int = 20,
) -> FeatureFrame:
    """Compute the full V1 feature set.

    The returned frame keeps the same index (UTC timestamps) as ``bars`` and one
    row per input bar. Warm-up rows contain ``NaN`` and must be excluded from
    trading decisions.

    When a ``spec`` is supplied, every indicator it declares is materialised as
    a column named by the indicator ``id`` (period may come from ``period_ref``
    into ``spec.parameters``). This is what makes the DSL's declarative
    ``indicators`` block actually take effect.
    """

    missing = [c for c in OHLCV_COLUMNS if c not in bars.columns]
    if missing:
        raise ValueError(f"bars frame is missing columns: {missing}")

    frame = bars.copy()
    if not isinstance(frame.index, pd.DatetimeIndex):
        frame.index = pd.DatetimeIndex(frame["timestamp"])
    frame = frame.sort_index()

    for period in ema_periods:
        frame[f"ema{period}"] = ema(frame["close"], period)
    for period in sma_periods:
        frame[f"sma{period}"] = sma(frame["close"], period)

    if "volume" in frame.columns:
        frame["volume_sma_20"] = sma(frame["volume"], 20)

    frame[f"atr{atr_period}"] = atr(frame["high"], frame["low"], frame["close"], atr_period)
    frame[f"rsi{rsi_period}"] = rsi(frame["close"], rsi_period)

    macd_df = macd(frame["close"], *macd_params)
    frame[["macd", "macd_signal", "macd_hist"]] = macd_df

    bb = bollinger_bands(frame["close"], bollinger_period)
    frame[["bb_middle", "bb_upper", "bb_lower"]] = bb

    frame = add_price_action_features(
        frame,
        atr_period=atr_period,
        ema_period=ema_periods[0] if ema_periods else 20,
        breakout_lookback=breakout_lookback,
    )

    warmup = max(
        max(ema_periods, default=0),
        max(sma_periods, default=0),
        atr_period,
        rsi_period,
        max(macd_params),
        bollinger_period,
        breakout_lookback,
    )

    if spec is not None:
        for indicator in spec.indicators:
            warmup = max(warmup, _materialize_indicator(frame, indicator, spec.parameters))

    return FeatureFrame(frame=frame, feature_version=FEATURE_VERSION, warmup_bars=warmup)


def _indicator_period(indicator: Any, parameters: dict[str, Any]) -> int:
    period = indicator.period
    ref = getattr(indicator, "period_ref", None)
    if ref:
        if ref not in parameters:
            raise ValueError(f"indicator '{indicator.id}' references unknown parameter '{ref}'")
        period = parameters[ref]
    if period is None:
        raise ValueError(f"indicator '{indicator.id}' needs a 'period' or 'period_ref'")
    period = int(period)
    if period < 1:
        raise ValueError(f"indicator '{indicator.id}' period must be >= 1")
    return period


def _materialize_indicator(frame: pd.DataFrame, indicator: Any, parameters: dict[str, Any]) -> int:
    """Write one declared indicator into ``frame``; return its warm-up period."""

    kind = str(indicator.type).upper()
    column = indicator.id
    source = indicator.input or "close"
    params = dict(indicator.params or {})
    if source not in frame.columns:
        raise ValueError(f"indicator '{column}' input '{source}' is not a known column")

    if kind == "EMA":
        period = _indicator_period(indicator, parameters)
        frame[column] = ema(frame[source], period)
        return period
    if kind == "SMA":
        period = _indicator_period(indicator, parameters)
        frame[column] = sma(frame[source], period)
        return period
    if kind == "RSI":
        period = _indicator_period(indicator, parameters)
        frame[column] = rsi(frame[source], period)
        return period
    if kind == "ATR":
        period = _indicator_period(indicator, parameters)
        frame[column] = atr(frame["high"], frame["low"], frame["close"], period)
        return period
    if kind == "MACD":
        fast = int(params.get("fast", indicator.period or 12))
        slow = int(params.get("slow", 26))
        signal = int(params.get("signal", 9))
        bands = macd(frame[source], fast, slow, signal)
        frame[column] = bands["macd"]
        frame[f"{column}_signal"] = bands["macd_signal"]
        frame[f"{column}_hist"] = bands["macd_hist"]
        return max(fast, slow, signal)
    if kind in {"BOLLINGER", "BB", "BOLLINGER_BANDS"}:
        period = (
            int(parameters.get(indicator.period_ref, indicator.period) or params.get("period", 20))
            if (indicator.period or getattr(indicator, "period_ref", None))
            else int(params.get("period", 20))
        )
        bands = bollinger_bands(frame[source], period, float(params.get("num_std", 2.0)))
        frame[column] = bands["bb_middle"]
        frame[f"{column}_upper"] = bands["bb_upper"]
        frame[f"{column}_lower"] = bands["bb_lower"]
        return period

    raise ValueError(f"unsupported indicator type '{indicator.type}' (id '{column}')")


def feature_input_hash(bars: pd.DataFrame, columns: tuple[str, ...] = OHLCV_COLUMNS) -> str:
    """Stable hash of the raw input bars, stored as evidence with every run."""

    hasher = hashlib.sha256()
    payload = bars[list(columns)].to_csv(float_format="%.10g")
    hasher.update(payload.encode("utf-8"))
    return hasher.hexdigest()
