"""AI provider configuration tests.

The provider endpoints are the only way to make the AI layer usable, and the
only place the product stores a user-supplied secret. The tests therefore pin
down three things above all: the key is encrypted at rest, the key is never
returned by any read path, and connectivity testing refuses non-allow-listed
URLs.
"""

from __future__ import annotations

import pytest

from app.data.ai_provider_service import (
    ProviderConfigError,
    create_provider,
    delete_provider,
    list_providers,
    update_provider,
)
from app.data.ai_provider_service import test_connection as probe_connection
from app.domain.models import AIProvider
from app.infrastructure.secrets import decrypt_secret


def _create_payload(**overrides) -> dict:
    payload = {
        "name": "openai-main",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-super-secret-value",
        "default_model": "gpt-4o-mini",
        "daily_budget_usd": 2.5,
    }
    payload.update(overrides)
    return payload


def test_create_provider_encrypts_key_and_never_returns_it(client) -> None:
    response = client.post("/api/v1/settings/ai/providers", json=_create_payload())
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["api_key_set"] is True
    assert body["name"] == "openai-main"
    assert body["daily_budget_usd"] == 2.5
    # The default model is auto-registered so cost accounting has a row.
    assert [m["model_name"] for m in body["models"]] == ["gpt-4o-mini"]

    # The secret must not appear anywhere in the serialized response.
    assert "sk-super-secret-value" not in response.text
    assert body["key_masked"].startswith("sk-s")
    assert set(body["key_masked"]) - set("sk-") == {"*"} or "*" in body["key_masked"]


def test_created_key_is_recoverable_and_encrypted_at_rest(client, db_session) -> None:
    client.post("/api/v1/settings/ai/providers", json=_create_payload())
    listed = client.get("/api/v1/settings/ai/providers").json()
    assert len(listed["providers"]) == 1
    provider_id = listed["providers"][0]["id"]

    # The API surfaces only a mask, but the service layer decrypts the value it
    # actually needs. Assert both: ciphertext on disk, plaintext on decrypt.
    row = db_session.get(AIProvider, provider_id)
    assert row is not None
    stored = row.api_key_encrypted or ""
    assert "sk-super-secret-value" not in stored
    assert decrypt_secret(stored) == "sk-super-secret-value"


def test_duplicate_name_rejected(client) -> None:
    assert client.post("/api/v1/settings/ai/providers", json=_create_payload()).status_code == 201
    second = client.post("/api/v1/settings/ai/providers", json=_create_payload())
    assert second.status_code == 422
    assert "already exists" in second.json()["detail"]


def test_remote_plain_http_rejected_but_localhost_allowed(client) -> None:
    bad = client.post(
        "/api/v1/settings/ai/providers",
        json=_create_payload(base_url="http://api.example.com/v1"),
    )
    assert bad.status_code == 422

    ok = client.post(
        "/api/v1/settings/ai/providers",
        json=_create_payload(name="local-vllm", base_url="http://localhost:8000/v1"),
    )
    assert ok.status_code == 201


def test_update_without_key_keeps_it_and_empty_string_clears_it(client) -> None:
    created = client.post("/api/v1/settings/ai/providers", json=_create_payload()).json()
    provider_id = created["id"]

    # Omit api_key -> keep the stored one.
    kept = client.put(
        f"/api/v1/settings/ai/providers/{provider_id}",
        json={"daily_budget_usd": 9.0},
    )
    assert kept.status_code == 200
    assert kept.json()["api_key_set"] is True
    assert kept.json()["daily_budget_usd"] == 9.0

    # Empty string -> clear the key.
    cleared = client.put(
        f"/api/v1/settings/ai/providers/{provider_id}",
        json={"api_key": ""},
    )
    assert cleared.status_code == 200
    assert cleared.json()["api_key_set"] is False
    assert cleared.json()["key_masked"] == ""


def test_update_missing_provider_404(client) -> None:
    response = client.put("/api/v1/settings/ai/providers/999", json={"daily_budget_usd": 1.0})
    assert response.status_code == 404


