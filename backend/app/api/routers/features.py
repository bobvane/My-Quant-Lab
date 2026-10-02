"""Feature definition endpoints (docs/12)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.models import FeatureDefinition

router = APIRouter(prefix="/features", tags=["features"])


@router.get("/versions", summary="Distinct feature versions across definitions/snapshots")
def feature_versions(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.domain.models import FeatureSnapshot
    from app.features.engine import FEATURE_VERSION

    definition_versions = sorted(
        {row.feature_version for row in db.scalars(select(FeatureDefinition)).all()}
    )
    snapshot_versions = sorted(
        {row.feature_version for row in db.scalars(select(FeatureSnapshot)).all()}
    )
    return {
        "engine_feature_version": FEATURE_VERSION,
        "definition_versions": definition_versions,
        "snapshot_versions": snapshot_versions,
    }


@router.get("", summary="List feature definitions")
def list_features(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.scalars(select(FeatureDefinition).order_by(FeatureDefinition.name)).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "feature_type": row.feature_type,
            "feature_version": row.feature_version,
            "description": row.description,
            "inputs": row.inputs_json,
            "params": row.params_json,
            "is_deterministic": row.is_deterministic,
            "lookahead_safe": row.lookahead_safe,
        }
        for row in rows
    ]
