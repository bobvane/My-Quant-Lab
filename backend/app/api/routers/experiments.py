"""Strategy experiment endpoints (docs/25, ADR-174 / ADR-182 / ADR-183).

An experiment is the persisted business object around a research run: it records what was
asked for (``request_json``), what was executed (``parameters_json`` and the frozen
configuration columns), and one ``experiment_results`` row per produced result -- so a
parameter sweep keeps its parameter/result pairs, and a single-shot run keeps the link to
the ``BacktestRun`` it created. Reading an experiment never re-runs a quant algorithm;
every number returned here was stored by the engine that computed it.

This module is only the HTTP shape. The implementation lives in
``app.data.experiment_service``: it resolves and validates the request before writing
anything, runs the kind's engine, and raises the ``HTTPException`` this layer maps.
Experiments execute synchronously inside the request, exactly like ``POST /backtests``:
no worker, no queue, no new dependency. The trade-off is that a long sweep occupies the
request; the benefit is that the response and the persisted row can never disagree.

Five hard rules still hold: nothing here is a trading endpoint, the AI layer is not
involved, and no quant value is computed outside ``app.research``.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.schemas import (
    ExperimentAdoptRequest,
    ExperimentCompareOut,
    ExperimentCreate,
    ExperimentDetailOut,
    ExperimentListOut,
    ExperimentUpdate,
)
from app.core.db import get_db
from app.data import experiment_service
from app.data.strategy_service import record_audit
from app.domain.models import PaperAccount, StrategyExperiment

router = APIRouter(prefix="/experiments", tags=["experiments"])

# The lifecycle a stored experiment can be in. Declared as a Literal so an unknown filter
# value is rejected by the same validation layer as every other bad parameter.
ExperimentStatus = Literal["draft", "running", "completed", "failed", "archived"]


def _load(db: Session, experiment_id: int) -> StrategyExperiment:
    """The stored experiment, or 404. No engine is involved in reading it."""

    experiment = db.scalars(
        select(StrategyExperiment).where(StrategyExperiment.id == experiment_id)
    ).one_or_none()
    if experiment is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    return experiment


@router.post(
    "", response_model=ExperimentDetailOut, status_code=201, summary="Create an experiment"
)
def create_experiment(
    payload: ExperimentCreate, db: Session = Depends(get_db)
) -> ExperimentDetailOut:
    """Create an experiment: either a stored draft, or a run executed now.

    With ``draft=true`` the validated request is frozen in a ``draft`` row and nothing is
    executed; ``POST /experiments/{id}/run`` runs it later. Without it the behaviour is
    unchanged: execute synchronously and answer 201 with the stored outcome.
    """

    experiment = experiment_service.create_experiment(db, payload)
    return experiment_service.experiment_detail(db, experiment)


@router.get("", response_model=ExperimentListOut, summary="List strategy experiments")
def list_experiments(
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=200),
    strategy_version_id: int | None = None,
    status: ExperimentStatus | None = Query(
        default=None, description="Only experiments in this lifecycle state"
    ),
    kind: str | None = Query(default=None, description="Only experiments of this kind"),
) -> ExperimentListOut:
    """The experiment history, newest first.

    Archived experiments are included by default -- archival hides a row from the active
    work, not from the record -- so a client that wants only live work asks for the
    statuses it wants.
    """

    stmt = select(StrategyExperiment)
    if strategy_version_id:
        stmt = stmt.where(StrategyExperiment.strategy_version_id == strategy_version_id)
    if status:
        stmt = stmt.where(StrategyExperiment.status == status)
    if kind:
        stmt = stmt.where(StrategyExperiment.kind == kind)
    rows = db.scalars(stmt.order_by(StrategyExperiment.id.desc()).limit(limit)).all()
    return ExperimentListOut(
        experiments=[experiment_service.experiment_summary(db, row) for row in rows]
    )


@router.get("/compare", response_model=ExperimentCompareOut, summary="Compare experiments")
def compare_experiments(
    db: Session = Depends(get_db),
    ids: list[str] = Query(
        ...,
        description="Experiment ids; repeat the parameter (?ids=1&ids=2) or comma separate them",
    ),
    limit: int = Query(default=10, ge=2, le=20),
) -> ExperimentCompareOut:
    """Line up stored experiments side by side.

    A pure projection of what is stored: the numbers come out of each experiment's
    ``summary_json`` exactly as the engine left them, plus the configuration they were
    produced with and whether the compared rows are actually comparable.
    """

    return experiment_service.compare_experiments(db, ids, limit=limit)


@router.post(
    "/from-backtest/{run_id}",
    response_model=ExperimentDetailOut,
    status_code=201,
    summary="Adopt a finished backtest run as an experiment",
)
def adopt_backtest_run(
    run_id: int,
    payload: ExperimentAdoptRequest | None = None,
    db: Session = Depends(get_db),
) -> ExperimentDetailOut:
    """Turn an already-finished backtest into an experiment without re-running it.

    The run's stored parameters, metrics, summary and reproduction hashes are copied
    verbatim; nothing is recomputed. A run can be adopted once -- a second attempt answers
    409 naming the experiment that already holds it.
    """

    experiment = experiment_service.adopt_backtest_run(
        db,
        run_id,
        name=payload.name if payload is not None else None,
        notes=payload.notes if payload is not None else None,
    )
    return experiment_service.experiment_detail(db, experiment)


@router.get("/{experiment_id}", response_model=ExperimentDetailOut, summary="Get an experiment")
def get_experiment(experiment_id: int, db: Session = Depends(get_db)) -> ExperimentDetailOut:
    """The experiment and its stored results, readable long after the POST returned."""

    return experiment_service.experiment_detail(db, _load(db, experiment_id))


@router.post(
    "/{experiment_id}/run",
    response_model=ExperimentDetailOut,
    summary="Run a stored draft experiment",
)
def run_experiment(experiment_id: int, db: Session = Depends(get_db)) -> ExperimentDetailOut:
    """Execute the request a draft (or a previously failed experiment) froze.

    The stored request is replayed, so a draft is reproducible by construction. Running
    anything that is not a draft or a failure answers 409: a completed experiment already
    has its numbers, and re-running it would replace them with a second set.
    """

    experiment = _load(db, experiment_id)
    experiment_service.run_experiment(db, experiment)
    return experiment_service.experiment_detail(db, experiment)


@router.post(
    "/{experiment_id}/archive",
    response_model=ExperimentDetailOut,
    summary="Archive an experiment",
)
def archive_experiment(experiment_id: int, db: Session = Depends(get_db)) -> ExperimentDetailOut:
    """Retire an experiment from the active history without deleting it.

    The row and its results stay readable, and the archive timestamp records when it was
    retired. A running experiment cannot be archived (409), and neither can one that is
    already archived.
    """

    experiment = _load(db, experiment_id)
    experiment_service.archive_experiment(db, experiment)
    return experiment_service.experiment_detail(db, experiment)


@router.patch(
    "/{experiment_id}", response_model=ExperimentDetailOut, summary="Update an experiment"
)
def update_experiment(
    experiment_id: int, payload: ExperimentUpdate, db: Session = Depends(get_db)
) -> ExperimentDetailOut:
    """Rename an experiment or rewrite its notes.

    Only the two human-facing fields are editable: the stored request and results are
    what ran, and rewriting them would make the record disagree with the engine.
    """

    experiment = _load(db, experiment_id)
    experiment_service.update_experiment(db, experiment, name=payload.name, notes=payload.notes)
    return experiment_service.experiment_detail(db, experiment)


@router.delete("/{experiment_id}", status_code=204, summary="Delete an experiment and its results")
def delete_experiment(experiment_id: int, db: Session = Depends(get_db)) -> Response:
    """Delete the experiment; the ``BacktestRun`` it produced is its own artifact.

    A paper account opened from this experiment keeps standing -- deleting an
    experiment must not delete an account, and it must not leave a reference to a row
    that is gone: the account's ``experiment_id`` is cleared here, so it reads as
    "never recorded" instead of pointing at a hole (ADR-209). The foreign key's
    ``ON DELETE SET NULL`` is the backstop in PostgreSQL; SQLite (tests) does not
    enforce it, so the honest behaviour must not depend on the dialect.
    """

    experiment = _load(db, experiment_id)
    kind = experiment.kind
    result_count = len(experiment.results)
    db.execute(
        update(PaperAccount)
        .where(PaperAccount.experiment_id == experiment_id)
        .values(experiment_id=None)
    )
    db.delete(experiment)
    record_audit(
        db,
        event_type="experiment_deleted",
        entity_type="strategy_experiment",
        entity_id=str(experiment_id),
        action="delete",
        payload={"kind": kind, "result_count": result_count},
    )
    db.commit()
    return Response(status_code=204)
