"""GitHub strategy import endpoints.

Two-step flow, human in the loop:

1. ``POST /importer/github/analyze`` — read-only. Fetches the repository as
   plain text, runs static AST analysis, returns findings + a draft DSL.
   Nothing is written to the database.
2. ``POST /importer/github/import`` — takes the (possibly human-edited) DSL,
   validates it through the same pipeline as hand-written strategies, and only
   then creates a Strategy + immutable StrategyVersion. The request has to name
   the commit the analysis read: the version and the watched source are recorded
   against that SHA, not against the branch that was asked for (ADR-060).

Repository code is never executed, never cloned, never run in any worker.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import GithubAnalyzeOut, GithubAnalyzeRequest, GithubImportRequest
from app.core.db import get_db
from app.data.github_source_service import record_snapshot
from app.data.strategy_service import create_strategy_version, parse_spec, record_audit, slugify
from app.domain.models import GitHubSnapshot, GitHubSource, Strategy
from app.importer import (
    GitHubClient,
    GitHubError,
    analyze_repository_files,
    build_coverage,
    build_draft_dsl,
    coverage_warnings,
    parse_repo_url,
)
from app.strategies.validator import validate_strategy

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/importer/github", tags=["importer"])


def _persist_github_source(
    db: Session, owner: str, repo: str, commit: str, content_hash: str
) -> GitHubSource:
    """Upsert the watched GitHub source + a snapshot for the imported commit (ADR-060).

    ``commit`` is a SHA, not a ref: the source row and the snapshot both have to
    name the revision a human actually reviewed, otherwise the watcher compares a
    branch name with a SHA and the "source commit" column shows a moving target.
    """

    url = f"https://github.com/{owner}/{repo}"
    source = db.scalar(select(GitHubSource).where(GitHubSource.repository_url == url))
    if source is None:
        source = GitHubSource(repository_url=url, default_branch="main", current_commit=commit)
        db.add(source)
        db.flush()
    else:
        source.current_commit = commit
        source.last_checked_at = dt.datetime.now(tz=dt.UTC)
    # One row per (source, commit): re-importing or re-checking a commit has to
    # refresh its explanation, not insert a duplicate (ADR-058).
    record_snapshot(
        db,
        source.id,
        commit,
        content_hash,
        {"imported": True, "reason": "manual_import"},
    )
    return source


def _finding_to_dict(finding: Any) -> dict[str, Any]:
    from dataclasses import asdict

    payload = asdict(finding)
    evidence = payload.pop("evidence", None)
    if isinstance(evidence, dict):
        payload["evidence_path"] = evidence.get("path")
        payload["evidence_lines"] = [evidence.get("start_line"), evidence.get("end_line")]
        payload["evidence_snippet"] = evidence.get("snippet")
    return payload


@router.post(
    "/analyze", response_model=GithubAnalyzeOut, summary="Analyze a repository (read-only)"
)
def analyze_repository(payload: GithubAnalyzeRequest) -> GithubAnalyzeOut:
    try:
        parse_repo_url(payload.repo_url)
    except GitHubError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        client = GitHubClient(token=payload.token)
        meta, files, fetch_coverage = client.fetch_repository(
            payload.repo_url,
            payload.ref,
            max_files=payload.max_files,
            max_seconds=payload.max_seconds,
        )
    except GitHubError as exc:
        raise HTTPException(status_code=502, detail=f"github fetch failed: {exc}") from exc

    findings = analyze_repository_files(files)
    draft_dsl, warnings = build_draft_dsl(meta, findings)
    coverage = build_coverage(fetch_coverage, findings)
    return GithubAnalyzeOut(
        owner=meta.owner,
        repo=meta.repo,
        ref=meta.ref,
        commit=meta.commit,
        description=meta.description,
        license=meta.license,
        analysis_version=coverage["analysis_version"],
        coverage=coverage,
        files_parsed=findings.files_parsed,
        files_inventoried=findings.files_inventoried,
        files_skipped=[
            {"path": skipped.path, "reason": skipped.reason} for skipped in findings.files_skipped
        ],
        files_unparsed=[
            {"path": unparsed.path, "reason": unparsed.reason}
            for unparsed in findings.files_unparsed
        ],
        indicators=[_finding_to_dict(f) for f in findings.indicators],
        rules=[_finding_to_dict(f) for f in findings.rules],
        params=[_finding_to_dict(f) for f in findings.params],
        unknowns=[_finding_to_dict(f) for f in findings.unknowns],
        unsafe_flags=[_finding_to_dict(f) for f in findings.unsafe_flags],
        draft_dsl=draft_dsl,
        warnings=warnings + coverage_warnings(coverage),
    )


@router.post("/import", status_code=201, summary="Import a reviewed DSL as a strategy")
def import_strategy(payload: GithubImportRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        owner, repo = parse_repo_url(payload.repo_url)
    except GitHubError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        spec = parse_spec(payload.dsl)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"DSL schema error: {exc}") from exc

    report = validate_strategy(spec)
    if not report.is_valid:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "DSL failed static validation; fix the issues and retry",
                "issues": [i.as_dict() for i in report.errors],
            },
        )

    slug = slugify(payload.name)
    strategy = db.scalar(select(Strategy).where(Strategy.slug == slug))
    if strategy is None:
        strategy = Strategy(
            name=payload.name,
            slug=slug,
            description=f"Imported from https://github.com/{owner}/{repo}",
            source_type="github",
            source_url=f"https://github.com/{owner}/{repo}",
        )
        db.add(strategy)
        db.flush()

    try:
        version_row = create_strategy_version(
            db,
            strategy,
            version=payload.version,
            dsl=payload.dsl,
            source_commit=payload.commit,
            source_url=f"https://github.com/{owner}/{repo}",
            evidence={
                "importer": "github",
                "repository": f"{owner}/{repo}",
                "ref": payload.ref,
                "commit": payload.commit,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    record_audit(
        db,
        event_type="strategy_imported",
        entity_type="strategy",
        entity_id=str(strategy.id),
        action="import",
        payload={
            "repository": f"{owner}/{repo}",
            "ref": payload.ref,
            "commit": payload.commit,
            "version": version_row.version,
            "immutable_hash": version_row.immutable_hash,
        },
    )
    _persist_github_source(db, owner, repo, payload.commit, version_row.immutable_hash)
    db.commit()

    return {
        "strategy_id": strategy.id,
        "strategy_version_id": version_row.id,
        "version": version_row.version,
        "source_commit": version_row.source_commit,
        "validation_status": version_row.validation_status,
        "immutable_hash": version_row.immutable_hash,
        "warnings": [i.as_dict() for i in report.warnings],
    }


@router.get("/sources", summary="List watched GitHub sources")
def list_sources(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.scalars(select(GitHubSource).order_by(GitHubSource.id)).all()
    return [
        {
            "id": row.id,
            "repository_url": row.repository_url,
            "default_branch": row.default_branch,
            "current_commit": row.current_commit,
            "license": row.license,
            "author": row.author,
            "is_watched": row.is_watched,
            "last_checked_at": row.last_checked_at,
            "last_import_status": row.last_import_status,
        }
        for row in rows
    ]


@router.get("/sources/{source_id}", summary="Get a watched GitHub source")
def get_source(source_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    from fastapi import HTTPException

    row = db.get(GitHubSource, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="github source not found")
    return {
        "id": row.id,
        "repository_url": row.repository_url,
        "default_branch": row.default_branch,
        "current_commit": row.current_commit,
        "license": row.license,
        "author": row.author,
        "is_watched": row.is_watched,
        "last_checked_at": row.last_checked_at,
        "last_import_status": row.last_import_status,
    }


@router.get("/sources/{source_id}/check", summary="Check a source for a new commit (live)")
def check_source_now(source_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    from fastapi import HTTPException

    from app.data.strategy_service import record_audit
    from app.importer import GitHubClient, parse_repo_url

    row = db.get(GitHubSource, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="github source not found")
    owner, repo = parse_repo_url(row.repository_url)
    try:
        head = GitHubClient().get_head_commit(owner, repo)
    except Exception as exc:  # noqa: BLE001 - surface a readable error
        raise HTTPException(status_code=502, detail=f"github check failed: {exc}") from exc
    new = bool(head and head != row.current_commit)
    row.last_checked_at = dt.datetime.now(tz=dt.UTC)
    record_audit(
        db,
        event_type="github_source_checked",
        entity_type="github_source",
        entity_id=str(source_id),
        action="check",
        payload={"head": head, "has_update": new},
    )
    db.commit()
    return {
        "source_id": source_id,
        "current_commit": row.current_commit,
        "head": head,
        "has_update": new,
    }


@router.get("/sources/{source_id}/snapshots", summary="Snapshots for a GitHub source")
def list_snapshots(
    source_id: int, db: Session = Depends(get_db), limit: int = 50
) -> list[dict[str, Any]]:
    from fastapi import HTTPException

    if db.get(GitHubSource, source_id) is None:
        raise HTTPException(status_code=404, detail="github source not found")
    rows = db.scalars(
        select(GitHubSnapshot)
        .where(GitHubSnapshot.source_id == source_id)
        .order_by(GitHubSnapshot.fetched_at.desc())
        .limit(min(limit, 500))
    ).all()
    return [
        {
            "id": row.id,
            "source_id": row.source_id,
            "commit": row.commit,
            "content_hash": row.content_hash,
            "fetched_at": row.fetched_at,
            # Why a check ended the way it did lives here: ``imported``,
            # ``reason``, ``transient``, ``coverage`` and ``warnings``. Reporting
            # only the commit made the stored explanation unreadable, so a user
            # saw "incomplete" with no way to find out what was missed (ADR-058).
            "extraction": row.extraction_json,
        }
        for row in rows
    ]
