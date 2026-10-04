"""Source ingestion endpoints (v2.1.0 / Phase 4): fetch, read, observe.

These endpoints never call a model and never spend AI budget, but they are the only
place in the API that makes the server talk to an address the caller chose, so the
security decisions live one layer down in ``app.sources``:

* ``app.sources.guard`` rejects a URI before any socket exists,
* ``app.sources.fetch`` connects to the address it validated (never re-resolving),
* ``app.sources.parse`` reads bytes as data — never as instructions,
* ``app.sources.ingest`` decides how much of it may be kept,
* ``app.data.source_snapshot_service`` writes the observation down.

A refusal is a result, not a crash: a blocked source is stored and reported (422 with
``snapshot_status="blocked"``), and a source that could not be reached or understood is
stored and reported as a fetch failure (502). Either way the row stays, because the
point of a snapshot is to be able to say what the platform actually saw.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import SourceIngestIn, SourcePdfIn, SourceSnapshotOut
from app.core.db import get_db
from app.data import source_snapshot_service as snapshots
from app.sources.ingest import (
    BLOCKED,
    RETAINED,
    IngestedMaterial,
    ingest_pdf_base64,
    ingest_pdf_uri,
    ingest_url,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai"])


def _refusal_detail(material: IngestedMaterial) -> dict[str, Any]:
    return {
        "snapshot_id": material.snapshot_id,
        "snapshot_status": material.status,
        "source_kind": material.kind,
        "original_uri": material.uri,
        "code": material.code,
        "message": material.message,
    }


def _refuse(material: IngestedMaterial) -> HTTPException:
    """Turn a stored refusal into the response its cause deserves.

    A policy block (a private address, a robots rule, a scheme this build does not
    speak) is 422: the request was understood and answered "no". A source that could
    not be reached or read is 502: the answer is about the network or the document,
    not about the request.
    """

    detail = _refusal_detail(material)
    if material.status == BLOCKED:
        return HTTPException(status_code=422, detail=detail)
    return HTTPException(status_code=502, detail=detail)


def _store(
    db: Session,
    material: IngestedMaterial,
    *,
    source_ref: str | None,
    label: str | None,
) -> tuple[IngestedMaterial, SourceSnapshotOut]:
    row = snapshots.record_material(db, material, source_ref=source_ref, label=label)
    db.commit()
    stored = replace(material, snapshot_id=row.id)
    return stored, SourceSnapshotOut(**snapshots.snapshot_payload(row))


@router.post(
    "/ai/sources/url",
    response_model=SourceSnapshotOut,
    summary="Fetch and read one web page into a source snapshot",
)
def ingest_source_url(payload: SourceIngestIn, db: Session = Depends(get_db)) -> SourceSnapshotOut:
    """Fetch one URL over http(s) on port 80/443 and keep an observation of it.

    The response carries hashes, byte and character counts, the parser identity and
    the retained excerpt. It never carries the full document: what the platform read
    is described, not republished.
    """

    try:
        material = ingest_url(
            payload.uri,
            source_ref=payload.source_ref,
            label=payload.label,
            retention=payload.retention,
            license_note=payload.license_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stored, out = _store(db, material, source_ref=payload.source_ref, label=payload.label)
    if stored.status != RETAINED:
        raise _refuse(stored)
    return out


@router.post(
    "/ai/sources/pdf",
    response_model=SourceSnapshotOut,
    summary="Read one PDF into a source snapshot (by URL or inline base64)",
)
def ingest_source_pdf(payload: SourcePdfIn, db: Session = Depends(get_db)) -> SourceSnapshotOut:
    """Read the text layer of a PDF. No OCR, no rendering, no guessing.

    A PDF without a text layer is reported ``unsupported`` — an honest "this build
    cannot read that", never an empty document handed to a model to interpret.
    """

    if bool(payload.uri) == bool(payload.content_base64):
        raise HTTPException(
            status_code=400,
            detail="provide exactly one of 'uri' or 'content_base64'",
        )
    try:
        if payload.uri:
            material = ingest_pdf_uri(
                payload.uri,
                source_ref=payload.source_ref,
                label=payload.label,
                retention=payload.retention,
                license_note=payload.license_note,
            )
        else:
            material = ingest_pdf_base64(
                payload.content_base64 or "",
                filename=payload.filename,
                source_ref=payload.source_ref,
                label=payload.label,
                retention=payload.retention,
                license_note=payload.license_note,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stored, out = _store(db, material, source_ref=payload.source_ref, label=payload.label)
    if stored.status != RETAINED:
        raise _refuse(stored)
    return out


@router.get(
    "/ai/sources/{snapshot_id}",
    response_model=SourceSnapshotOut,
    summary="One stored source snapshot",
)
def get_source_snapshot(snapshot_id: int, db: Session = Depends(get_db)) -> SourceSnapshotOut:
    """Read back an observation, including the ones that were refused."""

    row = snapshots.get_snapshot(db, snapshot_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"source snapshot {snapshot_id} not found")
    return SourceSnapshotOut(**snapshots.snapshot_payload(row))
