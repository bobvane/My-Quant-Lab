"""Persistence for watched GitHub sources (docs/05 §7).

One row per ``(source_id, commit)`` is the schema's contract
(``uq_github_snapshot``), but the same commit is legitimately observed more than
once: a human can import it and the watcher can then check it, and a check that
ran out of its time budget is retried on the next run (ADR-057). Writing a second
row raised ``IntegrityError: UNIQUE constraint failed: github_snapshots.source_id,
github_snapshots.commit``, which aborted the entire scheduled run and took every
other source's outcome down with it (ADR-058). Both call sites therefore go
through :func:`record_snapshot`.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import GitHubSnapshot


def record_snapshot(
    db: Session,
    source_id: int,
    commit: str,
    content_hash: str,
    extraction: dict[str, Any],
    manifest: dict[str, Any] | None = None,
) -> GitHubSnapshot:
    """Record what a check of ``commit`` found, refreshing an existing row.

    The newest observation wins: a snapshot exists to explain the source's
    *current* status, so when the watcher re-reads a commit a manual import
    already recorded (or vice versa), the explanation must be updated rather than
    duplicated or silently ignored.
    """

    snapshot = db.scalar(
        select(GitHubSnapshot).where(
            GitHubSnapshot.source_id == source_id, GitHubSnapshot.commit == commit
        )
    )
    if snapshot is None:
        snapshot = GitHubSnapshot(source_id=source_id, commit=commit)
        db.add(snapshot)
    snapshot.content_hash = content_hash
    snapshot.manifest_json = manifest if manifest is not None else {}
    snapshot.extraction_json = extraction
    snapshot.fetched_at = dt.datetime.now(tz=dt.UTC)
    return snapshot
