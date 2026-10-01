"""Backtest metrics read endpoint (docs/12)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.models import BacktestRun

router = APIRouter(prefix="/backtest-metrics", tags=["backtests"])


@router.get("/backtest/{backtest_id}", summary="Metrics for a backtest run")
def backtest_metrics(backtest_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    run = db.get(BacktestRun, backtest_id)
    if run is None or not run.result:
        raise HTTPException(status_code=404, detail="backtest run or result not found")
    return {
        "backtest_run_id": run.id,
        "status": run.status,
        "result_hash": run.result.result_hash,
        "summary": run.result.summary_json,
        "metrics": run.result.metrics_json,
        "calculated_at": run.result.calculated_at,
    }
