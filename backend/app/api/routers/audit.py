"""Audit log query endpoints (docs/12)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.models import AuditLog

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/logs", summary="Recent audit events")
def audit_logs(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    rows = db.scalars(
        select(AuditLog).order_by(AuditLog.id.desc()).limit(limit).offset(offset)
    ).all()
    return {"total": len(rows), "events": [_serialize(r) for r in rows]}


@router.get("/logs/entity/{entity_type}/{entity_id}", summary="Audit events for one entity")
def audit_for_entity(
    entity_type: str,
    entity_id: str,
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
        .order_by(AuditLog.id.desc())
        .limit(limit)
    ).all()
    return {"total": len(rows), "events": [_serialize(r) for r in rows]}


def _serialize(row: AuditLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_type": row.event_type,
        "actor": row.actor,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "action": row.action,
        "payload": row.payload_json,
        "created_at": row.created_at,
    }
