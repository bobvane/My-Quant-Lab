"""Strategy experiment service (docs/25, ADR-174 / ADR-182 / ADR-183).

An experiment is the persisted business object around a research run: what was asked
for (``request_json``), what ran (``parameters_json`` and the frozen configuration
columns), and one ``experiment_results`` row per produced result. This module owns the
implementation -- creating a draft, running it, adopting an already-finished backtest,
archiving it, and comparing stored rows. The router owns the HTTP shape (paths, status
codes) and maps the ``HTTPException`` raised here.

Three rules are enforced here and nowhere else:

* **Nothing is written before the request is validated.** Every rejection (404/422)
  happens before the first row is touched, so "a rejected request stores nothing" is a
  property of the code, not of the router.
* **The configuration columns are written once.** ``initial_capital`` / ``start_date`` /
  ``end_date`` describe the run that was asked for; they are never re-derived from the
  strategy's current parameters, so re-reading an old experiment keeps reporting the
  numbers it actually ran with (ADR-183).
* **Every reported number was stored by the engine that computed it.** Adopting a
  ``BacktestRun`` copies its recorded metrics, summary and hashes verbatim; nothing on
  this path recomputes a quant value.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    ExperimentCompareOut,
    ExperimentCreate,
    ExperimentDetailOut,
    ExperimentResultOut,
    ExperimentSummaryOut,
)
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

ENTITY_TYPE = "strategy_experiment"
RUNNABLE_STATUSES = frozenset({"draft", "failed"})
ARCHIVED_STATUS = "archived"
RUNNING_STATUS = "running"


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


# --------------------------------------------------------------------------- #
# Request preparation (read-only; no row is written here)
# --------------------------------------------------------------------------- #
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


# The comparable columns of an experiment. `COMPARE_METRICS` stays the backtest-side
# projection (shared with `GET /backtests/compare`); an experiment additionally publishes
# the three numbers its result page prints in full (ADR-185).
EXPERIMENT_METRICS: tuple[str, ...] = (*COMPARE_METRICS, "final_equity", "cagr", "total_fees")


def _total_fees(trades: Sequence[Any]) -> float:
    """Add up the fees the engine already recorded, per trade. No re-run (ADR-185)."""

    return float(sum((Decimal(str(trade.get("fees") or 0)) for trade in trades), Decimal(0)))


def _fees_of_run(db: Session, run_id: int) -> float:
    """The stored per-trade fees of one run, added up. A read of evidence, not a re-run."""

    total = db.scalar(
        select(func.coalesce(func.sum(BacktestTrade.fees), 0)).where(
            BacktestTrade.backtest_run_id == run_id
        )
    )
    return float(total or 0)


def _comparable(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Project stored metrics onto the comparable columns. Never invents a value."""

    source = metrics or {}
    return {name: source.get(name) for name in EXPERIMENT_METRICS}


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


def _initial_capital(prepared: _Prepared) -> Decimal | None:
    """The capital the run was configured with, frozen into the column.

    A Monte Carlo resamples an existing run, so its capital is the one that run
    recorded; every other kind carries its capital in the strategy's execution block.
    """

    if prepared.initial_capital > 0:
        return Decimal(str(prepared.initial_capital))
    if prepared.spec is not None:
        return Decimal(str(prepared.spec.execution.initial_capital))
    return None


