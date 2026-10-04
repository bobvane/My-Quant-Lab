"""Step 3.2: fetching through the guard, over a pinned connection.

Every test here talks to a real local HTTP server, but the URL it fetches carries
the name ``example.test`` — the guard seam is what maps that name to ``127.0.0.1``.
So the tests exercise the real socket path (real httpcore, real timeouts, real size
accounting) while the guard's own decisions stay covered by ``test_source_ssrf.py``.
"""

from __future__ import annotations

import contextlib
import http.server
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.sources import fetch as fetch_module
from app.sources.fetch import (
    FETCH_ERROR_CODES,
    MAX_REDIRECTS,
    RobotsVerdict,
    SourceFetchError,
    check_robots,
    fetch_document,
    parse_robots,
    retrieve_document,
    robots_agent,
    robots_allows,
    robots_url,
)
from app.sources.guard import CheckedURL, SourceBlocked

HOST = "example.test"
HTML = {"content-type": "text/html; charset=utf-8"}
PLAIN = {"content-type": "text/plain; charset=utf-8"}
PDF = {"content-type": "application/pdf"}


def site(
    routes: dict[str, tuple[int, dict[str, str], bytes]],
    *,
    delay: float = 0.0,
    seen: list[str] | None = None,
    hosts: list[str] | None = None,
) -> type[http.server.BaseHTTPRequestHandler]:
    """A one-route-table HTTP server; ``delay`` makes it slow on purpose."""

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            if seen is not None:
                seen.append(self.path)
            if hosts is not None:
                hosts.append(self.headers.get("host", ""))
            if delay:
                time.sleep(delay)
            status, headers, body = routes.get(
                self.path,
                (404, PLAIN, b"not found"),
            )
            try:
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args: object) -> None:
            return

    return Handler


@contextlib.contextmanager
def serving(handler: type[http.server.BaseHTTPRequestHandler]) -> Iterator[int]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def url_for(port: int, path: str) -> str:
    return f"http://{HOST}:{port}{path}"


def local_guard(raw: str, *, resolver: object = None, ports: object = None) -> CheckedURL:
    """The guard seam: the name ``example.test`` is pinned to the local server."""
    parts = urlsplit(raw)
    return CheckedURL(
        url=raw,
        scheme=parts.scheme,
        host=parts.hostname or "",
        port=parts.port or 80,
        addresses=("127.0.0.1",),
    )


def recording_guard(
    *,
    blocked: tuple[str, ...] = (),
    seen: list[str] | None = None,
) -> object:
    def guard(raw: str, *, resolver: object = None, ports: object = None) -> CheckedURL:
        if seen is not None:
            seen.append(raw)
        for marker in blocked:
            if marker in raw:
                raise SourceBlocked("address_not_allowed", f"refusing {raw}")
        return local_guard(raw, resolver=resolver, ports=ports)

    return guard


def unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_a_plain_page_comes_back_with_its_metadata() -> None:
    routes = {"/page": (200, HTML, b"<html><body>hello</body></html>")}
    with serving(site(routes)) as port:
        document = fetch_document(url_for(port, "/page"), guard=local_guard)

    assert document.url == url_for(port, "/page")
    assert document.status_code == 200
    assert document.content_type == "text/html; charset=utf-8"
    assert document.body == b"<html><body>hello</body></html>"
    assert document.size_bytes == 31
    assert document.redirects == ()
    assert document.retrieved_at.tzinfo is not None
    assert ("content-type", "text/html; charset=utf-8") in document.headers


def test_the_request_goes_to_the_validated_address_under_the_original_hostname() -> None:
    hosts: list[str] = []
    routes = {"/page": (200, HTML, b"ok")}
    with serving(site(routes, hosts=hosts)) as port:
        fetch_document(url_for(port, "/page"), guard=local_guard)

    # The TCP connection went to 127.0.0.1 (the pinned address) while the wire
    # request still named the host it was checked under.
    assert hosts == [f"{HOST}:{port}"]


def test_a_redirect_is_followed_and_recorded() -> None:
    routes = {
        "/old": (302, {"location": "/new"}, b""),
        "/new": (200, HTML, b"moved here"),
    }
    with serving(site(routes)) as port:
        document = fetch_document(url_for(port, "/old"), guard=local_guard)

    assert document.url == url_for(port, "/new")
    assert document.body == b"moved here"
    assert document.redirects == (url_for(port, "/old"),)


