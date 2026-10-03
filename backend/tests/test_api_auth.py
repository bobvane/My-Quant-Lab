"""Optional bearer auth for the REST API (docs/14 §4).

Default is off, so a local NAS deployment is unchanged. When ``API_AUTH_TOKEN``
is set, everything the application serves except the two health probes requires
the header — including ``/docs`` and ``/openapi.json``, which are doors like any
other (ADR-103); the bundled web container injects the token while proxying.
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


def test_the_documentation_surface_is_gated_too(client, monkeypatch) -> None:
    """`/docs` and `/openapi.json` are doors like any other (ADR-103).

    They used to answer anyone who could reach the port, which made the
    `.env.example` claim ("everything except the two health probes needs the
    token") false and handed the whole API shape to a client that never
    authenticated. The web container still presents the token for both, so the
    browser keeps its documentation.
    """
    monkeypatch.setattr(settings, "api_auth_token", TOKEN)

    assert client.get("/docs").status_code == 401
    assert client.get("/openapi.json").status_code == 401
    assert client.get("/openapi.json", headers={"Authorization": "Bearer wrong"}).status_code == 401

    header = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/docs", headers=header).status_code == 200
    assert client.get("/openapi.json", headers=header).status_code == 200


def test_an_unknown_path_is_not_a_way_around_the_token(client, monkeypatch) -> None:
    """The exemption is a list of two probes, not a prefix (ADR-103).

    `if not path.startswith("/api/v1")` was how the old middleware decided, so
    every route outside that prefix was open by construction. Anything the
    application answers must now name itself as an exception first.
    """
    monkeypatch.setattr(settings, "api_auth_token", TOKEN)
    assert client.get("/redoc").status_code in {401, 404}
    assert client.get("/api/v1/strategies").status_code == 401


def test_auth_token_is_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(api_auth_token="short")
    with pytest.raises(ValidationError):
        Settings(api_auth_token="has spaces in it")
    # A URL-safe token is accepted and trimmed.
    assert Settings(api_auth_token="  good-Token_123  ").api_auth_token == "good-Token_123"
    assert Settings(api_auth_token="").api_auth_token is None
