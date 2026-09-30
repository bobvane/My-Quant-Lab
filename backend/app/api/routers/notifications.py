"""Notification endpoints (M11).

Configure the generic webhook, verify it, and inspect what was sent. Secrets
stay write-only: the URL and signing secret are only ever returned masked.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    NotificationConfigOut,
    NotificationConfigUpdate,
    NotificationTestOut,
)
from app.core.db import get_db
from app.data.strategy_service import record_audit
from app.domain.models import AuditLog
from app.notifications.config import (
    get_notification_config,
    serialize_notification_config,
    update_notification_config,
)
from app.notifications.provider import NotificationConfigError
from app.notifications.service import (
    EVENT_FAILED,
    EVENT_NOTIFIED,
    EVENT_SUPPRESSED,
    send_test_notification,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["notifications"])

_NOTIFICATION_EVENTS = (EVENT_NOTIFIED, EVENT_FAILED, EVENT_SUPPRESSED, "notification_test")


@router.get("/config", response_model=NotificationConfigOut, summary="Notification settings")
def read_config(db: Session = Depends(get_db)) -> dict[str, Any]:
    return serialize_notification_config(get_notification_config(db))


@router.put("/config", response_model=NotificationConfigOut, summary="Update notification settings")
def update_config(
    payload: NotificationConfigUpdate, db: Session = Depends(get_db)
) -> dict[str, Any]:
    changes = payload.model_dump(exclude_unset=True)
    try:
        config = update_notification_config(db, changes)
    except NotificationConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    record_audit(
        db,
        event_type="notification_config_updated",
        entity_type="notification",
        entity_id="webhook",
        action="update",
        # Never carries the URL or the signing secret.
        payload={"fields": sorted(changes)},
    )
    db.commit()
    return serialize_notification_config(config)


@router.post("/test", response_model=NotificationTestOut, summary="Send a test notification")
def test_notification(db: Session = Depends(get_db)) -> dict[str, Any]:
    return send_test_notification(db)


@router.get("/events", summary="Recent notification deliveries, failures and suppressions")
def list_events(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.event_type.in_(_NOTIFICATION_EVENTS))
        .order_by(AuditLog.id.desc())
        .limit(limit)
    ).all()
    return {
        "events": [
            {
                "id": row.id,
                "event_type": row.event_type,
                "payload": row.payload_json,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    }
