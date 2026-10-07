"""AI budget: one decision chain, three levels (ADR-152).

``AI_DAILY_BUDGET_USD`` is the system-wide ceiling for a UTC day; each provider
carries its own ``daily_budget_usd``; a single research run may carry a task cap.
The three are checked in one place, in one order, and every refusal names the
level that refused — an operator reading a 429 should not have to guess which
knob was exhausted.

Quantitative features never consult this module: running out of AI budget pauses
explanations, nothing else.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings

__all__ = [
    "BudgetDecision",
    "decide",
    "guard",
    "record_usage",
    "spent_for_provider_usd",
    "spent_today_usd",
    "spent_today_usd_all",
]


@dataclass(frozen=True)
class BudgetDecision:
    """The answer of the budget chain, with the level that produced it."""

    allowed: bool
    scope: str
    reason: str
    limit_usd: float
    spent_usd: float
    estimated_usd: float
    remaining_usd: float

    def as_dict(self) -> dict[str, float | str | bool]:
        return {
            "allowed": self.allowed,
            "scope": self.scope,
            "reason": self.reason,
            "limit_usd": self.limit_usd,
            "spent_usd": self.spent_usd,
            "estimated_usd": self.estimated_usd,
            "remaining_usd": self.remaining_usd,
        }


def _blocked(
    scope: str, reason: str, limit: float, spent: float, estimated: float
) -> BudgetDecision:
    return BudgetDecision(
        allowed=False,
        scope=scope,
        reason=reason,
        limit_usd=limit,
        spent_usd=spent,
        estimated_usd=estimated,
        remaining_usd=0.0,
    )


def decide(
    *,
    global_limit_usd: float,
    global_spent_usd: float,
    provider_limit_usd: float,
    provider_spent_usd: float,
    estimated_usd: float = 0.0,
    task_limit_usd: float | None = None,
    calls_today: int | None = None,
    daily_call_limit: int | None = None,
) -> BudgetDecision:
    """Check one request against the global cap, the provider cap and its own cap.

    A limit of ``0`` blocks everything (the same meaning ``daily_budget_usd = 0``
    has always had: this provider may not be called). The order is global →
    provider → task cap → this request's cost → daily call count, so the reason
    names the outermost limit that is exhausted.
    """

    remaining = min(
        max(0.0, global_limit_usd - global_spent_usd),
        max(0.0, provider_limit_usd - provider_spent_usd),
    )
    if global_spent_usd >= global_limit_usd:
        return _blocked(
            "global",
            (
                f"AI daily budget exhausted ({global_spent_usd:.4f}/{global_limit_usd:.2f} USD, "
                "AI_DAILY_BUDGET_USD); quantitative features keep working"
            ),
            global_limit_usd,
            global_spent_usd,
            estimated_usd,
        )
    if provider_spent_usd >= provider_limit_usd:
        return _blocked(
            "provider",
            (
                f"provider AI budget exhausted ({provider_spent_usd:.4f}/"
                f"{provider_limit_usd:.2f} USD); quantitative features keep working"
            ),
            provider_limit_usd,
            provider_spent_usd,
            estimated_usd,
        )
    if task_limit_usd is not None and estimated_usd > task_limit_usd:
        return _blocked(
            "task",
            (
                f"this request could cost up to {estimated_usd:.4f} USD, above the "
                f"single-run cap of {task_limit_usd:.2f} USD"
            ),
            task_limit_usd,
            provider_spent_usd,
            estimated_usd,
        )
    if global_spent_usd + estimated_usd > global_limit_usd:
        return _blocked(
            "global",
            (
                f"this request could cost up to {estimated_usd:.4f} USD and only "
                f"{global_limit_usd - global_spent_usd:.4f} USD of the daily budget is left"
            ),
            global_limit_usd,
            global_spent_usd,
            estimated_usd,
        )
    if provider_spent_usd + estimated_usd > provider_limit_usd:
        return _blocked(
            "provider",
            (
                f"this request could cost up to {estimated_usd:.4f} USD and only "
                f"{provider_limit_usd - provider_spent_usd:.4f} USD of this provider's "
                "budget is left"
            ),
            provider_limit_usd,
            provider_spent_usd,
            estimated_usd,
        )
    if daily_call_limit is not None and calls_today is not None and calls_today >= daily_call_limit:
        return _blocked(
            "calls",
            f"daily AI task limit reached ({calls_today}/{daily_call_limit})",
            float(daily_call_limit),
            float(calls_today),
            estimated_usd,
        )
    return BudgetDecision(
        allowed=True,
        scope="none",
        reason="within budget",
        limit_usd=min(global_limit_usd, provider_limit_usd),
        spent_usd=max(global_spent_usd, provider_spent_usd),
        estimated_usd=estimated_usd,
        remaining_usd=remaining,
    )


def _usage_rows(db: Session, provider_id: int, *, today: dt.date) -> list[Any]:
    """Today's ``ai_usage`` rows for one provider."""

    from app.domain.models import AIUsage

    stmt = select(AIUsage).where(AIUsage.usage_date == today, AIUsage.provider_id == provider_id)
    return list(db.scalars(stmt).all())


def spent_today_usd(db: Session, provider_id: int) -> tuple[float, int]:
    """``(cost, calls)`` recorded for this provider since UTC midnight."""

    today = dt.datetime.now(tz=dt.UTC).date()
    rows = _usage_rows(db, provider_id, today=today)
    return (
        sum(float(row.total_cost_usd or 0) for row in rows),
        sum(int(row.call_count or 0) for row in rows),
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
    provider_name: str | None = None,
    model_name: str | None = None,
) -> None:
    """Add one call to today's counter for ``(provider, model, task)``.

    The provider/model names are stored alongside the ids so the row stays
    readable after that configuration is deleted (ADR-177).
    """

    from app.domain.models import AIUsage

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
    if provider_name and not row.provider_name:
        row.provider_name = provider_name
    if model_name and not row.model_name:
        row.model_name = model_name
    row.call_count = int(row.call_count or 0) + 1
    row.total_tokens = int(row.total_tokens or 0) + in_tokens + out_tokens
    row.total_cost_usd = Decimal(str(float(row.total_cost_usd or 0) + cost_usd))


def spent_today_usd_all(db: Session) -> float:
    """Everything spent today (UTC), across every provider."""

    from app.domain.models import AIUsage

    today = dt.datetime.now(tz=dt.UTC).date()
    rows = db.scalars(select(AIUsage).where(AIUsage.usage_date == today)).all()
    return sum(float(row.total_cost_usd or 0) for row in rows)


def spent_for_provider_usd(db: Session, provider_id: int) -> float:
    """Everything spent today (UTC) on one provider."""

    return spent_today_usd(db, provider_id)[0]


def guard(
    db: Session,
    *,
    provider_limit_usd: float,
    provider_spent_usd: float,
    estimated_usd: float = 0.0,
    task_limit_usd: float | None = None,
    calls_today: int | None = None,
    daily_call_limit: int | None = None,
) -> BudgetDecision:
    """Run the chain with the deployment's system-wide cap from settings."""

    return decide(
        global_limit_usd=float(settings.ai_daily_budget_usd or 0.0),
        global_spent_usd=spent_today_usd_all(db),
        provider_limit_usd=provider_limit_usd,
        provider_spent_usd=provider_spent_usd,
        estimated_usd=estimated_usd,
        task_limit_usd=task_limit_usd,
        calls_today=calls_today,
        daily_call_limit=daily_call_limit,
    )
