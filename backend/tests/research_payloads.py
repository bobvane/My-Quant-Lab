"""Canned model answers and a scripted router for the research-layer tests.

This module holds no tests. It exists so the researcher, architect, capability
and security test files can share one set of answers instead of triplicating
them. The router has the same shape as ``AIRouter.explain_signal`` and answers
from a list, so every test that uses it stays offline.
"""

from __future__ import annotations

from typing import Any

from app.ai import research as service
from app.ai.provider import AIRequest
from app.domain.models import AIProvider
from app.infrastructure.secrets import encrypt_secret

QUESTION = "Martin 说这个策略在 BTC 超跌之后反弹的时候买入。"
NOTE = "Martin：BTC 超跌之后反弹的时候买入。就这一句，没有别的了。"
MARTIN = "note-martin"

MOMENTUM_QUESTION = "用 20 日动量做一个横截面排名策略，每月调仓买排名前 10%。"
MOMENTUM_NOTE = "用户：在全部标的上按 20 日动量排名，每月调仓，买入排名前 10%。"
MOMENTUM = "note-momentum"


class ScriptedRouter:
    """Same shape as ``AIRouter.explain_signal``, answers from a script."""

    def __init__(self, outputs: list[Any]) -> None:
        self._outputs = list(outputs)
        self.calls: list[AIRequest] = []

    def explain_signal(self, request: AIRequest, *, spent_today_usd: float = 0.0) -> dict:
        self.calls.append(request)
        if not self._outputs:
            raise AssertionError("the pipeline asked for more answers than the script holds")
        answer = self._outputs.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def script(outputs: list[Any]):
    """``(router, router_factory)`` for :func:`service.start_research`."""

    router = ScriptedRouter(outputs)

    def factory(providers: dict, budget: float) -> ScriptedRouter:
        return router

    return router, factory


def make_provider(db_session) -> AIProvider:
    """An active provider row; the scripted router means nothing dials out."""

    row = AIProvider(
        name="test-openai",
        provider_type="openai_compatible",
        base_url="http://localhost:9",
        api_key_encrypted=encrypt_secret("sk-test"),
        default_model="test-model",
        daily_budget_usd=2.0,
    )
    db_session.add(row)
    db_session.flush()
    return row


def variant(question: str, tag: str) -> str:
    """A distinct question, so a second run is a fresh request.

    The runtime answers from cache when the inputs are identical (ADR-153), so
    two runs that ask the same question about the same material share one model
    answer. A test that wants a *different* answer has to ask something else.
    """

    return f"{question}（{tag}）"


def note_inputs(source_ref: str = MARTIN, text: str = NOTE) -> list[service.ResearchInput]:
    return [service.ResearchInput(text=text, source_ref=source_ref, label="Martin 的一段描述")]


def run_research(db_session, provider, outputs, **overrides):
    """One whole run against a script, committed so the API tests can reuse it."""

    router, factory = script(outputs)
    run = service.start_research(
        db_session,
        question=overrides.pop("question", QUESTION),
        inputs=overrides.pop("inputs", note_inputs()),
        providers=[(provider, "sk-test", [])],
        router_factory=factory,
        **overrides,
    )
    db_session.commit()
    return run, router


