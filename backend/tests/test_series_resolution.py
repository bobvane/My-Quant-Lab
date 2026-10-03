"""R3 — one symbol, several series: which data does a result come from?

A backtest request names a symbol and a timeframe, but the database may hold more
than one series for that pair: the unique key also contains the provider source and
the dataset version, so a second provider (or a re-sync under a new source) creates
a second row. The resolution used to be "whatever ``db.scalar`` returns first" — no
order, no archived filter, and an explicit ``series_id`` was accepted even when it
contradicted the requested symbol or timeframe. A number nobody can trace back to a
dataset is not a research result.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import select

from app.api.schemas import BacktestCreate, MarketDataSyncRequest, StrategyVersionCreate
from app.data.market_data_repo import frame_to_bars, get_or_create_series, upsert_bars
from app.domain.models import Asset, BacktestRun, MarketDataSeries, MarketDataSource

DSL: dict = {
    "schema_version": "1.0",
    "strategy": {"id": "series-resolution", "name": "Series Resolution", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "ema20"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0, "take_profit_r_multiple": 2.0},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}

SYNC_END = dt.datetime(2026, 1, 31, tzinfo=dt.UTC)


def _frame(end: dt.datetime, bars: int = 120) -> pd.DataFrame:
    index = pd.date_range(end=end, periods=bars, freq="D", tz="UTC")
    close = pd.Series(range(bars), index=index, dtype=float) + 100.0
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000.0,
        },
        index=index,
    ).rename_axis("timestamp")
    frame["is_closed"] = True
    return frame


def _utc(value: dt.datetime) -> dt.datetime:
    """SQLite hands datetimes back naive; compare them in UTC."""

    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


def _setup(client, db_session) -> tuple[int, Asset, MarketDataSeries]:
    """Sync one symbol and create a strategy version; return its first series."""

    client.post(
        "/api/v1/market-data/sync",
        json=MarketDataSyncRequest(
            symbol="DEMO-AAPL",
            timeframe="1d",
            start=SYNC_END - dt.timedelta(days=400),
            end=SYNC_END,
        ).model_dump(mode="json"),
    )
    strategy = client.post("/api/v1/strategies", json={"name": "Resolution"}).json()
    version = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json=StrategyVersionCreate(version="1.0.0", dsl=DSL).model_dump(),
    ).json()

    asset = db_session.scalar(select(Asset).where(Asset.symbol == "DEMO-AAPL"))
    assert asset is not None
    series = db_session.scalars(
        select(MarketDataSeries).where(MarketDataSeries.asset_id == asset.id)
    ).all()
    assert len(series) == 1
    return int(version["id"]), asset, series[0]


def _add_series(
    db_session,
    asset: Asset,
    *,
    source_name: str,
    end: dt.datetime,
    bars: int = 120,
    timeframe: str = "1d",
) -> MarketDataSeries:
    """Create a second series for the same asset/timeframe, from another source."""

    source = MarketDataSource(name=source_name, base_url="test")
    db_session.add(source)
    db_session.flush()
    series = get_or_create_series(db_session, asset=asset, timeframe=timeframe, source_id=source.id)
    upsert_bars(db_session, series, frame_to_bars(_frame(end, bars)))
    db_session.commit()
    return series


def _run_backtest(client, version_id: int, **overrides):
    payload = BacktestCreate(
        strategy_version_id=version_id, symbol="DEMO-AAPL", timeframe="1d"
    ).model_dump(mode="json")
    payload.update(overrides)
    return client.post("/api/v1/backtests", json=payload)


def test_the_series_with_the_fresh_data_wins_over_the_newer_row(client, db_session) -> None:
    version_id, asset, first = _setup(client, db_session)
    # Second row, and the older history: a fresher series must beat "the newest id".
    fresher = _add_series(
        db_session, asset, source_name="yahoo_finance", end=SYNC_END + dt.timedelta(days=28)
    )
    assert fresher.id > first.id

    response = _run_backtest(client, version_id)

    assert response.status_code == 200, response.text
    assert response.json()["dataset_version_id"] == fresher.id


def test_the_rule_is_the_data_not_the_row_order(client, db_session) -> None:
    """The first-inserted series wins when *it* holds the fresher history."""

    version_id, asset, first = _setup(client, db_session)
    older = _add_series(
        db_session, asset, source_name="yahoo_finance", end=SYNC_END - dt.timedelta(days=30)
    )
    assert older.id > first.id
    assert _utc(first.series_end) > _utc(older.series_end)

    response = _run_backtest(client, version_id)

    assert response.status_code == 200, response.text
    assert response.json()["dataset_version_id"] == first.id


def test_an_archived_series_is_never_used(client, db_session) -> None:
    version_id, asset, first = _setup(client, db_session)
    _add_series(
        db_session, asset, source_name="yahoo_finance", end=SYNC_END + dt.timedelta(days=28)
    )

    # Deleting a series a run still references archives it instead of purging it, which
    # is exactly how a series becomes archived in production.
    used = _run_backtest(client, version_id)
    assert used.status_code == 200, used.text
    chosen = used.json()["dataset_version_id"]

    deleted = client.delete(f"/api/v1/market-data/series/{chosen}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["archived"] is True
    db_session.expire_all()
    archived = db_session.get(MarketDataSeries, chosen)
    assert archived is not None and archived.is_archived is True

    response = _run_backtest(client, version_id)

    assert response.status_code == 200, response.text
    assert response.json()["dataset_version_id"] != chosen, "archived data must not be selected"
    assert first.id != chosen  # the surviving series is the one still in use


def test_a_backtest_names_the_series_and_its_source(client, db_session) -> None:
    version_id, asset, first = _setup(client, db_session)
    _add_series(
        db_session, asset, source_name="yahoo_finance", end=SYNC_END + dt.timedelta(days=28)
    )

    body = _run_backtest(client, version_id).json()

    assert body["dataset_version_id"] != first.id
    assert body["dataset_version"] == "v1"
    assert body["source"] == "yahoo_finance"
    assert body["symbol"] == "DEMO-AAPL"
    assert body["timeframe"] == "1d"
    stored = db_session.get(BacktestRun, body["id"])
    assert stored is not None and stored.dataset_version_id == body["dataset_version_id"]
    assert stored.result is not None
    assert stored.result.summary_json["source"] == "yahoo_finance"


def test_an_explicit_series_id_must_not_contradict_the_request(client, db_session) -> None:
    version_id, asset, first = _setup(client, db_session)
    other = _add_series(
        db_session, asset, source_name="yahoo_finance", end=SYNC_END + dt.timedelta(days=28)
    )

    response = _run_backtest(client, version_id, series_id=other.id, timeframe="1h")

    assert response.status_code == 422, response.text
    assert "1h" in response.json()["detail"]


def test_an_explicit_series_id_wins_when_it_is_consistent(client, db_session) -> None:
    version_id, asset, first = _setup(client, db_session)
    other = _add_series(
        db_session, asset, source_name="yahoo_finance", end=SYNC_END + dt.timedelta(days=28)
    )

    response = _run_backtest(client, version_id, series_id=first.id, symbol=None, timeframe="1d")

    assert response.status_code == 200, response.text
    assert response.json()["dataset_version_id"] == first.id
    assert first.id != other.id


def test_a_symbol_pointing_at_another_assets_series_is_rejected(client, db_session) -> None:
    version_id, asset, first = _setup(client, db_session)
    other_asset = Asset(symbol="OTHER", asset_class="stock")
    db_session.add(other_asset)
    db_session.flush()
    foreign = _add_series(db_session, other_asset, source_name="yahoo_finance", end=SYNC_END)

    response = _run_backtest(client, version_id, series_id=foreign.id)

    assert response.status_code == 422, response.text


def test_resolution_is_one_answered_rule(db_session) -> None:
    """The rule lives in one place, so a request it cannot answer is refused."""

    from app.data.market_data_repo import SeriesNotResolved, resolve_series

    with pytest.raises(SeriesNotResolved):
        resolve_series(db_session, timeframe="1d")


def test_a_request_without_a_symbol_is_refused(client, db_session) -> None:
    """No symbol must not mean "any asset that happens to have 1d bars"."""

    version_id, _asset, _series = _setup(client, db_session)

    response = client.post(
        "/api/v1/research/walk-forward",
        json={
            "strategy_version_id": version_id,
            "symbol": None,
            "timeframe": "1d",
            "train_bars": 100,
            "test_bars": 50,
        },
    )

    assert response.status_code in {400, 422}, response.text
