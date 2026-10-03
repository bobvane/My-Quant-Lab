"""Market data providers.

``YahooFinanceProvider`` is the V1 implementation of
:class:`app.domain.protocols.MarketDataProvider`. The business layer only talks
to the protocol, so a different vendor can be dropped in without touching the
quant core.
"""

from __future__ import annotations

import datetime as dt
import logging
import zoneinfo
from typing import Any, Protocol

import pandas as pd

from app.core.config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "PROVIDER_NAMES",
    "SUPPORTED_TIMEFRAMES",
    "MarketDataProvider",
    "ProviderError",
    "SymbolNotServed",
    "SyntheticProvider",
    "UnsupportedTimeframe",
    "YahooFinanceProvider",
    "asset_metadata_for",
    "get_market_data_provider",
    "infer_asset_class",
    "mark_closed_bars",
    "resolve_provider_name",
]

_TIMEFRAMES = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "1d": "1d",
    "1w": "1wk",
}

# Every timeframe any provider can serve. A request outside this set is refused
# rather than quietly answered with daily bars (ADR-120).
SUPPORTED_TIMEFRAMES = frozenset(_TIMEFRAMES)

# How long a bar of each timeframe lasts, used to decide whether it has closed.
# Calendar timeframes are compared by date; intraday ones by their exact period.
# Close is a fact about one bar, not about the newest one (ADR-117).
_CALENDAR_PERIOD_DAYS = {"1d": 1, "1w": 7, "1wk": 7}
_INTRADAY_PERIOD_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1_800,
    "1h": 3_600,
    "4h": 14_400,
}

