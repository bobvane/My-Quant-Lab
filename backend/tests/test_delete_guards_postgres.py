"""B1-T11: the delete guard on PostgreSQL, where the database really would have said no.

The SQLite half of the matrix (``test_delete_guards.py``) proves the guard's logic. It
cannot prove that the rows the guard refuses to touch would otherwise have been
protected: SQLite only enforces foreign keys when ``PRAGMA foreign_keys`` is on, so a
missing guard there can leave a dangling reference where PostgreSQL would answer with
an ``IntegrityError`` -- the 500 the B1 batch exists to remove.

This module therefore runs against PostgreSQL, and it refuses to fall back quietly:

* ``TEST_POSTGRES_URL`` unset -> ``skip`` with a message saying the evidence is missing
  (never a pass, and the CI step treats a skip as a failure of that step);
* the variable set but the session not on PostgreSQL -> **failure**, because a green run
  there would be the "asked for PostgreSQL, proved nothing" case.

Everything here is about the difference the dialect makes: the references are built with
real rows, the guard is asked to delete, and the assertions check the row counts rather
than only the HTTP status.
"""

from __future__ import annotations

import datetime as dt
import os

import pytest
from sqlalchemy import select

from app.api.schemas import StrategyVersionCreate
from app.domain.models import (
    Asset,
    BacktestRun,
    ExperimentResult,
    MarketDataBar,
    MarketDataSeries,
    MarketDataSource,
    PaperAccount,
    Strategy,
    StrategyExperiment,
    StrategyVersion,
)

pytestmark = pytest.mark.postgres

DSL: dict = {
    "schema_version": "1.0",
    "strategy": {"id": "delete-guards-pg", "name": "Delete Guards PG", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "ema20"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0, "take_profit_r_multiple": 2.0},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}

DAY = dt.datetime(2024, 1, 2, tzinfo=dt.UTC)


def _require_postgres(db_session):
    """The unconditional dialect check that makes a silent SQLite run impossible."""

    if not os.environ.get("TEST_POSTGRES_URL"):
        pytest.skip(
            "B1-T11: TEST_POSTGRES_URL is not set, so the PostgreSQL half of the "
            "delete-guard matrix did NOT run and nothing about PostgreSQL is proven here"
        )
    dialect = db_session.get_bind().dialect.name
    assert dialect == "postgresql", (
        f"B1-T11 asked for PostgreSQL but this session is on {dialect!r}: the connection "
        "did not happen, and a passing run here would prove nothing"
    )
    return db_session


def _strategy_with_version(client, name: str) -> tuple[int, int]:
    strategy = client.post("/api/v1/strategies", json={"name": name}).json()
    version = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json=StrategyVersionCreate(version="1.0.0", dsl=DSL).model_dump(),
    ).json()
    return strategy["id"], version["id"]


def _series(db_session, *, symbol: str) -> int:
    asset = Asset(symbol=symbol, asset_class="stock")
    source = MarketDataSource(name="synthetic", base_url="local")
    db_session.add_all([asset, source])
    db_session.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db_session.add(series)
    db_session.flush()
    db_session.add(
        MarketDataBar(
            series_id=series.id,
            timestamp=DAY,
            open=100,
            high=101,
            low=99,
            close=100,
            volume=1_000,
            source_hash="h" * 64,
        )
    )
    db_session.flush()
    return series.id


def _run(db_session, *, version_id: int, series_id: int) -> BacktestRun:
    run = BacktestRun(
        strategy_version_id=version_id,
        dataset_version_id=series_id,
        dataset_hash="d" * 64,
        status="completed",
    )
    db_session.add(run)
    db_session.flush()
    return run


def _experiment(db_session, *, version_id: int, series_id: int | None = None) -> StrategyExperiment:
    experiment = StrategyExperiment(
        name="PG guarded experiment",
        kind="sensitivity",
        strategy_version_id=version_id,
        series_id=series_id,
    )
    db_session.add(experiment)
    db_session.flush()
    return experiment


def _count(db_session, model, **where) -> int:
    conditions = [getattr(model, key) == value for key, value in where.items()]
    return len(db_session.scalars(select(model).where(*conditions)).all())


def test_a_version_only_account_reference_is_refused_on_postgres(client, db_session) -> None:
    """The referrer the old count missed -- and the case that used to be a 500."""

    _require_postgres(db_session)
    strategy_id, version_id = _strategy_with_version(client, "PG T15")
    db_session.add(
        PaperAccount(
            name="PG version-bound account",
            initial_cash=10_000,
            cash=10_000,
            strategy_version_id=version_id,
        )
    )
    db_session.commit()

    response = client.delete(f"/api/v1/strategies/{strategy_id}")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "delete_blocked"
    assert response.json()["error"]["details"]["blockers"] == [
        {
            "table": "paper_accounts",
            "kind": "paper_account",
            "label": "模拟盘账户",
            "count": 1,
        }
    ]
    db_session.expire_all()
    assert db_session.get(Strategy, strategy_id) is not None
    assert db_session.get(StrategyVersion, version_id) is not None


