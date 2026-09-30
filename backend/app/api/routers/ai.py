"""AI endpoints: status, explanations, task history.

Every number shown here was computed by the deterministic engine. The AI
endpoints only attach natural-language explanations to those facts. When no
provider is configured the quantitative product keeps working; only the
explanation buttons report "not configured".
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.explain import (
    AI_UNCONFIGURED,
    explain_backtest,
    explain_signal,
    get_active_provider,
    spent_today_usd,
)
from app.ai.provider import BudgetExceeded
from app.api.schemas import AIStatusOut, AITaskOut, ExplainOut
from app.core.db import get_db
from app.domain.models import AITask

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ai"])

NOT_CONFIGURED_DETAIL = (
    "AI provider not configured; configure one under Settings to enable explanations"
)


@router.get("/ai/status", response_model=AIStatusOut, summary="AI configuration and budget")
def ai_status(db: Session = Depends(get_db)) -> AIStatusOut:
    configured = get_active_provider(db)
    if configured is None:
        return AIStatusOut(
            configured=False,
            spent_today_usd=0.0,
            tasks_today=0,
            note=(
                "No AI provider configured. Set one up under Settings "
                "(OpenAI-compatible endpoint + key) to enable explanations. "
                "All quantitative features work without AI."
            ),
        )
    provider, model, _ = configured
    spent, calls = spent_today_usd(db, provider.id)
    budget = float(provider.daily_budget_usd or 0)
    return AIStatusOut(
        configured=True,
        provider_name=provider.name,
        model=model.model_name if model else provider.default_model,
        daily_budget_usd=budget,
        spent_today_usd=spent,
        tasks_today=calls,
        note=(
            "AI explains engine-computed facts only. Costs shown are estimates; "
            "quantitative features keep working if the budget runs out."
            if spent < budget
            else "Daily budget exhausted; explanations paused until tomorrow (UTC). "
            "Quantitative features keep working."
        ),
    )


@router.post(
    "/signals/{signal_id}/explain",
    response_model=ExplainOut,
    summary="Explain a persisted signal",
)
def explain_signal_endpoint(signal_id: int, db: Session = Depends(get_db)) -> ExplainOut:
    try:
        result = explain_signal(db, signal_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == AI_UNCONFIGURED:
            raise HTTPException(
                status_code=503,
                detail=NOT_CONFIGURED_DETAIL,
            ) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except BudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return ExplainOut(**result)


@router.post(
    "/signals/preview-explain",
    response_model=ExplainOut,
    summary="Explain the current (unpersisted) signal preview",
)
def explain_preview(payload: dict[str, Any], db: Session = Depends(get_db)) -> ExplainOut:
    """Explain a signal intent without persisting a Signal row.

    The dashboard scan results are ephemeral (no database id), so this endpoint
    re-derives the same intent the evidence endpoint shows and explains it.
    The AITask row is still persisted for audit and cache purposes.
    """

    from app.data.market_data_repo import load_bars
    from app.data.strategy_service import load_spec
    from app.domain.models import Asset, MarketDataSeries, StrategyVersion
    from app.features.engine import build_features
    from app.strategies.executor import run_strategy

    try:
        strategy_version_id = int(payload.get("strategy_version_id", 0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="strategy_version_id is required") from exc
    symbol = payload.get("symbol")
    timeframe = str(payload.get("timeframe") or "1d")

    version = db.get(StrategyVersion, strategy_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="strategy version not found")
    asset = db.scalar(select(Asset).where(Asset.symbol == symbol)) if symbol else None
    series = db.scalar(
        select(MarketDataSeries).where(
            MarketDataSeries.timeframe == timeframe,
            *([MarketDataSeries.asset_id == asset.id] if asset else []),
        )
    )
    if series is None:
        raise HTTPException(status_code=404, detail="no matching market data series")

    bars = load_bars(db, series, only_closed=True, limit=800)
    if bars.empty:
        raise HTTPException(status_code=422, detail="no closed bars available")
    feature_frame = build_features(bars)
    spec = load_spec(version)
    _decisions, intent = run_strategy(spec, feature_frame.frame)

    from app.ai.explain import explain_signal_facts

    facts: dict[str, Any] = {
        "kind": "signal",
        "state": intent.get("state"),
        "direction": intent.get("direction"),
        "symbol": asset.symbol if asset else None,
        "timeframe": timeframe,
        "bar_time": intent.get("bar_time"),
        "price_reference": intent.get("price_reference"),
        "stop_reference": intent.get("stop_reference"),
        "target_reference": intent.get("target_reference"),
        "triggered_rules": intent.get("triggered_rules", []),
        "strategy_version": version.version,
        "data_source": f"series:{series.id}/{series.dataset_version}",
    }
    try:
        result = explain_signal_facts(db, facts, state=str(intent.get("state") or "WAIT"))
    except RuntimeError as exc:
        if str(exc) == AI_UNCONFIGURED:
            raise HTTPException(
                status_code=503,
                detail=NOT_CONFIGURED_DETAIL,
            ) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except BudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return ExplainOut(**result)


@router.post(
    "/backtests/{run_id}/explain",
    response_model=ExplainOut,
    summary="Explain a completed backtest",
)
def explain_backtest_endpoint(run_id: int, db: Session = Depends(get_db)) -> ExplainOut:
    try:
        result = explain_backtest(db, run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == AI_UNCONFIGURED:
            raise HTTPException(
                status_code=503,
                detail=NOT_CONFIGURED_DETAIL,
            ) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except BudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return ExplainOut(**result)


@router.get("/ai/tasks", response_model=list[AITaskOut], summary="Recent AI tasks")
def list_ai_tasks(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[AITaskOut]:
    rows = db.scalars(select(AITask).order_by(AITask.id.desc()).limit(limit)).all()
    out: list[AITaskOut] = []
    for row in rows:
        payload = AITaskOut.model_validate(row)
        payload.cost_usd = float(row.cost_usd) if row.cost_usd is not None else None
        out.append(payload)
    return out


@router.get("/ai/usage-today", summary="Today's AI spend (UTC)")
def usage_today(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.domain.models import AIProvider, AIUsage

    today = dt.datetime.now(tz=dt.UTC).date()
    rows = db.scalars(select(AIUsage).where(AIUsage.usage_date == today)).all()
    by_provider: dict[str, Any] = {}
    for row in rows:
        provider = db.get(AIProvider, row.provider_id) if row.provider_id else None
        name = provider.name if provider else "unknown"
        entry = by_provider.setdefault(
            name, {"calls": 0, "tokens_estimated": 0, "cost_usd_estimated": 0.0}
        )
        entry["calls"] += int(row.call_count or 0)
        entry["tokens_estimated"] += int(row.total_tokens or 0)
        entry["cost_usd_estimated"] += float(row.total_cost_usd or 0)
    return {"date": today.isoformat(), "providers": by_provider}
