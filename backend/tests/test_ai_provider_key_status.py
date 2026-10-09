"""A stored key that cannot be read is not "no provider configured".

``ai_providers.api_key_encrypted`` is encrypted with a key derived from
``SECRET_KEY``. Changing ``SECRET_KEY`` afterwards leaves every ciphertext
intact and unreadable, and the old code had three ways of hiding that:

* ``get_active_provider`` looked at the *first* enabled provider only, so one
  unreadable key hid every healthy provider behind it;
* ``/ai/status`` answered ``No AI provider configured. Set one up under
  Settings`` — false, because the row is there and enabled, so the operator goes
  looking for a row they can already see;
* the provider list showed a masked key next to a green "启用", identical to a
  working row.

These tests pin the truth instead: name the provider, name the reason, keep the
healthy provider routable, and never claim "not configured" when a row exists.
Nothing here needs a network call or a real key.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet

from app.ai.explain import (
    KEY_EMPTY,
    KEY_UNDECRYPTABLE,
    get_active_provider,
    unusable_provider_key,
)
from app.api.routers.ai import NOT_CONFIGURED_DETAIL, _unavailable_detail
from app.domain.models import AIProvider
from app.infrastructure.secrets import encrypt_secret

BASE_URL = "https://openrouter.ai/api/v1"
NOT_CONFIGURED_TEXT = "No AI provider configured"


def _token_under_a_foreign_key() -> str:
    """A ciphertext the live SECRET_KEY cannot open (the SECRET_KEY changed)."""

    foreign = base64.urlsafe_b64encode(hashlib.sha256(b"a-key-that-is-not-the-live-one").digest())
    return Fernet(foreign).encrypt(b"sk-test").decode()


def _provider(
    db_session,
    *,
    name: str,
    encrypted: str,
    is_active: bool = True,
    default_model: str = "openrouter/free",
) -> AIProvider:
    row = AIProvider(
        name=name,
        provider_type="openai_compatible",
        base_url=BASE_URL,
        api_key_encrypted=encrypted,
        default_model=default_model,
        daily_budget_usd=2.0,
        is_active=is_active,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_status_names_the_provider_whose_key_cannot_be_read(client, db_session) -> None:
    _provider(db_session, name="OpenRouterFree", encrypted=_token_under_a_foreign_key())
    db_session.commit()

    status = client.get("/api/v1/ai/status").json()

    assert status["configured"] is False
    assert status["key_error"] == KEY_UNDECRYPTABLE
    assert status["provider_name"] == "OpenRouterFree"
    # The row exists and is enabled, so "no provider configured" would be a lie.
    assert NOT_CONFIGURED_TEXT not in status["note"]
    assert "OpenRouterFree" in status["note"]
    # The product keeps working without AI; the note has to say so.
    assert "work without AI" in status["note"]


def test_status_still_says_not_configured_when_there_is_no_row(client, db_session) -> None:
    status = client.get("/api/v1/ai/status").json()

    assert status["configured"] is False
    assert status["key_error"] is None
    assert NOT_CONFIGURED_TEXT in status["note"]


def test_status_calls_an_empty_stored_key_empty(client, db_session) -> None:
    _provider(db_session, name="EmptyKey", encrypted=encrypt_secret(""))
    db_session.commit()

    status = client.get("/api/v1/ai/status").json()

    assert status["configured"] is False
    assert status["key_error"] == KEY_EMPTY
    assert status["provider_name"] == "EmptyKey"


def test_a_healthy_provider_behind_an_unreadable_one_still_routes(client, db_session) -> None:
    """The broken row is first, and that must not hide the working one."""

    _provider(db_session, name="BrokenFirst", encrypted=_token_under_a_foreign_key())
    healthy = _provider(db_session, name="HealthySecond", encrypted=encrypt_secret("sk-live"))
    db_session.commit()

    assert healthy.id > 0
    picked = get_active_provider(db_session)

    assert picked is not None, "one unreadable key must not hide a healthy provider"
    assert picked[0].name == "HealthySecond"
    assert picked[2] == "sk-live"

    status = client.get("/api/v1/ai/status").json()
    assert status["configured"] is True
    assert status["provider_name"] == "HealthySecond"
    assert status["key_error"] is None


def test_only_enabled_rows_are_blamed(client, db_session) -> None:
    _provider(
        db_session, name="DisabledBroken", encrypted=_token_under_a_foreign_key(), is_active=False
    )
    db_session.commit()

    assert unusable_provider_key(db_session) is None
    status = client.get("/api/v1/ai/status").json()
    assert status["configured"] is False
    assert status["key_error"] is None
    assert NOT_CONFIGURED_TEXT in status["note"]


def test_the_provider_list_marks_an_unreadable_key(client, db_session) -> None:
    _provider(db_session, name="OpenRouterFree", encrypted=_token_under_a_foreign_key())
    db_session.commit()

    rows = client.get("/api/v1/settings/ai/providers").json()["providers"]
    row = next(r for r in rows if r["name"] == "OpenRouterFree")

    assert row["key_status"] == KEY_UNDECRYPTABLE
    assert row["api_key_set"] is True


def test_a_readable_key_is_reported_as_ok(client, db_session) -> None:
    _provider(db_session, name="Fine", encrypted=encrypt_secret("sk-live-value"))
    db_session.commit()

    row = client.get("/api/v1/settings/ai/providers").json()["providers"][0]

    assert row["key_status"] == "ok"
    assert row["key_masked"].startswith("sk-")


def test_the_refusal_says_which_provider_is_at_fault(client, db_session) -> None:
    _provider(db_session, name="OpenRouterFree", encrypted=_token_under_a_foreign_key())
    db_session.commit()

    detail = _unavailable_detail(db_session)

    assert NOT_CONFIGURED_TEXT not in detail
    assert "OpenRouterFree" in detail
    assert "SECRET_KEY" in detail


def test_the_refusal_stays_generic_without_a_broken_row(client, db_session) -> None:
    assert _unavailable_detail(db_session) == NOT_CONFIGURED_DETAIL
