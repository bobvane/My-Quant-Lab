"""Backtest endpoints.

Every run stores: strategy version, dataset (series + hash), parameters, engine
version and feature version. Results are never overwritten.

The run itself is executed and persisted by :mod:`app.data.backtest_service`, which
``POST /experiments`` shares: an experiment that ran a backtest must leave behind exactly
the same artifact as ``POST /backtests`` does, not a second implementation of it.

With ``BACKTEST_ASYNC`` on (default off) this router only *prepares* the run and hands the
engine to ``quantlab.run_backtest``; the same service drives both halves, so the synchronous
answer and the watched one cannot drift apart (ADR-180).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import AnalysisOut, BacktestCreate, BacktestOut, BacktestSummaryOut
from app.core.config import settings
from app.core.db import get_db
from app.data.backtest_service import (
    COMPARE_METRICS,
    BacktestFailed,
    BacktestRequestError,
    analysis_for_run,
    load_backtest_inputs,
    prepare_backtest,
    store_backtest,
    trade_dicts,
)
from app.domain.models import BacktestRun

router = APIRouter(prefix="/backtests", tags=["backtests"])


@router.post("", response_model=BacktestOut, summary="Run a backtest")
def create_backtest(payload: BacktestCreate, db: Session = Depends(get_db)) -> BacktestOut:
    # Built once so both paths resolve the request identically: an async run must reject
    # the same requests a synchronous one rejects, with the same status and detail.
    lookup: dict[str, Any] = {
        "strategy_version_id": payload.strategy_version_id,
        "symbol": payload.symbol,
        "series_id": payload.series_id,
        "timeframe": payload.timeframe,
        "timeframe_explicit": "timeframe" in payload.model_fields_set,
        "start": payload.start,
        "end": payload.end,
        "execution_overrides": payload.execution_overrides,
    }

    if settings.backtest_async:
        # prepare_backtest commits the running row before its id is enqueued: the worker is
        # another process and would not find a row still inside this transaction. The answer
        # is therefore a run to watch (progress/current_step) rather than a result to wait
        # for, and the status code stays 200 — a client that only reads `status` is not
        # broken by being handed work in progress (ADR-180).
        try:
            run, _ = prepare_backtest(db, parameters=payload.parameters, **lookup)
        except BacktestRequestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        _enqueue_backtest(run.id, payload.timeframe, payload.start, payload.end)
        return _to_out(run, None, None, None, None)

    try:
        inputs = load_backtest_inputs(db, **lookup)
    except BacktestRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    try:
        execution = store_backtest(
            db, inputs, parameters=payload.parameters, timeframe=payload.timeframe
        )
    except BacktestFailed as exc:
        raise HTTPException(status_code=500, detail="backtest execution failed") from exc

    run, outcome = execution.run, execution.outcome
    return _to_out(run, outcome.metrics, outcome.equity_curve, outcome.trades, outcome.warnings)


@router.get("", response_model=list[BacktestSummaryOut], summary="List backtests")
def list_backtests(
    db: Session = Depends(get_db),
    strategy_version_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[BacktestSummaryOut]:
    stmt = select(BacktestRun)
    if strategy_version_id:
        stmt = stmt.where(BacktestRun.strategy_version_id == strategy_version_id)
    rows = db.scalars(stmt.order_by(BacktestRun.id.desc()).limit(limit).offset(offset)).all()
    return [_to_summary(row) for row in rows]


@router.get("/compare", summary="Compare several backtest runs side by side")
def compare_backtests(
    db: Session = Depends(get_db),
    ids: str = Query(..., description="Comma separated backtest run ids"),
    limit: int = Query(default=10, ge=2, le=20),
) -> dict[str, Any]:
    try:
        run_ids = [int(part) for part in ids.split(",") if part.strip()][:limit]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="ids must be comma separated integers") from exc
    if len(run_ids) < 2:
        raise HTTPException(status_code=422, detail="at least two backtest ids are required")

    runs = db.scalars(select(BacktestRun).where(BacktestRun.id.in_(run_ids))).all()
    metrics = COMPARE_METRICS
    rows = []
    for run in runs:
        summary = dict(run.result.summary_json) if run.result else {}
        rows.append(
            {
                "run_id": run.id,
                "strategy_version_id": run.strategy_version_id,
                "status": run.status,
                "result_hash": run.result.result_hash if run.result else None,
                **{k: summary.get(k) for k in metrics},
            }
        )
    return {"metrics": list(metrics), "runs": rows}


@router.get("/{run_id}", response_model=BacktestOut, summary="Get a backtest result")
def get_backtest(run_id: int, db: Session = Depends(get_db)) -> BacktestOut:
    run = db.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest run not found")
    if run.result is None:
        # A run that is still executing, or that failed, has no result row yet. This used to
        # be a 409, which told a poller only that it was too early; the run itself is the
        # answer — `status`, `progress` and `current_step` say what it is doing and
        # `error_message` says why it stopped. The result fields stay absent so a client
        # cannot mistake an empty curve for a flat backtest (ADR-180).
        return _to_out(run, None, None, None, None)
    return _to_out(
        run,
        run.result.metrics_json,
        run.result.equity_curve_json,
        trade_dicts(run),
        list(run.result.warnings_json or []),
    )


@router.get(
    "/{run_id}/analysis",
    response_model=AnalysisOut,
    summary="Performance, risk and buy-and-hold comparison of a backtest",
)
def get_backtest_analysis(run_id: int, db: Session = Depends(get_db)) -> AnalysisOut:
    """Derive the Phase C view of one run, without touching anything.

    Nothing is written and nothing is recomputed from market data: the per-bar equity
    curve the engine stored already carries the close of every bar, so the comparison
    is built from the same bars, the same window and the same calendar as the strategy.
    A market-data read happens only for a stored curve that has no usable closes.
    The assembly itself lives in :mod:`app.data.backtest_service` so the AI explanation
    reads the same analysis the API returns, not a second copy of it.
    """

    run = db.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest run not found")
    if run.status != "completed" or run.result is None:
        # An analysis of a run that has not finished (or that failed) would be a
        # statement about an unfinished experiment. `GET /backtests/{id}` still answers
        # with the run itself so a poller can watch it (ADR-180); this endpoint is only
        # meaningful once there is a result.
        raise HTTPException(status_code=409, detail="only a completed run can be analysed")

    return AnalysisOut.model_validate(analysis_for_run(db, run))


@router.get("/{run_id}/trades", summary="List trades of a backtest")
def list_trades(run_id: int, db: Session = Depends(get_db)) -> list[dict]:
    run = db.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest run not found")
    return [
        {
            "id": t.id,
            "direction": t.direction,
            "entry_time": t.entry_time,
            "entry_price": float(t.entry_price),
            "exit_time": t.exit_time,
            "exit_price": float(t.exit_price) if t.exit_price is not None else None,
            "pnl": float(t.pnl) if t.pnl is not None else None,
            "r_multiple": float(t.r_multiple) if t.r_multiple is not None else None,
            "mae": float(t.mae) if t.mae is not None else None,
            "mfe": float(t.mfe) if t.mfe is not None else None,
            "exit_reason": t.exit_reason,
            "ambiguous_fill": t.ambiguous_fill,
        }
        for t in run.trades
    ]


@router.delete("/{run_id}", summary="Delete a backtest run and its results")
def delete_backtest(run_id: int, db: Session = Depends(get_db)) -> dict:
    run = db.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest run not found")

    # The cascade on BacktestResult and BacktestTrade handles the rest.
    #
    # An experiment_result that points at this run is NOT cascade deleted (ADR-174):
    # the experiment keeps the historical record of which run it used.
    db.delete(run)

    from app.data.strategy_service import record_audit

    record_audit(
        db,
        event_type="backtest_deleted",
        entity_type="backtest_run",
        entity_id=str(run_id),
        action="delete",
        payload={"strategy_version_id": run.strategy_version_id},
    )
    db.commit()
    return {"deleted": run_id}


def _to_summary(run: BacktestRun) -> BacktestSummaryOut:
    summary = run.result.summary_json if run.result else {}
    # The series names the symbol and timeframe. They are read here so a caller can
    # tell whether two runs are comparable at all, not just whether they share a hash.
    series = run.dataset if run.dataset_version_id else None
    return BacktestSummaryOut(
        id=run.id,
        strategy_version_id=run.strategy_version_id,
        dataset_version_id=run.dataset_version_id,
        engine_version=run.engine_version,
        feature_version=run.feature_version,
        status=run.status,
        dataset_hash=run.dataset_hash,
        created_at=run.created_at,
        total_return=summary.get("total_return"),
        max_drawdown=summary.get("max_drawdown"),
        sharpe=summary.get("sharpe"),
        win_rate=summary.get("win_rate"),
        number_of_trades=summary.get("number_of_trades"),
        final_equity=summary.get("final_equity"),
        symbol=series.asset.symbol if series is not None and series.asset is not None else None,
        timeframe=series.timeframe if series is not None else summary.get("timeframe"),
        # The stored summary is the immutable record of what ran; the live series is
        # only a fallback for runs stored before these fields existed.
        dataset_version=summary.get("dataset_version")
        or (series.dataset_version if series is not None else None),
        source=summary.get("source")
        or (series.source.name if series is not None and series.source is not None else None),
        progress=run.progress,
        current_step=run.current_step,
        error_message=run.error_message,
    )


def _to_out(
    run: BacktestRun,
    metrics: dict[str, Any] | None,
    equity_curve: list[dict[str, Any]] | None,
    trades: list[dict[str, Any]] | None,
    warnings: list[str] | None,
) -> BacktestOut:
    summary = _to_summary(run)
    return BacktestOut(
        **summary.model_dump(),
        metrics=metrics,
        equity_curve=equity_curve,
        trades=trades,
        # No result row means no hash. An empty string would be a hash a client could
        # compare, and every run without a result would appear to share it (ADR-180).
        result_hash=run.result.result_hash if run.result else None,
        parameters=run.parameters_json,
        execution_model=run.execution_model_json,
        warnings=warnings or [],
    )


def _enqueue_backtest(
    run_id: int,
    timeframe: str,
    start: dt.datetime | None,
    end: dt.datetime | None,
) -> None:
    """Hand a prepared run to Celery.

    The import is inside the function on purpose: only a deployment that turns
    ``BACKTEST_ASYNC`` on needs the broker, and a test can replace this one seam instead of
    standing up a worker. The window travels as ISO strings because the task arguments are
    JSON (``app/workers/celery_app.py``) and a datetime is not.
    """

    from app.workers.tasks import run_backtest

    run_backtest.delay(
        run_id,
        timeframe=timeframe,
        start=start.isoformat() if start else None,
        end=end.isoformat() if end else None,
    )