# --------------------------------------------------------------------------- #
# Execution (the only place a quant engine is called)
# --------------------------------------------------------------------------- #
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
        # `total_fees` is the only metric this layer derives, and it is derived from stored
        # evidence: the fees the engine just put on each of `outcome.trades` (ADR-185).
        metrics = {**outcome.metrics, "total_fees": _total_fees(outcome.trades)}
        return (
            [
                ExperimentResult(
                    experiment_id=experiment.id,
                    kind="backtest",
                    label=experiment.name,
                    parameters_json=payload.parameters or None,
                    backtest_run_id=run.id,
                    metrics_json=metrics,
                    payload_json=stored,
                )
            ],
            {
                "backtest_run_id": run.id,
                "result_hash": outcome.result_hash,
                "metrics": _comparable(metrics),
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


def _audit(
    db: Session,
    experiment: StrategyExperiment,
    *,
    event_type: str,
    action: str,
    payload: dict[str, Any] | None = None,
    user: str | None = None,
) -> None:
    """Write one ledger entry for the operation an experiment just went through."""

    record_audit(
        db,
        event_type=event_type,
        entity_type=ENTITY_TYPE,
        entity_id=str(experiment.id),
        action=action,
        payload=payload,
        actor=user or "system",
    )


def _attempt(
    db: Session,
    experiment: StrategyExperiment,
    payload: ExperimentCreate,
    prepared: _Prepared,
    *,
    user: str | None,
    discard_on_reject: bool,
) -> StrategyExperiment:
    """Execute a prepared request and record its outcome on ``experiment``.

    ``discard_on_reject`` is what separates the two callers. ``POST /experiments`` has
    only flushed its row, so a rejected request rolls back to nothing -- the documented
    "a rejected request stores nothing". ``POST /experiments/{id}/run`` works on a row
    that was committed earlier and cannot be un-written, so the same rejection has to be
    *recorded* on it, which is strictly more information for the caller.
    """

    try:
        results, summary = _execute(payload, prepared, experiment, db)
    except BacktestFailed as exc:
        # `store_backtest` already committed a failed run; the experiment records that
        # the research attempt happened and why it did not produce a result.
        return _fail(db, experiment, str(exc.run.error_message or exc), payload.kind, user=user)
    except (BacktestRequestError, ValueError) as exc:
        message = exc.detail if isinstance(exc, BacktestRequestError) else str(exc)
        status_code = exc.status_code if isinstance(exc, BacktestRequestError) else 422
        if discard_on_reject:
            db.rollback()
            raise HTTPException(status_code=status_code, detail=message) from exc
        return _fail(db, experiment, message, payload.kind, user=user)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("experiment %s failed", payload.kind)
        return _fail(db, experiment, str(exc), payload.kind, user=user)

    for result in results:
        db.add(result)

    summary["kind"] = payload.kind
    summary["result_count"] = len(results)
    experiment.summary_json = summary
    experiment.status = "completed"
    experiment.completed_at = dt.datetime.now(tz=dt.UTC)
    experiment.updated_at = dt.datetime.now(tz=dt.UTC)
    _audit(
        db,
        experiment,
        event_type="experiment_completed",
        action="strategy_experiment_run",
        payload={
            "kind": payload.kind,
            "strategy_version_id": payload.strategy_version_id,
            "result_count": len(results),
            "backtest_run_id": summary.get("backtest_run_id"),
        },
        user=user,
    )
    db.commit()
    db.refresh(experiment)
    return experiment


def _fail(
    db: Session,
    experiment: StrategyExperiment,
    message: str,
    kind: str,
    *,
    user: str | None = None,
) -> StrategyExperiment:
    """Record the failure on the experiment and leave the row readable.

    The row is the deliverable; the caller renders the failure. The engine's message is
    kept verbatim and the event is written to the audit ledger, so a failure is never
    swallowed.
    """

    now = dt.datetime.now(tz=dt.UTC)
    experiment.status = "failed"
    experiment.error_message = message[:2000]
    experiment.completed_at = now
    experiment.updated_at = now
    experiment.summary_json = {"kind": kind, "result_count": 0, "metrics": _comparable(None)}
    _audit(
        db,
        experiment,
        event_type="experiment_failed",
        action="strategy_experiment_run",
        payload={"kind": kind, "error_message": experiment.error_message},
        user=user,
    )
    db.commit()
    db.refresh(experiment)
    return experiment


# --------------------------------------------------------------------------- #
# Public operations
# --------------------------------------------------------------------------- #
def create_experiment(
    db: Session, payload: ExperimentCreate, *, user: str | None = None
) -> StrategyExperiment:
    """Create an experiment: either a stored draft, or a run executed now.

    ``draft=True`` freezes the validated request and stops there: the row records what
    *will* run, and no quant code is called until ``run_experiment``. Anything else is
    the synchronous behaviour ``POST /experiments`` always had.
    """

    prepared = _prepare(payload, db)
    now = dt.datetime.now(tz=dt.UTC)
    experiment = StrategyExperiment(
        name=payload.name,
        notes=payload.notes,
        status="draft" if payload.draft else RUNNING_STATUS,
        kind=payload.kind,
        strategy_version_id=payload.strategy_version_id,
        series_id=prepared.series.id if prepared.series is not None else None,
        symbol=prepared.symbol,
        timeframe=prepared.timeframe,
        parameters_json=payload.parameters or {},
        request_json=payload.model_dump(mode="json"),
        # The frozen configuration: written once, from the validated request.
        initial_capital=_initial_capital(prepared),
        start_date=_as_utc(payload.start, "start"),
        end_date=_as_utc(payload.end, "end"),
        started_at=None if payload.draft else now,
        updated_at=now,
    )
    db.add(experiment)
    db.flush()

    if payload.draft:
        _audit(
            db,
            experiment,
            event_type="experiment_created",
            action="strategy_experiment_created",
            payload={
                "kind": payload.kind,
                "strategy_version_id": payload.strategy_version_id,
                "draft": True,
            },
            user=user,
        )
        db.commit()
        db.refresh(experiment)
        return experiment

    return _attempt(db, experiment, payload, prepared, user=user, discard_on_reject=True)


def run_experiment(
    db: Session, experiment: StrategyExperiment, *, user: str | None = None
) -> StrategyExperiment:
    """Run a stored draft (or retry a failed experiment) from its frozen request."""

    if experiment.status not in RUNNABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"experiment {experiment.id} is '{experiment.status}'; "
                "only a draft or failed experiment can be run"
            ),
        )

    payload = ExperimentCreate.model_validate(experiment.request_json or {})
    # Validate before touching the row: a request that can no longer be resolved (the
    # version was removed, the series shrank below the window) leaves the stored
    # experiment exactly as it was rather than half-run.
    prepared = _prepare(payload, db)

    _clear_results(db, experiment)
    now = dt.datetime.now(tz=dt.UTC)
    experiment.status = RUNNING_STATUS
    experiment.error_message = None
    experiment.completed_at = None
    experiment.started_at = now
    experiment.updated_at = now
    db.flush()
    return _attempt(db, experiment, payload, prepared, user=user, discard_on_reject=False)


