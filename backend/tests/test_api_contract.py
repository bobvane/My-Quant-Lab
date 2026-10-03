"""Every call the frontend makes is a claim about a door the API serves (ADR-074).

``request<T>(path)`` is an *assertion*, not a check: ``T`` is unvalidated and
TypeScript only proves that the client compiles, never that ``path`` exists.
v0.9.9 shipped a client that called a method ``api.ts`` never defined, and the
only thing that caught it was ``vue-tsc``. The opposite direction -- the client
naming a route the server does not serve -- has no compiler at all, and the
first place it shows up is a red failed request in the browser.

This module reads ``frontend/src/api.ts`` and the application's own OpenAPI
document and reports:

* doors: every ``(path, method)`` the client calls must be served;
* knobs: every query key the client sends must be declared, and every query
  parameter the API marks required must be sent;
* payloads: the fields the client declares at the top level of its response
  interfaces must exist in the response schema (where the schema is a plain
  object, so nested shapes are not compared).

The scanner has to be trustworthy for any of that to mean anything, so the
last tests feed it a deliberately broken client and insist that it bites.
"""

from __future__ import annotations

import pathlib
import re
from collections.abc import Iterator
from typing import Any, NamedTuple

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CLIENT_SOURCE = REPO_ROOT / "frontend" / "src" / "api.ts"
API_PREFIX = "/api/v1"

_QUOTES = "\"'`"


class ClientCall(NamedTuple):
    """One ``request<Type>(path, options)`` call found in the client source."""

    response_type: str
    path: str
    method: str
    query_keys: frozenset[str]
    conditional_keys: frozenset[str]


def _skip_trivia(text: str, index: int) -> int:
    """Skip whitespace, ``//`` comments and ``/* */`` comments."""

    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
        elif text.startswith("//", index):
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline + 1
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
        else:
            break
    return index


def _skip_literal(text: str, index: int) -> int:
    """Return the index just past the string or template literal at *index*."""

    quote = text[index]
    index += 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
        elif quote == "`" and char == "$" and text.startswith("${", index):
            index = _skip_balanced(text, index + 1, "{", "}")
        elif char == quote:
            return index + 1
        else:
            index += 1
    return index


def _skip_balanced(text: str, index: int, opener: str, closer: str) -> int:
    """Return the index just past the bracket pair that opens at *index*.

    Strings, comments and nested pairs are honoured, so a ``)`` inside a path
    literal or a ``}`` inside a template expression cannot end the scan early.
    Generics are scanned as ``<``/``>`` pairs; a generic argument containing an
    arrow (``=>``) would confuse that pairing, which is why the floor test
    insists on finding a realistic number of calls.
    """

    depth = 0
    while index < len(text):
        char = text[index]
        if char in _QUOTES:
            index = _skip_literal(text, index)
            continue
        if text.startswith("//", index) or text.startswith("/*", index):
            index = _skip_trivia(text, index)
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return index


def _path_literal(arguments: str) -> tuple[str, frozenset[str], frozenset[str]] | None:
    """Read the path out of a ``request(...)`` argument list.

    Returns ``(path, query_keys, conditional_keys)``. ``None`` means the first
    argument is not a string literal, i.e. the path is built somewhere else and
    this guard cannot read it.

    Template expressions are classified by whether they build a query string:
    an expression containing ``?`` is the conditional-query idiom
    (``${flag ? '?key=1' : ''}``) and its keys are reported as conditional,
    because the client may or may not send them.
    """

    index = _skip_trivia(arguments, 0)
    if index >= len(arguments) or arguments[index] not in _QUOTES:
        return None
    quote = arguments[index]
    index += 1
    path: list[str] = []
    conditional_keys: set[str] = set()
    while index < len(arguments):
        char = arguments[index]
        if char == "\\":
            path.append(arguments[index + 1 : index + 2])
            index += 2
            continue
        if quote == "`" and char == "$" and arguments.startswith("${", index):
            end = _skip_balanced(arguments, index + 1, "{", "}")
            expression = arguments[index + 2 : end - 1]
            if "?" in expression:
                conditional_keys.update(_query_keys(expression))
            else:
                path.append("{}")
            index = end
            continue
        if char == quote:
            break
        path.append(char)
        index += 1

    raw = "".join(path)
    head, _, _query = raw.partition("?")
    # The keys are read from ``raw``, not from the part after ``?``: the
    # separator that introduces the first key is part of what ``_query_keys``
    # matches, so splitting first would hide ``/x?ids=1``.
    query_keys = _query_keys(raw)
    # A literal id and a ``${id}`` build the same request, so both become ``{}``;
    # only the shape and the method decide whether a door exists.
    normalised = re.sub(r"/\d+(?=/|$)", "/{}", head)
    normalised = re.sub(r"/+$", "", normalised) or "/"
    return normalised, frozenset(query_keys), frozenset(conditional_keys)


