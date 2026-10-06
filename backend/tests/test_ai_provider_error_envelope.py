"""A 200 that is not an answer: OpenRouter's error body, read honestly.

OpenRouter answers ``200 OK`` as soon as an upstream provider accepts a request,
so everything that fails *after* that point is reported inside the body. A
generation that dies therefore arrives as ``{"id": …, "error": {…}}`` with no
``choices`` at all -- and the client used to read the status, index into a key
that was not there, and compress the whole thing into
``ProviderCallError: provider 'openrouter' answered without a readable choice:
KeyError('choices')``. The operator learned nothing: not the rate limit, not the
upstream failure, not the generation id.

The contract these tests pin down: an error body is diagnosed as an error body,
a choice that finished with ``finish_reason == "error"`` is never an answer even
when it carries partial text, every other unreadable shape names the part that
was wrong, and the diagnostic never carries the credential. Only the documented
scalar fields survive (``code``, ``message``, ``metadata.error_type``,
``metadata.provider_code``, the top-level ``id``) -- ``metadata.raw`` and the
echoed request body are dropped, not stored.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.provider import OpenAICompatibleProvider, ProviderCallError

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
#: The model the failing NAS run actually asked for: free, 1M context, present in
#: the catalogue at position 154 -- which is why the 40-row Provider Test cannot
#: see it either way.
MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
API_KEY = "sk-test"

#: The envelope from the OpenRouter documentation, with every field the report
#: has to keep. ``metadata.raw`` is the upstream body and must never be stored.
ERROR_ENVELOPE: dict[str, Any] = {
    "id": "gen-1760000000-AbCdEf",
    "error": {
        "code": 429,
        "message": "Provider returned error: upstream is rate limited",
        "metadata": {
            "error_type": "rate_limit_exceeded",
            "provider_code": "rate_limited",
            "raw": "UPSTREAM BODY WITH sk-or-v1-9f8e7d6c5b4a INSIDE",
        },
    },
}


class _FakeResponse:
    """An ``httpx.Response`` with only what the client reads."""

    def __init__(
        self,
        payload: Any,
        *,
        status_code: int = 200,
        raises: Exception | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self._raises = raises

    def raise_for_status(self) -> None:
        if self._raises is not None:
            raise self._raises

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _client(monkeypatch, response: _FakeResponse) -> OpenAICompatibleProvider:
    import httpx

    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: response)
    return OpenAICompatibleProvider(base_url=OPENROUTER_BASE, api_key=API_KEY, name="openrouter")


def _ask(client: OpenAICompatibleProvider):
    return client.reply([{"role": "user", "content": "hi"}], model=MODEL)


def _success(**extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": MODEL,
        "choices": [{"message": {"content": "the answer"}}],
    }
    payload.update(extra)
    return payload


# --------------------------------------------------------------- the happy path


def test_a_standard_answer_is_still_read_the_same_way(monkeypatch) -> None:
    response = _FakeResponse(_success(usage={"prompt_tokens": 1200, "completion_tokens": 300}))
    client = _client(monkeypatch, response)

    reply = _ask(client)

    assert reply.text == "the answer"
    assert reply.model == MODEL
    # The usage and the ledger stay exactly as they were: this fix is about the
    # failure path and must not move the success one.
    assert reply.usage == {"input_tokens": 1200, "output_tokens": 300}
    assert reply.reported_usage is True
    assert client.last_reply is reply


def test_a_successful_answer_needs_no_usage_to_be_valid(monkeypatch) -> None:
    client = _client(monkeypatch, _FakeResponse(_success()))

    reply = _ask(client)

    assert reply.text == "the answer"
    assert reply.usage == {}


# ------------------------------------------------------------ the error envelope


def test_an_error_body_on_a_200_is_diagnosed_as_such(monkeypatch) -> None:
    """The incident's shape: HTTP 200, an ``error`` object, no ``choices``."""

    client = _client(monkeypatch, _FakeResponse(ERROR_ENVELOPE))

    with pytest.raises(ProviderCallError) as caught:
        _ask(client)

    message = str(caught.value)
    assert "error body" in message
    assert "KeyError" not in message
    # Every documented field is kept: the operator can name the failure without
    # opening the provider's console.
    assert "code=429" in message
    assert "message=Provider returned error: upstream is rate limited" in message
    assert "error_type=rate_limit_exceeded" in message
    assert "provider_code=rate_limited" in message
    assert "generation_id=gen-1760000000-AbCdEf" in message


def test_a_bare_error_body_is_still_readable(monkeypatch) -> None:
    client = _client(monkeypatch, _FakeResponse({"error": {"code": 502}}))

    with pytest.raises(ProviderCallError) as caught:
        _ask(client)

    assert "code=502" in str(caught.value)


def test_the_identity_comes_before_the_long_message(monkeypatch) -> None:
    """The task row keeps the first 500 characters of this text.

    A 300-character provider message placed before the generation id would lose
    the one field an operator needs to look the failure up, so the identity goes
    first and the free text last. The documented envelope fits comfortably.
    """

    client = _client(monkeypatch, _FakeResponse(ERROR_ENVELOPE))

    with pytest.raises(ProviderCallError) as caught:
        _ask(client)

    message = str(caught.value)
    assert message.index("generation_id=") < message.index("message=")
    assert len(message) <= 500, len(message)


