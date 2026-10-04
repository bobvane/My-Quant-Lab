"""URL and address policy for material the user asks us to fetch.

Every URL that reaches :mod:`app.sources.fetch` passes through :func:`check_url`
first.  The rules are about the *resolved address*, never about the host string:
a harmless-looking name can resolve to ``127.0.0.1``, and a literal address can
be spelled as a decimal integer, an octal quad or an IPv4-mapped IPv6 address.
One dangerous answer is enough to refuse the whole source.

This module makes no request.  It answers one question -- may we connect to this
URL, and to which addresses -- which is why the whole matrix can be tested
without a network (ADR-163).
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

#: Only the web is reachable.  ``file``, ``ftp``, ``gopher``, ``data`` and
#: ``javascript`` are refused by name before anything else happens.
ALLOWED_SCHEMES = ("http", "https")

#: Only the two well-known web ports.  An SSRF attempt does not need 8080, and a
#: published research source is on 80 or 443.
ALLOWED_PORTS = (80, 443)

#: Redirect chains longer than this are refused instead of followed.
MAX_REDIRECTS = 3

DEFAULT_PORTS = {"http": 80, "https": 443}

#: Names that mean "this machine" or "this network" whatever DNS says.
INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
METADATA_HOSTS = ("metadata.google.internal", "metadata.goog")

#: How the guard asks for addresses.  Tests inject their own resolver, so the
#: matrix needs no DNS and no network.
Resolver = Callable[[str, int], Sequence[str]]


class SourceBlocked(Exception):
    """A URL or a resolved address the guard refuses to connect to.

    ``code`` is stable and safe to log, show and assert on; ``message`` is
    written for the person who pasted the URL.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CheckedURL:
    """A URL that passed the guard, with the addresses it may be reached on."""

    url: str
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]


def system_resolver(host: str, port: int) -> list[str]:
    """Resolve ``host`` with the operating system, keeping every answer."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in addresses:
            addresses.append(address)
    return addresses


def check_redirect(
    current: CheckedURL,
    location: str,
    *,
    resolver: Resolver = system_resolver,
    ports: Sequence[int] = ALLOWED_PORTS,
) -> CheckedURL:
    """Follow one redirect hop through the whole guard.

    A redirect target is chosen by the remote server, so it is checked from
    scratch rather than trusted because the first URL passed.
    """
    return check_url(urljoin(current.url, location), resolver=resolver, ports=ports)


def check_url(
    raw: str,
    *,
    resolver: Resolver = system_resolver,
    ports: Sequence[int] = ALLOWED_PORTS,
) -> CheckedURL:
    """Decide whether ``raw`` may be fetched, and on which addresses.

    Raises :class:`SourceBlocked` with a stable code rather than returning a
    verdict object, because a refused address refuses the whole source.
    """
    text = (raw or "").strip()
    if not text or any(character in text for character in "\r\n\t"):
        raise SourceBlocked("invalid_url", "a source URL cannot be empty or contain whitespace")
    try:
        parts = urlsplit(text)
        scheme = (parts.scheme or "").lower()
        host = (parts.hostname or "").strip().lower().rstrip(".")
        port = parts.port
    except ValueError as error:
        raise SourceBlocked("invalid_url", f"'{text}' is not a URL: {error}") from error
    if scheme not in ALLOWED_SCHEMES:
        named = scheme or text.split(":", 1)[0][:32]
        raise SourceBlocked(
            "scheme_not_allowed",
            f"only http and https URLs can be fetched, not '{named}'",
        )
    if parts.username or parts.password:
        raise SourceBlocked(
            "credentials_not_allowed",
            "a source URL must not carry a username or password",
        )
    if not host:
        raise SourceBlocked("host_missing", "a source URL needs a host")
    resolved_port = port or DEFAULT_PORTS[scheme]
    if resolved_port not in ports:
        allowed = ", ".join(str(item) for item in ports)
        raise SourceBlocked(
            "port_not_allowed",
            f"only ports {allowed} can be fetched, not {resolved_port}",
        )
    addresses = _addresses_for(host, resolved_port, resolver)
    return CheckedURL(
        url=_render(scheme, host, resolved_port, parts.path, parts.query),
        scheme=scheme,
        host=host,
        port=resolved_port,
        addresses=addresses,
    )


def _addresses_for(host: str, port: int, resolver: Resolver) -> tuple[str, ...]:
    literal = _literal_address(host)
    if literal is not None:
        _check_address(host, literal)
        return (literal,)
    _check_name(host)
    addresses = _resolve(host, port, resolver)
    for address in addresses:
        _check_address(host, address)
    return addresses


def _literal_address(host: str) -> str | None:
    """Return the canonical spelling when ``host`` is already an IP address."""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def _check_name(host: str) -> None:
    if host == "localhost" or host.endswith(INTERNAL_SUFFIXES) or host in METADATA_HOSTS:
        raise SourceBlocked("host_not_allowed", f"'{host}' names a local or internal host")


def _resolve(host: str, port: int, resolver: Resolver) -> tuple[str, ...]:
    try:
        answers = [str(address).strip() for address in resolver(host, port)]
    except SourceBlocked:
        raise
    except (OSError, UnicodeError) as error:
        raise SourceBlocked("dns_failed", f"'{host}' could not be resolved: {error}") from error
    answers = [address for address in answers if address]
    if not answers:
        raise SourceBlocked("dns_no_addresses", f"'{host}' resolved to no addresses")
    return tuple(answers)


def _check_address(host: str, text: str) -> None:
    try:
        address = ipaddress.ip_address(text)
    except ValueError as error:
        raise SourceBlocked(
            "address_unreadable",
            f"'{host}' resolved to '{text}', which is not an IP address",
        ) from error
    problem = _address_problem(address)
    if problem is not None:
        raise SourceBlocked(
            "address_not_allowed", f"'{host}' resolves to {text}, which is {problem}"
        )


def _address_problem(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Say what is wrong with an address, or ``None`` when it is public."""
    if isinstance(address, ipaddress.IPv6Address):
        if address.scope_id:
            return "an IPv6 address with a scope id"
        if address.ipv4_mapped is not None:
            return _address_problem(address.ipv4_mapped)
    if address.is_multicast:
        return "a multicast address"
    if address.is_unspecified:
        return "the unspecified address"
    if address.is_loopback:
        return "a loopback address"
    if address.is_link_local:
        return "a link-local address"
    if address.is_private:
        return "a private or reserved address"
    if address.is_reserved:
        return "a reserved address"
    if not address.is_global:
        return "not a globally routable address"
    return None


def _render(scheme: str, host: str, port: int, path: str, query: str) -> str:
    host_part = f"[{host}]" if ":" in host else host
    netloc = host_part if port == DEFAULT_PORTS[scheme] else f"{host_part}:{port}"
    return urlunsplit((scheme, netloc, path or "/", query, ""))
