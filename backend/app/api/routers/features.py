"""Feature definition endpoints (docs/12)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.models import FeatureDefinition

router = APIRouter(prefix="/features", tags=["features"])


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
