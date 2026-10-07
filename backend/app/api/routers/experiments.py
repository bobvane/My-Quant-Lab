"""Strategy experiment endpoints (docs/25, ADR-174).

An experiment is the persisted business object around a research run: it records what was
asked for (``request_json``), what was executed (``parameters_json``), and one
``experiment_results`` row per produced result -- so a parameter sweep keeps its
parameter/result pairs, and a single-shot run keeps the link to the ``BacktestRun`` it
created. Reading an experiment never re-runs a quant algorithm; every number returned here
was stored by the engine that computed it.

Experiments execute synchronously inside the request, exactly like ``POST /backtests``: no
worker, no queue, no new dependency. The trade-off is that a long sweep occupies the
request; the benefit is that the response and the persisted row can never disagree.

Five hard rules still hold: nothing here is a trading endpoint, the AI layer is not
involved, and no quant value is computed outside ``app.research``.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.schemas import (
    ExperimentCompareOut,
    ExperimentCreate,
    ExperimentDetailOut,
    ExperimentListOut,
    ExperimentResultOut,
    ExperimentSummaryOut,
)
from app.core.db import get_db
from app.data.backtest_service import (
    COMPARE_METRICS,
    MIN_BACKTEST_BARS,
    BacktestFailed,
    BacktestInputs,
    BacktestRequestError,
    load_backtest_inputs,
    store_backtest,
)
from app.data.market_data_repo import load_bars, resolve_series
from app.data.strategy_service import load_spec, record_audit
from app.domain.models import (
    BacktestRun,
    BacktestTrade,
    ExperimentResult,
    MarketDataSeries,
    StrategyExperiment,
    StrategyVersion,
)
from app.research.monte_carlo import run_monte_carlo
from app.research.sensitivity import TRACKED_METRICS, run_sensitivity
from app.research.walk_forward import run_holdout, run_walk_forward
from app.strategies.dsl import StrategySpec

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/experiments", tags=["experiments"])


@dataclass
class _Prepared:
    """Everything the request needs, resolved and validated before anything is written."""

    version: StrategyVersion
    spec: StrategySpec | None
    series: MarketDataSeries | None
    frame: Any
    symbol: str | None
    timeframe: str
    inputs: BacktestInputs | None = None
    run: BacktestRun | None = None
    trades: list[dict[str, Any]] = field(default_factory=list)
    initial_capital: float = 0.0


def _as_utc(value: str | None, field_name: str) -> dt.datetime | None:
    """Parse a request timestamp; a date-only value is read as UTC midnight."""

    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"{field_name} must be an ISO-8601 timestamp"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _version_label(version: StrategyVersion) -> str:
    return f"{version.strategy_id}@{version.version}"


def _comparable(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Project stored metrics onto the comparable five. Never invents a value."""

    source = metrics or {}
    return {name: source.get(name) for name in COMPARE_METRICS}


def _point_label(parameters: dict[str, Any] | None) -> str | None:
    if not parameters:
        return None
    return ", ".join(f"{key}={value}" for key, value in parameters.items())[:160]


def _symbol_of(series: MarketDataSeries | None) -> str | None:
    if series is None or series.asset is None:
        return None
    return series.asset.symbol