# Crypto pairs quote 24/7 and carry no exchange session.
_CRYPTO_QUOTES = ("USD", "USDT", "USDC", "BTC", "ETH", "EUR", "BUSD")
# Well-known ETF tickers, so a synced symbol is labelled honestly rather than
# silently filed as a single stock.
_KNOWN_ETFS = frozenset({"SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "GLD", "SLV", "TLT", "ARKK"})


class ProviderError(RuntimeError):
    """Raised for a provider-side problem the caller should surface."""


class SymbolNotServed(ProviderError):
    """The requested symbol is not served by this provider.

    Kept distinct so the API can answer 400 (wrong provider chosen) instead of
    502 (something upstream broke).
    """


class UnsupportedTimeframe(ProviderError):
    """The provider cannot produce bars at the requested resolution.

    Raised instead of substituting a different timeframe: a series labelled ``1h``
    that actually holds daily bars is worse than an error, because features,
    backtests and signals then read the wrong resolution without anyone noticing.
    """


def infer_asset_class(symbol: str) -> str:
    """Best-effort asset class from the ticker shape.

    ``BTC-USD`` → crypto, ``SPY`` → etf, ``^GSPC`` → index, else stock.
    Guessing wrong is worse than being generic, so the default is ``stock``.
    """

    text = (symbol or "").strip().upper()
    if text.startswith("^"):
        return "index"
    if "-" in text:
        base, _, quote = text.partition("-")
        if quote in _CRYPTO_QUOTES and base.isalpha():
            return "crypto"
    if text.endswith(("=F", "=X")):
        return "future"
    if text in _KNOWN_ETFS:
        return "etf"
    return "stock"


def _bar_period(timeframe: str) -> tuple[int, int] | None:
    """Return ``(calendar_days, seconds)`` for a timeframe, or ``None`` if unknown."""

    if timeframe in _CALENDAR_PERIOD_DAYS:
        return _CALENDAR_PERIOD_DAYS[timeframe], 0
    if timeframe in _INTRADAY_PERIOD_SECONDS:
        return 0, _INTRADAY_PERIOD_SECONDS[timeframe]
    return None


def mark_closed_bars(
    frame: pd.DataFrame, timeframe: str, timezone_name: str = "UTC", now: dt.datetime | None = None
) -> pd.DataFrame:
    """Flag, bar by bar, whether each bar has actually closed.

    A provider happily returns today's still-forming daily candle. Treating it as
    closed would let the strategy see an unfinished bar — exactly the lookahead
    the project forbids. Every bar is therefore judged against *its own* period:
    it counts as closed once the period it covers is over. Judging only the last
    bar, or comparing its date with today's, gets two cases wrong — the weekly bar
    whose timestamp is this Monday (still running on Wednesday) and the hourly bar
    containing ``now``.

    A timeframe whose length is unknown is never assumed closed; the newest bar
    stays forming, because guessing its period is the same mistake one level up.
    """

    out = frame.copy()
    if out.empty:
        return out
    out["is_closed"] = True

    try:
        tz = zoneinfo.ZoneInfo(timezone_name)
    except Exception:
        tz = dt.UTC
    reference = (now or dt.datetime.now(tz=dt.UTC)).astimezone(tz)
    reference_utc = reference.astimezone(dt.UTC)

    period = _bar_period(timeframe)
    if period is None:
        out.iloc[-1, out.columns.get_loc("is_closed")] = False
        return out
    period_days, period_seconds = period

    flags: list[bool] = []
    for raw in out.index:
        stamp = pd.Timestamp(raw)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("UTC")
        if period_days:
            local = stamp.tz_convert(tz)
            flags.append(reference.date() >= local.date() + dt.timedelta(days=period_days))
        else:
            closes_at = stamp.tz_convert(dt.UTC) + dt.timedelta(seconds=period_seconds)
            flags.append(reference_utc >= closes_at)

    out["is_closed"] = flags
    return out


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
    """Deterministic offline provider used for demos, tests and smoke checks.

    It generates a reproducible random walk seeded from the symbol, so the
    dashboard can be exercised without any API key.

    It deliberately serves **only** its own ``DEMO-*`` tickers. Handing back a
    random walk under the name ``AAPL`` would put fabricated prices behind a
    real ticker — a research tool must never do that, however convenient.
    """

    name = "synthetic"

    KNOWN_SYMBOLS = ("DEMO-AAPL", "DEMO-BTC")

    # The random walk is a daily series; it cannot stand in for intraday bars.
    TIMEFRAMES = frozenset({"1d"})

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

        if symbol.upper() not in self.KNOWN_SYMBOLS:
            raise SymbolNotServed(
                f"the synthetic provider only serves {', '.join(self.KNOWN_SYMBOLS)}; "
                f"'{symbol}' is not one of them. Set MARKET_DATA_PROVIDER=yahoo_finance "
                "to fetch real market data."
            )
        if timeframe not in self.TIMEFRAMES:
            raise UnsupportedTimeframe(
                f"the synthetic provider only produces {', '.join(sorted(self.TIMEFRAMES))} "
                f"bars, so it cannot serve '{timeframe}'. Set MARKET_DATA_PROVIDER="
                "yahoo_finance for intraday data."
            )

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

    TIMEFRAMES = SUPPORTED_TIMEFRAMES

    def __init__(self) -> None:
        self._module: Any | None = None

    def _ensure(self) -> Any:
        if self._module is None:
            try:
                import yfinance  # type: ignore
            except ImportError as exc:
                raise ProviderError(
                    "yfinance is not installed in this image, so real market data "
                    "is unavailable. Use MARKET_DATA_PROVIDER=synthetic, or install "
                    "the 'market-data' extra."
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
        """Fetch OHLCV from Yahoo Finance.

        Robust against the shapes yfinance actually returns: a single ticker can
        come back with flat columns or a MultiIndex, the date column may be named
        ``Date`` or ``Datetime``, and an in-progress session yields NaN rows.
        """

        # Checked before ``_ensure()``: a wrong timeframe is a caller error and must
        # not depend on the optional dependency being installed.
        if timeframe not in _TIMEFRAMES:
            raise UnsupportedTimeframe(
                f"yahoo finance cannot serve '{timeframe}'; supported: "
                f"{', '.join(sorted(_TIMEFRAMES))}."
            )
        yf = self._ensure()
        interval = _TIMEFRAMES[timeframe]
        try:
            raw = yf.download(
                symbol,
                start=start.date().isoformat(),
                end=(end + dt.timedelta(days=1)).date().isoformat(),
                interval=interval,
                auto_adjust=adjusted,
                progress=False,
                # No thread pool: this runs inside a NAS container, and yfinance
                # otherwise spawns worker threads per request.
                threads=False,
            )
        except Exception as exc:
            raise ProviderError(
                f"yahoo finance request failed for '{symbol}': {type(exc).__name__}: {exc}"
            ) from exc

        if raw is None or len(raw) == 0:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        frame = raw.copy()
        # Single-ticker downloads may carry MultiIndex columns like ('Close','AAPL').
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = [str(col[0]).lower() for col in frame.columns]
        else:
            frame.columns = [str(col).lower() for col in frame.columns]

        frame = frame.reset_index()
        # The index column is 'Date' or 'Datetime' depending on the interval.
        frame = frame.rename(columns={frame.columns[0]: "timestamp"})
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)

        for column in ("open", "high", "low", "close", "volume"):
            if column not in frame.columns:
                frame[column] = 0.0 if column == "volume" else pd.NA
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

        # Drop rows where the candle is incomplete/garbage; Yahoo pads the
        # current session with NaN when the market is closed.
        frame = frame.dropna(subset=["open", "high", "low", "close"])
        if frame.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        frame["volume"] = frame["volume"].fillna(0.0)
        return frame.set_index("timestamp")[["open", "high", "low", "close", "volume"]].sort_index()

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


_PROVIDER_ALIASES = {
    "synthetic": "synthetic",
    "demo": "synthetic",
    "yahoo_finance": "yahoo_finance",
    "yahoo": "yahoo_finance",
    "yfinance": "yahoo_finance",
}

PROVIDER_NAMES = tuple(sorted({"synthetic", "yahoo_finance"}))


def resolve_provider_name(name: str | None = None) -> str:
    """Map a user-supplied provider name onto a canonical provider name."""

    raw = (name or settings.market_data_provider or "synthetic").strip().lower()
    resolved = _PROVIDER_ALIASES.get(raw)
    if resolved is None:
        raise ProviderError(
            f"unknown market data provider '{name}'. Supported: {', '.join(PROVIDER_NAMES)}."
        )
    return resolved


def get_market_data_provider(name: str | None = None) -> MarketDataProvider:
    """Return a provider instance.

    ``name`` lets a caller override the configured default per request; when it
    is omitted the setting ``MARKET_DATA_PROVIDER`` decides.
    """

    resolved = resolve_provider_name(name)
    if resolved == "synthetic":
        return SyntheticProvider()
    return YahooFinanceProvider()


def asset_metadata_for(provider: MarketDataProvider, symbol: str) -> dict[str, Any]:
    """Build asset metadata for a newly synced symbol.

    Prefers what the provider declares, then falls back to ticker-shape
    inference. Never invents an exchange.
    """

    text = (symbol or "").strip()
    for entry in provider.list_assets():
        if str(entry.get("symbol", "")).upper() == text.upper():
            return dict(entry)
    return {
        "symbol": text,
        "display_name": text,
        "asset_class": infer_asset_class(text),
        "currency": "USD",
        "exchange": None,
    }
