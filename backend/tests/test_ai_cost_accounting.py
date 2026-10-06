"""What one AI call costs, and who is allowed to say so (v2.4.0 Step 1).

The ledger used to multiply a character count by the rate card, so a deployment
with model prices left at 0 recorded ``0.0`` no matter what the model actually
did -- a confident number nobody could audit. A model with a configured price
now has to come back with the provider's own token counts: without them the task
fails loudly instead of inventing a cost, and an unpriced model keeps the old
estimate for backwards compatibility.
"""

from __future__ import annotations

import dataclasses
import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.ai.provider import (
    SIGNAL_EXPLANATION_SCHEMA,
    AIRequest,
    OpenAICompatibleProvider,
    ProviderCallError,
)
from app.ai.role_contracts import contract_for_role
from app.ai.runtime import run_task
from app.domain.models import AIModel, AIProvider, AITask
from app.infrastructure.secrets import encrypt_secret

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
PRIMARY_MODEL = "meta/muse-spark-1.3"
#: OpenRouter's published rate for the primary model, per million tokens.
IN_PER_MTOK = Decimal("1.25")
OUT_PER_MTOK = Decimal("4.25")
#: 1200 in + 300 out at the rates above.
EXPECTED_COST = 0.0015 + 0.001275

ANSWER = {
    "summary": "价格上穿均线，触发买入信号。",
    "why": ["收盘价高于 EMA20"],
    "what_to_watch_next": ["下一根 K 线能否站稳"],
    "risk_notes": ["震荡市容易假突破"],
    "plain_language": "简单说：趋势刚刚转强，但还没走稳。",
}


class _FakeResponse:
    def __init__(self, payload: dict | None) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _answering_post(*, usage: dict | None):
    """A stand-in for ``httpx.post`` that answers like OpenRouter would."""

    def post(url: str, **kwargs: object) -> _FakeResponse:
        payload: dict = {
            "model": PRIMARY_MODEL,
            "choices": [{"message": {"content": json.dumps(ANSWER)}}],
        }
        if usage is not None:
            payload["usage"] = usage
        return _FakeResponse(payload)

    return post


def _provider(db_session, *, budget: float = 2.0) -> AIProvider:
    provider = AIProvider(
        name="openrouter",
        provider_type="openai_compatible",
        base_url=OPENROUTER_BASE,
        api_key_encrypted=encrypt_secret("sk-test"),
        default_model=PRIMARY_MODEL,
        daily_budget_usd=budget,
    )
    db_session.add(provider)
    db_session.flush()
    return provider


def _priced_model(db_session, provider: AIProvider) -> AIModel:
    model = AIModel(
        provider_id=provider.id,
        model_name=PRIMARY_MODEL,
        capability_tier="standard",
        input_cost_per_mtok=IN_PER_MTOK,
        output_cost_per_mtok=OUT_PER_MTOK,
        is_active=True,
    )
    db_session.add(model)
    db_session.flush()
    return model


def _request(**overrides) -> AIRequest:
    contract = contract_for_role("EXPLAINER")
    request = AIRequest(
        task_type="signal_explanation",
        prompt_name=contract.prompt_name_for("signal_explanation"),
        prompt_version=contract.version,
        system_prompt=contract.system_prompt_for("signal_explanation"),
        user_prompt="facts",
        structured_facts={"state": "BUY", "symbol": "DEMO-AAPL"},
        schema=SIGNAL_EXPLANATION_SCHEMA,
        role="EXPLAINER",
    )
    return dataclasses.replace(request, **overrides)


def _only_task(db_session) -> AITask:
    return db_session.scalars(select(AITask)).one()


# ------------------------------------------------------------------- the client


