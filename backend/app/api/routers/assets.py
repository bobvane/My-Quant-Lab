"""Asset endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import AssetCreate, AssetOut
from app.core.db import get_db
from app.domain.models import Asset, MarketDataSeries

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("", response_model=list[AssetOut], summary="List assets")
def list_assets(
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    asset_class: str | None = None,
    search: str | None = None,
) -> list[AssetOut]:
    stmt = select(Asset).where(Asset.is_active.is_(True))
    if asset_class:
        stmt = stmt.where(Asset.asset_class == asset_class)
    if search:
        stmt = stmt.where(Asset.symbol.ilike(f"%{search}%"))
    stmt = stmt.order_by(Asset.symbol).limit(limit).offset(offset)
    rows = db.scalars(stmt).all()
    return [AssetOut.model_validate(row) for row in rows]


@router.post("", response_model=AssetOut, status_code=201, summary="Create asset")
def create_asset(payload: AssetCreate, db: Session = Depends(get_db)) -> AssetOut:
    existing = db.scalar(select(Asset).where(Asset.symbol == payload.symbol))
    if existing is not None:
        raise HTTPException(status_code=409, detail="asset symbol already exists")
    asset = Asset(**payload.model_dump())
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return AssetOut.model_validate(asset)


@router.get("/{asset_id}", response_model=AssetOut, summary="Get asset")
def get_asset(asset_id: int, db: Session = Depends(get_db)) -> AssetOut:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    return AssetOut.model_validate(asset)


@router.get("/{asset_id}/series", summary="List data series for an asset")
def list_series(asset_id: int, db: Session = Depends(get_db)) -> list[dict]:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    series = db.scalars(select(MarketDataSeries).where(MarketDataSeries.asset_id == asset_id)).all()
    return [
        {
            "id": row.id,
            "timeframe": row.timeframe,
            "source_id": row.source_id,
            "dataset_version": row.dataset_version,
            "quality_status": row.quality_status,
            "bars": row.series_start is not None,
            "series_start": row.series_start,
            "series_end": row.series_end,
            "last_sync_at": row.last_sync_at,
        }
        for row in series
    ]