def _clear_results(db: Session, experiment: StrategyExperiment) -> None:
    """Drop the previous attempt's result rows so a re-run replaces instead of doubling."""

    for row in list(experiment.results):
        db.delete(row)
    db.flush()


def adopt_backtest_run(
    db: Session,
    run_id: int,
    *,
    name: str | None = None,
    notes: str | None = None,
    user: str | None = None,
) -> StrategyExperiment:
    """Turn an already-finished ``BacktestRun`` into an experiment.

    The run is the source of truth: its parameters, metrics, summary and reproduction
    hashes are copied verbatim into the new experiment, and one ``ExperimentResult``
    keeps the lineage back to ``backtest_runs.id``. Nothing is recomputed, so the
    experiment and the run can never disagree about what happened.
    """

    run = db.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest run not found")
    if run.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=(
                f"backtest run {run_id} is '{run.status}'; "
                "only a completed run can be adopted as an experiment"
            ),
        )
    result = run.result
    if result is None:
        raise HTTPException(
            status_code=409,
            detail=f"backtest run {run_id} has no stored result to adopt",
        )

    # One run, one experiment: a second adoption would fork the same evidence into two
    # twins, so point the caller at the experiment that already holds it.
    already = db.scalars(
        select(ExperimentResult).where(ExperimentResult.backtest_run_id == run_id)
    ).all()
    if already:
        owners = ", ".join(str(value) for value in sorted({row.experiment_id for row in already}))
        raise HTTPException(
            status_code=409,
            detail=f"backtest run {run_id} was already adopted by experiment {owners}",
        )

    series = run.dataset
    symbol = series.asset.symbol if series is not None and series.asset is not None else None
    stored_summary = dict(result.summary_json or {})
    stored_metrics = dict(result.metrics_json or {})
    # Every engine-produced metric travels verbatim (ADR-183). `total_fees` is the one key
    # this layer adds, and it is still a read of stored evidence: the fees already sitting
    # in `backtest_trades` for this run, added up (ADR-185).
    stored_metrics["total_fees"] = _fees_of_run(db, run.id)
    parameters = dict(run.parameters_json or {})
    execution = dict(run.execution_model_json or {})
    capital = _adopted_capital(stored_metrics, execution)
    timeframe = stored_summary.get("timeframe") or (
        series.timeframe if series is not None else "1d"
    )
    experiment_name = name or _default_adopt_name(run.id, symbol)
    now = dt.datetime.now(tz=dt.UTC)

    experiment = StrategyExperiment(
        name=experiment_name,
        notes=notes,
        status="completed",
        kind="backtest",
        strategy_version_id=run.strategy_version_id,
        series_id=run.dataset_version_id,
        symbol=symbol,
        timeframe=timeframe,
        parameters_json=parameters,
        request_json={
            # The run's own input snapshot, plus the marker that says where the
            # experiment came from. Replaying it re-runs the same request.
            "kind": "backtest",
            "name": experiment_name,
            "strategy_version_id": run.strategy_version_id,
            "series_id": run.dataset_version_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "parameters": parameters,
            "execution_model": execution,
            "dataset_hash": run.dataset_hash,
            "adopted_from_backtest_run": run.id,
        },
        summary_json={
            "kind": "backtest",
            "result_count": 1,
            "backtest_run_id": run.id,
            # Copied from the run, never recomputed: these are the handles that make the
            # run reproducible, and an adopted experiment must resolve to the same one.
            "result_hash": result.result_hash,
            "engine_version": run.engine_version,
            "feature_version": run.feature_version,
            "dataset_hash": run.dataset_hash,
            "dataset_version": stored_summary.get("dataset_version"),
            "source": stored_summary.get("source"),
            "timeframe": timeframe,
            "initial_capital": capital,
            "final_equity": stored_summary.get("final_equity"),
            "adopted_from_backtest_run": run.id,
            # Verbatim, like the result row below: an adopted summary reports the numbers
            # the run stored, so it can never drift from the run it came from.
            "metrics": dict(stored_metrics),
        },
        initial_capital=None if capital is None else Decimal(str(capital)),
        start_date=series.series_start if series is not None else None,
        end_date=series.series_end if series is not None else None,
        started_at=run.started_at or run.created_at,
        completed_at=run.finished_at,
        updated_at=now,
    )
    db.add(experiment)
    db.flush()
    db.add(
        ExperimentResult(
            experiment_id=experiment.id,
            kind="backtest",
            label=experiment_name,
            parameters_json=parameters or None,
            backtest_run_id=run.id,
            # The engine's metrics for that run, verbatim, plus the one key this layer
            # reads out of the stored trades (`total_fees`, ADR-185). Nothing is recomputed.
            metrics_json=stored_metrics,
            payload_json={
                "result_hash": result.result_hash,
                "engine_version": run.engine_version,
                "feature_version": run.feature_version,
                "dataset_hash": run.dataset_hash,
                "dataset_version_id": run.dataset_version_id,
                "dataset_version": stored_summary.get("dataset_version"),
                "source": stored_summary.get("source"),
                "timeframe": timeframe,
                "initial_capital": capital,
                "final_equity": stored_summary.get("final_equity"),
                "symbol": symbol,
                "adopted_from_backtest_run": run.id,
            },
        )
    )
    _audit(
        db,
        experiment,
        event_type="experiment_adopted_from_backtest",
        action="strategy_experiment_adopted_from_backtest",
        payload={
            "backtest_run_id": run.id,
            "strategy_version_id": run.strategy_version_id,
            "result_hash": result.result_hash,
        },
        user=user,
    )
    db.commit()
    db.refresh(experiment)
    return experiment


