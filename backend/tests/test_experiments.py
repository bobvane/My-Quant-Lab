"""Strategy Experiment API tests (docs/25 切片 C, ADR-174).

The experiment is a first-class entity: the POST persists the experiment *and* its result
rows, so a sweep is still readable after the HTTP response is gone, a failure is still a
row, and an experiment points at the ``BacktestRun`` it produced without owning it.

These tests deliberately assert the persisted rows as well as the response body: the
contract is about what is left in the database when the request is over.
"""

from __future__ import annotations

import copy

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import MarketDataSyncRequest
from app.domain.models import BacktestRun, ExperimentResult, StrategyExperiment

_SYMBOL = "DEMO-AAPL"

# Parameterised through `period_ref`, so a sweep has an axis to move and the other kinds
# still run a real strategy.
_DSL = {
    "schema_version": "1.0",
    "strategy": {"id": "exp-api", "name": "Experiment API", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "indicators": [{"id": "trend", "type": "EMA", "period_ref": "trend_period"}],
    "parameters": {"trend_period": 20},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "trend"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "trend"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0, "take_profit_r_multiple": 2.0},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}

# The ADR-055 trap: with 400 daily bars the 500/900-period EMAs never materialise, so those
# points report a flat 0.0 that would beat every genuinely losing point.
_STARVED_GRID = {"trend_period": [100, 300, 500, 900]}


def _seed(client, dsl: dict | None = None) -> int:
    """Sync synthetic data, create a strategy version and return its id."""

    client.post("/api/v1/market-data/sync", json=MarketDataSyncRequest(symbol=_SYMBOL).model_dump())
    strategy = client.post("/api/v1/strategies", json={"name": "Experiments"}).json()
    version = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json={"version": "1.0.0", "dsl": copy.deepcopy(dsl or _DSL)},
    ).json()
    return int(version["id"])


def _completed_run(client, version_id: int) -> int:
    run = client.post(
        "/api/v1/backtests",
        json={"strategy_version_id": version_id, "symbol": _SYMBOL, "timeframe": "1d"},
    ).json()
    return int(run["id"])


def _create(client, version_id: int, **overrides):
    payload = {"name": "experiment", "kind": "backtest", "strategy_version_id": version_id}
    payload.update(overrides)
    return client.post("/api/v1/experiments", json=payload)


def _experiment_rows(db_session) -> list[StrategyExperiment]:
    return list(db_session.scalars(select(StrategyExperiment)).all())


def _fresh_session(db_session) -> Session:
    """A second session on the same database: proves a read comes from the rows, not from
    the response object or the identity map that served the request."""

    return Session(bind=db_session.get_bind())


# --- the five kinds ----------------------------------------------------------------


