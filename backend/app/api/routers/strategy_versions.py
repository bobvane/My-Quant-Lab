"""Strategy version management (docs/03 M04, docs/12).

Versions themselves are immutable; what changes here is only *which* version is
the active one for a strategy. Every activation is audited.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import StrategyVersionOut
from app.core.db import get_db
from app.data.strategy_service import (
    ACTIVATABLE_VALIDATION_STATUSES,
    activation_refusal,
    record_audit,
)
from app.domain.models import StrategyParameter, StrategyVersion

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/strategy-versions", tags=["strategies"])


def _serialize(row: StrategyVersion) -> StrategyVersionOut:
    return StrategyVersionOut(
        id=row.id,
        strategy_id=row.strategy_id,
        version=row.version,
        schema_version=row.schema_version,
        immutable_hash=row.immutable_hash,
        validation_status=row.validation_status,
        is_current=row.is_current,
        created_at=row.created_at,
        dsl=row.dsl_json,
    )


@router.get("", response_model=list[StrategyVersionOut], summary="List strategy versions")
def list_versions(
    db: Session = Depends(get_db),
    strategy_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[StrategyVersionOut]:
    stmt = select(StrategyVersion)
    if strategy_id is not None:
        stmt = stmt.where(StrategyVersion.strategy_id == strategy_id)
    rows = db.scalars(stmt.order_by(StrategyVersion.id.desc()).limit(limit)).all()
    return [_serialize(row) for row in rows]


@router.get("/{version_id}", response_model=StrategyVersionOut, summary="Get a strategy version")
def get_version(version_id: int, db: Session = Depends(get_db)) -> StrategyVersionOut:
    row = db.get(StrategyVersion, version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="strategy version not found")
    return _serialize(row)


@router.get("/{version_id}/parameters", summary="Parameters stored for a version")
def get_parameters(version_id: int, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    if db.get(StrategyVersion, version_id) is None:
        raise HTTPException(status_code=404, detail="strategy version not found")
    rows = db.scalars(
        select(StrategyParameter)
        .where(StrategyParameter.strategy_version_id == version_id)
        .order_by(StrategyParameter.id)
    ).all()
    return [
        {
            "id": row.id,
            "parameters": row.parameters_json,
            "description": row.description,
            "is_default": row.is_default,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.put(
    "/{version_id}/activate", response_model=StrategyVersionOut, summary="Activate a version"
)
def activate_version(version_id: int, db: Session = Depends(get_db)) -> StrategyVersionOut:
    row = db.get(StrategyVersion, version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="strategy version not found")
    if row.validation_status not in ACTIVATABLE_VALIDATION_STATUSES:
        # Activation is what puts a rule set on the signal path, so a version the
        # validator did not mark `valid` may not take it (ADR-171). The refusal is
        # itself auditable: "someone tried to activate an unvalidated version" is a
        # fact worth keeping, and nothing else is written.
        record_audit(
            db,
            event_type="strategy_version_activation_rejected",
            entity_type="strategy_version",
            entity_id=str(row.id),
            action="reject",
            payload={
                "strategy_id": row.strategy_id,
                "version": row.version,
                "validation_status": row.validation_status,
                "reason": "validation_status_not_valid",
            },
        )
        db.commit()
        raise HTTPException(status_code=422, detail=activation_refusal(row.validation_status))
    db.query(StrategyVersion).filter(
        StrategyVersion.strategy_id == row.strategy_id,
        StrategyVersion.id != row.id,
    ).update({"is_current": False})
    row.is_current = True
    record_audit(
        db,
        event_type="strategy_version_activated",
        entity_type="strategy_version",
        entity_id=str(row.id),
        action="activate",
        payload={"strategy_id": row.strategy_id, "version": row.version},
    )
    db.commit()
    db.refresh(row)
    return _serialize(row)
