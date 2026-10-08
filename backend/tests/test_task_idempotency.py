"""Redelivery is normal now, so the business work behind each task must survive it.

v2.6.0 P0 turns on ``task_acks_late`` and ``task_reject_on_worker_lost``: a worker that is
killed while a task is in flight hands its message back to the broker, and the *same*
business work arrives a second time -- in a different process, with a different session,
against a database that already holds most of the first attempt's writes. That makes
"arrived twice" a supported path instead of an exception, and every task that owns durable
state has to answer for it:

* a redelivered research run reuses the hypothesis it already paid for;
* a redelivered backtest that lost a race to another attempt is reported as it really
  ended (``completed``), never rewritten as ``failed``;
* a signal notification is sent *at most once* -- losing one is better than sending two;
* one colliding row does not discard the symbols, sources or signals beside it.

HTTP is deliberately absent: the request path already had tests, and the promise above
lives one layer below it, in the task and service functions the broker calls.
"""

from __future__ import annotations

import contextlib
import datetime as dt

import pandas as pd

# The seeding helpers of the neighbouring suites are reused instead of copied: the run
# paths they build are the ones the API really takes, which is what makes a redelivery
# here the same event the broker would replay.
from research_payloads import (
    QUESTION,
    draft_payload,
    hypothesis_payload,
    make_provider,
    recorded_messages,
    variant,
)
from research_payloads import run_research as run_research_with_a_script
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from test_backtest_status import _post_run, _seed_version
from test_notifications import _enable, _patch_post, _seed_signal
from test_outcome_evaluator import _seed_signal_with_bars
from test_research_async import scripted_provider

from app.api.routers import backtests as backtests_router
from app.core.config import settings
from app.domain.models import (
    AIResearchRun,
    Asset,
    BacktestResult,
    BacktestRun,
    GitHubSource,
    MarketDataSeries,
    MarketDataSource,
    Signal,
    SignalOutcome,
    StrategyDraft,
    StrategyHypothesis,
    StrategyVersion,
)
from app.notifications.service import notify_pending_signals
from app.simulation import outcome_evaluator
from app.simulation.outcome_evaluator import evaluate_pending_outcomes
from app.workers import tasks as worker_tasks


@contextlib.contextmanager
def _worker_scope(db_session):
    """``session_scope`` as the worker sees it: the test's own session."""

    yield db_session


# --------------------------------------------------------------------------- #
# Backtests: a second delivery must not invent a failure
# --------------------------------------------------------------------------- #
def test_backtest_redelivery_after_completion_is_not_a_failure(client, db_session, monkeypatch):
    """The run finished; both ways its message can come back have to say so.

    A redelivery normally finds ``status == "completed"`` and stops at the guard. The
    narrow race is the one that used to hurt: two attempts were alive together, this one
    had already loaded the row as ``running``, and the unique key on
    ``backtest_results.backtest_run_id`` refuses its result. Losing a race is not a
    business failure, so the row is re-read and its real verdict reported.
    """

    version = _seed_version(client)
    monkeypatch.setattr(settings, "backtest_async", True)
    monkeypatch.setattr(backtests_router, "_enqueue_backtest", lambda *args, **kwargs: None)
    body = _post_run(client, version).json()
    monkeypatch.setattr(worker_tasks, "session_scope", lambda: _worker_scope(db_session))

    first = worker_tasks.run_backtest(body["id"])
    assert first == {"run_id": body["id"], "status": "completed", "step": "completed"}
    run_id = body["id"]

    # The ordinary redelivery: the guard decides before any work starts.
    again = worker_tasks.run_backtest(run_id)
    assert again["skipped"] == "already decided"
    assert len(db_session.scalars(select(BacktestResult)).all()) == 1

    # The race. This session had loaded the row while it was still running, and the other
    # attempt committed its ending after that: the core update is invisible to the object
    # already in the identity map, which is exactly the state the losing attempt holds.
    db_session.execute(
        update(BacktestRun)
        .where(BacktestRun.id == run_id)
        .values(status="running", progress=20, current_step="computing features")
        .execution_options(synchronize_session=False)
    )
    db_session.commit()
    db_session.expire_all()
    stale = db_session.get(BacktestRun, run_id)
    assert stale is not None and stale.status == "running"
    db_session.execute(
        update(BacktestRun)
        .where(BacktestRun.id == run_id)
        .values(status="completed", progress=100, current_step="completed")
        .execution_options(synchronize_session=False)
    )
    db_session.commit()
    assert stale.status == "running"  # the other attempt's commit is not visible here

    raced = worker_tasks.run_backtest(run_id)
    assert raced["skipped"] == "already completed"
    assert raced["status"] == "completed"

    db_session.expire_all()
    healed = db_session.get(BacktestRun, run_id)
    assert healed.status == "completed"
    assert healed.error_message is None  # a lost race is not a failed strategy
    # The losing attempt wrote rungs on its way in, and a rung commits: the winner's
    # ending is put back, so a finished run does not read as "computing features, 20%".
    assert healed.progress == 100
    assert healed.current_step == "completed"
    assert len(db_session.scalars(select(BacktestResult)).all()) == 1


