"""Capability registry: what this system can actually compute (ADR-151).

An AI research role may only propose what the deterministic engine can execute.
Rather than asking a model to remember that list, the list is derived from the
code that implements it — the DSL enums, the feature engine, the metrics
dataclass, the data providers — and a test checks the two against each other
(``backend/tests/test_capabilities.py``). Anything the system cannot do is
listed with a reason so a caller can report ``UNSUPPORTED`` instead of silently
building a strategy that cannot be executed.

``assess()`` is the guard: give it the capability tokens a proposed strategy
needs and it answers ``SUPPORTED`` / ``PARTIALLY_SUPPORTED`` / ``UNSUPPORTED``
with the missing pieces and why they are missing.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from app.data.providers import PROVIDER_NAMES
from app.features.catalogue import FEATURE_CATALOGUE
from app.features.engine import SUPPORTED_INDICATOR_TYPES
from app.research.analysis import BENCHMARK_KINDS, DERIVED_METRICS
from app.research.metrics import BARRS_PER_YEAR, Metrics
from app.strategies.dsl import (
    ComparisonOp,
    ExecutionSpec,
    FillModel,
    MarketSpec,
    OrderType,
    RiskSpec,
    SizingMode,
)

__all__ = [
    "CAPABILITY_STATUSES",
    "MODEL_CAPABILITIES",
    "UNSUPPORTED_CAPABILITIES",
    "CapabilityGroup",
    "CapabilityReport",
    "assess",
    "capability_payload",
    "supported_tokens",
]

CapabilityStatus = Literal["SUPPORTED", "PARTIALLY_SUPPORTED", "UNSUPPORTED"]

#: The three answers a capability check may give.
CAPABILITY_STATUSES: tuple[str, ...] = ("SUPPORTED", "PARTIALLY_SUPPORTED", "UNSUPPORTED")

#: Interface-level capabilities of a *model* (not of this system). A role
#: contract states the minimum it needs; a model profile states what it has.
#: Nothing here says which model is smarter — only which interfaces it exposes.
MODEL_CAPABILITIES: tuple[str, ...] = (
    "structured_output",
    "tool_calling",
    "long_context",
    "vision",
    "reasoning",
    "code_understanding",
    "web_research",
)


@dataclass(frozen=True)
class CapabilityGroup:
    """One dimension of the registry, with where its truth comes from."""

    key: str
    label: str
    source: str
    items: tuple[str, ...]
    note: str = ""


def _module_items(root: str, names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"{root}/{name}.py" for name in names)


_ENGINE_MODULES: tuple[str, ...] = (
    "research/engine",
    "research/analysis",
    "research/walk_forward",
    "research/sensitivity",
    "research/monte_carlo",
    "research/ensemble",
    "simulation/signal_engine",
    "simulation/paper_engine",
)


def _feature_items(family: str) -> tuple[str, ...]:
    return tuple(spec.name for spec in FEATURE_CATALOGUE if spec.family == family)


#: Every group is derived from code; ``source`` names the file that decides it.
GROUPS: tuple[CapabilityGroup, ...] = (
    CapabilityGroup(
        key="operators",
        label="Comparison operators",
        source="backend/app/strategies/dsl.py:63",
        items=tuple(get_args(ComparisonOp)),
    ),
    CapabilityGroup(
        key="indicators",
        label="Indicators the feature engine materialises",
        source="backend/app/features/engine.py SUPPORTED_INDICATOR_TYPES",
        items=SUPPORTED_INDICATOR_TYPES,
        note=(
            "BB and BOLLINGER_BANDS are accepted spellings of BOLLINGER, not "
            "separate indicators (INDICATOR_ALIASES)."
        ),
    ),
    CapabilityGroup(
        key="features",
        label="Indicator columns the feature engine produces",
        source="backend/app/features/catalogue.py FEATURE_CATALOGUE",
        items=_feature_items("indicator"),
    ),
    CapabilityGroup(
        key="price_action_features",
        label="Price-action features the feature engine produces",
        source="backend/app/features/catalogue.py FEATURE_CATALOGUE",
        items=_feature_items("price_action"),
    ),
    CapabilityGroup(
        key="fill_models",
        label="Fill models",
        source="backend/app/strategies/dsl.py:64",
        items=tuple(get_args(FillModel)),
    ),
    CapabilityGroup(
        key="entry_order_types",
        label="Entry order types",
        source="backend/app/strategies/dsl.py:186",
        items=tuple(get_args(OrderType)),
    ),
    CapabilityGroup(
        key="sizing_modes",
        label="Position sizing modes",
        source="backend/app/strategies/dsl.py:188",
        items=tuple(get_args(SizingMode)),
    ),
    CapabilityGroup(
        key="risk_models",
        label="Risk controls",
        source="backend/app/strategies/dsl.py:139-142 (RiskSpec)",
        items=tuple(RiskSpec.model_fields),
    ),
    CapabilityGroup(
        key="execution_fields",
        label="Execution assumptions the DSL can express",
        source="backend/app/strategies/dsl.py:215-234 (ExecutionSpec)",
        items=tuple(ExecutionSpec.model_fields),
    ),
    CapabilityGroup(
        key="market_fields",
        label="Market declarations the DSL can express",
        source="backend/app/strategies/dsl.py:237-242 (MarketSpec)",
        items=tuple(MarketSpec.model_fields),
        note=(
            "asset_classes and timeframes are free strings in the DSL; which "
            "symbols and intervals actually return bars is decided by the data "
            "provider, not by the schema."
        ),
    ),
    CapabilityGroup(
        key="metrics",
        label="Performance metrics the backtest engine computes",
        source="backend/app/research/metrics.py:34-53 (Metrics)",
        items=tuple(
            f.name
            for f in dataclasses.fields(Metrics)
            if f.name not in {"notes", "initial_capital"}
        ),
    ),
    CapabilityGroup(
        key="analysis_metrics",
        label="Performance and risk metrics the analysis layer derives",
        source="backend/app/research/analysis.py DERIVED_METRICS",
        items=DERIVED_METRICS,
        note=(
            "Computed on demand from the stored equity curve and trades by a pure "
            "function; the engine's own stored metrics are the 'metrics' group."
        ),
    ),
    CapabilityGroup(
        key="comparisons",
        label="Comparison series the analysis layer can build",
        source="backend/app/research/analysis.py BENCHMARK_KINDS",
        items=BENCHMARK_KINDS,
        note="Buy and hold is derived from the closes the stored equity curve carries.",
    ),
    CapabilityGroup(
        key="timeframe_annualisation",
        label="Timeframes with an annualisation factor",
        source="backend/app/research/metrics.py:18-26 (BARRS_PER_YEAR)",
        items=tuple(BARRS_PER_YEAR),
        note="A timeframe with no factor cannot be annualised (CAGR/Sharpe come back N/A).",
    ),
    CapabilityGroup(
        key="data_providers",
        label="Market data providers",
        source="backend/app/data/providers.py:454 (PROVIDER_NAMES)",
        items=PROVIDER_NAMES,
        note="Only the provider named by MARKET_DATA_PROVIDER is active at a time.",
    ),
    CapabilityGroup(
        key="analysis_engines",
        label="Deterministic analysis engines",
        source="backend/app/",
        items=_module_items("backend/app", _ENGINE_MODULES),
        note="Each entry is a module that exists in this repository; none of them calls a model.",
    ),
)


@dataclass(frozen=True)
class UnsupportedCapability:
    """Something a research question may ask for, and why we cannot do it."""

    token: str
    label: str
    reason: str
    partial: bool = False


#: Known gaps, in the vocabulary a research request would use. ``partial`` means
#: the system can express part of it, which reports PARTIALLY_SUPPORTED.
UNSUPPORTED_CAPABILITIES: tuple[UnsupportedCapability, ...] = (
    UnsupportedCapability(
        token="short_selling",
        label="Short selling",
        reason=(
            "the DSL can declare allow_short and the backtest engine can take short "
            "entries, but paper trading is long-only (one position); a short strategy "
            "cannot be paper-verified end to end"
        ),
        partial=True,
    ),
    UnsupportedCapability(
        token="cross_sectional_universe",
        label="Universe / cross-sectional ranking",
        reason=(
            "a StrategySpec describes one symbol at a time; there is no ranking, "
            "screening or universe selection step in the engine"
        ),
    ),
    UnsupportedCapability(
        token="portfolio_rules",
        label="Portfolio construction",
        reason="no portfolio weighting, rebalancing or multi-asset allocation exists",
    ),
    UnsupportedCapability(
        token="leverage",
        label="Leverage and margin",
        reason="position sizing is cash-constrained; leverage and margin are not modelled",
    ),
    UnsupportedCapability(
        token="market_microstructure",
        label="T+1, price limits, lot sizes, trading calendar",
        reason=(
            "execution is next-bar-open or close-bar with fees and slippage only; "
            "session rules of a specific exchange are not modelled"
        ),
    ),
    UnsupportedCapability(
        token="var_cvar",
        label="VaR / CVaR",
        reason=(
            "the analysis layer stops at volatility, downside deviation, drawdown, "
            "recovery and Calmar; quantile loss estimates are not computed"
        ),
    ),
    UnsupportedCapability(
        token="fundamentals",
        label="Fundamental data",
        reason="no fundamental data source is connected (price bars only)",
    ),
    UnsupportedCapability(
        token="news",
        label="News and sentiment feeds",
        reason="no news feed is connected",
    ),
    UnsupportedCapability(
        token="vision",
        label="Chart or screenshot understanding",
        reason="planned research sources are text, URL, GitHub and PDF; images are not parsed",
    ),
    UnsupportedCapability(
        token="rag",
        label="Vector search / RAG",
        reason="not planned for the first phase; sources become structured context instead",
    ),
    UnsupportedCapability(
        token="live_execution",
        label="Live broker execution",
        reason="no broker endpoint exists by design; this project never places orders",
    ),
)


@dataclass(frozen=True)
class CapabilityReport:
    """The guard's answer for one set of requested capabilities."""

    status: str
    requested: tuple[str, ...]
    supported: tuple[str, ...]
    missing: tuple[str, ...]
    reasons: dict[str, str] = field(default_factory=dict)
    partial: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "requested": list(self.requested),
            "supported": list(self.supported),
            "missing": list(self.missing),
            "partial": list(self.partial),
            "reasons": dict(self.reasons),
        }


