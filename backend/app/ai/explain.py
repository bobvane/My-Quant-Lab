"""AI explanations for signals and backtests (advisory only).

Hard rules, enforced by construction:

* Every number shown to the user comes from the deterministic engine
  (``build_signal_facts`` / ``build_backtest_facts`` read stored results).
  The model receives them as *input* and may only restate them in words.
* The output schema contains no numeric fact fields, so there is nowhere for
  an invented number to hide.
* The prompt is no longer a string in this module: it is assembled from the
  System Contract and the EXPLAINER role contract shipped under
  ``backend/app/ai/contracts/`` (ADR-150), which is also where the prompt version
  comes from.
* The call itself runs through ``app.ai.runtime`` (ADR-153), so the cache key,
  the three-level budget guard and the audit trail are shared with every other
  AI task instead of being re-implemented here. Identical facts still reuse the
  stored explanation for free; switching provider, model or contract no longer
  reuses an older answer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.budget import record_usage, spent_today_usd
from app.ai.explanation_guard import check_explanation
from app.ai.provider import (
    SIGNAL_EXPLANATION_SCHEMA,
    AIRequest,
    AIRouter,
    OpenAICompatibleProvider,
)
from app.ai.role_contracts import contract_for_role, system_contract
from app.ai.runtime import estimate_tokens, run_task
from app.data.backtest_service import analysis_for_run
from app.domain.models import AIModel, AIProvider, BacktestRun, Signal, StrategyVersion
from app.infrastructure.secrets import decrypt_secret

logger = logging.getLogger(__name__)

__all__ = [
    "AI_UNCONFIGURED",
    "BACKTEST_EXPLANATION_SCHEMA",
    "EXPLAINER_ROLE",
    "PERFORMANCE_EXPLANATION_SCHEMA",
    "ExplanationRejected",
    "SIGNAL_EXPLANATION_SCHEMA",
    "build_backtest_facts",
    "build_performance_facts",
    "build_signal_facts",
    "estimate_tokens",
    "explain_backtest",
    "explain_performance",
    "explain_signal",
    "explain_signal_facts",
    "explainer_prompt",
    "get_active_provider",
    "record_usage",
    "spent_today_usd",
]

AI_UNCONFIGURED = "ai_not_configured"

EXPLAINER_ROLE = "EXPLAINER"

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

#: Phase C explanation (docs/30 §8). Like the schema above it has no numeric
#: field, because every figure it may mention is already in the facts.
PERFORMANCE_EXPLANATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "conclusion",
        "drivers",
        "risks",
        "confidence",
        "next_step",
    ],
    "properties": {
        "conclusion": {"type": "string"},
        "drivers": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string"},
        "next_step": {"type": "string"},
    },
}


class ExplanationRejected(RuntimeError):
    """The explanation claimed something the analysis does not support.

    Raised instead of returning text that carries a number nobody computed or a
    promise about the future. The caller shows the numbers alone; the task row
    keeps the model's words for audit (ADR-189).
    """

    def __init__(self, violations: list[dict[str, str]]) -> None:
        self.violations = violations
        detail = ", ".join(f"{item['code']}: {item['detail']}" for item in violations)
        super().__init__(f"explanation rejected: {detail}")


def explainer_prompt(task_type: str) -> tuple[str, str, str, str]:
    """Prompt material for one explainer task, read from the contract files.

    Returns ``(system_prompt, prompt_name, prompt_version, prompt_hash)``. The
    system prompt is the *System Contract* followed by this role's section for
    the task, in that order, so the rules that treat outside text as data cannot
    be displaced by a role file.
    """

    system = system_contract()
    contract = contract_for_role(EXPLAINER_ROLE)
    return (
        f"{system.body}\n\n{contract.system_prompt_for(task_type)}",
        contract.prompt_name_for(task_type),
        contract.version,
        f"{system.content_hash}:{contract.content_hash}",
    )


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


def get_active_providers(db: Session) -> list[tuple[AIProvider, str, list[AIModel]]]:
    """All routable providers: ``(provider, decrypted_key, active_models)``.

    Used to build the multi-provider routing catalog (ADR-014). Providers whose
    stored key cannot be decrypted are skipped; the API key is never surfaced.
    """

    rows = db.scalars(
        select(AIProvider)
        .where(AIProvider.is_active.is_(True), AIProvider.api_key_encrypted.is_not(None))
        .order_by(AIProvider.id)
    ).all()
    out: list[tuple[AIProvider, str, list[AIModel]]] = []
    for provider in rows:
        try:
            api_key = decrypt_secret(provider.api_key_encrypted or "")
        except Exception:
            logger.warning("stored AI key failed to decrypt for %s", provider.name, exc_info=True)
            continue
        if not api_key:
            continue
        models = list(
            db.scalars(
                select(AIModel)
                .where(AIModel.provider_id == provider.id, AIModel.is_active.is_(True))
                .order_by(AIModel.id)
            ).all()
        )
        out.append((provider, api_key, models))
    return out


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

    providers = get_active_providers(db)
    if not providers:
        raise RuntimeError(AI_UNCONFIGURED)

    system_prompt, prompt_name, prompt_version, prompt_hash = explainer_prompt("signal_explanation")
    request = AIRequest(
        task_type="signal_explanation",
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        role=EXPLAINER_ROLE,
        system_prompt=system_prompt,
        user_prompt=f"Explain this {state} signal in plain language.",
        structured_facts=facts,
        schema=SIGNAL_EXPLANATION_SCHEMA,
        model=None,  # the router picks provider/model by capability + cost
    )
    return run_task(
        db,
        request,
        providers=providers,
        router_factory=router_factory,
        prompt_hash=prompt_hash,
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

    providers = get_active_providers(db)
    if not providers:
        raise RuntimeError(AI_UNCONFIGURED)

    system_prompt, prompt_name, prompt_version, prompt_hash = explainer_prompt("backtest_analysis")
    facts = build_backtest_facts(db, run)
    request = AIRequest(
        task_type="backtest_analysis",
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        role=EXPLAINER_ROLE,
        system_prompt=system_prompt,
        user_prompt="Explain these backtest results in plain language.",
        structured_facts=facts,
        schema=BACKTEST_EXPLANATION_SCHEMA,
        model=None,  # the router picks provider/model by capability + cost
    )
    return run_task(
        db,
        request,
        providers=providers,
        router_factory=router_factory,
        prompt_hash=prompt_hash,
    )


def build_performance_facts(
    db: Session, run: BacktestRun, analysis: dict[str, Any]
) -> dict[str, Any]:
    """The facts an explainer may use: the stored analysis, and nothing else.

    The blocks are passed through unchanged so the numbers the model is allowed
    to mention are exactly the numbers the page shows. ``result_hash`` is left
    out on purpose: its hex digits are not a figure, and feeding them to the
    number check would only widen what counts as "supported".
    """

    version = db.get(StrategyVersion, run.strategy_version_id)
    series = run.dataset
    asset = series.asset if series is not None else None
    window = analysis.get("window") or {}
    benchmark = analysis.get("benchmark")
    if isinstance(benchmark, dict):
        # The comparison curve is a chart, not a fact: sending hundreds of equity
        # points would bury the figures the model is supposed to restate, and the
        # design keeps raw curves out of the fact pack (§8.2).
        benchmark = {key: value for key, value in benchmark.items() if key != "curve"}
    return {
        "kind": "performance_analysis",
        "run_id": run.id,
        "strategy": {
            "name": version.strategy.name if version is not None and version.strategy else None,
            "version": version.version if version is not None else None,
            "symbol": asset.symbol if asset is not None else None,
            "asset_class": asset.asset_class if asset is not None else None,
            "timeframe": series.timeframe if series is not None else None,
            "window_start": window.get("start"),
            "window_end": window.get("end"),
            "bars": window.get("bars"),
        },
        "performance": analysis.get("performance"),
        "risk": analysis.get("risk"),
        "benchmark": benchmark,
        "sample": analysis.get("sample"),
        "caveats": analysis.get("caveats"),
        "notes": [
            "Every figure above was computed by the deterministic engine.",
            "The buy-and-hold comparison excludes fees and slippage.",
            "Past results do not predict future returns.",
        ],
    }


def explain_performance(
    db: Session,
    run_id: int,
    *,
    analysis: dict[str, Any] | None = None,
    router_factory: Callable[[dict[str, OpenAICompatibleProvider], float], AIRouter] | None = None,
) -> dict[str, Any]:
    """Explain the Phase C analysis of a completed run, cache and budget shared.

    ``analysis`` may be handed in by a caller that already computed it (the
    analysis endpoint does), otherwise it is assembled once here. An explanation
    that fails the numeric-admission or prediction check is refused outright.
    """

    run = db.get(BacktestRun, run_id)
    if run is None:
        raise LookupError(f"backtest run {run_id} not found")
    if run.status != "completed" or run.result is None:
        raise ValueError(f"backtest run {run_id} is '{run.status}', not completed")

    providers = get_active_providers(db)
    if not providers:
        raise RuntimeError(AI_UNCONFIGURED)

    stored_analysis = analysis if analysis is not None else analysis_for_run(db, run)
    facts = build_performance_facts(db, run, stored_analysis)
    system_prompt, prompt_name, prompt_version, prompt_hash = explainer_prompt(
        "performance_explanation"
    )
    request = AIRequest(
        task_type="performance_explanation",
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        role=EXPLAINER_ROLE,
        system_prompt=system_prompt,
        user_prompt=(
            "Explain this run's performance, risk and buy-and-hold comparison in plain language."
        ),
        structured_facts=facts,
        schema=PERFORMANCE_EXPLANATION_SCHEMA,
        model=None,  # the router picks provider/model by capability + cost
    )
    result = run_task(
        db,
        request,
        providers=providers,
        router_factory=router_factory,
        prompt_hash=prompt_hash,
    )
    violations = check_explanation(result["explanation"], facts)
    if violations:
        raise ExplanationRejected(violations)
    return result


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
