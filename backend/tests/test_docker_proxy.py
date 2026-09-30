"""Read-only Docker proxy security tests (ADR-024).

The whitelist function ``_is_allowed`` is the security boundary. It is a pure
function tested here against every method/path/query combination that matters.

The actual HTTP wiring (server startup, Unix socket handling, proxy
pass-through) is verified by the CI compose smoke test, which boots the real
proxy container and asserts its healthcheck passes.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_spec = importlib.util.spec_from_file_location(
    "readonly_proxy",
    str(pathlib.Path(__file__).resolve().parents[2] / "docker" / "readonly_proxy.py"),
)
proxy_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proxy_module)  # type: ignore[union-attr]


def _allowed(method: str, path: str, query: str) -> bool:
    return proxy_module._is_allowed(method, path, query)


# --------------------------------------------------------------------------- #
# Whitelisted: these DO reach the Docker daemon
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("method", "path", "query"),
    [
        ("GET", "/containers/json", "all=1"),
        ("GET", "/containers/json", ""),
        ("GET", "/v1.43/containers/json", "all=1"),
        ("GET", "/v1.47/containers/json", ""),
        ("GET", "/containers/abc123/stats", "stream=false"),
        ("GET", "/containers/abc123/stats", "stream=false&one-shot=false"),
        ("GET", "/v1.47/containers/abc123/stats", "stream=false"),
    ],
)
def test_whitelisted_requests_pass(method: str, path: str, query: str) -> None:
    assert _allowed(method, path, query) is True


# --------------------------------------------------------------------------- #
# Refused: these get 403 and never reach the Docker daemon
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("method", "path", "query"),
    [
        # writes
        ("POST", "/containers/json", ""),
        ("PUT", "/containers/abc123", ""),
        ("DELETE", "/containers/abc123", ""),
        ("PATCH", "/containers/abc123", ""),
        # lifecycle
        ("POST", "/containers/abc123/start", ""),
        ("POST", "/containers/abc123/stop", ""),
        ("POST", "/containers/abc123/restart", ""),
        ("POST", "/containers/abc123/kill", ""),
        ("POST", "/containers/abc123/wait", ""),
        # exec
        ("POST", "/containers/abc123/exec", ""),
        ("GET", "/containers/abc123/exec/start", ""),
        # images
        ("POST", "/images/create", ""),
        ("GET", "/images/json", ""),
        ("DELETE", "/images/abc123", ""),
        # volumes / networks / secrets / swarm
        ("GET", "/volumes", ""),
        ("POST", "/volumes/create", ""),
        ("GET", "/networks", ""),
        ("POST", "/networks/create", ""),
        ("GET", "/secrets", ""),
        ("POST", "/secrets/create", ""),
        ("GET", "/swarm", ""),
        ("POST", "/swarm/init", ""),
        # non-whitelisted reads
        ("GET", "/events", ""),
        ("GET", "/info", ""),
        ("GET", "/version", ""),
        ("GET", "/containers/abc123/json", ""),
        ("GET", "/containers/abc123/logs", ""),
        ("GET", "/containers/abc123/export", ""),
        ("GET", "/nodes", ""),
        ("GET", "/tasks", ""),
        ("GET", "/plugins", ""),
        # streaming / zero-delta stats
        ("GET", "/containers/abc123/stats", ""),
        ("GET", "/containers/abc123/stats", "one-shot=true"),
        ("GET", "/containers/abc123/stats", "stream=false&one-shot=true"),
        # container inspect
        ("GET", "/containers/abc123/json", ""),
        # wrong method on whitelisted path
        ("POST", "/containers/json", ""),
        ("HEAD", "/containers/json", ""),
        ("OPTIONS", "/containers/json", ""),
        # path traversal
        ("GET", "/../etc/passwd", ""),
    ],
)
def test_non_whitelisted_requests_refused(method: str, path: str, query: str) -> None:
    assert _allowed(method, path, query) is False


# --------------------------------------------------------------------------- #
# Token authorisation
# --------------------------------------------------------------------------- #
class _Mini:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers
        self.denied: tuple[int, str] | None = None

    def _deny(self, code: int, msg: str) -> None:
        self.denied = (code, msg)


def test_token_enforced_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(proxy_module, "TOKEN", "secret-token")
    assert proxy_module._Handler._authorised(_Mini({"X-QuantLab-Token": "secret-token"})) is True
    assert proxy_module._Handler._authorised(_Mini({})) is False
    assert proxy_module._Handler._authorised(_Mini({"X-QuantLab-Token": "wrong"})) is False


def test_token_not_required_when_unset(monkeypatch) -> None:
    monkeypatch.setattr(proxy_module, "TOKEN", "")
    assert proxy_module._Handler._authorised(_Mini({})) is True
