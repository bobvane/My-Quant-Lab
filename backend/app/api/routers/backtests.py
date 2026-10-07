"""Backtest endpoints.

Every run stores: strategy version, dataset (series + hash), parameters, engine
version and feature version. Results are never overwritten.

The run itself is executed and persisted by :mod:`app.data.backtest_service`, which
``POST /experiments`` shares: an experiment that ran a backtest must leave behind exactly
the same artifact as ``POST /backtests`` does, not a second implementation of it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import BacktestCreate, BacktestOut, BacktestSummaryOut
from app.core.db import get_db
from app.data.backtest_service import (
    COMPARE_METRICS,
    BacktestFailed,
    BacktestRequestError,
    load_backtest_inputs,
    store_backtest,
)
from app.domain.models import BacktestRun

router = APIRouter(prefix="/backtests", tags=["backtests"])


@router.post("", response_model=BacktestOut, summary="Run a backtest")
def create_backtest(payload: BacktestCreate, db: Session = Depends(get_db)) -> BacktestOut:
    try:
        inputs = load_backtest_inputs(
            db,
            strategy_version_id=payload.strategy_version_id,
            symbol=payload.symbol,
            series_id=payload.series_id,
            timeframe=payload.timeframe,
            timeframe_explicit="timeframe" in payload.model_fields_set,
            start=payload.start,
            end=payload.end,
            execution_overrides=payload.execution_overrides,
        )
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
        raise HTTPException(status_code=409, detail="backtest has no result yet")
    trades = [
        {
            "direction": t.direction,
            "entry_time": t.entry_time,
            "entry_price": float(t.entry_price),
            "exit_time": t.exit_time,
            "exit_price": float(t.exit_price) if t.exit_price is not None else None,
            "quantity": float(t.quantity),
            "fees": float(t.fees),
            "slippage": float(t.slippage),
            "pnl": float(t.pnl) if t.pnl is not None else None,
            "pnl_pct": float(t.pnl_pct) if t.pnl_pct is not None else None,
            "r_multiple": float(t.r_multiple) if t.r_multiple is not None else None,
            "mae": float(t.mae) if t.mae is not None else None,
            "mfe": float(t.mfe) if t.mfe is not None else None,
            "exit_reason": t.exit_reason,
            "ambiguous_fill": t.ambiguous_fill,
        }
        for t in run.trades
    ]
    return _to_out(
        run,
        run.result.metrics_json,
        run.result.equity_curve_json,
        trades,
        list(run.result.warnings_json or []),
    )


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

    # Resource events are observational, safe to keep even after deletion.
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
    )


def _to_out(
    run: BacktestRun,
    metrics: dict[str, Any],
    equity_curve: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    warnings: list[str],
) -> BacktestOut:
    summary = _to_summary(run)
    return BacktestOut(
        **summary.model_dump(),
        metrics=metrics,
        equity_curve=equity_curve,
        trades=trades,
        result_hash=run.result.result_hash if run.result else "",
        parameters=run.parameters_json,
        execution_model=run.execution_model_json,
        warnings=warnings,
    )