# --------------------------------------------------------------------------- #
# Research: the hypothesis already paid for is reused
# --------------------------------------------------------------------------- #
def test_research_redelivery_reuses_the_hypothesis(db_session, monkeypatch):
    """A run killed between the two model calls must not buy the first one again.

    The first attempt is scripted to stop inside the architect step, which leaves the
    state a killed worker leaves: the hypothesis is committed, the draft is missing, the
    run still says ``running``. The redelivery is then fed *only* the architect's answer,
    so a pipeline that repeated the researcher would run out of script -- and the row
    count is the second witness, because storing a hypothesis always inserts one.
    """

    provider = make_provider(db_session)
    question = variant(QUESTION, "redelivery")

    # Delivery one: the researcher's answer is scripted and the architect's is not, so the
    # pipeline stops inside the architect step with the hypothesis already committed.
    run, _router = run_research_with_a_script(
        db_session, provider, [hypothesis_payload()], question=question
    )
    db_session.expire_all()
    run = db_session.get(AIResearchRun, run.id)
    assert run is not None
    paid_hypothesis = run.hypothesis_id
    assert paid_hypothesis is not None
    assert run.draft_id is None
    assert run.current_step == "architect"

    # A worker killed by the OS writes no verdict, and an in-process test cannot kill
    # itself; the state that kill leaves is restored instead -- the last commit's state:
    # running, mid-architect, hypothesis paid for, no draft. A redelivery that reused the
    # row above would still have to pay for the researcher's answer again.
    run.status = "running"
    run.current_step = "architect"
    run.error_message = None
    run.completed_at = None
    db_session.commit()

    monkeypatch.setattr(worker_tasks, "session_scope", lambda: _worker_scope(db_session))
    with scripted_provider(monkeypatch, [draft_payload()]):
        summary = worker_tasks.run_research(run.id)

    assert summary == {"run_id": run.id, "status": "completed", "step": "completed"}
    assert len(recorded_messages()) == 1  # only the architect asked the model again

    db_session.expire_all()
    stored = db_session.get(AIResearchRun, run.id)
    assert stored.hypothesis_id == paid_hypothesis  # the same row, not a new one
    assert stored.draft_id is not None
    assert len(db_session.scalars(select(StrategyHypothesis)).all()) == 1
    assert len(db_session.scalars(select(StrategyDraft)).all()) == 1


# --------------------------------------------------------------------------- #
# Notifications: at most once
# --------------------------------------------------------------------------- #
def test_two_notifiers_do_not_double_send(db_session, monkeypatch):
    """A claim that is committed before the send is what makes "once" hold.

    Two notifiers can race, and one of them can die between claiming a signal and handing
    it to the provider. Nothing can know whether the far end saw that send, so the durable
    claim wins: the signal is never a candidate again, and the cost of being wrong is a
    missing notification rather than a duplicate one.
    """

    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=200)
    _enable(db_session)
    signal = _seed_signal(db_session)

    # The other notifier claimed the signal and then died before sending: claiming is a
    # commit, so the state it left behind is exactly this.
    signal.notified_at = dt.datetime(2026, 1, 2, 0, 0, tzinfo=dt.UTC)
    db_session.commit()

    result = notify_pending_signals(db_session)
    assert result["sent"] == 0
    assert result["candidates"] == 0
    assert calls == []  # at most once: none, never two