def _adopted_capital(metrics: dict[str, Any], execution: dict[str, Any]) -> float | None:
    """The run's recorded capital: its metrics first, then its execution model."""

    for source in (metrics, execution):
        value = source.get("initial_capital")
        if value is not None:
            return float(value)
    return None


def _default_adopt_name(run_id: int, symbol: str | None) -> str:
    return f"回测 #{run_id} · {symbol}" if symbol else f"回测 #{run_id}"


def archive_experiment(
    db: Session, experiment: StrategyExperiment, *, user: str | None = None
) -> StrategyExperiment:
    """Archive an experiment: it leaves the active history but is not deleted."""

    if experiment.status == RUNNING_STATUS:
        raise HTTPException(
            status_code=409,
            detail=f"experiment {experiment.id} is still running; it cannot be archived",
        )
    if experiment.status == ARCHIVED_STATUS:
        raise HTTPException(
            status_code=409,
            detail=f"experiment {experiment.id} is already archived",
        )

    now = dt.datetime.now(tz=dt.UTC)
    experiment.status = ARCHIVED_STATUS
    experiment.archived_at = now
    experiment.updated_at = now
    _audit(
        db,
        experiment,
        event_type="experiment_archived",
        action="strategy_experiment_archived",
        payload={"kind": experiment.kind},
        user=user,
    )
    db.commit()
    db.refresh(experiment)
    return experiment


