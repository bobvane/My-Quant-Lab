"""Market data sync and bar retrieval."""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import BarOut, MarketDataSyncRequest
from app.core.db import get_db
from app.data.market_data_repo import (
    frame_to_bars,
    get_or_create_series,
    load_bars,
    upsert_bars,
)
from app.data.providers import get_market_data_provider
from app.domain.models import Asset, MarketDataSeries, MarketDataSource

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/market-data", tags=["market-data"])


def _ensure_source(db: Session, provider_name: str) -> MarketDataSource:
    source = db.scalar(select(MarketDataSource).where(MarketDataSource.name == provider_name))
    if source is None:
        source = MarketDataSource(
            name=provider_name,
            provider_type="rest_api",
            base_url=f"provider://{provider_name}",
        )
        db.add(source)
        db.flush()
    return source


@router.get("/series", summary="List market data series")
def list_series(
    db: Session = Depends(get_db),
    asset_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    stmt = select(MarketDataSeries)
    if asset_id:
        stmt = stmt.where(MarketDataSeries.asset_id == asset_id)
    rows = db.scalars(stmt.order_by(MarketDataSeries.id).limit(limit)).all()
    return [
        {
            "id": row.id,
            "asset_id": row.asset_id,
            "timeframe": row.timeframe,
            "source_id": row.source_id,
            "dataset_version": row.dataset_version,
            "quality_status": row.quality_status,
            "series_start": row.series_start,
            "series_end": row.series_end,
            "last_sync_at": row.last_sync_at,
        }
        for row in rows
    ]


@router.get("/series/{series_id}/bars", response_model=list[BarOut], summary="List bars")
def list_bars(
    series_id: int,
    db: Session = Depends(get_db),
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    limit: int = Query(default=500, ge=1, le=20_000),
) -> list[BarOut]:
    series = db.get(MarketDataSeries, series_id)
    if series is None:
        raise HTTPException(status_code=404, detail="series not found")
    frame = load_bars(db, series, start=start, end=end, limit=limit)
    if frame.empty:
        return []
    return [
        BarOut(
            timestamp=ts,
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            is_closed=True,
        )
        for ts, row in frame.iterrows()
    ]


@router.post("/sync", summary="Sync OHLCV data for one symbol")
def sync_market_data(payload: MarketDataSyncRequest, db: Session = Depends(get_db)) -> dict:
    provider_name = payload.provider or "synthetic"
    try:
        provider = get_market_data_provider()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    end = payload.end or dt.datetime.now(tz=dt.UTC)
    start = payload.start or (end - dt.timedelta(days=payload.lookback_days))

    try:
        frame: pd.DataFrame = provider.get_ohlcv(payload.symbol, payload.timeframe, start, end)
    except Exception as exc:  # pragma: no cover - network/provider specific
        logger.exception("market data sync failed for %s", payload.symbol)
        raise HTTPException(status_code=502, detail=f"provider error: {exc}") from exc

    if frame.empty:
        return {
            "symbol": payload.symbol,
            "timeframe": payload.timeframe,
            "provider": provider_name,
            "inserted": 0,
            "message": "provider returned no bars",
        }

    asset = db.scalar(select(Asset).where(Asset.symbol == payload.symbol))
    if asset is None:
        sample = next((a for a in provider.list_assets() if a["symbol"] == payload.symbol), None)
        asset = Asset(
            symbol=payload.symbol,
            display_name=(sample or {}).get("display_name", payload.symbol),
            asset_class=(sample or {}).get("asset_class", "stock"),
            currency=(sample or {}).get("currency", "USD"),
            exchange=(sample or {}).get("exchange"),
        )
        db.add(asset)
        db.flush()

    source = _ensure_source(db, provider_name)
    series = get_or_create_series(db, asset=asset, timeframe=payload.timeframe, source_id=source.id)
    inserted = upsert_bars(db, series, frame_to_bars(frame))
    series.quality_status = "valid" if inserted else series.quality_status
    db.commit()
    return {
        "symbol": payload.symbol,
        "timeframe": payload.timeframe,
        "provider": provider_name,
        "series_id": series.id,
        "inserted": inserted,
        "series_start": series.series_start,
        "series_end": series.series_end,
    }


@router.get("/latest/{symbol}", summary="Latest bars for a symbol")
def latest_bars(
    symbol: str,
    db: Session = Depends(get_db),
    timeframe: str = "1d",
    limit: int = Query(default=30, ge=1, le=500),
) -> dict:
    asset = db.scalar(select(Asset).where(Asset.symbol == symbol))
    if asset is None:
        raise HTTPException(status_code=404, detail=f"asset '{symbol}' not found")
    series = db.scalar(
        select(MarketDataSeries)
        .where(MarketDataSeries.asset_id == asset.id, MarketDataSeries.timeframe == timeframe)
        .order_by(MarketDataSeries.id)
    )
    if series is None:
        raise HTTPException(status_code=404, detail="no series for this symbol/timeframe")
    frame = load_bars(db, series, only_closed=True)
    if frame.empty:
        return {"symbol": symbol, "timeframe": timeframe, "bars": []}
    tail = frame.tail(limit)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "series_id": series.id,
        "bars": [
            {
                "timestamp": ts.isoformat(),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": float(row.volume),
            }
            for ts, row in tail.iterrows()
        ],
    }
