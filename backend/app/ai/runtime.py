"""AI runtime: cache key, budget guard and audit trail (ADR-153).

The runtime is the one place an AI call is executed, so it is the one place the
rules can be enforced:

* **cache key** — an answer is reused only for the same *payload* (task, prompt
  reference, role, engine facts) produced by the same provider and model. Keying
  on the payload alone served an explanation written by model A to a caller who
  had switched to model B, which is how a stale answer survives a model change.
* **budget** — the three-level chain in :mod:`app.ai.budget` runs before the
  provider is called, and a refusal is a ``BudgetExceeded`` carrying the level
  that refused.
* **audit** — every task records its role, the contract hash behind its prompt,
  the hash of the stored output and any untrusted source references, so a
  completed explanation can be traced back to the exact files that produced it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.budget import guard, record_usage, spent_today_usd, spent_today_usd_all
from app.ai.provider import (
    AIRequest,
    AIRouter,
    BudgetExceeded,
    ModelOption,
    OpenAICompatibleProvider,
    validate_structured_dict,
)
from app.core.config import settings
from app.domain.models import AIModel, AIProvider, AITask

logger = logging.getLogger(__name__)

__all__ = [
    "audit_payload",
    "cache_key",
    "estimate_request_cost",
    "estimate_tokens",
    "output_hash",
    "run_task",
    "source_snapshot_hash",
]


def estimate_tokens(text: str) -> int:
    """Rough token estimate (chars/4), always labelled as estimated downstream."""

    return max(1, len(text or "") // 4)


def output_hash(output: Any) -> str:
    """Hash of the exact output that was stored, for the audit trail."""

    payload = json.dumps(output, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def source_snapshot_hash(request: AIRequest) -> str:
    """Identity of the untrusted material a request read (empty when it read none).

    Two runs of the same question over different source text must not share a
    cache entry, so the text itself goes into the hash rather than a label.
    """

    if not request.untrusted_sources:
        return ""
    payload = [
        {"kind": source.kind, "ref": source.ref, "text": source.text}
        for source in request.untrusted_sources
    ]
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def cache_key(
    request: AIRequest,
    *,
    provider: str,
    model: str,
    prompt_hash: str = "",
    tool_result_hash: str = "",
    source_snapshot_hash: str = "",
    strategy_version: str | None = None,
) -> str:
    """The identity of one answer: payload + who produced it + what it read.

    The extra components are empty today except ``provider``/``model``/
    ``prompt_hash``; they exist so the research tasks that arrive with tool calls
    and source snapshots cannot accidentally share a cache entry with an
    uncited run of the same prompt.
    """

    payload = {
        "payload": request.input_hash(),
        "provider": provider,
        "model": model,
        "role": request.role,
        "prompt_hash": prompt_hash,
        "tool_result_hash": tool_result_hash,
        "source_snapshot_hash": source_snapshot_hash,
        "strategy_version": strategy_version,
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def estimate_request_cost(request: AIRequest, model: AIModel | None) -> float:
    """Worst-case cost of one request, priced with the chosen model.

    The prompt is measured exactly (it is text we already hold) and the output is
    priced at ``max_tokens``, so the guard errs towards refusing rather than
    overshooting the day's budget.
    """

    if model is None:
        return 0.0
    in_tokens = estimate_tokens(request.system_prompt) + estimate_tokens(request.user_prompt)
    in_tokens += estimate_tokens(
        json.dumps(request.structured_facts, default=str, ensure_ascii=False)
    )
    out_tokens = int(request.max_tokens or 0)
    return (in_tokens / 1_000_000) * float(model.input_cost_per_mtok or 0) + (
        out_tokens / 1_000_000
    ) * float(model.output_cost_per_mtok or 0)


def audit_payload(db: Session, task: AITask) -> dict[str, Any]:
    """Everything needed to explain later how one AI result came to exist."""

    from app.ai.role_contracts import load_contracts

    provider = db.get(AIProvider, task.provider_id) if task.provider_id else None
    model = db.get(AIModel, task.model_id) if task.model_id else None
    contract = load_contracts().get(task.role) if task.role else None
    return {
        "task_id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "role": task.role,
        "role_contract": contract.ref if contract is not None else None,
        "role_contract_hash": contract.content_hash if contract is not None else None,
        "prompt_name": task.prompt_name,
        "prompt_version": task.prompt_version,
        "provider": provider.name if provider is not None else None,
        "model": (model.model_name if model is not None else None),
        "input_hash": task.input_hash,
        "output_hash": task.output_hash,
        "source_ids": list(task.source_ids_json or []),
        "strategy_version_id": task.strategy_version_id,
        "research_run_id": task.research_run_id,
        "cost_usd": float(task.cost_usd) if task.cost_usd is not None else None,
        "token_usage": dict(task.token_usage_json or {}),
        "error_message": task.error_message,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
    }


def _catalogue(
    db: Session, providers: list[tuple[AIProvider, str, list[AIModel]]]
) -> tuple[
    dict[str, OpenAICompatibleProvider],
    dict[str, AIProvider],
    dict[str, list[AIModel]],
    list[ModelOption],
    dict[str, float],
]:
    live: dict[str, OpenAICompatibleProvider] = {}
    by_name: dict[str, AIProvider] = {}
    provider_models: dict[str, list[AIModel]] = {}
    models: list[ModelOption] = []
    budgets: dict[str, float] = {}
    for provider, api_key, pmodels in providers:
        live[provider.name] = OpenAICompatibleProvider(
            base_url=provider.base_url,
            api_key=api_key,
            name=provider.name,
            timeout=float(settings.ai_task_timeout_seconds or 60.0),
        )
        by_name[provider.name] = provider
        provider_models[provider.name] = pmodels
        spent, _ = spent_today_usd(db, provider.id)
        budgets[provider.name] = max(0.0, float(provider.daily_budget_usd or 0) - spent)
        for model in pmodels:
            if not model.model_name:
                continue
            models.append(
                ModelOption(
                    provider.name,
                    model.model_name,
                    model.capability_tier or "standard",
                    float(model.input_cost_per_mtok or 0),
                    float(model.output_cost_per_mtok or 0),
                )
            )
        if not pmodels:
            models.append(
                ModelOption(
                    provider.name, provider.default_model or "default", "standard", 0.0, 0.0
                )
            )
    return live, by_name, provider_models, models, budgets


def run_task(
    db: Session,
    request: AIRequest,
    *,
    providers: list[tuple[AIProvider, str, list[AIModel]]],
    router_factory: Callable[[dict[str, OpenAICompatibleProvider], float], AIRouter] | None = None,
    prompt_hash: str = "",
) -> dict[str, Any]:
    """Execute one structured AI task: cache → budget → call → validate → audit."""

    if not providers:
        raise RuntimeError("ai_not_configured")

    live, by_name, provider_models, models, budgets = _catalogue(db, providers)
    global_limit = float(settings.ai_daily_budget_usd or 0.0)
    global_spent = spent_today_usd_all(db)
    total_budget = max(0.0, global_limit - global_spent)

    if router_factory is not None:
        # Test seam: the injected factory receives the live-provider map and the
        # remaining system budget, keeping its contract unchanged.
        router = router_factory(live, total_budget)
    else:
        router = AIRouter(live, total_budget, models=models, budgets=budgets)

    pick = getattr(router, "pick", None)
    if callable(pick):
        provider_name, model_name = pick(request.task_type, request.model)
    else:
        # A fake router (tests) has no pick(): fall back to the first provider.
        provider_name = next(iter(by_name))
        model_name = request.model or "default"
    provider = by_name[provider_name]
    chosen_models = provider_models.get(provider_name, [])
    model = next((m for m in chosen_models if m.model_name == model_name), None)

    key = cache_key(
        request,
        provider=provider_name,
        model=model_name,
        prompt_hash=prompt_hash,
        source_snapshot_hash=source_snapshot_hash(request),
    )
    cached = db.scalar(
        select(AITask).where(
            AITask.input_hash == key,
            AITask.status == "completed",
            AITask.output_json.is_not(None),
        )
    )
    if cached is not None and cached.output_json:
        # A cached answer costs nothing, so it is served even when the budget is
        # exhausted; the budget only gates new provider calls.
        return {
            "explanation": dict(cached.output_json),
            "cached": True,
            "task_id": cached.id,
            "model": model_name,
            "cost_usd_estimated": 0.0,
        }

    spent, calls = spent_today_usd(db, provider.id)
    decision = guard(
        db,
        provider_limit_usd=float(provider.daily_budget_usd or 0),
        provider_spent_usd=spent,
        estimated_usd=estimate_request_cost(request, model),
        # A single run may not exceed its own cap, and a deployment may cap how
        # many AI calls a day it will pay for; both are settings, so an operator
        # can tighten them without touching code (0 means "no calls at all").
        task_limit_usd=float(settings.ai_task_budget_usd or 0),
        calls_today=calls,
        daily_call_limit=int(settings.ai_daily_task_limit or 0),
    )
    if not decision.allowed:
        raise BudgetExceeded(decision.reason)

    task = AITask(
        task_type=request.task_type,
        provider_id=provider.id,
        model_id=model.id if model else None,
        prompt_name=request.prompt_name,
        prompt_version=request.prompt_version,
        role=request.role or None,
        input_hash=key,
        input_json={"facts": request.structured_facts},
        source_ids_json=[
            {"kind": source.kind, "ref": source.ref} for source in request.untrusted_sources
        ]
        or None,
        status="running",
    )
    db.add(task)
    db.flush()

    try:
        output = router.explain_signal(request, spent_today_usd=spent)
        validated = validate_structured_dict(output, request.schema or {})
    except BudgetExceeded:
        task.status = "failed"
        task.error_message = "budget exceeded during call"
        db.commit()
        raise
    except Exception as exc:
        task.status = "failed"
        task.error_message = f"{type(exc).__name__}: {exc}"[:500]
        db.commit()
        raise RuntimeError(f"AI provider call failed: {exc}") from exc

    in_tokens = estimate_tokens(request.system_prompt + request.user_prompt)
    out_tokens = estimate_tokens(str(validated))
    in_price = float(model.input_cost_per_mtok) if model else 0.0
    out_price = float(model.output_cost_per_mtok) if model else 0.0
    cost = (in_tokens / 1_000_000) * in_price + (out_tokens / 1_000_000) * out_price

    task.output_json = dict(validated)
    task.output_hash = output_hash(validated)
    task.token_usage_json = {
        "input_tokens_estimated": in_tokens,
        "output_tokens_estimated": out_tokens,
        "estimated": True,
    }
    task.cost_usd = Decimal(str(cost))
    task.status = "completed"
    task.completed_at = dt.datetime.now(tz=dt.UTC)
    record_usage(
        db,
        provider_id=provider.id,
        model_id=model.id if model else None,
        task_type=request.task_type,
        in_tokens=in_tokens,
        out_tokens=out_tokens,
        cost_usd=cost,
    )
    db.commit()
    return {
        "explanation": dict(validated),
        "cached": False,
        "task_id": task.id,
        "model": model_name,
        "cost_usd_estimated": cost,
    }