def update_experiment(
    db: Session,
    experiment: StrategyExperiment,
    *,
    name: str | None = None,
    notes: str | None = None,
    user: str | None = None,
) -> StrategyExperiment:
    """Rename or re-note an experiment. The stored request and results never change."""

    changed: dict[str, Any] = {}
    if name is not None:
        experiment.name = name
        changed["name"] = name
    if notes is not None:
        experiment.notes = notes
        changed["notes"] = notes
    if not changed:
        # The schema rejects an empty PATCH, so this is the guard for direct callers.
        raise HTTPException(status_code=422, detail="give at least one of name or notes")

    experiment.updated_at = dt.datetime.now(tz=dt.UTC)
    _audit(
        db,
        experiment,
        event_type="experiment_updated",
        action="strategy_experiment_updated",
        payload=changed,
        user=user,
    )
    db.commit()
    db.refresh(experiment)
    return experiment


# --------------------------------------------------------------------------- #
# Read-side projections (stored values only)
# --------------------------------------------------------------------------- #
def _as_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _iso(value: dt.datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _version_of(db: Session, experiment: StrategyExperiment) -> StrategyVersion | None:
    """The version row behind an experiment.

    ``Session.get`` answers from the identity map, so a page of experiments that share
    a version resolves that version once instead of once per row.
    """

    if experiment.strategy_version_id is None:
        return None
    return db.get(StrategyVersion, experiment.strategy_version_id)


def _strategy_name(version: StrategyVersion | None) -> str | None:
    if version is None:
        return None
    strategy = version.strategy
    return strategy.name if strategy is not None else None


def _is_adopted(experiment: StrategyExperiment) -> bool:
    return bool((experiment.request_json or {}).get("adopted_from_backtest_run"))


def _symbols(experiment: StrategyExperiment, results: Sequence[ExperimentResult] = ()) -> list[str]:
    """The instruments an experiment touched, de-duplicated in order.

    The column is the primary answer -- every writer sets it. A stored result that names
    a symbol is the fallback for a row whose column is empty; in the detail shape those
    rows are already loaded, so consulting them costs nothing.
    """

    ordered: list[str] = []
    candidates: list[Any] = [experiment.symbol]
    candidates.extend((row.payload_json or {}).get("symbol") for row in results)
    for candidate in candidates:
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _summary_fields(
    db: Session, experiment: StrategyExperiment, results: Sequence[ExperimentResult] = ()
) -> dict[str, Any]:
    """The stored fields of the summary shape, shared by the list and detail views."""

    summary = experiment.summary_json or {}
    version = _version_of(db, experiment)
    return {
        "id": experiment.id,
        "name": experiment.name,
        "kind": experiment.kind,
        "status": experiment.status,
        "strategy_version_id": experiment.strategy_version_id,
        "series_id": experiment.series_id,
        "symbol": experiment.symbol,
        "timeframe": experiment.timeframe,
        "result_count": int(summary.get("result_count") or 0),
        "backtest_run_id": summary.get("backtest_run_id"),
        # The comparable columns are always present, even for a row stored before they
        # existed: a value nobody stored is published as null (unknown) -- never as 0, and
        # never silently absent -- while kind-specific metrics the summary does carry stay
        # in place (ADR-185).
        "metrics": {**_comparable(None), **dict(summary.get("metrics") or {})},
        "created_at": experiment.created_at,
        "started_at": experiment.started_at,
        "completed_at": experiment.completed_at,
        "error_message": experiment.error_message,
        "updated_at": experiment.updated_at,
        "archived_at": experiment.archived_at,
        "initial_capital": _as_float(experiment.initial_capital),
        "start_date": experiment.start_date,
        "end_date": experiment.end_date,
        "strategy_id": version.strategy_id if version is not None else None,
        "strategy_name": _strategy_name(version),
        "version": version.version if version is not None else None,
        "symbols": _symbols(experiment, results),
        "is_adopted": _is_adopted(experiment),
    }


def experiment_summary(db: Session, experiment: StrategyExperiment) -> ExperimentSummaryOut:
    """List/history shape: no result rows and no engine payloads."""

    return ExperimentSummaryOut(**_summary_fields(db, experiment))


def experiment_detail(db: Session, experiment: StrategyExperiment) -> ExperimentDetailOut:
    """The experiment and its stored results, re-read from the database.

    The result rows are selected again instead of taken from the relationship: a re-run
    deletes the previous attempt's rows inside the same unit of work, and the response
    must describe what is stored now -- (experiment_id, id) is exactly the index that
    serves both this filter and this order.
    """

    rows = db.scalars(
        select(ExperimentResult)
        .where(ExperimentResult.experiment_id == experiment.id)
        .order_by(ExperimentResult.id)
    ).all()
    return ExperimentDetailOut(
        **_summary_fields(db, experiment, rows),
        notes=experiment.notes,
        parameters=experiment.parameters_json or {},
        request=experiment.request_json or {},
        summary=experiment.summary_json,
        results=[
            ExperimentResultOut(
                id=row.id,
                kind=row.kind,
                label=row.label,
                parameters=row.parameters_json,
                backtest_run_id=row.backtest_run_id,
                metrics=row.metrics_json,
                payload=row.payload_json or {},
                created_at=row.created_at,
            )
            for row in rows
        ],
    )


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #
# Fixed order and one label per dimension, so the same set of rows always reports the
# same list. The key functions read the STORED configuration, never a live default.
_DIFFERENCE_CHECKS: tuple[tuple[str, Callable[[dict[str, Any]], Any]], ...] = (
    ("参数不同", lambda config: config["parameters"]),
    ("标的不同", lambda config: config["symbols"]),
    ("时间范围不同", lambda config: (config["start"], config["end"])),
    ("初始资金不同", lambda config: config["initial_capital"]),
    ("策略版本不同", lambda config: config["strategy_version_id"]),
)


def _parse_ids(ids: Sequence[str], limit: int) -> list[int]:
    parts: list[str] = []
    for chunk in ids:
        parts.extend(part.strip() for part in chunk.split(",") if part.strip())
    try:
        parsed = [int(part) for part in parts][:limit]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="ids must be comma separated integers") from exc
    if len(parsed) < 2:
        raise HTTPException(status_code=422, detail="at least two experiment ids are required")
    return parsed


