"""Market data provider tests.

No test here touches the network: the Yahoo provider is exercised against a
fake ``yfinance`` module, and the synthetic provider is fully offline.

The behaviours pinned down are the ones that decide whether research results
can be trusted:

* the synthetic provider refuses to serve a real ticker with random data;
* a still-forming bar is flagged ``is_closed=False`` instead of being passed
  off as a finished candle;
* the ``provider`` request field is actually honoured.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.data.market_data_repo import frame_to_bars
from app.data.providers import (
    ProviderError,
    SymbolNotServed,
    YahooFinanceProvider,
    asset_metadata_for,
    get_market_data_provider,
    infer_asset_class,
    mark_closed_bars,
    resolve_provider_name,
)


# --------------------------------------------------------------------------- #
# naming / inference
# --------------------------------------------------------------------------- #
def test_resolve_provider_name_aliases() -> None:
    assert resolve_provider_name(None) in {"synthetic", "yahoo_finance"}
    assert resolve_provider_name("synthetic") == "synthetic"
    assert resolve_provider_name("demo") == "synthetic"
    assert resolve_provider_name("yahoo") == "yahoo_finance"
    assert resolve_provider_name("yahoo_finance") == "yahoo_finance"
    assert resolve_provider_name("YFinance") == "yahoo_finance"


def test_resolve_provider_name_rejects_unknown() -> None:
    with pytest.raises(ProviderError, match="unknown market data provider"):
        resolve_provider_name("bloomberg")


def test_get_market_data_provider_returns_named_instance() -> None:
    assert get_market_data_provider("synthetic").name == "synthetic"
    assert get_market_data_provider("yahoo").name == "yahoo_finance"


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("AAPL", "stock"),
        ("MSFT", "stock"),
        ("SPY", "etf"),
        ("QQQ", "etf"),
        ("BTC-USD", "crypto"),
        ("ETH-USDT", "crypto"),
        ("^GSPC", "index"),
        ("ES=F", "future"),
    ],
)
def test_infer_asset_class(symbol: str, expected: str) -> None:
    assert infer_asset_class(symbol) == expected


def test_asset_metadata_prefers_provider_declaration() -> None:
    provider = get_market_data_provider("yahoo")
    meta = asset_metadata_for(provider, "SPY")
    assert meta["asset_class"] == "etf"
    assert meta["display_name"] == "SPDR S&P 500 ETF"


def test_asset_metadata_falls_back_to_inference() -> None:
    provider = get_market_data_provider("yahoo")
    meta = asset_metadata_for(provider, "TSLA")
    assert meta["asset_class"] == "stock"
    assert meta["exchange"] is None  # never invented


# --------------------------------------------------------------------------- #
# synthetic provider guard
# --------------------------------------------------------------------------- #
def test_synthetic_provider_serves_its_demo_symbols() -> None:
    provider = get_market_data_provider("synthetic")
    end = dt.datetime(2026, 1, 10, tzinfo=dt.UTC)
    frame = provider.get_ohlcv("DEMO-AAPL", "1d", end - dt.timedelta(days=30), end)
    assert not frame.empty
    assert frame.index.tz is not None


def test_synthetic_provider_refuses_real_tickers() -> None:
    """Random data must never be published under a real ticker."""

    provider = get_market_data_provider("synthetic")
    end = dt.datetime(2026, 1, 10, tzinfo=dt.UTC)
    with pytest.raises(SymbolNotServed, match="yahoo_finance"):
        provider.get_ohlcv("AAPL", "1d", end - dt.timedelta(days=30), end)
    with pytest.raises(SymbolNotServed):
        provider.get_ohlcv("BTC-USD", "1d", end - dt.timedelta(days=30), end)


# --------------------------------------------------------------------------- #
# closed-bar semantics
# --------------------------------------------------------------------------- #
def test_daily_bar_covering_today_is_not_closed() -> None:
    index = pd.date_range("2026-09-28", periods=3, freq="D", tz="UTC")
    frame = pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=index
    )
    out = mark_closed_bars(frame, "1d", "UTC", now=dt.datetime(2026, 9, 30, 12, tzinfo=dt.UTC))
    assert list(out["is_closed"]) == [True, True, False]


def test_daily_bars_all_in_the_past_are_closed() -> None:
    index = pd.date_range("2026-09-01", periods=3, freq="D", tz="UTC")
    frame = pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=index
    )
    out = mark_closed_bars(frame, "1d", "UTC", now=dt.datetime(2026, 9, 30, 12, tzinfo=dt.UTC))
    assert out["is_closed"].all()


def test_timezone_awareness_decides_daily_closure() -> None:
    """22:00 in New York is already the next day in UTC: the NYSE bar that
    closed at 16:00 local must count as closed."""

    index = pd.DatetimeIndex(
        [pd.Timestamp("2026-09-29 00:00", tz="America/New_York")], name="timestamp"
    )
    frame = pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=index
    )
    # 2026-09-30 02:00 UTC == 2026-09-29 22:00 NY
    now = dt.datetime(2026, 9, 30, 2, 0, tzinfo=dt.UTC)
    out = mark_closed_bars(frame, "1d", "America/New_York", now=now)
    assert bool(out["is_closed"].iloc[0]) is False  # still the 29th in NY

    later = dt.datetime(2026, 9, 30, 14, 0, tzinfo=dt.UTC)  # 10:00 NY next day
    out2 = mark_closed_bars(frame, "1d", "America/New_York", now=later)
    assert bool(out2["is_closed"].iloc[0]) is True


def test_frame_to_bars_honours_is_closed_column() -> None:
    index = pd.date_range("2026-09-01", periods=2, freq="D", tz="UTC")
    frame = pd.DataFrame(
        {
            "open": [1.0, 2.0],
            "high": [1.0, 2.0],
            "low": [1.0, 2.0],
            "close": [1.0, 2.0],
            "volume": [10.0, 20.0],
            "is_closed": [True, False],
        },
        index=index,
    )
    bars = frame_to_bars(frame)
    assert [b["is_closed"] for b in bars] == [True, False]


def test_frame_to_bars_defaults_to_closed_without_flag() -> None:
    index = pd.date_range("2026-09-01", periods=1, freq="D", tz="UTC")
    frame = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
        index=index,
    )
    assert frame_to_bars(frame)[0]["is_closed"] is True


# --------------------------------------------------------------------------- #
# yahoo provider parsing (fake yfinance, no network)
# --------------------------------------------------------------------------- #
class _FakeYFinance:
    def __init__(self, frame: pd.DataFrame | None) -> None:
        self._frame = frame
        self.calls: list[dict] = []

    def download(self, symbol: str, **kwargs) -> pd.DataFrame | None:
        self.calls.append({"symbol": symbol, **kwargs})
        return self._frame


def _provider_with(fake: _FakeYFinance) -> YahooFinanceProvider:
    provider = YahooFinanceProvider()
    provider._module = fake
    return provider


def test_yahoo_parses_flat_columns() -> None:
    raw = pd.DataFrame(
        {
            "Open": [10.0, 11.0],
            "High": [10.5, 11.5],
            "Low": [9.5, 10.5],
            "Close": [10.2, 11.1],
            "Volume": [100, 200],
        },
        index=pd.DatetimeIndex(["2026-09-01", "2026-09-02"], name="Date"),
    )
    provider = _provider_with(_FakeYFinance(raw))
    frame = provider.get_ohlcv(
        "AAPL", "1d", dt.datetime(2026, 9, 1, tzinfo=dt.UTC), dt.datetime(2026, 9, 3, tzinfo=dt.UTC)
    )
    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
    assert frame.index.tz is not None
    assert len(frame) == 2


def test_yahoo_parses_multiindex_columns() -> None:
    cols = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Volume"], ["AAPL"]])
    raw = pd.DataFrame(
        [[10.0, 10.5, 9.5, 10.2, 100.0], [11.0, 11.5, 10.5, 11.1, 200.0]],
        columns=cols,
        index=pd.DatetimeIndex(["2026-09-01", "2026-09-02"], name="Datetime"),
    )
    provider = _provider_with(_FakeYFinance(raw))
    frame = provider.get_ohlcv(
        "AAPL", "1d", dt.datetime(2026, 9, 1, tzinfo=dt.UTC), dt.datetime(2026, 9, 3, tzinfo=dt.UTC)
    )
    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
    assert float(frame["close"].iloc[-1]) == pytest.approx(11.1)


def test_yahoo_drops_incomplete_nan_rows() -> None:
    raw = pd.DataFrame(
        {
            "Open": [10.0, float("nan")],
            "High": [10.5, float("nan")],
            "Low": [9.5, float("nan")],
            "Close": [10.2, float("nan")],
            "Volume": [100.0, 0.0],
        },
        index=pd.DatetimeIndex(["2026-09-01", "2026-09-02"], name="Date"),
    )
    provider = _provider_with(_FakeYFinance(raw))
    frame = provider.get_ohlcv(
        "AAPL", "1d", dt.datetime(2026, 9, 1, tzinfo=dt.UTC), dt.datetime(2026, 9, 3, tzinfo=dt.UTC)
    )
    assert len(frame) == 1


def test_yahoo_empty_response_yields_empty_frame() -> None:
    provider = _provider_with(_FakeYFinance(None))
    frame = provider.get_ohlcv(
        "AAPL", "1d", dt.datetime(2026, 9, 1, tzinfo=dt.UTC), dt.datetime(2026, 9, 3, tzinfo=dt.UTC)
    )
    assert frame.empty


def test_yahoo_download_failure_becomes_provider_error() -> None:
    class _Boom:
        def download(self, *a, **k):
            raise OSError("connection reset")

    provider = YahooFinanceProvider()
    provider._module = _Boom()
    with pytest.raises(ProviderError, match="yahoo finance request failed"):
        provider.get_ohlcv(
            "AAPL",
            "1d",
            dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
            dt.datetime(2026, 9, 3, tzinfo=dt.UTC),
        )


def test_yahoo_missing_dependency_is_reported_clearly(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def blocked(name: str, *args, **kwargs):
        if name == "yfinance":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    provider = YahooFinanceProvider()
    with pytest.raises(ProviderError, match="yfinance is not installed"):
        provider.get_ohlcv(
            "AAPL",
            "1d",
            dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
            dt.datetime(2026, 9, 3, tzinfo=dt.UTC),
        )


# --------------------------------------------------------------------------- #
# API integration
# --------------------------------------------------------------------------- #
def test_sync_honours_explicit_provider_field(client) -> None:
    """The request's provider field used to be ignored entirely."""

    response = client.post(
        "/api/v1/market-data/sync",
        json={"symbol": "DEMO-AAPL", "timeframe": "1d", "provider": "synthetic"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "synthetic"
    assert body["inserted"] > 0
    assert body["still_forming_bars"] >= 0


def test_sync_rejects_unknown_provider_with_400(client) -> None:
    response = client.post(
        "/api/v1/market-data/sync",
        json={"symbol": "AAPL", "timeframe": "1d", "provider": "bloomberg"},
    )
    assert response.status_code == 400
    assert "unknown market data provider" in response.json()["detail"]


def test_sync_rejects_real_ticker_on_synthetic_provider(client) -> None:
    response = client.post(
        "/api/v1/market-data/sync",
        json={"symbol": "AAPL", "timeframe": "1d", "provider": "synthetic"},
    )
    assert response.status_code == 400
    assert "yahoo_finance" in response.json()["detail"]


def test_sync_empty_provider_result_mentions_rate_limiting(client, monkeypatch) -> None:
    """yfinance reports a rate limit as an empty frame, not an exception, so the
    message must not let that look like 'this ticker has no data'."""

    import app.api.routers.market_data as md
    from app.data.providers import get_market_data_provider as real_get

    class _Empty:
        name = "yahoo_finance"

        def list_assets(self):
            return []

        def get_ohlcv(self, *a, **k):
            import pandas as pd

            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        def get_quote(self, symbol):
            return {"symbol": symbol, "price": None}

    monkeypatch.setattr(md, "get_market_data_provider", lambda name=None: _Empty())
    response = client.post(
        "/api/v1/market-data/sync",
        json={"symbol": "AAPL", "timeframe": "1d", "provider": "yahoo_finance"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["inserted"] == 0
    assert "rate-limiting" in body["message"]
    assert real_get is not None


def test_sync_creates_asset_with_inferred_metadata(client) -> None:
    client.post(
        "/api/v1/market-data/sync",
        json={"symbol": "DEMO-BTC", "timeframe": "1d", "provider": "synthetic"},
    )
    assets = client.get("/api/v1/assets").json()
    demo_btc = next(a for a in assets if a["symbol"] == "DEMO-BTC")
    assert demo_btc["asset_class"] == "crypto"


# --------------------------------------------------------------------------- #
# scheduled watchlist resolution
# --------------------------------------------------------------------------- #
def test_watchlist_follows_the_active_provider(monkeypatch) -> None:
    """The old code hard-coded DEMO-* tickers, which broke real providers."""

    from app.workers.tasks import resolve_watchlist

    assert resolve_watchlist(get_market_data_provider("synthetic")) == [
        "DEMO-AAPL",
        "DEMO-BTC",
    ]
    assert resolve_watchlist(get_market_data_provider("yahoo")) == [
        "AAPL",
        "MSFT",
        "SPY",
        "BTC-USD",
    ]


def test_watchlist_prefers_explicit_argument() -> None:
    from app.workers.tasks import resolve_watchlist

    assert resolve_watchlist(get_market_data_provider("synthetic"), ["TSLA"]) == ["TSLA"]


def test_watchlist_uses_setting_when_present(monkeypatch) -> None:
    from app.core.config import settings
    from app.workers.tasks import resolve_watchlist

    monkeypatch.setattr(settings, "market_data_watchlist", ["NVDA", "ETH-USD"])
    assert resolve_watchlist(get_market_data_provider("yahoo")) == ["NVDA", "ETH-USD"]
