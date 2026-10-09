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
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.explain import (
    AI_UNCONFIGURED,
    KEY_EMPTY,
    KEY_UNDECRYPTABLE,
    explain_backtest,
    explain_performance,
    explain_signal,
    get_active_provider,
    spent_today_usd,
    unusable_provider_key,
)
from app.ai.provider import BudgetExceeded
from app.api.schemas import (
    AIStatusOut,
    AITaskOut,
    CompileDraftIn,
    DraftConfirmationIn,
    ExplainOut,
    FormalizeIn,
    ResearchRunIn,
    ResearchRunOut,
)
from app.core.config import settings
from app.core.db import get_db
from app.domain.models import AIModel, AITask

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ai"])

NOT_CONFIGURED_DETAIL = (
    "AI provider not configured; configure one under Settings to enable explanations"
)

# The refusals below say *which* provider is at fault when one is configured and
# its key cannot be read. "Not configured" would be false there: the row exists
# and is enabled, and the only fix is re-entering the key (or restoring the
# SECRET_KEY the key was encrypted with).
KEY_UNREADABLE_DETAIL = (
    "AI provider '{name}' is enabled but its stored key cannot be decrypted: the "
    "encryption key (SECRET_KEY) changed after this key was saved. Re-enter the key "
    "under Settings to enable explanations"
)
KEY_EMPTY_DETAIL = (
    "AI provider '{name}' is enabled but its stored key is empty; re-enter it under "
    "Settings to enable explanations"
)


def _unavailable_detail(db: Session) -> str:
    """Why AI is unavailable, naming the provider when one is at fault."""

    broken = unusable_provider_key(db)
    if broken is None:
        return NOT_CONFIGURED_DETAIL
    provider, problem = broken
    template = KEY_EMPTY_DETAIL if problem == KEY_EMPTY else KEY_UNREADABLE_DETAIL
    return template.format(name=provider.name)


def _compile_error(
    status_code: int, code: str, message: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    """A refusal of the compile endpoint, in the shape the app already uses.

    ``{"error": {"code", "message", "details"}}`` is the envelope ``create_app``
    answers with before a handler runs at all (the 401, the 429 and the 500), and
    docs/29 §16.7 fixes the same keys for a missing draft or strategy. Answering
    in that envelope keeps one shape per outcome instead of a second dialect.
    """

    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details or {}}},
    )


# The OpenAPI document has to describe the refusals above: `_compile_error` builds its
# own JSONResponse, so FastAPI cannot infer the shape from a model, and a generated
# client would otherwise treat a 404/409 body as undefined (docs/29 §16.7).
_COMPILE_ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "additionalProperties": False,
            "required": ["code", "message", "details"],
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
                "details": {"type": "object"},
            },
        }
    },
}


@router.get("/ai/status", response_model=AIStatusOut, summary="AI configuration and budget")
def ai_status(db: Session = Depends(get_db)) -> AIStatusOut:
    configured = get_active_provider(db)
    if configured is None:
        broken = unusable_provider_key(db)
        if broken is not None:
            provider, problem = broken
            return AIStatusOut(
                configured=False,
                provider_name=provider.name,
                key_error=problem,
                spent_today_usd=0.0,
                tasks_today=0,
                note=(
                    f"Provider '{provider.name}' is enabled but its stored key cannot be "
                    "decrypted, so nothing can be routed. This happens when the SECRET_KEY "
                    "changed after the key was saved; re-enter the key under Settings to "
                    "use explanations again. All quantitative features work without AI."
                    if problem == KEY_UNDECRYPTABLE
                    else f"Provider '{provider.name}' is enabled but its stored key is empty; "
                    "re-enter it under Settings. All quantitative features work without AI."
                ),
            )
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
    if model is None:
        # The provider is enabled but no model row on it is active. With no rows
        # at all the default model is our only name and stays routable; with rows
        # that were all switched off, routing refuses (ADR-173). Asking the same
        # question here keeps this endpoint from naming a model that ``run_task``
        # would never call.
        declared = db.scalar(
            select(func.count()).select_from(AIModel).where(AIModel.provider_id == provider.id)
        )
        if declared:
            spent, calls = spent_today_usd(db, provider.id)
            return AIStatusOut(
                configured=False,
                provider_name=provider.name,
                spent_today_usd=spent,
                tasks_today=calls,
                note=(
                    f"Provider '{provider.name}' has no active model: every model row is "
                    "deactivated, so no AI task can be routed. Enable one in the model "
                    "catalogue to use explanations again."
                ),
            )
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
                detail=_unavailable_detail(db),
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
                detail=_unavailable_detail(db),
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
                detail=_unavailable_detail(db),
            ) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except BudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return ExplainOut(**result)


