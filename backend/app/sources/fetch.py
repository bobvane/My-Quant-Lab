"""Fetching a research source over a connection the guard already approved.

The rules of this module (ADR-163):

* nothing here decides whether a URL may be reached — :mod:`app.sources.guard`
  does, and **every hop of every redirect goes through it again**;
* there is no ``trust_env``: we speak to ``httpcore`` directly, so the Docker
  ``HTTP_PROXY``/``HTTPS_PROXY`` variables cannot take over the connection path
  the guard just validated;
* the TCP connection is made to an address the guard validated, while the ``Host``
  header and the TLS SNI keep the original hostname — a DNS answer that changes
  between the check and the connection cannot move us (no TOCTOU window);
* bodies are bounded *while* they are read, not after;
* ``robots.txt`` is just another fetch: same guard, same timeouts, same size limit,
  same redirect re-checks. Its text never leaves this module.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

import httpcore

from app.sources.guard import (
    MAX_REDIRECTS,
    CheckedURL,
    Resolver,
    SourceBlocked,
    check_url,
    system_resolver,
)

Guard = Callable[..., CheckedURL]

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_ROBOTS_BYTES = 64 * 1024
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 15.0
POOL_TIMEOUT_SECONDS = 5.0
TOTAL_BUDGET_SECONDS = 30.0
USER_AGENT = "MyQuantLab/2.1 (+research source ingestion)"

DOCUMENT_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "application/pdf",
)

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
NO_ROBOTS_STATUSES = frozenset({400, 401, 403, 404, 410, 451})

FETCH_ERROR_CODES = (
    "timeout",
    "connection_failed",
    "protocol_failed",
    "too_many_redirects",
    "redirect_without_location",
    "status_not_ok",
    "content_type_not_allowed",
    "response_too_large",
    "empty_response",
    "budget_exhausted",
    "robots_disallowed",
    "robots_unavailable",
)


class SourceFetchError(Exception):
    """A fetch that failed for a reason the caller can report and store."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        url: str | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.url = url
        self.status = status


@dataclass(frozen=True)
class FetchedDocument:
    """What came back, exactly as it came back (before parsing)."""

    url: str
    status_code: int
    content_type: str
    headers: tuple[tuple[str, str], ...]
    body: bytes
    redirects: tuple[str, ...]
    retrieved_at: datetime

    @property
    def size_bytes(self) -> int:
        return len(self.body)


@dataclass(frozen=True)
class RobotsVerdict:
    """Whether ``robots.txt`` lets us read this URL, and why.

    ``checked`` is false when there was nothing to obey (no robots.txt at all, or
    the caller asked us not to consult it); a robots.txt we could not read raises
    instead of pretending to be a verdict.
    """

    url: str
    checked: bool
    allowed: bool
    reason: str
    matched_rule: str | None = None
    status_code: int | None = None
    retrieved_at: datetime | None = None


@dataclass(frozen=True)
class RetrievedDocument:
    """A document and the robots verdict that came with it."""

    document: FetchedDocument
    robots: RobotsVerdict


