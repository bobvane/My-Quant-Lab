"""The single place a backtest is executed and persisted (ADR-174).

``POST /backtests`` and ``POST /experiments`` with ``kind="backtest"`` must produce the
same artifact: a ``BacktestRun`` plus its ``BacktestResult``, metric rows, trade rows and
``backtest_completed`` audit event. That persistence used to be inline in the backtests
route handler; it lives here so an experiment cannot grow a second, subtly different copy
of it (a real ledger, the metric grouping and the optional resource event are easy to
drift apart).

Nothing here implements a quant algorithm: ``app.research.engine.run_backtest`` remains
the only engine.

The steps are separate functions — :func:`prepare_backtest`, :func:`execute_backtest`,
:func:`fail_backtest` — because the engine no longer has to run inside the request that
asked for it: with ``BACKTEST_ASYNC`` on, a worker in another process drives the same two
steps by run id, and the row it finds is the row a polling client reads for ``progress``
and ``current_step`` (ADR-180).
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any, NoReturn

import pandas as pd
from sqlalchemy.orm import Session

from app.data.market_data_repo import load_bars, resolve_series, series_content_hash
from app.data.strategy_service import load_spec, record_audit
from app.domain.models import (
    BacktestMetric,
    BacktestResult,
    BacktestRun,
    BacktestTrade,
    MarketDataSeries,
    StrategyVersion,
)
from app.features.engine import FEATURE_VERSION
from app.research.engine import ENGINE_VERSION, run_backtest
from app.strategies.dsl import StrategySpec, merge_spec_overrides

logger = logging.getLogger(__name__)

__all__ = [
    "COMPARE_METRICS",
    "MIN_BACKTEST_BARS",
    "PROGRESS_LADDER",
    "BacktestExecution",
    "BacktestFailed",
    "BacktestInputs",
    "BacktestRequestError",
    "advance_backtest",
    "execute_backtest",
    "fail_backtest",
    "load_backtest_inputs",
    "prepare_backtest",
    "rebuild_backtest_inputs",
    "store_backtest",
]

#: Fewer closed bars than this cannot produce a meaningful run.
MIN_BACKTEST_BARS = 60

#: The five numbers the two comparison endpoints project side by side (``GET
#: /backtests/compare`` and ``GET /experiments/compare``). Defined once so an experiment
#: history cannot end up showing a different set of columns than a run history does.
COMPARE_METRICS = ("total_return", "max_drawdown", "sharpe", "win_rate", "number_of_trades")

#: The rungs an executing run climbs, and the percentage each one means (ADR-180). One
#: table so the route, the worker and a client cannot disagree about what "45" is, and so
#: the API can draw a bar without knowing the engine.
#:
#: ``run_backtest`` is a single call (ADR-174 keeps it the only engine) and reports no
#: boundary between computing features, walking the strategy and evaluating exits, so a run
#: jumps from ``computing features`` to ``computing metrics``. ``running strategy`` and
#: ``evaluating exits`` are listed because they are the engine's own phases, but nothing
#: commits them: a percentage for a phase nobody can observe would be invented.
PROGRESS_LADDER: dict[str, int] = {
    "loading data": 5,
    "computing features": 20,
    "running strategy": 45,
    "evaluating exits": 70,
    "computing metrics": 90,
    "completed": 100,
}


class BacktestRequestError(Exception):
    """The request cannot become a run. Nothing has been persisted."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class BacktestFailed(Exception):
    """The engine raised. The run row — committed before this is raised — records it."""

    def __init__(self, run: BacktestRun) -> None:
        super().__init__(run.error_message or "backtest execution failed")
        self.run = run


@dataclass(frozen=True)
class BacktestInputs:
    """Everything a run needs, already validated and resolved."""

    version: StrategyVersion
    series: MarketDataSeries
    frame: pd.DataFrame
    spec: StrategySpec
    dataset_hash: str


@dataclass(frozen=True)
class BacktestExecution:
    run: BacktestRun
    outcome: Any


