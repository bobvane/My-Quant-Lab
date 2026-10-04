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

    from app.data.market_data_repo import load_bars, resolve_series
    from app.data.strategy_service import load_spec
    from app.domain.models import StrategyVersion
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
    series = resolve_series(db, symbol=symbol, timeframe=timeframe)

    bars = load_bars(db, series, only_closed=True, limit=800)
    if bars.empty:
        raise HTTPException(status_code=422, detail="no closed bars available")
    spec = load_spec(version)
    feature_frame = build_features(bars, spec=spec)
    _decisions, intent = run_strategy(spec, feature_frame.frame)

    from app.ai.explain import explain_signal_facts

    facts: dict[str, Any] = {
        "kind": "signal",
        "state": intent.get("state"),
        "direction": intent.get("direction"),
        "symbol": series.asset.symbol if series.asset is not None else symbol,
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


@router.get("/ai/tasks/{task_id}/status", summary="Status of one AI task")
def get_ai_task_status(task_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.domain.models import AITask

    row = db.get(AITask, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ai task not found")
    return {
        "id": row.id,
        "task_type": row.task_type,
        "status": row.status,
        "error_message": row.error_message,
        "created_at": row.created_at,
        "completed_at": row.completed_at,
    }


@router.get("/ai/tasks/{task_id}", summary="One AI task with its stored output")
def get_ai_task(task_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.domain.models import AITask

    row = db.get(AITask, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ai task not found")
    return {
        "id": row.id,
        "task_type": row.task_type,
        "provider_id": row.provider_id,
        "model_id": row.model_id,
        "prompt_name": row.prompt_name,
        "prompt_version": row.prompt_version,
        "status": row.status,
        "input_hash": row.input_hash,
        "output": row.output_json,
        "token_usage": row.token_usage_json,
        "cost_usd": float(row.cost_usd) if row.cost_usd is not None else None,
        "error_message": row.error_message,
        "created_at": row.created_at,
        "completed_at": row.completed_at,
    }


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


@router.get("/ai/models", summary="AI models across providers")
def list_models(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.domain.models import AIModel, AIProvider

    rows = db.scalars(select(AIModel).order_by(AIModel.id)).all()
    providers = {p.id: p.name for p in db.scalars(select(AIProvider)).all()}
    return {
        "models": [
            {
                "id": m.id,
                "provider": providers.get(m.provider_id),
                "model_name": m.model_name,
                "capability_tier": m.capability_tier,
                "input_cost_per_mtok": float(m.input_cost_per_mtok),
                "output_cost_per_mtok": float(m.output_cost_per_mtok),
                "is_active": m.is_active,
            }
            for m in rows
        ]
    }


@router.get("/ai/usage", summary="AI usage rows (by date / provider)")
def ai_usage(
    db: Session = Depends(get_db),
    date: str | None = None,
    provider_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    from app.domain.models import AIUsage

    stmt = select(AIUsage).order_by(AIUsage.id.desc())
    if date:
        try:
            parsed_date = dt.date.fromisoformat(date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD") from exc
        stmt = stmt.where(AIUsage.usage_date == parsed_date)
    if provider_id is not None:
        stmt = stmt.where(AIUsage.provider_id == provider_id)
    rows = db.scalars(stmt.limit(limit)).all()
    return {
        "usage": [
            {
                "id": r.id,
                "usage_date": r.usage_date,
                "provider_id": r.provider_id,
                "model_id": r.model_id,
                "task_type": r.task_type,
                "call_count": r.call_count,
                "total_tokens": r.total_tokens,
                "total_cost_usd": float(r.total_cost_usd),
            }
            for r in rows
        ]
    }


def _seed_builtin_prompts(db: Session) -> None:
    """Idempotently register the built-in prompt definitions (docs/06 §prompt 版本化).

    The definitions are read from the role contract files instead of being spelled
    out here (ADR-150): the file that the model actually receives is the file that
    gets registered, and the same file's hash travels with every call.
    """

    from app.ai.role_contracts import role_contracts, sync_role_contracts, task_output_schemas
    from app.domain.models import AIPrompt

    schemas = task_output_schemas()
    changed = False
    for contract in sorted(role_contracts(), key=lambda c: (c.name, c.version)):
        for task_type in contract.task_types:
            name = contract.prompt_name_for(task_type)
            exists = db.scalar(
                select(AIPrompt).where(AIPrompt.name == name, AIPrompt.version == contract.version)
            )
            if exists is not None:
                continue
            db.add(
                AIPrompt(
                    name=name,
                    version=contract.version,
                    task_type=task_type,
                    system_prompt=contract.system_prompt_for(task_type),
                    user_template="",
                    output_schema_json=schemas.get(task_type),
                )
            )
            changed = True
    if changed:
        db.commit()
    sync_role_contracts(db)


@router.get("/ai/prompts", summary="AI prompt templates")
def list_prompts(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.domain.models import AIPrompt

    _seed_builtin_prompts(db)
    rows = db.scalars(select(AIPrompt).order_by(AIPrompt.name, AIPrompt.version)).all()
    return {
        "prompts": [
            {
                "id": p.id,
                "name": p.name,
                "version": p.version,
                "task_type": p.task_type,
                "capability_tier": p.capability_tier,
                "is_active": p.is_active,
            }
            for p in rows
        ]
    }


@router.get("/ai/capabilities", summary="What the quantitative engine can express")
def ai_capabilities() -> dict[str, Any]:
    """The AI's knowledge boundary, generated from the code (ADR-151).

    Everything listed here is derived from the DSL types, the feature engine, the
    metrics dataclass and the market-data providers, so a capability cannot be
    advertised here without existing there. The ``unsupported`` list is the
    counterpart: the things a model must refuse to quietly invent.
    """

    from app.capabilities import capability_payload

    return capability_payload()


@router.get("/ai/roles", summary="Role contracts shipped with this build")
def ai_roles(db: Session = Depends(get_db)) -> dict[str, Any]:
    """The role contracts as parsed from disk, plus the indexed rows behind them.

    Reading this endpoint also refreshes ``ai_role_contracts`` (the same
    idempotent sync ``/ai/prompts`` performs for prompt rows), so the audit trail
    always has a row for a contract that was actually used.
    """

    from app.ai.role_contracts import (
        CONTRACTS_DIR,
        role_contracts,
        sync_role_contracts,
        system_contract,
    )

    rows = sync_role_contracts(db)
    indexed = {row.name: row for row in rows}
    contracts = sorted(role_contracts(), key=lambda c: (c.name, c.version))
    system = system_contract()
    return {
        "contracts_dir": str(CONTRACTS_DIR),
        "system": {
            "ref": system.ref,
            "content_hash": system.content_hash,
            "source_path": system.path,
        },
        "roles": [
            {
                "name": contract.name,
                "role": contract.role,
                "version": contract.version,
                "ref": contract.ref,
                "task_types": list(contract.task_types),
                "prompt_names": dict(contract.prompt_names),
                "required_capabilities": list(contract.required_capabilities),
                "output_language": contract.output_language,
                "content_hash": contract.content_hash,
                "source_path": contract.path,
                "indexed": contract.name in indexed,
            }
            for contract in contracts
        ],
    }


@router.get("/ai/audit/{task_id}", summary="Trace one AI task back to its inputs")
def ai_audit(task_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Provider, model, role contract, prompt version, hashes and sources of one task."""

    from app.ai.runtime import audit_payload

    task = db.get(AITask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"AI task {task_id} not found")
    return audit_payload(db, task)
