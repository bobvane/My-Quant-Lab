"""Research endpoints: walk-forward / out-of-sample analysis."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    OOSOut,
    OOSRequest,
    SensitivityOut,
    SensitivityRequest,
    WalkForwardOut,
    WalkForwardRequest,
)
from app.core.db import get_db
from app.data.market_data_repo import load_bars
from app.data.strategy_service import load_spec, record_audit
from app.domain.models import Asset, MarketDataSeries, StrategyVersion
from app.research.sensitivity import run_sensitivity
from app.research.walk_forward import run_holdout, run_walk_forward

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/research", tags=["research"])


@router.post("/walk-forward", response_model=WalkForwardOut, summary="Run walk-forward")
def walk_forward(payload: WalkForwardRequest, db: Session = Depends(get_db)) -> WalkForwardOut:
    strategy_version = db.get(StrategyVersion, payload.strategy_version_id)
    if strategy_version is None:
        raise HTTPException(status_code=404, detail="strategy version not found")

    if payload.symbol:
        asset = db.scalar(select(Asset).where(Asset.symbol == payload.symbol))
        if asset is None:
            raise HTTPException(status_code=404, detail=f"asset '{payload.symbol}' not found")
        series = db.scalar(
            select(MarketDataSeries).where(
                MarketDataSeries.asset_id == asset.id,
                MarketDataSeries.timeframe == payload.timeframe,
            )
        )
    else:
        series = None
    if series is None:
        raise HTTPException(status_code=404, detail="market data series not found")

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

    asset = (
        db.scalar(select(Asset).where(Asset.symbol == payload.symbol)) if payload.symbol else None
    )
    if payload.symbol and asset is None:
        raise HTTPException(status_code=404, detail=f"asset '{payload.symbol}' not found")
    series = db.scalar(
        select(MarketDataSeries).where(
            MarketDataSeries.timeframe == payload.timeframe,
            *([MarketDataSeries.asset_id == asset.id] if asset else []),
        )
    )
    if series is None:
        raise HTTPException(status_code=404, detail="market data series not found")

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

    asset = (
        db.scalar(select(Asset).where(Asset.symbol == payload.symbol)) if payload.symbol else None
    )
    if payload.symbol and asset is None:
        raise HTTPException(status_code=404, detail=f"asset '{payload.symbol}' not found")
    series = db.scalar(
        select(MarketDataSeries).where(
            MarketDataSeries.timeframe == payload.timeframe,
            *([MarketDataSeries.asset_id == asset.id] if asset else []),
        )
    )
    if series is None:
        raise HTTPException(status_code=404, detail="market data series not found")

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
