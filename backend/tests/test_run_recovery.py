"""Stuck-run recovery: a row that claims to be running must be either running or failed.

A run is a durable row that another process moves forward. If that process dies, the
row stays ``running`` for ever: nothing owns it, so nothing notices. Late
acknowledgement closes the common case (the message is re-delivered and the run
finishes or fails on its own), and this sweep closes the rest — the message is gone
too, or the whole App was restarted with runs in flight (docs/33 §6).

The rules are conditional ``UPDATE``s, so the properties that matter are all about
what they *refuse* to match: a fresh run, a run that already decided, and a second
runner arriving late.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from itertools import count

from sqlalchemy import select

from app.data import run_recovery
from app.domain.models import (
    AIResearchRun,
    Asset,
    AuditLog,
    BacktestRun,
    MarketDataSeries,
    MarketDataSource,
    Strategy,
    StrategyVersion,
)

NOW = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.UTC)

#: The seeded strategy/asset/source names are unique columns, so every run needs its
#: own triple.
_SEEDS = count(1)


def _seed(db_session) -> tuple[int, int]:
    """The minimum a backtest run needs to exist: a version and a series."""

    tag = next(_SEEDS)
    strategy = Strategy(name=f"Reaper {tag}", slug=f"reaper-{tag}")
    db_session.add(strategy)
    db_session.flush()
    version = StrategyVersion(
        strategy_id=strategy.id, version="1.0.0", dsl_json={}, immutable_hash="v" * 64
    )
    db_session.add(version)
    db_session.flush()
    asset = Asset(symbol=f"REAP{tag}", asset_class="stock")
    db_session.add(asset)
    db_session.flush()
    source = MarketDataSource(name=f"reaper-src-{tag}", base_url="x")
    db_session.add(source)
    db_session.flush()
    series = MarketDataSeries(asset_id=asset.id, timeframe="1d", source_id=source.id)
    db_session.add(series)
    db_session.flush()
    return version.id, series.id


def _backtest(
    db_session,
    *,
    status: str = "running",
    progress: int = 0,
    started_at: dt.datetime | None = None,
    current_step: str = "loading data",
) -> BacktestRun:
    version_id, series_id = _seed(db_session)
    run = BacktestRun(
        strategy_version_id=version_id,
        dataset_version_id=series_id,
        dataset_hash="d" * 64,
        status=status,
        progress=progress,
        current_step=current_step,
        started_at=started_at if started_at is not None else NOW - dt.timedelta(hours=5),
        created_at=NOW - dt.timedelta(hours=5),
    )
    db_session.add(run)
    db_session.commit()
    return run


def _research(
    db_session,
    *,
    status: str = "queued",
    created_at: dt.datetime | None = None,
    updated_at: dt.datetime | None = None,
) -> AIResearchRun:
    run = AIResearchRun(question="q", status=status, current_step=status)
    run.created_at = created_at if created_at is not None else NOW - dt.timedelta(hours=5)
    run.updated_at = updated_at if updated_at is not None else NOW - dt.timedelta(hours=5)
    db_session.add(run)
    db_session.commit()
    return run


def _audits(db_session) -> list[AuditLog]:
    return list(db_session.scalars(select(AuditLog)).all())


def _stored(db_session, model, run_id):
    """Read the row the way the next reader would.

    The sweep writes with a bulk ``UPDATE``, so the session's identity map is not
    the source of truth for what landed; in production the reaper has a session of
    its own and this question does not arise.
    """

    db_session.expire_all()
    return db_session.get(model, run_id)


@contextmanager
def _worker_scope(db_session):
    """``session_scope`` as a worker sees it: the test's own session."""

    yield db_session


