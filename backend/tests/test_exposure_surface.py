"""The token has to cover every door, and the documented switch has to be pressed.

Three claims used to live only in prose:

* ``docker/web.nginx.conf`` presented the bearer token for ``/api/`` but not for
  ``/docs`` or ``/openapi.json``, and the API's own middleware let everything
  outside ``/api/v1`` through by construction — so a deployment that set
  ``API_AUTH_TOKEN`` still handed its full schema to anyone who could reach the
  port, and the web proxy then let an anonymous visitor use Swagger's
  "Try it out" as the token holder (ADR-103).
* ``.env.example`` documents ``WEB_BIND=127.0.0.1`` as the way to close the LAN
  door, and no workflow ever ran it: a documented switch nobody presses is a
  claim, not a capability (ADR-104).
* ``scripts/Test-NasDeployment.ps1`` read the schema without the header, so a
  deployment that did set a token would fail its own self-check for a reason
  that has nothing to do with the routes being checked (ADR-103).

The guards here are text-level on purpose: the behaviour they pin is spread over
an nginx config, a compose deployment, a PowerShell script and a workflow, none
of which the unit suite ships with. The behaviour itself is asserted by
``backend/tests/test_api_auth.py`` (the API side) and by the ``Exposure
assertions`` step in ``.github/workflows/ci.yml`` (the deployment side).
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
NGINX = REPO_ROOT / "docker" / "web.nginx.conf"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
MAIN = REPO_ROOT / "backend" / "app" / "api" / "main.py"
NAS_CHECK = REPO_ROOT / "scripts" / "Test-NasDeployment.ps1"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

_LOCATION = re.compile(r"location\s+(?P<pattern>[^{]+?)\s*\{")
_OPEN_PATHS = re.compile(r"open_paths\s*=\s*\{(?P<body>.+)\}")
_EXEMPT_NAME = re.compile(r"api_prefix\}/(?P<name>[a-z]+)")


def _text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _locations(text: str) -> list[tuple[str, str]]:
    """Every `location <pattern> { ... }` block, brace-matched.

    Not a single regex: the bodies contain `${AUTH_LINE}`, so a `[^}]*` body
    would stop inside the placeholder and see a location without its token.
    """
    blocks: list[tuple[str, str]] = []
    for match in _LOCATION.finditer(text):
        depth = 1
        index = match.end()
        while index < len(text) and depth:
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
            index += 1
        blocks.append((match.group("pattern").strip(), text[match.end() : index - 1]))
    return blocks


def _proxied_locations(text: str) -> list[tuple[str, str]]:
    return [
        (pattern, body)
        for pattern, body in _locations(text)
        if "proxy_pass http://quantlab-api:8080" in body
    ]


def test_every_location_that_proxies_the_api_presents_the_token() -> None:
    """A door that talks to the API is a door the API's token belongs on (ADR-103)."""
    proxying = _proxied_locations(_text(NGINX))
    assert {pattern for pattern, _ in proxying} >= {"/api/", "/docs", "/openapi.json"}, (
        "the proxy locations changed: re-derive which of them serve the API before "
        f"trusting this guard (found {sorted(pattern for pattern, _ in proxying)})"
    )
    unprotected = [pattern for pattern, body in proxying if "${AUTH_LINE}" not in body]
    assert not unprotected, (
        f"{unprotected} proxy the API without presenting the token, so a client that "
        "reaches the container is authenticated while one that reaches the port is "
        "not (ADR-103)"
    )


def test_the_api_does_not_decide_by_prefix_what_needs_a_token() -> None:
    """`if not path.startswith(api_prefix)` is what made /docs a public door (ADR-103)."""
    text = _text(MAIN)
    middleware = text[
        text.index("async def enforce_api_auth") : text.index("async def enforce_rate_limit")
    ]
    assert "startswith(settings.api_prefix)" not in middleware, (
        "the auth middleware is deciding by prefix again: every route outside "
        "/api/v1 would be open by construction (ADR-103)"
    )
    assert "path in open_paths" in middleware, (
        "the middleware must exempt a named list of probes, not a namespace (ADR-103)"
    )


def test_the_documented_exemption_list_is_the_middlewares_exemption_list() -> None:
    """One fact, two files: if they drift, the docs describe another product (ADR-103)."""
    match = _OPEN_PATHS.search(_text(MAIN))
    assert match, "the middleware no longer names its exemptions, so nothing can be documented"
    names = set(_EXEMPT_NAME.findall(match.group("body")))
    assert names == {"healthz", "health"}, (
        f"the middleware exempts {sorted(names)}; either the list grew (then say so in "
        ".env.example) or the pattern stopped matching it"
    )

    documented = _text(ENV_EXAMPLE)
    assert "除 /api/v1/healthz 与 /api/v1/health 外" in documented, (
        ".env.example no longer states which doors stay open when the token is set"
    )
    assert "包括 /docs 与" in documented, (
        ".env.example does not say that the documentation console needs the token as "
        "well, which is exactly the claim that used to be false (ADR-103)"
    )


def test_the_deployment_self_check_presents_the_token_when_it_reads_the_schema() -> None:
    """The self-check must fail for the routes it checks, not for its own token (ADR-103)."""
    text = _text(NAS_CHECK)
    step = text[text.index("$schemaUrl =") : text.index("$paths = @(")]
    assert "Authorization" in step, (
        "the schema probe sends no Authorization header, so a NAS with API_AUTH_TOKEN "
        "set reports a deployment failure that is really its own 401 (ADR-103)"
    )
    assert "-Headers" in step, "the header is built but never passed to the request"


def test_ci_presses_the_documented_off_switch_and_the_token() -> None:
    """Both claims get one live run per push, so neither can rot (ADR-104)."""
    text = _text(CI)
    assert "WEB_BIND=127.0.0.1" in text, (
        "no workflow ever deploys with the documented off switch, so "
        "`WEB_BIND=127.0.0.1` is a claim rather than a capability (ADR-104)"
    )
    assert "API_AUTH_TOKEN=$TOKEN" in text, "no workflow ever deploys with a token set"
    assert text.count("/tmp/exposure.env") >= 2, (
        "the overrides are exported into the shell but never written into an env file "
        "that compose is given: whether they reach the containers then depends on "
        "compose's shell-vs-env-file precedence, which is not something this guard can "
        "see, and a step that loses them would report success having asserted nothing "
        "(ADR-104)"
    )
    assert "http://$lan:8081/healthz" in text, (
        "the off switch is set but never probed from a non-loopback address, which is "
        "the only way to know the door closed (ADR-104)"
    )
    assert "/docs /openapi.json /api/v1/strategies" in text, (
        "CI does not probe the documentation doors directly on the API port (ADR-103)"
    )
    assert '[ "$got" = "401" ]' in text and '[ "$got" = "200" ]' in text, (
        "the exposure step prints status codes without failing on them, so it would "
        "report a leak as success (ADR-071/104)"
    )
