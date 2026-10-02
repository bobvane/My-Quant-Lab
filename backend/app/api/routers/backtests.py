"""Backtest endpoints.

Every run stores: strategy version, dataset (series + hash), parameters, engine
version and feature version. Results are never overwritten.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import BacktestCreate, BacktestOut, BacktestSummaryOut
from app.core.db import get_db
from app.data.market_data_repo import load_bars, series_content_hash
from app.data.strategy_service import load_spec, record_audit
from app.domain.models import (
    Asset,
    BacktestMetric,
    BacktestResult,
    BacktestRun,
    BacktestTrade,
    MarketDataSeries,
    StrategyVersion,
)
from app.research.engine import run_backtest
from app.strategies.dsl import merge_spec_overrides

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/backtests", tags=["backtests"])


def _resolve_series(db: Session, payload: BacktestCreate) -> MarketDataSeries:
    if payload.series_id:
        series = db.get(MarketDataSeries, payload.series_id)
        if series is None:
            raise HTTPException(status_code=404, detail="series not found")
        return series
    if not payload.symbol:
        raise HTTPException(status_code=400, detail="either series_id or symbol is required")
    asset = db.scalar(select(Asset).where(Asset.symbol == payload.symbol))
    if asset is None:
        raise HTTPException(status_code=404, detail=f"asset '{payload.symbol}' not found")
    series = db.scalar(
        select(MarketDataSeries).where(
            MarketDataSeries.asset_id == asset.id,
            MarketDataSeries.timeframe == payload.timeframe,
        )
    )
    if series is None:
        raise HTTPException(
            status_code=404,
            detail=f"no market data for '{payload.symbol}' {payload.timeframe}; sync first",
        )
    return series


@router.post("", response_model=BacktestOut, summary="Run a backtest")
def create_backtest(payload: BacktestCreate, db: Session = Depends(get_db)) -> BacktestOut:
    strategy_version = db.get(StrategyVersion, payload.strategy_version_id)
    if strategy_version is None:
        raise HTTPException(status_code=404, detail="strategy version not found")
    if strategy_version.validation_status != "valid":
        raise HTTPException(
            status_code=422,
            detail=f"strategy version is '{strategy_version.validation_status}', not 'valid'",
        )

    series = _resolve_series(db, payload)
    frame = load_bars(db, series, start=payload.start, end=payload.end, only_closed=True)
    if len(frame) < 60:
        raise HTTPException(
            status_code=422,
            detail=f"need at least 60 closed bars, series has {len(frame)}",
        )

    spec = load_spec(strategy_version)
    if payload.execution_overrides:
        # merge_spec_overrides (not model_copy) so a nested override such as
        # {"sizing": {...}} is validated instead of silently kept as a dict.
        spec = merge_spec_overrides(spec, {"execution": payload.execution_overrides})

    dataset_hash = series_content_hash(frame)

    run = BacktestRun(
        strategy_version_id=strategy_version.id,
        dataset_version_id=series.id,
        engine_version="1.0.0",
        feature_version="pending",
        parameters_json=payload.parameters,
        execution_model_json=spec.execution.model_dump(),
        dataset_hash=dataset_hash,
        status="running",
        started_at=dt.datetime.now(tz=dt.UTC),
    )
    db.add(run)
    db.flush()

    try:
        outcome = run_backtest(
            spec,
            frame,
            strategy_version=f"{strategy_version.strategy_id}@{strategy_version.version}",
            timeframe=payload.timeframe,
            parameters=payload.parameters,
        )
    except Exception as exc:  # pragma: no cover - defensive
        run.status = "failed"
        run.error_message = str(exc)[:500]
        run.finished_at = dt.datetime.now(tz=dt.UTC)
        db.commit()
        logger.exception("backtest failed")
        raise HTTPException(status_code=500, detail="backtest execution failed") from exc

    run.feature_version = outcome.feature_version
    run.status = "completed"
    run.finished_at = dt.datetime.now(tz=dt.UTC)

    result = BacktestResult(
        backtest_run_id=run.id,
        summary_json=_summary(outcome, series),
        equity_curve_json=outcome.equity_curve,
        metrics_json=outcome.metrics,
        result_hash=outcome.result_hash,
    )
    db.add(result)
    db.flush()

    for group, name, value in _metric_rows(outcome.metrics):
        db.add(
            BacktestMetric(
                backtest_result_id=result.id,
                metric_group=group,
                metric_name=name,
                metric_value=value,
                is_available=value is not None,
                metric_text=None if value is not None else "N/A",
            )
        )

    for trade in outcome.trades:
        db.add(
            BacktestTrade(
                backtest_run_id=run.id,
                symbol=trade["symbol"],
                direction=trade["direction"],
                entry_time=_as_datetime(trade["entry_time"]),
                entry_price=trade["entry_price"],
                exit_time=_as_datetime(trade["exit_time"]),
                exit_price=trade["exit_price"],
                quantity=trade["quantity"],
                fees=trade["fees"],
                slippage=trade["slippage"],
                pnl=trade["pnl"],
                pnl_pct=trade["pnl_pct"],
                r_multiple=trade["r_multiple"],
                mae=trade["mae"],
                mfe=trade["mfe"],
                entry_reason="entry_long",
                exit_reason=trade["exit_reason"],
                ambiguous_fill=trade["ambiguous_fill"],
                strategy_version=trade["strategy_version"],
            )
        )

    record_audit(
        db,
        event_type="backtest_completed",
        entity_type="backtest_run",
        entity_id=str(run.id),
        action="create",
        payload={
            "strategy_version_id": strategy_version.id,
            "dataset_version_id": series.id,
            "dataset_hash": dataset_hash,
            "result_hash": outcome.result_hash,
            "engine_version": outcome.engine_version,
            "feature_version": outcome.feature_version,
        },
    )
    db.commit()
    db.refresh(run)

    # Resource event: window peaks come from the monitor's samples when they cover
    # the run; a task shorter than one collection cycle leaves them null rather than
    # invented.
    #
    # This is optional monitoring, so it runs *after* the backtest is committed and
    # in its own transaction. `record_resource_event` flushes, so a failure here
    # leaves the session rollback-pending: previously the exception was swallowed
    # without a rollback, which poisoned the request and turned a perfectly good
    # backtest into a 500 (PendingRollbackError). Committing first means the
    # rollback can only ever discard the monitoring row, never the result.
    try:
        from app.infrastructure.resource_store import record_resource_event

        record_resource_event(
            db,
            event_key=f"backtest:{run.id}",
            event_type="backtest_completed",
            started_at=run.started_at,
            ended_at=run.finished_at,
            payload={
                "backtest_run_id": run.id,
                "strategy_version_id": run.strategy_version_id,
                "dataset_version_id": run.dataset_version_id,
                "trade_count": len(outcome.trades),
                "result_hash": outcome.result_hash,
            },
        )
        db.commit()
    except Exception:  # pragma: no cover - monitoring must never break backtests
        logger.warning("resource event recording failed", exc_info=True)
        db.rollback()

    return _to_out(run, outcome.metrics, outcome.equity_curve, outcome.trades, outcome.warnings)


def _as_datetime(value: Any) -> dt.datetime | None:
    """Accept ISO strings / datetimes and return an aware UTC datetime."""

    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.UTC)
    import pandas as pd

    stamp = pd.Timestamp(value)
    if stamp.tz is None:
        stamp = stamp.tz_localize("UTC")
    return stamp.to_pydatetime()


def _summary(outcome: Any, series: MarketDataSeries) -> dict[str, Any]:
    return {
        "dataset_version_id": series.id,
        "dataset_version": series.dataset_version,
        "initial_capital": outcome.initial_capital,
        "final_equity": outcome.final_equity,
        "total_return": outcome.metrics.get("total_return"),
        "max_drawdown": outcome.metrics.get("max_drawdown"),
        "sharpe": outcome.metrics.get("sharpe"),
        "win_rate": outcome.metrics.get("win_rate"),
        "number_of_trades": outcome.metrics.get("number_of_trades"),
        "timeframe": outcome.timeframe,
    }


def _metric_rows(metrics: dict[str, Any]) -> list[tuple[str, str, Any]]:
    groups = {
        "return": ("total_return", "cagr", "final_equity", "initial_capital"),
        "risk": ("max_drawdown", "annualized_volatility", "max_drawdown_duration_bars"),
        "risk_adjusted": ("sharpe", "sortino"),
        "trade": (
            "number_of_trades",
            "win_rate",
            "avg_win",
            "avg_loss",
            "profit_factor",
            "expectancy",
            "average_holding_bars",
            "max_consecutive_losses",
        ),
        "position": ("exposure", "turnover"),
    }
    rows: list[tuple[str, str, Any]] = []
    for group, names in groups.items():
        for name in names:
            if name in metrics:
                rows.append((group, name, metrics[name]))
    return rows


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
    metrics = ("total_return", "max_drawdown", "sharpe", "win_rate", "number_of_trades")
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
        [],
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