def test_a_never_started_backtest_is_reaped_after_the_queue_budget(db_session) -> None:
    """``progress == 0`` means the API committed the row and no worker ever touched it."""

    run = _backtest(db_session, progress=0, started_at=NOW - dt.timedelta(hours=3))

    report = run_recovery.recover_stuck_runs(db_session, now=NOW)

    assert report["backtests"] == 1
    stored = _stored(db_session, BacktestRun, run.id)
    assert stored.status == "failed"
    assert stored.current_step == "failed"
    assert stored.error_message == run_recovery.QUEUED_MESSAGE
    assert stored.finished_at is not None
    # The user has to be able to tell "the execution died" from "the strategy failed".
    assert "worker" in stored.error_message

    audit = _audits(db_session)
    assert [entry.event_type for entry in audit] == [run_recovery.AUDIT_EVENT]
    assert audit[0].entity_type == "backtest_run"
    assert audit[0].entity_id == str(run.id)
    assert audit[0].payload_json["reason"] == "never_started"


def test_an_in_flight_backtest_is_reaped_only_after_the_lost_budget(db_session) -> None:
    """The first rung is the execution's own evidence, so the budget is the hard limit."""

    started = NOW - dt.timedelta(hours=2)
    run = _backtest(db_session, progress=20, started_at=started, current_step="computing features")

    # Half an hour of silence is a running task, not a lost one.
    quiet = run_recovery.recover_stuck_runs(db_session, now=started + dt.timedelta(minutes=30))
    assert quiet["backtests"] == 0
    assert _stored(db_session, BacktestRun, run.id).status == "running"

    # Past the budget (task_time_limit + margin) the silence is the diagnosis.
    late = run_recovery.recover_stuck_runs(db_session, now=started + dt.timedelta(seconds=4000))
    assert late["backtests"] == 1
    stored = _stored(db_session, BacktestRun, run.id)
    assert stored.status == "failed"
    assert stored.error_message == run_recovery.LOST_MESSAGE
    assert _audits(db_session)[0].payload_json["reason"] == "worker_lost"


def test_a_fresh_run_is_never_touched(db_session) -> None:
    """Everything a normal deployment looks like: work that is simply still young."""

    fresh_backtest = _backtest(db_session, progress=0, started_at=NOW - dt.timedelta(minutes=5))
    fresh_research = _research(
        db_session,
        status="queued",
        created_at=NOW - dt.timedelta(minutes=5),
        updated_at=NOW - dt.timedelta(minutes=5),
    )

    report = run_recovery.recover_stuck_runs(db_session, now=NOW)

    assert report == {"backtests": 0, "research": 0, "recovered": []}
    assert _stored(db_session, BacktestRun, fresh_backtest.id).status == "running"
    assert _stored(db_session, AIResearchRun, fresh_research.id).status == "queued"
    assert _audits(db_session) == []


def test_a_research_run_that_is_still_producing_is_not_reaped(db_session) -> None:
    """``created_at`` would condemn a run that queued long and only then started working.

    Two model calls can start minutes after the request, so the honest marker for a
    ``running`` research run is its last write, not when it was enqueued.
    """

    run = _research(
        db_session,
        status="running",
        created_at=NOW - dt.timedelta(hours=6),
        updated_at=NOW - dt.timedelta(seconds=30),
    )

    assert run_recovery.recover_stuck_runs(db_session, now=NOW)["research"] == 0
    assert _stored(db_session, AIResearchRun, run.id).status == "running"


def test_a_stalled_research_run_is_reaped_after_the_lost_budget(db_session) -> None:
    run = _research(
        db_session,
        status="running",
        created_at=NOW - dt.timedelta(hours=6),
        updated_at=NOW - dt.timedelta(seconds=4000),
    )

    report = run_recovery.recover_stuck_runs(db_session, now=NOW)

    assert report["research"] == 1
    stored = _stored(db_session, AIResearchRun, run.id)
    assert stored.status == "failed"
    assert stored.current_step == "failed"
    assert stored.error_message == run_recovery.LOST_MESSAGE
    # The two run tables do not share a name for "this run stopped here".
    assert stored.completed_at is not None
    assert _audits(db_session)[0].entity_type == "ai_research_run"
    assert _audits(db_session)[0].payload_json["reason"] == "worker_lost"