def test_delete_is_refused_when_task_history_exists(client, db_session) -> None:
    from app.domain.models import AITask

    created = client.post("/api/v1/settings/ai/providers", json=_create_payload()).json()
    provider_id = created["id"]
    db_session.add(
        AITask(
            task_type="signal_explanation",
            provider_id=provider_id,
            prompt_name="signal_explain",
            prompt_version="1.0.0",
            input_hash="h" * 64,
            status="completed",
        )
    )
    db_session.commit()

    refused = client.delete(f"/api/v1/settings/ai/providers/{provider_id}")
    assert refused.status_code == 409
    assert "deactivate" in refused.json()["detail"]

    # Deactivating keeps the audit trail intact.
    deactivated = client.put(
        f"/api/v1/settings/ai/providers/{provider_id}", json={"is_active": False}
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False


def test_delete_removes_unused_provider(client) -> None:
    created = client.post("/api/v1/settings/ai/providers", json=_create_payload()).json()
    provider_id = created["id"]
    response = client.delete(f"/api/v1/settings/ai/providers/{provider_id}")
    assert response.status_code == 200
    assert client.get("/api/v1/settings/ai/providers").json()["providers"] == []


def test_delete_takes_the_default_model_row_with_it(client, db_session) -> None:
    """The ``default_model`` is not a referrer: it is a row this delete owns.

    Creating a provider auto-registers its default model (`create_provider`), so
    the model row is the provider's own child -- the cascade is the intended
    route for a model that no call ever used.
    """
    from sqlalchemy import select

    from app.domain.models import AIModel

    created = client.post("/api/v1/settings/ai/providers", json=_create_payload()).json()
    provider_id = created["id"]
    assert db_session.scalar(select(AIModel).where(AIModel.provider_id == provider_id)) is not None

    assert client.delete(f"/api/v1/settings/ai/providers/{provider_id}").status_code == 200

    assert db_session.scalar(select(AIModel).where(AIModel.provider_id == provider_id)) is None


def test_a_deactivated_provider_without_history_still_deletes(client) -> None:
    """Deactivating is not a lock: it changes nothing about whether a delete is allowed."""
    created = client.post("/api/v1/settings/ai/providers", json=_create_payload()).json()
    provider_id = created["id"]
    off = client.put(f"/api/v1/settings/ai/providers/{provider_id}", json={"is_active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False

    assert client.delete(f"/api/v1/settings/ai/providers/{provider_id}").status_code == 200
    assert client.get("/api/v1/settings/ai/providers").json()["providers"] == []


def test_a_refused_delete_keeps_the_provider_model_and_history(client, db_session) -> None:
    """The 409 must be a refusal, not a partial delete that dropped the audit trail."""
    from sqlalchemy import func, select

    from app.domain.models import AIModel, AITask

    created = client.post("/api/v1/settings/ai/providers", json=_create_payload()).json()
    provider_id = created["id"]
    db_session.add(
        AITask(
            task_type="signal_explanation",
            provider_id=provider_id,
            prompt_name="signal_explain",
            prompt_version="1.0.0",
            input_hash="h" * 64,
            status="completed",
        )
    )
    db_session.commit()

    assert client.delete(f"/api/v1/settings/ai/providers/{provider_id}").status_code == 409

    listed = client.get("/api/v1/settings/ai/providers").json()["providers"]
    assert [p["id"] for p in listed] == [provider_id]
    assert db_session.scalar(select(AIModel).where(AIModel.provider_id == provider_id)) is not None
    tasks = db_session.scalar(
        select(func.count()).select_from(AITask).where(AITask.provider_id == provider_id)
    )
    assert tasks == 1


def test_audit_records_provider_changes_without_secrets(client) -> None:
    client.post("/api/v1/settings/ai/providers", json=_create_payload())
    audit = client.get("/api/v1/audit/logs").json()
    events = [e for e in audit["events"] if e["event_type"].startswith("ai_provider")]
    assert events
    assert "sk-super-secret-value" not in str(audit)


def test_creating_provider_activates_the_ai_layer(client) -> None:
    """The whole point: before this, explanations were impossible."""
    assert client.get("/api/v1/ai/status").json()["configured"] is False
    client.post("/api/v1/settings/ai/providers", json=_create_payload())
    status = client.get("/api/v1/ai/status").json()
    assert status["configured"] is True
    assert status["provider_name"] == "openai-main"
    assert status["model"] == "gpt-4o-mini"
    assert status["daily_budget_usd"] == 2.5


def test_test_connection_against_unreachable_host_returns_ok_false(client) -> None:
    """Network failure must be a reported result, not a 500."""
    response = client.post(
        "/api/v1/settings/ai/providers/test",
        json={"base_url": "http://127.0.0.1:9/v1", "api_key": "sk-x"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "sk-x" not in response.text


def test_test_connection_rejects_remote_http(client) -> None:
    response = client.post(
        "/api/v1/settings/ai/providers/test",
        json={"base_url": "http://api.example.com/v1", "api_key": "sk-x"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "localhost" in response.json()["detail"]


class _FakeResponse:
    def __init__(self, status_code: int, payload: object = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def test_service_connection_maps_status_codes(monkeypatch) -> None:
    import httpx

    cases = [
        (401, None, False, "unauthorized"),
        (404, None, False, "/models endpoint"),
        (500, None, False, "HTTP 500"),
        (200, {"data": [{"id": "m-a"}, {"id": "m-b"}]}, True, "2 model"),
    ]
    for status, payload, expect_ok, needle in cases:
        monkeypatch.setattr(
            httpx, "get", lambda *a, _s=status, _p=payload, **k: _FakeResponse(_s, _p)
        )
        result = probe_connection("https://api.example.com/v1", "sk-secret")
        assert result["ok"] is expected_bool(expect_ok), (status, result)
        assert needle in result["detail"]
        assert "sk-secret" not in str(result)


def expected_bool(value: bool) -> bool:
    return value


def test_service_layer_round_trip(db_session) -> None:
    provider = create_provider(
        db_session,
        name="svc",
        base_url="https://api.example.com/v1",
        api_key="sk-abc",
        default_model="m1",
        daily_budget_usd=1.0,
    )
    assert provider.id is not None
    listed = list_providers(db_session)
    assert listed[0]["name"] == "svc"
    assert listed[0]["api_key_set"] is True

    update_provider(db_session, provider.id, daily_budget_usd=3.0, name="svc2")
    assert list_providers(db_session)[0]["daily_budget_usd"] == 3.0

    delete_provider(db_session, provider.id)
    assert list_providers(db_session) == []


def test_service_rejects_missing_provider(db_session) -> None:
    with pytest.raises(ProviderConfigError):
        update_provider(db_session, 12345, daily_budget_usd=1.0)
    with pytest.raises(ProviderConfigError):
        delete_provider(db_session, 12345)
