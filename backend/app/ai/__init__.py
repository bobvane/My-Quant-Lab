"""AI layer: advisory interpretation and research assistance."""

from app.ai.explain import (
    BACKTEST_EXPLANATION_SCHEMA,
    explain_backtest,
    explain_signal,
    explain_signal_facts,
    get_active_provider,
    spent_today_usd,
)
from app.ai.provider import (
    SIGNAL_EXPLANATION_SCHEMA,
    AIRequest,
    AIRouter,
    BudgetExceeded,
    OpenAICompatibleProvider,
    validate_structured_dict,
    validate_structured_output,
)

__all__ = [
    "AIRequest",
    "AIRouter",
    "BACKTEST_EXPLANATION_SCHEMA",
    "BudgetExceeded",
    "OpenAICompatibleProvider",
    "SIGNAL_EXPLANATION_SCHEMA",
    "explain_backtest",
    "explain_signal",
    "explain_signal_facts",
    "get_active_provider",
    "spent_today_usd",
    "validate_structured_dict",
    "validate_structured_output",
]
