"""AI explanations for signals and backtests (advisory only).

Hard rules, enforced by construction:

* Every number shown to the user comes from the deterministic engine
  (``build_signal_facts`` / ``build_backtest_facts`` read stored results).
  The model receives them as *input* and may only restate them in words.
* The output schema contains no numeric fact fields, so there is nowhere for
  an invented number to hide.
* Every call is hashed (``AIRequest.input_hash``): identical facts reuse the
  stored explanation without spending budget.
* Token counts from the minimal client are character-based *estimates* and are
  labeled as such everywhere they appear; budget enforcement uses the same
  estimate consistently.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.provider import (
    SIGNAL_EXPLANATION_SCHEMA,
    AIRequest,
    AIRouter,
    BudgetExceeded,
    OpenAICompatibleProvider,
    validate_structured_dict,
)
from app.domain.models import AIModel, AIProvider, AITask, AIUsage, BacktestRun, Signal
from app.infrastructure.secrets import decrypt_secret

logger = logging.getLogger(__name__)

__all__ = [
    "AI_UNCONFIGURED",
    "BACKTEST_EXPLANATION_SCHEMA",
    "build_backtest_facts",
    "build_signal_facts",
    "estimate_tokens",
    "explain_backtest",
    "explain_signal",
    "explain_signal_facts",
    "get_active_provider",
    "spent_today_usd",
]

AI_UNCONFIGURED = "ai_not_configured"

BACKTEST_EXPLANATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "summary",
        "key_drivers",
        "risks",
        "what_to_watch_next",
        "plain_language",
    ],
    "properties": {
        "summary": {"type": "string"},
        "key_drivers": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "what_to_watch_next": {"type": "array", "items": {"type": "string"}},
        "plain_language": {"type": "string"},
    },
}

SIGNAL_SYSTEM_PROMPT = (
    "You are a patient tutor explaining a trading signal to a non-programmer. "
    "You receive authoritative facts already computed by a quantitative engine. "
    "RULES: never invent, recompute or round any number — restate the given "
    "figures exactly or say a figure is unavailable. Never promise profit. "
    "Never output a BUY/SELL recommendation of your own; explain the one given. "
    "Keep it short and plain."
)

BACKTEST_SYSTEM_PROMPT = (
    "You are a patient tutor explaining backtest results to a non-programmer. "
    "You receive authoritative statistics already computed by a backtest engine. "
    "RULES: never invent, recompute or round any number — restate the given "
    "figures exactly or say a figure is unavailable. Past results do not "
    "predict future returns; say so. Keep it short and plain."
)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (chars/4), always labeled as estimated downstream."""

    return max(1, len(text or "") // 4)


def get_active_provider(
    db: Session,
) -> tuple[AIProvider, AIModel | None, str] | None:
    """Return ``(provider, model, decrypted_key)`` or None when unconfigured."""

    provider = db.scalar(
        select(AIProvider)
        .where(AIProvider.is_active.is_(True), AIProvider.api_key_encrypted.is_not(None))
        .order_by(AIProvider.id)
    )
    if provider is None:
        return None
    model = db.scalar(
        select(AIModel)
        .where(AIModel.provider_id == provider.id, AIModel.is_active.is_(True))
        .order_by(AIModel.id)
    )
    try:
        api_key = decrypt_secret(provider.api_key_encrypted or "")
    except Exception:
        logger.warning("stored AI key failed to decrypt", exc_info=True)
        return None
    if not api_key:
        return None
    return provider, model, api_key


def spent_today_usd(db: Session, provider_id: int) -> tuple[float, int]:
    """Return ``(cost, calls)`` recorded for this provider since UTC midnight."""

    today = dt.datetime.now(tz=dt.UTC).date()
    rows = db.scalars(
        select(AIUsage).where(AIUsage.provider_id == provider_id, AIUsage.usage_date == today)
    ).all()
    return (
        sum(float(r.total_cost_usd or 0) for r in rows),
        sum(int(r.call_count or 0) for r in rows),
    )


def record_usage(
    db: Session,
    *,
    provider_id: int,
    model_id: int | None,
    task_type: str,
    in_tokens: int,
    out_tokens: int,
    cost_usd: float,
) -> None:
    today = dt.datetime.now(tz=dt.UTC).date()
    row = db.scalar(
        select(AIUsage).where(
            AIUsage.usage_date == today,
            AIUsage.provider_id == provider_id,
            AIUsage.model_id == model_id,
            AIUsage.task_type == task_type,
        )
    )
    if row is None:
        row = AIUsage(
            usage_date=today,
            provider_id=provider_id,
            model_id=model_id,
            task_type=task_type,
        )
        db.add(row)
    row.call_count = int(row.call_count or 0) + 1
    row.total_tokens = int(row.total_tokens or 0) + in_tokens + out_tokens
    row.total_cost_usd = Decimal(str(float(row.total_cost_usd or 0) + cost_usd))


def build_signal_facts(db: Session, signal: Signal) -> dict[str, Any]:
    """Assemble the deterministic layers for one signal. No AI involved."""

    from app.domain.models import Asset, StrategyVersion

    version = db.get(StrategyVersion, signal.strategy_version_id)
    asset = db.get(Asset, signal.asset_id)
    rules = signal.triggered_rules_json or []
    return {
        "kind": "signal",
        "state": signal.state,
        "direction": signal.direction,
        "symbol": asset.symbol if asset else None,
        "timeframe": signal.timeframe,
        "bar_time": signal.bar_timestamp.isoformat() if signal.bar_timestamp else None,
        "price_reference": _num(signal.price_reference),
        "stop_reference": _num(signal.stop_reference),
        "target_reference": _num(signal.target_reference),
        "triggered_rules": rules if isinstance(rules, list) else [],
        "strategy_version": version.version if version else None,
        "data_source": signal.data_source,
    }


def build_backtest_facts(db: Session, run: BacktestRun) -> dict[str, Any]:
    """Assemble stored backtest statistics. No AI involved, no recomputation."""

    summary = dict(run.result.summary_json) if run.result else {}
    trades = [
        {
            "direction": t.direction,
            "entry_price": _num(t.entry_price),
            "exit_price": _num(t.exit_price),
            "pnl": _num(t.pnl),
            "exit_reason": t.exit_reason,
        }
        for t in (run.trades or [])[:20]
    ]
    return {
        "kind": "backtest",
        "status": run.status,
        "engine_version": run.engine_version,
        "feature_version": run.feature_version,
        "dataset_hash": run.dataset_hash,
        "summary": summary,
        "sample_trades": trades,
        "trade_count": len(run.trades or []),
    }


def explain_signal(
    db: Session,
    signal_id: int,
    *,
    router_factory: Callable[[dict[str, OpenAICompatibleProvider], float], AIRouter] | None = None,
) -> dict[str, Any]:
    """Explain a persisted signal, reusing cache and enforcing budget."""

    signal = db.get(Signal, signal_id)
    if signal is None:
        raise LookupError(f"signal {signal_id} not found")

    facts = build_signal_facts(db, signal)
    result = explain_signal_facts(db, facts, state=signal.state, router_factory=router_factory)
    signal.explanation_json = dict(result["explanation"])
    signal.ai_task_id = result["task_id"]
    db.commit()
    return result


def explain_signal_facts(
    db: Session,
    facts: dict[str, Any],
    *,
    state: str = "WAIT",
    router_factory: Callable[[dict[str, OpenAICompatibleProvider], float], AIRouter] | None = None,
) -> dict[str, Any]:
    """Explain pre-assembled signal facts without requiring a Signal row.

    Used by the dashboard preview flow where scan results are ephemeral.
    The AITask row is still persisted for audit and cache purposes.
    """

    configured = get_active_provider(db)
    if configured is None:
        raise RuntimeError(AI_UNCONFIGURED)
    provider, model, api_key = configured

    request = AIRequest(
        task_type="signal_explanation",
        prompt_name="signal_explain",
        prompt_version="1.0.0",
        system_prompt=SIGNAL_SYSTEM_PROMPT,
        user_prompt=f"Explain this {state} signal in plain language.",
        structured_facts=facts,
        schema=SIGNAL_EXPLANATION_SCHEMA,
        model=model.model_name if model else provider.default_model,
    )
    return _run_explain(
        db,
        request,
        provider=provider,
        model=model,
        api_key=api_key,
        router_factory=router_factory,
    )


def explain_backtest(
    db: Session,
    run_id: int,
    *,
    router_factory: Callable[[dict[str, OpenAICompatibleProvider], float], AIRouter] | None = None,
) -> dict[str, Any]:
    """Explain a completed backtest run, reusing cache and enforcing budget."""

    run = db.get(BacktestRun, run_id)
    if run is None:
        raise LookupError(f"backtest run {run_id} not found")
    if run.status != "completed":
        raise ValueError(f"backtest run {run_id} is '{run.status}', not completed")

    configured = get_active_provider(db)
    if configured is None:
        raise RuntimeError(AI_UNCONFIGURED)
    provider, model, api_key = configured

    facts = build_backtest_facts(db, run)
    request = AIRequest(
        task_type="backtest_analysis",
        prompt_name="backtest_explain",
        prompt_version="1.0.0",
        system_prompt=BACKTEST_SYSTEM_PROMPT,
        user_prompt="Explain these backtest results in plain language.",
        structured_facts=facts,
        schema=BACKTEST_EXPLANATION_SCHEMA,
        model=model.model_name if model else provider.default_model,
    )
    return _run_explain(
        db,
        request,
        provider=provider,
        model=model,
        api_key=api_key,
        router_factory=router_factory,
    )


def _run_explain(
    db: Session,
    request: AIRequest,
    *,
    provider: AIProvider,
    model: AIModel | None,
    api_key: str,
    router_factory: Callable[[dict[str, OpenAICompatibleProvider], float], AIRouter] | None,
) -> dict[str, Any]:
    budget = float(provider.daily_budget_usd or 0)
    spent, _ = spent_today_usd(db, provider.id)
    if spent >= budget:
        raise BudgetExceeded(
            f"daily AI budget exhausted ({spent:.4f}/{budget:.2f} USD); "
            "quantitative features keep working"
        )

    input_hash = request.input_hash()
    cached = db.scalar(
        select(AITask).where(
            AITask.input_hash == input_hash,
            AITask.status == "completed",
            AITask.output_json.is_not(None),
        )
    )
    if cached is not None and cached.output_json:
        return {
            "explanation": dict(cached.output_json),
            "cached": True,
            "task_id": cached.id,
            "model": request.model,
            "cost_usd_estimated": 0.0,
        }

    live = OpenAICompatibleProvider(base_url=provider.base_url, api_key=api_key, name=provider.name)
    router = (router_factory or AIRouter)({provider.name: live}, budget)

    task = AITask(
        task_type=request.task_type,
        provider_id=provider.id,
        model_id=model.id if model else None,
        prompt_name=request.prompt_name,
        prompt_version=request.prompt_version,
        input_hash=input_hash,
        input_json={"facts": request.structured_facts},
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
        "model": request.model,
        "cost_usd_estimated": cost,
    }


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
