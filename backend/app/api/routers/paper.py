"""Paper trading endpoints.

Paper accounts are **completely isolated** from the real portfolio: they live in
their own tables, are funded with virtual cash and can never write to Ghostfolio.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import PaperAccountCreate, PaperAccountOut
from app.core.db import get_db
from app.data.strategy_service import record_audit
from app.domain.models import PaperAccount, PaperPosition, PaperTrade

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/paper", tags=["paper-trading"])


@router.get("/accounts", response_model=list[PaperAccountOut], summary="List paper accounts")
def list_accounts(db: Session = Depends(get_db)) -> list[PaperAccountOut]:
    rows = db.scalars(select(PaperAccount).order_by(PaperAccount.id)).all()
    return [PaperAccountOut.model_validate(row) for row in rows]


@router.post(
    "/accounts", response_model=PaperAccountOut, status_code=201, summary="Create paper account"
)
def create_account(payload: PaperAccountCreate, db: Session = Depends(get_db)) -> PaperAccountOut:
    account = PaperAccount(
        name=payload.name,
        strategy_id=payload.strategy_id,
        base_currency=payload.base_currency,
        initial_cash=payload.initial_cash,
        cash=payload.initial_cash,
        settings_json=payload.settings,
    )
    db.add(account)
    db.flush()
    record_audit(
        db,
        event_type="paper_account_created",
        entity_type="paper_account",
        entity_id=str(account.id),
        action="create",
        payload={"name": account.name, "initial_cash": str(account.initial_cash)},
    )
    db.commit()
    db.refresh(account)
    return PaperAccountOut.model_validate(account)


@router.get("/accounts/{account_id}", response_model=PaperAccountOut, summary="Get paper account")
def get_account(account_id: int, db: Session = Depends(get_db)) -> PaperAccountOut:
    account = db.get(PaperAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    return PaperAccountOut.model_validate(account)


@router.get("/accounts/{account_id}/equity", summary="Equity curve of a paper account")
def account_equity(account_id: int, db: Session = Depends(get_db)) -> dict:
    account = db.get(PaperAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    trades = db.scalars(
        select(PaperTrade)
        .where(PaperTrade.account_id == account_id)
        .order_by(PaperTrade.entry_time)
    ).all()
    realized = sum(float(t.pnl or 0) for t in trades if t.exit_time is not None)
    positions = db.scalars(
        select(PaperPosition).where(PaperPosition.account_id == account_id)
    ).all()
    return {
        "account_id": account_id,
        "cash": float(account.cash),
        "initial_cash": float(account.initial_cash),
        "realized_pnl": realized,
        "positions": [
            {
                "asset_id": p.asset_id,
                "quantity": float(p.quantity),
                "avg_cost": float(p.avg_cost),
                "realized_pnl": float(p.realized_pnl),
            }
            for p in positions
        ],
        "trades_count": len(trades),
        "note": "Paper accounts are virtual and fully isolated from real holdings.",
    }


@router.get("/accounts/{account_id}/trades", summary="List paper trades")
def account_trades(account_id: int, db: Session = Depends(get_db)) -> list[dict]:
    account = db.get(PaperAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    rows = db.scalars(
        select(PaperTrade).where(PaperTrade.account_id == account_id).order_by(PaperTrade.id)
    ).all()
    return [
        {
            "id": t.id,
            "asset_id": t.asset_id,
            "direction": t.direction,
            "entry_time": t.entry_time,
            "entry_price": float(t.entry_price),
            "exit_time": t.exit_time,
            "exit_price": float(t.exit_price) if t.exit_price is not None else None,
            "quantity": float(t.quantity),
            "pnl": float(t.pnl) if t.pnl is not None else None,
            "reason": t.reason,
            "strategy_version": t.strategy_version,
        }
        for t in rows
    ]


@router.post("/accounts/{account_id}/reset", summary="Reset a paper account (audited)")
def reset_account(
    account_id: int,
    initial_cash: float | None = None,
    db: Session = Depends(get_db),
) -> dict:
    account = db.get(PaperAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="paper account not found")
    target = initial_cash or float(account.initial_cash)
    account.cash = target
    account.initial_cash = target
    account.reset_count += 1
    for position in db.scalars(
        select(PaperPosition).where(PaperPosition.account_id == account_id)
    ).all():
        db.delete(position)
    db.query(PaperTrade).filter(PaperTrade.account_id == account_id).delete()
    record_audit(
        db,
        event_type="paper_account_reset",
        entity_type="paper_account",
        entity_id=str(account_id),
        action="reset",
        payload={"new_cash": target, "reset_count": account.reset_count},
    )
    db.commit()
    return {
        "account_id": account_id,
        "cash": float(account.cash),
        "reset_count": account.reset_count,
        "warning": "All virtual positions and trades were deleted. This cannot be undone.",
    }
