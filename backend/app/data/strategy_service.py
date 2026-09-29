"""Strategy service: version creation, hashing and immutability enforcement."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import AuditLog, Strategy, StrategyParameter, StrategyVersion
from app.strategies.dsl import StrategySpec
from app.strategies.validator import validate_strategy

logger = logging.getLogger(__name__)

__all__ = [
    "create_strategy_version",
    "immutable_hash",
    "load_spec",
    "parse_spec",
    "record_audit",
    "slugify",
]


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "strategy"


def parse_spec(dsl: dict[str, Any]) -> StrategySpec:
    """Parse a DSL document, raising ``ValueError`` with a readable message."""

    try:
        return StrategySpec.model_validate(dsl)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ValueError(f"invalid strategy DSL -> {details}") from exc


def immutable_hash(dsl: dict[str, Any], version: str) -> str:
    """Content hash used to prove a strategy version was never modified."""

    canonical = json.dumps(
        {"version": version, "dsl": dsl}, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_spec(strategy_version: StrategyVersion) -> StrategySpec:
    return StrategySpec.model_validate(strategy_version.dsl_json)


def create_strategy_version(
    db: Session,
    strategy: Strategy,
    *,
    version: str,
    dsl: dict[str, Any],
    source_commit: str | None = None,
    source_url: str | None = None,
    prompt_version: str | None = None,
    evidence: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
    make_current: bool = True,
) -> StrategyVersion:
    """Insert a new immutable strategy version.

    Existing versions are never touched: this is the mechanism that keeps old
    backtest results reproducible.
    """

    duplicate = db.scalar(
        select(StrategyVersion).where(
            StrategyVersion.strategy_id == strategy.id, StrategyVersion.version == version
        )
    )
    if duplicate is not None:
        raise ValueError(f"version '{version}' already exists for this strategy")

    try:
        spec = parse_spec(dsl)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    report = validate_strategy(spec)
    status = "valid" if report.is_valid else "invalid"
    errors = [i.as_dict() for i in report.errors]

    strategy_version = StrategyVersion(
        strategy_id=strategy.id,
        version=version,
        schema_version=spec.schema_version,
        dsl_json=dsl,
        source_commit=source_commit,
        source_url=source_url or strategy.source_url,
        prompt_version=prompt_version,
        evidence_json=evidence,
        immutable_hash=immutable_hash(dsl, version),
        is_current=False,
        validation_status=status,
        validation_errors=errors or None,
    )
    db.add(strategy_version)
    db.flush()

    db.add(
        StrategyParameter(
            strategy_version_id=strategy_version.id,
            parameters_json=parameters or spec.parameters or {},
            description="created with strategy version",
            is_default=True,
        )
    )

    if make_current:
        db.query(StrategyVersion).filter(
            StrategyVersion.strategy_id == strategy.id,
            StrategyVersion.id != strategy_version.id,
        ).update({"is_current": False})
        strategy_version.is_current = True

    strategy.lifecycle = "normalized" if report.is_valid else "imported"
    record_audit(
        db,
        event_type="strategy_version_created",
        entity_type="strategy_version",
        entity_id=str(strategy_version.id),
        action="create",
        payload={
            "strategy_id": strategy.id,
            "version": version,
            "immutable_hash": strategy_version.immutable_hash,
            "validation_status": status,
        },
    )
    db.commit()
    db.refresh(strategy_version)
    return strategy_version


def record_audit(
    db: Session,
    *,
    event_type: str,
    entity_type: str,
    entity_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
    actor: str = "system",
) -> AuditLog:
    event = AuditLog(
        event_type=event_type,
        actor=actor,
        entity_type=entity_type,
        entity_id=str(entity_id),
        action=action,
        payload_json=payload,
        created_at=dt.datetime.now(tz=dt.UTC),
    )
    db.add(event)
    return event