def load_backtest_inputs(
    db: Session,
    *,
    strategy_version_id: int,
    symbol: str | None = None,
    series_id: int | None = None,
    timeframe: str = "1d",
    timeframe_explicit: bool = False,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    execution_overrides: dict[str, Any] | None = None,
    version: StrategyVersion | None = None,
    series: MarketDataSeries | None = None,
    frame: pd.DataFrame | None = None,
) -> BacktestInputs:
    """Resolve and validate everything a run needs, writing nothing.

    ``version`` / ``series`` / ``frame`` let a caller that already resolved them (the
    experiments router does, to fill the experiment row) skip duplicate queries without
    changing any of the checks. Every rejection is a :class:`BacktestRequestError`, so a
    caller can decide whether it may still commit its own bookkeeping row.
    """

    if version is None:
        version = db.get(StrategyVersion, strategy_version_id)
    if version is None:
        raise BacktestRequestError(404, "strategy version not found")
    if version.validation_status != "valid":
        raise BacktestRequestError(
            422, f"strategy version is '{version.validation_status}', not 'valid'"
        )

    if series is None:
        # The timeframe is only checked against an explicit series when the caller
        # actually sent one: it has a default, so treating the default as a demand
        # would reject a request that only names a series (ADR-119).
        series = resolve_series(
            db,
            symbol=symbol,
            series_id=series_id,
            timeframe=timeframe if timeframe_explicit else None,
        )
    if frame is None:
        frame = load_bars(db, series, start=start, end=end, only_closed=True)
    if len(frame) < MIN_BACKTEST_BARS:
        raise BacktestRequestError(
            422, f"need at least {MIN_BACKTEST_BARS} closed bars, series has {len(frame)}"
        )

    spec = load_spec(version)
    if execution_overrides:
        # merge_spec_overrides (not model_copy) so a nested override such as
        # {"sizing": {...}} is validated instead of silently kept as a dict.
        spec = merge_spec_overrides(spec, {"execution": execution_overrides})

    return BacktestInputs(
        version=version,
        series=series,
        frame=frame,
        spec=spec,
        dataset_hash=series_content_hash(frame),
    )