def test_a_queued_research_run_is_reaped_after_the_queue_budget(db_session) -> None:
    run = _research(
        db_session,
        status="queued",
        created_at=NOW - dt.timedelta(hours=3),
        updated_at=NOW - dt.timedelta(hours=3),
    )

    report = run_recovery.recover_stuck_runs(db_session, now=NOW)

    assert report["research"] == 1
    stored = _stored(db_session, AIResearchRun, run.id)
    assert stored.status == "failed"
    assert stored.error_message == run_recovery.QUEUED_MESSAGE
    assert stored.completed_at is not None
    assert _audits(db_session)[0].payload_json["reason"] == "never_started"


def test_the_reaper_never_resurrects(db_session) -> None:
    """A decided run is never matched, whatever its timestamps look like."""

    decided = {
        status: _backtest(
            db_session,
            status=status,
            progress=20,
            started_at=NOW - dt.timedelta(days=3),
            current_step=status,
        )
        for status in ("completed", "failed", "cancelled", "aborted")
    }
    research = _research(
        db_session,
        status="completed",
        created_at=NOW - dt.timedelta(days=3),
        updated_at=NOW - dt.timedelta(days=3),
    )

    report = run_recovery.recover_stuck_runs(db_session, now=NOW)

    assert report["backtests"] == 0
    assert report["research"] == 0
    for status, run in decided.items():
        stored = _stored(db_session, BacktestRun, run.id)
        assert stored.status == status
        assert stored.current_step == status
        assert stored.error_message is None
        assert stored.finished_at is None
    assert _stored(db_session, AIResearchRun, research.id).status == "completed"
    assert _audits(db_session) == []


def test_the_reaper_is_idempotent(db_session) -> None:
    """The second runner — Beat and the API's startup sweep — finds nothing to do."""

    run = _backtest(db_session, progress=20, started_at=NOW - dt.timedelta(hours=3))

    first = run_recovery.recover_stuck_runs(db_session, now=NOW)
    stored = _stored(db_session, BacktestRun, run.id)
    verdict = (stored.status, stored.error_message, stored.finished_at)

    second = run_recovery.recover_stuck_runs(db_session, now=NOW + dt.timedelta(minutes=5))

    assert first["backtests"] == 1
    assert second["backtests"] == 0
    assert second["recovered"] == []
    after = _stored(db_session, BacktestRun, run.id)
    assert (after.status, after.error_message, after.finished_at) == verdict
    # One recovery, one audit entry: the claim is the row's state, not a counter.
    assert len(_audits(db_session)) == 1


def test_the_recovered_run_is_not_revived_by_a_redelivered_task(db_session, monkeypatch) -> None:
    """The sweep and the queue both act on the same row; the task must yield.

    A redelivered message is exactly what late acknowledgement produces, so this is
    the ordering the queue creates on its own: the reaper fails the run, then a
    worker that never heard about it picks the message up.
    """

    from app.workers import tasks as worker_tasks

    run = _backtest(db_session, progress=20, started_at=NOW - dt.timedelta(hours=3))
    run_recovery.recover_stuck_runs(db_session, now=NOW)
    # The task opens a session of its own in production, so it reads the committed
    # state and never this session's snapshot of it.
    db_session.expire_all()
    monkeypatch.setattr(worker_tasks, "session_scope", lambda: _worker_scope(db_session))

    summary = worker_tasks.run_backtest(run.id)

    assert summary["skipped"] == "already decided"
    stored = _stored(db_session, BacktestRun, run.id)
    assert stored.status == "failed"
    # The verdict came from the reaper, not from a second, later execution: the
    # redelivery must not rewrite the reason with its own failure.
    assert stored.error_message == run_recovery.LOST_MESSAGE


