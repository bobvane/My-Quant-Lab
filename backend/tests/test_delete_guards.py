"""Every delete names who points at the row, and deletes nothing when somebody does (ADR-222).

The three deleting endpoints each asked one remembered question: strategy looked at
backtests, signals and accounts but not at versions, series looked only at backtests,
and backtest looked at nothing at all. A row somebody really pointed at therefore
reached the database, hit a foreign key and surfaced to the user as a generic
``500 internal server error``.

The guard now derives the referrers from the foreign-key graph
(``app/domain/references.py``), classifies every edge with an explicit business
policy, and answers with one shape:

* ``block``    -> 409 ``delete_blocked``; nothing is deleted, not even partly
* ``archive``  -> the row is kept and flagged, unless the caller insists (``?purge=true``)
* ``set-null`` -> the row is deleted, the reference is cleared, and the caller is told
                  through a top-level ``warnings`` array
* ``cascade``  -> the child goes with the parent and is never reported as a blocker

These tests are the SQLite half of the B1 matrix (T1-T10, T12-T15). The PostgreSQL half
is ``test_delete_guards_postgres.py`` (T11): SQLite only enforces foreign keys when the
pragma is on, so it cannot prove that the database would really have refused.
"""

from __future__ import annotations

import datetime as dt
import pathlib

from sqlalchemy import select

from app.api.schemas import StrategyVersionCreate
from app.domain import references
from app.domain.models import (
    AIResearchRun,
    AITask,
    Asset,
    AuditLog,
    BacktestResult,
    BacktestRun,
    BacktestTrade,
    ExperimentResult,
    FeatureSnapshot,
    MarketDataBar,
    MarketDataSeries,
    MarketDataSource,
    PaperAccount,
    PaperTrade,
    Signal,
    Strategy,
    StrategyDraft,
    StrategyExperiment,
    StrategyHypothesis,
    StrategyParameter,
    StrategyVersion,
)

PREFIX = "/api/v1/market-data"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

DSL: dict = {
    "schema_version": "1.0",
    "strategy": {"id": "delete-guards", "name": "Delete Guards", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "ema20"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0, "take_profit_r_multiple": 2.0},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}

DAY = dt.datetime(2024, 1, 2, tzinfo=dt.UTC)


# --------------------------------------------------------------------------------------
# fixtures: the smallest rows that really point at something
# --------------------------------------------------------------------------------------
def _strategy_with_version(client, name: str) -> tuple[int, int]:
    """A strategy and one version, created through the API so the slugs are real."""

    strategy = client.post("/api/v1/strategies", json={"name": name}).json()
    version = client.post(
        f"/api/v1/strategies/{strategy['id']}/versions",
        json=StrategyVersionCreate(version="1.0.0", dsl=DSL).model_dump(),
    ).json()
    return strategy["id"], version["id"]


def _bar(series_id: int, day: int) -> MarketDataBar:
    return MarketDataBar(
        series_id=series_id,
        timestamp=DAY + dt.timedelta(days=day),
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
    symbol: str = "GUARD-AAPL",
    source_name: str = "synthetic",
    bars: int = 3,
    snapshots: int = 0,
) -> dict:
    """One asset and one series, with optional bars and feature snapshots."""

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
                bar_timestamp=DAY + dt.timedelta(days=day),
                feature_version="1.0.0",
                values_json={"close": 100.0},
                input_hash="i" * 64,
                available_at=DAY + dt.timedelta(days=day),
            )
        )
    db_session.commit()
    return {"series_id": series.id, "asset_id": asset.id}


def _run(db_session, *, version_id: int, series_id: int, status: str = "completed") -> BacktestRun:
    run = BacktestRun(
        strategy_version_id=version_id,
        dataset_version_id=series_id,
        dataset_hash="d" * 64,
        status=status,
    )
    db_session.add(run)
    db_session.flush()
    return run


def _signal(db_session, *, version_id: int, asset_id: int, day: int = 2) -> Signal:
    signal = Signal(
        strategy_version_id=version_id,
        asset_id=asset_id,
        timeframe="1d",
        bar_timestamp=dt.datetime(2024, 1, day, tzinfo=dt.UTC),
        state="new",
        direction="LONG",
        feature_snapshot_hash="0" * 64,
        data_source="synthetic",
    )
    db_session.add(signal)
    db_session.flush()
    return signal


