"""The single place a backtest is executed and persisted (ADR-174).

``POST /backtests`` and ``POST /experiments`` with ``kind="backtest"`` must produce the
same artifact: a ``BacktestRun`` plus its ``BacktestResult``, metric rows, trade rows and
``backtest_completed`` audit event. That persistence used to be inline in the backtests
route handler; it lives here so an experiment cannot grow a second, subtly different copy
of it (a real ledger, the metric grouping and the optional resource event are easy to
drift apart).

Nothing here implements a quant algorithm: ``app.research.engine.run_backtest`` remains
the only engine.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

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
    "BacktestExecution",
    "BacktestFailed",
    "BacktestInputs",
    "BacktestRequestError",
    "load_backtest_inputs",
    "store_backtest",
]

#: Fewer closed bars than this cannot produce a meaningful run.
MIN_BACKTEST_BARS = 60

#: The five numbers the two comparison endpoints project side by side (``GET
#: /backtests/compare`` and ``GET /experiments/compare``). Defined once so an experiment
#: history cannot end up showing a different set of columns than a run history does.
COMPARE_METRICS = ("total_return", "max_drawdown", "sharpe", "win_rate", "number_of_trades")


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


def store_backtest(
    db: Session,
    inputs: BacktestInputs,
    *,
    parameters: dict[str, Any] | None = None,
    timeframe: str = "1d",
) -> BacktestExecution:
    """Run the engine and persist run, result, metric rows, trade rows and the audit event.

    Raises :class:`BacktestFailed` after committing a ``status="failed"`` run row, so the
    failure is never swallowed and never lost with the request.
    """

    parameters = parameters or {}
    version = inputs.version
    series = inputs.series

    run = BacktestRun(
        strategy_version_id=version.id,
        dataset_version_id=series.id,
        engine_version=ENGINE_VERSION,
        feature_version=FEATURE_VERSION,
        parameters_json=parameters,
        execution_model_json=inputs.spec.execution.model_dump(),
        dataset_hash=inputs.dataset_hash,
        status="running",
        started_at=dt.datetime.now(tz=dt.UTC),
    )
    db.add(run)
    db.flush()

    try:
        outcome = run_backtest(
            inputs.spec,
            inputs.frame,
            strategy_version=f"{version.strategy_id}@{version.version}",
            timeframe=timeframe,
            parameters=parameters,
        )
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)[:500]
        run.finished_at = dt.datetime.now(tz=dt.UTC)
        db.commit()
        logger.exception("backtest failed")
        raise BacktestFailed(run) from exc

    run.feature_version = outcome.feature_version
    run.status = "completed"
    run.finished_at = dt.datetime.now(tz=dt.UTC)

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