def test_an_empty_error_object_is_not_mistaken_for_a_missing_one(monkeypatch) -> None:
    client = _client(monkeypatch, _FakeResponse({"error": {}, "choices": []}))

    with pytest.raises(ProviderCallError) as caught:
        _ask(client)

    assert "error body" in str(caught.value)


def test_a_non_dict_error_is_read_as_a_message(monkeypatch) -> None:
    client = _client(monkeypatch, _FakeResponse({"error": "boom"}))

    with pytest.raises(ProviderCallError) as caught:
        _ask(client)

    assert "message=boom" in str(caught.value)


# --------------------------------------------------------------- unreadable shapes


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ([], "not a JSON object"),
        ({"id": "gen-1"}, "no 'choices' list"),
        ({"choices": {}}, "no 'choices' list"),
        ({"choices": []}, "'choices' is empty"),
        ({"choices": ["answered"]}, "'choices[0]' is not an object"),
        ({"choices": [{"finish_reason": "stop"}]}, "'choices[0].message' is missing"),
        ({"choices": [{"message": {}}]}, "content"),
        ({"choices": [{"message": {"content": ["a", "b"]}}]}, "content"),
        ({"choices": [{"message": {"content": None}}]}, "content"),
    ],
)
def test_every_unreadable_shape_names_what_was_wrong(
    monkeypatch, payload: Any, expected: str
) -> None:
    client = _client(monkeypatch, _FakeResponse(payload))

    with pytest.raises(ProviderCallError) as caught:
        _ask(client)

    message = str(caught.value)
    assert expected in message, message
    # Never the old bare repr of a failed lookup.
    assert "KeyError" not in message
    assert "IndexError" not in message


def test_a_choice_that_finished_with_an_error_is_not_an_answer(monkeypatch) -> None:
    """Partial text beside ``finish_reason: "error"`` is a failure, not content."""

    payload = {
        "id": "gen-1760000001-XyZ",
        "choices": [
            {
                "finish_reason": "error",
                "error": {"code": 502, "message": "upstream provider exploded"},
                "message": {"content": "partial answer that must never be stored"},
            }
        ],
    }
    client = _client(monkeypatch, _FakeResponse(payload))

    with pytest.raises(ProviderCallError) as caught:
        _ask(client)

    message = str(caught.value)
    assert "finish_reason" not in message  # the wording is ours, not the raw body
    assert "upstream provider exploded" in message
    assert "code=502" in message
    assert "partial answer" not in message
    assert client.last_reply is None


def test_a_choice_that_failed_without_detail_still_fails(monkeypatch) -> None:
    payload = {"choices": [{"finish_reason": "error", "message": {"content": "x"}}]}
    client = _client(monkeypatch, _FakeResponse(payload))

    with pytest.raises(ProviderCallError) as caught:
        _ask(client)

    assert "the choice failed" in str(caught.value)


def test_a_body_that_is_not_json_is_a_readable_failure(monkeypatch) -> None:
    client = _client(monkeypatch, _FakeResponse(None))

    with pytest.raises(ProviderCallError) as caught:
        _ask(client)

    assert "not JSON" in str(caught.value)


# ------------------------------------------------------------------- the secret


def test_the_diagnostic_never_carries_the_credential(monkeypatch) -> None:
    """The provider's own text is third-party input and is de-secreted."""

    payload = {
        "id": "gen-1760000002-Leak",
        "error": {
            "code": 401,
            "message": (
                f"bad key {API_KEY} and header Authorization: Bearer {API_KEY} "
                "plus a second one sk-or-v1-9f8e7d6c5b4a"
            ),
            "metadata": {
                "error_type": "authentication_error",
                "raw": f"Authorization: Bearer {API_KEY}",
            },
        },
    }
    client = _client(monkeypatch, _FakeResponse(payload))

    with pytest.raises(ProviderCallError) as caught:
        _ask(client)

    message = str(caught.value)
    assert API_KEY not in message
    assert "sk-or-v1-9f8e7d6c5b4a" not in message
    assert "***" in message
    # ``metadata.raw`` is dropped wholesale, not clipped.
    assert "raw=" not in message


def test_an_overlong_error_message_is_clipped(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        _FakeResponse({"error": {"code": 500, "message": "x" * 5000}}),
    )

    with pytest.raises(ProviderCallError) as caught:
        _ask(client)

    message = str(caught.value)
    assert len(message) <= 700, len(message)
    assert "…" in message


# ------------------------------------------------------------------- the status


def test_an_http_error_status_is_not_swallowed(monkeypatch) -> None:
    """4xx/5xx keep raising the httpx error, exactly as before this fix."""

    import httpx

    request = httpx.Request("POST", f"{OPENROUTER_BASE}/chat/completions")
    failure = httpx.HTTPStatusError(
        "Client error '401 Unauthorized'",
        request=request,
        response=httpx.Response(401, request=request),
    )
    response = _FakeResponse({"error": {"code": 401}}, status_code=401, raises=failure)
    client = _client(monkeypatch, response)

    with pytest.raises(httpx.HTTPStatusError):
        _ask(client)