def _prepare(payload: ExperimentCreate, db: Session) -> _Prepared:
    """Resolve and validate the request. Raises ``HTTPException`` before any write."""

    version = db.get(StrategyVersion, payload.strategy_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="strategy version not found")

    if payload.kind == "monte_carlo":
        return _prepare_monte_carlo(payload, db, version)

    series = resolve_series(
        db,
        symbol=payload.symbol,
        series_id=payload.series_id,
        # The timeframe has a default, so treating it as a demand would reject a request
        # that only names a series whose timeframe it never mentioned (ADR-119).
        timeframe=payload.timeframe if "timeframe" in payload.model_fields_set else None,
    )
    frame = load_bars(
        db,
        series,
        start=_as_utc(payload.start, "start"),
        end=_as_utc(payload.end, "end"),
        only_closed=True,
    )
    symbol = payload.symbol or _symbol_of(series)

    if payload.kind == "backtest":
        try:
            inputs = load_backtest_inputs(
                db,
                strategy_version_id=payload.strategy_version_id,
                version=version,
                series=series,
                frame=frame,
            )
        except BacktestRequestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return _Prepared(
            version=version,
            spec=inputs.spec,
            series=series,
            frame=frame,
            symbol=symbol,
            timeframe=payload.timeframe,
            inputs=inputs,
        )

    if payload.kind == "sensitivity":
        if not payload.grid:
            raise HTTPException(
                status_code=422,
                detail="sensitivity experiments require a non-empty grid",
            )
        # The engine caps the grid at MAX_GRID_POINTS and rejects an axis the strategy
        # does not declare; letting it decide keeps one definition of "a valid axis".
        if payload.metric not in TRACKED_METRICS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unsupported metric '{payload.metric}'; "
                    f"choose one of {', '.join(TRACKED_METRICS)}"
                ),
            )
        if len(frame) < MIN_BACKTEST_BARS:
            raise HTTPException(
                status_code=422,
                detail=f"need at least {MIN_BACKTEST_BARS} closed bars, series has {len(frame)}",
            )

    if payload.kind == "walk_forward":
        needed = payload.train_bars + payload.test_bars
        if len(frame) < needed:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"need at least {needed} closed bars to split into "
                    f"{payload.train_bars} train + {payload.test_bars} test, "
                    f"series has {len(frame)}"
                ),
            )

    if payload.kind == "oos" and len(frame) < 2:
        raise HTTPException(
            status_code=422,
            detail=(
                f"need at least 2 closed bars for an out-of-sample split, series has {len(frame)}"
            ),
        )

    return _Prepared(
        version=version,
        spec=load_spec(version),
        series=series,
        frame=frame,
        symbol=symbol,
        timeframe=payload.timeframe,
    )


