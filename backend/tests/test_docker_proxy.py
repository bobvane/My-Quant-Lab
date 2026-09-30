"""Read-only Docker proxy security tests (ADR-024).

The whitelist function ``_is_allowed`` is the security boundary and runs
everywhere (pure, cross-platform). The full HTTP round-trip over a Unix socket
additionally proves pass-through wiring; it is skipped on platforms without
``socket.AF_UNIX`` (e.g. Windows) because the proxy itself only ever runs on
Linux, where CI exercises those cases.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import socket
import threading

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
# The whitelist matrix: what reaches the Docker daemon vs what is refused.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("method", "path", "query"),
    [
        ("GET", "/containers/json", "all=1"),
        ("GET", "/containers/json", ""),
        ("GET", "/v1.43/containers/json", "all=1"),
        ("GET", "/containers/abc123/stats", "stream=false"),
        ("GET", "/v1.47/containers/abc123/stats", "stream=false&one-shot=false"),
    ],
)
def test_whitelisted_requests_pass(method: str, path: str, query: str) -> None:
    assert _allowed(method, path, query) is True


@pytest.mark.parametrize(
    ("method", "path", "query"),
    [
        # writes / lifecycle
        ("POST", "/containers/json", ""),
        ("DELETE", "/containers/abc123", ""),
        ("POST", "/containers/abc123/exec", ""),
        ("POST", "/containers/abc123/start", ""),
        ("POST", "/containers/abc123/stop", ""),
        ("POST", "/containers/abc123/kill", ""),
        ("POST", "/images/create", ""),
        ("POST", "/build", ""),
        ("POST", "/volumes/create", ""),
        ("POST", "/networks/create", ""),
        ("POST", "/swarm/init", ""),
        ("POST", "/secrets/create", ""),
        # reads outside the whitelist
        ("GET", "/events", ""),
        ("GET", "/images/json", ""),
        ("GET", "/volumes", ""),
        ("GET", "/networks", ""),
        ("GET", "/info", ""),
        ("GET", "/version", ""),
        ("GET", "/containers/abc123/json", ""),
        ("GET", "/containers/abc123/logs", ""),
        ("GET", "/containers/abc123/exec/start", ""),
        ("GET", "/secrets", ""),
        ("GET", "/swarm", ""),
        ("GET", "/../etc/passwd", ""),
        # streaming stats refused
        ("GET", "/containers/abc123/stats", ""),
        ("GET", "/containers/abc123/stats", "one-shot=true"),
        # wrong method on a whitelisted path
        ("POST", "/containers/json", ""),
        ("HEAD", "/containers/json", ""),
    ],
)
def test_non_whitelisted_requests_refused(method: str, path: str, query: str) -> None:
    assert _allowed(method, path, query) is False


# --------------------------------------------------------------------------- #
# token authorisation
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


# --------------------------------------------------------------------------- #
# Full HTTP round trip over a real Unix socket (POSIX only; CI runs these).
# --------------------------------------------------------------------------- #
unix_ok = hasattr(socket, "AF_UNIX")


def _build_sockets(tmp_path: pathlib.Path):
    """Start the real proxy (TCP) + a fake Docker upstream (Unix socket).

    The proxy connects to the fake upstream via ``_UnixHTTPConnection``, which
    is the exact wiring used in production. The test client talks to the proxy
    over plain HTTP on a random port.
    """

    from socketserver import BaseRequestHandler, TCPServer, ThreadingMixIn

    class _FakeHandler(BaseRequestHandler):
        def handle(self) -> None:
            try:
                data = self.request.recv(65536).decode("utf-8", "replace")
                parts = (data.splitlines()[0] if data else "").split()
                method, path = (parts + ["", ""])[:2]
                with self.server.lock:  # type: ignore[attr-defined]
                    self.server.requests.append((method, path))  # type: ignore[attr-defined]
                body = json.dumps({"data": [], "cpu": 1}).encode()
                self.request.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
                )
            except Exception:
                pass

    class _FakeUnixServer(ThreadingMixIn, TCPServer):
        """TCPServer with AF_UNIX: inherits socket creation, bind and listen."""

        address_family = socket.AF_UNIX
        daemon_threads = True
        allow_reuse_address = True

    docker_sock = tmp_path / "docker.sock"

    fake = _FakeUnixServer(str(docker_sock), _FakeHandler)
    fake.requests: list[tuple[str, str]] = []  # type: ignore[attr-defined]
    proxy_module.TOKEN = ""
    proxy_module.DOCKER_SOCK = str(docker_sock)

    # The proxy itself is a TCP server (ThreadingHTTPServer); port 0 lets the
    # OS pick a free port, which we then read back from server_address.
    proxy = proxy_module._Server(("127.0.0.1", 0), proxy_module._Handler)
    proxy_port = proxy.server_address[1]

    for _srv in (fake, proxy):
        threading.Thread(target=_srv.serve_forever, daemon=True).start()
    return {"fake": fake, "proxy": proxy, "proxy_port": proxy_port}


def _http_to_proxy(port: int, method: str, path: str) -> tuple[int, str]:
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", port)
    connection.request(method, path, headers={"Host": "proxy"})
    response = connection.getresponse()
    body = response.read().decode("utf-8", "replace")
    status = response.status
    connection.close()
    return status, body


@pytest.mark.skipif(not unix_ok, reason="platform without socket.AF_UNIX")
def test_roundtrip_whitelisted_list(tmp_path: pathlib.Path) -> None:
    env = _build_sockets(tmp_path)
    try:
        status, body = _http_to_proxy(env["proxy_port"], "GET", "/containers/json?all=1")
        assert status == 200
        assert "data" in body
    finally:
        env["proxy"].shutdown()
        env["fake"].shutdown()
        env["proxy"].server_close()
        env["fake"].server_close()


@pytest.mark.skipif(not unix_ok, reason="platform without socket.AF_UNIX")
def test_roundtrip_denied_request_never_reaches_upstream(tmp_path: pathlib.Path) -> None:
    env = _build_sockets(tmp_path)
    try:
        status, body = _http_to_proxy(env["proxy_port"], "DELETE", "/containers/abc123")
        assert status == 403
        assert env["fake"].requests == []  # upstream untouched
    finally:
        env["proxy"].shutdown()
        env["fake"].shutdown()
        env["proxy"].server_close()
        env["fake"].server_close()