def test_every_redirect_target_goes_through_the_guard_again() -> None:
    seen: list[str] = []
    paths: list[str] = []
    routes = {
        "/old": (302, {"location": f"http://{HOST}/super-secret"}, b""),
    }
    guard = recording_guard(blocked=("/super-secret",), seen=seen)
    with serving(site(routes, seen=paths)) as port:
        target = url_for(port, "/old")
        with pytest.raises(SourceBlocked) as raised:
            fetch_document(target, guard=guard)

    assert raised.value.code == "address_not_allowed"
    assert seen[0] == target
    # The forbidden hop is a URL, not the fetched path: guard sees an absolute URL.
    assert seen[-1].endswith("/super-secret")
    assert paths == ["/old"]  # nothing was ever connected to the blocked target


def test_a_redirect_loop_is_cut_off_after_the_redirect_limit() -> None:
    seen: list[str] = []
    routes = {"/loop": (302, {"location": "/loop"}, b"")}
    guard = recording_guard(seen=seen)
    with serving(site(routes)) as port, pytest.raises(SourceFetchError) as raised:
        fetch_document(url_for(port, "/loop"), guard=guard)

    assert raised.value.code == "too_many_redirects"
    assert len(seen) == MAX_REDIRECTS + 1


def test_a_redirect_without_a_location_header_is_refused() -> None:
    routes = {"/nowhere": (302, {}, b"")}
    with serving(site(routes)) as port, pytest.raises(SourceFetchError) as raised:
        fetch_document(url_for(port, "/nowhere"), guard=local_guard)

    assert raised.value.code == "redirect_without_location"


def test_a_response_that_is_not_a_success_is_refused() -> None:
    routes = {"/broken": (500, PLAIN, b"boom")}
    with serving(site(routes)) as port, pytest.raises(SourceFetchError) as raised:
        fetch_document(url_for(port, "/broken"), guard=local_guard)

    assert raised.value.code == "status_not_ok"
    assert raised.value.status == 500
    assert "status_not_ok" in FETCH_ERROR_CODES


def test_a_content_type_this_version_cannot_read_is_refused() -> None:
    routes = {"/archive": (200, {"content-type": "application/zip"}, b"PK\x03\x04")}
    with serving(site(routes)) as port, pytest.raises(SourceFetchError) as raised:
        fetch_document(url_for(port, "/archive"), guard=local_guard)

    assert raised.value.code == "content_type_not_allowed"
    assert "application/zip" in raised.value.message


def test_a_response_without_a_content_type_is_refused() -> None:
    routes = {"/opaque": (200, {}, b"who knows")}
    with serving(site(routes)) as port, pytest.raises(SourceFetchError) as raised:
        fetch_document(url_for(port, "/opaque"), guard=local_guard)

    assert raised.value.code == "content_type_not_allowed"


def test_an_empty_body_is_refused() -> None:
    routes = {"/empty": (200, HTML, b"")}
    with serving(site(routes)) as port, pytest.raises(SourceFetchError) as raised:
        fetch_document(url_for(port, "/empty"), guard=local_guard)

    assert raised.value.code == "empty_response"


def test_a_response_larger_than_the_limit_is_refused_while_reading() -> None:
    routes = {"/huge": (200, HTML, b"x" * (1024 * 1024))}
    with serving(site(routes)) as port, pytest.raises(SourceFetchError) as raised:
        fetch_document(url_for(port, "/huge"), guard=local_guard, max_bytes=4096)

    assert raised.value.code == "response_too_large"
    assert "4096" in raised.value.message


def test_a_slow_response_times_out() -> None:
    routes = {"/slow": (200, HTML, b"eventually")}
    with serving(site(routes, delay=2.0)) as port, pytest.raises(SourceFetchError) as raised:
        fetch_document(url_for(port, "/slow"), guard=local_guard, read_timeout=0.2)

    assert raised.value.code == "timeout"


def test_a_connection_nobody_answers_is_bounded_and_reported() -> None:
    # Whether a closed port is refused at once or silently dropped depends on the
    # platform and the firewall, so the assertion is what we actually promise: the
    # fetch comes back quickly, with a code from the documented set.
    port = unused_port()
    started = time.monotonic()
    with pytest.raises(SourceFetchError) as raised:
        fetch_document(url_for(port, "/page"), guard=local_guard, connect_timeout=0.5)

    assert raised.value.code in {"timeout", "connection_failed"}
    assert raised.value.code in FETCH_ERROR_CODES
    assert time.monotonic() - started < 5.0


def test_a_connection_that_drops_the_body_is_reported_not_shortened() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = int(server.getsockname()[1])

    def serve_once() -> None:
        connection, _ = server.accept()
        connection.recv(4096)
        connection.sendall(
            b"HTTP/1.1 200 OK\r\ncontent-type: text/html\r\ncontent-length: 10\r\n\r\n"
        )
        connection.close()
        server.close()

    threading.Thread(target=serve_once, daemon=True).start()
    with pytest.raises(SourceFetchError) as raised:
        fetch_document(url_for(port, "/page"), guard=local_guard, read_timeout=2.0)

    # A body that stops early is a failure, never a short document; httpcore calls
    # this a protocol error, and anything else the socket reports is a broken
    # connection. Both are in the documented set.
    assert raised.value.code in {"protocol_failed", "connection_failed"}
    assert raised.value.code in FETCH_ERROR_CODES


