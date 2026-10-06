"""The human confirmation of a strategy draft (v2.4.0 Step 1).

A draft is a proposal. The research layer writes it, the compiler may formalize
it, and neither may put it live. The one thing no machine in this repository can
produce is a person saying "yes, this is the strategy I meant" -- so that is
what this module records, and it records it as an append-only audit event.

Three properties are deliberate:

* **Recording a decision never creates a ``StrategyVersion``.** No row, no
  ``validation_status``, no ``is_current``. A confirmation is evidence about a
  draft, not a strategy.
* **The audit log is the store.** ``strategy_drafts`` has no spare column (its
  ``status`` already carries the capability verdict), and v2.4.0 Step 1 is not
  allowed a migration, so the record lives where append-only evidence already
  lives: ``audit_logs``, indexed by ``(entity_type, entity_id)``.
* **The latest event is the current decision.** Reading is "newest row for this
  draft", which is why a re-decision needs no update path.

The gate this serves is a *process* gate: the draft is the AI's proposal, this
record is a human's answer, and a live strategy still needs the deterministic
compiler plus the ADR-171 activation gate (``validation_status == "valid"``).
Nothing here can shortcut either of them.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.strategy_service import record_audit
from app.domain.models import AuditLog, StrategyDraft

#: The three answers a reviewer may give. ``needs_revision`` is not a rejection:
#: it says "come back with a changed draft", so the run stays reviewable instead
#: of being thrown away.
DECISIONS: tuple[str, ...] = ("confirmed", "rejected", "needs_revision")

#: One audit event type per decision, so a query can filter by verdict instead of
#: parsing payloads.
EVENT_TYPES: dict[str, str] = {
    "confirmed": "strategy_draft_confirmed",
    "rejected": "strategy_draft_rejected",
    "needs_revision": "strategy_draft_needs_revision",
}

ACTIONS: dict[str, str] = {
    "confirmed": "confirm",
    "rejected": "reject",
    "needs_revision": "request_changes",
}

LABELS: dict[str, str] = {
    "confirmed": "已确认",
    "rejected": "已驳回",
    "needs_revision": "需修改",
}

ENTITY_TYPE = "strategy_draft"

#: Who is answering. The deployment authenticates with a single bearer token, so
#: the audit can identify the role but not the person; per-person identity would
#: need an identity provider, which is out of scope here.
DEFAULT_ACTOR = "operator"


@dataclass(frozen=True)
class Confirmation:
    """One recorded human decision about one draft."""

    draft_id: int
    decision: str
    note: str | None
    decided_by: str
    decided_at: dt.datetime | None
    audit_id: int


def validate_decision(decision: str) -> str:
    """Return ``decision`` or refuse it; the caller maps the error to a 422."""

    if decision not in DECISIONS:
        raise ValueError(
            f"unknown confirmation decision '{decision}'; expected one of " + ", ".join(DECISIONS)
        )
    return decision


def record_confirmation(
    db: Session,
    *,
    draft: StrategyDraft,
    decision: str,
    note: str | None = None,
    actor: str = DEFAULT_ACTOR,
) -> Confirmation:
    """Append the human decision for ``draft``. Adds a row, never commits.

    The payload states in the record itself that no strategy version followed:
    an audit reader a year from now should not have to infer that from the fact
    that the tables happen to be empty.
    """

    decision = validate_decision(decision)
    event = record_audit(
        db,
        event_type=EVENT_TYPES[decision],
        entity_type=ENTITY_TYPE,
        entity_id=str(draft.id),
        action=ACTIONS[decision],
        payload={
            "draft_id": draft.id,
            "run_id": draft.run_id,
            "hypothesis_id": draft.hypothesis_id,
            "decision": decision,
            "label": LABELS[decision],
            "note": note,
            "capability_status": draft.capability_status,
            "draft_status": draft.status,
            "compiled_strategy_version_id": draft.compiled_strategy_version_id,
            "is_human_decision": True,
            "strategy_version_created": False,
        },
        actor=actor or DEFAULT_ACTOR,
    )
    db.flush()
    return Confirmation(
        draft_id=draft.id,
        decision=decision,
        note=note,
        decided_by=event.actor,
        decided_at=event.created_at,
        audit_id=event.id,
    )


def latest_confirmation(db: Session, draft_id: int) -> Confirmation | None:
    """The newest decision for ``draft_id``, or ``None`` if nobody answered."""

    row = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.entity_type == ENTITY_TYPE,
            AuditLog.entity_id == str(draft_id),
            AuditLog.event_type.in_(tuple(EVENT_TYPES.values())),
        )
        .order_by(AuditLog.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    payload = row.payload_json or {}
    return Confirmation(
        draft_id=draft_id,
        decision=str(payload.get("decision") or ""),
        note=payload.get("note"),
        decided_by=row.actor,
        decided_at=row.created_at,
        audit_id=row.id,
    )


def confirmation_view(db: Session, draft_id: int) -> dict[str, Any] | None:
    """The confirmation as it is exposed next to a draft, or ``None``."""

    confirmation = latest_confirmation(db, draft_id)
    if confirmation is None:
        return None
    return {
        "decision": confirmation.decision,
        "label": LABELS.get(confirmation.decision, confirmation.decision),
        "note": confirmation.note,
        "decided_by": confirmation.decided_by,
        "decided_at": (confirmation.decided_at.isoformat() if confirmation.decided_at else None),
        "audit_id": confirmation.audit_id,
        "is_human_decision": True,
        "strategy_version_created": False,
    }


__all__ = [
    "ACTIONS",
    "DECISIONS",
    "DEFAULT_ACTOR",
    "ENTITY_TYPE",
    "EVENT_TYPES",
    "LABELS",
    "Confirmation",
    "confirmation_view",
    "latest_confirmation",
    "record_confirmation",
    "validate_decision",
]
