"""Settings endpoints.

Secrets can be written and cleared but are **never** returned: responses only
expose a masked value and whether a secret is present.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.data.strategy_service import record_audit
from app.domain.models import SystemSetting

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])

SECRET_PLACEHOLDER = "********"


def _serialize(row: SystemSetting) -> dict[str, Any]:
    if row.is_secret:
        return {
            "key": row.key,
            "is_secret": True,
            "value": SECRET_PLACEHOLDER,
            "is_set": row.value_json is not None,
            "description": row.description,
            "updated_at": row.updated_at,
        }
    return {
        "key": row.key,
        "is_secret": False,
        "value": (row.value_json or {}).get("value"),
        "description": row.description,
        "updated_at": row.updated_at,
    }


@router.get("", summary="List settings")
def list_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(SystemSetting).order_by(SystemSetting.key)).all()
    return {
        "settings": [_serialize(row) for row in rows],
        "environment": {
            "app_version": settings.app_version,
            "environment": settings.environment,
            "market_data_provider": settings.market_data_provider,
            "default_currency": settings.default_currency,
            "default_timezone": settings.default_timezone,
            "ai_daily_budget_usd": settings.ai_daily_budget_usd,
            "ghostfolio_configured": bool(settings.ghostfolio_base_url),
        },
    }


@router.put("", summary="Create or update a setting")
def upsert_setting(payload: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    key = payload.get("key")
    if not key:
        raise HTTPException(status_code=400, detail="key is required")
    if str(key).startswith("notification_"):
        # Notification settings have their own validated endpoint; keeping them
        # out of the generic path prevents bypassing URL/quiet-hours checks.
        raise HTTPException(
            status_code=400,
            detail="notification settings must be updated via /notifications/config",
        )
    is_secret = bool(payload.get("is_secret", False))
    value = payload.get("value")

    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    created = row is None
    if row is None:
        row = SystemSetting(key=key, is_secret=is_secret)
        db.add(row)
    row.is_secret = is_secret
    row.description = payload.get("description", row.description)
    row.value_json = None if value in (None, "") else {"value": value}
    record_audit(
        db,
        event_type="setting_updated" if not created else "setting_created",
        entity_type="system_setting",
        entity_id=key,
        action="update" if not created else "create",
        payload={"key": key, "is_secret": is_secret},
    )
    db.commit()
    db.refresh(row)
    return _serialize(row)


@router.get("/ai/providers", summary="List AI providers (keys never returned)")
def ai_providers(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.data.ai_provider_service import list_providers

    return {
        "providers": list_providers(db),
        "note": (
            "The AI layer is advisory: it explains engine-computed facts and never "
            "produces prices, returns or statistics."
        ),
    }


@router.post(
    "/ai/providers",
    status_code=201,
    summary="Create an AI provider (key is encrypted and never returned)",
)
def create_ai_provider(payload: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.api.schemas import AIProviderCreate
    from app.data.ai_provider_service import (
        ProviderConfigError,
        create_provider,
        serialize_provider,
    )

    try:
        spec = AIProviderCreate.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        provider = create_provider(
            db,
            name=spec.name,
            base_url=spec.base_url,
            api_key=spec.api_key,
            provider_type=spec.provider_type,
            default_model=spec.default_model,
            daily_budget_usd=spec.daily_budget_usd,
            is_active=spec.is_active,
            models=[m.model_dump() for m in spec.models],
        )
    except ProviderConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    record_audit(
        db,
        event_type="ai_provider_created",
        entity_type="ai_provider",
        entity_id=str(provider.id),
        action="create",
        payload={"name": provider.name, "base_url": provider.base_url},
    )
    db.commit()
    db.refresh(provider)
    return serialize_provider(db, provider)


@router.put("/ai/providers/{provider_id}", summary="Update an AI provider")
def update_ai_provider(
    provider_id: int, payload: dict[str, Any], db: Session = Depends(get_db)
) -> dict[str, Any]:
    from app.api.schemas import AIProviderUpdate
    from app.data.ai_provider_service import (
        ProviderConfigError,
        serialize_provider,
        update_provider,
    )

    try:
        spec = AIProviderUpdate.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        provider = update_provider(
            db,
            provider_id,
            name=spec.name,
            base_url=spec.base_url,
            api_key=spec.api_key,
            default_model=spec.default_model,
            daily_budget_usd=spec.daily_budget_usd,
            is_active=spec.is_active,
        )
    except ProviderConfigError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    record_audit(
        db,
        event_type="ai_provider_updated",
        entity_type="ai_provider",
        entity_id=str(provider.id),
        action="update",
        # Deliberately excludes the key: audit must never carry secrets.
        payload={"name": provider.name, "base_url": provider.base_url},
    )
    db.commit()
    db.refresh(provider)
    return serialize_provider(db, provider)


@router.delete("/ai/providers/{provider_id}", summary="Delete an unused AI provider")
def delete_ai_provider(provider_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.data.ai_provider_service import ProviderConfigError, delete_provider

    try:
        name = delete_provider(db, provider_id)
    except ProviderConfigError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    record_audit(
        db,
        event_type="ai_provider_deleted",
        entity_type="ai_provider",
        entity_id=str(provider_id),
        action="delete",
        payload={"name": name},
    )
    db.commit()
    return {"deleted": provider_id, "name": name}


@router.post("/ai/providers/{provider_id}/test", summary="Test a stored provider")
def test_stored_provider(provider_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.data.ai_provider_service import ProviderConfigError, test_connection
    from app.domain.models import AIProvider
    from app.infrastructure.secrets import decrypt_secret

    provider = db.get(AIProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="provider not found")
    if not provider.api_key_encrypted:
        return {"ok": False, "detail": "no API key stored for this provider", "models_found": []}
    try:
        api_key = decrypt_secret(provider.api_key_encrypted)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="stored key could not be decrypted") from exc
    try:
        return test_connection(provider.base_url, api_key)
    except ProviderConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/ai/providers/test", summary="Test credentials before saving")
def test_new_provider(payload: dict[str, Any]) -> dict[str, Any]:
    from app.api.schemas import AIProviderTestRequest
    from app.data.ai_provider_service import test_connection

    try:
        spec = AIProviderTestRequest.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return test_connection(spec.base_url, spec.api_key)


@router.get("/audit", summary="Recent audit events")
def audit_log(db: Session = Depends(get_db), limit: int = 50, offset: int = 0) -> dict[str, Any]:
    from app.domain.models import AuditLog

    rows = db.scalars(
        select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500)).offset(offset)
    ).all()
    return {
        "total": len(rows),
        "events": [
            {
                "id": r.id,
                "event_type": r.event_type,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "action": r.action,
                "payload": r.payload_json,
                "created_at": r.created_at,
            }
            for r in rows
        ],
    }


@router.get("/ghostfolio/test", summary="Test Ghostfolio connection")
def test_ghostfolio() -> dict[str, Any]:
    from app.data.ghostfolio import GhostfolioAdapter, GhostfolioError

    try:
        adapter = GhostfolioAdapter()
        result = adapter.test_connection()
        return {**result, "base_url": adapter.base_url}
    except GhostfolioError as exc:
        return {"ok": False, "detail": str(exc), "base_url": settings.ghostfolio_base_url or ""}


@router.get("/ghostfolio/holdings", summary="Ghostfolio holdings (read-only)")
def ghostfolio_holdings(debug: bool = False) -> dict[str, Any]:
    from app.data.ghostfolio import GhostfolioAdapter, GhostfolioError

    try:
        # The constructor is inside the try on purpose: an unconfigured Ghostfolio
        # raises while reading the settings, so construction outside the handler turned
        # a missing configuration into an unhandled 500 (ADR-067).
        adapter = GhostfolioAdapter()
        if debug:
            # Shape introspection only: key names / container types, never values.
            payload = adapter.get_holdings()
            root = payload if isinstance(payload, dict) else {}
            holdings = root.get("holdings")
            if isinstance(holdings, dict):
                sample: Any = sorted(holdings.keys())[:10]
            elif isinstance(holdings, list):
                sample = (
                    sorted(holdings[0].keys()) if holdings and isinstance(holdings[0], dict) else []
                )
            else:
                sample = None
            return {
                "root_keys": sorted(root.keys()),
                "accounts_type": type(root.get("accounts")).__name__,
                "holdings_type": type(holdings).__name__,
                "holdings_sample": sample,
            }
        return adapter.get_portfolio_summary(include_dividends=True)
    except GhostfolioError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
