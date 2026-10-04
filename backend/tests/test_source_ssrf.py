"""The SSRF matrix for source ingestion.

Every case here is decided by :mod:`app.sources.guard`, which never opens a
socket: the resolver is injected, so the matrix runs offline and cannot pass by
accident because a machine happens to answer DNS differently (ADR-163).

The rule under test is that the *address* decides, never the host string.  A
name that looks harmless can resolve to ``127.0.0.1``; a literal address can be
spelled as a decimal integer, an octal quad or an IPv4-mapped IPv6 address; and
one dangerous answer among several is enough to refuse the source.
"""

from __future__ import annotations

import ipaddress
import socket

import pytest

from app.sources.guard import SourceBlocked, check_redirect, check_url

PUBLIC = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


def resolver_for(*addresses: str):
    """A resolver that always answers with ``addresses``."""

    def resolve(host: str, port: int) -> list[str]:
        return list(addresses)

    return resolve


def offline_resolver(host: str, port: int) -> list[str]:
    raise AssertionError(f"the guard resolved '{host}' instead of trusting the literal")


def blocked(raw: str, *, resolver=None) -> SourceBlocked:
    with pytest.raises(SourceBlocked) as caught:
        check_url(raw, resolver=resolver or resolver_for(PUBLIC))
    return caught.value


# --- what is allowed -------------------------------------------------------


def test_a_public_url_is_allowed_and_keeps_its_shape():
    checked = check_url("https://example.com/paper?id=7#results", resolver=resolver_for(PUBLIC))
    assert checked.scheme == "https"
    assert checked.host == "example.com"
    assert checked.port == 443
    assert checked.addresses == (PUBLIC,)
    assert checked.url == "https://example.com/paper?id=7"


def test_http_defaults_to_port_eighty_and_a_root_path():
    checked = check_url("http://example.com", resolver=resolver_for(PUBLIC))
    assert (checked.port, checked.url) == (80, "http://example.com/")


def test_a_literal_public_address_is_accepted_without_dns():
    checked = check_url(f"http://{PUBLIC}/notes", resolver=offline_resolver)
    assert checked.addresses == (PUBLIC,)


def test_a_trailing_dot_is_normalised_before_use():
    checked = check_url("https://example.com./paper", resolver=resolver_for(PUBLIC))
    assert (checked.host, checked.url) == ("example.com", "https://example.com/paper")


def test_the_resolver_is_asked_once_with_the_normalised_host_and_port():
    seen: list[tuple[str, int]] = []

    def resolver(host: str, port: int) -> list[str]:
        seen.append((host, port))
        return [PUBLIC]

    check_url("https://Example.COM./x", resolver=resolver)
    assert seen == [("example.com", 443)]


def test_every_validated_address_is_reported():
    checked = check_url("https://example.com/", resolver=resolver_for(PUBLIC, "93.184.216.35"))
    assert checked.addresses == (PUBLIC, "93.184.216.35")


def test_an_ipv4_mapped_public_address_is_judged_by_the_ipv4_address():
    checked = check_url(f"http://[::ffff:{PUBLIC}]/", resolver=offline_resolver)
    mapped = ipaddress.ip_address(checked.addresses[0]).ipv4_mapped
    assert mapped == ipaddress.ip_address(PUBLIC)


def test_the_two_well_known_ports_are_allowed():
    for raw, port in (
        ("http://example.com:80/", 80),
        ("https://example.com:443/", 443),
        ("https://example.com:80/", 80),
    ):
        assert check_url(raw, resolver=resolver_for(PUBLIC)).port == port


# --- schemes, credentials and broken URLs ----------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "file:///etc/passwd",
        "ftp://example.com/paper.pdf",
        "gopher://example.com/",
        "data:text/html,<b>hello</b>",
        "javascript:alert(1)",
    ],
)
def test_only_http_and_https_schemes_are_fetched(raw):
    assert blocked(raw).code == "scheme_not_allowed"


@pytest.mark.parametrize("raw", ["http://user:secret@example.com/", "http://bob@example.com/"])
def test_a_url_with_credentials_is_refused(raw):
    assert blocked(raw).code == "credentials_not_allowed"


@pytest.mark.parametrize(
    "raw, code",
    [
        ("", "invalid_url"),
        ("   ", "invalid_url"),
        ("http://", "host_missing"),
        ("http://[::1", "invalid_url"),
        ("http://example.com:eight/", "invalid_url"),
        ("http://example.com/\nHost: metadata.google.internal", "invalid_url"),
    ],
)
def test_a_broken_url_is_refused(raw, code):
    assert blocked(raw).code == code


# --- names that mean "here" ------------------------------------------------


@pytest.mark.parametrize(
    "host", ["localhost", "localhost.", "LOCALHOST", "api.localhost", "api.localhost."]
)
def test_localhost_and_its_subdomains_are_refused(host):
    error = blocked(f"http://{host}/")
    assert error.code == "host_not_allowed"
    assert "local" in error.message


@pytest.mark.parametrize(
    "host",
    [
        "printer.local",
        "wiki.internal",
        "box.home.arpa",
        "metadata.google.internal",
        "metadata.goog",
    ],
)
def test_internal_names_are_refused(host):
    assert blocked(f"http://{host}/").code == "host_not_allowed"


@pytest.mark.parametrize("port", [8080, 8443, 22, 6379, 3000])
def test_a_non_standard_port_is_refused(port):
    error = blocked(f"http://example.com:{port}/")
    assert error.code == "port_not_allowed"
    assert str(port) in error.message


# --- addresses that are never a research source ----------------------------


