"""Feature snapshot endpoints (docs/09 §5, docs/11).

Signals store the exact feature row they were computed from, so a signal can be
reproduced and audited later.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.models import FeatureSnapshot, MarketDataSeries

router = APIRouter(prefix="/feature-snapshots", tags=["features"])


def _serialize(row: FeatureSnapshot) -> dict[str, Any]:
    return {
        "series_id": row.series_id,
        "bar_timestamp": row.bar_timestamp,
        "feature_version": row.feature_version,
        "input_hash": row.input_hash,
        "values": row.values_json,
        "available_at": row.available_at,
    }


@router.get("/{series_id}", summary="Recent feature snapshots for a series")
def list_snapshots(
    series_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    if db.get(MarketDataSeries, series_id) is None:
        raise HTTPException(status_code=404, detail="market data series not found")
    rows = db.scalars(
        select(FeatureSnapshot)
        .where(FeatureSnapshot.series_id == series_id)
        .order_by(FeatureSnapshot.bar_timestamp.desc())
        .limit(limit)
    ).all()
    return [_serialize(row) for row in rows]


@router.get("/{series_id}/latest", summary="Latest feature snapshot for a series")
def latest_snapshot(series_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    if db.get(MarketDataSeries, series_id) is None:
        raise HTTPException(status_code=404, detail="market data series not found")
    row = db.scalar(
        select(FeatureSnapshot)
        .where(FeatureSnapshot.series_id == series_id)
        .order_by(FeatureSnapshot.bar_timestamp.desc())
        .limit(1)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="no feature snapshot for this series")
    return _serialize(row)