def test_a_series_an_experiment_used_is_refused_on_postgres(client, db_session) -> None:
    """``strategy_experiments.series_id`` used to be invisible to the series guard."""

    _require_postgres(db_session)
    _, version_id = _strategy_with_version(client, "PG T5")
    series_id = _series(db_session, symbol="PG-T5-AAPL")
    _experiment(db_session, version_id=version_id, series_id=series_id)
    db_session.commit()

    response = client.delete(f"/api/v1/market-data/series/{series_id}")

    assert response.status_code == 409, response.text
    assert [item["kind"] for item in response.json()["error"]["details"]["blockers"]] == [
        "experiment"
    ]
    db_session.expire_all()
    assert _count(db_session, MarketDataBar, series_id=series_id) == 1
    assert db_session.get(MarketDataSeries, series_id) is not None


def test_a_run_an_experiment_result_points_at_is_refused_on_postgres(client, db_session) -> None:
    """``experiment_results.backtest_run_id`` has no ``ondelete``: the database refuses,
    so the guard has to refuse first and say why."""

    _require_postgres(db_session)
    _, version_id = _strategy_with_version(client, "PG T6")
    series_id = _series(db_session, symbol="PG-T6-AAPL")
    run = _run(db_session, version_id=version_id, series_id=series_id)
    experiment = _experiment(db_session, version_id=version_id)
    db_session.add(
        ExperimentResult(
            experiment_id=experiment.id,
            kind="point",
            backtest_run_id=run.id,
            payload_json={"parameters": {}},
        )
    )
    db_session.commit()

    response = client.delete(f"/api/v1/backtests/{run.id}")

    assert response.status_code == 409, response.text
    assert [item["table"] for item in response.json()["error"]["details"]["blockers"]] == [
        "experiment_results"
    ]
    db_session.expire_all()
    assert db_session.get(BacktestRun, run.id) is not None


def test_a_bound_paper_account_does_not_stop_the_delete_on_postgres(client, db_session) -> None:
    """``paper_accounts.backtest_run_id`` is ``ON DELETE SET NULL``, so PostgreSQL itself
    would not refuse this delete.  The endpoint also clears the binding on its own
    (ADR-222, for SQLite where the foreign-key pragma is off), so what this asserts is
    the end state on the dialect that does enforce foreign keys: the account survives,
    the column really reads back as NULL, and the run is gone. Read back through the
    session after ``expire_all`` so the value comes from the database, not the ORM."""

    _require_postgres(db_session)
    _, version_id = _strategy_with_version(client, "PG T8")
    series_id = _series(db_session, symbol="PG-T8-AAPL")
    run = _run(db_session, version_id=version_id, series_id=series_id)
    account = PaperAccount(
        name="PG bound account",
        initial_cash=10_000,
        cash=10_000,
        backtest_run_id=run.id,
    )
    db_session.add(account)
    db_session.commit()

    response = client.delete(f"/api/v1/backtests/{run.id}")

    assert response.status_code == 200, response.text
    assert response.json()["warnings"], "clearing the binding must be announced"
    db_session.expire_all()
    survivor = db_session.get(PaperAccount, account.id)
    assert survivor is not None
    assert survivor.backtest_run_id is None
    assert _count(db_session, BacktestRun, id=run.id) == 0


def test_a_refusal_deletes_nothing_on_postgres(client, db_session) -> None:
    """No partial delete: the counts are compared around a refusal that names three
    different kinds of referrer at once."""

    _require_postgres(db_session)
    strategy_id, version_id = _strategy_with_version(client, "PG T13")
    series_id = _series(db_session, symbol="PG-T13-AAPL")
    _run(db_session, version_id=version_id, series_id=series_id)
    _experiment(db_session, version_id=version_id)
    db_session.add(
        PaperAccount(
            name="PG T13 account",
            initial_cash=10_000,
            cash=10_000,
            strategy_version_id=version_id,
        )
    )
    db_session.commit()

    before = (
        _count(db_session, Strategy),
        _count(db_session, StrategyVersion),
        _count(db_session, BacktestRun),
        _count(db_session, StrategyExperiment),
        _count(db_session, PaperAccount),
    )
    response = client.delete(f"/api/v1/strategies/{strategy_id}")
    assert response.status_code == 409, response.text
    db_session.expire_all()
    after = (
        _count(db_session, Strategy),
        _count(db_session, StrategyVersion),
        _count(db_session, BacktestRun),
        _count(db_session, StrategyExperiment),
        _count(db_session, PaperAccount),
    )
    assert after == before