def _query_keys(text: str) -> set[str]:
    """Keys of the form ``?key=`` / ``&key=`` in *text*."""

    return set(re.findall(r"[?&]([A-Za-z_][A-Za-z0-9_]*)=", text))


def _call_sites(source: str) -> Iterator[ClientCall]:
    """Yield every readable ``request`` call in *source*."""

    for match in re.finditer(r"\brequest\b", source):
        index = _skip_trivia(source, match.end())
        response_type = ""
        if index < len(source) and source[index] == "<":
            end = _skip_balanced(source, index, "<", ">")
            response_type = source[index + 1 : end - 1].strip()
            index = _skip_trivia(source, end)
        if index >= len(source) or source[index] != "(":
            continue
        end = _skip_balanced(source, index, "(", ")")
        arguments = source[index + 1 : end - 1]
        literal = _path_literal(arguments)
        if literal is None:
            continue
        path, query_keys, conditional_keys = literal
        method_match = re.search(r"method\s*:\s*['\"](\w+)['\"]", arguments)
        method = method_match.group(1).upper() if method_match else "GET"
        yield ClientCall(response_type, path, method, query_keys, conditional_keys)


def _client_calls(source: str | None = None) -> list[ClientCall]:
    """The calls the shipped client actually makes."""

    if source is None:
        source = CLIENT_SOURCE.read_text(encoding="utf-8")
    return list(_call_sites(source))


