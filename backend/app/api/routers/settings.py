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


@router.get("/ai/providers", summary="Configured AI providers (keys never returned)")
def ai_providers(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.domain.models import AIModel, AIProvider

    providers = db.scalars(select(AIProvider).order_by(AIProvider.id)).all()
    return {
        "providers": [
            {
                "id": p.id,
                "name": p.name,
                "provider_type": p.provider_type,
                "base_url": p.base_url,
                "default_model": p.default_model,
                "is_active": p.is_active,
                "daily_budget_usd": float(p.daily_budget_usd),
                "api_key_set": bool(p.api_key_encrypted),
                "models": [
                    {
                        "id": m.id,
                        "model_name": m.model_name,
                        "capability_tier": m.capability_tier,
                        "input_cost_per_mtok": float(m.input_cost_per_mtok),
                        "output_cost_per_mtok": float(m.output_cost_per_mtok),
                    }
                    for m in db.scalars(select(AIModel).where(AIModel.provider_id == p.id)).all()
                ],
            }
            for p in providers
        ],
        "note": (
            "The AI layer is advisory: it explains engine-computed facts and never "
            "produces prices, returns or statistics."
        ),
    }


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
