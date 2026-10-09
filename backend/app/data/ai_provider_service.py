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

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain.models import AIModel, AIProvider
from app.infrastructure.secrets import decrypt_secret, encrypt_secret, mask_secret

logger = logging.getLogger(__name__)

__all__ = [
    "ModelConfigError",
    "ProviderConfigError",
    "create_provider",
    "delete_model",
    "delete_provider",
    "list_providers",
    "parse_model_entry",
    "save_provider_models",
    "serialize_model",
    "serialize_provider",
    "test_connection",
    "update_model",
    "update_provider",
]

TEST_TIMEOUT_SECONDS = 10.0
_MAX_DETAIL_CHARS = 300
#: Capability tiers MQL itself understands. A user-typed entry is only split
#: into fields when it uses one of these, so model ids that merely contain a
#: colon (``google/gemma-4-31b-it:free``) survive verbatim (ADR-176).
CAPABILITY_TIERS = ("cheap", "standard", "high")


class ProviderConfigError(ValueError):
    """Raised when a provider cannot be created/updated/deleted as requested."""


class ModelConfigError(ProviderConfigError):
    """Raised when a model cannot be enabled/disabled as requested (HTTP 409).

    The only such rule today is the "last routable model" guard: disabling the
    provider's ``default_model`` while it is the last active model would leave an
    enabled provider with nothing to route to, so the request is refused instead
    of silently changing ``default_model``.
    """


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
    """Probe ``GET {base_url}/models`` and report what came back.

    The whole ``data`` array is returned: MQL does not cap how many models a
    provider may report (an OpenRouter key reports hundreds), and the count in
    ``detail`` is the real number, not a truncated one (ADR-176).
    """

    def _failure(detail: str) -> dict[str, Any]:
        return {"ok": False, "detail": detail, "models_found": [], "models_total": 0}

    try:
        url = _validate_base_url(base_url)
    except ProviderConfigError as exc:
        return _failure(str(exc))

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
        return _failure(_sanitize_detail(f"{type(exc).__name__}: {exc}"))

    if response.status_code == 401:
        return _failure("401 unauthorized — check the API key")
    if response.status_code == 404:
        return _failure(
            "404 — the base_url has no /models endpoint. "
            "Use the API root (e.g. https://api.openai.com/v1), not the chat path."
        )
    if response.status_code >= 400:
        return _failure(f"HTTP {response.status_code} from {url}/models")

    try:
        payload = response.json()
    except Exception:
        return _failure("response was not JSON")

    ids: list[str] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                ids.append(item["id"])
    return {
        "ok": True,
        "detail": f"connected — {len(ids)} model(s) reported",
        "models_found": ids,
        "models_total": len(ids),
    }


def parse_model_entry(text: str) -> dict[str, Any]:
    """Split one user-typed model entry into a name plus optional MQL fields.

    Only MQL's own documented shapes are parsed — ``name:tier`` or
    ``name:tier:input_cost:output_cost`` with ``tier`` in :data:`CAPABILITY_TIERS`
    and numeric costs. Everything else is kept verbatim as the model name,
    because real model ids contain colons (``openrouter/free``,
    ``google/gemma-4-31b-it:free``). The old ``entry.split(':')`` behaviour
    silently renamed such a model to ``google/gemma-4-31b-it`` (ADR-176).
    """

    raw = str(text or "").strip()
    if not raw:
        return {}
    parts = raw.split(":")
    if (
        len(parts) >= 4
        and parts[-3] in CAPABILITY_TIERS
        and _is_number(parts[-2])
        and _is_number(parts[-1])
    ):
        name = ":".join(parts[:-3]).strip()
        if name:
            return {
                "model_name": name,
                "capability_tier": parts[-3],
                "input_cost_per_mtok": float(parts[-2]),
                "output_cost_per_mtok": float(parts[-1]),
            }
    elif len(parts) >= 2 and parts[-1] in CAPABILITY_TIERS:
        name = ":".join(parts[:-1]).strip()
        if name:
            return {"model_name": name, "capability_tier": parts[-1]}
    return {"model_name": raw}


