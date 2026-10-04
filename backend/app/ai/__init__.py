"""AI layer: advisory interpretation and research assistance.

The package exposes the pieces that other layers are allowed to use. Prompts live
in ``contracts/`` as files, not as string constants (ADR-150), and every call goes
through ``runtime.run_task`` so cache, budget and audit behave the same way for
explanation today and for research tasks later (ADR-153).
"""

from app.ai.budget import BudgetDecision, decide, record_usage, spent_today_usd
from app.ai.explain import (
    BACKTEST_EXPLANATION_SCHEMA,
    explain_backtest,
    explain_signal,
    explain_signal_facts,
    explainer_prompt,
    get_active_provider,
)
from app.ai.provider import (
    SIGNAL_EXPLANATION_SCHEMA,
    AIRequest,
    AIRouter,
    BudgetExceeded,
    OpenAICompatibleProvider,
    UntrustedSource,
    assemble_messages,
    validate_structured_dict,
    validate_structured_output,
    wrap_untrusted,
)
from app.ai.role_contracts import (
    RoleContract,
    contract_for_role,
    load_contracts,
    role_contracts,
    system_contract,
)
from app.ai.runtime import audit_payload, cache_key, output_hash, run_task

__all__ = [
    "AIRequest",
    "AIRouter",
    "BACKTEST_EXPLANATION_SCHEMA",
    "BudgetDecision",
    "BudgetExceeded",
    "OpenAICompatibleProvider",
    "RoleContract",
    "SIGNAL_EXPLANATION_SCHEMA",
    "UntrustedSource",
    "assemble_messages",
    "audit_payload",
    "cache_key",
    "contract_for_role",
    "decide",
    "explain_backtest",
    "explain_signal",
    "explain_signal_facts",
    "explainer_prompt",
    "get_active_provider",
    "load_contracts",
    "output_hash",
    "record_usage",
    "role_contracts",
    "run_task",
    "spent_today_usd",
    "system_contract",
    "validate_structured_dict",
    "validate_structured_output",
    "wrap_untrusted",
]
