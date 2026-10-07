"""Adopting an existing backtest as an experiment, and comparing stored experiments.

An experiment does not have to be run by the experiment layer: ``POST
/experiments/from-backtest/{run_id}`` turns a ``BacktestRun`` that already finished into
an experiment. The run is the source of truth -- parameters, metrics, summary and the
reproduction hashes are copied verbatim, and the new ``ExperimentResult`` keeps the
lineage back to ``backtest_runs.id``. Nothing is recomputed, so the experiment and the run
can never disagree about what happened; and a run is adopted once, so the same evidence
never forks into twins.

The comparison endpoints are pure projections of stored rows: they report the numbers the
engines stored, the configuration those numbers were produced with, and whether the
compared rows are actually comparable.
"""

from __future__ import annotations

import copy
from decimal import Decimal

from sqlalchemy import select

from app.api.schemas import MarketDataSyncRequest
from app.domain.models import (
    AuditLog,
    BacktestResult,
    BacktestRun,
    ExperimentResult,
    MarketDataSeries,
    StrategyExperiment,
    StrategyVersion,
)

_SYMBOL = "DEMO-AAPL"
# The comparable columns of an experiment: the five shared with `GET /backtests/compare`,
# plus the three the research page prints in full (ADR-185).
_METRICS = [
    "total_return",
    "max_drawdown",
    "sharpe",
    "win_rate",
    "number_of_trades",
    "final_equity",
    "cagr",
    "total_fees",
]

_DSL = {
    "schema_version": "1.0",
    "strategy": {"id": "exp-adoption", "name": "Experiment Adoption", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "indicators": [{"id": "trend", "type": "EMA", "period_ref": "trend_period"}],
    "parameters": {"trend_period": 20},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "trend"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "trend"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0, "take_profit_r_multiple": 2.0},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}


def _seed(client, dsl: dict | None = None) -> int:
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


def _adopt(client, run_id: int, **body):
    url = f"/api/v1/experiments/from-backtest/{run_id}"
    return client.post(url, json=body) if body else client.post(url)


def _results(db_session, experiment_id: int) -> list[ExperimentResult]:
    return list(
        db_session.scalars(
            select(ExperimentResult)
            .where(ExperimentResult.experiment_id == experiment_id)
            .order_by(ExperimentResult.id)
        ).all()
    )


def _compare(client, *experiment_ids: int):
    return client.get(
        "/api/v1/experiments/compare",
        params=[("ids", str(experiment_id)) for experiment_id in experiment_ids],
    )


# --- adopting a finished run ------------------------------------------------------


def test_adopting_a_completed_run_copies_the_stored_result_verbatim(client, db_session) -> None:
    version_id = _seed(client)
    run_id = _completed_run(client, version_id)
    run = db_session.get(BacktestRun, run_id)
    stored = run.result
    stored_metrics = dict(stored.metrics_json)
    runs_before = len(db_session.scalars(select(BacktestRun)).all())

    response = _adopt(client, run_id)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["status"] == "completed"
    assert body["kind"] == "backtest"
    assert body["result_count"] == 1
    assert body["name"] == f"回测 #{run_id} · {_SYMBOL}"
    assert body["notes"] is None
    assert body["strategy_version_id"] == version_id
    assert body["symbol"] == _SYMBOL
    assert body["symbols"] == [_SYMBOL]
    assert body["is_adopted"] is True
    assert body["started_at"] is not None and body["completed_at"] is not None
    assert body["error_message"] is None

    # Verbatim metrics at both levels: the numbers the engine stored, not a recompute --
    # plus `total_fees`, the one key the experiment layer derives, out of the fees already
    # sitting on this run's stored trades (ADR-185).
    stored_fees = float(sum((trade.fees for trade in run.trades), Decimal(0)))
    expected = {**stored_metrics, "total_fees": stored_fees}
    assert body["metrics"] == expected
    assert body["results"][0]["metrics"] == expected
    assert body["summary"]["metrics"] == expected
    assert set(expected) - set(stored_metrics) == {"total_fees"}
    # The run itself never carried that key: it is the experiment layer's read of the fees.
    assert "total_fees" not in stored_metrics

    # The reproduction handles travel with the experiment.
    assert body["summary"]["result_hash"] == stored.result_hash
    assert body["summary"]["engine_version"] == run.engine_version
    assert body["summary"]["feature_version"] == run.feature_version
    assert body["summary"]["dataset_hash"] == run.dataset_hash
    assert body["results"][0]["payload"]["result_hash"] == stored.result_hash
    assert body["results"][0]["payload"]["engine_version"] == run.engine_version
    assert body["results"][0]["payload"]["dataset_hash"] == run.dataset_hash

    # Lineage back to the run, and the run's own configuration.
    assert body["results"][0]["backtest_run_id"] == run_id
    assert body["summary"]["backtest_run_id"] == run_id
    assert body["request"]["adopted_from_backtest_run"] == run_id
    assert body["parameters"] == dict(run.parameters_json or {})
    assert body["timeframe"] == stored.summary_json["timeframe"]
    assert body["initial_capital"] == float(stored.summary_json["initial_capital"])
    series = run.dataset
    assert body["start_date"] is not None and body["end_date"] is not None
    assert body["start_date"][:10] == series.series_start.strftime("%Y-%m-%d")
    assert body["end_date"][:10] == series.series_end.strftime("%Y-%m-%d")
    assert body["results"][0]["kind"] == "backtest"
    assert body["results"][0]["label"] == body["name"]
    assert body["results"][0]["payload"]["symbol"] == _SYMBOL

    # Adopting is not a re-run: no second BacktestRun, and the source row is untouched.
    assert len(db_session.scalars(select(BacktestRun)).all()) == runs_before
    assert dict(db_session.get(BacktestRun, run_id).result.metrics_json) == stored_metrics
    assert len(_results(db_session, body["id"])) == 1

    # The adopted experiment is discoverable like any other, and says where it came from.
    listed = client.get("/api/v1/experiments").json()["experiments"]
    assert [row["id"] for row in listed] == [body["id"]]
    assert listed[0]["is_adopted"] is True
    assert listed[0]["backtest_run_id"] == run_id


