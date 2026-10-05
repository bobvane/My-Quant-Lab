"""Strategy service: version creation, hashing and immutability enforcement."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
from collections.abc import Iterable
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
    "next_version",
    "parse_spec",
    "record_audit",
    "slugify",
    "strategy_dsl_problem",
    "strategy_version_plan",
]

# Versions this service is willing to increment on its own. A version it cannot
# read as three numbers is still a legal version (reviewers use `v2-beta`), it
# just has to be named by the caller instead of guessed (ADR-061).
_VERSION_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")


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


def next_version(existing: Iterable[str]) -> str:
    """Return the next free patch version after the highest one in ``existing``.

    ``1.0.0`` for a strategy that has no versions yet, ``1.0.1`` after ``1.0.0``
    and so on. Anything that is not ``major.minor.patch`` raises ``ValueError``:
    the ledger cannot be incremented, so the caller has to name the version
    rather than have this function invent one that might collide (ADR-061).
    """

    versions = list(existing)
    if not versions:
        return "1.0.0"

    parsed: list[tuple[int, int, int]] = []
    for version in versions:
        match = _VERSION_RE.match(version)
        if match is None:
            raise ValueError(
                f"cannot assign a version: existing version {version!r} is not "
                f"major.minor.patch, so name the version explicitly"
            )
        parsed.append((int(match["major"]), int(match["minor"]), int(match["patch"])))

    major, minor, patch = max(parsed)
    return f"{major}.{minor}.{patch + 1}"


def strategy_dsl_problem(dsl: dict[str, Any]) -> str | None:
    """Return why this DSL could not be imported unattended, or ``None`` if it can.

    The watcher imports with no human in the loop, so it has to ask the same judge
    the human import path asks: ``parse_spec`` then ``validate_strategy``. Asking
    first is what keeps an unimportable draft from being handed to
    ``create_strategy_version`` inside a Celery task, where the ``ValueError`` used
    to escape ``check_source`` and kill the whole scheduled run (ADR-062).
    """

    try:
        spec = parse_spec(dsl)
    except ValueError as exc:
        return str(exc)

    report = validate_strategy(spec)
    if report.is_valid:
        return None
    return "invalid strategy DSL -> " + "; ".join(
        f"{issue.path or ''}: {issue.message}".lstrip(": ") for issue in report.errors
    )


def strategy_version_plan(db: Session, name: str) -> dict[str, Any]:
    """Describe the version ledger a name resolves to: what exists, what comes next.

    The service that owns immutability owns numbering: callers ask what would be
    assigned instead of guessing `1.0.0` and walking into a duplicate (ADR-061).
    """

    slug = slugify(name)
    strategy = db.scalar(select(Strategy).where(Strategy.slug == slug))
    versions: list[str] = []
    if strategy is not None:
        versions = list(
            db.scalars(
                select(StrategyVersion.version)
                .where(StrategyVersion.strategy_id == strategy.id)
                .order_by(StrategyVersion.id)
            ).all()
        )

    plan: dict[str, Any] = {
        "name": name,
        "slug": slug,
        "strategy_id": strategy.id if strategy is not None else None,
        "versions": versions,
        "next_version": None,
        "can_assign": True,
        "reason": "",
    }
    try:
        plan["next_version"] = next_version(versions)
    except ValueError as exc:
        plan["can_assign"] = False
        plan["reason"] = str(exc)
    return plan


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
    commit: bool = True,
) -> StrategyVersion:
    """Insert a new immutable strategy version.

    Existing versions are never touched: this is the mechanism that keeps old
    backtest results reproducible.

    ``commit=False`` leaves the new row flushed but uncommitted, so a caller that
    has to write something *else* in the same transaction (the compiler binds the
    draft to the version it just created) can commit -- or roll back -- both
    together instead of leaving half a change behind.
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
    if commit:
        db.commit()
        db.refresh(strategy_version)
    else:
        db.flush()
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
