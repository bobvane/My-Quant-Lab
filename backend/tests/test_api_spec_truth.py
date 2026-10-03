"""The API spec must describe the doors the application really serves.

``docs/12_API_SPEC.md`` is the document a deployer reads before pointing a
client, a script or a curl at the box.  A v1.6.3 audit compared its ~95 endpoint
declarations with ``create_app().openapi()`` and found eleven high-severity
drifts: whole families that never existed (``/market-data-snapshots/*``,
``/strategy-parameters*``, ``/backtest-results*``), doors written with the wrong
mount point (four AI families that really live under ``/settings/ai/providers*``),
a request body printed as query parameters, and twenty real doors the document
never mentioned at all.  ``backend/tests/test_api_contract.py`` cannot see any of
it: it binds the *client* to the API, and the client was healthy.

The fix is a contract the document cannot silently break: every endpoint line in
``docs/12_API_SPEC.md`` carries exactly one status marker, and this guard binds
the three groups to the running application in both directions.

    ``[已实现]``  the route is served today
    ``[计划]``    not implemented yet
    ``[取消]``    never implemented, or withdrawn

An ``[已实现]`` claim the API does not serve fails, a served route the document
never claims fails, and a ``[计划]``/``[取消]`` claim that *is* served fails too
(otherwise a stale marker could hide a real door forever).  A line with an
endpoint and no marker fails, so the next endpoint cannot be added without
declaring which of the three it is.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "docs" / "12_API_SPEC.md"

API_PREFIX = "/api/v1"
MARKERS = ("[已实现]", "[计划]", "[取消]")

# ```GET /path``` or ``GET /path``, optionally with a query string or a trailing
# "/" — the shapes this document actually uses.
CLAIM = re.compile(r"`(GET|POST|PUT|PATCH|DELETE) +(/[^`?\s]*)`")

# Served, but deliberately absent from the OpenAPI document (and therefore from
# ``create_app().openapi()``), so the guard has to know it by name.
SERVED_OUTSIDE_THE_SCHEMA = {("GET", "/healthz")}

Claim = tuple[int, str, str, str]  # line number, marker, method, path


def _normalise(path: str) -> str:
    """A path comparable between prose and OpenAPI: no query, params as ``{}``."""

    path = path.split("?")[0].split("#")[0].rstrip("/") or "/"
    return re.sub(r"\{[^}]*\}", "{}", path)


def _claims() -> list[Claim]:
    """Every endpoint the document claims, with its marker and line number."""

    claims: list[Claim] = []
    for number, line in enumerate(SPEC_PATH.read_text(encoding="utf-8").splitlines(), 1):
        found = CLAIM.findall(line)
        if not found:
            continue
        present = [marker for marker in MARKERS if marker in line]
        marker = present[0] if len(present) == 1 else "|".join(present)
        for method, path in found:
            claims.append((number, marker, method.upper(), _normalise(path)))
    return claims


def _served() -> set[tuple[str, str]]:
    """Every ``(method, path)`` the running application answers."""

    from app.api.main import create_app

    served: set[tuple[str, str]] = set()
    for raw_path, methods in create_app().openapi().get("paths", {}).items():
        path = raw_path[len(API_PREFIX) :] if raw_path.startswith(API_PREFIX) else raw_path
        for method in methods:
            served.add((method.upper(), _normalise(path)))
    return served | SERVED_OUTSIDE_THE_SCHEMA


def _marked(claims: list[Claim], marker: str) -> dict[tuple[str, str], int]:
    """The claimed routes of one marker group, mapped to their line numbers."""

    return {(method, path): line for line, mark, method, path in claims if mark == marker}


@pytest.fixture(scope="module")
def claims() -> list[Claim]:
    return _claims()


@pytest.fixture(scope="module")
def served() -> set[tuple[str, str]]:
    return _served()


def test_the_scan_finds_both_sides() -> None:
    """Floors: a guard that reads nothing would pass for the wrong reason."""

    claims = _claims()
    served = _served()
    implemented = _marked(claims, "[已实现]")
    assert len(served) >= 90, f"only {len(served)} served routes found; the OpenAPI scan broke"
    assert len(implemented) >= 90, (
        f"only {len(implemented)} implemented claims found; the spec scan broke"
    )


def test_every_endpoint_line_declares_its_status(claims: list[Claim]) -> None:
    """An unmarked endpoint line is a claim nobody can check."""

    unmarked = [
        f"docs/12_API_SPEC.md:{line}: {method} {path}"
        for line, marker, method, path in claims
        if marker not in MARKERS
    ]
    assert unmarked == [], (
        "endpoint lines without exactly one [已实现]/[计划]/[取消] marker: " + "; ".join(unmarked)
    )


def test_every_implemented_claim_is_served(
    claims: list[Claim], served: set[tuple[str, str]]
) -> None:
    """The family this audit found: a document teaching doors that answer 404."""

    missing = [
        f"docs/12_API_SPEC.md:{line}: {method} {path}"
        for (method, path), line in sorted(_marked(claims, "[已实现]").items(), key=lambda i: i[1])
        if (method, path) not in served
    ]
    assert missing == [], "the spec promises routes the API does not serve: " + "; ".join(missing)


def test_every_served_route_is_claimed(claims: list[Claim], served: set[tuple[str, str]]) -> None:
    """The other half: twenty real doors the document never mentioned."""

    documented = _marked(claims, "[已实现]")
    undocumented = [
        f"{method} {path}" for method, path in sorted(served) if (method, path) not in documented
    ]
    assert undocumented == [], (
        "routes the API serves that docs/12_API_SPEC.md never claims: " + "; ".join(undocumented)
    )


def test_planned_and_cancelled_claims_are_not_served(
    claims: list[Claim], served: set[tuple[str, str]]
) -> None:
    """A marker that outlives the code hides a real door: flip it instead."""

    stale = []
    for marker in ("[计划]", "[取消]"):
        for (method, path), line in sorted(_marked(claims, marker).items(), key=lambda i: i[1]):
            if (method, path) in served:
                stale.append(f"docs/12_API_SPEC.md:{line}: {marker} {method} {path} is served")
    assert stale == [], "stale markers on routes that exist: " + "; ".join(stale)


def test_the_spec_covers_every_mounted_router() -> None:
    """A whole router added without a word in the spec still has to fail."""

    spec = SPEC_PATH.read_text(encoding="utf-8")
    from app.api.main import create_app

    prefixes: dict[str, Any] = {}
    for route in create_app().routes:
        path = getattr(route, "path", "")
        if path.startswith(f"{API_PREFIX}/"):
            prefixes[path[len(API_PREFIX) :].split("/")[1]] = True
    missing = [name for name in sorted(prefixes) if f"/{name}" not in spec]
    assert missing == [], "docs/12_API_SPEC.md never mentions these mounted prefixes: " + ", ".join(
        missing
    )
