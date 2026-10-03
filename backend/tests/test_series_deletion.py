"""Deleting a market series must be honest about what it leaves behind (ADR-081).

A completed backtest points at the exact dataset it was computed from
(``backtest_runs.dataset_version_id``), and that pointer *is* the reproducibility
evidence: deleting the series under it would turn a reproducible result into a
number nobody can re-derive. PostgreSQL therefore refuses the delete — correctly —
but the endpoint turned that refusal into an opaque ``500 internal server error``,
so the UI could only say "删除不掉". The SQLite test database never noticed either
way, because SQLite ignores foreign keys unless they are switched on.

These tests pin the contract that replaces the 500:

* nobody uses the series  -> it is really deleted (bars and feature snapshots too)
* a backtest used it      -> it is archived (hidden from 行情同步, data kept, the
                            run keeps pointing at a series that still exists)
* the caller insists      -> ``?purge=true`` refuses with 409 and a reason that
                            names the runs (same convention as delete_strategy)
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.domain.models import (
    Asset,
    AuditLog,
    BacktestRun,
    FeatureSnapshot,
    MarketDataBar,
    MarketDataSeries,
    MarketDataSource,
    Strategy,
    StrategyVersion,
)

PREFIX = "/api/v1/market-data"


def _bar(series_id: int, day: int) -> MarketDataBar:
    return MarketDataBar(
        series_id=series_id,
        timestamp=dt.datetime(2024, 1, 2, tzinfo=dt.UTC) + dt.timedelta(days=day),
        open=100,
        high=101,
        low=99,
        close=100,
        volume=1_000,
        source_hash="h" * 64,
    )


def _seed(
    db_session,
    *,
    symbol: str = "DEMO-AAPL",
    source_name: str = "synthetic",
    runs: int = 0,
    bars: int = 3,
    snapshots: int = 0,
) -> dict:
    """One asset/series, with optional bars, feature snapshots and backtest runs."""

    asset = Asset(symbol=symbol, asset_class="stock")
    source = MarketDataSource(name=source_name, base_url="local")
    db_session.add_all([asset, source])
    db_session.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db_session.add(series)
    db_session.flush()
    for day in range(bars):
        db_session.add(_bar(series.id, day))
    for day in range(snapshots):
        db_session.add(
            FeatureSnapshot(
                series_id=series.id,
                bar_timestamp=dt.datetime(2024, 1, 2, tzinfo=dt.UTC) + dt.timedelta(days=day),
                feature_version="1.0.0",
                values_json={"close": 100.0},
                input_hash="i" * 64,
                available_at=dt.datetime(2024, 1, 2, tzinfo=dt.UTC) + dt.timedelta(days=day),
            )
        )
    run_ids: list[int] = []
    if runs:
        strategy = Strategy(name=f"Seeded {symbol}", slug=f"seeded-{symbol.lower()}")
        db_session.add(strategy)
        db_session.flush()
        version = StrategyVersion(
            strategy_id=strategy.id, version="1.0.0", dsl_json={}, immutable_hash="c" * 64
        )
        db_session.add(version)
        db_session.flush()
        for _ in range(runs):
            run = BacktestRun(
                strategy_version_id=version.id,
                dataset_version_id=series.id,
                dataset_hash="d" * 64,
                status="completed",
            )
            db_session.add(run)
            db_session.flush()
            run_ids.append(run.id)
    db_session.commit()
    return {"series_id": series.id, "asset_id": asset.id, "run_ids": run_ids}


def _count(db_session, model, **where) -> int:
    conditions = [getattr(model, key) == value for key, value in where.items()]
    return len(db_session.scalars(select(model).where(*conditions)).all())


def test_a_series_nobody_uses_is_really_deleted(client, db_session) -> None:
    seeded = _seed(db_session, bars=3, snapshots=2)
    series_id = seeded["series_id"]

    response = client.delete(f"{PREFIX}/series/{series_id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == series_id
    assert body["symbol"] == "DEMO-AAPL"
    assert body["archived"] is False
    assert body["blocking_runs"] == 0
    assert db_session.get(MarketDataSeries, series_id) is None
    # Bars and feature snapshots must not survive their series.
    assert _count(db_session, MarketDataBar, series_id=series_id) == 0
    assert _count(db_session, FeatureSnapshot, series_id=series_id) == 0


def test_a_series_a_backtest_used_is_archived_not_orphaned(client, db_session) -> None:
    seeded = _seed(db_session, runs=2)
    series_id = seeded["series_id"]

    response = client.delete(f"{PREFIX}/series/{series_id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["archived"] is True, "a series a run still points at must not be hard deleted"
    assert body["blocking_runs"] == 2
    assert "回测" in body["message"]

    series = db_session.get(MarketDataSeries, series_id)
    assert series is not None, "the evidence must survive: the run still points here"
    assert series.is_archived is True
    # The bars stay too: a reproducible run needs them.
    assert _count(db_session, MarketDataBar, series_id=series_id) == 3

    run = db_session.get(BacktestRun, seeded["run_ids"][0])
    assert run is not None
    assert db_session.get(MarketDataSeries, run.dataset_version_id) is not None


def test_deleting_an_archived_series_twice_stays_calm(client, db_session) -> None:
    seeded = _seed(db_session, runs=1)
    series_id = seeded["series_id"]

    first = client.delete(f"{PREFIX}/series/{series_id}")
    second = client.delete(f"{PREFIX}/series/{series_id}")

    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["archived"] is True
    assert db_session.get(MarketDataSeries, series_id) is not None


def test_an_archived_series_disappears_from_the_sync_list(client, db_session) -> None:
    archived = _seed(db_session, symbol="DEMO-AAPL", runs=1)
    active = _seed(db_session, symbol="AAPL", source_name="synthetic-two", bars=1)

    assert client.delete(f"{PREFIX}/series/{archived['series_id']}").status_code == 200

    listed = client.get(f"{PREFIX}/series").json()
    ids = {row["id"] for row in listed}
    assert archived["series_id"] not in ids, "an archived series is hidden by default"
    assert active["series_id"] in ids

    everything = client.get(f"{PREFIX}/series?include_archived=true").json()
    by_id = {row["id"]: row for row in everything}
    assert archived["series_id"] in by_id
    assert by_id[archived["series_id"]]["is_archived"] is True
    assert by_id[active["series_id"]]["is_archived"] is False


def test_one_series_reports_its_archive_state(client, db_session) -> None:
    seeded = _seed(db_session, runs=1)
    series_id = seeded["series_id"]

    assert client.get(f"{PREFIX}/series/{series_id}").json()["is_archived"] is False
    client.delete(f"{PREFIX}/series/{series_id}")
    assert client.get(f"{PREFIX}/series/{series_id}").json()["is_archived"] is True


def test_purging_a_used_series_is_refused_with_a_reason(client, db_session) -> None:
    seeded = _seed(db_session, runs=3)
    series_id = seeded["series_id"]

    response = client.delete(f"{PREFIX}/series/{series_id}?purge=true")

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "3" in detail, "the refusal must name how many runs depend on it"
    assert "回测" in detail
    assert db_session.get(MarketDataSeries, series_id) is not None
    assert _count(db_session, BacktestRun) == 3


def test_purging_a_free_series_still_works(client, db_session) -> None:
    seeded = _seed(db_session, bars=2)
    series_id = seeded["series_id"]

    response = client.delete(f"{PREFIX}/series/{series_id}?purge=true")

    assert response.status_code == 200, response.text
    assert response.json()["archived"] is False
    assert db_session.get(MarketDataSeries, series_id) is None


def test_an_unknown_series_is_a_404(client, db_session) -> None:
    assert client.delete(f"{PREFIX}/series/424242").status_code == 404
    assert client.delete(f"{PREFIX}/series/424242?purge=true").status_code == 404


def test_an_archived_series_can_be_restored(client, db_session) -> None:
    seeded = _seed(db_session, runs=1)
    series_id = seeded["series_id"]
    client.delete(f"{PREFIX}/series/{series_id}")

    response = client.post(f"{PREFIX}/series/{series_id}/restore")

    assert response.status_code == 200, response.text
    assert response.json()["is_archived"] is False
    assert db_session.get(MarketDataSeries, series_id).is_archived is False
    assert series_id in {row["id"] for row in client.get(f"{PREFIX}/series").json()}
    assert client.post(f"{PREFIX}/series/424242/restore").status_code == 404


def test_archive_and_delete_write_an_audit_trail(client, db_session) -> None:
    archived = _seed(db_session, runs=1)
    client.delete(f"{PREFIX}/series/{archived['series_id']}")
    free = _seed(db_session, symbol="AAPL", source_name="synthetic-two", bars=1)
    client.delete(f"{PREFIX}/series/{free['series_id']}")

    events = {
        row.event_type: row
        for row in db_session.scalars(
            select(AuditLog).where(AuditLog.entity_type == "market_data_series")
        ).all()
    }
    assert "market_data_series_archived" in events
    assert "market_data_series_deleted" in events
    assert events["market_data_series_archived"].action == "archive"
    assert events["market_data_series_archived"].entity_id == str(archived["series_id"])


def test_the_test_database_enforces_foreign_keys(db_session) -> None:
    """Guard for the reason the 500 reached production: SQLite is lenient by default."""

    assert db_session.execute(text("PRAGMA foreign_keys")).scalar() == 1
    seeded = _seed(db_session, runs=1)
    db_session.delete(db_session.get(MarketDataSeries, seeded["series_id"]))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