def test_a_failed_send_releases_the_claim_so_a_retry_can_send(db_session, monkeypatch):
    """A send that never reached the provider is not a delivery.

    The claim is only the lock; a failure that is *known* has to give it back, or the
    signal would be lost for a reason nobody can see. Here the first tick fails at the
    provider and the second, with the provider healthy, delivers -- the same signal, once.
    """

    calls: list[dict] = []
    _patch_post(monkeypatch, calls, status=500)
    _enable(db_session)
    signal = _seed_signal(db_session)

    failed = notify_pending_signals(db_session)
    assert failed["sent"] == 0
    assert failed["failed"] == 1
    db_session.refresh(signal)
    assert signal.notified_at is None  # released, so the retry below is legitimate

    _patch_post(monkeypatch, calls, status=200)
    assert notify_pending_signals(db_session)["sent"] == 1
    assert len(calls) == 2


# --------------------------------------------------------------------------- #
# Market data: one colliding symbol does not cost the others
# --------------------------------------------------------------------------- #
class _FixedFrameProvider:
    """The smallest provider ``sync_market_data`` accepts: a name and one frame."""

    name = "fake-sync"

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def get_ohlcv(self, *args, **kwargs) -> pd.DataFrame:
        return self._frame


def test_sync_market_data_survives_a_conflicting_row(db_session, monkeypatch, sample_bars):
    """The PK on ``(series_id, timestamp)`` is what makes a redelivery collide.

    Bars are written per symbol, and one symbol is one savepoint, so a collision has to
    cost that symbol and nothing else: not the transaction the caller committed into,
    and not the symbols queued behind it. The colliding symbol is therefore put *first*
    -- if the failure ended the loop, the symbol after it would never be written.
    """

    assets: dict[str, Asset] = {}
    for symbol in ("AAA", "BBB"):
        asset = Asset(symbol=symbol, asset_class="stock")
        db_session.add(asset)
        assets[symbol] = asset
    db_session.commit()

    provider = _FixedFrameProvider(sample_bars)
    real_upsert = worker_tasks.upsert_bars

    def _collide_or_write(db, series, bars):
        if series.asset_id == assets["BBB"].id:
            raise IntegrityError("INSERT INTO market_bars", {}, Exception("duplicate key"))
        return real_upsert(db, series, bars)

    monkeypatch.setattr(worker_tasks, "get_market_data_provider", lambda: provider)
    monkeypatch.setattr(worker_tasks, "resolve_watchlist", lambda provider, symbols: ["BBB", "AAA"])
    monkeypatch.setattr(worker_tasks, "upsert_bars", _collide_or_write)
    monkeypatch.setattr(worker_tasks, "session_scope", lambda: _worker_scope(db_session))

    result = worker_tasks.sync_market_data()

    assert result["provider"] == "fake-sync"
    assert result["inserted"]["BBB"] == -1
    # BBB collided first and AAA was still synced, so the savepoint both contained the
    # failure and left the session able to carry on.
    assert result["inserted"]["AAA"] > 0
    db_session.expire_all()
    # The colliding symbol was rolled back on its own -- its series is gone with the bars
    # that could not be written -- while the source and the symbol behind it survived.
    assert db_session.scalars(select(MarketDataSource)).all()
    surviving = db_session.scalars(select(MarketDataSeries)).all()
    assert [series.asset_id for series in surviving] == [assets["AAA"].id]