@pytest.mark.parametrize("address", ["127.0.0.1", "127.1.2.3", "127.255.255.254", "::1"])
def test_a_loopback_address_is_refused(address):
    error = blocked(f"http://[{address}]/" if ":" in address else f"http://{address}/")
    assert error.code == "address_not_allowed"
    assert "loopback" in error.message


@pytest.mark.parametrize("address", ["10.0.0.5", "172.16.9.9", "192.168.1.1"])
def test_a_private_address_is_refused(address):
    error = blocked(f"http://{address}/")
    assert error.code == "address_not_allowed"
    assert "private or reserved" in error.message


@pytest.mark.parametrize("address", ["169.254.169.254", "169.254.0.1"])
def test_the_metadata_address_and_other_link_local_addresses_are_refused(address):
    error = blocked(f"http://{address}/")
    assert error.code == "address_not_allowed"
    assert "link-local" in error.message


@pytest.mark.parametrize("address", ["0.0.0.0", "224.0.0.1", "240.0.0.1", "255.255.255.255"])
def test_unspecified_multicast_and_reserved_addresses_are_refused(address):
    error = blocked(f"http://{address}/")
    assert error.code == "address_not_allowed"
    assert address in error.message


@pytest.mark.parametrize(
    "address", ["100.64.0.1", "100.127.255.254", "198.18.0.1", "198.19.255.255"]
)
def test_cgnat_and_benchmark_addresses_are_refused(address):
    error = blocked(f"http://{address}/")
    assert error.code == "address_not_allowed"
    assert address in error.message


@pytest.mark.parametrize(
    "address",
    [
        "fc00::1",
        "fd12:3456:789a::1",
        "2001:db8::1",
        "::ffff:127.0.0.1",
        "::ffff:10.1.2.3",
        "fe80::1%eth0",
    ],
)
def test_a_non_public_ipv6_address_is_refused(address):
    error = blocked(f"http://[{address}]/")
    assert error.code == "address_not_allowed"
    assert address in error.message


def test_a_clever_host_spelling_is_judged_by_its_resolved_address():
    for host in ("2130706433", "0x7f000001", "0177.0.0.1", "127.0.0.1.nip.io"):
        error = blocked(f"http://{host}/", resolver=resolver_for("127.0.0.1"))
        assert error.code == "address_not_allowed"
        assert "127.0.0.1" in error.message


def test_a_literal_private_address_is_refused_without_asking_dns():
    assert blocked("http://10.1.2.3/", resolver=offline_resolver).code == "address_not_allowed"


# --- what the resolver answers ---------------------------------------------


def test_a_public_name_that_resolves_to_a_private_address_is_refused():
    error = blocked("http://research.example.com/", resolver=resolver_for("192.168.1.10"))
    assert error.code == "address_not_allowed"
    assert "192.168.1.10" in error.message


@pytest.mark.parametrize(
    "answers",
    [
        (PUBLIC, "10.0.0.1"),
        ("10.0.0.1", PUBLIC),
        (PUBLIC, PUBLIC_V6, "127.0.0.1"),
    ],
)
def test_one_dangerous_answer_is_enough_to_refuse_the_source(answers):
    error = blocked("http://research.example.com/", resolver=resolver_for(*answers))
    assert error.code == "address_not_allowed"


def test_a_name_that_cannot_be_resolved_is_refused():
    def resolver(host: str, port: int) -> list[str]:
        raise socket.gaierror(11001, "getaddrinfo failed")

    error = blocked("http://nope.example.com/", resolver=resolver)
    assert error.code == "dns_failed"
    assert "nope.example.com" in error.message


def test_a_name_that_resolves_to_nothing_is_refused():
    assert blocked("http://quiet.example.com/", resolver=resolver_for()).code == (
        "dns_no_addresses"
    )


def test_an_answer_that_is_not_an_address_is_refused():
    error = blocked("http://odd.example.com/", resolver=resolver_for("not-an-ip"))
    assert error.code == "address_unreadable"


# --- redirects are checked like fresh URLs ---------------------------------


@pytest.mark.parametrize(
    "location, code",
    [
        ("http://192.168.1.1/admin", "address_not_allowed"),
        ("http://localhost/", "host_not_allowed"),
        ("http://[::1]/", "address_not_allowed"),
        ("http://169.254.169.254/latest/meta-data/", "address_not_allowed"),
        ("file:///etc/passwd", "scheme_not_allowed"),
        ("http://example.com:8080/", "port_not_allowed"),
    ],
)
def test_a_redirect_is_checked_like_a_fresh_url(location, code):
    current = check_url("http://research.example.com/paper", resolver=resolver_for(PUBLIC))
    with pytest.raises(SourceBlocked) as caught:
        check_redirect(current, location, resolver=resolver_for(PUBLIC))
    assert caught.value.code == code


def test_a_relative_redirect_stays_on_the_guarded_host():
    current = check_url("http://research.example.com/paper", resolver=resolver_for(PUBLIC))
    followed = check_redirect(current, "/other", resolver=resolver_for(PUBLIC))
    assert followed.url == "http://research.example.com/other"


def test_a_name_that_changes_its_answer_is_re_checked_on_the_next_hop():
    answers = [PUBLIC]

    def resolver(host: str, port: int) -> list[str]:
        return [answers.pop(0)] if answers else ["127.0.0.1"]

    current = check_url("http://rebind.example.com/", resolver=resolver)
    assert current.addresses == (PUBLIC,)
    with pytest.raises(SourceBlocked) as caught:
        check_redirect(current, "http://rebind.example.com/", resolver=resolver)
    assert caught.value.code == "address_not_allowed"