def test_the_environment_proxy_cannot_take_over_the_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = {"/page": (200, HTML, b"direct")}
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    with serving(site(routes)) as port:
        document = fetch_document(url_for(port, "/page"), guard=local_guard)

    assert document.body == b"direct"


def test_the_fetcher_never_asks_the_environment_for_a_proxy() -> None:
    source = Path(fetch_module.__file__).read_text(encoding="utf-8")

    # No keyword that would let httpx-style code read HTTP_PROXY/HTTPS_PROXY, and no
    # client that does it for us: this module only speaks httpcore directly.
    assert "trust_env=" not in source
    assert "getproxies" not in source
    assert "import httpx" not in source


def test_robots_url_is_the_origin_plus_robots_txt() -> None:
    assert (
        robots_url(local_guard("https://example.test/deep/page?q=1"))
        == "https://example.test/robots.txt"
    )
    assert (
        robots_url(local_guard("http://example.test:8080/page"))
        == "http://example.test:8080/robots.txt"
    )


def test_the_robots_agent_is_the_product_token() -> None:
    assert robots_agent("MyQuantLab/2.1 (+research source ingestion)") == "MyQuantLab"
    assert robots_agent("") == "MyQuantLab"


def test_robots_requests_go_through_the_guard() -> None:
    seen: list[str] = []
    routes = {"/robots.txt": (200, PLAIN, b"User-agent: *\nAllow: /\n")}
    guard = recording_guard(blocked=("robots.txt",), seen=seen)
    with serving(site(routes)) as port, pytest.raises(SourceBlocked):
        check_robots(local_guard(url_for(port, "/page")), guard=guard)

    assert seen and seen[0].endswith("/robots.txt")


def test_a_robots_redirect_is_re_checked() -> None:
    seen: list[str] = []
    routes = {
        "/robots.txt": (302, {"location": "/robots-really.txt"}, b""),
        "/robots-really.txt": (200, PLAIN, b"User-agent: *\nAllow: /\n"),
    }
    guard = recording_guard(blocked=("robots-really",), seen=seen)
    with serving(site(routes)) as port, pytest.raises(SourceBlocked):
        check_robots(local_guard(url_for(port, "/page")), guard=guard)

    assert seen[-1].endswith("/robots-really.txt")


def test_robots_allows_when_the_site_has_no_robots_txt() -> None:
    routes = {"/page": (200, HTML, b"fine")}
    with serving(site(routes)) as port:
        verdict = check_robots(local_guard(url_for(port, "/page")), guard=local_guard)

    assert verdict.checked is False
    assert verdict.allowed is True
    assert verdict.status_code == 404
    assert "no robots.txt" in verdict.reason


def test_robots_disallows_a_path() -> None:
    routes = {
        "/robots.txt": (200, PLAIN, b"User-agent: *\nDisallow: /private\n"),
        "/private/thing": (200, HTML, b"secret"),
    }
    with serving(site(routes)) as port:
        verdict = check_robots(
            local_guard(url_for(port, "/private/thing")),
            guard=local_guard,
        )

    assert verdict.checked is True
    assert verdict.allowed is False
    assert verdict.matched_rule == "/private"
    assert "disallows" in verdict.reason


def test_a_robots_file_we_cannot_read_is_not_a_verdict() -> None:
    routes = {"/robots.txt": (500, PLAIN, b"oops")}
    with serving(site(routes)) as port, pytest.raises(SourceFetchError) as raised:
        check_robots(local_guard(url_for(port, "/page")), guard=local_guard)

    assert raised.value.code == "robots_unavailable"
    assert "robots_unavailable" in FETCH_ERROR_CODES


def test_a_robots_file_that_times_out_is_not_a_verdict() -> None:
    routes = {"/robots.txt": (200, PLAIN, b"User-agent: *\nAllow: /\n")}
    with serving(site(routes, delay=2.0)) as port, pytest.raises(SourceFetchError) as raised:
        check_robots(
            local_guard(url_for(port, "/page")),
            guard=local_guard,
            read_timeout=0.2,
        )

    assert raised.value.code == "robots_unavailable"


def test_the_robots_verdict_cannot_carry_the_file_itself() -> None:
    fields = set(RobotsVerdict.__dataclass_fields__)

    assert fields == {
        "url",
        "checked",
        "allowed",
        "reason",
        "matched_rule",
        "status_code",
        "retrieved_at",
    }


