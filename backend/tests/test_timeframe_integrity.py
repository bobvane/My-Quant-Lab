"""D3 — the timeframe a caller asks for is the timeframe it gets.

A sync request carries a timeframe. Before this was enforced the synthetic provider
ignored it (always a daily walk) and the Yahoo provider fell back to ``1d`` for
anything it did not recognise, so ``timeframe=1h`` produced a series *labelled* 1h
that actually held daily bars — and every feature, backtest and signal downstream
read the wrong resolution without a single error message. A substitution nobody can
see is worse than a refusal.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from app.api.schemas import MarketDataSyncRequest
from app.data.providers import (
    SUPPORTED_TIMEFRAMES,
    ProviderError,
    SyntheticProvider,
    UnsupportedTimeframe,
    YahooFinanceProvider,
)
from app.domain.models import MarketDataSeries

_START = dt.datetime(2026, 9, 20, tzinfo=dt.UTC)
_END = dt.datetime(2026, 9, 30, tzinfo=dt.UTC)


def test_the_synthetic_provider_refuses_an_intraday_timeframe() -> None:
    with pytest.raises(UnsupportedTimeframe):
        SyntheticProvider().get_ohlcv("DEMO-AAPL", "1h", _START, _END)


def test_the_synthetic_provider_still_serves_its_daily_bars() -> None:
    frame = SyntheticProvider().get_ohlcv("DEMO-AAPL", "1d", _START, _END)

    assert not frame.empty


def test_yahoo_refuses_an_unknown_timeframe_before_importing_yfinance() -> None:
    """The refusal must not depend on the optional dependency being installed."""

    provider = YahooFinanceProvider()

    with pytest.raises(UnsupportedTimeframe):
        provider.get_ohlcv("AAPL", "3d", _START, _END)
    assert provider._module is None, "the timeframe check must run before _ensure()"


def test_every_advertised_timeframe_belongs_to_the_supported_set() -> None:
    for candidate in (SyntheticProvider(), YahooFinanceProvider()):
        assert candidate.TIMEFRAMES <= SUPPORTED_TIMEFRAMES
        assert candidate.TIMEFRAMES, f"{candidate.name} advertises no timeframe at all"


def test_a_sync_request_for_an_unsupported_timeframe_is_rejected(client, db_session) -> None:
    payload = MarketDataSyncRequest(
        symbol="DEMO-AAPL", timeframe="1h", start=_START, end=_END
    ).model_dump(mode="json")

    response = client.post("/api/v1/market-data/sync", json=payload)

    assert response.status_code == 400
    assert "1h" in response.json()["detail"]
    stored = db_session.scalars(
        select(MarketDataSeries).where(MarketDataSeries.timeframe == "1h")
    ).all()
    assert stored == [], "a refused request must not leave a mislabelled series behind"


def test_a_sync_request_records_the_timeframe_it_actually_used(client) -> None:
    payload = MarketDataSyncRequest(
        symbol="DEMO-AAPL", timeframe="1d", start=_START, end=_END
    ).model_dump(mode="json")

    body = client.post("/api/v1/market-data/sync", json=payload).json()

    assert body["timeframe"] == "1d"
    assert body["inserted"] > 0


def test_an_unsupported_timeframe_is_a_provider_error_not_a_symbol_error() -> None:
    """The API answers 400 for both, but the two say different things — and a
    caller catching ``ProviderError`` should not have to guess which one it got."""

    assert issubclass(UnsupportedTimeframe, ProviderError)
