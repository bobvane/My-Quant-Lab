"""Market data sync and bar retrieval."""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import BarOut, MarketDataSyncRequest
from app.core.config import settings
from app.core.db import get_db
from app.data.market_data_repo import (
    assess_bars_quality,
    frame_to_bars,
    get_or_create_series,
    load_bars,
    upsert_bars,
)
from app.data.providers import (
    ProviderError,
    SymbolNotServed,
    asset_metadata_for,
    get_market_data_provider,
    mark_closed_bars,
)
from app.domain.models import Asset, MarketDataBar, MarketDataSeries, MarketDataSource

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


@router.get("/data-sources", summary="List configured market data sources")
def list_data_sources(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(MarketDataSource).order_by(MarketDataSource.id)).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "provider_type": row.provider_type,
            "base_url": row.base_url,
            "rate_limit_per_minute": row.rate_limit_per_minute,
            "is_active": row.is_active,
            "has_api_key": bool(row.api_key_encrypted),
        }
        for row in rows
    ]


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


@router.get("/series/{series_id}", summary="Get one market data series")
def get_series(series_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(MarketDataSeries, series_id)
    if row is None:
        raise HTTPException(status_code=404, detail="market data series not found")
    bars = (
        db.scalar(
            select(func.count())
            .select_from(MarketDataBar)
            .where(MarketDataBar.series_id == series_id)
        )
        or 0
    )
    return {
        "id": row.id,
        "asset_id": row.asset_id,
        "timeframe": row.timeframe,
        "source_id": row.source_id,
        "dataset_version": row.dataset_version,
        "quality_status": row.quality_status,
        "adjusted": row.adjusted,
        "timezone": row.timezone,
        "series_start": row.series_start,
        "series_end": row.series_end,
        "last_sync_at": row.last_sync_at,
        "content_hash": row.content_hash,
        "bar_count": int(bars),
    }


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
    # Resolve the provider from the request, falling back to the configured
    # default. Previously the request field was accepted and then ignored, so a
    # caller could not actually choose the source.
    try:
        provider = get_market_data_provider(payload.provider)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    provider_name = provider.name

    end = payload.end or dt.datetime.now(tz=dt.UTC)
    start = payload.start or (end - dt.timedelta(days=payload.lookback_days))

    try:
        frame: pd.DataFrame = provider.get_ohlcv(payload.symbol, payload.timeframe, start, end)
    except SymbolNotServed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderError as exc:
        logger.warning("market data provider error for %s: %s", payload.symbol, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - unexpected provider failure
        logger.exception("market data sync failed for %s", payload.symbol)
        raise HTTPException(status_code=502, detail=f"provider error: {exc}") from exc

    if frame.empty:
        return {
            "symbol": payload.symbol,
            "timeframe": payload.timeframe,
            "provider": provider_name,
            "inserted": 0,
            "message": (
                "provider returned no bars. Possible causes: (1) the ticker is wrong; "
                "(2) the market has no data in this range; (3) the data provider is "
                "rate-limiting this IP — yfinance reports that as an empty result "
                "rather than an error, so retry later if the ticker is valid."
            ),
        }

    # Decide which bars have actually closed before persisting: a daily candle
    # covering today is still forming and must not be treated as usable input.
    frame = mark_closed_bars(frame, payload.timeframe, settings.default_timezone)
    closed_bars = int(frame["is_closed"].sum())
    forming_bars = int((~frame["is_closed"]).sum())

    asset = db.scalar(select(Asset).where(Asset.symbol == payload.symbol))
    if asset is None:
        meta = asset_metadata_for(provider, payload.symbol)
        asset = Asset(
            symbol=str(meta.get("symbol") or payload.symbol),
            display_name=meta.get("display_name"),
            asset_class=str(meta.get("asset_class") or "stock"),
            currency=str(meta.get("currency") or "USD"),
            exchange=meta.get("exchange"),
        )
        db.add(asset)
        db.flush()

    source = _ensure_source(db, provider_name)
    series = get_or_create_series(db, asset=asset, timeframe=payload.timeframe, source_id=source.id)
    inserted = upsert_bars(db, series, frame_to_bars(frame))
    quality_status, quality = assess_bars_quality(frame)
    series.quality_status = quality_status
    db.commit()
    return {
        "symbol": payload.symbol,
        "timeframe": payload.timeframe,
        "provider": provider_name,
        "series_id": series.id,
        "inserted": inserted,
        "closed_bars_in_fetch": closed_bars,
        "still_forming_bars": forming_bars,
        "series_start": series.series_start,
        "series_end": series.series_end,
        "quality_status": quality_status,
        "quality": quality,
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


@router.delete("/series/{series_id}", summary="Delete a series and all its bars")
def delete_series(series_id: int, db: Session = Depends(get_db)) -> dict:
    series = db.get(MarketDataSeries, series_id)
    if series is None:
        raise HTTPException(status_code=404, detail="series not found")
    asset = db.get(Asset, series.asset_id)
    symbol = asset.symbol if asset else str(series.asset_id)
    db.delete(series)
    db.commit()
    return {"deleted": series_id, "symbol": symbol}