def _prepare_monte_carlo(
    payload: ExperimentCreate, db: Session, version: StrategyVersion
) -> _Prepared:
    """A Monte Carlo experiment resamples the STORED trades of an existing run.

    It never re-runs the backtest: the sample it resamples is the one the earlier run
    recorded, which is the whole point of keeping trades in the database.
    """

    if payload.backtest_run_id is None:
        raise HTTPException(
            status_code=422,
            detail="monte_carlo experiments require backtest_run_id",
        )
    run = db.get(BacktestRun, payload.backtest_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest run not found")
    if run.status != "completed":
        raise HTTPException(
            status_code=422,
            detail=(
                f"backtest run {run.id} is '{run.status}'; only completed runs can be resampled"
            ),
        )

    rows = db.scalars(select(BacktestTrade).where(BacktestTrade.backtest_run_id == run.id)).all()
    trades = [{"pnl": float(row.pnl)} for row in rows if row.pnl is not None]
    if not trades:
        raise HTTPException(
            status_code=422,
            detail="this backtest produced no closed trades, so there is nothing to resample",
        )

    initial_capital = float((run.execution_model_json or {}).get("initial_capital") or 0.0)
    if initial_capital <= 0:
        raise HTTPException(
            status_code=422,
            detail="the backtest run does not record a positive initial_capital",
        )

    series = db.get(MarketDataSeries, run.dataset_version_id)
    return _Prepared(
        version=version,
        spec=None,
        series=series,
        frame=None,
        symbol=_symbol_of(series),
        timeframe=series.timeframe if series is not None else "1d",
        run=run,
        trades=trades,
        initial_capital=initial_capital,
    )


def _execute(
    payload: ExperimentCreate, prepared: _Prepared, experiment: StrategyExperiment, db: Session
) -> tuple[list[ExperimentResult], dict[str, Any]]:
    """Run the kind's engine and return its result rows plus the stored summary."""

    version = prepared.version
    label = _version_label(version)

    if payload.kind == "backtest":
        execution = store_backtest(
            db,
            prepared.inputs,
            parameters=payload.parameters or {},
            timeframe=prepared.timeframe,
        )
        run, outcome = execution.run, execution.outcome
        # Not `outcome.as_dict()`: the heavy trades/equity curve already live behind
        # `backtest_run_id`, and `as_dict()` drops `warmup_unmet` (ADR-054/055).
        stored = {
            "strategy_version": outcome.strategy_version,
            "engine_version": outcome.engine_version,
            "feature_version": outcome.feature_version,
            "dataset_hash": outcome.dataset_hash,
            "timeframe": outcome.timeframe,
            "initial_capital": outcome.initial_capital,
            "final_equity": outcome.final_equity,
            "result_hash": outcome.result_hash,
            "warmup_unmet": bool(outcome.warmup_unmet),
            "trade_count": len(outcome.trades),
            "warnings": list(outcome.warnings),
        }
        return (
            [
                ExperimentResult(
                    experiment_id=experiment.id,
                    kind="backtest",
                    label=experiment.name,
                    parameters_json=payload.parameters or None,
                    backtest_run_id=run.id,
                    metrics_json=outcome.metrics,
                    payload_json=stored,
                )
            ],
            {
                "backtest_run_id": run.id,
                "result_hash": outcome.result_hash,
                "metrics": _comparable(outcome.metrics),
            },
        )

    if payload.kind == "sensitivity":
        outcome = run_sensitivity(
            prepared.spec,
            prepared.frame,
            grid=payload.grid,
            base_parameters=payload.parameters,
            metric=payload.metric,
            strategy_version=label,
            timeframe=prepared.timeframe,
        )
        points = outcome["points"]
        results = [
            ExperimentResult(
                experiment_id=experiment.id,
                kind="sensitivity_point",
                label=_point_label(point["parameters"]),
                # The parameter/result pair is the deliverable of a sweep: without it the
                # history is a list of scores with no way to know which setting produced
                # which one.
                parameters_json=point["parameters"],
                backtest_run_id=None,
                metrics_json=point["metrics"],
                payload_json=point,
            )
            for point in points
        ]
        starved = [point for point in points if point.get("warmup_unmet")]
        return (
            results,
            {
                "sensitivity_version": outcome["sensitivity_version"],
                "metric": outcome["metric"],
                "axes": outcome["axes"],
                "grid_points": outcome["grid_points"],
                "evaluated_points": outcome["evaluated_points"],
                "ranked_points": outcome["ranked_points"],
                "warmup_unmet_points": outcome["warmup_unmet_points"],
                "warmup_unmet_results": [
                    {"parameters": point["parameters"], "label": _point_label(point["parameters"])}
                    for point in starved
                ],
                "statistics": outcome["summary"],
                "best": outcome["best"],
                "worst": outcome["worst"],
                "stable": outcome["stable"],
                "warnings": outcome["warnings"],
                "metrics": _best_point_metrics(outcome["best"], points),
            },
        )

    if payload.kind == "monte_carlo":
        outcome = run_monte_carlo(
            prepared.trades,
            initial_capital=prepared.initial_capital,
            runs=payload.runs,
            trades_per_run=payload.trades_per_run,
            seed=payload.seed,
            timeframe=prepared.timeframe,
        )
        statistics = dict(outcome["summary"])
        return (
            [
                ExperimentResult(
                    experiment_id=experiment.id,
                    kind="monte_carlo",
                    label=experiment.name,
                    parameters_json={
                        "runs": payload.runs,
                        "trades_per_run": payload.trades_per_run,
                        "seed": payload.seed,
                    },
                    backtest_run_id=prepared.run.id if prepared.run else None,
                    metrics_json=statistics,
                    payload_json=outcome,
                )
            ],
            {
                "backtest_run_id": prepared.run.id if prepared.run else None,
                "method": outcome["method"],
                "monte_carlo_version": outcome["monte_carlo_version"],
                "observed_trades": statistics.get("observed_trades"),
                "metrics": {
                    "probability_of_profit": statistics.get("probability_of_profit"),
                    "probability_of_ruin": statistics.get("probability_of_ruin"),
                    "observed_trades": statistics.get("observed_trades"),
                },
            },
        )

    if payload.kind == "walk_forward":
        outcome = run_walk_forward(
            prepared.spec,
            prepared.frame,
            train_bars=payload.train_bars,
            test_bars=payload.test_bars,
            step=payload.step,
            strategy_version=label,
            timeframe=prepared.timeframe,
        )
        statistics = dict(outcome["summary"])
        return (
            [
                ExperimentResult(
                    experiment_id=experiment.id,
                    kind="walk_forward",
                    label=experiment.name,
                    parameters_json={
                        "train_bars": payload.train_bars,
                        "test_bars": payload.test_bars,
                        "step": payload.step,
                    },
                    backtest_run_id=None,
                    metrics_json=statistics,
                    payload_json=outcome,
                )
            ],
            {"windows": outcome["windows"], "metrics": _comparable(statistics)},
        )

    outcome = run_holdout(
        prepared.spec,
        prepared.frame,
        oos_pct=payload.oos_pct,
        oos_start=payload.oos_start,
        strategy_version=label,
        timeframe=prepared.timeframe,
    )
    out_of_sample = dict(outcome["out_of_sample"])
    return (
        [
            ExperimentResult(
                experiment_id=experiment.id,
                kind="oos",
                label=experiment.name,
                parameters_json={"oos_pct": payload.oos_pct, "oos_start": payload.oos_start},
                backtest_run_id=None,
                metrics_json=out_of_sample,
                payload_json=outcome,
            )
        ],
        {
            "split_time": outcome["split_time"],
            "in_sample_bars": outcome["in_sample_bars"],
            "out_of_sample_bars": outcome["out_of_sample_bars"],
            # The out-of-sample leg is the number this kind exists to report.
            "metrics": _comparable(out_of_sample),
        },
    )


def _best_point_metrics(
    best: dict[str, Any] | None, points: list[dict[str, Any]]
) -> dict[str, Any]:
    """Look up the metrics the engine already chose as best -- never re-rank.

    A point the warm-up starved is skipped even if it matches: its metrics are the flat
    zeros of a strategy that never traded, and those beat a genuinely losing point
    (v1.4.5 / ADR-055).
    """

    if not best:
        return {}
    for point in points:
        if point.get("warmup_unmet"):
            continue
        if point.get("result_hash") == best.get("result_hash") and point.get(
            "parameters"
        ) == best.get("parameters"):
            return dict(point.get("metrics") or {})
    return {}


@router.post(
    "", response_model=ExperimentDetailOut, status_code=201, summary="Create an experiment"
)
def create_experiment(
    payload: ExperimentCreate, db: Session = Depends(get_db)
) -> ExperimentDetailOut:
    prepared = _prepare(payload, db)

    experiment = StrategyExperiment(
        name=payload.name,
        notes=payload.notes,
        status="running",
        kind=payload.kind,
        strategy_version_id=payload.strategy_version_id,
        series_id=prepared.series.id if prepared.series is not None else None,
        symbol=prepared.symbol,
        timeframe=prepared.timeframe,
        parameters_json=payload.parameters or {},
        request_json=payload.model_dump(mode="json"),
        started_at=dt.datetime.now(tz=dt.UTC),
    )
    db.add(experiment)
    db.flush()

    try:
        results, summary = _execute(payload, prepared, experiment, db)
    except BacktestFailed as exc:
        # `store_backtest` already committed a failed run; the experiment records that
        # the research attempt happened and why it did not produce a result.
        return _fail(db, experiment, str(exc.run.error_message or exc), payload.kind)
    except (BacktestRequestError, ValueError) as exc:
        # A rejected request must leave nothing behind: the row above was only flushed,
        # never committed, so dropping it is the whole of "zero rows written".
        db.rollback()
        detail = exc.detail if isinstance(exc, BacktestRequestError) else str(exc)
        status_code = exc.status_code if isinstance(exc, BacktestRequestError) else 422
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("experiment %s failed", payload.kind)
        return _fail(db, experiment, str(exc), payload.kind)

    for result in results:
        db.add(result)

    summary["kind"] = payload.kind
    summary["result_count"] = len(results)
    experiment.summary_json = summary
    experiment.status = "completed"
    experiment.completed_at = dt.datetime.now(tz=dt.UTC)
    record_audit(
        db,
        event_type="experiment_completed",
        entity_type="strategy_experiment",
        entity_id=str(experiment.id),
        action="run",
        payload={
            "kind": payload.kind,
            "strategy_version_id": payload.strategy_version_id,
            "result_count": len(results),
            "backtest_run_id": summary.get("backtest_run_id"),
        },
    )
    db.commit()
    db.refresh(experiment)
    return _detail(experiment)


def _fail(
    db: Session, experiment: StrategyExperiment, message: str, kind: str
) -> ExperimentDetailOut:
    """Record the failure on the created experiment and answer 201 with that status.

    The created row is the deliverable of the POST; the UI renders the failure. The
    engine's message is kept verbatim and the event is written to the audit ledger, so a
    failure is never swallowed.
    """

    experiment.status = "failed"
    experiment.error_message = message[:2000]
    experiment.completed_at = dt.datetime.now(tz=dt.UTC)
    experiment.summary_json = {"kind": kind, "result_count": 0, "metrics": _comparable(None)}
    record_audit(
        db,
        event_type="experiment_failed",
        entity_type="strategy_experiment",
        entity_id=str(experiment.id),
        action="run",
        payload={"kind": kind, "error_message": experiment.error_message},
    )
    db.commit()
    db.refresh(experiment)
    return _detail(experiment)


@router.get("", response_model=ExperimentListOut, summary="List strategy experiments")
def list_experiments(
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=200),
    strategy_version_id: int | None = None,
) -> ExperimentListOut:
    stmt = select(StrategyExperiment)
    if strategy_version_id:
        stmt = stmt.where(StrategyExperiment.strategy_version_id == strategy_version_id)
    rows = db.scalars(stmt.order_by(StrategyExperiment.id.desc()).limit(limit)).all()
    return ExperimentListOut(experiments=[_summary(row) for row in rows])


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
    ``summary_json`` exactly as the engine left them.
    """

    parts: list[str] = []
    for chunk in ids:
        parts.extend(part.strip() for part in chunk.split(",") if part.strip())
    try:
        experiment_ids = [int(part) for part in parts][:limit]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="ids must be comma separated integers") from exc
    if len(experiment_ids) < 2:
        raise HTTPException(status_code=422, detail="at least two experiment ids are required")

    rows = db.scalars(
        select(StrategyExperiment).where(StrategyExperiment.id.in_(experiment_ids))
    ).all()
    stored = {row.id: row for row in rows}
    experiments = []
    for experiment_id in experiment_ids:
        experiment = stored.get(experiment_id)
        if experiment is None:
            continue
        summary = experiment.summary_json or {}
        metrics = dict(summary.get("metrics") or {})
        experiments.append(
            {
                "id": experiment.id,
                "name": experiment.name,
                "kind": experiment.kind,
                "status": experiment.status,
                "strategy_version_id": experiment.strategy_version_id,
                "symbol": experiment.symbol,
                "timeframe": experiment.timeframe,
                "result_count": int(summary.get("result_count") or 0),
                "best": summary.get("best"),
                "worst": summary.get("worst"),
                **{name: metrics.get(name) for name in COMPARE_METRICS},
            }
        )
    return ExperimentCompareOut(metrics=list(COMPARE_METRICS), experiments=experiments)


@router.get("/{experiment_id}", response_model=ExperimentDetailOut, summary="Get an experiment")
def get_experiment(experiment_id: int, db: Session = Depends(get_db)) -> ExperimentDetailOut:
    """The experiment and its stored results, readable long after the POST returned."""

    experiment = db.scalars(
        select(StrategyExperiment)
        .where(StrategyExperiment.id == experiment_id)
        .options(selectinload(StrategyExperiment.results))
    ).one_or_none()
    if experiment is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    return _detail(experiment)


@router.delete("/{experiment_id}", status_code=204, summary="Delete an experiment and its results")
def delete_experiment(experiment_id: int, db: Session = Depends(get_db)) -> Response:
    """Delete the experiment; the ``BacktestRun`` it produced is its own artifact."""

    experiment = db.get(StrategyExperiment, experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="experiment not found")

    kind = experiment.kind
    result_count = len(experiment.results)
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


def _summary(experiment: StrategyExperiment) -> ExperimentSummaryOut:
    summary = experiment.summary_json or {}
    return ExperimentSummaryOut(
        id=experiment.id,
        name=experiment.name,
        kind=experiment.kind,
        status=experiment.status,
        strategy_version_id=experiment.strategy_version_id,
        series_id=experiment.series_id,
        symbol=experiment.symbol,
        timeframe=experiment.timeframe,
        result_count=int(summary.get("result_count") or 0),
        backtest_run_id=summary.get("backtest_run_id"),
        metrics=dict(summary.get("metrics") or {}),
        created_at=experiment.created_at,
        started_at=experiment.started_at,
        completed_at=experiment.completed_at,
        error_message=experiment.error_message,
    )


def _detail(experiment: StrategyExperiment) -> ExperimentDetailOut:
    results = [
        ExperimentResultOut(
            id=result.id,
            kind=result.kind,
            label=result.label,
            parameters=result.parameters_json,
            backtest_run_id=result.backtest_run_id,
            metrics=result.metrics_json,
            payload=result.payload_json or {},
            created_at=result.created_at,
        )
        # Ordered by id so a sweep reads back in the order it was evaluated; the
        # (experiment_id, id) index serves both the filter and this order.
        for result in sorted(experiment.results, key=lambda row: row.id or 0)
    ]
    return ExperimentDetailOut(
        **_summary(experiment).model_dump(),
        notes=experiment.notes,
        parameters=experiment.parameters_json or {},
        request=experiment.request_json or {},
        summary=experiment.summary_json,
        results=results,
    )
