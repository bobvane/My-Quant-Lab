"""Model-level enable/disable: the second routing switch (ADR-173).

``ai_models.is_active`` was read by the router and written by nothing: the
catalogue displayed a flag no code could change, and the runtime synthesized the
provider's ``default_model`` whenever a provider had no *active* model. That made
"the operator switched every model off" and "the provider is still called, at
price 0, with no model row to account against" the same state.

These tests pin the endpoint, its one refusal rule, and the routing consequences:
a disabled model leaves the candidate list, a provider with model rows that are
all disabled has *no* routable model (rather than a synthesized one), and only a
provider with no model rows at all keeps the compatibility fallback.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json

import pytest
from sqlalchemy import func, select

from app.ai.explain import get_active_providers
from app.ai.provider import SIGNAL_EXPLANATION_SCHEMA, AIRequest, AIRouter
from app.ai.role_contracts import contract_for_role
from app.ai.runtime import _catalogue, run_task
from app.domain.models import AIModel, AIProvider, AITask, AIUsage
from app.infrastructure.secrets import encrypt_secret

DEFAULT_MODEL = "openrouter/free"
SECOND_MODEL = "nvidia/nemotron:free"
API_KEY = "sk-super-secret-value"
BASE_URL = "https://openrouter.ai/api/v1"


def _payload(**overrides: object) -> dict:
    payload: dict = {
        "name": "openrouter-free",
        "base_url": BASE_URL,
        "api_key": API_KEY,
        "default_model": DEFAULT_MODEL,
        "daily_budget_usd": 2.0,
        "models": [
            {"model_name": DEFAULT_MODEL, "capability_tier": "standard"},
            {"model_name": SECOND_MODEL, "capability_tier": "standard"},
        ],
    }
    payload.update(overrides)
    return payload


def _create(client, **overrides: object) -> dict:
    response = client.post("/api/v1/settings/ai/providers", json=_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _ids(created: dict) -> dict[str, int]:
    return {str(m["model_name"]): int(m["id"]) for m in created["models"]}


def _switch(client, model_id: int, is_active: bool = False):
    return client.put(f"/api/v1/settings/ai/models/{model_id}", json={"is_active": is_active})


def _listed(client) -> dict[str, dict]:
    rows = client.get("/api/v1/ai/models").json()["models"]
    return {str(row["model_name"]): row for row in rows}


def _seed(
    db_session,
    *,
    name: str = "openrouter-free",
    default_model: str = DEFAULT_MODEL,
    models: tuple[str, ...] = (DEFAULT_MODEL,),
    is_active: bool = True,
):
    provider = AIProvider(
        name=name,
        provider_type="openai_compatible",
        base_url=BASE_URL,
        api_key_encrypted=encrypt_secret("sk-test"),
        default_model=default_model,
        daily_budget_usd=2.0,
        is_active=is_active,
    )
    db_session.add(provider)
    db_session.flush()
    rows = []
    for model_name in models:
        row = AIModel(provider_id=provider.id, model_name=model_name)
        db_session.add(row)
        rows.append(row)
    db_session.flush()
    return provider, rows


def _request(**overrides: object) -> AIRequest:
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


# ------------------------------------------------------------------- the endpoint


def test_switching_one_model_off_flips_only_that_flag(client) -> None:
    created = _create(client)
    model_id = _ids(created)[SECOND_MODEL]

    response = _switch(client, model_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model_name"] == SECOND_MODEL
    assert body["is_active"] is False
    assert body["provider"] == "openrouter-free"
    assert body["provider_id"] == created["id"]
    assert body["provider_is_active"] is True

    listed = _listed(client)
    assert listed[SECOND_MODEL]["is_active"] is False
    assert listed[DEFAULT_MODEL]["is_active"] is True

    # The row is switched, never deleted, and the provider is untouched.
    provider = client.get("/api/v1/settings/ai/providers").json()["providers"][0]
    assert provider["is_active"] is True
    assert provider["default_model"] == DEFAULT_MODEL
    assert {m["model_name"] for m in provider["models"]} == {DEFAULT_MODEL, SECOND_MODEL}


def test_a_disabled_model_can_be_enabled_again(client) -> None:
    created = _create(client)
    model_id = _ids(created)[SECOND_MODEL]
    assert _switch(client, model_id).status_code == 200

    response = _switch(client, model_id, is_active=True)
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is True
    assert _listed(client)[SECOND_MODEL]["is_active"] is True


def test_the_last_routable_default_model_cannot_be_switched_off(client) -> None:
    created = _create(client, models=[{"model_name": DEFAULT_MODEL}])
    model_id = _ids(created)[DEFAULT_MODEL]

    refused = _switch(client, model_id)
    assert refused.status_code == 409, refused.text
    assert "default_model" in refused.json()["detail"]

    # A refusal changes nothing: the model is still active and still routable.
    assert _listed(client)[DEFAULT_MODEL]["is_active"] is True
    assert client.get("/api/v1/ai/status").json()["configured"] is True


def test_a_non_default_model_may_be_switched_off_even_when_it_is_the_last_other(client) -> None:
    created = _create(client)
    ids = _ids(created)

    # Not the default model, so switching it off is allowed even though it is the
    # only other active model: the provider still has its default model to route.
    assert _switch(client, ids[SECOND_MODEL]).status_code == 200
    # Now the default model carries the provider alone, so it is protected.
    assert _switch(client, ids[DEFAULT_MODEL]).status_code == 409

    listed = {name: row["is_active"] for name, row in _listed(client).items()}
    assert listed == {DEFAULT_MODEL: True, SECOND_MODEL: False}


def test_an_unknown_model_is_a_404_and_a_bad_body_is_a_422(client) -> None:
    assert _switch(client, 999999).status_code == 404

    created = _create(client)
    model_id = _ids(created)[DEFAULT_MODEL]
    assert client.put(f"/api/v1/settings/ai/models/{model_id}", json={}).status_code == 422
    assert (
        client.put(
            f"/api/v1/settings/ai/models/{model_id}",
            json={"is_active": False, "api_key": API_KEY},
        ).status_code
        == 422
    )
    # The rejected bodies left the flag alone.
    assert _listed(client)[DEFAULT_MODEL]["is_active"] is True


def test_the_switch_is_audited_without_the_api_key(client) -> None:
    created = _create(client)
    model_id = _ids(created)[SECOND_MODEL]
    assert _switch(client, model_id).status_code == 200

    ledger = client.get(f"/api/v1/audit/logs/entity/ai_model/{model_id}").json()
    entry = next(e for e in ledger["events"] if e["event_type"] == "ai_model_updated")
    assert entry["entity_type"] == "ai_model"
    assert entry["entity_id"] == str(model_id)
    assert entry["action"] == "update"
    assert entry["payload"] == {"model_name": SECOND_MODEL, "is_active": False}

    text = json.dumps(ledger)
    assert API_KEY not in text
    assert BASE_URL not in text


def test_switching_a_model_keeps_task_and_usage_history(client, db_session) -> None:
    created = _create(client)
    ids = _ids(created)
    task = AITask(
        task_type="signal_explanation",
        provider_id=created["id"],
        model_id=ids[SECOND_MODEL],
        prompt_name="signal_explanation",
        prompt_version="1",
        input_hash="hash-1",
        output_json={"summary": "kept"},
        status="completed",
    )
    usage = AIUsage(
        usage_date=dt.date.today(),
        provider_id=created["id"],
        model_id=ids[SECOND_MODEL],
        task_type="signal_explanation",
        call_count=1,
        total_tokens=10,
        total_cost_usd=0.0,
    )
    db_session.add_all([task, usage])
    db_session.flush()
    task_id = task.id

    assert _switch(client, ids[SECOND_MODEL]).status_code == 200

    assert db_session.scalar(select(func.count()).select_from(AITask)) == 1
    assert db_session.scalar(select(func.count()).select_from(AIUsage)) == 1
    detail = client.get(f"/api/v1/ai/tasks/{task_id}")
    assert detail.status_code == 200, detail.text
    assert str(detail.json()).count("kept") >= 1


# -------------------------------------------------------------------- the routing


def test_a_disabled_model_leaves_the_candidate_list(db_session) -> None:
    provider, rows = _seed(
        db_session, default_model=DEFAULT_MODEL, models=(DEFAULT_MODEL, SECOND_MODEL)
    )
    rows[1].is_active = False
    db_session.flush()

    live, _by_name, _provider_models, options, budgets = _catalogue(
        db_session, get_active_providers(db_session)
    )
    assert [option.model for option in options] == [DEFAULT_MODEL]

    router = AIRouter(live, 10.0, models=options, budgets=budgets)
    assert router.pick("signal_explanation") == (provider.name, DEFAULT_MODEL)
    # Existing behaviour, pinned here so it cannot change silently: a preferred
    # model that is not a candidate degrades to whatever is (never a crash, and
    # never the disabled model itself).
    assert router.pick("signal_explanation", SECOND_MODEL) == (provider.name, DEFAULT_MODEL)


def test_switching_every_model_off_leaves_no_synthetic_fallback(db_session) -> None:
    _provider, rows = _seed(
        db_session, default_model=DEFAULT_MODEL, models=(DEFAULT_MODEL, SECOND_MODEL)
    )
    for row in rows:
        row.is_active = False
    db_session.flush()

    *_rest, options, _budgets = _catalogue(db_session, get_active_providers(db_session))
    # Every row is switched off on purpose: `default_model` must not come back as
    # a 0-cost synthetic option.
    assert options == []


def test_a_provider_without_model_rows_keeps_the_compatibility_fallback(db_session) -> None:
    provider, _rows = _seed(db_session, default_model="legacy-model", models=())

    *_rest, options, _budgets = _catalogue(db_session, get_active_providers(db_session))
    assert [(o.provider, o.model, o.total_cost) for o in options] == [
        (provider.name, "legacy-model", 0.0)
    ]


def test_run_task_refuses_when_every_model_is_switched_off(db_session) -> None:
    _provider, rows = _seed(db_session, default_model=DEFAULT_MODEL, models=(DEFAULT_MODEL,))
    rows[0].is_active = False
    db_session.flush()

    calls: list = []

    def factory(providers: dict, budget: float):  # pragma: no cover - must not be reached
        raise AssertionError("the provider must not be called without an active model")

    with pytest.raises(RuntimeError) as excinfo:
        run_task(
            db_session,
            _request(),
            providers=get_active_providers(db_session),
            router_factory=factory,
        )

    assert "ai_no_active_model" in str(excinfo.value)
    assert calls == []
    # No pseudo task was booked against a model_id-less, 0-cost route.
    assert db_session.scalar(select(func.count()).select_from(AITask)) == 0


def test_a_disabled_provider_hides_its_active_models(db_session) -> None:
    _provider, _rows = _seed(db_session, models=(DEFAULT_MODEL,), is_active=False)
    assert get_active_providers(db_session) == []


def test_status_stops_naming_a_model_nobody_can_route(client) -> None:
    created = _create(client)
    ids = _ids(created)

    status = client.get("/api/v1/ai/status").json()
    assert status["configured"] is True
    assert status["model"] == DEFAULT_MODEL

    # Switching the default model off is allowed while another one is active,
    # and the status line follows the router to that other model.
    assert _switch(client, ids[DEFAULT_MODEL]).status_code == 200
    status = client.get("/api/v1/ai/status").json()
    assert status["configured"] is True
    assert status["model"] == SECOND_MODEL

    # Now nothing is routable, and the status must say so instead of naming a
    # model that `run_task` would refuse to call.
    assert _switch(client, ids[SECOND_MODEL]).status_code == 200
    status = client.get("/api/v1/ai/status").json()
    assert status["configured"] is False
    assert "no active model" in status["note"]
    assert status["provider_name"] == "openrouter-free"

    # The catalogue still lists both rows so the operator can switch them back on.
    listed = _listed(client)
    assert {name: row["is_active"] for name, row in listed.items()} == {
        DEFAULT_MODEL: False,
        SECOND_MODEL: False,
    }
    assert all(row["provider_is_active"] is True for row in listed.values())