def _experiment(
    db_session,
    *,
    version_id: int,
    series_id: int | None = None,
    name: str = "Guarded experiment",
) -> StrategyExperiment:
    experiment = StrategyExperiment(
        name=name,
        kind="sensitivity",
        strategy_version_id=version_id,
        series_id=series_id,
    )
    db_session.add(experiment)
    db_session.flush()
    return experiment


def _account(
    db_session,
    *,
    name: str,
    strategy_id: int | None = None,
    strategy_version_id: int | None = None,
    backtest_run_id: int | None = None,
) -> PaperAccount:
    account = PaperAccount(
        name=name,
        initial_cash=10_000,
        cash=10_000,
        strategy_id=strategy_id,
        strategy_version_id=strategy_version_id,
        backtest_run_id=backtest_run_id,
    )
    db_session.add(account)
    db_session.flush()
    return account


def _unwritten_ai_task(db_session, *, version_id: int) -> AITask:
    """An ``ai_tasks`` row pointing at a version.

    Nothing in the application writes ``ai_tasks.strategy_version_id`` today
    (``app/ai/runtime.py`` builds the row without it), but the column is a real
    foreign key, so the guard must protect the state even though the app cannot
    currently produce it. The row therefore has to be built by hand.
    """

    task = AITask(
        task_type="architect",
        prompt_name="strategy_architect",
        prompt_version="1.0",
        input_hash="a" * 64,
        status="completed",
        strategy_version_id=version_id,
    )
    db_session.add(task)
    db_session.flush()
    return task


def _compiled_draft(db_session, *, version_id: int) -> StrategyDraft:
    run = AIResearchRun(question="为什么这个策略会有效？", status="drafted")
    db_session.add(run)
    db_session.flush()
    hypothesis = StrategyHypothesis(run_id=run.id, strategy_name="Guarded strategy")
    db_session.add(hypothesis)
    db_session.flush()
    draft = StrategyDraft(
        run_id=run.id,
        hypothesis_id=hypothesis.id,
        version="1.0",
        status="SUPPORTED",
        capability_status="SUPPORTED",
        compiled_strategy_version_id=version_id,
    )
    db_session.add(draft)
    db_session.flush()
    return draft


def _paper_trade(
    db_session, *, account_id: int, asset_id: int, strategy_version: str
) -> PaperTrade:
    """A trade whose ``strategy_version`` is a *string* label, not a foreign key."""

    trade = PaperTrade(
        account_id=account_id,
        asset_id=asset_id,
        direction="LONG",
        entry_time=DAY,
        entry_price=100,
        quantity=1,
        strategy_version=strategy_version,
    )
    db_session.add(trade)
    db_session.flush()
    return trade


def _count(db_session, model, **where) -> int:
    conditions = [getattr(model, key) == value for key, value in where.items()]
    return len(db_session.scalars(select(model).where(*conditions)).all())


def _version_label(db_session, version_id: int) -> str:
    version = db_session.get(StrategyVersion, version_id)
    assert version is not None
    return str(version.version)


def _blame(response) -> dict:
    """Assert the unified refusal and return its ``error`` object."""

    assert response.status_code == 409, response.text
    body = response.json()
    assert set(body) == {"error"}, body
    assert body["error"]["code"] == references.DELETE_BLOCKED
    assert body["error"]["message"], "the refusal must be explainable"
    assert "warnings" not in body, "a refusal is not a success"
    return body["error"]


def _blockers(error: dict) -> list[dict]:
    return error["details"]["blockers"]


