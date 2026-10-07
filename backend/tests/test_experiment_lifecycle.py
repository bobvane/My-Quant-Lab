"""Experiment lifecycle tests: draft, run, archive, rename, and frozen snapshots.

ADR-182 split "describe an experiment" from "execute it": a draft stores the validated
request and runs nothing, ``POST /experiments/{id}/run`` executes exactly that stored
request, and the lifecycle refuses transitions that would destroy evidence (a completed
experiment is never re-run, a running one is never archived). ADR-183 froze the run's
configuration into the experiment row.

These tests assert the persisted rows as well as the response bodies, because the contract
is about what is left in the database -- and about what is *not* left there after a
rejection.
"""

from __future__ import annotations

import copy
from decimal import Decimal

from sqlalchemy import select

from app.api.schemas import MarketDataSyncRequest
from app.domain.models import (
    BacktestRun,
    BacktestTrade,
    ExperimentResult,
    StrategyExperiment,
    StrategyVersion,
)

_SYMBOL = "DEMO-AAPL"

# `trend_period` is the sweep axis, so the same DSL supports every kind the lifecycle
# tests exercise.
_DSL = {
    "schema_version": "1.0",
    "strategy": {"id": "exp-lifecycle", "name": "Experiment Lifecycle", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "indicators": [{"id": "trend", "type": "EMA", "period_ref": "trend_period"}],
    "parameters": {"trend_period": 20},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "trend"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "trend"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0, "take_profit_r_multiple": 2.0},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}


def _seed(client, dsl: dict | None = None) -> int:
    """Sync synthetic data, create a strategy version and return its id."""

    client.post("/api/v1/market-data/sync", json=MarketDataSyncRequest(symbol=_SYMBOL).model_dump())
    strategy = client.post("/api/v1/strategies", json={"name": "Experiments"}).json()
    version = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json={"version": "1.0.0", "dsl": copy.deepcopy(dsl or _DSL)},
    ).json()
    return int(version["id"])


def _create(client, version_id: int, **overrides):
    payload = {"name": "experiment", "kind": "backtest", "strategy_version_id": version_id}
    payload.update(overrides)
    return client.post("/api/v1/experiments", json=payload)


def _results(db_session, experiment_id: int) -> list[ExperimentResult]:
    return list(
        db_session.scalars(
            select(ExperimentResult)
            .where(ExperimentResult.experiment_id == experiment_id)
            .order_by(ExperimentResult.id)
        ).all()
    )


# --- drafts run nothing -----------------------------------------------------------


