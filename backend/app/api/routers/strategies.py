"""Strategy endpoints: library, immutable versions and DSL validation."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    StrategyCreate,
    StrategyOut,
    StrategyValidationOut,
    StrategyVersionCreate,
    StrategyVersionOut,
)
from app.core.db import get_db
from app.data.strategy_service import (
    create_strategy_version,
    parse_spec,
    slugify,
)
from app.domain.models import BacktestRun, Strategy, StrategyVersion
from app.strategies.validator import validate_strategy

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("", response_model=list[StrategyOut], summary="List strategies")
def list_strategies(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: str | None = None,
) -> list[StrategyOut]:
    stmt = select(Strategy)
    if status:
        stmt = stmt.where(Strategy.status == status)
    rows = db.scalars(stmt.order_by(Strategy.id.desc()).limit(limit).offset(offset)).all()
    result: list[StrategyOut] = []
    for row in rows:
        payload = StrategyOut.model_validate(row)
        payload.version_count = (
            db.scalar(
                select(func.count())
                .select_from(StrategyVersion)
                .where(StrategyVersion.strategy_id == row.id)
            )
            or 0
        )
        result.append(payload)
    return result


@router.post("", response_model=StrategyOut, status_code=201, summary="Create strategy")
def create_strategy(payload: StrategyCreate, db: Session = Depends(get_db)) -> StrategyOut:
    slug = payload.slug or slugify(payload.name)
    exists = db.scalar(select(Strategy).where(Strategy.slug == slug))
    if exists is not None:
        raise HTTPException(status_code=409, detail="strategy slug already exists")
    strategy = Strategy(**{**payload.model_dump(exclude={"slug"}), "slug": slug})
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return StrategyOut.model_validate(strategy)


@router.get("/{strategy_id}", response_model=StrategyOut, summary="Get strategy")
def get_strategy(strategy_id: int, db: Session = Depends(get_db)) -> StrategyOut:
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    payload = StrategyOut.model_validate(strategy)
    payload.version_count = (
        db.scalar(
            select(func.count())
            .select_from(StrategyVersion)
            .where(StrategyVersion.strategy_id == strategy_id)
        )
        or 0
    )
    return payload


@router.delete("/{strategy_id}", summary="Delete a strategy and all its versions")
def delete_strategy(strategy_id: int, db: Session = Depends(get_db)) -> dict:
    from app.data.strategy_service import record_audit

    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")

    # Refuse to delete if any version has backtest results (audit trail)
    has_backtests = db.scalar(
        select(BacktestRun.id)
        .join(StrategyVersion, StrategyVersion.id == BacktestRun.strategy_version_id)
        .where(StrategyVersion.strategy_id == strategy_id)
        .limit(1)
    )
    if has_backtests is not None:
        raise HTTPException(
            status_code=409,
            detail="此策略有回测记录关联，不能直接删除。请先删除相关回测记录。",
        )

    name = strategy.name
    db.delete(strategy)
    record_audit(
        db,
        event_type="strategy_deleted",
        entity_type="strategy",
        entity_id=str(strategy_id),
        action="delete",
        payload={"name": name},
    )
    db.commit()
    return {"deleted": strategy_id, "name": name}


@router.get(
    "/{strategy_id}/versions",
    response_model=list[StrategyVersionOut],
    summary="List strategy versions",
)
def list_versions(strategy_id: int, db: Session = Depends(get_db)) -> list[StrategyVersionOut]:
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    rows = db.scalars(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy_id)
        .order_by(StrategyVersion.id)
    ).all()
    return [
        StrategyVersionOut(
            id=row.id,
            strategy_id=row.strategy_id,
            version=row.version,
            schema_version=row.schema_version,
            immutable_hash=row.immutable_hash,
            validation_status=row.validation_status,
            is_current=row.is_current,
            created_at=row.created_at,
            dsl=row.dsl_json,
        )
        for row in rows
    ]


@router.post(
    "/{strategy_id}/versions",
    response_model=StrategyVersionOut,
    status_code=201,
    summary="Create a new immutable strategy version",
)
def create_version(
    strategy_id: int, payload: StrategyVersionCreate, db: Session = Depends(get_db)
) -> StrategyVersionOut:
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    try:
        row = create_strategy_version(
            db,
            strategy,
            version=payload.version,
            dsl=payload.dsl,
            source_commit=payload.source_commit,
            source_url=payload.source_url,
            prompt_version=payload.prompt_version,
            evidence=payload.evidence,
            parameters=payload.parameters,
            make_current=payload.make_current,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return StrategyVersionOut(
        id=row.id,
        strategy_id=row.strategy_id,
        version=row.version,
        schema_version=row.schema_version,
        immutable_hash=row.immutable_hash,
        validation_status=row.validation_status,
        is_current=row.is_current,
        created_at=row.created_at,
        dsl=row.dsl_json,
    )


@router.post("/validate", response_model=StrategyValidationOut, summary="Validate a DSL document")
def validate_dsl(dsl: dict) -> StrategyValidationOut:
    try:
        spec = parse_spec(dsl)
    except ValueError as exc:
        return StrategyValidationOut(
            is_valid=False,
            issues=[
                {
                    "severity": "error",
                    "code": "schema_error",
                    "message": str(exc),
                    "path": None,
                }
            ],
            available_columns=[],
        )
    report = validate_strategy(spec)
    return StrategyValidationOut(
        is_valid=report.is_valid,
        issues=[i.as_dict() for i in report.issues],
        available_columns=report.available_columns,
    )


@router.get(
    "/versions/{version_id}/verify",
    summary="Verify a strategy version has not been modified",
)
def verify_version(version_id: int, db: Session = Depends(get_db)) -> dict:
    from app.data.strategy_service import immutable_hash

    row = db.get(StrategyVersion, version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="strategy version not found")
    recomputed = immutable_hash(row.dsl_json, row.version)
    return {
        "strategy_version_id": row.id,
        "version": row.version,
        "stored_hash": row.immutable_hash,
        "recomputed_hash": recomputed,
        "intact": recomputed == row.immutable_hash,
    }


@router.get("/{strategy_id}/lineage", summary="Strategy provenance / lineage")
def strategy_lineage(strategy_id: int, db: Session = Depends(get_db)) -> dict:
    """Where a strategy came from, and every version's provenance."""

    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    versions = db.scalars(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy_id)
        .order_by(StrategyVersion.id)
    ).all()
    return {
        "strategy_id": strategy.id,
        "name": strategy.name,
        "source_type": strategy.source_type,
        "source_url": strategy.source_url,
        "license": strategy.license,
        "author": strategy.author,
        "lifecycle": strategy.lifecycle,
        "versions": [
            {
                "id": v.id,
                "version": v.version,
                "source_url": v.source_url,
                "source_commit": v.source_commit,
                "prompt_version": v.prompt_version,
                "evidence": v.evidence_json,
                "immutable_hash": v.immutable_hash,
                "is_current": v.is_current,
                "created_at": v.created_at,
            }
            for v in versions
        ],
    }