# --------------------------------------------------------------------------------------
# T1-T4: a strategy somebody points at
# --------------------------------------------------------------------------------------
def test_a_backtest_blocks_deleting_its_strategy(client, db_session) -> None:
    """B1-T1: the referrer the old guard did check, now with a machine-readable body."""

    seeded = _seed(db_session)
    strategy_id, version_id = _strategy_with_version(client, "T1 guarded")
    _run(db_session, version_id=version_id, series_id=seeded["series_id"])
    db_session.commit()

    error = _blame(client.delete(f"/api/v1/strategies/{strategy_id}"))

    assert error["details"]["entity"] == "strategy"
    assert error["details"]["id"] == strategy_id
    assert _blockers(error) == [
        {"table": "backtest_runs", "kind": "backtest", "label": "回测记录", "count": 1}
    ]
    assert error["details"]["next"] == [{"action": "manual_cleanup", "available": True}]
    assert "回测记录" in error["message"]
    assert db_session.get(Strategy, strategy_id) is not None


def test_a_signal_blocks_deleting_its_strategy(client, db_session) -> None:
    """B1-T2."""

    seeded = _seed(db_session, symbol="T2-AAPL")
    strategy_id, version_id = _strategy_with_version(client, "T2 guarded")
    _signal(db_session, version_id=version_id, asset_id=seeded["asset_id"])
    db_session.commit()

    error = _blame(client.delete(f"/api/v1/strategies/{strategy_id}"))

    assert [(item["kind"], item["count"]) for item in _blockers(error)] == [("signal", 1)]
    assert "信号记录" in error["message"]


def test_an_experiment_blocks_deleting_its_strategy(client, db_session) -> None:
    """B1-T3: ``strategy_experiments`` was missing from the old three-way count."""

    strategy_id, version_id = _strategy_with_version(client, "T3 guarded")
    _experiment(db_session, version_id=version_id)
    db_session.commit()

    error = _blame(client.delete(f"/api/v1/strategies/{strategy_id}"))

    assert _blockers(error) == [
        {"table": "strategy_experiments", "kind": "experiment", "label": "策略实验", "count": 1}
    ]


def test_an_ai_task_and_a_compiled_draft_block_deleting_their_strategy(client, db_session) -> None:
    """B1-T4: the AI layer's two pointers, one of which the application never writes.

    ``ai_tasks.strategy_version_id`` has no writer anywhere in ``app/`` -- the row is
    built by hand here. That proves the guard protects the state; it does **not** prove
    that production history is free of such rows (that was never queried).
    """

    strategy_id, version_id = _strategy_with_version(client, "T4 guarded")
    _unwritten_ai_task(db_session, version_id=version_id)
    _compiled_draft(db_session, version_id=version_id)
    db_session.commit()

    error = _blame(client.delete(f"/api/v1/strategies/{strategy_id}"))

    assert {(item["table"], item["kind"], item["count"]) for item in _blockers(error)} == {
        ("ai_tasks", "ai_task", 1),
        ("strategy_drafts", "draft", 1),
    }


# --------------------------------------------------------------------------------------
# T5, T6, T8: the other two endpoints
# --------------------------------------------------------------------------------------
def test_an_experiment_blocks_deleting_its_series_even_with_purge(client, db_session) -> None:
    """B1-T5: ``strategy_experiments.series_id`` was missing, yet the caller must not
    be able to delete the bars the experiment was computed from -- purge or not."""

    seeded = _seed(db_session, symbol="T5-AAPL", bars=3)
    _, version_id = _strategy_with_version(client, "T5 guarded")
    _experiment(db_session, version_id=version_id, series_id=seeded["series_id"])
    db_session.commit()
    series_id = seeded["series_id"]

    error = _blame(client.delete(f"{PREFIX}/series/{series_id}"))
    assert error["details"]["entity"] == "series"
    assert [(item["kind"], item["count"]) for item in _blockers(error)] == [("experiment", 1)]

    _blame(client.delete(f"{PREFIX}/series/{series_id}?purge=true"))
    db_session.expire_all()
    assert _count(db_session, MarketDataBar, series_id=series_id) == 3
    assert db_session.get(MarketDataSeries, series_id) is not None