def test_backtest_kind_persists_a_real_run_and_completes(client, db_session) -> None:
    version_id = _seed(client)

    response = _create(
        client,
        version_id,
        name="baseline",
        kind="backtest",
        symbol=_SYMBOL,
        timeframe="1d",
        parameters={"trend_period": 10},
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["status"] == "completed"
    assert body["kind"] == "backtest"
    assert body["result_count"] == 1
    assert body["error_message"] is None
    assert body["started_at"] and body["completed_at"]
    assert body["parameters"] == {"trend_period": 10}

    result = body["results"][0]
    assert result["kind"] == "backtest"
    assert result["parameters"] == {"trend_period": 10}
    assert result["metrics"]["number_of_trades"] > 0
    run_id = result["backtest_run_id"]
    assert run_id is not None
    # The experiment reuses the same persistence path as POST /backtests.
    run = db_session.get(BacktestRun, run_id)
    assert run is not None and run.status == "completed"
    assert client.get(f"/api/v1/backtests/{run_id}").status_code == 200
    assert body["summary"]["backtest_run_id"] == run_id
    assert body["summary"]["metrics"]["total_return"] == result["metrics"]["total_return"]


def test_sensitivity_kind_keeps_one_row_per_grid_point(client, db_session) -> None:
    version_id = _seed(client)

    response = _create(
        client,
        version_id,
        name="period sweep",
        kind="sensitivity",
        symbol=_SYMBOL,
        timeframe="1d",
        grid={"trend_period": [10, 20, 40]},
        metric="sharpe",
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["status"] == "completed"
    assert body["result_count"] == 3
    assert [row["kind"] for row in body["results"]] == ["sensitivity_point"] * 3
    # The parameter/result pair, per point: this is what makes the sweep re-readable.
    assert [row["parameters"]["trend_period"] for row in body["results"]] == [10, 20, 40]
    assert all(row["metrics"] is not None for row in body["results"])
    assert all(row["payload"]["parameters"]["trend_period"] for row in body["results"])
    assert all(row["backtest_run_id"] is None for row in body["results"])
    assert len({row["payload"]["result_hash"] for row in body["results"]}) == 3
    assert body["results"][0]["label"] == "trend_period=10"

    summary = body["summary"]
    assert summary["grid_points"] == 3
    assert summary["evaluated_points"] == 3
    assert summary["ranked_points"] == 3
    assert summary["warmup_unmet_points"] == 0
    assert summary["warmup_unmet_results"] == []
    assert "mean" in summary["statistics"]
    assert summary["best"]["parameters"]["trend_period"] in (10, 20, 40)
    # The flattened detail metrics are the winning point's stored metrics, not a recompute.
    winner = next(
        row for row in body["results"] if row["parameters"] == summary["best"]["parameters"]
    )
    assert body["metrics"] == winner["metrics"]

    rows = db_session.scalars(select(ExperimentResult)).all()
    assert len(rows) == 3


def test_sensitivity_preserves_warmup_unmet_per_point_and_per_summary(client) -> None:
    version_id = _seed(client)

    response = _create(
        client,
        version_id,
        name="starved sweep",
        kind="sensitivity",
        symbol=_SYMBOL,
        timeframe="1d",
        grid=_STARVED_GRID,
        metric="total_return",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    summary = body["summary"]

    assert body["status"] == "completed"
    assert summary["evaluated_points"] == 4
    assert summary["warmup_unmet_points"] == 2
    assert summary["ranked_points"] == 2
    assert [item["parameters"]["trend_period"] for item in summary["warmup_unmet_results"]] == [
        500,
        900,
    ]

    by_period = {row["parameters"]["trend_period"]: row for row in body["results"]}
    assert set(by_period) == {100, 300, 500, 900}
    assert [by_period[p]["payload"]["warmup_unmet"] for p in (500, 900)] == [True, True]
    assert [by_period[p]["payload"]["warmup_unmet"] for p in (100, 300)] == [False, False]
    # A starved point is flat 0.0 and must not win the ranking.
    for period in (500, 900):
        assert by_period[period]["metrics"]["number_of_trades"] == 0
        assert by_period[period]["payload"]["objective"] == 0.0
    assert summary["best"]["parameters"]["trend_period"] == 100
    assert by_period[100]["metrics"]["total_return"] < 0
    assert body["metrics"] == by_period[100]["metrics"]


def test_monte_carlo_kind_resamples_the_stored_trades(client, db_session) -> None:
    version_id = _seed(client)
    run_id = _completed_run(client, version_id)
    trade_count = len(db_session.get(BacktestRun, run_id).trades)

    response = _create(
        client,
        version_id,
        name="resample",
        kind="monte_carlo",
        backtest_run_id=run_id,
        runs=200,
        seed=7,
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["status"] == "completed"
    assert body["summary"]["backtest_run_id"] == run_id
    result = body["results"][0]
    assert result["kind"] == "monte_carlo"
    assert result["backtest_run_id"] == run_id
    assert result["parameters"] == {"runs": 200, "trades_per_run": None, "seed": 7}
    assert result["payload"]["seed"] == 7
    assert result["payload"]["sample_equity_paths"]
    assert result["metrics"]["observed_trades"] == trade_count
    assert 0.0 <= result["metrics"]["probability_of_profit"] <= 1.0
    # Resampling stored trades is what the run recorded; no second BacktestRun appeared.
    assert len(db_session.scalars(select(BacktestRun)).all()) == 1


def test_walk_forward_kind_completes(client) -> None:
    version_id = _seed(client)

    response = _create(
        client,
        version_id,
        name="rolling",
        kind="walk_forward",
        symbol=_SYMBOL,
        timeframe="1d",
        train_bars=100,
        test_bars=30,
        step=30,
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["status"] == "completed"
    result = body["results"][0]
    assert result["kind"] == "walk_forward"
    assert result["parameters"] == {"train_bars": 100, "test_bars": 30, "step": 30}
    assert result["payload"]["windows"] >= 1
    assert result["payload"]["segments"]
    assert body["summary"]["windows"] == result["payload"]["windows"]


def test_oos_kind_completes(client) -> None:
    version_id = _seed(client)

    response = _create(
        client,
        version_id,
        name="holdout",
        kind="oos",
        symbol=_SYMBOL,
        timeframe="1d",
        oos_pct=0.3,
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["status"] == "completed"
    result = body["results"][0]
    assert result["kind"] == "oos"
    assert result["parameters"] == {"oos_pct": 0.3, "oos_start": None}
    assert result["payload"]["split_time"]
    assert body["summary"]["in_sample_bars"] + body["summary"]["out_of_sample_bars"] > 0
    assert "total_return" in result["metrics"]


# --- persistence: history, read-back, compare, delete ------------------------------


def test_results_are_still_readable_after_the_response_ends(client, db_session) -> None:
    version_id = _seed(client)
    created = _create(
        client,
        version_id,
        name="readable later",
        kind="sensitivity",
        symbol=_SYMBOL,
        grid={"trend_period": [10, 20]},
    ).json()
    experiment_id = created["id"]

    fetched = client.get(f"/api/v1/experiments/{experiment_id}")
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["id"] == experiment_id
    assert body["name"] == "readable later"
    assert body["request"]["kind"] == "sensitivity"
    assert body["request"]["grid"] == {"trend_period": [10, 20]}
    assert len(body["results"]) == 2

    # A separate session reads the rows back out of the database itself.
    with _fresh_session(db_session) as other:
        rows = other.scalars(
            select(ExperimentResult).where(ExperimentResult.experiment_id == experiment_id)
        ).all()
        assert len(rows) == 2
        assert rows[0].parameters_json == {"trend_period": 10}
        assert rows[0].payload_json["result_hash"] == body["results"][0]["payload"]["result_hash"]


def test_history_lists_newest_first_and_filters_by_version(client) -> None:
    version_id = _seed(client)
    first = _create(client, version_id, name="first", kind="oos", symbol=_SYMBOL).json()
    second = _create(client, version_id, name="second", kind="oos", symbol=_SYMBOL).json()

    response = client.get("/api/v1/experiments")
    assert response.status_code == 200, response.text
    listed = response.json()["experiments"]
    assert [row["id"] for row in listed[:2]] == [second["id"], first["id"]]
    assert listed[0]["name"] == "second"
    assert listed[0]["result_count"] == 1
    assert "results" not in listed[0]

    filtered = client.get("/api/v1/experiments", params={"strategy_version_id": version_id}).json()[
        "experiments"
    ]
    assert [row["id"] for row in filtered[:2]] == [second["id"], first["id"]]

    other = client.get("/api/v1/experiments", params={"strategy_version_id": 999_999}).json()
    assert other["experiments"] == []

    limited = client.get("/api/v1/experiments", params={"limit": 1}).json()
    assert [row["id"] for row in limited["experiments"]] == [second["id"]]


def test_compare_projects_the_stored_metrics_of_two_experiments(client) -> None:
    version_id = _seed(client)
    first = _create(client, version_id, name="left", kind="backtest", symbol=_SYMBOL).json()
    second = _create(client, version_id, name="right", kind="backtest", symbol=_SYMBOL).json()

    response = client.get(
        "/api/v1/experiments/compare",
        params=[("ids", str(first["id"])), ("ids", str(second["id"]))],
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["metrics"] == [
        "total_return",
        "max_drawdown",
        "sharpe",
        "win_rate",
        "number_of_trades",
    ]
    assert [row["id"] for row in body["experiments"]] == [first["id"], second["id"]]
    for row, created in zip(body["experiments"], (first, second), strict=True):
        assert row["name"] == created["name"]
        # Pure projection: the compared numbers are the ones already stored.
        for metric in body["metrics"]:
            assert row[metric] == created["metrics"][metric]

    comma = client.get(
        "/api/v1/experiments/compare",
        params={"ids": f"{first['id']},{second['id']}"},
    )
    assert comma.status_code == 200, comma.text
    assert comma.json() == body


def test_compare_rejects_a_single_id(client) -> None:
    version_id = _seed(client)
    created = _create(client, version_id, name="lonely", kind="oos", symbol=_SYMBOL).json()
    response = client.get("/api/v1/experiments/compare", params={"ids": str(created["id"])})
    assert response.status_code == 422, response.text
    assert "at least two" in response.json()["detail"]


def test_delete_cascades_results_but_keeps_the_backtest_run(client, db_session) -> None:
    version_id = _seed(client)
    created = _create(client, version_id, name="doomed", kind="backtest", symbol=_SYMBOL).json()
    experiment_id = created["id"]
    run_id = created["results"][0]["backtest_run_id"]

    assert client.delete(f"/api/v1/experiments/{experiment_id}").status_code == 204

    assert client.get(f"/api/v1/experiments/{experiment_id}").status_code == 404
    assert db_session.get(StrategyExperiment, experiment_id) is None
    assert (
        db_session.scalars(
            select(ExperimentResult).where(ExperimentResult.experiment_id == experiment_id)
        ).all()
        == []
    )
    # The backtest is its own artifact: deleting the experiment must not delete it.
    assert db_session.get(BacktestRun, run_id) is not None
    assert client.get(f"/api/v1/backtests/{run_id}").status_code == 200


def test_missing_experiment_is_404(client) -> None:
    assert client.get("/api/v1/experiments/4242").status_code == 404
    assert client.delete("/api/v1/experiments/4242").status_code == 404


# --- rejected requests write nothing ----------------------------------------------


def test_unknown_field_is_rejected_and_writes_nothing(client, db_session) -> None:
    version_id = _seed(client)
    response = _create(client, version_id, symbol=_SYMBOL, runs_count=5)
    assert response.status_code == 422, response.text
    assert _experiment_rows(db_session) == []


def test_unknown_strategy_version_is_404_and_writes_nothing(client, db_session) -> None:
    _seed(client)
    response = _create(client, 999_999, kind="oos", symbol=_SYMBOL)
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "strategy version not found"
    assert _experiment_rows(db_session) == []


def test_unresolvable_symbol_is_404_and_writes_nothing(client, db_session) -> None:
    version_id = _seed(client)
    response = _create(client, version_id, kind="oos", symbol="DEMO-NOPE")
    assert response.status_code == 404, response.text
    assert _experiment_rows(db_session) == []


def test_sensitivity_without_a_grid_is_422_and_writes_nothing(client, db_session) -> None:
    version_id = _seed(client)
    response = _create(client, version_id, kind="sensitivity", symbol=_SYMBOL)
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "sensitivity experiments require a non-empty grid"
    assert _experiment_rows(db_session) == []


def test_sensitivity_with_an_unknown_axis_is_422_and_writes_nothing(client, db_session) -> None:
    version_id = _seed(client)
    response = _create(
        client,
        version_id,
        kind="sensitivity",
        symbol=_SYMBOL,
        grid={"not_a_parameter": [1, 2]},
    )
    assert response.status_code == 422, response.text
    assert "not declared by the strategy" in response.json()["detail"]
    # The experiment row was flushed before the engine ran; the rollback must drop it.
    assert _experiment_rows(db_session) == []


def test_monte_carlo_without_a_run_id_is_422_and_writes_nothing(client, db_session) -> None:
    version_id = _seed(client)
    response = _create(client, version_id, kind="monte_carlo")
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "monte_carlo experiments require backtest_run_id"
    assert _experiment_rows(db_session) == []


def test_monte_carlo_with_an_unknown_run_is_404_and_writes_nothing(client, db_session) -> None:
    version_id = _seed(client)
    response = _create(client, version_id, kind="monte_carlo", backtest_run_id=999_999)
    assert response.status_code == 404, response.text
    assert _experiment_rows(db_session) == []


def test_walk_forward_with_too_few_bars_is_422_and_writes_nothing(client, db_session) -> None:
    version_id = _seed(client)
    response = _create(
        client,
        version_id,
        kind="walk_forward",
        symbol=_SYMBOL,
        train_bars=5_000,
        test_bars=1_000,
    )
    assert response.status_code == 422, response.text
    assert "need at least" in response.json()["detail"]
    assert _experiment_rows(db_session) == []


# --- a failing engine is a persisted fact, not a missing experiment ---------------


def test_engine_failure_is_persisted_as_failed(client, db_session, monkeypatch) -> None:
    version_id = _seed(client)

    def boom(*args, **kwargs):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr("app.api.routers.experiments.run_walk_forward", boom)

    response = _create(client, version_id, name="broken", kind="walk_forward", symbol=_SYMBOL)
    # The created row is the deliverable of the POST: 201, with the failure on it.
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_message"] == "engine exploded"
    assert body["results"] == []
    assert body["result_count"] == 0
    assert body["completed_at"] is not None
    assert body["summary"]["metrics"]["total_return"] is None

    stored = db_session.get(StrategyExperiment, body["id"])
    assert stored.status == "failed"
    assert stored.error_message == "engine exploded"
    assert _experiment_rows(db_session)[-1].status == "failed"
    # Readable afterwards, too.
    assert client.get(f"/api/v1/experiments/{body['id']}").json()["status"] == "failed"


def test_backtest_engine_failure_keeps_the_failed_run(client, db_session, monkeypatch) -> None:
    version_id = _seed(client)

    def boom(*args, **kwargs):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr("app.data.backtest_service.run_backtest", boom)

    response = _create(client, version_id, name="broken run", kind="backtest", symbol=_SYMBOL)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert "engine exploded" in body["error_message"]
    assert body["results"] == []

    runs = db_session.scalars(select(BacktestRun)).all()
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert "engine exploded" in runs[0].error_message


def test_experiments_record_audit_events(client, db_session) -> None:
    from app.domain.models import AuditLog

    version_id = _seed(client)
    created = _create(client, version_id, name="audited", kind="oos", symbol=_SYMBOL).json()
    client.delete(f"/api/v1/experiments/{created['id']}")

    events = {
        row.event_type
        for row in db_session.scalars(
            select(AuditLog).where(AuditLog.entity_type == "strategy_experiment")
        ).all()
    }
    assert events == {"experiment_completed", "experiment_deleted"}
