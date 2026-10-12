"""``ADMIN_TOKEN``: the administrator gate for dangerous operations (ADR-218).

V-B0 ships the gate and its configuration only — no production endpoint declares
it yet (maintenance is V-B2, export/restore is V-B3). These tests therefore bolt
the *shipped* dependency onto the *shipped* app on a probe route that stays out
of the schema, so what runs here is the real gate, the real exception handler and
the real middleware stack rather than a copy of them.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import admin_auth
from app.api.admin_auth import ADMIN_TOKEN_HEADER, require_admin_token
from app.api.main import app
from app.core.config import Settings, settings

ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = "/__admin_probe"
GOOD_TOKEN = "admin-token-1234567890"


@pytest.fixture
def admin_client():
    """The shipped app with one admin-guarded probe route bolted on.

    ``include_in_schema=False`` keeps ``/openapi.json`` — and with it every
    route-claim guard — untouched, and teardown removes the route again so no
    later test in the same session can see it. The client is not used as a
    context manager on purpose: V-B0 changes nothing that needs startup.
    """

    async def probe() -> dict[str, str]:
        return {"ok": "true"}

    app.add_api_route(
        PROBE_PATH,
        probe,
        methods=["POST", "GET"],
        dependencies=[Depends(require_admin_token)],
        include_in_schema=False,
    )
    try:
        yield TestClient(app)
    finally:
        app.router.routes = [
            route for route in app.router.routes if getattr(route, "path", None) != PROBE_PATH
        ]


def _error(response) -> dict[str, object]:
    """The API error envelope, which is the only shape the gate may use."""

    return response.json()["error"]


# --- the refusal when nothing is configured ---------------------------------


def test_an_unconfigured_admin_token_refuses_the_operation(admin_client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", None)

    response = admin_client.post(PROBE_PATH)

    assert response.status_code == 503
    assert _error(response)["code"] == "admin_token_not_configured"
    assert "ADMIN_TOKEN" in str(_error(response)["message"])
    # The operator reads this; the project's newer messages are Chinese.
    assert "管理员" in str(_error(response)["message"])


def test_an_empty_or_blank_admin_token_is_not_configured():
    assert Settings(admin_token="").admin_token is None
    assert Settings(admin_token="   ").admin_token is None
    assert Settings(admin_token=None).admin_token is None


def test_the_refusal_is_not_the_middleware_one(admin_client, monkeypatch):
    """503 + a configured-token code, not the generic 401 of the API token."""

    monkeypatch.setattr(settings, "admin_token", None)

    response = admin_client.post(PROBE_PATH)

    assert response.status_code != 401
    assert _error(response)["code"] != "unauthorized"
    assert set(_error(response)) == {"code", "message", "details"}


# --- the matching / missing / wrong token -----------------------------------


def test_the_matching_admin_token_is_accepted(admin_client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", GOOD_TOKEN)

    response = admin_client.post(PROBE_PATH, headers={ADMIN_TOKEN_HEADER: GOOD_TOKEN})

    assert response.status_code == 200
    assert response.json() == {"ok": "true"}


def test_a_missing_admin_token_is_401(admin_client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", GOOD_TOKEN)

    response = admin_client.post(PROBE_PATH)

    assert response.status_code == 401
    assert _error(response)["code"] == "admin_token_invalid"
    assert response.headers["www-authenticate"] == ADMIN_TOKEN_HEADER


def test_a_wrong_admin_token_is_401(admin_client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", GOOD_TOKEN)

    response = admin_client.post(PROBE_PATH, headers={ADMIN_TOKEN_HEADER: "admin-token-1234567891"})

    assert response.status_code == 401
    assert _error(response)["code"] == "admin_token_invalid"


def test_the_header_name_is_matched_case_insensitively(admin_client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", GOOD_TOKEN)

    response = admin_client.post(PROBE_PATH, headers={"x-admin-token": GOOD_TOKEN})

    assert response.status_code == 200


def test_surrounding_whitespace_in_the_header_is_ignored(admin_client, monkeypatch):
    """The configured value is stripped on load, so the presented one is too."""

    monkeypatch.setattr(settings, "admin_token", GOOD_TOKEN)

    response = admin_client.post(PROBE_PATH, headers={ADMIN_TOKEN_HEADER: f"  {GOOD_TOKEN}  "})

    assert response.status_code == 200


# --- carriers we refuse ------------------------------------------------------


def test_the_token_is_never_taken_from_a_query_body_cookie_or_other_header(
    admin_client, monkeypatch
):
    monkeypatch.setattr(settings, "admin_token", GOOD_TOKEN)

    query = admin_client.post(f"{PROBE_PATH}?admin_token={GOOD_TOKEN}")
    body = admin_client.post(PROBE_PATH, json={"admin_token": GOOD_TOKEN})
    cookie = admin_client.post(PROBE_PATH, cookies={"admin_token": GOOD_TOKEN})
    other = admin_client.post(PROBE_PATH, headers={"Admin-Token": GOOD_TOKEN})

    for response in (query, body, cookie, other):
        assert response.status_code == 401, response.text
        assert _error(response)["code"] == "admin_token_invalid"


def test_the_api_token_does_not_authorize_an_admin_operation(admin_client, monkeypatch):
    """The two credentials are not interchangeable, in either direction."""

    api_token = "api-token-1234567890"
    monkeypatch.setattr(settings, "api_auth_token", api_token)
    monkeypatch.setattr(settings, "admin_token", GOOD_TOKEN)

    # Right Bearer token (the middleware lets it through), wrong admin token.
    interchangeable = admin_client.post(
        PROBE_PATH,
        headers={"Authorization": f"Bearer {api_token}", ADMIN_TOKEN_HEADER: api_token},
    )
    assert interchangeable.status_code == 401
    assert _error(interchangeable)["code"] == "admin_token_invalid"

    # No Bearer token at all: the middleware answers first, in its own shape.
    unauthenticated = admin_client.post(PROBE_PATH, headers={ADMIN_TOKEN_HEADER: GOOD_TOKEN})
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "unauthorized"


def test_the_gate_never_names_the_api_token_as_a_fallback():
    """A structural pin on the "no fallback" rule of ADR-218."""

    source = inspect.getsource(admin_auth)

    assert "api_auth_token" not in source


# --- configuration validation ------------------------------------------------


@pytest.mark.parametrize("value", ["short", "has spaces in it", "with\nnewline"])
def test_a_malformed_admin_token_refuses_to_load(value):
    with pytest.raises(ValidationError) as excinfo:
        Settings(admin_token=value)

    assert "ADMIN_TOKEN" in str(excinfo.value)


def test_a_good_admin_token_is_stripped():
    assert Settings(admin_token=f"  {GOOD_TOKEN}  ").admin_token == GOOD_TOKEN


def test_the_gate_reads_the_token_on_every_call(admin_client, monkeypatch):
    """Restarting with a new .env must take effect; nothing is cached at import."""

    monkeypatch.setattr(settings, "admin_token", GOOD_TOKEN)
    accepted = admin_client.post(PROBE_PATH, headers={ADMIN_TOKEN_HEADER: GOOD_TOKEN})
    assert accepted.status_code == 200

    monkeypatch.setattr(settings, "admin_token", None)
    refused = admin_client.post(PROBE_PATH, headers={ADMIN_TOKEN_HEADER: GOOD_TOKEN})
    assert refused.status_code == 503


# --- V-B0 ships no endpoint of its own --------------------------------------


def test_v_b0_adds_no_admin_guarded_endpoint():
    paths = set(app.openapi()["paths"])
    guarded = [
        route.path
        for route in app.routes
        for dependency in getattr(route, "dependencies", [])
        if getattr(dependency, "dependency", None) is require_admin_token
    ]

    assert guarded == []
    assert PROBE_PATH not in paths
    assert not [path for path in paths if "maintenance" in path or "backup" in path]


# --- configuration and documentation, where an operator looks ----------------


def test_the_token_is_configurable_in_both_deployment_files():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "\nADMIN_TOKEN=\n" in example
    assert "ADMIN_TOKEN: ${ADMIN_TOKEN:-}" in compose


def test_the_proxy_never_injects_the_admin_token():
    """The whole point of a second token: the bundle must not add it for you."""

    for name in ("docker/app.nginx.conf", "docker/entrypoint.sh"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "Admin-Token" not in text, name
        assert "ADMIN_TOKEN" not in text, name


def test_the_operator_is_told_what_the_admin_token_is_for():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    security = (ROOT / "docs/14_SECURITY_LICENSE.md").read_text(encoding="utf-8")

    for text in (readme, security):
        assert "ADMIN_TOKEN" in text
        assert "X-Admin-Token" in text