def _normalise(token: str) -> str:
    return str(token).strip().lower().replace(" ", "_")


def supported_tokens() -> dict[str, str]:
    """``token -> group key`` for every supported group and item name."""

    index: dict[str, str] = {}
    for group in GROUPS:
        index[_normalise(group.key)] = group.key
        for item in group.items:
            index.setdefault(_normalise(item), group.key)
    return index


_UNSUPPORTED_INDEX: dict[str, UnsupportedCapability] = {
    _normalise(entry.token): entry for entry in UNSUPPORTED_CAPABILITIES
}


def assess(requested: Iterable[str]) -> CapabilityReport:
    """Check a set of capability tokens against what the engine can do.

    Tokens match a group name (``metrics``), an item name (``crosses_above``,
    ``macd``), an ``group:item`` pair, or one of the known gaps. Unknown tokens
    are reported as missing rather than guessed at.
    """

    wanted = tuple(_normalise(token) for token in requested if str(token).strip())
    supported: list[str] = []
    missing: list[str] = []
    partial: list[str] = []
    reasons: dict[str, str] = {}
    index = supported_tokens()

    for token in wanted:
        bare = token.split(":", 1)[1] if ":" in token else token
        gap = _UNSUPPORTED_INDEX.get(bare)
        if gap is not None:
            if gap.partial:
                partial.append(token)
            else:
                missing.append(token)
            reasons[token] = gap.reason
            continue
        if bare in index:
            supported.append(token)
            continue
        reasons[token] = (
            "not in this system's capability registry; if it is a real capability, "
            "the registry (and the engine behind it) has to be extended first"
        )
        missing.append(token)

    if missing and not supported and not partial:
        status = "UNSUPPORTED"
    elif missing or partial:
        status = "PARTIALLY_SUPPORTED"
    else:
        status = "SUPPORTED"
    return CapabilityReport(
        status=status,
        requested=wanted,
        supported=tuple(supported),
        missing=tuple(missing),
        reasons=reasons,
        partial=tuple(partial),
    )


def capability_payload() -> dict[str, Any]:
    """The whole registry, ready to be returned by the API or fed to a role."""

    return {
        "statuses": list(CAPABILITY_STATUSES),
        "model_capabilities": list(MODEL_CAPABILITIES),
        "groups": [
            {
                "key": group.key,
                "label": group.label,
                "source": group.source,
                "items": list(group.items),
                "note": group.note,
            }
            for group in GROUPS
        ],
        "unsupported": [
            {
                "token": entry.token,
                "label": entry.label,
                "reason": entry.reason,
                "partial": entry.partial,
            }
            for entry in UNSUPPORTED_CAPABILITIES
        ],
    }
