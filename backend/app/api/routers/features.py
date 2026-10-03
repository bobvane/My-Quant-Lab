"""Feature endpoints (docs/12).

The catalogue is served from code — ``app.features.catalogue`` — because the features
are computed in code. The ``features`` table this module used to read was never written
by anything, so ``GET /features`` answered an empty list for the whole life of the
project (ADR-093). What is still read from the database is the set of feature versions
that were actually persisted into ``feature_snapshots``, which is real evidence.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.models import FeatureSnapshot
from app.features.catalogue import catalogue_payload
from app.features.engine import FEATURE_VERSION
from app.features.indicators import INDICATOR_VERSION
from app.features.price_action import PA_FEATURE_VERSION

router = APIRouter(prefix="/features", tags=["features"])


@router.get("/versions", summary="Feature versions: what the engine builds and what is stored")
def feature_versions(db: Session = Depends(get_db)) -> dict[str, Any]:
    snapshot_versions = sorted(
        {row.feature_version for row in db.scalars(select(FeatureSnapshot)).all()}
    )
    return {
        "engine_feature_version": FEATURE_VERSION,
        "indicator_version": INDICATOR_VERSION,
        "price_action_version": PA_FEATURE_VERSION,
        "snapshot_versions": snapshot_versions,
    }


@router.get("", summary="List the features this engine computes")
def list_features() -> list[dict[str, Any]]:
    return catalogue_payload()
