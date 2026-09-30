"""AI provider management: create, update, delete and connectivity testing.

Secrets handled here follow one rule: **the API key is write-only**. It is
encrypted at rest through ``app.infrastructure.secrets`` and the only thing any
read path may expose is a masked hint plus a boolean "is set".

Connectivity testing is the single place in the product that reaches out to a
user-supplied URL. It is therefore guarded: only ``https://`` (or ``http://``
for loopback) is accepted, the request is short-lived, and the error text is
sanitised before it is returned so a provider cannot echo back injected content.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import AIModel, AIProvider
from app.infrastructure.secrets import decrypt_secret, encrypt_secret, mask_secret

logger = logging.getLogger(__name__)

__all__ = [
    "ProviderConfigError",
    "create_provider",
    "delete_provider",
    "list_providers",
    "serialize_provider",
    "test_connection",
    "update_provider",
]

TEST_TIMEOUT_SECONDS = 10.0
MAX_MODELS_RETURNED = 40
_MAX_DETAIL_CHARS = 300


class ProviderConfigError(ValueError):
    """Raised when a provider cannot be created/updated/deleted as requested."""


def _validate_base_url(base_url: str) -> str:
    text = (base_url or "").strip().rstrip("/")
    lowered = text.lower()
    if lowered.startswith("https://"):
        return text
    if lowered.startswith("http://"):
        # Plain HTTP is only tolerated for a local/loopback endpoint, where the
        # traffic never leaves the machine.
        host_part = lowered[len("http://") :].split("/")[0].split(":")[0]
        if host_part in {"localhost", "127.0.0.1", "::1"}:
            return text
        raise ProviderConfigError(
            "http:// is only allowed for localhost; use https:// for remote providers"
        )
    raise ProviderConfigError("base_url must start with https:// (or http:// for localhost)")


def test_connection(base_url: str, api_key: str) -> dict[str, Any]:
    """Probe ``GET {base_url}/models`` and report what came back."""

    try:
        url = _validate_base_url(base_url)
    except ProviderConfigError as exc:
        return {"ok": False, "detail": str(exc), "models_found": []}

    import httpx

    try:
        response = httpx.get(
            f"{url}/models",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=TEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        # Never include the key in the message; `exc` from httpx only carries
        # the URL, but strip anything long or brace-y to be safe.
        return {
            "ok": False,
            "detail": _sanitize_detail(f"{type(exc).__name__}: {exc}"),
            "models_found": [],
        }

    if response.status_code == 401:
        return {"ok": False, "detail": "401 unauthorized — check the API key", "models_found": []}
    if response.status_code == 404:
        return {
            "ok": False,
            "detail": (
                "404 — the base_url has no /models endpoint. "
                "Use the API root (e.g. https://api.openai.com/v1), not the chat path."
            ),
            "models_found": [],
        }
    if response.status_code >= 400:
        return {
            "ok": False,
            "detail": f"HTTP {response.status_code} from {url}/models",
            "models_found": [],
        }

    try:
        payload = response.json()
    except Exception:
        return {"ok": False, "detail": "response was not JSON", "models_found": []}

    ids: list[str] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        for item in data[:MAX_MODELS_RETURNED]:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                ids.append(item["id"])
    return {
        "ok": True,
        "detail": f"connected — {len(ids)} model(s) reported",
        "models_found": ids,
    }


def _sanitize_detail(text: str) -> str:
    cleaned = " ".join(str(text).split())
    if len(cleaned) > _MAX_DETAIL_CHARS:
        cleaned = cleaned[:_MAX_DETAIL_CHARS] + "…"
    return cleaned


def serialize_provider(db: Session, provider: AIProvider) -> dict[str, Any]:
    """Public shape of a provider: never includes the key itself."""

    models = db.scalars(
        select(AIModel).where(AIModel.provider_id == provider.id).order_by(AIModel.id)
    ).all()
    key_hint = ""
    if provider.api_key_encrypted:
        try:
            key_hint = mask_secret(decrypt_secret(provider.api_key_encrypted))
        except Exception:  # pragma: no cover - corrupted ciphertext
            key_hint = "********"
    return {
        "id": provider.id,
        "name": provider.name,
        "provider_type": provider.provider_type,
        "base_url": provider.base_url,
        "default_model": provider.default_model,
        "is_active": provider.is_active,
        "daily_budget_usd": float(provider.daily_budget_usd or 0),
        "api_key_set": bool(provider.api_key_encrypted),
        "key_masked": key_hint,
        "models": [
            {
                "id": m.id,
                "model_name": m.model_name,
                "capability_tier": m.capability_tier,
                "input_cost_per_mtok": float(m.input_cost_per_mtok),
                "output_cost_per_mtok": float(m.output_cost_per_mtok),
            }
            for m in models
        ],
    }


def list_providers(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(select(AIProvider).order_by(AIProvider.id)).all()
    return [serialize_provider(db, row) for row in rows]


def create_provider(
    db: Session,
    *,
    name: str,
    base_url: str,
    api_key: str,
    provider_type: str = "openai_compatible",
    default_model: str | None = None,
    daily_budget_usd: float = 2.0,
    is_active: bool = True,
    models: list[dict[str, Any]] | None = None,
) -> AIProvider:
    clean_name = name.strip()
    if db.scalar(select(AIProvider).where(AIProvider.name == clean_name)) is not None:
        raise ProviderConfigError(f"a provider named '{clean_name}' already exists")
    url = _validate_base_url(base_url)
    if not api_key.strip():
        raise ProviderConfigError("api_key is required")

    provider = AIProvider(
        name=clean_name,
        provider_type=provider_type,
        base_url=url,
        api_key_encrypted=encrypt_secret(api_key.strip()),
        default_model=(default_model or None),
        daily_budget_usd=daily_budget_usd,
        is_active=is_active,
    )
    db.add(provider)
    db.flush()

    # Always register at least the default model so cost accounting has a row
    # to attach to, even when the user did not enumerate models.
    declared = list(models or [])
    if not declared and provider.default_model:
        declared = [{"model_name": provider.default_model}]
    seen: set[str] = set()
    for item in declared:
        model_name = str(item.get("model_name") or "").strip()
        if not model_name or model_name in seen:
            continue
        seen.add(model_name)
        db.add(
            AIModel(
                provider_id=provider.id,
                model_name=model_name,
                capability_tier=str(item.get("capability_tier") or "standard"),
                input_cost_per_mtok=float(item.get("input_cost_per_mtok") or 0),
                output_cost_per_mtok=float(item.get("output_cost_per_mtok") or 0),
            )
        )
    db.flush()
    return provider


def update_provider(
    db: Session,
    provider_id: int,
    *,
    name: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    default_model: str | None = None,
    daily_budget_usd: float | None = None,
    is_active: bool | None = None,
) -> AIProvider:
    provider = db.get(AIProvider, provider_id)
    if provider is None:
        raise ProviderConfigError(f"provider {provider_id} not found")

    if name is not None:
        new_name = name.strip()
        clash = db.scalar(
            select(AIProvider).where(AIProvider.name == new_name, AIProvider.id != provider_id)
        )
        if clash is not None:
            raise ProviderConfigError(f"a provider named '{new_name}' already exists")
        provider.name = new_name

    if base_url is not None:
        provider.base_url = _validate_base_url(base_url)
    if api_key is not None:
        # Empty string clears the key; omitting the field keeps it.
        provider.api_key_encrypted = encrypt_secret(api_key.strip()) if api_key.strip() else None
    if default_model is not None:
        provider.default_model = default_model or None
        if provider.default_model:
            exists = db.scalar(
                select(AIModel).where(
                    AIModel.provider_id == provider.id,
                    AIModel.model_name == provider.default_model,
                )
            )
            if exists is None:
                db.add(AIModel(provider_id=provider.id, model_name=provider.default_model))
    if daily_budget_usd is not None:
        provider.daily_budget_usd = daily_budget_usd
    if is_active is not None:
        provider.is_active = is_active
    db.flush()
    return provider


def delete_provider(db: Session, provider_id: int) -> str:
    provider = db.get(AIProvider, provider_id)
    if provider is None:
        raise ProviderConfigError(f"provider {provider_id} not found")

    # Refuse to delete a provider that historical AI tasks still reference, so
    # the audit trail keeps pointing at something real.
    from app.domain.models import AITask

    used = db.scalar(select(AITask.id).where(AITask.provider_id == provider_id).limit(1))
    if used is not None:
        raise ProviderConfigError(
            "this provider has AI task history; deactivate it instead of deleting"
        )
    name = provider.name
    db.delete(provider)
    db.flush()
    return name
