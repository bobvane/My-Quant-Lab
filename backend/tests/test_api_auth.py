"""Optional bearer auth for the REST API (docs/14 §4).

Default is off, so a local NAS deployment is unchanged. When ``API_AUTH_TOKEN``
is set, everything under ``/api/v1`` except the two health probes requires the
header; the bundled web container injects it while proxying ``/api``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings

TOKEN = "s3cret-token-123"


def test_api_is_open_when_no_token_configured(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_auth_token", None)
    assert client.get("/api/v1/strategies").status_code == 200


def test_api_requires_bearer_when_token_set(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_auth_token", TOKEN)

    assert client.get("/api/v1/strategies").status_code == 401
    assert (
        client.get("/api/v1/strategies", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )
    # The scheme must be Bearer, not a bare token.
    assert client.get("/api/v1/strategies", headers={"Authorization": TOKEN}).status_code == 401

    ok = client.get("/api/v1/strategies", headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200


def test_health_probes_stay_open(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_auth_token", TOKEN)
    assert client.get("/api/v1/healthz").status_code == 200
    assert client.get("/api/v1/health").status_code == 200


def test_write_requests_are_gated(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_auth_token", TOKEN)
    denied = client.post("/api/v1/strategies", json={"name": "X"})
    assert denied.status_code == 401
    allowed = client.post(
        "/api/v1/strategies",
        json={"name": "X"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert allowed.status_code == 201


def test_openapi_is_not_gated(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_auth_token", TOKEN)
    assert client.get("/openapi.json").status_code == 200


def test_auth_token_is_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(api_auth_token="short")
    with pytest.raises(ValidationError):
        Settings(api_auth_token="has spaces in it")
    # A URL-safe token is accepted and trimmed.
    assert Settings(api_auth_token="  good-Token_123  ").api_auth_token == "good-Token_123"
    assert Settings(api_auth_token="").api_auth_token is None