def _api_operations(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """Map ``(path, method)`` to its OpenAPI operation, path parameters as ``{}``."""

    operations: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_path, methods in spec.get("paths", {}).items():
        path = raw_path[len(API_PREFIX) :] if raw_path.startswith(API_PREFIX) else raw_path
        path = re.sub(r"\{[^}]*\}", "{}", path) or "/"
        for method, operation in methods.items():
            operations[(path, method.upper())] = operation
    return operations


def _query_parameters(operation: dict[str, Any]) -> list[dict[str, Any]]:
    """The query parameters an operation declares."""

    return [item for item in operation.get("parameters", []) if item.get("in") == "query"]


def _missing_doors(
    calls: list[ClientCall], operations: dict[tuple[str, str], dict[str, Any]]
) -> list[str]:
    """Calls that name a ``(path, method)`` the API does not serve."""

    findings = []
    for call in calls:
        if (call.path, call.method) not in operations:
            findings.append(f"{call.method} {call.path}")
    return findings


def _undeclared_query_keys(
    calls: list[ClientCall], operations: dict[tuple[str, str], dict[str, Any]]
) -> list[str]:
    """Query keys the client sends that the operation never declared."""

    findings = []
    for call in calls:
        operation = operations.get((call.path, call.method))
        if operation is None:
            continue
        declared = {parameter["name"] for parameter in _query_parameters(operation)}
        sent = call.query_keys | call.conditional_keys
        for key in sorted(sent - declared):
            findings.append(f"{call.method} {call.path}: sends undeclared query key '{key}'")
    return findings


def _unsent_required_query_keys(
    calls: list[ClientCall], operations: dict[tuple[str, str], dict[str, Any]]
) -> list[str]:
    """Required query parameters the client never sends.

    Only unconditional keys count: a key the client sends only when a flag is
    set cannot satisfy a parameter the API always demands.
    """

    findings = []
    for call in calls:
        operation = operations.get((call.path, call.method))
        if operation is None:
            continue
        for parameter in _query_parameters(operation):
            if parameter.get("required") and parameter["name"] not in call.query_keys:
                findings.append(
                    f"{call.method} {call.path}: omits required query key '{parameter['name']}'"
                )
    return findings


def _schema_fields(schema: Any, spec: dict[str, Any]) -> set[str] | None:
    """Field names a response schema declares, unwrapping refs, arrays, unions."""

    if not isinstance(schema, dict):
        return None
    for key in ("anyOf", "oneOf", "allOf"):
        options = schema.get(key)
        if options:
            merged: set[str] = set()
            for option in options:
                found = _schema_fields(option, spec)
                if found:
                    merged |= found
            return merged or None
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return _schema_fields(
            spec.get("components", {}).get("schemas", {}).get(name or "", {}), spec
        )
    if schema.get("type") == "array":
        return _schema_fields(schema.get("items", {}), spec)
    properties = schema.get("properties")
    if properties:
        return set(properties)
    return None


def _response_schema(operation: dict[str, Any]) -> Any:
    """The JSON schema of an operation's 200 response, if it has one."""

    response = operation.get("responses", {}).get("200", {})
    return response.get("content", {}).get("application/json", {}).get("schema")


def _interface_body(source: str, name: str) -> str | None:
    """The braced body of ``interface Name`` / ``type Name``, or ``None``."""

    match = re.search(rf"\b(?:interface|type)\s+{re.escape(name)}\b", source)
    if match is None:
        return None
    start = source.find("{", match.end())
    if start < 0:
        return None
    end = _skip_balanced(source, start, "{", "}")
    return source[start + 1 : end - 1]


def _declared_fields(body: str) -> list[str]:
    """Field names declared directly on an interface body.

    Depth-aware on purpose: the flat alternative also collects the fields of
    nested object literals, which then look like fields the API forgot to send.
    """

    fields: list[str] = []
    depth = 0
    index = 0
    while index < len(body):
        char = body[index]
        if char in _QUOTES:
            index = _skip_literal(body, index)
            continue
        if body.startswith("//", index) or body.startswith("/*", index):
            index = _skip_trivia(body, index)
            continue
        if char in "[{(":
            depth += 1
        elif char in "]})":
            depth -= 1
        elif depth == 0 and (char.isalpha() or char in "_$"):
            word = re.match(r"[A-Za-z_$][A-Za-z0-9_$]*", body[index:])
            assert word is not None
            name = word.group(0)
            following = body[index + len(name) :].lstrip()
            if following[:1] in (":", "?"):
                fields.append(name)
            index += len(name)
            continue
        index += 1
    return fields


def _undeclared_response_fields(
    source: str,
    calls: list[ClientCall],
    operations: dict[tuple[str, str], dict[str, Any]],
    spec: dict[str, Any],
) -> tuple[list[str], int]:
    """Client response fields the API's schema never mentions, and the count compared."""

    findings = []
    compared = 0
    for call in calls:
        name = re.sub(r"\[\]$", "", call.response_type)
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name):
            continue
        operation = operations.get((call.path, call.method))
        if operation is None:
            continue
        served = _schema_fields(_response_schema(operation), spec)
        body = _interface_body(source, name)
        if served is None or body is None:
            continue
        compared += 1
        for field in sorted(set(_declared_fields(body)) - served):
            findings.append(
                f"{name} for {call.method} {call.path}: declares field '{field}' "
                f"the response schema does not describe"
            )
    return findings, compared


def _spec() -> dict[str, Any]:
    """The application's own OpenAPI document."""

    from app.api.main import create_app

    return create_app().openapi()


# --------------------------------------------------------------------------- #
# The guard itself
# --------------------------------------------------------------------------- #


def test_the_client_source_is_where_this_guard_expects_it() -> None:
    """A guard pointed at the wrong file would pass for the wrong reason."""

    source = CLIENT_SOURCE.read_text(encoding="utf-8")
    assert "async function request" in source
    assert f"'{API_PREFIX}'" in source, (
        "the client's default API base moved; the prefix is unchecked"
    )


def test_the_scan_finds_the_calls_and_the_doors() -> None:
    """Floors that fail when the scanner stops understanding the client."""

    calls = _client_calls()
    operations = _api_operations(_spec())
    assert len(operations) >= 90, f"only {len(operations)} operations found; the spec scan broke"
    assert len(calls) >= 60, f"only {len(calls)} client calls found; the client scan broke"
    assert len({call.path for call in calls}) >= 50, "the client scan collapsed to a few paths"