def test_retrieve_document_asks_robots_before_the_page() -> None:
    paths: list[str] = []
    routes = {
        "/robots.txt": (200, PLAIN, b"User-agent: *\nAllow: /\n"),
        "/page": (200, HTML, b"the page"),
    }
    with serving(site(routes, seen=paths)) as port:
        result = retrieve_document(url_for(port, "/page"), guard=local_guard)

    assert paths == ["/robots.txt", "/page"]
    assert result.document.body == b"the page"
    assert result.robots.checked is True
    assert result.robots.allowed is True


def test_retrieve_document_refuses_a_disallowed_page() -> None:
    paths: list[str] = []
    routes = {
        "/robots.txt": (200, PLAIN, b"User-agent: *\nDisallow: /private\n"),
        "/private/thing": (200, HTML, b"secret"),
    }
    with serving(site(routes, seen=paths)) as port, pytest.raises(SourceFetchError) as raised:
        retrieve_document(url_for(port, "/private/thing"), guard=local_guard)

    assert raised.value.code == "robots_disallowed"
    assert paths == ["/robots.txt"]


def test_retrieve_document_can_skip_robots() -> None:
    paths: list[str] = []
    routes = {"/page": (200, HTML, b"the page")}
    with serving(site(routes, seen=paths)) as port:
        result = retrieve_document(
            url_for(port, "/page"),
            guard=local_guard,
            respect_robots=False,
        )

    assert result.document.body == b"the page"
    assert result.robots.checked is False
    assert result.robots.reason == "robots.txt was not consulted"
    assert paths == ["/page"]


def test_a_pdf_content_type_is_allowed_to_arrive() -> None:
    routes = {"/paper.pdf": (200, PDF, b"%PDF-1.4 minimal")}
    with serving(site(routes)) as port:
        document = fetch_document(url_for(port, "/paper.pdf"), guard=local_guard)

    assert document.content_type == "application/pdf"


def test_robots_groups_are_parsed_by_user_agent() -> None:
    text = (
        "# a comment\n"
        "Sitemap: https://example.test/sitemap.xml\n"
        "\n"
        "User-agent: *\n"
        "Disallow: /wild\n"
        "\n"
        "User-agent: OtherBot\n"
        "User-agent: MyQuantLab\n"
        "Disallow: /mine\n"
        "Allow: /mine/open\n"
    )
    groups = parse_robots(text)

    assert groups[0][0] == ("*",)
    assert groups[0][1] == ((False, "/wild"),)
    assert groups[1][0] == ("otherbot", "myquantlab")
    assert groups[1][1] == ((False, "/mine"), (True, "/mine/open"))


def test_the_longest_matching_rule_wins() -> None:
    text = "User-agent: *\nDisallow: /\nAllow: /public/\n"

    assert robots_allows(text, path="/public/thing", agent="MyQuantLab") == (True, "/public/")
    assert robots_allows(text, path="/elsewhere", agent="MyQuantLab") == (False, "/")


def test_an_allow_wins_a_tie() -> None:
    text = "User-agent: *\nDisallow: /x\nAllow: /x\n"

    assert robots_allows(text, path="/x", agent="MyQuantLab") == (True, "/x")


def test_wildcards_and_the_end_anchor_are_honoured() -> None:
    text = "User-agent: *\nDisallow: /*.pdf$\n"

    assert robots_allows(text, path="/docs/a.pdf", agent="MyQuantLab") == (False, "/*.pdf$")
    assert robots_allows(text, path="/docs/a.pdf", query="v=1", agent="MyQuantLab") == (True, None)
    assert robots_allows(text, path="/docs/a.txt", agent="MyQuantLab") == (True, None)


def test_the_named_group_beats_the_wildcard_group() -> None:
    text = "User-agent: *\nDisallow: /\nUser-agent: MyQuant\nAllow: /\n"

    assert robots_allows(text, path="/x", agent="MyQuantLab") == (True, "/")
    assert robots_allows(text, path="/x", agent="OtherBot") == (False, "/")


def test_an_empty_disallow_imposes_nothing() -> None:
    text = "User-agent: *\nDisallow:\n"

    assert robots_allows(text, path="/anything", agent="MyQuantLab") == (True, None)


def test_a_rule_before_any_user_agent_line_is_ignored() -> None:
    text = "Disallow: /everywhere\nUser-agent: *\nAllow: /\n"

    assert robots_allows(text, path="/x", agent="MyQuantLab") == (True, "/")


def test_a_robots_file_with_no_rules_allows_everything() -> None:
    assert robots_allows("", path="/x", agent="MyQuantLab") == (True, None)
