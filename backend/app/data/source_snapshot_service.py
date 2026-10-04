"""Record each ingestion as an append-only observation, and read one back (ADR-163).

A snapshot is what the platform saw, not a second copy of the document: hashes,
HTTP metadata, parser identity and the retained excerpt. Fetching the same URL twice
writes two rows, so ``research_artifacts.snapshot_id`` can answer "which material did
this run read?" rather than "which URL did it name?".

This module is the only place that turns an :class:`~app.sources.ingest.IngestedMaterial`
into storage, and the only place that builds the API shape for a stored row, so the
network layer stays free of the database and both stay free of FastAPI.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import AISourceSnapshot
from app.sources.ingest import (
    IngestedMaterial,
    ingest_pdf_uri,
    ingest_url,
    material_from_snapshot,
)


def _iso(moment: datetime | None) -> str | None:
    """Serialize a timestamp the same way whether the row is fresh or re-read.

    SQLite hands naive datetimes back and PostgreSQL hands aware ones, so a snapshot
    read straight after writing it would otherwise describe a different instant than
    the same snapshot read a minute later.
    """

    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat()


def record_material(
    db: Session,
    material: IngestedMaterial,
    *,
    source_ref: str | None = None,
    label: str | None = None,
) -> AISourceSnapshot:
    """Store one observation. Never updates an earlier one: snapshots are append-only."""

    metadata: dict[str, Any] = dict(material.metadata)
    warnings = [dict(warning) for warning in material.warnings]
    if warnings:
        metadata["warnings"] = warnings
    if source_ref:
        metadata["source_ref"] = source_ref
    if label:
        metadata["label"] = label

    row = AISourceSnapshot(
        source_type=material.kind,
        url=material.uri or "",
        final_url=material.final_uri,
        status=material.status,
        parse_status=material.parse_status,
        http_status=material.http_status,
        content_type=material.content_type,
        size_bytes=material.size_bytes,
        chars_read=material.chars_read,
        source_hash=material.source_hash,
        text_hash=material.text_hash,
        parser=material.parser,
        parser_version=material.parser_version,
        robots_ok=material.robots_ok,
        retention=material.retention,
        retained_chars=material.retained_chars,
        truncated=material.truncated,
        excerpt=material.text or None,
        license_note=material.license_note,
        error=material.message or None,
        metadata_json=metadata or None,
        fetched_at=material.fetched_at,
    )
    db.add(row)
    db.flush()
    return row


def get_snapshot(db: Session, snapshot_id: int) -> AISourceSnapshot | None:
    return db.get(AISourceSnapshot, snapshot_id)


def snapshot_payload(row: AISourceSnapshot) -> dict[str, Any]:
    """The API shape of a snapshot: hashes and a bounded excerpt, never the full text."""

    metadata = dict(row.metadata_json or {})
    return {
        "snapshot_id": row.id,
        "source_kind": row.source_type,
        "snapshot_status": row.status,
        "parse_status": row.parse_status,
        "source_ref": metadata.get("source_ref"),
        "label": metadata.get("label"),
        "original_uri": row.url or None,
        "final_uri": row.final_url,
        "status_code": row.http_status,
        "content_type": row.content_type,
        "bytes_read": row.size_bytes or 0,
        "chars_read": row.chars_read or 0,
        "source_hash": row.source_hash,
        "text_hash": row.text_hash,
        "parser": row.parser,
        "parser_version": row.parser_version,
        "robots_ok": row.robots_ok,
        "retention": {
            "policy": row.retention,
            "retained_chars": row.retained_chars,
            "truncated": bool(row.truncated),
        },
        "excerpt": [
            piece for piece in (line.strip() for line in (row.excerpt or "").splitlines()) if piece
        ],
        "warnings": list(metadata.get("warnings") or []),
        "redirects": list(metadata.get("redirects") or []),
        "error_code": metadata.get("error_code"),
        "error_message": row.error,
        "fetched_at": _iso(row.fetched_at),
        "created_at": _iso(row.created_at),
    }


def recent_snapshots(db: Session, *, limit: int = 20) -> list[AISourceSnapshot]:
    statement = select(AISourceSnapshot).order_by(AISourceSnapshot.id.desc()).limit(limit)
    return list(db.execute(statement).scalars())


def snapshot_ingester(db: Session, *, retrieve: Any = None) -> Callable[[Any], IngestedMaterial]:
    """The callable the research layer calls for ``url``/``pdf`` sources (Step 3.6).

    Either fetches the URI and writes a new observation, or loads the observation a
    ``snapshot_id`` already named — the AI layer never learns how to speak HTTP, and
    this layer never learns how to talk to a model.
    """

    passes: dict[str, Any] = {} if retrieve is None else {"retrieve": retrieve}

    def ingest(item: Any) -> IngestedMaterial:
        source_ref = getattr(item, "source_ref", None)
        label = getattr(item, "label", None)
        snapshot_id = getattr(item, "snapshot_id", None)
        if snapshot_id is not None:
            row = get_snapshot(db, snapshot_id)
            if row is None:
                raise ValueError(f"unknown snapshot_id {snapshot_id}")
            stored = material_from_snapshot(row)
            return replace(
                stored,
                source_ref=source_ref or stored.source_ref,
                label=label or stored.label,
            )

        kind = getattr(item, "kind", "url")
        uri = (getattr(item, "uri", None) or "").strip()
        if not uri:
            raise ValueError(f"a '{kind}' source needs a uri or a snapshot_id")
        options: dict[str, Any] = {
            "source_ref": source_ref,
            "label": label,
            "retention": getattr(item, "retention", None),
            "license_note": getattr(item, "license_note", None),
            **passes,
        }
        material = ingest_pdf_uri(uri, **options) if kind == "pdf" else ingest_url(uri, **options)
        row = record_material(db, material, source_ref=source_ref, label=label)
        return replace(material, snapshot_id=row.id)

    return ingest


__all__ = [
    "get_snapshot",
    "recent_snapshots",
    "record_material",
    "snapshot_ingester",
    "snapshot_payload",
]