def test_one_bad_record_does_not_stop_the_sweep(db_session, monkeypatch) -> None:
    """A row that cannot be recovered is left behind, and the rest still are (docs/33 §6.4)."""

    first = _backtest(db_session, progress=20, started_at=NOW - dt.timedelta(hours=3))
    second = _backtest(db_session, progress=20, started_at=NOW - dt.timedelta(hours=3))

    real = run_recovery.record_audit

    def _explode_on_the_first(db, **kwargs):
        if kwargs["entity_id"] == str(first.id):
            raise RuntimeError("audit log is unavailable")
        return real(db, **kwargs)

    monkeypatch.setattr(run_recovery, "record_audit", _explode_on_the_first)

    report = run_recovery.recover_stuck_runs(db_session, now=NOW)

    assert report["backtests"] == 1
    assert [entry["run_id"] for entry in report["recovered"]] == [second.id]
    # The whole row write was rolled back with its audit entry, so the next sweep
    # sees exactly the same stuck run instead of a half-recovered one.
    assert _stored(db_session, BacktestRun, first.id).status == "running"
    assert _stored(db_session, BacktestRun, second.id).status == "failed"


def test_a_second_runner_finding_a_reaped_row_writes_nothing(db_session) -> None:
    """The conditional ``UPDATE`` is the arbiter, so a losing runner matches zero rows."""

    run = _backtest(db_session, progress=20, started_at=NOW - dt.timedelta(hours=3))
    run_recovery.recover_stuck_runs(db_session, now=NOW)
    audits_before = len(_audits(db_session))

    # A runner that read the row before the winner committed still holds the old
    # snapshot: re-deciding from that snapshot must be a no-op.
    report = run_recovery.recover_stuck_runs(db_session, now=NOW + dt.timedelta(hours=1))

    assert report["backtests"] == 0
    assert report["recovered"] == []
    assert len(_audits(db_session)) == audits_before
    assert _stored(db_session, BacktestRun, run.id).status == "failed"


def test_the_startup_entry_point_uses_the_same_sweep(db_session, monkeypatch) -> None:
    """The API's startup path must not grow a second set of rules."""

    # This entry point takes no clock, so the row is placed relative to the real one.
    run = _backtest(
        db_session, progress=20, started_at=dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=1)
    )

    monkeypatch.setattr(run_recovery, "session_scope", lambda: _worker_scope(db_session))

    report = run_recovery.recover_stuck_runs_in_new_session()

    assert report["backtests"] == 1
    assert _stored(db_session, BacktestRun, run.id).status == "failed"


def test_a_startup_sweep_that_cannot_run_never_stops_the_api(monkeypatch) -> None:
    """Recovery may never be the reason the App refuses to serve (docs/33 §6.6)."""

    def _unreachable():
        raise RuntimeError("database is unreachable")

    monkeypatch.setattr(run_recovery, "session_scope", _unreachable)

    report = run_recovery.recover_stuck_runs_in_new_session()

    assert report["error"] is True
    assert report["recovered"] == []


def test_the_two_recovery_call_sites_share_one_implementation() -> None:
    """Beat's task and the API's startup path are wired to the same function."""

    import app.api.main as api_main
    from app.workers import tasks as worker_tasks

    assert (
        api_main.recover_stuck_runs_in_new_session is run_recovery.recover_stuck_runs_in_new_session
    )
    assert worker_tasks.recover_stuck_runs is run_recovery.recover_stuck_runs


def test_the_reaper_task_sweeps_in_its_own_session(db_session, monkeypatch) -> None:
    from app.workers import tasks as worker_tasks

    run = _backtest(
        db_session, progress=20, started_at=dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=1)
    )
    monkeypatch.setattr(worker_tasks, "session_scope", lambda: _worker_scope(db_session))

    report = worker_tasks.reap_stuck_runs()

    assert report["backtests"] == 1
    assert _stored(db_session, BacktestRun, run.id).status == "failed"
