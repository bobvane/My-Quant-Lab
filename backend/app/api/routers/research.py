"""Research endpoints: walk-forward / out-of-sample analysis."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    EnsembleOut,
    EnsembleRequest,
    EnsembleSweepOut,
    EnsembleSweepRequest,
    MonteCarloOut,
    MonteCarloRequest,
    OOSOut,
    OOSRequest,
    SensitivityOut,
    SensitivityRequest,
    WalkForwardOut,
    WalkForwardRequest,
)
from app.core.db import get_db
from app.data.market_data_repo import load_bars, resolve_series
from app.data.strategy_service import load_spec, record_audit
from app.domain.models import (
    Asset,
    BacktestRun,
    BacktestTrade,
    MarketDataSeries,
    StrategyVersion,
)
from app.research.ensemble import EnsembleMember, run_ensemble, run_ensemble_sweep
from app.research.monte_carlo import run_monte_carlo
from app.research.sensitivity import run_sensitivity
from app.research.walk_forward import run_holdout, run_walk_forward

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/research", tags=["research"])


@router.post("/walk-forward", response_model=WalkForwardOut, summary="Run walk-forward")
def walk_forward(payload: WalkForwardRequest, db: Session = Depends(get_db)) -> WalkForwardOut:
    strategy_version = db.get(StrategyVersion, payload.strategy_version_id)
    if strategy_version is None:
        raise HTTPException(status_code=404, detail="strategy version not found")

    series = resolve_series(db, symbol=payload.symbol, timeframe=payload.timeframe)

    frame = load_bars(db, series, only_closed=True)
    if len(frame) < payload.train_bars + payload.test_bars:
        raise HTTPException(
            status_code=422,
            detail=(
                f"need at least {payload.train_bars + payload.test_bars} bars, "
                f"series has {len(frame)}"
            ),
        )

    spec = load_spec(strategy_version)
    outcome = run_walk_forward(
        spec,
        frame,
        train_bars=payload.train_bars,
        test_bars=payload.test_bars,
        step=payload.step,
        strategy_version=f"{strategy_version.strategy_id}@{strategy_version.version}",
        timeframe=payload.timeframe,
    )
    record_audit(
        db,
        event_type="walk_forward_completed",
        entity_type="strategy_version",
        entity_id=str(strategy_version.id),
        action="run",
        payload={"windows": outcome["windows"], "summary": outcome["summary"]},
    )
    db.commit()
    return WalkForwardOut(**outcome)


@router.post("/oos", response_model=OOSOut, summary="Out-of-sample holdout split")
def oos(payload: OOSRequest, db: Session = Depends(get_db)) -> OOSOut:
    """Split into an in-sample and an out-of-sample window (docs/07 §11).

    The test window is the last ``oos_pct`` of bars, or everything from
    ``oos_start`` onward. The same strategy spec is used for both sides.
    """

    strategy_version = db.get(StrategyVersion, payload.strategy_version_id)
    if strategy_version is None:
        raise HTTPException(status_code=404, detail="strategy version not found")

    series = resolve_series(db, symbol=payload.symbol, timeframe=payload.timeframe)

    frame = load_bars(db, series, only_closed=True)
    spec = load_spec(strategy_version)
    try:
        outcome = run_holdout(
            spec,
            frame,
            oos_pct=payload.oos_pct,
            oos_start=payload.oos_start,
            strategy_version=f"{strategy_version.strategy_id}@{strategy_version.version}",
            timeframe=payload.timeframe,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    record_audit(
        db,
        event_type="oos_completed",
        entity_type="strategy_version",
        entity_id=str(strategy_version.id),
        action="run",
        payload={
            "split_time": outcome["split_time"],
            "in_sample": outcome["in_sample"],
            "out_of_sample": outcome["out_of_sample"],
        },
    )
    db.commit()
    return OOSOut(**outcome)


@router.post(
    "/sensitivity",
    response_model=SensitivityOut,
    summary="Parameter sensitivity sweep",
)
def sensitivity(payload: SensitivityRequest, db: Session = Depends(get_db)) -> SensitivityOut:
    """Sweep declared parameters and report how the metrics respond (docs/21).

    Descriptive only: the report ranks grid points so a human can see the shape of
    the surface. It never recommends parameters, and the AI layer never touches
    these numbers (docs/02 §3).
    """

    strategy_version = db.get(StrategyVersion, payload.strategy_version_id)
    if strategy_version is None:
        raise HTTPException(status_code=404, detail="strategy version not found")

    series = resolve_series(db, symbol=payload.symbol, timeframe=payload.timeframe)

    frame = load_bars(db, series, only_closed=True)
    if len(frame) < 60:
        raise HTTPException(
            status_code=422,
            detail=f"need at least 60 closed bars, series has {len(frame)}",
        )

    spec = load_spec(strategy_version)
    try:
        outcome = run_sensitivity(
            spec,
            frame,
            grid=payload.grid,
            base_parameters=payload.base_parameters,
            metric=payload.metric,
            strategy_version=f"{strategy_version.strategy_id}@{strategy_version.version}",
            timeframe=payload.timeframe,
        )
    except ValueError as exc:
        # Bad grid shape / unknown axis / too many points are all caller errors.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    record_audit(
        db,
        event_type="sensitivity_completed",
        entity_type="strategy_version",
        entity_id=str(strategy_version.id),
        action="run",
        payload={
            "metric": outcome["metric"],
            "grid_points": outcome["grid_points"],
            "evaluated_points": outcome["evaluated_points"],
            "axes": outcome["axes"],
            "summary": outcome["summary"],
        },
    )
    db.commit()
    return SensitivityOut(**outcome)


@router.post(
    "/monte-carlo",
    response_model=MonteCarloOut,
    summary="Resample a backtest's trades (Monte Carlo)",
)
def monte_carlo(payload: MonteCarloRequest, db: Session = Depends(get_db)) -> MonteCarloOut:
    """Bootstrap a completed backtest's trades into a distribution (docs/22).

    Reads the trades already stored for the run — it does **not** re-run the
    backtest, so the distribution is anchored to the exact result being examined.
    This is a resampling of history, not a forecast.
    """

    run = db.get(BacktestRun, payload.backtest_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest run not found")
    if run.status != "completed":
        raise HTTPException(
            status_code=422,
            detail=f"backtest run {run.id} is '{run.status}'; only completed runs can be resampled",
        )

    # The seeded RNG draws by *index*, so the pool's order is part of the result: read it
    # in a pinned order instead of trusting heap order (ADR-197).
    rows = db.scalars(
        select(BacktestTrade)
        .where(BacktestTrade.backtest_run_id == run.id)
        .order_by(BacktestTrade.id)
    ).all()
    trades = [{"pnl": float(r.pnl)} for r in rows if r.pnl is not None]
    if not trades:
        raise HTTPException(
            status_code=422,
            detail="this backtest produced no closed trades, so there is nothing to resample",
        )

    execution = run.execution_model_json or {}
    try:
        initial_capital = float(execution.get("initial_capital") or 0.0)
    except (TypeError, ValueError):
        initial_capital = 0.0
    if initial_capital <= 0:
        raise HTTPException(
            status_code=422,
            detail="the backtest run does not record a positive initial_capital",
        )

    # Annualisation must use the timeframe the run actually used, not something the
    # caller asserts — BacktestRun has no timeframe column, so read it from the
    # dataset series it ran against.
    series = db.get(MarketDataSeries, run.dataset_version_id)
    timeframe = series.timeframe if series is not None else "1d"

    try:
        outcome = run_monte_carlo(
            trades,
            initial_capital=initial_capital,
            runs=payload.runs,
            trades_per_run=payload.trades_per_run,
            seed=payload.seed,
            timeframe=timeframe,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    record_audit(
        db,
        event_type="monte_carlo_completed",
        entity_type="backtest_run",
        entity_id=str(run.id),
        action="run",
        payload={
            "runs": payload.runs,
            "seed": payload.seed,
            "method": outcome["method"],
            "timeframe": timeframe,
            "observed_trades": outcome["summary"]["observed_trades"],
            "probability_of_profit": outcome["summary"]["probability_of_profit"],
        },
    )
    db.commit()
    return MonteCarloOut(**outcome)


@router.post(
    "/ensemble",
    response_model=EnsembleOut,
    summary="Vote several strategy versions into one portfolio",
)
def ensemble(payload: EnsembleRequest, db: Session = Depends(get_db)) -> EnsembleOut:
    """Combine strategy versions by weighted vote (docs/24, ADR-047).

    Members must share a dataset and timeframe, so the basket is resolved once and the
    same bars are handed to every member.
    """

    labelled, series, asset, frame = _resolve_ensemble_inputs(payload, db)

    try:
        outcome = run_ensemble(
            [member for _, member in labelled],
            frame,
            vote_threshold=payload.vote_threshold,
            # The field is named `execution_overrides`, so its keys are execution fields
            # (fee_bps, initial_capital, sizing, ...) — the same shape POST /backtests
            # accepts. `merge_spec_overrides` wants spec-level keys, hence the wrap.
            spec_overrides={"execution": payload.execution_overrides}
            if payload.execution_overrides
            else None,
            strategy_version="ensemble",
            timeframe=payload.timeframe,
        )
    except ValueError as exc:
        # No members / too many / bad threshold / no common bars are caller errors.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Which dataset the vote ran on. The engine sees only bars, so the series identity
    # is attached here; the comparison table uses it to tell a comparable member run
    # from one on a different symbol/timeframe.
    outcome["dataset_version_id"] = series.id
    outcome["symbol"] = asset.symbol if asset is not None else None
    outcome["timeframe"] = series.timeframe

    record_audit(
        db,
        event_type="ensemble_completed",
        entity_type="strategy",
        entity_id="+".join(str(v.strategy_id) for v, _ in labelled),
        action="run",
        payload={
            "members": [{"strategy_version_id": v.id, "weight": m.weight} for v, m in labelled],
            "vote_threshold": outcome["vote_threshold"],
            "bars_evaluated": outcome["bars_evaluated"],
            "agreement": outcome["agreement"],
            "final_equity": outcome["final_equity"],
        },
    )
    db.commit()
    return EnsembleOut(**outcome)


@router.post(
    "/ensemble/sweep",
    response_model=EnsembleSweepOut,
    summary="Vote the same members at several thresholds",
)
def ensemble_sweep(
    payload: EnsembleSweepRequest, db: Session = Depends(get_db)
) -> EnsembleSweepOut:
    """Sweep the ensemble's only knob and report the shape (docs/24 §7, ADR-052).

    ``vote_threshold`` is the one setting an ensemble has, and because the weighted
    vote is a sum of member weights it can only land on coalition totals: the surface
    is a staircase, not a curve. Running one threshold hides which coalitions were
    skipped. This is descriptive, exactly like the parameter sensitivity sweep
    (docs/21) — it never recommends a threshold.
    """

    labelled, series, asset, frame = _resolve_ensemble_inputs(payload, db)

    try:
        outcome = run_ensemble_sweep(
            [member for _, member in labelled],
            frame,
            thresholds=payload.thresholds,
            spec_overrides={"execution": payload.execution_overrides}
            if payload.execution_overrides
            else None,
            strategy_version="ensemble",
            timeframe=payload.timeframe,
        )
    except ValueError as exc:
        # No members / bad threshold / too many thresholds / no common bars.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    outcome["dataset_version_id"] = series.id
    outcome["symbol"] = asset.symbol if asset is not None else None
    outcome["timeframe"] = series.timeframe

    record_audit(
        db,
        event_type="ensemble_sweep_completed",
        entity_type="strategy",
        entity_id="+".join(str(v.strategy_id) for v, _ in labelled),
        action="run",
        payload={
            "members": [{"strategy_version_id": v.id, "weight": m.weight} for v, m in labelled],
            "thresholds": outcome["thresholds"],
            "bars_evaluated": outcome["bars_evaluated"],
            "possible_votes": outcome["possible_votes"],
            "entries_taken": [
                {"vote_threshold": p["vote_threshold"], "entries_taken": p["entries_taken"]}
                for p in outcome["points"]
            ],
        },
    )
    db.commit()
    return EnsembleSweepOut(**outcome)


def _resolve_ensemble_inputs(
    payload: EnsembleRequest, db: Session
) -> tuple[list[tuple[StrategyVersion, EnsembleMember]], MarketDataSeries, Asset | None, Any]:
    """Resolve members and the shared bar series for an ensemble request.

    Extracted so the single-threshold and sweep endpoints resolve their inputs the same
    way: the two must never disagree about which bars were voted on, or the sweep would
    describe an ensemble the endpoint does not produce.
    """

    # Resolve every member up front: a missing version is a 404 before any work happens.
    #
    # Duplicates are rejected rather than merged. Submitting the same version twice
    # normalises to two 0.5 weights, which makes the strict-majority threshold
    # trivially satisfiable by that one strategy — the report would show a "vote" that
    # is really just a single member's own signal, which is precisely the deception an
    # ensemble is supposed to avoid.
    seen_versions: set[int] = set()
    duplicates: list[int] = []
    for entry in payload.members:
        if entry.strategy_version_id in seen_versions:
            duplicates.append(entry.strategy_version_id)
        seen_versions.add(entry.strategy_version_id)
    if duplicates:
        raise HTTPException(
            status_code=422,
            detail=(
                "duplicate members: strategy version(s) "
                + ", ".join(str(v) for v in sorted(set(duplicates)))
                + " appear more than once. Voting with a single strategy against itself"
                " is not an ensemble; give each version once (use its weight instead)."
            ),
        )

    labelled: list[tuple[StrategyVersion, EnsembleMember]] = []
    for entry in payload.members:
        version = db.get(StrategyVersion, entry.strategy_version_id)
        if version is None:
            raise HTTPException(
                status_code=404,
                detail=f"strategy version {entry.strategy_version_id} not found",
            )
        labelled.append(
            (
                version,
                EnsembleMember(
                    label=f"{version.strategy_id}@{version.version}",
                    spec=load_spec(version),
                    weight=entry.weight,
                ),
            )
        )

    series = resolve_series(db, symbol=payload.symbol, timeframe=payload.timeframe)
    asset = series.asset

    frame = load_bars(db, series, only_closed=True)
    if len(frame) < 60:
        raise HTTPException(
            status_code=422,
            detail=f"need at least 60 closed bars, series has {len(frame)}",
        )

    return labelled, series, asset, frame
