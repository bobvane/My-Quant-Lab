"""A backtest run is watchable: ``progress``/``current_step``, failures, and the handover.

Three things have to hold for a client that only holds a run id (ADR-180): the two columns
must move during the run, a failed run must be readable *without* a result row instead of
answering "no result yet", and turning ``BACKTEST_ASYNC`` on must hand the engine to the
worker while the synchronous answer — same status code, same shape — stays what it was.

The worker is exercised the way ``tests/test_research_async.py`` exercises the research
worker: as a plain function whose ``session_scope`` is the test session, so the task's own
process/transaction boundary is real without a broker.
"""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import select

from app.api.routers import backtests as backtests_router
from app.api.schemas import BacktestCreate, MarketDataSyncRequest, StrategyVersionCreate
from app.core.config import settings
from app.data import backtest_service
from app.domain.models import BacktestResult, BacktestRun
from app.workers import tasks as worker_tasks

API = "/api/v1"

DSL: dict = {
    "schema_version": "1.0",
    "strategy": {"id": "status-test", "name": "Status Test", "version": "1.0.0"},
    "market": {"asset_classes": ["stock"], "timeframes": ["1d"]},
    "entry": {"long": {"all": [{"op": "crosses_above", "left": "close", "right": "ema20"}]}},
    "exit": {"long": {"any": [{"op": "crosses_below", "left": "close", "right": "ema20"}]}},
    "risk": {"stop_loss_atr_multiple": 2.0, "take_profit_r_multiple": 2.0},
    "execution": {"fill_model": "next_bar_open", "fee_bps": 10, "slippage_bps": 5},
}


def _seed_version(client) -> dict:
    """A valid strategy version over a synced synthetic series — the API's own entry path."""

    client.post(
        f"{API}/market-data/sync", json=MarketDataSyncRequest(symbol="DEMO-AAPL").model_dump()
    )
    strategy = client.post(f"{API}/strategies", json={"name": "Watchable"}).json()
    return client.post(
        f"{API}/strategies/{strategy['id']}/versions",
        json=StrategyVersionCreate(version="1.0.0", dsl=DSL).model_dump(),
    ).json()


def _post_run(client, version: dict):
    return client.post(
        f"{API}/backtests",
        json=BacktestCreate(
            strategy_version_id=version["id"], symbol="DEMO-AAPL", timeframe="1d"
        ).model_dump(mode="json"),
    )


@contextmanager
def _worker_scope(db_session):
    """Stand in for ``session_scope`` so the task runs in the test's own transaction."""

    yield db_session


def test_a_synchronous_run_walks_the_ladder_and_finishes_on_it(client, db_session, monkeypatch):
    """The committed rungs are the evidence: a poller has to see movement, not one jump."""

    seen: list[tuple[str, int]] = []
    commit_rung = backtest_service.advance_backtest

    def spy(db, run, step: str) -> None:
        commit_rung(db, run, step)
        seen.append((step, run.progress))

    monkeypatch.setattr(backtest_service, "advance_backtest", spy)

    body = _post_run(client, _seed_version(client)).json()

    assert body["status"] == "completed"
    assert body["progress"] == 100
    assert body["current_step"] == "completed"
    # `running strategy` and `evaluating exits` are the engine's own phases: `run_backtest`
    # is one call, so a real run jumps from `computing features` to `computing metrics`.
    assert seen == [("computing features", 20), ("computing metrics", 90)]
    assert backtest_service.PROGRESS_LADDER == {
        "loading data": 5,
        "computing features": 20,
        "running strategy": 45,
        "evaluating exits": 70,
        "computing metrics": 90,
        "completed": 100,
    }

    db_session.expire_all()
    run = db_session.get(BacktestRun, body["id"])
    assert run.result is not None
    assert run.finished_at is not None
    assert run.error_message is None

    # (c) The run is readable, and the detail carries the same numbers as the POST.
    detail = client.get(f"{API}/backtests/{body['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["progress"] == 100
    assert detail.json()["current_step"] == "completed"
    assert detail.json()["result_hash"] == run.result.result_hash


def test_a_failed_run_is_readable_without_a_result(client, db_session, monkeypatch):
    """A failure is a state of the run, not a missing resource."""

    def boom(*args, **kwargs):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(backtest_service, "run_backtest", boom)
    response = _post_run(client, _seed_version(client))

    assert response.status_code == 500
    assert response.json()["detail"] == "backtest execution failed"

    db_session.expire_all()
    run = db_session.scalar(select(BacktestRun))
    assert run.status == "failed"
    assert run.error_message and "engine exploded" in run.error_message
    assert run.progress < 100
    assert run.finished_at is not None
    assert run.current_step == "failed"
    assert run.result is None
    assert db_session.scalars(select(BacktestResult)).all() == []

    # (c) The GET used to answer 409 here, which told a poller only that it was too early.
    detail = client.get(f"{API}/backtests/{run.id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "failed"
    assert body["progress"] == run.progress
    assert body["current_step"] == "failed"
    assert body["error_message"] == run.error_message
    assert body["metrics"] is None
    assert body["equity_curve"] is None
    assert body["result_hash"] is None

    # A genuinely missing run is still a 404: nothing to report is not a state.
    assert client.get(f"{API}/backtests/{run.id + 1000}").status_code == 404


def test_with_the_flag_on_the_worker_finishes_the_run(client, db_session, monkeypatch):
    """The handover is off by default, and this is what it does when it is on."""

    monkeypatch.setattr(settings, "backtest_async", True)
    handed_over: list[tuple] = []
    monkeypatch.setattr(
        backtests_router,
        "_enqueue_backtest",
        lambda run_id, timeframe, start, end: handed_over.append((run_id, timeframe, start, end)),
    )

    body = _post_run(client, _seed_version(client)).json()

    # (d) The POST answers with the run to watch, at the same status code as ever, and with
    # no result payload at all — an empty curve would read as a flat backtest.
    assert body["status"] in ("running", "queued")
    assert body["progress"] == 0
    assert body["current_step"] == "loading data"
    assert body.get("result") is None
    assert body["result_hash"] is None
    assert body["equity_curve"] is None
    assert handed_over == [(body["id"], "1d", None, None)]

    # The run is durable before it is enqueued, so a poll right now can see it.
    db_session.expire_all()
    assert db_session.get(BacktestRun, body["id"]).result is None
    queued = client.get(f"{API}/backtests/{body['id']}")
    assert queued.status_code == 200, queued.text
    assert queued.json()["current_step"] == "loading data"
    assert queued.json()["result_hash"] is None

    monkeypatch.setattr(worker_tasks, "session_scope", lambda: _worker_scope(db_session))
    result = worker_tasks.run_backtest(body["id"])
    assert result == {"run_id": body["id"], "status": "completed", "step": "completed"}

    db_session.expire_all()
    run = db_session.get(BacktestRun, body["id"])
    assert run.progress == 100
    assert run.result is not None
    finished = client.get(f"{API}/backtests/{body['id']}").json()
    assert finished["status"] == "completed"
    assert finished["progress"] == 100
    assert finished["current_step"] == "completed"
    assert finished["result_hash"] == run.result.result_hash

    # A duplicate delivery must not run the engine again, and must not add a second result.
    assert worker_tasks.run_backtest(body["id"]) == {
        "run_id": body["id"],
        "status": "completed",
        "skipped": "already decided",
    }
    db_session.expire_all()
    assert len(db_session.scalars(select(BacktestResult)).all()) == 1
