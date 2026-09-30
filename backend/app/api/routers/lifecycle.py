"""Strategy lifecycle endpoints (Phase 8).

Expose the deterministic, evidence-gated promotion/degradation rules. Nothing
here asks a model anything: the stage is derived from recorded facts only.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import LifecycleApplyIn, LifecycleApplyOut, StrategyLifecycleOut
from app.core.db import get_db
from app.domain.models import Strategy
from app.strategies.lifecycle import LifecycleError, apply_lifecycle, evaluate_lifecycle

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/lifecycle", tags=["lifecycle"])


@router.get(
    "/strategies",
    response_model=list[StrategyLifecycleOut],
    summary="Lifecycle overview for every strategy",
)
def list_lifecycles(
    db: Session = Depends(get_db),
    limit: int = Query(default=200, ge=1, le=500),
) -> list[StrategyLifecycleOut]:
    rows = db.scalars(select(Strategy).order_by(Strategy.id).limit(limit)).all()
    return [StrategyLifecycleOut(**evaluate_lifecycle(db, row)) for row in rows]


@router.get(
    "/strategies/{strategy_id}",
    response_model=StrategyLifecycleOut,
    summary="Lifecycle and evidence for one strategy",
)
def get_lifecycle(strategy_id: int, db: Session = Depends(get_db)) -> StrategyLifecycleOut:
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    return StrategyLifecycleOut(**evaluate_lifecycle(db, strategy))


@router.post(
    "/strategies/{strategy_id}/apply",
    response_model=LifecycleApplyOut,
    summary="Apply a lifecycle transition if the evidence supports it",
)
def apply_transition(
    strategy_id: int, payload: LifecycleApplyIn, db: Session = Depends(get_db)
) -> LifecycleApplyOut:
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    previous = strategy.lifecycle
    try:
        apply_lifecycle(db, strategy, payload.target_stage, actor="user", note=payload.note)
    except LifecycleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return LifecycleApplyOut(
        strategy_id=strategy.id,
        previous=previous,
        current=strategy.lifecycle,
        applied=strategy.lifecycle != previous,
        detail="lifecycle updated" if strategy.lifecycle != previous else "already at that stage",
    )
