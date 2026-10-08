"""Stuck-run recovery: stop a run whose worker is gone from claiming to be running.

A backtest or a research run is a durable row that another process moves forward.
If that process dies, the row stays ``running`` for ever: nothing else owns it, so
nothing else notices. With late acknowledgement (``workers/celery_app.py``) a lost
task is normally redelivered and the run finishes or fails on its own; this module
is the net for the case where the *message* is gone too, and it is also what
repairs the very common case immediately: an App restart with runs in flight.

The rules (docs/33 §6.4) are conditional ``UPDATE … WHERE`` statements, one pair
per run type. That is what makes recovery idempotent and concurrency-safe without
a lock: two runners racing the same row both issue the statement, and the second
matches zero rows. A row that already reached a terminal state is never matched at
all, so the reaper can never resurrect a decided run.

Every threshold is measured against a *durable* time marker:

* a backtest still at ``progress = 0`` was committed by the API and never touched
  by a worker, so ``started_at`` (the enqueue time, see
  :func:`app.data.backtest_service.prepare_backtest`) answers "how long has this
  been waiting";
* a backtest past ``progress = 0`` was started by a worker;
* a research run at ``status = "queued"`` was never picked up (``created_at`` is
  the enqueue time), and one at ``status = "running"`` is in flight — for that one
  ``created_at`` is *not* usable, because a run can queue for a long time before
  its first model call, so the last write (``updated_at``, kept fresh by
  ``TimestampMixin``) is the honest marker. One AI call is bounded by
  ``ai_task_timeout_seconds`` (600 s), far below the recovery budget.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import session_scope
from app.data.strategy_service import record_audit
from app.domain.models import AIResearchRun, BacktestRun

logger = logging.getLogger(__name__)

__all__ = ["recover_stuck_runs", "recover_stuck_runs_in_new_session"]

#: Written to ``error_message``, so it has to tell a human what happened and that
#: the strategy was not the problem -- the execution of the run was.
LOST_MESSAGE = "worker lost while this run was in flight; start a new run"
QUEUED_MESSAGE = "never picked up by a worker; the queue message was lost"

#: Audit vocabulary. One event type with a ``reason`` in the payload keeps the
#: audit surface greppable without inventing a word per run type.
AUDIT_EVENT = "run_recovered"

#: Bound on one sweep. A backlog larger than this is drained by the next tick
#: rather than held in one transaction.
BATCH_LIMIT = 200


def _cutoff(now: dt.datetime, seconds: int) -> dt.datetime:
    return now - dt.timedelta(seconds=seconds)


def _reap(
    db: Session,
    model: Any,
    *,
    entity_type: str,
    reason: str,
    message: str,
    conditions: list[Any],
    now: dt.datetime,
    terminal_field: str = "finished_at",
) -> list[dict[str, Any]]:
    """Fail every matching row, one conditional ``UPDATE`` at a time.

    Per row rather than one bulk statement so that a single row's problem can
    never roll back the rest of the sweep, and so that the audit entry can name
    the run it belongs to.

    ``terminal_field`` is the column that records "this run stopped here": the two
    run tables do not share a name for it (``BacktestRun.finished_at``,
    ``AIResearchRun.completed_at``), and the sweep must not invent one.
    """

    recovered: list[dict[str, Any]] = []
    run_ids = db.scalars(
        select(model.id).where(*conditions).order_by(model.id).limit(BATCH_LIMIT)
    ).all()
    for run_id in run_ids:
        # Each row is committed on its own and every failure is contained here:
        # a row that cannot be recovered -- a lock that will not clear, an audit
        # write that trips a constraint -- must not cost the rest of the sweep,
        # which is what an unguarded loop would do because the session is shared.
        try:
            # The same conditions are repeated in the UPDATE: between the SELECT
            # and here another runner (or the redelivered task itself) may have
            # moved the row, and the row's current state, not our snapshot, is
            # what decides.
            #
            # ``synchronize_session=False``: the row's state is the database's
            # answer, not the ORM's. Letting SQLAlchemy reconcile the identity map
            # means it re-evaluates the criteria in Python, and comparing the
            # stored ``started_at`` with the cutoff raises on a dialect that
            # returns naive timestamps (every SQLite test would take the
            # per-row failure path below and recover nothing).
            result = db.execute(
                update(model)
                .where(model.id == run_id, *conditions)
                .values(
                    status="failed",
                    current_step="failed",
                    error_message=message,
                    **{terminal_field: now},
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                db.rollback()
                logger.info("run %s: %s no longer applies; left alone", run_id, reason)
                continue
            record_audit(
                db,
                event_type=AUDIT_EVENT,
                entity_type=entity_type,
                entity_id=str(run_id),
                action="recover",
                payload={"reason": reason, "message": message},
            )
            db.commit()
        except Exception:  # noqa: BLE001 - one row may never stop the sweep
            db.rollback()
            logger.exception(
                "could not recover %s %s; leaving it for the next sweep",
                entity_type,
                run_id,
            )
            continue
        logger.warning("recovered %s %s: %s", entity_type, run_id, reason)
        recovered.append({"run_id": run_id, "reason": reason})
    return recovered


def recover_stuck_runs(db: Session, *, now: dt.datetime | None = None) -> dict[str, Any]:
    """Fail every run that is demonstrably not running, and report what was done.

    Returns ``{"backtests": N, "research": M, "recovered": [...]}``; an empty
    sweep is the normal, quiet case.
    """

    moment = now or dt.datetime.now(tz=dt.UTC)
    lost_before = _cutoff(moment, settings.run_recovery_lost_after_seconds)
    queued_before = _cutoff(moment, settings.run_recovery_queued_after_seconds)

    backtests = _reap(
        db,
        BacktestRun,
        entity_type="backtest_run",
        reason="worker_lost",
        message=LOST_MESSAGE,
        conditions=[
            BacktestRun.status == "running",
            BacktestRun.progress > 0,
            BacktestRun.started_at.is_not(None),
            BacktestRun.started_at < lost_before,
        ],
        now=moment,
    )
    backtests += _reap(
        db,
        BacktestRun,
        entity_type="backtest_run",
        reason="never_started",
        message=QUEUED_MESSAGE,
        conditions=[
            BacktestRun.status == "running",
            BacktestRun.progress == 0,
            BacktestRun.started_at.is_not(None),
            BacktestRun.started_at < queued_before,
        ],
        now=moment,
    )
    research = _reap(
        db,
        AIResearchRun,
        entity_type="ai_research_run",
        reason="worker_lost",
        message=LOST_MESSAGE,
        conditions=[
            AIResearchRun.status == "running",
            AIResearchRun.updated_at < lost_before,
        ],
        now=moment,
        terminal_field="completed_at",
    )
    research += _reap(
        db,
        AIResearchRun,
        entity_type="ai_research_run",
        reason="never_started",
        message=QUEUED_MESSAGE,
        conditions=[
            AIResearchRun.status == "queued",
            AIResearchRun.created_at < queued_before,
        ],
        now=moment,
        terminal_field="completed_at",
    )
    recovered = backtests + research
    if recovered:
        logger.warning("stuck-run recovery failed %d run(s)", len(recovered))
    return {"backtests": len(backtests), "research": len(research), "recovered": recovered}


def recover_stuck_runs_in_new_session() -> dict[str, Any]:
    """Run one sweep in a session of its own, absorbing every failure.

    This is the entry point for callers that must not be taken down by recovery:
    the API's startup path. A sweep that cannot reach the database is a logged
    warning, never an exception that stops the API from serving.
    """

    try:
        with session_scope() as db:
            return recover_stuck_runs(db)
    except Exception:  # noqa: BLE001 - recovery may never break its caller
        logger.exception("stuck-run recovery sweep failed; continuing without it")
        return {"backtests": 0, "research": 0, "recovered": [], "error": True}