class PinnedBackend(httpcore.SyncBackend):
    """Connect to an address the guard validated instead of a fresh DNS answer.

    httpcore builds the ``Host`` header and the TLS ``server_hostname`` from the
    URL, so replacing only the TCP address keeps the request identical on the wire
    — the server still sees (and is authenticated as) its own name.
    """

    def __init__(self, addresses: Mapping[str, str]) -> None:
        self._addresses = dict(addresses)

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: object | None = None,
    ) -> httpcore.NetworkStream:
        return super().connect_tcp(
            self._addresses.get(host, host),
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


def fetch_document(
    raw_url: str,
    *,
    guard: Guard = check_url,
    resolver: Resolver = system_resolver,
    max_bytes: int = MAX_DOCUMENT_BYTES,
    allowed_types: Sequence[str] | None = DOCUMENT_CONTENT_TYPES,
    connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
    read_timeout: float = READ_TIMEOUT_SECONDS,
    pool_timeout: float = POOL_TIMEOUT_SECONDS,
    budget_seconds: float = TOTAL_BUDGET_SECONDS,
    user_agent: str = USER_AGENT,
    retrieved_at: datetime | None = None,
) -> FetchedDocument:
    """Guard, follow up to ``MAX_REDIRECTS`` re-checked hops, and return the body."""
    checked = guard(raw_url, resolver=resolver)
    return _follow(
        checked,
        guard=guard,
        resolver=resolver,
        max_bytes=max_bytes,
        allowed_types=allowed_types,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        pool_timeout=pool_timeout,
        budget_seconds=budget_seconds,
        user_agent=user_agent,
        retrieved_at=retrieved_at,
    )


def retrieve_document(
    raw_url: str,
    *,
    guard: Guard = check_url,
    resolver: Resolver = system_resolver,
    respect_robots: bool = True,
    max_bytes: int = MAX_DOCUMENT_BYTES,
    allowed_types: Sequence[str] | None = DOCUMENT_CONTENT_TYPES,
    connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
    read_timeout: float = READ_TIMEOUT_SECONDS,
    pool_timeout: float = POOL_TIMEOUT_SECONDS,
    budget_seconds: float = TOTAL_BUDGET_SECONDS,
    user_agent: str = USER_AGENT,
    retrieved_at: datetime | None = None,
) -> RetrievedDocument:
    """Fetch a source the way the ingestion layer must: guard, robots, then fetch.

    The robots request and the document share one time budget, so a slow
    ``robots.txt`` cannot make the whole retrieval unbounded.
    """
    checked = guard(raw_url, resolver=resolver)
    deadline = time.monotonic() + budget_seconds
    if respect_robots:
        robots = check_robots(
            checked,
            guard=guard,
            resolver=resolver,
            user_agent=user_agent,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            pool_timeout=pool_timeout,
            budget_seconds=_remaining(deadline, budget_seconds),
            retrieved_at=retrieved_at,
        )
        if not robots.allowed:
            raise SourceFetchError(
                "robots_disallowed",
                f"{checked.host} asked not to be read: {robots.reason}",
                url=checked.url,
            )
    else:
        robots = RobotsVerdict(
            url=robots_url(checked),
            checked=False,
            allowed=True,
            reason="robots.txt was not consulted",
        )
    document = _follow(
        checked,
        guard=guard,
        resolver=resolver,
        max_bytes=max_bytes,
        allowed_types=allowed_types,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        pool_timeout=pool_timeout,
        budget_seconds=_remaining(deadline, budget_seconds),
        user_agent=user_agent,
        retrieved_at=retrieved_at,
    )
    return RetrievedDocument(document=document, robots=robots)


def check_robots(
    checked: CheckedURL,
    *,
    guard: Guard = check_url,
    resolver: Resolver = system_resolver,
    user_agent: str = USER_AGENT,
    max_bytes: int = MAX_ROBOTS_BYTES,
    connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
    read_timeout: float = READ_TIMEOUT_SECONDS,
    pool_timeout: float = POOL_TIMEOUT_SECONDS,
    budget_seconds: float = TOTAL_BUDGET_SECONDS,
    retrieved_at: datetime | None = None,
) -> RobotsVerdict:
    """Ask ``robots.txt`` whether this URL may be read.

    A robots.txt we could not read (5xx, 429, timeout, connection failure, an
    unreadable body) raises :class:`SourceFetchError` with code
    ``robots_unavailable``: it never turns into a silent "allowed". A site with no
    robots.txt at all (4xx other than 429) is a real verdict: nothing to obey.
    """
    url = robots_url(checked)
    try:
        page = fetch_document(
            url,
            guard=guard,
            resolver=resolver,
            max_bytes=max_bytes,
            allowed_types=None,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            pool_timeout=pool_timeout,
            budget_seconds=budget_seconds,
            user_agent=user_agent,
            retrieved_at=retrieved_at,
        )
    except SourceBlocked:
        raise
    except SourceFetchError as error:
        if error.code == "status_not_ok" and error.status in NO_ROBOTS_STATUSES:
            return RobotsVerdict(
                url=url,
                checked=False,
                allowed=True,
                reason=f"the site has no robots.txt ({error.status})",
                status_code=error.status,
            )
        raise SourceFetchError(
            "robots_unavailable",
            f"robots.txt for {checked.host} could not be read: {error.message}",
            url=url,
            status=error.status,
        ) from error

    parts = urlsplit(checked.url)
    allowed, pattern = robots_allows(
        page.body.decode("utf-8", errors="replace"),
        path=parts.path or "/",
        query=parts.query,
        agent=robots_agent(user_agent),
    )
    if not allowed:
        reason = f"robots.txt disallows '{pattern}'"
    elif pattern is not None:
        reason = f"robots.txt allows it ('{pattern}')"
    else:
        reason = "robots.txt has no rule for it"
    return RobotsVerdict(
        url=url,
        checked=True,
        allowed=allowed,
        reason=reason,
        matched_rule=pattern,
        status_code=page.status_code,
        retrieved_at=page.retrieved_at,
    )


def robots_url(checked: CheckedURL) -> str:
    parts = urlsplit(checked.url)
    return f"{parts.scheme}://{parts.netloc}/robots.txt"


def robots_agent(user_agent: str) -> str:
    """The product token a robots.txt group has to name to address us."""
    token = user_agent.split("(", 1)[0].strip().split("/", 1)[0].strip()
    return token or "MyQuantLab"


def parse_robots(text: str) -> tuple[tuple[tuple[str, ...], tuple[tuple[bool, str], ...]], ...]:
    """Parse ``user-agent``/``allow``/``disallow`` groups, ignoring everything else."""
    groups: list[tuple[tuple[str, ...], tuple[tuple[bool, str], ...]]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    started = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            if started:
                groups.append((tuple(agents), tuple(rules)))
                agents = []
                rules = []
                started = False
            agents.append(value.lower())
        elif field in ("allow", "disallow"):
            if not agents:
                continue
            started = True
            rules.append((field == "allow", value))
    if started or rules:
        groups.append((tuple(agents), tuple(rules)))
    return tuple(groups)


def robots_allows(
    text: str,
    *,
    path: str,
    query: str = "",
    agent: str,
) -> tuple[bool, str | None]:
    """Decide one URL against a robots.txt body: longest match wins, Allow breaks ties.

    Returns ``(allowed, matched_pattern)``; a URL no rule mentions is allowed.
    """
    target = path + (f"?{query}" if query else "")
    rules = _rules_for(parse_robots(text), agent)
    if not rules:
        return True, None
    best: tuple[bool, str] | None = None
    for allow, pattern in rules:
        if not pattern:
            # "Disallow:" with no value imposes nothing; "Allow:" with no value allows nothing.
            continue
        if not _pattern_matches(target, pattern):
            continue
        if best is None or len(pattern) > len(best[1]) or (len(pattern) == len(best[1]) and allow):
            best = (allow, pattern)
    if best is None:
        return True, None
    return best[0], best[1]


def _rules_for(
    groups: tuple[tuple[tuple[str, ...], tuple[tuple[bool, str], ...]], ...],
    agent: str,
) -> tuple[tuple[bool, str], ...]:
    """The group that addresses us: the longest matching token, else the ``*`` group."""
    wanted = agent.lower()
    wildcard: tuple[tuple[bool, str], ...] | None = None
    best: tuple[tuple[bool, str], ...] | None = None
    best_length = -1
    for agents, rules in groups:
        for name in agents:
            if name == "*":
                if wildcard is None:
                    wildcard = rules
            elif name and wanted.startswith(name) and len(name) > best_length:
                best = rules
                best_length = len(name)
    if best is not None:
        return best
    return wildcard if wildcard is not None else ()


def _pattern_matches(target: str, pattern: str) -> bool:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    expression = "".join(".*" if char == "*" else re.escape(char) for char in body)
    return re.match(expression + ("$" if anchored else ""), target) is not None


def _follow(
    checked: CheckedURL,
    *,
    guard: Guard,
    resolver: Resolver,
    max_bytes: int,
    allowed_types: Sequence[str] | None,
    connect_timeout: float,
    read_timeout: float,
    pool_timeout: float,
    budget_seconds: float,
    user_agent: str,
    retrieved_at: datetime | None,
) -> FetchedDocument:
    deadline = time.monotonic() + budget_seconds
    redirects: list[str] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceFetchError(
                "budget_exhausted",
                f"fetching {checked.url} took longer than {budget_seconds:g}s",
                url=checked.url,
            )
        status, headers, content_type, body = _open_once(
            checked,
            max_bytes=max_bytes,
            connect_timeout=min(connect_timeout, remaining),
            read_timeout=min(read_timeout, remaining),
            pool_timeout=min(pool_timeout, remaining),
            user_agent=user_agent,
        )
        if status in REDIRECT_STATUSES:
            location = _header(headers, "location")
            if not location:
                raise SourceFetchError(
                    "redirect_without_location",
                    f"{checked.url} answered {status} without a Location header",
                    url=checked.url,
                    status=status,
                )
            if len(redirects) >= MAX_REDIRECTS:
                raise SourceFetchError(
                    "too_many_redirects",
                    f"{checked.url} redirected more than {MAX_REDIRECTS} times",
                    url=checked.url,
                    status=status,
                )
            redirects.append(checked.url)
            # A redirect target is a new URL: it goes through the whole guard again.
            checked = guard(urljoin(checked.url, location), resolver=resolver)
            continue
        if not 200 <= status < 300:
            raise SourceFetchError(
                "status_not_ok",
                f"{checked.url} answered {status}",
                url=checked.url,
                status=status,
            )
        if allowed_types is not None and _media_type(content_type) not in allowed_types:
            served = content_type or "no content type"
            raise SourceFetchError(
                "content_type_not_allowed",
                f"{checked.url} served {served}, which this version cannot read",
                url=checked.url,
                status=status,
            )
        if not body:
            raise SourceFetchError(
                "empty_response",
                f"{checked.url} answered {status} with an empty body",
                url=checked.url,
                status=status,
            )
        return FetchedDocument(
            url=checked.url,
            status_code=status,
            content_type=content_type,
            headers=headers,
            body=body,
            redirects=tuple(redirects),
            retrieved_at=retrieved_at or datetime.now(UTC),
        )


def _open_once(
    checked: CheckedURL,
    *,
    max_bytes: int,
    connect_timeout: float,
    read_timeout: float,
    pool_timeout: float,
    user_agent: str,
) -> tuple[int, tuple[tuple[str, str], ...], str, bytes]:
    """One guarded connection: no redirect following, no environment proxy, no retries."""
    backend = PinnedBackend({checked.host: checked.addresses[0]})
    timeouts = {
        "connect": connect_timeout,
        "read": read_timeout,
        "write": read_timeout,
        "pool": pool_timeout,
    }
    headers = [("user-agent", user_agent), ("accept", "*/*")]
    try:
        with (
            httpcore.ConnectionPool(network_backend=backend, retries=0) as pool,
            pool.stream(
                "GET",
                checked.url,
                headers=headers,
                extensions={"timeout": timeouts},
            ) as response,
        ):
            status = response.status
            raw = tuple(
                (name.decode("latin-1").lower(), value.decode("latin-1"))
                for name, value in response.headers
            )
            # A redirect body is thrown away, so it does not count against the limit.
            body = (
                b""
                if status in REDIRECT_STATUSES
                else _read_body(response, max_bytes, url=checked.url)
            )
    except SourceFetchError:
        raise
    except httpcore.TimeoutException as error:
        raise SourceFetchError(
            "timeout",
            f"{checked.url} timed out ({error.__class__.__name__})",
            url=checked.url,
        ) from None
    except (
        httpcore.ConnectError,
        httpcore.ReadError,
        httpcore.WriteError,
        httpcore.NetworkError,
    ) as error:
        raise SourceFetchError(
            "connection_failed",
            f"{checked.url} could not be reached ({error.__class__.__name__})",
            url=checked.url,
        ) from None
    except httpcore.ProtocolError as error:
        raise SourceFetchError(
            "protocol_failed",
            f"{checked.url} spoke something other than HTTP ({error.__class__.__name__})",
            url=checked.url,
        ) from None
    return status, raw, _header(raw, "content-type"), body


def _read_body(response: httpcore.Response, max_bytes: int, *, url: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.stream:
        total += len(chunk)
        if total > max_bytes:
            raise SourceFetchError(
                "response_too_large",
                f"{url} sent more than {max_bytes} bytes",
                url=url,
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _media_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _header(headers: tuple[tuple[str, str], ...], name: str) -> str:
    for key, value in headers:
        if key == name:
            return value
    return ""


def _remaining(deadline: float, budget_seconds: float) -> float:
    return max(min(deadline - time.monotonic(), budget_seconds), 0.1)
