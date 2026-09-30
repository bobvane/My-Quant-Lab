#!/usr/bin/env python3
"""Quant Lab read-only Docker stats proxy (ADR-024).

Purpose: let the resource collector see every container on the NAS WITHOUT
giving Quant Lab the Docker socket. The socket equals host root, so it is
mounted **only into this isolated container** and every request is filtered.

Allowed (whitelist, after stripping an optional version prefix):
    GET /containers/json
    GET /containers/<id>/stats?stream=false

Everything else — POST/PUT/DELETE/PATCH, exec, build, create, start, stop,
kill, rm, images, volumes, networks, websockets, unbounded stats streams — is
answered 403 without ever reaching the Docker daemon. Requests that would
stream are refused so a client cannot hold a connection forever.

The proxy listens on TCP 9100 inside the compose `backend` network only; the
port is deliberately NOT published to the host. A shared-token header
(`X-QuantLab-Token`) adds defence in depth.

Runtime cost: stdlib only, one thread per request, ~15-25 MB RSS.
"""

from __future__ import annotations

import http.client
import os
import re
import socket
import socketserver
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DOCKER_SOCK = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")
LISTEN_PORT = int(os.environ.get("PROXY_PORT", "9100"))
TOKEN = os.environ.get("QUANTLAB_PROXY_TOKEN", "")

_VERSION_PREFIX = re.compile(r"^/v[\d.]+(?=/)")
_LIST_RE = re.compile(r"^/containers/json$")
_STATS_RE = re.compile(r"^/containers/[^/]+/stats$")


def _is_allowed(method: str, path: str, query: str) -> bool:
    if method != "GET":
        return False
    stripped = _VERSION_PREFIX.sub("", path)
    if _LIST_RE.match(stripped):
        return True
    if _STATS_RE.match(stripped):
        # one-shot=true reports cpu == precpu (zero delta, useless); omitting
        # stream=false would hold the connection open forever. Only a bounded
        # read is allowed.
        return "stream=false" in query and "one-shot=true" not in query
    return False


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTP over a Unix domain socket (the Docker socket)."""

    def __init__(self, socket_path: str) -> None:
        super().__init__("localhost")
        self._socket_path = socket_path

    def connect(self) -> None:  # type: ignore[override]
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect(self._socket_path)
        self.sock = sock


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "quantlab-ro-proxy/1.0"

    def log_message(self, fmt: str, *args: object) -> None:  # quieter NAS logs
        sys.stdout.write("[proxy] %s %s\n" % (self.command, self.path))

    def _deny(self, code: int, message: str) -> None:
        body = message.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> bool:
        if not TOKEN:
            return True
        if self.headers.get("X-QuantLab-Token") == TOKEN:
            return True
        self._deny(403, "forbidden: bad or missing X-QuantLab-Token")
        return False

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        path, _, query = self.path.partition("?")
        if not _is_allowed("GET", path, query):
            self._deny(
                403,
                "forbidden: read-only proxy allows only "
                "GET /containers/json and GET /containers/<id>/stats?stream=false",
            )
            return
        if not self._authorised():
            return

        try:
            connection = _UnixHTTPConnection(DOCKER_SOCK)
            connection.request("GET", self.path, headers={"Host": "docker"})
            upstream = connection.getresponse()
            payload = upstream.read()
            status = upstream.status
            content_type = upstream.getheader("Content-Type", "application/json")
        except Exception as exc:
            self._deny(502, f"docker socket error: {type(exc).__name__}")
            return
        finally:
            connection.close()

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_only(self) -> None:
        self._deny(403, "forbidden: read-only proxy (GET only)")

    do_POST = _read_only
    do_PUT = _read_only
    do_DELETE = _read_only
    do_PATCH = _read_only
    do_HEAD = _read_only


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    if not os.path.exists(DOCKER_SOCK):
        print(f"[proxy] FATAL: docker socket not found at {DOCKER_SOCK}", file=sys.stderr)
        return 1
    server = _Server(("0.0.0.0", LISTEN_PORT), _Handler)
    print(
        f"[proxy] read-only docker stats proxy on :{LISTEN_PORT} -> {DOCKER_SOCK}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