def _experiments_named(client, name: str) -> list[dict]:
    listed = client.get("/api/v1/experiments").json()["experiments"]
    return [row for row in listed if row["name"] == name]


def test_adopting_honours_a_name_and_rejects_an_unknown_field(client) -> None:
    version_id = _seed(client)
    run_id = _completed_run(client, version_id)

    bad = client.post(f"/api/v1/experiments/from-backtest/{run_id}", json={"title": "nope"})
    assert bad.status_code == 422, bad.text
    # The rejected body never reached the service, so the run is still adoptable.
    assert _experiments_named(client, "nope") == []

    response = _adopt(client, run_id, name="my import", notes="because")
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "my import"
    assert response.json()["notes"] == "because"
    assert response.json()["results"][0]["label"] == "my import"


def test_adopting_the_same_run_twice_is_a_conflict_naming_the_experiment(
    client, db_session
) -> None:
    version_id = _seed(client)
    run_id = _completed_run(client, version_id)

    first = _adopt(client, run_id)
    assert first.status_code == 201, first.text

    again = _adopt(client, run_id)
    assert again.status_code == 409, again.text
    assert f"experiment {first.json()['id']}" in again.json()["detail"]

    # One run, one experiment: no twin was written.
    experiments = db_session.scalars(select(StrategyExperiment)).all()
    assert [row.id for row in experiments] == [first.json()["id"]]
    assert len(_results(db_session, first.json()["id"])) == 1


def test_adopting_an_unknown_run_is_404(client) -> None:
    _seed(client)
    response = _adopt(client, 999_999)
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "backtest run not found"


def test_adopting_an_unfinished_run_is_a_conflict(client, db_session) -> None:
    version_id = _seed(client)
    series = db_session.scalars(select(MarketDataSeries)).first()
    pending = BacktestRun(
        strategy_version_id=version_id,
        dataset_version_id=series.id,
        dataset_hash="0" * 64,
        status="pending",
    )
    db_session.add(pending)
    db_session.commit()

    response = _adopt(client, pending.id)
    assert response.status_code == 409, response.text
    assert "only a completed run can be adopted" in response.json()["detail"]
    assert db_session.scalars(select(StrategyExperiment)).all() == []


def test_adopting_a_completed_run_without_a_stored_result_is_a_conflict(client, db_session) -> None:
    version_id = _seed(client)
    run_id = _completed_run(client, version_id)
    stored = db_session.scalars(
        select(BacktestResult).where(BacktestResult.backtest_run_id == run_id)
    ).one()
    db_session.delete(stored)
    db_session.commit()
    # The relationship was loaded while serving POST /backtests; forget it so the service
    # reads the table the way production would.
    db_session.expire_all()

    response = _adopt(client, run_id)
    assert response.status_code == 409, response.text
    assert "has no stored result" in response.json()["detail"]
    assert db_session.scalars(select(StrategyExperiment)).all() == []


def test_every_experiment_result_keeps_the_lineage_of_its_run(client, db_session) -> None:
    version_id = _seed(client)
    run_id = _completed_run(client, version_id)
    adopted = _adopt(client, run_id)
    assert adopted.status_code == 201, adopted.text
    own = _create(client, version_id, name="own", kind="backtest", symbol=_SYMBOL)
    assert own.status_code == 201, own.text

    for created in (adopted.json(), own.json()):
        stored = db_session.get(StrategyExperiment, created["id"])
        assert db_session.get(StrategyVersion, stored.strategy_version_id) is not None
        assert stored.strategy_version_id == version_id
        rows = _results(db_session, created["id"])
        assert rows
        for row in rows:
            assert row.backtest_run_id is not None
            run = db_session.get(BacktestRun, row.backtest_run_id)
            # The run behind a result row exists and belongs to the experiment's version:
            # without this, a result would be evidence of nothing in particular.
            assert run is not None
            assert run.strategy_version_id == stored.strategy_version_id


# --- comparing stored experiments --------------------------------------------------