def test_the_client_keeps_the_usage_the_provider_reported(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(
        httpx,
        "post",
        _answering_post(usage={"prompt_tokens": 1200, "completion_tokens": 300}),
    )
    client = OpenAICompatibleProvider(
        base_url=OPENROUTER_BASE, api_key="sk-test", name="openrouter"
    )

    reply = client.reply([{"role": "user", "content": "hi"}], model=PRIMARY_MODEL)

    assert reply.text == json.dumps(ANSWER)
    assert reply.model == PRIMARY_MODEL
    assert reply.usage == {"input_tokens": 1200, "output_tokens": 300}
    assert reply.reported_usage is True
    assert client.last_reply is reply
    # ``chat`` still returns plain text, so the older callers are untouched.
    assert client.chat([{"role": "user", "content": "hi"}], model=PRIMARY_MODEL) == json.dumps(
        ANSWER
    )


def test_a_provider_that_reports_no_usage_keeps_an_empty_usage(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(httpx, "post", _answering_post(usage=None))
    client = OpenAICompatibleProvider(base_url=OPENROUTER_BASE, api_key="sk-test")

    reply = client.reply([{"role": "user", "content": "hi"}], model=PRIMARY_MODEL)

    assert reply.usage == {}
    assert reply.reported_usage is False


def test_an_answer_without_a_choice_is_a_loud_failure(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: _FakeResponse({"error": "boom"}))
    client = OpenAICompatibleProvider(base_url=OPENROUTER_BASE, api_key="sk-test")

    with pytest.raises(ProviderCallError):
        client.reply([{"role": "user", "content": "hi"}], model=PRIMARY_MODEL)


# ------------------------------------------------------------------ the ledger


def test_a_priced_model_records_the_cost_the_provider_reported(db_session, monkeypatch) -> None:
    import httpx

    provider = _provider(db_session)
    model = _priced_model(db_session, provider)
    monkeypatch.setattr(
        httpx,
        "post",
        _answering_post(usage={"prompt_tokens": 1200, "completion_tokens": 300}),
    )

    result = run_task(db_session, _request(), providers=[(provider, "sk-test", [model])])

    assert result["cached"] is False
    assert result["model"] == PRIMARY_MODEL
    assert result["cost_usd_estimated"] == pytest.approx(EXPECTED_COST)

    task = _only_task(db_session)
    assert task.status == "completed"
    assert task.cost_usd == Decimal("0.002775")
    assert task.token_usage_json == {
        "input_tokens": 1200,
        "output_tokens": 300,
        "input_tokens_estimated": 1200,
        "output_tokens_estimated": 300,
        "estimated": False,
        "reported_by_provider": True,
    }


def test_a_priced_model_that_reports_nothing_fails_instead_of_inventing_a_cost(
    db_session, monkeypatch
) -> None:
    import httpx

    provider = _provider(db_session)
    model = _priced_model(db_session, provider)
    monkeypatch.setattr(httpx, "post", _answering_post(usage=None))

    with pytest.raises(RuntimeError, match="reported no token usage"):
        run_task(db_session, _request(), providers=[(provider, "sk-test", [model])])

    task = _only_task(db_session)
    assert task.status == "failed"
    assert "reported no token usage" in (task.error_message or "")
    # No fabricated number: the ledger keeps the column's zero default, it does
    # not record "1200 tokens x a rate card" as if a provider had confirmed it.
    assert float(task.cost_usd or 0) == 0.0
    assert task.token_usage_json is None


def test_an_unpriced_model_keeps_the_estimated_fallback(db_session, monkeypatch) -> None:
    """No rate card (the historical default) still works, but says it is a guess."""

    import httpx

    provider = _provider(db_session)
    monkeypatch.setattr(httpx, "post", _answering_post(usage=None))

    result = run_task(db_session, _request(), providers=[(provider, "sk-test", [])])

    assert result["cost_usd_estimated"] == 0.0
    task = _only_task(db_session)
    assert task.status == "completed"
    assert task.token_usage_json is not None
    assert task.token_usage_json["estimated"] is True
    assert task.token_usage_json["reported_by_provider"] is False
    assert task.cost_usd == Decimal("0")
