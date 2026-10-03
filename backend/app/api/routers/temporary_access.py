"""Temporary remote access endpoints (ADR-125).

Three verbs on one resource: read the state, open a public URL, close it. The
manager owns the ``cloudflared`` process; this layer only maps its refusals to
status codes and writes the audit trail.

Nothing here can run a shell command: both the binary and the URL it exposes are
deployment settings, never request parameters, so there is no request shape that
turns this endpoint into a remote executor.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.data.strategy_service import record_audit
from app.infrastructure.temporary_access import TemporaryAccessError, manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings/temporary-access", tags=["settings"])


def _audit(db: Session, *, action: str, payload: dict[str, Any]) -> None:
    """Record that a public door was opened or closed.

    Bookkeeping must not be able to fail the request: if the row cannot be
    written, the tunnel is still real and the answer must still say so.
    """

    try:
        record_audit(
            db,
            event_type=f"temporary_access_{action}",
            entity_type="temporary_access",
            entity_id="tunnel",
            action=action,
            payload=payload,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("could not record the temporary access audit entry (%s)", action)


@router.get("", summary="Temporary remote access state")
def read_temporary_access() -> dict[str, Any]:
    """The current state, after enforcing liveness and the deadline once more."""

    return manager.status()


@router.post("/start", summary="Open a temporary public URL")
def start_temporary_access(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Start a Quick Tunnel; answers ``starting`` and is polled for the address."""

    try:
        state = manager.start()
    except TemporaryAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    _audit(
        db,
        action="start",
        payload={"target_url": state["target_url"], "expires_at": state["expires_at"]},
    )
    return state


@router.post("/stop", summary="Close the temporary public URL")
def stop_temporary_access(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Close the tunnel now; a no-op when none is open."""

    state = manager.stop()
    _audit(db, action="stop", payload={"target_url": state["target_url"]})
    return state