def test_compare_reports_a_different_configuration_and_each_config(client) -> None:
    version_id = _seed(client)
    left = _create(
        client,
        version_id,
        name="left",
        kind="backtest",
        symbol=_SYMBOL,
        parameters={"trend_period": 10},
    ).json()
    right = _create(
        client,
        version_id,
        name="right",
        kind="backtest",
        symbol=_SYMBOL,
        parameters={"trend_period": 40},
    ).json()

    response = _compare(client, left["id"], right["id"])
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["metrics"] == _METRICS
    assert body["comparability"] == "different-config"
    assert "参数不同" in body["differences"]

    for row, created in zip(body["experiments"], (left, right), strict=True):
        config = row["config"]
        assert config["experiment_id"] == created["id"]
        assert config["name"] == created["name"]
        assert config["kind"] == "backtest"
        assert config["status"] == "completed"
        assert config["strategy_id"] is not None
        assert config["strategy_name"] == "Experiments"
        assert config["version"] == "1.0.0"
        assert config["strategy_version_id"] == version_id
        assert config["symbols"] == [_SYMBOL]
        assert config["timeframe"] == "1d"
        assert config["parameters"] == created["parameters"]
        assert config["initial_capital"] == 10_000.0
        assert config["start"] is None and config["end"] is None
        assert row["name"] == created["name"]
        # The compared numbers are the ones already stored on each experiment.
        for metric in body["metrics"]:
            assert row[metric] == created["metrics"][metric]


def test_compare_of_identical_configurations_is_same_config(client) -> None:
    version_id = _seed(client)
    left = _create(
        client,
        version_id,
        name="left",
        kind="backtest",
        symbol=_SYMBOL,
        parameters={"trend_period": 20},
    ).json()
    right = _create(
        client,
        version_id,
        name="right",
        kind="backtest",
        symbol=_SYMBOL,
        parameters={"trend_period": 20},
    ).json()

    body = _compare(client, left["id"], right["id"]).json()
    assert body["comparability"] == "same-config"
    assert body["differences"] == []

    def _configuration(row: dict) -> dict:
        # The two rows only differ in what identifies them; the configuration is the same.
        return {
            key: value
            for key, value in row["config"].items()
            if key not in {"experiment_id", "name"}
        }

    assert [_configuration(row) for row in body["experiments"]] == [
        _configuration(body["experiments"][0])
    ] * 2


def test_compare_names_every_dimension_that_differs(client, db_session) -> None:
    version_id = _seed(client)
    version = db_session.get(StrategyVersion, version_id)
    other_dsl = copy.deepcopy(_DSL)
    other_dsl["execution"]["initial_capital"] = 25_000
    other = client.post(
        f"/api/v1/strategies/{version.strategy_id}/versions",
        json={"version": "2.0.0", "dsl": other_dsl},
    ).json()
    assert "id" in other, other

    left = _create(client, version_id, name="left", kind="backtest", symbol=_SYMBOL).json()
    right = _create(client, int(other["id"]), name="right", kind="backtest", symbol=_SYMBOL).json()

    body = _compare(client, left["id"], right["id"]).json()
    # Same parameters and same symbol, so only these two dimensions differ -- and the
    # comparison says so instead of silently implying the numbers are comparable.
    assert body["differences"] == ["初始资金不同", "策略版本不同"]
    assert body["comparability"] == "different-config"
    assert body["experiments"][0]["config"]["initial_capital"] == 10_000.0
    assert body["experiments"][1]["config"]["initial_capital"] == 25_000.0
    assert body["experiments"][1]["config"]["version"] == "2.0.0"


def test_compare_rejects_ids_that_are_not_numbers(client) -> None:
    _seed(client)
    response = client.get("/api/v1/experiments/compare", params={"ids": "1,abc"})
    assert response.status_code == 422, response.text
    assert "comma separated integers" in response.json()["detail"]


def test_adopting_a_run_writes_the_full_ledger_action(client, db_session) -> None:
    """The adoption ledger entry is 41 characters; ``audit_logs.action`` must hold it whole.

    The column shipped as ``VARCHAR(32)``, so PostgreSQL rejected this very write and every
    adoption answered HTTP 500 on the deployed NAS, while SQLite -- this suite -- stored the
    same string without complaint (ADR-186). The width is also asserted in
    ``tests/test_audit_action_length.py``; it is asserted here as well because this is the
    flow that writes it.
    """

    version_id = _seed(client)
    run_id = _completed_run(client, version_id)
    adopted = _adopt(client, run_id)
    assert adopted.status_code == 201, adopted.text
    experiment_id = adopted.json()["id"]

    entries = db_session.scalars(
        select(AuditLog).where(
            AuditLog.entity_type == "strategy_experiment",
            AuditLog.entity_id == str(experiment_id),
            AuditLog.event_type == "experiment_adopted_from_backtest",
        )
    ).all()
    assert len(entries) == 1, [entry.event_type for entry in entries]

    entry = entries[0]
    assert entry.action == "strategy_experiment_adopted_from_backtest"
    assert len(entry.action) == 41
    assert AuditLog.__table__.c.action.type.length is not None
    assert AuditLog.__table__.c.action.type.length >= len(entry.action)
