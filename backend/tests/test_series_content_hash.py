"""``market_data.content_hash`` must be written by whoever writes the bars (ADR-092).

The column was declared on ``MarketDataSeries`` and read by
``GET /market-data/series/{id}``, but nothing in the repository ever wrote it: the
existing ``series_content_hash()`` helper was only ever used as a local variable while
creating a backtest run. Every series therefore reported ``content_hash: null`` — a
field whose whole purpose is to let a caller confirm that the bars it is analysing are
the bars a stored result was computed from.

The refresh now happens inside ``upsert_bars()``, the one function both bar-writing
paths (``POST /market-data/sync`` and the scheduled sync worker) go through, so no
caller can forget it. These tests pin the semantics that makes the value useful:

* it covers every *closed* bar of the series, in timestamp order;
* re-syncing the same bars does not change it (idempotent sync, idempotent hash);
* adding a bar does change it;
* an empty series has no hash at all, which is not the hash of an empty frame;
* a real backtest records exactly this value in ``backtest_runs.dataset_hash``.
"""

from __future__ import annotations

import pandas as pd

from app.api.schemas import BacktestCreate, MarketDataSyncRequest, StrategyVersionCreate
from app.data.market_data_repo import (
    frame_to_bars,
    get_or_create_series,
    load_bars,
    series_content_hash,
    upsert_bars,
)
from app.domain.models import Asset, MarketDataSeries, MarketDataSource

DSL: dict = {
    "schema_version": "1.0",
    "strategy": {"id": "hash", "name": "Hash", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "ema20"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}


def _series(db_session, symbol: str = "HASH") -> MarketDataSeries:
    asset = Asset(symbol=symbol, asset_class="stock")
    source = MarketDataSource(name=f"src-{symbol}", base_url="x")
    db_session.add_all([asset, source])
    db_session.flush()
    return get_or_create_series(db_session, asset=asset, timeframe="1d", source_id=source.id)


def test_a_sync_stores_the_hash_of_the_bars_it_inserted(db_session, sample_bars: pd.DataFrame):
    series = _series(db_session)

    inserted = upsert_bars(db_session, series, frame_to_bars(sample_bars))

    assert inserted == len(sample_bars)
    stored = load_bars(db_session, series, only_closed=True)
    assert series.content_hash is not None
    assert series.content_hash == series_content_hash(stored)


def test_resyncing_the_same_bars_leaves_the_hash_alone(db_session, sample_bars: pd.DataFrame):
    series = _series(db_session)
    upsert_bars(db_session, series, frame_to_bars(sample_bars))
    first = series.content_hash

    inserted = upsert_bars(db_session, series, frame_to_bars(sample_bars))

    assert inserted == 0
    assert series.content_hash == first


def test_a_new_bar_changes_the_hash(db_session, sample_bars: pd.DataFrame):
    series = _series(db_session)
    upsert_bars(db_session, series, frame_to_bars(sample_bars))
    before = series.content_hash

    extra = sample_bars.iloc[[-1]].copy()
    extra.index = extra.index + pd.Timedelta(days=1)
    upsert_bars(db_session, series, frame_to_bars(extra))

    assert series.content_hash != before
    assert series.content_hash == series_content_hash(
        load_bars(db_session, series, only_closed=True)
    )


def test_a_series_without_bars_has_no_hash_at_all(db_session):
    series = _series(db_session, symbol="EMPTY")

    assert series.content_hash is None
    # ``None`` is not "the hash of an empty frame": the API must not hand out a digest
    # for data that does not exist.
    assert series_content_hash(pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))


def test_the_series_endpoint_returns_the_hash(client, sample_bars: pd.DataFrame) -> None:
    series_id = client.post(
        "/api/v1/market-data/sync",
        json=MarketDataSyncRequest(symbol="DEMO-BTC").model_dump(),
    ).json()["series_id"]

    body = client.get(f"/api/v1/market-data/series/{series_id}").json()

    assert body["content_hash"], "the endpoint used to answer null for every series"


def test_a_backtest_records_the_hash_the_series_carries(client) -> None:
    series_id = client.post(
        "/api/v1/market-data/sync",
        json=MarketDataSyncRequest(symbol="DEMO-BTC").model_dump(),
    ).json()["series_id"]
    strategy = client.post("/api/v1/strategies", json={"name": "Hash BT"}).json()
    version = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json=StrategyVersionCreate(version="1.0.0", dsl=DSL).model_dump(),
    ).json()

    run = client.post(
        "/api/v1/backtests",
        json=BacktestCreate(
            strategy_version_id=version["id"], symbol="DEMO-BTC", timeframe="1d"
        ).model_dump(mode="json"),
    ).json()

    assert run["status"] == "completed"
    assert (
        run["dataset_hash"]
        == client.get(f"/api/v1/market-data/series/{series_id}").json()["content_hash"]
    ), "a reproducible result must name the exact dataset it was computed from"