# --------------------------------------------------------------------------- #
# Signal outcomes: the unique key decides, not the batch
# --------------------------------------------------------------------------- #
def test_evaluate_signal_outcomes_survives_a_duplicate_outcome(db_session, monkeypatch):
    """A concurrent writer that stored the same outcome must not fail this batch.

    The pending query cannot see the other worker's row yet, so the collision is real and
    expected; the row is a duplicate, which is the one case that needs no repair. Two
    signals share one series here, so the duplicate has to cost one signal's *insert* and
    nothing else: the batch counts the loss and still evaluates the signal beside it.
    """

    first_id, bars, _series = _seed_signal_with_bars(db_session)
    db_session.flush()
    first = db_session.get(Signal, first_id)
    assert first is not None
    # A second signal on the same series, evaluated in the same batch. It has to come from
    # another strategy version: `signals` is unique on
    # (strategy_version_id, asset_id, timeframe, bar_timestamp), and a later timestamp
    # would leave too few bars behind it to evaluate.
    version = db_session.get(StrategyVersion, first.strategy_version_id)
    assert version is not None
    other_version = StrategyVersion(
        strategy_id=version.strategy_id, version="2.0.0", dsl_json={}, immutable_hash="y" * 64
    )
    db_session.add(other_version)
    db_session.flush()
    second = Signal(
        strategy_version_id=other_version.id,
        asset_id=first.asset_id,
        timeframe=first.timeframe,
        bar_timestamp=first.bar_timestamp,
        state=first.state,
        direction=first.direction,
        price_reference=first.price_reference,
        feature_snapshot_hash="s" * 64,
        data_source=first.data_source,
    )
    db_session.add(second)
    db_session.commit()

    injected = False

    def _load_with_a_racing_writer(db, series, **kwargs):
        nonlocal injected
        # Another worker evaluated the first signal while this one was reading its bars.
        if not injected:
            injected = True
            db.add(
                SignalOutcome(
                    signal_id=first_id,
                    outcome_state="profitable",
                    entry_time=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                    exit_time=dt.datetime(2026, 1, 11, tzinfo=dt.UTC),
                    entry_price=100.0,
                    exit_price=108.0,
                    pnl_pct=0.08,
                    mae=0.03,
                    mfe=0.10,
                    evaluated_at=dt.datetime(2026, 1, 12, tzinfo=dt.UTC),
                    notes="evaluated by the other worker",
                )
            )
            db.flush()
        return bars

    monkeypatch.setattr(outcome_evaluator, "load_bars", _load_with_a_racing_writer)

    result = evaluate_pending_outcomes(db_session, bars_after=10)

    assert result["skipped"] == 1  # the signal the other worker already stored
    assert result["evaluated"] == 1  # its neighbour was still evaluated
    db_session.expire_all()
    outcomes = db_session.scalars(select(SignalOutcome).order_by(SignalOutcome.id)).all()
    assert len(outcomes) == 2
    # The racing writer's row is the one that was kept for the first signal.
    kept = next(outcome for outcome in outcomes if outcome.signal_id == first_id)
    assert kept.notes.startswith("evaluated by")
    assert any(outcome.signal_id == second.id for outcome in outcomes)


# --------------------------------------------------------------------------- #
# GitHub watch: per source, rerunnable
# --------------------------------------------------------------------------- #
def test_github_source_check_reruns_safely(db_session, monkeypatch):
    """A source that collides is its own problem.

    ``check_source`` compares against ``current_commit`` and writes; a redelivery of the
    same sweep can therefore collide. The sweep must still account for every watched
    source, and one source's failure must not roll back the work of the ones beside it.
    """

    sources: list[GitHubSource] = []
    for tag in ("one", "two"):
        source = GitHubSource(repository_url=f"https://github.com/bobvane/{tag}", is_watched=True)
        db_session.add(source)
        sources.append(source)
    db_session.commit()

    def _check_source(db, source):
        if source.id == sources[0].id:
            raise IntegrityError("INSERT INTO github_snapshots", {}, Exception("duplicate key"))
        return "unchanged"

    monkeypatch.setattr(worker_tasks, "check_source", _check_source)
    monkeypatch.setattr(worker_tasks, "session_scope", lambda: _worker_scope(db_session))

    result = worker_tasks.check_github_sources()

    assert result["checked"] == 2
    assert result["error"] == 1
    assert result["unchanged"] == 1
    # The session is still usable and the watched sources are untouched by the failure.
    assert len(db_session.scalars(select(GitHubSource)).all()) == 2
