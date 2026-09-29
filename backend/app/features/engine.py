"""Feature engine: build a deterministic feature frame from OHLCV bars."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pandas as pd

from app.features.indicators import INDICATOR_VERSION, atr, bollinger_bands, ema, macd, rsi, sma
from app.features.price_action import PA_FEATURE_VERSION, add_price_action_features

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
    return FeatureFrame(frame=frame, feature_version=FEATURE_VERSION, warmup_bars=warmup)


def feature_input_hash(bars: pd.DataFrame, columns: tuple[str, ...] = OHLCV_COLUMNS) -> str:
    """Stable hash of the raw input bars, stored as evidence with every run."""

    hasher = hashlib.sha256()
    payload = bars[list(columns)].to_csv(float_format="%.10g")
    hasher.update(payload.encode("utf-8"))
    return hasher.hexdigest()