def _is_number(text: str) -> bool:
    if not str(text).strip():
        return False
    try:
        float(text)
    except ValueError:
        return False
    return True


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
    key_status = ""
    if provider.api_key_encrypted:
        try:
            decrypted = decrypt_secret(provider.api_key_encrypted)
        except Exception:
            # The row survived a SECRET_KEY change: the ciphertext is intact but
            # nothing can read it any more. Saying so is the difference between an
            # operator re-entering a key and an operator hunting a bug.
            logger.warning("stored AI key failed to decrypt for %s", provider.name, exc_info=True)
            key_hint = "********"
            key_status = "undecryptable"
        else:
            key_hint = mask_secret(decrypted)
            key_status = "ok" if decrypted else "empty"
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
        "key_status": key_status,
        "models": [
            {
                "id": m.id,
                "model_name": m.model_name,
                "capability_tier": m.capability_tier,
                "input_cost_per_mtok": float(m.input_cost_per_mtok),
                "output_cost_per_mtok": float(m.output_cost_per_mtok),
                "is_active": bool(m.is_active),
            }
            for m in models
        ],
    }


def serialize_model(db: Session, model: AIModel) -> dict[str, Any]:
    """Public shape of one model row.

    ``provider_is_active`` rides along because the two switches are independent:
    without it the UI cannot tell a routable model from one whose provider is
    disabled, which is exactly the confusion ADR-173 closes.
    """

    provider = db.get(AIProvider, model.provider_id)
    return {
        "id": model.id,
        "provider_id": model.provider_id,
        "provider": provider.name if provider is not None else None,
        "provider_is_active": bool(provider.is_active) if provider is not None else False,
        "model_name": model.model_name,
        "capability_tier": model.capability_tier,
        "input_cost_per_mtok": float(model.input_cost_per_mtok or 0),
        "output_cost_per_mtok": float(model.output_cost_per_mtok or 0),
        "is_active": bool(model.is_active),
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


def save_provider_models(
    db: Session,
    provider_id: int,
    *,
    models: list[dict[str, Any]] | None = None,
    manual_models: list[str] | None = None,
) -> list[AIModel]:
    """Reconcile a provider's model catalogue with an explicit selection.

    ``models`` are verbatim entries (from the discovery list or the existing
    catalogue); ``manual_models`` are raw strings typed by the user and parsed
    with :func:`parse_model_entry`. A selected name is created when it is new and
    set active; the provider's other rows are only switched off — this call never
    deletes a model row, so history keeps resolving (ADR-176, ADR-177).
    """

    provider = db.get(AIProvider, provider_id)
    if provider is None:
        raise ProviderConfigError(f"provider {provider_id} not found")

    desired: dict[str, dict[str, Any]] = {}
    for item in models or []:
        spec = _entry_spec(item)
        name = str(spec.get("model_name") or "")
        if name:
            desired[name] = spec
    for text in manual_models or []:
        spec = parse_model_entry(text)
        name = str(spec.get("model_name") or "")
        if name:
            desired[name] = {**desired.get(name, {}), **spec}

    rows = db.scalars(
        select(AIModel).where(AIModel.provider_id == provider.id).order_by(AIModel.id)
    ).all()
    by_name = {row.model_name: row for row in rows}

    for name, spec in desired.items():
        row = by_name.get(name)
        if row is None:
            db.add(
                AIModel(
                    provider_id=provider.id,
                    model_name=name,
                    capability_tier=str(spec.get("capability_tier") or "standard"),
                    input_cost_per_mtok=float(spec.get("input_cost_per_mtok") or 0),
                    output_cost_per_mtok=float(spec.get("output_cost_per_mtok") or 0),
                    is_active=True,
                )
            )
            continue
        row.is_active = True
        if spec.get("capability_tier"):
            row.capability_tier = str(spec["capability_tier"])
        if spec.get("input_cost_per_mtok") is not None:
            row.input_cost_per_mtok = float(spec["input_cost_per_mtok"])
        if spec.get("output_cost_per_mtok") is not None:
            row.output_cost_per_mtok = float(spec["output_cost_per_mtok"])

    for row in rows:
        if row.model_name not in desired:
            row.is_active = False

    db.flush()
    return list(
        db.scalars(
            select(AIModel).where(AIModel.provider_id == provider.id).order_by(AIModel.id)
        ).all()
    )


def _entry_spec(item: Any) -> dict[str, Any]:
    """Normalise one catalogue entry without inventing fields it did not carry."""

    if isinstance(item, dict):
        spec: dict[str, Any] = {"model_name": str(item.get("model_name") or "").strip()}
        tier = item.get("capability_tier")
        if tier:
            spec["capability_tier"] = str(tier)
        for key in ("input_cost_per_mtok", "output_cost_per_mtok"):
            if item.get(key) is not None:
                spec[key] = float(item[key])
        return spec
    return {"model_name": str(item or "").strip()}


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


def update_model(db: Session, model_id: int, *, is_active: bool) -> AIModel:
    """Enable or disable one model row (ADR-173).

    Only ``is_active`` changes: no row is deleted, no foreign key moves, and the
    AI task / usage history that references this model stays readable. The one
    refusal is the last-routable-model guard — see :class:`ModelConfigError`.
    """

    model = db.get(AIModel, model_id)
    if model is None:
        raise ProviderConfigError(f"model {model_id} not found")

    provider = db.get(AIProvider, model.provider_id)
    if not is_active and model.is_active and provider is not None:
        is_default = (provider.default_model or None) == model.model_name
        if is_default:
            others = db.scalar(
                select(func.count())
                .select_from(AIModel)
                .where(
                    AIModel.provider_id == provider.id,
                    AIModel.id != model.id,
                    AIModel.is_active.is_(True),
                )
            )
            if not others:
                raise ModelConfigError(
                    f"'{model.model_name}' is the only active model of provider "
                    f"'{provider.name}' and it is that provider's default_model; "
                    "enable another model and point default_model at it, or "
                    "deactivate the provider itself"
                )

    model.is_active = is_active
    db.flush()
    return model


def delete_provider(db: Session, provider_id: int) -> str:
    """Delete a provider configuration, leaving its history intact.

    Configuration and history have separate lifecycles (ADR-177): the provider
    and its models go away, while every AI task and usage row is kept — with the
    provider/model names snapshotted onto it and the foreign keys cleared — so
    old runs and cost reports stay readable instead of turning into orphans.
    """

    provider = db.get(AIProvider, provider_id)
    if provider is None:
        raise ProviderConfigError(f"provider {provider_id} not found")

    from app.domain.models import AITask, AIUsage

    name = provider.name
    model_names = {
        row.id: row.model_name
        for row in db.scalars(select(AIModel).where(AIModel.provider_id == provider.id)).all()
    }
    # A history row may point at this provider, at one of its models, or at both
    # (a usage row can name a model without naming the provider), so both links
    # have to be collected before the models cascade away with the provider.
    task_filter = [AITask.provider_id == provider.id]
    usage_filter = [AIUsage.provider_id == provider.id]
    if model_names:
        task_filter.append(AITask.model_id.in_(model_names))
        usage_filter.append(AIUsage.model_id.in_(model_names))

    for task in db.scalars(select(AITask).where(or_(*task_filter))).all():
        if task.provider_id == provider.id:
            task.provider_name = task.provider_name or name
            task.provider_id = None
        if task.model_id is not None and task.model_id in model_names:
            # The model only ever belonged to this provider, so naming it here is
            # how a row that reached us through the model alone gets its name.
            task.model_name = task.model_name or model_names[task.model_id]
            task.provider_name = task.provider_name or name
            task.model_id = None

    for usage in db.scalars(select(AIUsage).where(or_(*usage_filter))).all():
        if usage.provider_id == provider.id:
            usage.provider_name = usage.provider_name or name
            usage.provider_id = None
        if usage.model_id is not None and usage.model_id in model_names:
            usage.model_name = usage.model_name or model_names[usage.model_id]
            usage.provider_name = usage.provider_name or name
            usage.model_id = None

    db.flush()
    db.delete(provider)
    db.flush()
    return name


def delete_model(db: Session, model_id: int) -> str:
    """Delete one model row, leaving its task/usage history readable (ADR-177)."""

    model = db.get(AIModel, model_id)
    if model is None:
        raise ProviderConfigError(f"model {model_id} not found")

    from app.domain.models import AITask, AIUsage

    provider = db.get(AIProvider, model.provider_id)
    provider_name = provider.name if provider is not None else None
    name = model.model_name

    for task in db.scalars(select(AITask).where(AITask.model_id == model.id)).all():
        task.model_name = task.model_name or name
        task.provider_name = task.provider_name or provider_name
        task.model_id = None

    for usage in db.scalars(select(AIUsage).where(AIUsage.model_id == model.id)).all():
        usage.model_name = usage.model_name or name
        usage.provider_name = usage.provider_name or provider_name
        usage.model_id = None

    if provider is not None and (provider.default_model or None) == name:
        # A dangling default_model would be synthesised back into a routable
        # option by the runtime catalogue fallback, resurrecting the row we just
        # removed from the catalogue.
        provider.default_model = None

    db.flush()
    db.delete(model)
    db.flush()
    return name
