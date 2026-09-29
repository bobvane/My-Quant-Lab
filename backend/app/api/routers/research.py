"""Research endpoints: walk-forward / out-of-sample analysis."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import WalkForwardOut, WalkForwardRequest
from app.core.db import get_db
from app.data.market_data_repo import load_bars
from app.data.strategy_service import load_spec, record_audit
from app.domain.models import Asset, MarketDataSeries, StrategyVersion
from app.research.walk_forward import run_walk_forward

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