def test_every_client_call_names_a_door_the_api_serves() -> None:
    """The v0.9.9 class, in the direction TypeScript cannot see."""

    findings = _missing_doors(_client_calls(), _api_operations(_spec()))
    assert findings == [], "the client calls doors the API does not serve: " + "; ".join(findings)


def test_the_client_sends_only_declared_query_keys() -> None:
    """An invented query key is silently ignored by FastAPI, so nothing else catches it."""

    findings = _undeclared_query_keys(_client_calls(), _api_operations(_spec()))
    assert findings == [], "the client sends query keys the API does not declare: " + "; ".join(
        findings
    )


def test_the_client_sends_every_required_query_key() -> None:
    """A required parameter the client omits is a 422 the compiler cannot predict."""

    findings = _unsent_required_query_keys(_client_calls(), _api_operations(_spec()))
    assert findings == [], "the client omits required query keys: " + "; ".join(findings)


def test_the_client_declares_no_field_the_api_never_sends() -> None:
    """A declared field the API never sends renders as ``undefined``, not as an error."""

    findings, compared = _undeclared_response_fields(
        CLIENT_SOURCE.read_text(encoding="utf-8"),
        _client_calls(),
        _api_operations(_spec()),
        _spec(),
    )
    assert compared >= 20, f"only {compared} responses were compared; the field scan went blind"
    assert findings == [], "the client declares fields the API does not send: " + "; ".join(
        findings
    )


_BROKEN_CLIENT = """
export interface Strategy { id: number }

const api = {
  good: () => request<Strategy[]>('/strategies'),
  unknownDoor: () => request<Strategy[]>('/strategies/nonexistent'),
  wrongMethod: () => request<Strategy[]>('/strategies', { method: 'DELETE' }),
  undeclaredKey: () => request<Strategy[]>('/strategies?nonsense=1'),
  missingKey: () => request<Strategy[]>('/backtests/compare'),
}
"""

_KEPT_CLIENT = """
export interface Strategy { id: number }

/**
 * The shape that fooled a flat field parser: `nonsense` lives *inside*
 * `summary`, so it is not a field of the response. A depth-aware reader stays
 * quiet about it, exactly as it has to stay quiet about the real client's
 * `SensitivityResult`.
 */
export interface Sensitivity {
  sensitivity_version: string
  metric: string
  summary: {
    mean: number | null
    nonsense: number | null
  }
}

const api = {
  good: () => request<Strategy[]>('/strategies'),
  compare: () => request<Strategy[]>('/backtests/compare?ids=1&ids=2'),
  conditional: () => request<Strategy[]>(
    `/backtests?limit=5${flag ? '&strategy_version_id=7' : ''}`,
  ),
  nested: () => request<Sensitivity>('/research/sensitivity', { method: 'POST' }),
}
"""


def _every_finding(source: str) -> str:
    """Run all four checks over *source* and join the findings."""

    spec = _spec()
    operations = _api_operations(spec)
    calls = list(_call_sites(source))
    verdicts = [
        _missing_doors(calls, operations),
        _undeclared_query_keys(calls, operations),
        _unsent_required_query_keys(calls, operations),
        _undeclared_response_fields(source, calls, operations, spec)[0],
    ]
    return "\n".join(finding for verdict in verdicts for finding in verdict)


def test_the_contract_checker_bites_on_a_broken_client() -> None:
    """The guard has to fail when a client breaks the contract, one fault per check."""

    problems = _every_finding(_BROKEN_CLIENT)
    for expected in (
        "DELETE /strategies",  # a method the door does not offer
        "/strategies/nonexistent",  # a door that does not exist
        "undeclared query key 'nonsense'",  # a key the operation never declared
        "omits required query key 'ids'",  # a parameter the API always demands
    ):
        assert expected in problems, f"the guard stayed silent about: {expected}"


def test_the_contract_checker_accepts_a_client_that_keeps_the_contract() -> None:
    """A guard that cannot pass is as useless as one that cannot fail."""

    assert _every_finding(_KEPT_CLIENT) == ""
