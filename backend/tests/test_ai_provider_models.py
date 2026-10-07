"""AI provider model discovery, selection and deletion (ADR-176, ADR-177).

Two product decisions are pinned down here.

First, MQL no longer caps what an OpenAI-compatible ``/models`` endpoint returns
and never invents a model name: ``google/gemma-4-31b-it:free`` and
``openrouter/free`` keep their colons. Only the user's own selection enters the
catalogue.

Second, a provider or model is *current configuration* and is always deletable —
history is a separate lifecycle. Deleting configuration snapshots the names onto
the AI task and usage rows and clears only the foreign keys, so cost reports and
task history stay readable instead of becoming empty, "unknown" or a broken
query.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest

from app.data.ai_provider_service import parse_model_entry
from app.data.ai_provider_service import test_connection as probe_connection
from app.domain.models import AIModel, AITask, AIUsage

BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openrouter/free"
DISCOVERED = "google/gemma-4-31b-it:free"
SECOND_DISCOVERED = "thinkingmachines/inkling:free"


def _payload(**overrides) -> dict:
    payload = {
        "name": "openrouter-main",
        "base_url": BASE_URL,
        "api_key": "sk-secret-value",
        "default_model": DEFAULT_MODEL,
        "daily_budget_usd": 2.0,
    }
    payload.update(overrides)
    return payload


def _create(client, **overrides) -> dict:
    response = client.post("/api/v1/settings/ai/providers", json=_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _provider(client, provider_id: int) -> dict:
    providers = client.get("/api/v1/settings/ai/providers").json()["providers"]
    return next(p for p in providers if p["id"] == provider_id)


def _models(client, provider_id: int) -> list[dict]:
    return _provider(client, provider_id)["models"]


def _save(client, provider_id: int, *, models=None, manual=None) -> dict:
    body: dict = {}
    if models is not None:
        body["models"] = models
    if manual is not None:
        body["manual_models"] = manual
    response = client.put(f"/api/v1/settings/ai/providers/{provider_id}/models", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _names(client, provider_id: int) -> set[str]:
    return {row["model_name"] for row in _models(client, provider_id)}


def _active_names(client, provider_id: int) -> set[str]:
    return {row["model_name"] for row in _models(client, provider_id) if row["is_active"]}


class _FakeResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


def _catalogue(count: int) -> list[dict]:
    return [{"id": f"vendor/model-{index}:free"} for index in range(count)]


# --- discovery ---------------------------------------------------------------


def test_discovery_returns_every_model_the_endpoint_lists(monkeypatch) -> None:
    """The old 40-row cap truncated OpenRouter; the count now tells the truth."""
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse({"data": _catalogue(137)}))

    result = probe_connection(BASE_URL, "sk-secret-value")

    assert result["ok"] is True
    assert result["models_total"] == 137
    assert len(result["models_found"]) == 137
    assert result["models_found"][-1] == "vendor/model-136:free"
    assert "137 model" in result["detail"]


def test_stored_provider_test_reports_the_full_catalogue(client, monkeypatch) -> None:
    """``读取/加载模型`` reuses the connection test, so it must not cap either."""
    created = _create(client)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse({"data": _catalogue(137)}))

    result = client.post(f"/api/v1/settings/ai/providers/{created['id']}/test").json()

    assert result["models_total"] == 137
    assert len(result["models_found"]) == 137
    assert "137 model" in result["detail"]


# --- parsing -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        (DISCOVERED, {"model_name": DISCOVERED}),
        (DEFAULT_MODEL, {"model_name": DEFAULT_MODEL}),
        (SECOND_DISCOVERED, {"model_name": SECOND_DISCOVERED}),
        ("vendor/model:nitro", {"model_name": "vendor/model:nitro"}),
        (
            "openai/gpt-4o-mini:cheap:0.15:0.6",
            {
                "model_name": "openai/gpt-4o-mini",
                "capability_tier": "cheap",
                "input_cost_per_mtok": 0.15,
                "output_cost_per_mtok": 0.6,
            },
        ),
        ("some-model:high", {"model_name": "some-model", "capability_tier": "high"}),
        ("", {}),
    ],
)
def test_only_mqls_documented_shapes_are_split(entry: str, expected: dict) -> None:
    assert parse_model_entry(entry) == expected


# --- selection ---------------------------------------------------------------


def test_manual_add_keeps_a_colon_bearing_model_id_verbatim(client) -> None:
    created = _create(client)

    _save(client, created["id"], manual=[DISCOVERED, SECOND_DISCOVERED, ""])

    assert _names(client, created["id"]) == {DEFAULT_MODEL, DISCOVERED, SECOND_DISCOVERED}
    assert DISCOVERED in _active_names(client, created["id"])


def test_only_the_selected_models_enter_the_catalogue(client) -> None:
    """A 137-model discovery does not mean 137 rows: the selection decides."""
    created = _create(client)

    _save(
        client,
        created["id"],
        models=[
            {"model_name": DISCOVERED},
            {
                "model_name": "openai/gpt-4o-mini",
                "capability_tier": "cheap",
                "input_cost_per_mtok": 0.15,
                "output_cost_per_mtok": 0.6,
            },
            {"model_name": DISCOVERED},
        ],
        manual=[SECOND_DISCOVERED],
    )

    rows = {row["model_name"]: row for row in _models(client, created["id"])}
    # The untouched default was not selected: it is kept, but switched off.
    assert set(rows) == {DEFAULT_MODEL, DISCOVERED, "openai/gpt-4o-mini", SECOND_DISCOVERED}
    assert _active_names(client, created["id"]) == {
        DISCOVERED,
        "openai/gpt-4o-mini",
        SECOND_DISCOVERED,
    }
    assert rows["openai/gpt-4o-mini"]["capability_tier"] == "cheap"
    assert rows["openai/gpt-4o-mini"]["input_cost_per_mtok"] == 0.15


def test_saving_the_same_selection_twice_does_not_duplicate_rows(client) -> None:
    created = _create(client)
    selection = [{"model_name": DISCOVERED}, {"model_name": SECOND_DISCOVERED}]

    _save(client, created["id"], models=selection)
    first = [row["id"] for row in _models(client, created["id"])]
    _save(client, created["id"], models=selection)
    second = [row["id"] for row in _models(client, created["id"])]

    assert first == second
    assert len(_models(client, created["id"])) == 3  # default + two discovered


def test_unchecking_a_model_deactivates_it_without_deleting_it(client) -> None:
    created = _create(client)
    _save(client, created["id"], models=[{"model_name": DISCOVERED}])
    assert DISCOVERED in _active_names(client, created["id"])

    _save(client, created["id"], models=[])

    rows = {row["model_name"]: row for row in _models(client, created["id"])}
    assert DISCOVERED in rows, "deactivating must not delete the row"
    assert rows[DISCOVERED]["is_active"] is False
    assert _active_names(client, created["id"]) == set()


def test_reselecting_a_deactivated_model_activates_it_again(client) -> None:
    created = _create(client)
    _save(client, created["id"], models=[])
    assert _active_names(client, created["id"]) == set()

    _save(client, created["id"], models=[{"model_name": DEFAULT_MODEL}])

    assert _active_names(client, created["id"]) == {DEFAULT_MODEL}


def test_saving_models_leaves_tier_and_costs_of_existing_rows_alone(client) -> None:
    created = _create(client)
    _save(
        client,
        created["id"],
        models=[
            {
                "model_name": DISCOVERED,
                "capability_tier": "high",
                "input_cost_per_mtok": 1.5,
                "output_cost_per_mtok": 2.5,
            }
        ],
    )

    _save(client, created["id"], models=[{"model_name": DISCOVERED}])

    row = next(r for r in _models(client, created["id"]) if r["model_name"] == DISCOVERED)
    assert row["capability_tier"] == "high"
    assert row["input_cost_per_mtok"] == 1.5
    assert row["output_cost_per_mtok"] == 2.5


def test_saving_models_does_not_touch_the_provider_switch_or_budget(client) -> None:
    created = _create(client, is_active=False)

    _save(client, created["id"], models=[{"model_name": DISCOVERED}])

    provider = _provider(client, created["id"])
    assert provider["is_active"] is False
    assert provider["daily_budget_usd"] == 2.0
    assert provider["default_model"] == DEFAULT_MODEL


def test_the_model_switch_endpoint_still_works_after_a_save(client) -> None:
    created = _create(client)
    _save(client, created["id"], models=[{"model_name": DISCOVERED}])
    row = next(r for r in _models(client, created["id"]) if r["model_name"] == DISCOVERED)

    switched = client.put(f"/api/v1/settings/ai/models/{row['id']}", json={"is_active": False})

    assert switched.status_code == 200
    assert switched.json()["is_active"] is False
    assert switched.json()["model_name"] == DISCOVERED


def test_saving_models_for_an_unknown_provider_is_a_404(client) -> None:
    response = client.put("/api/v1/settings/ai/providers/99999/models", json={"models": []})
    assert response.status_code == 404


def test_a_rejected_tier_is_a_422(client) -> None:
    created = _create(client)
    response = client.put(
        f"/api/v1/settings/ai/providers/{created['id']}/models",
        json={"models": [{"model_name": DISCOVERED, "capability_tier": "free"}]},
    )
    assert response.status_code == 422


def test_manual_entries_are_bounded(client) -> None:
    created = _create(client)
    response = client.put(
        f"/api/v1/settings/ai/providers/{created['id']}/models",
        json={"manual_models": ["x" * 129]},
    )
    assert response.status_code == 422


# --- deletion ----------------------------------------------------------------


def _seed_history(db_session, provider_id: int, model_id: int) -> tuple[int, int]:
    task = AITask(
        task_type="signal_explanation",
        provider_id=provider_id,
        model_id=model_id,
        provider_name=None,
        model_name=None,
        prompt_name="signal_explain",
        prompt_version="1.0.0",
        input_hash="h" * 64,
        status="completed",
        cost_usd=Decimal("0.25"),
    )
    usage = AIUsage(
        usage_date=dt.date.today(),
        provider_id=provider_id,
        model_id=model_id,
        task_type="signal_explanation",
        call_count=3,
        total_tokens=1234,
        total_cost_usd=Decimal("0.25"),
    )
    db_session.add_all([task, usage])
    db_session.commit()
    return task.id, usage.id


def test_a_provider_without_history_is_deletable(client) -> None:
    created = _create(client)

    response = client.delete(f"/api/v1/settings/ai/providers/{created['id']}")

    assert response.status_code == 200
    assert client.get("/api/v1/settings/ai/providers").json()["providers"] == []


def test_a_model_without_history_is_deletable(client) -> None:
    created = _create(client)
    _save(client, created["id"], models=[{"model_name": DISCOVERED}])
    row = next(r for r in _models(client, created["id"]) if r["model_name"] == DISCOVERED)

    response = client.delete(f"/api/v1/settings/ai/models/{row['id']}")

    assert response.status_code == 200
    assert response.json()["model_name"] == DISCOVERED
    assert _names(client, created["id"]) == {DEFAULT_MODEL}


def test_deleting_a_provider_keeps_task_and_usage_history_readable(client, db_session) -> None:
    created = _create(client)
    model = _models(client, created["id"])[0]
    task_id, usage_id = _seed_history(db_session, created["id"], model["id"])

    assert client.delete(f"/api/v1/settings/ai/providers/{created['id']}").status_code == 200

    db_session.expire_all()
    task = db_session.get(AITask, task_id)
    usage = db_session.get(AIUsage, usage_id)
    assert task is not None and usage is not None, "history must survive the delete"
    assert task.provider_id is None and task.model_id is None
    assert task.provider_name == "openrouter-main"
    assert task.model_name == DEFAULT_MODEL
    assert usage.provider_id is None and usage.model_id is None
    assert usage.provider_name == "openrouter-main"
    assert usage.model_name == DEFAULT_MODEL
    assert usage.call_count == 3 and float(usage.total_cost_usd) == 0.25

    # The read paths still name what was used, and still answer.
    detail = client.get(f"/api/v1/ai/tasks/{task_id}")
    assert detail.status_code == 200
    assert detail.json()["provider_name"] == "openrouter-main"
    assert detail.json()["model_name"] == DEFAULT_MODEL

    listed = client.get("/api/v1/ai/usage").json()["usage"]
    assert [row["provider_name"] for row in listed] == ["openrouter-main"]
    assert [row["model_name"] for row in listed] == [DEFAULT_MODEL]

    today = client.get("/api/v1/ai/usage-today").json()["providers"]
    assert today["openrouter-main"]["calls"] == 3
    assert today["openrouter-main"]["cost_usd_estimated"] == 0.25


def test_deleting_a_model_keeps_task_and_usage_history_readable(client, db_session) -> None:
    created = _create(client)
    _save(client, created["id"], models=[{"model_name": DISCOVERED}])
    row = next(r for r in _models(client, created["id"]) if r["model_name"] == DISCOVERED)
    task_id, usage_id = _seed_history(db_session, created["id"], row["id"])

    assert client.delete(f"/api/v1/settings/ai/models/{row['id']}").status_code == 200

    db_session.expire_all()
    assert db_session.get(AIModel, row["id"]) is None
    task = db_session.get(AITask, task_id)
    usage = db_session.get(AIUsage, usage_id)
    assert task is not None and usage is not None
    assert task.model_id is None
    assert task.provider_id == created["id"], "the provider is untouched"
    assert task.model_name == DISCOVERED
    assert usage.model_id is None
    assert usage.provider_id == created["id"]
    assert usage.model_name == DISCOVERED

    detail = client.get(f"/api/v1/ai/tasks/{task_id}").json()
    assert detail["model_name"] == DISCOVERED
    listed = client.get("/api/v1/ai/usage").json()["usage"]
    assert [row["model_name"] for row in listed] == [DISCOVERED]


def test_deleting_a_model_that_does_not_exist_is_a_404(client) -> None:
    assert client.delete("/api/v1/settings/ai/models/99999").status_code == 404
