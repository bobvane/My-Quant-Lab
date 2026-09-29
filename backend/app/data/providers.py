"""Market data providers.

``YahooFinanceProvider`` is the V1 implementation of
:class:`app.domain.protocols.MarketDataProvider`. The business layer only talks
to the protocol, so a different vendor can be dropped in without touching the
quant core.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Protocol

import pandas as pd

from app.core.config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "MarketDataProvider",
    "YahooFinanceProvider",
    "get_market_data_provider",
    "SyntheticProvider",
]

_TIMEFRAMES = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "1d": "1d",
    "1w": "1wk",
}


def _midnight_utc(value: dt.datetime) -> dt.datetime:
    """Floor an (aware) datetime to UTC midnight."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)


class MarketDataProvider(Protocol):
    name: str

    def list_assets(self) -> list[dict[str, Any]]: ...

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: dt.datetime,
        end: dt.datetime,
        *,
        adjusted: bool = True,
    ) -> pd.DataFrame: ...

    def get_quote(self, symbol: str) -> dict[str, Any]: ...


class SyntheticProvider:
    """Deterministic offline provider used for tests and NAS smoke checks.

    It generates a reproducible random walk from a seed derived from the symbol
    so the demo dashboard works even without external API keys.
    """

    name = "synthetic"

    def list_assets(self) -> list[dict[str, Any]]:
        return [
            {
                "symbol": "DEMO-AAPL",
                "display_name": "Demo Large Cap",
                "asset_class": "stock",
                "currency": "USD",
                "exchange": "NASDAQ",
            },
            {
                "symbol": "DEMO-BTC",
                "display_name": "Demo Bitcoin",
                "asset_class": "crypto",
                "currency": "USD",
                "exchange": "CRYPTO",
            },
        ]

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: dt.datetime,
        end: dt.datetime,
        *,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """Daily bars aligned to UTC midnight.

        Alignment matters: if timestamps were derived from ``now`` the same
        request would return different bar timestamps on every call and repeated
        syncs would duplicate data.
        """

        import zlib

        import numpy as np

        # zlib.crc32 (not hash()) so the series is identical across processes;
        # Python's string hash is randomised per interpreter run.
        seed = zlib.crc32(symbol.encode("utf-8"))
        rng = np.random.default_rng(seed)

        start_day = _midnight_utc(start)
        end_day = _midnight_utc(end)
        periods = max(2, int((end_day - start_day).days) + 1)
        index = pd.date_range(start=start_day, periods=periods, freq="D", tz="UTC")

        steps = rng.normal(0.0004, 0.015, size=periods)
        close = 100.0 * np.exp(np.cumsum(steps))
        open_ = np.concatenate([[close[0]], close[:-1]])
        # OHLC consistency: high/low must bracket both open and close
        body_high = np.maximum(open_, close)
        body_low = np.minimum(open_, close)
        high = body_high * (1 + rng.uniform(0.0, 0.01, size=periods))
        low = body_low * (1 - rng.uniform(0.0, 0.01, size=periods))
        volume = rng.integers(1_000_000, 5_000_000, size=periods).astype(float)
        return pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=index,
        ).rename_axis("timestamp")

    def get_quote(self, symbol: str) -> dict[str, Any]:
        end = dt.datetime.now(tz=dt.UTC)
        frame = self.get_ohlcv(symbol, "1d", end - dt.timedelta(days=5), end)
        last = frame.iloc[-1]
        return {
            "symbol": symbol,
            "price": float(last["close"]),
            "currency": "USD",
            "as_of": frame.index[-1].isoformat(),
            "source": self.name,
        }


class YahooFinanceProvider:
    """Thin wrapper over ``yfinance`` (install optional extra ``market-data``)."""

    name = "yahoo_finance"

    def __init__(self) -> None:
        self._module: Any | None = None

    def _ensure(self) -> Any:
        if self._module is None:
            try:
                import yfinance  # type: ignore
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "yfinance is not installed; install the 'market-data' extra"
                ) from exc
            self._module = yfinance
        return self._module

    def list_assets(self) -> list[dict[str, Any]]:
        return [
            {
                "symbol": "AAPL",
                "display_name": "Apple Inc.",
                "asset_class": "stock",
                "currency": "USD",
                "exchange": "NASDAQ",
            },
            {
                "symbol": "MSFT",
                "display_name": "Microsoft Corp.",
                "asset_class": "stock",
                "currency": "USD",
                "exchange": "NASDAQ",
            },
            {
                "symbol": "SPY",
                "display_name": "SPDR S&P 500 ETF",
                "asset_class": "etf",
                "currency": "USD",
                "exchange": "NYSEARCA",
            },
            {
                "symbol": "BTC-USD",
                "display_name": "Bitcoin USD",
                "asset_class": "crypto",
                "currency": "USD",
                "exchange": "CRYPTO",
            },
        ]

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: dt.datetime,
        end: dt.datetime,
        *,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        yf = self._ensure()
        interval = _TIMEFRAMES.get(timeframe, "1d")
        raw = yf.download(
            symbol,
            start=start.date().isoformat(),
            end=(end + dt.timedelta(days=1)).date().isoformat(),
            interval=interval,
            auto_adjust=adjusted,
            progress=False,
        )
        if raw is None or raw.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        frame = raw.reset_index()
        frame = frame.rename(columns=str.lower)
        keep = ["timestamp", "open", "high", "low", "close", "volume"]
        frame = frame[[c for c in keep if c in frame.columns]]
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        return frame.set_index("timestamp").sort_index()

    def get_quote(self, symbol: str) -> dict[str, Any]:
        end = dt.datetime.now(tz=dt.UTC)
        frame = self.get_ohlcv(symbol, "1d", end - dt.timedelta(days=10), end)
        if frame.empty:
            return {"symbol": symbol, "price": None, "source": self.name}
        return {
            "symbol": symbol,
            "price": float(frame["close"].iloc[-1]),
            "currency": "USD",
            "as_of": frame.index[-1].isoformat(),
            "source": self.name,
        }


def get_market_data_provider() -> MarketDataProvider:
    """Return the configured provider instance."""

    name = settings.market_data_provider
    if name in {"synthetic", "demo"}:
        return SyntheticProvider()
    if name in {"yahoo_finance", "yahoo"}:
        return YahooFinanceProvider()
    raise ValueError(f"unknown market data provider '{name}'")