def _config_of(db: Session, experiment: StrategyExperiment) -> dict[str, Any]:
    """The stored configuration of one experiment, as compared by ``_differences``."""

    version = _version_of(db, experiment)
    return {
        "experiment_id": experiment.id,
        "name": experiment.name,
        "kind": experiment.kind,
        "status": experiment.status,
        "strategy_id": version.strategy_id if version is not None else None,
        "strategy_name": _strategy_name(version),
        "version": version.version if version is not None else None,
        "strategy_version_id": experiment.strategy_version_id,
        "symbols": _symbols(experiment),
        "timeframe": experiment.timeframe,
        "start": _iso(experiment.start_date),
        "end": _iso(experiment.end_date),
        "initial_capital": _as_float(experiment.initial_capital),
        "parameters": dict(experiment.parameters_json or {}),
    }


def _differences(configs: Sequence[dict[str, Any]]) -> list[str]:
    """The stored dimensions the compared experiments disagree on.

    An empty list means the rows ran the same configuration, so their metric columns can
    be read against each other; anything else names the dimensions that make the
    comparison approximate.
    """

    if len(configs) < 2:
        return []
    first = configs[0]
    return [
        label
        for label, key in _DIFFERENCE_CHECKS
        if any(key(config) != key(first) for config in configs[1:])
    ]


def compare_experiments(
    db: Session, ids: Sequence[str], *, limit: int = 10
) -> ExperimentCompareOut:
    """Line up stored experiments side by side.

    A pure projection of what is stored: the numbers come out of each experiment's
    ``summary_json`` exactly as the engine left them, and each row additionally carries
    the configuration the numbers were produced with.
    """

    experiment_ids = _parse_ids(ids, limit)
    rows = db.scalars(
        select(StrategyExperiment).where(StrategyExperiment.id.in_(experiment_ids))
    ).all()
    stored = {row.id: row for row in rows}

    experiments: list[dict[str, Any]] = []
    configs: list[dict[str, Any]] = []
    for experiment_id in experiment_ids:
        experiment = stored.get(experiment_id)
        if experiment is None:
            continue
        summary = experiment.summary_json or {}
        metrics = dict(summary.get("metrics") or {})
        config = _config_of(db, experiment)
        configs.append(config)
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
                **{name: metrics.get(name) for name in EXPERIMENT_METRICS},
                "config": config,
            }
        )

    differences = _differences(configs)
    return ExperimentCompareOut(
        metrics=list(EXPERIMENT_METRICS),
        comparability="same-config" if not differences else "different-config",
        differences=differences,
        experiments=experiments,
    )