def test_an_experiment_blocks_deleting_the_backtest_it_used(client, db_session) -> None:
    """B1-T6: ``DELETE /backtests/{id}`` had no check at all."""

    seeded = _seed(db_session)
    strategy_id, version_id = _strategy_with_version(client, "T6 guarded")
    run = _run(db_session, version_id=version_id, series_id=seeded["series_id"])
    db_session.add(
        BacktestResult(
            backtest_run_id=run.id,
            summary_json={"trade_count": 0},
            result_hash="r" * 64,
        )
    )
    db_session.add(
        BacktestTrade(
            backtest_run_id=run.id,
            symbol="GUARD-AAPL",
            direction="LONG",
            entry_time=DAY,
            entry_price=100,
            quantity=1,
            strategy_version=f"{strategy_id}@1.0.0",
        )
    )
    experiment = _experiment(db_session, version_id=version_id, name="Uses the run")
    db_session.add(
        ExperimentResult(
            experiment_id=experiment.id,
            kind="point",
            backtest_run_id=run.id,
            payload_json={"parameters": {}},
        )
    )
    db_session.commit()

    error = _blame(client.delete(f"/api/v1/backtests/{run.id}"))

    assert error["details"]["entity"] == "backtest_run"
    assert [(item["kind"], item["count"]) for item in _blockers(error)] == [("experiment", 1)]
    assert db_session.get(BacktestRun, run.id) is not None
    assert _count(db_session, BacktestResult, backtest_run_id=run.id) == 1
    assert _count(db_session, BacktestTrade, backtest_run_id=run.id) == 1