@router.post(
    "/backtests/{run_id}/explain-performance",
    response_model=ExplainOut,
    summary="Explain a completed backtest's performance, risk and buy-and-hold comparison",
)
def explain_performance_endpoint(run_id: int, db: Session = Depends(get_db)) -> ExplainOut:
    """Phase C explanation (docs/30 §8): words on top of the stored analysis.

    The numbers are already computed; this endpoint never becomes the source of a
    figure. A rejected explanation (an unsupported number, a promise about the
    future) is answered with 502 so the page falls back to numbers alone.
    """

    try:
        result = explain_performance(db, run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        if str(exc) == AI_UNCONFIGURED:
            raise HTTPException(
                status_code=503,
                detail=_unavailable_detail(db),
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
    provider_name, model_name = _history_names(db, row)
    return {
        "id": row.id,
        "task_type": row.task_type,
        "provider_id": row.provider_id,
        "model_id": row.model_id,
        "provider_name": provider_name,
        "model_name": model_name,
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


def _history_names(db: Session, row: Any) -> tuple[str | None, str | None]:
    """Name an AI task/usage row, snapshot first, live configuration second.

    Migration 0017 backfills the snapshots and the runtime writes them from now
    on, so a deleted provider/model still has a name here (ADR-177); the fallback
    only serves rows written before that.
    """

    from app.domain.models import AIModel, AIProvider

    provider_name = getattr(row, "provider_name", None)
    model_name = getattr(row, "model_name", None)
    if not provider_name and row.provider_id is not None:
        provider = db.get(AIProvider, row.provider_id)
        provider_name = provider.name if provider is not None else None
    if not model_name and row.model_id is not None:
        model = db.get(AIModel, row.model_id)
        model_name = model.model_name if model is not None else None
    return provider_name, model_name


@router.get("/ai/usage-today", summary="Today's AI spend (UTC)")
def usage_today(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.domain.models import AIProvider, AIUsage

    today = dt.datetime.now(tz=dt.UTC).date()
    rows = db.scalars(select(AIUsage).where(AIUsage.usage_date == today)).all()
    by_provider: dict[str, Any] = {}
    for row in rows:
        name, _ = _history_names(db, row)
        if not name:
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
    from app.data.ai_provider_service import serialize_model

    rows = db.scalars(select(AIModel).order_by(AIModel.id)).all()
    # One serializer for both the read and the enable/disable write, so the UI
    # can never see two different shapes for the same row (ADR-173).
    return {"models": [serialize_model(db, m) for m in rows]}


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
    usage: list[dict[str, Any]] = []
    for r in rows:
        provider_name, model_name = _history_names(db, r)
        usage.append(
            {
                "id": r.id,
                "usage_date": r.usage_date,
                "provider_id": r.provider_id,
                "model_id": r.model_id,
                "provider_name": provider_name,
                "model_name": model_name,
                "task_type": r.task_type,
                "call_count": r.call_count,
                "total_tokens": r.total_tokens,
                "total_cost_usd": float(r.total_cost_usd),
            }
        )
    return {"usage": usage}


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


# --------------------------------------------------------------------------- #
# Research layer (v1.9.8): material in, hypothesis and draft out
# --------------------------------------------------------------------------- #
@router.post(
    "/ai/research",
    response_model=ResearchRunOut,
    summary="Research a strategy idea: sources in, hypothesis and draft out",
    # The same endpoint answers 202 when the model steps were queued, and the document
    # has to say so: a client that knows only 200 would read a queued run as a
    # finished one (docs/26 C10).
    responses={
        202: {
            "model": ResearchRunOut,
            "description": (
                "The material was read and stored, and the model steps were queued: "
                "`status` is `queued` and `GET /ai/research/{run_id}` reports the rest. "
                "This is the answer when `AI_RESEARCH_ASYNC` is on (the default)."
            ),
        }
    },
)
def start_research_run(payload: ResearchRunIn, db: Session = Depends(get_db)) -> Any:
    """Read material, form a hypothesis, formalize a draft, check capabilities.

    A refusal is a result, not an error: the run is returned with
    ``status="rejected"`` and the violations that caused it. The draft is never
    executable, and nothing here reaches the backtest engine.

    Two model calls do not fit inside a proxy's patience, so by default the material
    is read and stored here and the model steps are queued: the answer is 202 with a
    run whose ``status`` is ``queued``, and ``GET /ai/research/{run_id}`` carries the
    outcome from there. ``AI_RESEARCH_ASYNC=false`` runs the same pipeline inline and
    answers 200 with the finished run, which a deployment with no worker needs.

    A source the platform had to fetch itself can fail before any model is called:
    a source blocked on policy grounds refuses the whole run (422), and a source
    that could not be fetched or read fails it (502) — neither is dressed up as an
    AI rejection (docs/27 §5.2). Both are decided before anything is queued, because
    neither answer would change if a worker tried.
    """

    from app.ai import research as research_service

    inputs = [
        research_service.ResearchInput(
            text=item.text or "",
            kind=item.kind,
            source_ref=item.source_ref,
            label=item.label,
            uri=item.uri,
            license_note=item.license_note,
            retention=item.retention,
            snapshot_id=item.snapshot_id,
        )
        for item in payload.sources
    ]
    queued = settings.ai_research_async
    try:
        run = research_service.prepare_research(
            db,
            question=payload.question,
            inputs=inputs,
            ingest=_research_ingester(db),
            queued=queued,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()

    _raise_for_unserved_run(db, run)
    if not queued:
        run = research_service.execute_research(db, run, model=payload.model)
        db.commit()
        _raise_for_unserved_run(db, run)
        return ResearchRunOut(**research_service.run_payload(db, run))

    _enqueue_research(run.id, payload.model)
    return JSONResponse(
        status_code=202,
        content=ResearchRunOut(**research_service.run_payload(db, run)).model_dump(mode="json"),
    )


def _raise_for_unserved_run(db: Session, run: Any) -> None:
    """Raise the transport answer for a run that never reached a model.

    Three outcomes are decided before a token is spent, and each keeps the answer it
    already had: a deployment with no provider (503), a source blocked by policy (422)
    and a source that could not be read (502). In async mode this runs *before*
    anything is queued, so a request that cannot be served is never handed to a worker
    that would only rediscover it.
    """

    if run.status == "failed" and run.error_message == AI_UNCONFIGURED:
        # The run row stays: it records that the request was made and why it
        # could not be answered.
        raise HTTPException(status_code=503, detail=_unavailable_detail(db))
    if run.status == "rejected" and run.current_step == "ingest":
        raise HTTPException(status_code=422, detail=_refused_run_detail(run))
    if run.status == "failed" and run.current_step == "ingest":
        raise HTTPException(
            status_code=502,
            detail={
                "error": "source_unavailable",
                "run_id": run.id,
                "message": run.error_message or "a source could not be read",
            },
        )


def _enqueue_research(run_id: int, model: str | None = None) -> None:
    """Hand a prepared run to the worker, and nothing else.

    Imported inside the function on purpose: the API process needs no Celery to serve
    every other endpoint, and a test can replace this one seam to assert that a queued
    request really was queued instead of run inline.
    """

    from app.workers.tasks import run_research

    run_research.delay(run_id, model)


def _research_ingester(db: Session) -> Any:
    """The fetcher the research service uses, or ``None`` when it cannot fetch.

    Injected rather than imported by ``app.ai.research`` so the AI layer stays free
    of network code (docs/27 §5.2).
    """

    from app.data import source_snapshot_service as snapshots

    return snapshots.snapshot_ingester(db)


def _refused_run_detail(run: Any) -> dict[str, Any]:
    violations = list(run.violations_json or [])
    first = violations[0] if violations else {}
    return {
        "error": "source_blocked",
        "run_id": run.id,
        "snapshot_id": first.get("snapshot_id"),
        "source_ref": first.get("source_ref"),
        "code": first.get("code"),
        "message": run.error_message or first.get("message") or "a source was refused",
        "violations": violations,
    }


def _with_snapshots(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Attach each source's stored snapshot summary, when the run observed one.

    The run records which snapshot it used; the snapshot record is what makes the
    provenance auditable, so one GET answers "which material was this based on"
    without a second round trip (docs/27 §5.2, §8).
    """

    from app.data import source_snapshot_service as snapshots

    sources = []
    for source in payload.get("sources") or []:
        entry = dict(source)
        snapshot_id = entry.get("snapshot_id")
        row = snapshots.get_snapshot(db, snapshot_id) if snapshot_id else None
        entry["snapshot"] = snapshots.snapshot_payload(row) if row is not None else None
        sources.append(entry)
    payload["sources"] = sources
    return payload


@router.get("/ai/research", summary="Recent research runs")
def list_research_runs(
    limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)
) -> dict[str, Any]:
    from app.ai import research as research_service

    runs = research_service.recent_runs(db, limit=limit)
    return {"runs": [research_service.run_summary(run) for run in runs]}


@router.get("/ai/research/{run_id}", response_model=ResearchRunOut, summary="One research run")
def get_research_run(run_id: int, db: Session = Depends(get_db)) -> ResearchRunOut:
    from app.ai import research as research_service
    from app.domain.models import AIResearchRun

    run = db.get(AIResearchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"research run {run_id} not found")
    payload = _with_snapshots(db, research_service.run_payload(db, run))
    return ResearchRunOut(**payload)


@router.post(
    "/ai/strategy/formalize",
    summary="Formalize a stored hypothesis into a new draft",
)
def formalize_hypothesis_endpoint(
    payload: FormalizeIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Run only the architect, for a hypothesis that already passed its gates.

    Returns the created draft. A refusal is 422 with the violations, because the
    request was well formed but the model's answer was not acceptable.
    """

    from app.ai import research as research_service
    from app.ai.research_schemas import ResearchRejected

    try:
        draft = research_service.formalize_hypothesis(
            db,
            hypothesis_id=payload.hypothesis_id,
            run_id=payload.run_id,
            model=payload.model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BudgetExceeded as exc:
        db.commit()
        raise HTTPException(status_code=429, detail=f"AI budget exhausted: {exc}") from exc
    except ResearchRejected as rejected:
        db.commit()
        raise HTTPException(status_code=422, detail=rejected.as_dict()) from rejected
    except RuntimeError as exc:
        db.commit()
        if str(exc) == AI_UNCONFIGURED:
            raise HTTPException(status_code=503, detail=_unavailable_detail(db)) from exc
        logger.warning("AI formalization failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"AI provider call failed: {exc}") from exc
    db.commit()
    return {"draft": research_service.draft_payload(draft)}


@router.post(
    "/ai/strategy/drafts/{draft_id}/compile",
    summary="Compile a stored draft into a strategy version",
    # 201 is the "a row was created" answer docs/29 §16.7 freezes for COMPILED; the
    # refusal paths return their own JSONResponse, so this route is not one model.
    status_code=201,
    response_model=None,
    # The refusals that happen before the compiler answers are built by hand, so the
    # document has to be told about them. Without this it offered only 201 and 422, and
    # the five refusal codes existed nowhere but in docs/29 §16.7 and in the tests.
    responses={
        404: {
            "description": (
                "The row the request names does not exist: `draft_not_found` or "
                "`strategy_not_found` (docs/29 §16.7). A transport answer, not a compiler "
                "verdict, so the body carries no `report`."
            ),
            "content": {"application/json": {"schema": _COMPILE_ERROR_SCHEMA}},
        },
        409: {
            "description": (
                "Nothing was compiled, for one of two reasons. The human gate has not "
                "been passed: `draft_not_confirmed` (no review recorded, or the newest "
                "decision is `rejected`/`needs_revision`). Or the target identity is "
                "already taken: `draft_already_compiled`, `version_unassignable` or "
                "`version_conflict` (docs/29 §16.7). `error.details` locates the draft "
                "or the target; a 409 never carries `result` or `report` (ADR-169)."
            ),
            "content": {"application/json": {"schema": _COMPILE_ERROR_SCHEMA}},
        },
    },
)
def compile_draft_endpoint(
    draft_id: int, payload: CompileDraftIn, db: Session = Depends(get_db)
) -> JSONResponse | dict[str, Any]:
    """Run the deterministic compiler for one stored draft (docs/29 §16.7).

    The compiler decides, and this endpoint only carries the decision into the
    database: ``COMPILED`` creates exactly one ``StrategyVersion`` whose
    ``evidence_json.compile_report`` is the report the compiler returned, and
    points the draft at it; ``NEEDS_USER_DECISION`` and ``REJECTED`` create
    nothing, so a draft the contract refuses cannot leave a half-built strategy
    version behind. The request names a target and nothing else -- no spec, no
    hash, no compiler version -- because the compiler is the only way in.
    """

    from app.ai import confirmation as confirmation_service
    from app.compiler import compile_strategy_draft
    from app.data import strategy_service
    from app.domain.models import Strategy, StrategyVersion
    from app.domain.models import StrategyDraft as StrategyDraftRow

    draft = db.get(StrategyDraftRow, draft_id)
    if draft is None:
        return _compile_error(404, "draft_not_found", f"strategy draft {draft_id} not found")

    strategy = db.get(Strategy, payload.strategy_id)
    if strategy is None:
        return _compile_error(
            404, "strategy_not_found", f"strategy {payload.strategy_id} not found"
        )

    # One draft describes one strategy version. Compiling it twice would create a
    # second row for the same draft, so the second attempt is a conflict that says
    # what the draft is already bound to.
    if draft.compiled_strategy_version_id is not None:
        bound = db.get(StrategyVersion, draft.compiled_strategy_version_id)
        return _compile_error(
            409,
            "draft_already_compiled",
            f"strategy draft {draft_id} is already compiled into strategy version "
            f"{draft.compiled_strategy_version_id}",
            {
                "strategy_version_id": draft.compiled_strategy_version_id,
                "version": bound.version if bound is not None else None,
            },
        )

    # The human gate (v2.5.0 Step 2). A draft is a proposal; only a person may
    # turn it into a strategy version, so the compiler is not called at all until
    # the newest recorded decision is "confirmed". This is enforced here rather
    # than in the UI, which means a direct API call is refused exactly the same
    # way. Nothing has been written when this returns.
    try:
        confirmation_service.require_confirmation(db, draft)
    except confirmation_service.ConfirmationRequired as exc:
        return _compile_error(
            409,
            confirmation_service.UNCONFIRMED_CODE,
            str(exc),
            {"draft_id": draft.id, "decision": exc.decision},
        )

    # The version comes from the project's own rule (ADR-061), not from a new one
    # invented here. A history that cannot be incremented has no answer, because
    # this request may not name a version itself.
    try:
        version = strategy_service.next_version([row.version for row in strategy.versions])
    except ValueError as exc:
        return _compile_error(
            409,
            "version_unassignable",
            str(exc),
            {
                "strategy_id": strategy.id,
                "versions": [row.version for row in strategy.versions],
            },
        )

    result = compile_strategy_draft(draft, strategy.id, version)
    if not result.is_compiled:
        # The request was well formed and the draft was not compilable: 422 with
        # the compiler's own report, and nothing written.
        return JSONResponse(
            status_code=422,
            content={"result": result.result, "report": result.report},
        )

    try:
        strategy_version = strategy_service.create_strategy_version(
            db,
            strategy,
            version=version,
            dsl=result.as_dsl(),
            evidence={"compile_report": result.report},
            # COMPILED is not "live" (ADR-171). Compiling produces a version; what
            # puts it on the signal path is a separate, explicit, audited activation.
            make_current=False,
            commit=False,
        )
    except ValueError as exc:
        db.rollback()
        return _compile_error(
            409, "version_conflict", str(exc), {"strategy_id": strategy.id, "version": version}
        )
    except IntegrityError as exc:
        db.rollback()
        logger.warning("strategy version %s could not be created: %s", version, exc)
        return _compile_error(
            409,
            "version_conflict",
            f"version '{version}' already exists for this strategy",
            {"strategy_id": strategy.id, "version": version},
        )

    # The version row, its parameters, its audit event and the draft's
    # back-reference commit as one transaction: a failure here rolls the version
    # back too, so the draft is never bound to a version that does not exist.
    draft.compiled_strategy_version_id = strategy_version.id
    db.commit()

    return {
        "result": result.result,
        "strategy_id": strategy.id,
        "strategy_version_id": strategy_version.id,
        "version": strategy_version.version,
        "compile_hash": result.compile_hash,
        "report": result.report,
    }


@router.post(
    "/ai/strategy/drafts/{draft_id}/confirmations",
    summary="Record a human decision about a draft",
    # 201: a row was created, and the row *is* the record -- an audit event.
    status_code=201,
    response_model=None,
    responses={
        404: {"description": "The draft does not exist."},
        422: {"description": "The decision is not one of the three allowed answers."},
    },
)
def confirm_draft_endpoint(
    draft_id: int, payload: DraftConfirmationIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Record what a person decided about one draft (v2.4.0 Step 1).

    The draft is the AI's proposal; this is the human answer, kept as an
    append-only audit event. It creates no ``StrategyVersion``, sets no
    ``validation_status`` and touches no ``is_current``: a confirmation is
    evidence about a draft, and the deterministic compiler plus the ADR-171
    activation gate remain the only ways a strategy goes live. The audit row is
    the whole transaction.
    """

    from app.ai import confirmation as confirmation_service
    from app.domain.models import StrategyDraft as StrategyDraftRow

    draft = db.get(StrategyDraftRow, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="draft_not_found")

    try:
        record = confirmation_service.record_confirmation(
            db, draft=draft, decision=payload.decision, note=payload.note
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    db.commit()
    return {
        "draft_id": draft.id,
        "run_id": draft.run_id,
        "decision": record.decision,
        "label": confirmation_service.LABELS[record.decision],
        "confirmation": confirmation_service.confirmation_view(db, draft.id),
        # Stated in the response, not implied: this endpoint cannot make a
        # strategy live, and a client should not have to read the source to know.
        "strategy_version_created": False,
        "compiled_strategy_version_id": draft.compiled_strategy_version_id,
    }