def prepare_backtest(
    db: Session,
    *,
    strategy_version_id: int,
    symbol: str | None = None,
    series_id: int | None = None,
    timeframe: str = "1d",
    timeframe_explicit: bool = False,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    execution_overrides: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
    version: StrategyVersion | None = None,
    series: MarketDataSeries | None = None,
    frame: pd.DataFrame | None = None,
    inputs: BacktestInputs | None = None,
) -> tuple[BacktestRun, BacktestInputs]:
    """Resolve the inputs and create the ``status="running"`` run row.

    It COMMITS, and that is the point: with ``BACKTEST_ASYNC`` on the next step is another
    process, which opens its own session and looks the run up by id. A row still sitting in
    an uncommitted transaction would not exist for that worker — it would report the run as
    missing and the run would sit at ``progress=0`` for ever. Committing here is also what
    makes the run watchable: ``0``/``loading data`` is a durable answer to a poll, not a
    value that disappears with the request.

    The request's own shape is stored too (``parameters_json``, ``execution_model_json``)
    because the worker gets nothing but the run id: whatever it must replay has to be on
    the row.

    ``inputs`` is for an in-process caller that has already resolved them —
    :func:`store_backtest`, and ``POST /experiments`` through it. Re-resolving there would
    drop the request's ``execution_overrides`` and record the execution model of the strategy
    version instead of the one the run is about to use.
    """

    if inputs is None:
        inputs = load_backtest_inputs(
            db,
            strategy_version_id=strategy_version_id,
            symbol=symbol,
            series_id=series_id,
            timeframe=timeframe,
            timeframe_explicit=timeframe_explicit,
            start=start,
            end=end,
            execution_overrides=execution_overrides,
            version=version,
            series=series,
            frame=frame,
        )

    run = BacktestRun(
        strategy_version_id=inputs.version.id,
        dataset_version_id=inputs.series.id,
        engine_version=ENGINE_VERSION,
        feature_version=FEATURE_VERSION,
        parameters_json=parameters or {},
        execution_model_json=inputs.spec.execution.model_dump(),
        dataset_hash=inputs.dataset_hash,
        status="running",
        started_at=dt.datetime.now(tz=dt.UTC),
        progress=0,
        current_step="loading data",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run, inputs


def rebuild_backtest_inputs(
    db: Session,
    run: BacktestRun,
    *,
    timeframe: str | None = None,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> BacktestInputs:
    """Resolve a run's inputs again from the row alone, for a caller in another process.

    Only the row survives the handover, so this reverses the request from what the row
    keeps: the bound dataset fixes the series, and the stored ``execution_model_json`` is
    replayed as an override so the engine runs the execution model the request was answered
    with, not the one the strategy version would pick on its own. ``start``/``end`` are not
    on the row, so a caller that still has the request's window passes it; without a window
    the run covers the whole series, which is what a request that sent none asked for.
    """

    return load_backtest_inputs(
        db,
        strategy_version_id=run.strategy_version_id,
        series_id=run.dataset_version_id,
        timeframe=timeframe or (run.dataset.timeframe if run.dataset is not None else "1d"),
        start=start,
        end=end,
        execution_overrides=run.execution_model_json or None,
    )


def execute_backtest(
    db: Session,
    run: BacktestRun,
    inputs: BacktestInputs,
    *,
    parameters: dict[str, Any] | None = None,
    timeframe: str = "1d",
) -> BacktestExecution:
    """Run the engine on a prepared run and persist everything it produced.

    Writes the result, the metric rows, the trade rows and the ``backtest_completed`` audit
    event, stepping ``progress``/``current_step`` and committing as it goes so a polling
    client sees movement instead of one jump at the end. A run whose engine raises is
    recorded by :func:`fail_backtest`, which commits and re-raises :class:`BacktestFailed`,
    so the caller's transaction is never the only copy of a failure.
    """

    parameters = parameters or {}
    version = inputs.version
    series = inputs.series

    advance_backtest(db, run, "computing features")
    try:
        outcome = run_backtest(
            inputs.spec,
            inputs.frame,
            strategy_version=f"{version.strategy_id}@{version.version}",
            timeframe=timeframe,
            parameters=parameters,
        )
    except Exception as exc:
        fail_backtest(db, run, exc)

    advance_backtest(db, run, "computing metrics")

    run.feature_version = outcome.feature_version
    run.status = "completed"
    run.finished_at = dt.datetime.now(tz=dt.UTC)
    # 100 lands in the same commit as the result rows: a client that reads "completed"
    # must find the result it is told to expect, never a completed run with no result.
    run.progress = PROGRESS_LADDER["completed"]
    run.current_step = "completed"

    result = BacktestResult(
        backtest_run_id=run.id,
        summary_json=_summary(outcome, series),
        equity_curve_json=outcome.equity_curve,
        metrics_json=outcome.metrics,
        warnings_json=list(outcome.warnings),
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
            "strategy_version_id": version.id,
            "dataset_version_id": series.id,
            "dataset_hash": inputs.dataset_hash,
            "result_hash": outcome.result_hash,
            "engine_version": outcome.engine_version,
            "feature_version": outcome.feature_version,
        },
    )
    db.commit()
    db.refresh(run)

    _record_resource_event(db, run, outcome)

    return BacktestExecution(run=run, outcome=outcome)


def store_backtest(
    db: Session,
    inputs: BacktestInputs,
    *,
    parameters: dict[str, Any] | None = None,
    timeframe: str = "1d",
) -> BacktestExecution:
    """Prepare and immediately execute a run — the synchronous behaviour, in one call.

    This is what a caller that owns the request wants; it stays exactly the sequence the
    route used to run inline, including the committed ``status="failed"`` row behind
    :class:`BacktestFailed`.
    """

    run, _ = prepare_backtest(
        db,
        strategy_version_id=inputs.version.id,
        timeframe=timeframe,
        parameters=parameters,
        inputs=inputs,
    )
    return execute_backtest(db, run, inputs, parameters=parameters, timeframe=timeframe)


def fail_backtest(db: Session, run: BacktestRun, error: BaseException) -> NoReturn:
    """Record *error* on the run row, commit it, then raise :class:`BacktestFailed`.

    The commit is not an implementation detail: the exception unwinds the caller's
    transaction, and a run that failed while nobody was watching still has to be readable —
    a client polling the run must find the reason, not a run frozen at "running". The rung
    is left where it was (``current_step="failed"``, ``progress`` below 100), because a run
    that died in ``computing metrics`` did get that far.
    """

    run.status = "failed"
    run.error_message = str(error)[:500]
    run.finished_at = dt.datetime.now(tz=dt.UTC)
    run.current_step = "failed"
    db.commit()
    logger.exception("backtest failed")
    raise BacktestFailed(run) from error


def advance_backtest(db: Session, run: BacktestRun, step: str) -> None:
    """Move the run to *step* and commit, so a poller sees the move immediately.

    Public because the worker drives a step the engine cannot see: rebuilding its inputs
    from the row alone is real work in a second process, and without a commit at that point
    a queued run would show no sign of life until the engine call returned.
    """

    run.current_step = step
    run.progress = PROGRESS_LADDER[step]
    db.commit()


def _record_resource_event(db: Session, run: BacktestRun, outcome: Any) -> None:
    """Optional monitoring, deliberately in its own transaction after the run is committed.

    Window peaks come from the monitor's samples when they cover the run; a task shorter
    than one collection cycle leaves them null rather than invented. `record_resource_event`
    flushes, so a failure here would otherwise leave the session rollback-pending and turn
    a perfectly good backtest into a 500 (PendingRollbackError) — committing first means the
    rollback can only ever discard the monitoring row.
    """

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
        # Which provider the data came from. A run is only reproducible against the
        # same series, so the source has to travel with the result (ADR-119).
        "source": series.source.name if series.source is not None else None,
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