def test_a_draft_freezes_the_request_and_runs_nothing(client, db_session) -> None:
    version_id = _seed(client)

    response = _create(
        client,
        version_id,
        name="later",
        kind="backtest",
        symbol=_SYMBOL,
        parameters={"trend_period": 10},
        draft=True,
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["status"] == "draft"
    assert body["results"] == []
    assert body["result_count"] == 0
    assert body["started_at"] is None
    assert body["completed_at"] is None
    assert body["error_message"] is None
    # The request is stored exactly as it was validated, ready to be replayed.
    assert body["parameters"] == {"trend_period": 10}
    assert body["request"]["draft"] is True
    assert body["request"]["parameters"] == {"trend_period": 10}
    # ADR-183: the configuration is frozen at creation time.
    assert body["initial_capital"] == 10_000.0
    assert body["updated_at"] is not None

    # Nothing ran: no result row and no backtest run.
    assert _results(db_session, body["id"]) == []
    assert db_session.scalars(select(BacktestRun)).all() == []
    stored = db_session.get(StrategyExperiment, body["id"])
    assert stored.status == "draft"
    assert stored.started_at is None
    assert stored.summary_json is None


def test_the_history_filters_drafts_by_status_and_kind(client) -> None:
    version_id = _seed(client)
    draft = _create(client, version_id, name="draft", kind="backtest", symbol=_SYMBOL, draft=True)
    finished = _create(client, version_id, name="finished", kind="oos", symbol=_SYMBOL).json()
    assert draft.status_code == 201, draft.text

    drafts = client.get("/api/v1/experiments", params={"status": "draft"})
    assert drafts.status_code == 200, drafts.text
    assert [row["id"] for row in drafts.json()["experiments"]] == [draft.json()["id"]]
    assert drafts.json()["experiments"][0]["status"] == "draft"

    completed = client.get("/api/v1/experiments", params={"status": "completed"})
    assert [row["id"] for row in completed.json()["experiments"]] == [finished["id"]]

    by_kind = client.get("/api/v1/experiments", params={"kind": "oos"})
    assert [row["id"] for row in by_kind.json()["experiments"]] == [finished["id"]]

    both = client.get("/api/v1/experiments", params={"status": "draft", "kind": "oos"}).json()[
        "experiments"
    ]
    assert both == []


def test_the_history_rejects_an_unknown_status_filter(client) -> None:
    _seed(client)
    response = client.get("/api/v1/experiments", params={"status": "finished"})
    assert response.status_code == 422, response.text


# --- running a draft --------------------------------------------------------------


def test_running_a_draft_runs_the_same_implementation_as_the_synchronous_post(
    client, db_session
) -> None:
    version_id = _seed(client)
    payload = {
        "name": "same",
        "kind": "backtest",
        "symbol": _SYMBOL,
        "parameters": {"trend_period": 10},
    }
    direct = _create(client, version_id, **payload)
    assert direct.status_code == 201, direct.text
    draft = _create(client, version_id, draft=True, **payload)
    assert draft.status_code == 201, draft.text

    response = client.post(f"/api/v1/experiments/{draft.json()['id']}/run")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "completed"
    assert body["error_message"] is None
    assert body["result_count"] == 1
    assert body["started_at"] is not None
    assert body["completed_at"] is not None
    assert body["results"][0]["backtest_run_id"] is not None
    # Same request, same code path: the numbers must be identical, not merely both valid.
    assert body["results"][0]["metrics"] == direct.json()["results"][0]["metrics"]
    assert body["metrics"] == direct.json()["metrics"]

    rows = _results(db_session, body["id"])
    assert len(rows) == 1
    assert rows[0].metrics_json == direct.json()["results"][0]["metrics"]
    assert rows[0].backtest_run_id == body["results"][0]["backtest_run_id"]


def test_running_a_finished_experiment_is_a_conflict(client, db_session) -> None:
    version_id = _seed(client)
    created = _create(client, version_id, name="done", kind="backtest", symbol=_SYMBOL).json()

    response = client.post(f"/api/v1/experiments/{created['id']}/run")
    assert response.status_code == 409, response.text
    assert "only a draft or failed experiment can be run" in response.json()["detail"]

    # The stored result is the point of the refusal: it must still be there, unchanged.
    stored = db_session.get(StrategyExperiment, created["id"])
    assert stored.status == "completed"
    assert stored.completed_at is not None
    assert len(_results(db_session, created["id"])) == 1
    assert (
        client.get(f"/api/v1/experiments/{created['id']}").json()["metrics"] == created["metrics"]
    )


def test_running_a_draft_that_can_no_longer_be_resolved_leaves_it_untouched(
    client, db_session
) -> None:
    version_id = _seed(client)
    draft = _create(
        client, version_id, name="orphan", kind="backtest", symbol=_SYMBOL, draft=True
    ).json()

    # The version is invalidated after the draft was stored. Validation happens before the
    # experiment is touched, so the row keeps saying "draft" instead of "failed".
    version = db_session.get(StrategyVersion, version_id)
    version.validation_status = "invalid"
    db_session.commit()

    response = client.post(f"/api/v1/experiments/{draft['id']}/run")
    assert response.status_code == 422, response.text
    assert "not 'valid'" in response.json()["detail"]

    stored = db_session.get(StrategyExperiment, draft["id"])
    assert stored.status == "draft"
    assert stored.started_at is None
    assert stored.completed_at is None
    assert stored.error_message is None
    assert _results(db_session, draft["id"]) == []


# --- a failure is a stored fact ---------------------------------------------------


def test_a_run_that_fails_stores_the_engine_reason_verbatim_and_can_be_retried(
    client, db_session, monkeypatch
) -> None:
    version_id = _seed(client)
    draft = _create(
        client, version_id, name="broken", kind="walk_forward", symbol=_SYMBOL, draft=True
    ).json()

    def boom(*args, **kwargs):
        raise RuntimeError("engine exploded")

    with monkeypatch.context() as patched:
        patched.setattr("app.data.experiment_service.run_walk_forward", boom)
        failed = client.post(f"/api/v1/experiments/{draft['id']}/run")

    assert failed.status_code == 200, failed.text
    body = failed.json()
    assert body["status"] == "failed"
    # Verbatim: the engine's own wording, not a rewritten summary of it.
    assert body["error_message"] == "engine exploded"
    assert body["results"] == []
    assert body["result_count"] == 0
    assert body["completed_at"] is not None
    assert _results(db_session, draft["id"]) == []

    # The row outlives the failure.
    fetched = client.get(f"/api/v1/experiments/{draft['id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["status"] == "failed"
    assert fetched.json()["error_message"] == "engine exploded"

    # And a failed experiment is runnable again: that is what the failure is for.
    retried = client.post(f"/api/v1/experiments/{draft['id']}/run")
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "completed"
    assert retried.json()["error_message"] is None
    assert retried.json()["result_count"] == 1
    assert len(_results(db_session, draft["id"])) == 1


# --- archiving --------------------------------------------------------------------


def test_archiving_retires_the_experiment_but_keeps_it_listed(client, db_session) -> None:
    version_id = _seed(client)
    created = _create(client, version_id, name="retire", kind="backtest", symbol=_SYMBOL).json()

    response = client.post(f"/api/v1/experiments/{created['id']}/archive")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "archived"
    assert body["archived_at"] is not None
    # Archival is not deletion: the results are still there.
    assert len(body["results"]) == 1
    assert client.get(f"/api/v1/experiments/{created['id']}").status_code == 200

    again = client.post(f"/api/v1/experiments/{created['id']}/archive")
    assert again.status_code == 409, again.text
    assert "already archived" in again.json()["detail"]

    # The default history is the record, so it still contains the archived experiment.
    listed = client.get("/api/v1/experiments").json()["experiments"]
    assert created["id"] in [row["id"] for row in listed]

    archived = client.get("/api/v1/experiments", params={"status": "archived"}).json()[
        "experiments"
    ]
    assert [row["id"] for row in archived] == [created["id"]]
    assert archived[0]["archived_at"] is not None


def test_archiving_a_running_experiment_is_a_conflict(client, db_session) -> None:
    version_id = _seed(client)
    created = _create(client, version_id, name="busy", kind="backtest", symbol=_SYMBOL).json()

    # A synchronously executed experiment is never observed mid-flight; the lifecycle guard
    # is what this test is about, so the row is put into that state directly.
    stored = db_session.get(StrategyExperiment, created["id"])
    stored.status = "running"
    db_session.commit()

    response = client.post(f"/api/v1/experiments/{created['id']}/archive")
    assert response.status_code == 409, response.text
    assert "still running" in response.json()["detail"]
    assert db_session.get(StrategyExperiment, created["id"]).archived_at is None


# --- renaming ---------------------------------------------------------------------


def test_patch_requires_a_field_and_leaves_the_result_alone(client, db_session) -> None:
    version_id = _seed(client)
    created = _create(
        client, version_id, name="before", kind="backtest", symbol=_SYMBOL, notes="first"
    ).json()
    url = f"/api/v1/experiments/{created['id']}"

    assert client.patch(url, json={}).status_code == 422
    assert client.patch(url, json={"name": ""}).status_code == 422
    # Only the two editable fields exist; anything else is a rejected request.
    assert client.patch(url, json={"status": "archived"}).status_code == 422

    renamed = client.patch(url, json={"name": "after"})
    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert body["name"] == "after"
    assert body["notes"] == "first"
    assert body["parameters"] == created["parameters"]
    assert body["results"][0]["metrics"] == created["results"][0]["metrics"]

    stored = db_session.get(StrategyExperiment, created["id"])
    assert stored.name == "after"
    assert stored.updated_at is not None
    assert stored.updated_at >= stored.created_at

    noted = client.patch(url, json={"notes": "because"})
    assert noted.status_code == 200, noted.text
    assert noted.json()["notes"] == "because"
    assert noted.json()["name"] == "after"


def test_patching_a_missing_experiment_is_404(client) -> None:
    _seed(client)
    assert client.patch("/api/v1/experiments/4242", json={"name": "x"}).status_code == 404
    assert client.post("/api/v1/experiments/4242/run").status_code == 404
    assert client.post("/api/v1/experiments/4242/archive").status_code == 404


# --- frozen snapshots (ADR-183) ---------------------------------------------------


def test_the_stored_parameters_and_configuration_do_not_follow_the_strategy(
    client, db_session
) -> None:
    version_id = _seed(client)
    created = _create(
        client,
        version_id,
        name="frozen",
        kind="backtest",
        symbol=_SYMBOL,
        parameters={"trend_period": 10},
    ).json()

    # The strategy moves on. A published version is immutable by trigger, so "moved on"
    # means two writes that are legal: its stored default parameter row changes, and a new
    # version with a different capital becomes the current one.
    version = db_session.get(StrategyVersion, version_id)
    parameters = list(version.parameters)
    assert parameters, "the seeded version should carry its default parameter row"
    for parameter in parameters:
        parameter.parameters_json = {"trend_period": 999}
    db_session.commit()

    moved_on = copy.deepcopy(_DSL)
    moved_on["parameters"] = {"trend_period": 999}
    moved_on["execution"]["initial_capital"] = 5_000
    published = client.post(
        f"/api/v1/strategies/{version.strategy_id}/versions",
        json={"version": "2.0.0", "dsl": moved_on},
    )
    assert published.status_code == 201, published.text
    assert int(published.json()["id"]) != version_id

    fetched = client.get(f"/api/v1/experiments/{created['id']}")
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    # The experiment kept the request it actually ran...
    assert body["parameters"] == {"trend_period": 10}
    assert body["results"][0]["parameters"] == {"trend_period": 10}
    assert body["results"][0]["metrics"] == created["results"][0]["metrics"]
    # ...and the configuration it actually ran with.
    assert body["initial_capital"] == 10_000.0

    stored = db_session.get(StrategyExperiment, created["id"])
    assert stored.parameters_json == {"trend_period": 10}
    assert float(stored.initial_capital) == 10_000.0
    assert _results(db_session, created["id"])[0].parameters_json == {"trend_period": 10}


# --- the history projection -------------------------------------------------------


def test_history_projects_the_strategy_and_filters_by_version(client, db_session) -> None:
    version_id = _seed(client)
    first = _create(client, version_id, name="first", kind="oos", symbol=_SYMBOL).json()

    version = db_session.get(StrategyVersion, version_id)
    second_version = client.post(
        f"/api/v1/strategies/{version.strategy_id}/versions",
        json={"version": "1.1.0", "dsl": copy.deepcopy(_DSL)},
    ).json()
    second = _create(client, int(second_version["id"]), name="second", kind="oos", symbol=_SYMBOL)
    assert second.status_code == 201, second.text

    listed = client.get("/api/v1/experiments").json()["experiments"]
    assert [row["id"] for row in listed] == [second.json()["id"], first["id"]]

    row = listed[1]
    assert row["strategy_version_id"] == version_id
    assert row["strategy_id"] == version.strategy_id
    assert row["strategy_name"] == "Experiments"
    assert row["version"] == "1.0.0"
    assert row["symbols"] == [_SYMBOL]
    assert row["is_adopted"] is False

    only_first = client.get(
        "/api/v1/experiments", params={"strategy_version_id": version_id}
    ).json()["experiments"]
    assert [item["id"] for item in only_first] == [first["id"]]
    assert only_first[0]["version"] == "1.0.0"

    only_second = client.get(
        "/api/v1/experiments", params={"strategy_version_id": int(second_version["id"])}
    ).json()["experiments"]
    assert [item["id"] for item in only_second] == [second.json()["id"]]
    assert only_second[0]["version"] == "1.1.0"


# --- the printed metrics of a result (ADR-185) -------------------------------------

# The result page prints these eight numbers, so an experiment has to publish all eight.
_PRINTED_METRICS = {
    "total_return",
    "max_drawdown",
    "sharpe",
    "win_rate",
    "number_of_trades",
    "final_equity",
    "cagr",
    "total_fees",
}


def test_a_result_publishes_the_eight_printed_metrics_from_stored_values(
    client, db_session
) -> None:
    version_id = _seed(client)
    created = _create(client, version_id, name="printed", kind="backtest", symbol=_SYMBOL).json()
    assert created["status"] == "completed", created

    run = db_session.get(BacktestRun, created["results"][0]["backtest_run_id"])
    assert run is not None and run.result is not None
    stored = dict(run.result.metrics_json)
    trades = db_session.scalars(
        select(BacktestTrade).where(BacktestTrade.backtest_run_id == run.id)
    ).all()
    assert trades, "the fixture produced no trades: a fee sum of 0 would prove nothing"
    expected_fees = float(sum((trade.fees for trade in trades), Decimal(0)))

    metrics = created["results"][0]["metrics"]
    # Seven of the eight are the engine's own stored numbers, key for key.
    assert set(metrics) == _PRINTED_METRICS | set(stored)
    for name in ("total_return", "max_drawdown", "sharpe", "win_rate", "number_of_trades"):
        assert metrics[name] == stored[name]
    assert metrics["final_equity"] == stored["final_equity"]
    assert metrics["cagr"] == stored["cagr"]
    # The eighth is the experiment layer reading the stored per-trade fees, not a re-run.
    assert metrics["total_fees"] == expected_fees
    assert not any(isinstance(trade.fees, type(None)) for trade in trades)
    assert "total_fees" not in stored

    # The history row carries the same eight values (minus the engine payloads), so the
    # list and the detail can never print two different fee totals.
    history = client.get(f"/api/v1/experiments/{created['id']}").json()["metrics"]
    assert set(history) == _PRINTED_METRICS
    assert history == {name: metrics[name] for name in history}


def test_a_result_stored_before_this_projection_reports_unknown_not_zero(
    client, db_session
) -> None:
    version_id = _seed(client)
    created = _create(client, version_id, name="legacy", kind="backtest", symbol=_SYMBOL).json()

    # Rows written before ADR-185 have no `total_fees` in their stored summary. No backfill
    # is claimed, so the projection must say "unknown" -- reporting 0 would claim a fee
    # total nobody ever computed.
    row = db_session.get(StrategyExperiment, created["id"])
    summary = dict(row.summary_json)
    summary["metrics"] = {
        name: value for name, value in summary["metrics"].items() if name != "total_fees"
    }
    row.summary_json = summary
    db_session.commit()

    metrics = client.get(f"/api/v1/experiments/{created['id']}").json()["metrics"]
    assert metrics["total_fees"] is None
    assert metrics["final_equity"] is not None and metrics["final_equity"] != 0
    assert metrics["total_return"] is not None