def test_an_unbinding_reference_is_a_warning_not_a_refusal(client, db_session) -> None:
    """B1-T8: ``paper_accounts.backtest_run_id`` is ``SET NULL``.

    The account survives, the binding is cleared, and the caller must be told -- a
    silent ``UPDATE`` is exactly the kind of change that goes unnoticed (ADR-007).
    """

    seeded = _seed(db_session)
    _, version_id = _strategy_with_version(client, "T8 guarded")
    run = _run(db_session, version_id=version_id, series_id=seeded["series_id"])
    account = _account(db_session, name="Bound account", backtest_run_id=run.id)
    db_session.commit()

    response = client.delete(f"/api/v1/backtests/{run.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == run.id
    assert isinstance(body["warnings"], list) and len(body["warnings"]) == 1
    assert "1 个模拟盘账户" in body["warnings"][0]
    assert "绑定会被清空" in body["warnings"][0]

    db_session.expire_all()
    survivor = db_session.get(PaperAccount, account.id)
    assert survivor is not None and survivor.backtest_run_id is None


def test_the_binding_is_cleared_by_the_guard_not_by_the_database(client, db_session) -> None:
    """B1-T8b: the same clear happens even where the database would not do it.

    PostgreSQL clears ``ondelete="SET NULL"`` columns itself.  SQLite only does it while
    ``PRAGMA foreign_keys`` is on, and the application never turns it on
    (``backend/app/core/db.py``), so the endpoint has to clear the binding too --
    otherwise the 200 says "绑定会被清空" and the column keeps pointing at a row that is
    gone.  This asserts the clearing happens *before* anything is deleted, which is the
    part only the guard can do.
    """

    seeded = _seed(db_session)
    _, version_id = _strategy_with_version(client, "T8b guarded")
    run = _run(db_session, version_id=version_id, series_id=seeded["series_id"])
    account = _account(db_session, name="Still bound", backtest_run_id=run.id)
    db_session.commit()

    report = references.collect(db_session, entity=references.BACKTEST_RUN, entity_id=run.id)
    assert [blocker.kind for blocker in report.unbinding] == ["paper_account"]
    assert report.unbinding_columns == (("paper_accounts", "backtest_run_id"),)

    assert references.apply_unbindings(db_session, report) == 1
    db_session.commit()

    db_session.expire_all()
    assert db_session.get(BacktestRun, run.id) is not None
    survivor = db_session.get(PaperAccount, account.id)
    assert survivor is not None and survivor.backtest_run_id is None
    assert references.apply_unbindings(db_session, report) == 0


# --------------------------------------------------------------------------------------
# T7: the success path keeps its old shape and gains ``warnings``
# --------------------------------------------------------------------------------------
def test_deleting_an_unreferenced_strategy_succeeds(client, db_session) -> None:
    """B1-T7 (strategies)."""

    strategy_id, version_id = _strategy_with_version(client, "T7 guarded")
    db_session.add(StrategyParameter(strategy_version_id=version_id, parameters_json={}))
    db_session.commit()

    response = client.delete(f"/api/v1/strategies/{strategy_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == strategy_id
    assert body["name"] == "T7 guarded"
    assert body["warnings"] == []
    assert _count(db_session, AuditLog, event_type="strategy_deleted") == 1
    assert client.get(f"/api/v1/strategies/{strategy_id}").status_code == 404


def test_deleting_an_unreferenced_series_succeeds(client, db_session) -> None:
    """B1-T7 (series)."""

    seeded = _seed(db_session, symbol="T7-AAPL", bars=2, snapshots=1)
    series_id = seeded["series_id"]

    response = client.delete(f"{PREFIX}/series/{series_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == series_id
    assert body["archived"] is False
    assert body["blocking_runs"] == 0
    assert body["warnings"] == []
    assert _count(db_session, MarketDataBar, series_id=series_id) == 0


def test_deleting_an_unreferenced_backtest_succeeds(client, db_session) -> None:
    """B1-T7 (backtests)."""

    seeded = _seed(db_session)
    _, version_id = _strategy_with_version(client, "T7b guarded")
    run = _run(db_session, version_id=version_id, series_id=seeded["series_id"])
    db_session.commit()

    response = client.delete(f"/api/v1/backtests/{run.id}")
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": run.id, "warnings": []}


# --------------------------------------------------------------------------------------
# T9: the archive path keeps working, and restore still restores
# --------------------------------------------------------------------------------------
def test_a_used_series_is_archived_and_can_be_restored(client, db_session) -> None:
    """B1-T9 (ADR-081): a used series is hidden, not destroyed, and ``restore`` works."""

    seeded = _seed(db_session, symbol="T9-AAPL", bars=2)
    _, version_id = _strategy_with_version(client, "T9 guarded")
    _run(db_session, version_id=version_id, series_id=seeded["series_id"])
    db_session.commit()
    series_id = seeded["series_id"]

    response = client.delete(f"{PREFIX}/series/{series_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] is None
    assert body["archived"] is True
    assert body["blocking_runs"] == 1
    assert body["warnings"] == []
    assert "回测" in body["message"]

    db_session.expire_all()
    assert db_session.get(MarketDataSeries, series_id).is_archived is True

    restored = client.post(f"{PREFIX}/series/{series_id}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["is_archived"] is False
    db_session.expire_all()
    assert _count(db_session, MarketDataBar, series_id=series_id) == 2


# --------------------------------------------------------------------------------------
# T10: the guards that keep the policy table honest
# --------------------------------------------------------------------------------------
def test_the_policy_table_covers_every_foreign_key_that_points_at_a_deletable_row() -> None:
    """B1-T10 (i)+(ii): a new foreign key with no policy turns this test red."""

    assert references.policy_columns_missing_from_metadata() == ()

    declared: set[tuple[str, str]] = set()
    for entity in (references.STRATEGY, references.SERIES, references.BACKTEST_RUN):
        assert references.edges_without_policy(entity) == ()
        declared.update((edge.table, edge.column) for edge in references.inbound_edges(entity))

    assert set(references.POLICIES) <= declared


def test_every_edge_gets_a_rule_and_an_unclassified_one_falls_back_to_blocking() -> None:
    """B1-T10 (iii): the four rules really are distinct, and the fallback is conservative."""

    cascades = (
        references.Edge("strategy_parameters", "strategy_version_id", "strategy_versions", "id"),
        references.Edge("strategy_versions", "strategy_id", "strategies", "id"),
        references.Edge("market_data_bars", "series_id", "market_data", "id"),
        references.Edge("feature_snapshots", "series_id", "market_data", "id"),
        references.Edge("backtest_results", "backtest_run_id", "backtest_runs", "id"),
        references.Edge("backtest_trades", "backtest_run_id", "backtest_runs", "id"),
    )
    for edge in cascades:
        assert references.classify(edge).rule == references.RULE_CASCADE, edge.describe()

    unbinding = references.Edge("paper_accounts", "backtest_run_id", "backtest_runs", "id")
    assert references.classify(unbinding).rule == references.RULE_SET_NULL

    archived = references.Edge("backtest_runs", "dataset_version_id", "market_data", "id")
    assert references.classify(archived).rule == references.RULE_ARCHIVE

    unknown = references.classify(
        references.Edge("mystery_table", "strategy_id", "strategies", "id")
    )
    assert unknown.rule == references.RULE_BLOCK
    assert unknown.kind == references.KIND_UNKNOWN
    assert "mystery_table.strategy_id" in unknown.label


def test_a_child_row_is_not_reported_as_a_blocker(client, db_session) -> None:
    """B1-T10 (iii) at runtime: cascade children must neither block nor appear."""

    seeded = _seed(db_session, symbol="T10-AAPL", bars=2, snapshots=1)
    strategy_id, version_id = _strategy_with_version(client, "T10 guarded")
    run = _run(db_session, version_id=version_id, series_id=seeded["series_id"])
    db_session.add(BacktestResult(backtest_run_id=run.id, summary_json={}, result_hash="r" * 64))
    db_session.add(StrategyParameter(strategy_version_id=version_id, parameters_json={}))
    db_session.commit()

    # Every one of these is a cascade child of something being deleted below.
    assert client.delete(f"/api/v1/backtests/{run.id}").status_code == 200
    assert client.delete(f"/api/v1/strategies/{strategy_id}").status_code == 200
    assert client.delete(f"{PREFIX}/series/{seeded['series_id']}").status_code == 200

    db_session.expire_all()
    assert _count(db_session, BacktestResult) == 0
    assert _count(db_session, StrategyParameter) == 0
    assert _count(db_session, FeatureSnapshot) == 0
    assert _count(db_session, MarketDataBar) == 0


# --------------------------------------------------------------------------------------
# T13: nothing is deleted when the answer is "no"
# --------------------------------------------------------------------------------------
def test_a_refusal_deletes_nothing_at_all(client, db_session) -> None:
    """B1-T13: the check happens before the first ``DELETE``, not after the first error."""

    seeded = _seed(db_session, symbol="T13-AAPL", bars=2)
    strategy_id, version_id = _strategy_with_version(client, "T13 guarded")
    run = _run(db_session, version_id=version_id, series_id=seeded["series_id"])
    db_session.add(BacktestResult(backtest_run_id=run.id, summary_json={}, result_hash="r" * 64))
    _experiment(db_session, version_id=version_id, name="T13 experiment")
    _account(db_session, name="T13 account", strategy_version_id=version_id)
    db_session.commit()

    before = (
        _count(db_session, Strategy),
        _count(db_session, StrategyVersion),
        _count(db_session, BacktestRun),
        _count(db_session, BacktestResult),
        _count(db_session, StrategyExperiment),
        _count(db_session, PaperAccount),
    )

    _blame(client.delete(f"/api/v1/strategies/{strategy_id}"))

    db_session.expire_all()
    after = (
        _count(db_session, Strategy),
        _count(db_session, StrategyVersion),
        _count(db_session, BacktestRun),
        _count(db_session, BacktestResult),
        _count(db_session, StrategyExperiment),
        _count(db_session, PaperAccount),
    )
    assert after == before


# --------------------------------------------------------------------------------------
# T14: a string reference is not a foreign key, so it must be named by hand
# --------------------------------------------------------------------------------------
def test_a_string_reference_is_reported_in_the_warnings(client, db_session) -> None:
    """B1-T14: ``paper_trades.strategy_version`` is a label like ``7@1.0.0``.

    It is not a foreign key, so the graph cannot see it; the delete succeeds and the
    trade survives, but the caller is told how many trades still name the version.
    """

    seeded = _seed(db_session, symbol="T14-AAPL")
    strategy_id, version_id = _strategy_with_version(client, "T14 guarded")
    account = _account(db_session, name="T14 account")
    label = f"{strategy_id}@{_version_label(db_session, version_id)}"
    trade = _paper_trade(
        db_session,
        account_id=account.id,
        asset_id=seeded["asset_id"],
        strategy_version=label,
    )
    db_session.commit()

    response = client.delete(f"/api/v1/strategies/{strategy_id}")
    assert response.status_code == 200, response.text
    warnings = response.json()["warnings"]
    assert any("1 笔" in warning and "模拟成交" in warning for warning in warnings), warnings

    db_session.expire_all()
    assert db_session.get(PaperTrade, trade.id) is not None


def test_a_trade_naming_another_version_is_not_counted(client, db_session) -> None:
    """B1-T14 (negative half): the prefix must match the strategy, not just end in a version."""

    seeded = _seed(db_session, symbol="T14b-AAPL")
    strategy_id, _ = _strategy_with_version(client, "T14b guarded")
    account = _account(db_session, name="T14b account")
    _paper_trade(
        db_session,
        account_id=account.id,
        asset_id=seeded["asset_id"],
        strategy_version="999999@1.0.0",
    )
    db_session.commit()

    response = client.delete(f"/api/v1/strategies/{strategy_id}")
    assert response.status_code == 200, response.text
    assert response.json()["warnings"] == []


# --------------------------------------------------------------------------------------
# T15: an account bound to a *version* blocks the strategy delete
# --------------------------------------------------------------------------------------
def test_an_account_holding_a_version_blocks_the_strategy_delete(client, db_session) -> None:
    """B1-T15: this is the referrer the old count missed.

    The old guard counted ``paper_accounts.strategy_id`` only, so an account whose
    ``strategy_id`` is NULL but whose ``strategy_version_id`` points at one of the
    strategy's versions reached the database and came back as a 500.
    """

    strategy_id, version_id = _strategy_with_version(client, "T15 guarded")
    account = _account(db_session, name="Version-bound account", strategy_version_id=version_id)
    db_session.commit()

    error = _blame(client.delete(f"/api/v1/strategies/{strategy_id}"))

    assert _blockers(error) == [
        {"table": "paper_accounts", "kind": "paper_account", "label": "模拟盘账户", "count": 1}
    ]
    assert db_session.get(Strategy, strategy_id) is not None
    assert db_session.get(StrategyVersion, version_id) is not None
    assert db_session.get(PaperAccount, account.id) is not None


def test_an_account_matching_both_edges_is_counted_once(client, db_session) -> None:
    """B1-T15 (merge): the direct and the version pointer are the same account."""

    strategy_id, version_id = _strategy_with_version(client, "T15b guarded")
    _account(
        db_session,
        name="Doubly bound account",
        strategy_id=strategy_id,
        strategy_version_id=version_id,
    )
    db_session.commit()

    error = _blame(client.delete(f"/api/v1/strategies/{strategy_id}"))
    assert [(item["table"], item["count"]) for item in _blockers(error)] == [("paper_accounts", 1)]


# --------------------------------------------------------------------------------------
# T12: the user can see why, and what to do next
# --------------------------------------------------------------------------------------
def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_the_reason_and_the_next_step_reach_the_interface_and_the_spec() -> None:
    """B1-T12: the new 409 fields are useless unless something displays them.

    The three views own the ``warnings`` list (a change that is not an error), the
    shared helper in ``api.ts`` owns the reason and the "what else you can do" line --
    it is the only place that can render ``error.details.next`` for every endpoint.
    """

    spec = _read("docs/12_API_SPEC.md")
    assert references.DELETE_BLOCKED in spec
    assert "warnings" in spec
    assert "blockers" in spec

    for view in ("StrategiesView.vue", "DataView.vue", "BacktestView.vue"):
        text = _read(f"frontend/src/views/{view}")
        assert "deleteNotices" in text, f"{view} must keep the warnings it was given"
        assert "deleteBlockedHint(" in text, f"{view} must show the reason"

    api_text = _read("frontend/src/api.ts")
    assert "export function deleteBlockedHint" in api_text
    # The hint is built from the server's own list, so an action the endpoint does not
    # really offer is never offered to the user (ADR-138).
    assert "details?.next" in api_text
    for action in ("archive", "purge", "manual_cleanup"):
        assert action in api_text, f"the {action} hint must have a sentence"