class RecordingProvider:
    """A provider that keeps the messages the runtime really assembled.

    :class:`ScriptedRouter` stands in for ``AIRouter``, so it never sees the
    messages. This one sits *below* the real router: ``AIRouter.explain_signal``
    still calls ``assemble_messages``, and the provider records what came out of
    it. That is what the trust-boundary tests need to look at.
    """

    queue: list[Any] = []
    instances: list[RecordingProvider] = []

    def __init__(
        self, base_url: str, api_key: str, name: str = "openai_compatible", *, timeout: float = 60.0
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.name = name
        self.timeout = timeout
        self.messages: list[list[dict]] = []
        RecordingProvider.instances.append(self)

    def structured_output(self, messages, *, model: str = "", schema=None) -> dict:
        self.messages.append(list(messages))
        if not RecordingProvider.queue:
            raise AssertionError("the pipeline asked for more answers than the script holds")
        answer = RecordingProvider.queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def run_research_through_the_router(db_session, provider, outputs, monkeypatch, **overrides):
    """One whole run with the real ``AIRouter`` and a recording provider below it."""

    from app.ai import runtime as runtime_module

    RecordingProvider.queue = list(outputs)
    RecordingProvider.instances = []
    monkeypatch.setattr(runtime_module, "OpenAICompatibleProvider", RecordingProvider)
    run = service.start_research(
        db_session,
        question=overrides.pop("question", QUESTION),
        inputs=overrides.pop("inputs", note_inputs()),
        providers=[(provider, "sk-test", [])],
        router_factory=None,
        **overrides,
    )
    db_session.commit()
    return run


def recorded_messages() -> list[list[dict]]:
    """Every message list the runtime assembled, in call order.

    ``_catalogue`` builds one provider per ``run_task`` call, so the instances
    hold one request each; the researcher step comes first.
    """

    return [messages for provider in RecordingProvider.instances for messages in provider.messages]


def hypothesis_payload(source_ref: str = MARTIN) -> dict[str, Any]:
    """What a well-behaved RESEARCHER answers for the Martin question."""

    return {
        "strategy_name": "BTC oversold rebound",
        "understanding": "Martin 想在一次超跌之后的反弹里买入 BTC，其余都没有说明。",
        "objective": "参与超跌之后的反弹",
        "market": ["crypto"],
        "asset_class": "crypto",
        "universe": "BTC",
        "rules": [
            {
                "id": "r-market",
                "field": "market",
                "statement": "标的为 BTC。",
                "origin": "EXPLICIT",
                "confidence": "high",
                "evidence": [{"source_ref": source_ref, "quote": "BTC"}],
            },
            {
                "id": "r-entry",
                "field": "entry",
                "statement": "在超跌之后的反弹里买入。",
                "origin": "EXPLICIT",
                "confidence": "medium",
                "evidence": [{"source_ref": source_ref, "quote": "超跌之后反弹的时候买入"}],
            },
            {
                "id": "r-oversold",
                "field": "indicator",
                "statement": "为了让想法可测试，把“超跌”定义为 RSI(14) < 30。",
                "origin": "ASSUMED",
                "confidence": "low",
                "parameters": {"indicator": "RSI", "period": 14, "threshold": 30},
            },
            {
                "id": "r-rebound",
                "field": "indicator",
                "statement": "为了让想法可测试，把“反弹”定义为 RSI 上穿 30。",
                "origin": "ASSUMED",
                "confidence": "low",
            },
            {
                "id": "r-timeframe",
                "field": "timeframe",
                "statement": "用哪个周期，材料没有说明。",
                "origin": "UNKNOWN",
                "confidence": "high",
            },
        ],
        "ambiguities": [
            {
                "phrase": "超跌",
                "readings": ["RSI 低于 30", "价格跌破 20 日低点", "回撤超过 X%"],
                "needs_decision": True,
            },
            {
                "phrase": "反弹",
                "readings": ["RSI 上穿 30", "收盘价高于前一根 K 线"],
                "needs_decision": True,
            },
        ],
        "unknowns": [
            {"field": "timeframe", "why": "材料没有说明周期。", "needed_to_formalize": True},
            {"field": "exit", "why": "材料没有说明卖出条件。", "needed_to_formalize": True},
            {"field": "risk", "why": "材料没有说明止损。", "needed_to_formalize": True},
            {"field": "sizing", "why": "材料没有说明仓位。", "needed_to_formalize": True},
        ],
        "assumptions": [
            {
                "statement": (
                    "把“超跌”定义为 RSI(14) < 30：这是 AI 为了能测试而提出的定义，"
                    "不是 Martin 说的规则。"
                ),
                "applies_to": ["indicator"],
                "reason": "材料没有定义。",
            },
            {
                "statement": "把“反弹”定义为 RSI 上穿 30：同样是 AI 提出的可测试定义。",
                "applies_to": ["indicator"],
                "reason": "材料没有定义。",
            },
        ],
        "capability_requests": [],
        "limitations": ["材料是一句口头描述，没有参数。"],
        "confidence": "low",
    }


def draft_payload(source_ref: str = MARTIN) -> dict[str, Any]:
    """What a well-behaved STRATEGY_ARCHITECT answers for that hypothesis."""

    return {
        "strategy_name": "BTC oversold rebound (RSI formalization)",
        "status": "SUPPORTED",
        "market": {
            "markets": ["crypto"],
            "asset_classes": ["crypto"],
            "timeframes": ["1d"],
            "universe": "BTC",
        },
        "rules": [
            {
                "id": "d-market",
                "field": "market",
                "statement": "在 BTC 上。",
                "origin": "EXPLICIT",
                "confidence": "high",
                "derived_from": "r-market",
                "evidence": [{"source_ref": source_ref, "quote": "BTC"}],
            },
            {
                "id": "d-intent",
                "field": "entry",
                "statement": "在超跌之后的反弹里买入。",
                "origin": "EXPLICIT",
                "confidence": "medium",
                "derived_from": "r-entry",
                "evidence": [{"source_ref": source_ref, "quote": "超跌之后反弹的时候买入"}],
            },
            {
                "id": "d-entry",
                "field": "indicator",
                "statement": "RSI(14) 上穿 30 时买入。",
                "origin": "ASSUMED",
                "confidence": "low",
                "derived_from": "r-rebound",
                "parameters": {"indicator": "RSI", "period": 14, "threshold": 30},
            },
        ],
        "indicators": [
            {
                "name": "RSI",
                "origin": "ASSUMED",
                "parameters": {"period": 14},
                "note": "AI 提出的可测试定义",
            }
        ],
        "unknowns": [
            {"field": "timeframe", "why": "材料没有说明周期。"},
            {"field": "exit", "why": "材料没有说明卖出条件。"},
            {"field": "risk", "why": "材料没有说明止损。"},
            {"field": "sizing", "why": "材料没有说明仓位。"},
        ],
        "required_capabilities": [],
        "experimental_alternatives": [],
        "assumptions": [
            {
                "statement": "RSI(14) < 30 与上穿 30 是 AI 的可测试定义，不是原文规则。",
                "applies_to": ["indicator"],
            }
        ],
        "parameters": {"rsi_period": 14, "rsi_threshold": 30},
        "notes": ["这是定义，不是建议：本项目没有回测，也没有收益数字。"],
        "understanding_of_original": "Martin 的原始描述是定性的，没有参数。",
    }


def momentum_hypothesis_payload(source_ref: str = MOMENTUM) -> dict[str, Any]:
    """The §8 case: 20-day momentum ranked across a universe, rebalanced monthly.

    Every rule here is EXPLICIT, and two of them ask for capabilities the
    registry does not have. Nothing about this may be quietly narrowed.
    """

    return {
        "strategy_name": "Cross-sectional 20-day momentum",
        "understanding": "在全部标的上按 20 日动量排名，每月调仓买入排名前 10%。",
        "objective": "横截面动量轮动",
        "market": ["equities"],
        "asset_class": "equity",
        "universe": "美股大盘",
        "rules": [
            {
                "id": "m-signal",
                "field": "indicator",
                "statement": "按 20 日动量给标的排名。",
                "origin": "EXPLICIT",
                "confidence": "high",
                "parameters": {"lookback_days": 20},
                "required_capabilities": ["cross_sectional_universe"],
                "evidence": [{"source_ref": source_ref, "quote": "20 日动量排名"}],
            },
            {
                "id": "m-rebalance",
                "field": "execution",
                "statement": "每月调仓，买入排名前 10% 的标的。",
                "origin": "EXPLICIT",
                "confidence": "high",
                "required_capabilities": ["cross_sectional_universe", "portfolio_rules"],
                "evidence": [{"source_ref": source_ref, "quote": "每月调仓，买入排名前 10%"}],
            },
        ],
        "ambiguities": [
            {
                "phrase": "前 10%",
                "readings": ["按排名取前 10% 的标的", "取排名最前的 10 只"],
                "needs_decision": True,
            }
        ],
        "unknowns": [
            {"field": "risk", "why": "材料没有说明止损。", "needed_to_formalize": True},
            {"field": "sizing", "why": "材料没有说明每只标的的权重。", "needed_to_formalize": True},
        ],
        "assumptions": [],
        "capability_requests": [
            {
                "capability": "cross_sectional_universe",
                "statement": "需要在多个标的之间排名。",
                "reason": "横截面动量。",
            }
        ],
        "limitations": ["材料没有说明再平衡后的权重。"],
        "confidence": "high",
    }


def momentum_draft_payload(
    source_ref: str = MOMENTUM,
    *,
    status: str = "SUPPORTED",
    with_alternative: bool = False,
    with_ema: bool = True,
) -> dict[str, Any]:
    """The architect's answer for that hypothesis.

    ``status="SUPPORTED"`` with no alternative is the silent downgrade: the
    universe shrank to one asset, the ranking disappeared, and the draft claims
    the system supports it. ``status="PARTIALLY_SUPPORTED"`` with an alternative
    is the same idea done honestly.

    The 20-day momentum leg has no indicator of its own — the registry's
    ``indicators`` group is EMA/SMA/RSI/ATR/MACD/BOLLINGER — so the architect may
    only formalize it with something that exists (an EMA(20) trend rule), and it
    has to say that the definition is its own, not the user's. ``with_ema=False``
    drops even that: the case where the system has nothing to build the signal
    with, and the draft has to name it as missing.
    """

    signal_statement = (
        "AI 形式化：收盘价高于 EMA(20) 时视为「20 日动量」为正。"
        if with_ema
        else "本系统没有动量类指标，这条信号无法形式化。"
    )
    formalization = (
        "把「20 日动量」形式化为「收盘价高于 EMA(20)」：这是 AI 为了能测试而提出的定义，"
        "不是用户原文写下的规则。"
        if with_ema
        else "「20 日动量」本系统无法表达，草案只能把它记成缺失的能力。"
    )
    draft: dict[str, Any] = {
        "strategy_name": "20-day momentum",
        "status": status,
        "market": {
            "markets": ["equities"],
            "asset_classes": ["equity"],
            "timeframes": ["1d"],
            "universe": "AAPL" if not with_alternative else "美股大盘",
        },
        "indicators": (
            [
                {
                    "name": "EMA",
                    "origin": "ASSUMED",
                    "parameters": {"period": 20},
                    "note": "AI 为表达「20 日动量」提出的形式化",
                }
            ]
            if with_ema
            else []
        ),
        "rules": [
            {
                "id": "d-signal",
                "field": "indicator",
                "statement": signal_statement,
                "origin": "ASSUMED",
                "confidence": "low",
                "derived_from": "m-signal",
                "evidence": [{"source_ref": source_ref}],
            },
            {
                "id": "d-rebalance",
                "field": "execution",
                "statement": "每月调仓，买入动量排名前 10% 的标的。",
                "origin": "EXPLICIT",
                "confidence": "high",
                "derived_from": "m-rebalance",
                "evidence": [{"source_ref": source_ref, "quote": "每月调仓，买入排名前 10%"}],
            },
        ],
        "unknowns": [
            {"field": "risk", "why": "材料没有说明止损。"},
            {"field": "sizing", "why": "材料没有说明每只标的的权重。"},
        ],
        "required_capabilities": [],
        "experimental_alternatives": [],
        "assumptions": [
            {
                "statement": formalization,
                "applies_to": ["indicator"],
                "reason": "注册表里的指标只有 EMA/SMA/RSI/ATR/MACD/BOLLINGER。",
            }
        ],
        "parameters": {"lookback_days": 20},
        "notes": ["这是形式化草案，没有回测，也没有收益数字。"],
        "understanding_of_original": "原始要求是在全部标的上排名并每月调仓。",
    }
    if not with_alternative:
        return draft

    draft["market"]["universe"] = "单标的 AAPL（实验）"
    draft["rules"][1]["statement"] = "实验版：只在单标的上做 20 日动量，不做横截面排名。"
    draft["rules"][1]["origin"] = "ASSUMED"
    draft["assumptions"].append(
        {
            "statement": "实验版把横截面排名缩成单标的：这是 AI 提出的实验，不是原始要求。",
            "applies_to": ["execution"],
            "reason": "横截面排名不在本系统的能力范围内。",
        }
    )
    draft["required_capabilities"] = [
        {
            "capability": "cross_sectional_universe",
            "affected_rule": "d-signal",
            "reason": "本系统只能对单个标的做确定性回测。",
            "suggested_alternative": "先用单标的 20 日动量做实验，排名能力补齐后再做原策略。",
            "alternative_is_experimental": True,
        },
        {
            "capability": "portfolio_rules",
            "affected_rule": "d-rebalance",
            "reason": "本系统没有组合层规则。",
            "suggested_alternative": "实验版只做单标的，不做组合权重。",
            "alternative_is_experimental": True,
        },
    ]
    draft["experimental_alternatives"] = [
        {
            "label": "Experiment: single-asset 20-day momentum",
            "statement": "先在 AAPL 上验证 20 日动量本身，不做横截面排名。",
            "what_it_gives_up": ["横截面排名", "每月调仓的前 10% 组合"],
            "differs_from_original": True,
        }
    ]
    return draft
