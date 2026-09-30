"""Ghostfolio REST adapter (read-only).

Ghostfolio requires a two-step authentication flow:
  Step 1: POST /api/v1/auth/anonymous  {"accessToken": "<128-char token>"}
          → HTTP 201, returns {"authToken": "<jwt>"}
  Step 2: GET /api/v1/export with Authorization: Bearer <jwt>
          → returns full portfolio data (accounts, activities, holdings)

The access token (128-char string from Ghostfolio Settings) can NOT be used
directly as a Bearer token. Each collection cycle gets a fresh JWT.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

__all__ = ["GhostfolioAdapter", "GhostfolioError"]

_TIMEOUT = 15.0


class GhostfolioError(RuntimeError):
    """Raised when Ghostfolio API is unreachable or returns an error."""


class GhostfolioAdapter:
    """Read-only REST client for the Ghostfolio API (two-step auth)."""

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = (base_url or settings.ghostfolio_base_url or "").rstrip("/")
        self._access_token = token or settings.ghostfolio_api_key or ""
        if not self.base_url:
            raise GhostfolioError("GHOSTFOLIO_BASE_URL is not configured")
        if not self._access_token:
            raise GhostfolioError("GHOSTFOLIO_API_KEY is not configured")

    def _get_jwt(self) -> str:
        """Step 1: exchange the access token for a temporary JWT."""

        url = f"{self.base_url}/api/v1/auth/anonymous"
        try:
            # proxy=None: Ghostfolio is on the LAN, never route through proxy
            with httpx.Client(proxy=None, timeout=_TIMEOUT) as client:
                response = client.post(
                    url,
                    json={"accessToken": self._access_token},
                    headers={"Content-Type": "application/json"},
                )
        except Exception as exc:
            raise GhostfolioError(
                f"cannot reach Ghostfolio at {self.base_url}: {type(exc).__name__}"
            ) from exc

        if response.status_code == 401:
            raise GhostfolioError(
                "401 — access token rejected by Ghostfolio. "
                "Check GHOSTFOLIO_API_KEY matches the token from Ghostfolio Settings."
            )
        if response.status_code == 404:
            raise GhostfolioError(
                f"404 — auth endpoint not found at {url}. Check GHOSTFOLIO_BASE_URL is correct."
            )
        if response.status_code not in (200, 201):
            raise GhostfolioError(
                f"Ghostfolio auth returned HTTP {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        jwt = data.get("authToken")
        if not jwt:
            raise GhostfolioError("Ghostfolio auth response missing 'authToken' field")
        return jwt

    def _get(self, path: str, jwt: str) -> Any:
        """Step 2: call a data endpoint with the temporary JWT."""

        url = f"{self.base_url}/api{path}"
        try:
            with httpx.Client(proxy=None, timeout=_TIMEOUT) as client:
                response = client.get(
                    url, headers={"Authorization": f"Bearer {jwt}", "Accept": "application/json"}
                )
        except Exception as exc:
            raise GhostfolioError(
                f"Ghostfolio request failed for {path}: {type(exc).__name__}"
            ) from exc
        if response.status_code == 401:
            raise GhostfolioError("401 — JWT expired or invalid")
        if response.status_code >= 400:
            raise GhostfolioError(f"Ghostfolio returned HTTP {response.status_code} for {path}")
        try:
            return response.json()
        except Exception as exc:
            raise GhostfolioError("Ghostfolio response is not valid JSON") from exc

    def test_connection(self) -> dict[str, Any]:
        """Verify the full two-step auth + data flow."""

        jwt = self._get_jwt()
        data = self._get("/v1/export", jwt)
        accounts_count = len(data.get("accounts", []))
        activities_count = len(data.get("activities", []))
        return {
            "ok": True,
            "detail": (
                f"connected — {accounts_count} account(s), {activities_count} activity/activities"
            ),
            "accounts_count": accounts_count,
            "activities_count": activities_count,
        }

    def get_export(self) -> dict[str, Any]:
        """Full portfolio export: accounts, activities, holdings, etc."""

        jwt = self._get_jwt()
        return self._get("/v1/export", jwt)

    def get_portfolio_summary(self) -> dict[str, Any]:
        """Aggregate portfolio summary for the signal evidence layer.

        Handles all known Ghostfolio export field name variations:
        - Profile key: SymbolProfile / symbolProfile / AssetProfile
        - Type key: type / Type / activityType
        - Symbol key inside profile: symbol / dataSourceSymbol / ticker
        """

        export = self.get_export()
        activities = export.get("activities", [])
        accounts = export.get("accounts", [])

        logger.info(
            "Ghostfolio export: %d activities, %d accounts",
            len(activities),
            len(accounts),
        )
        if activities:
            first = activities[0]
            logger.info(
                "First activity keys: %s, type=%s",
                list(first.keys()),
                first.get("type") or first.get("Type") or first.get("activityType"),
            )

        # Aggregate current holdings from activities
        holdings: dict[str, dict[str, Any]] = {}
        for activity in activities:
            # Try every known profile key variation
            profile = (
                activity.get("SymbolProfile")
                or activity.get("symbolProfile")
                or activity.get("AssetProfile")
                or activity.get("assetProfile")
                or {}
            )
            if not isinstance(profile, dict):
                continue

            # Try every known symbol key variation
            symbol = (
                profile.get("symbol")
                or profile.get("dataSourceSymbol")
                or profile.get("ticker")
                or ""
            )
            if not symbol:
                continue

            # Try every known type key variation
            activity_type = (
                activity.get("type") or activity.get("Type") or activity.get("activityType") or ""
            ).upper()

            quantity = float(activity.get("quantity") or activity.get("Quantity") or 0)

            if symbol not in holdings:
                name = profile.get("name") or profile.get("SymbolName") or symbol
                currency = activity.get("currency") or profile.get("currency") or "USD"
                holdings[symbol] = {
                    "symbol": symbol,
                    "name": name,
                    "quantity": 0.0,
                    "currency": currency,
                }

            if activity_type == "BUY":
                holdings[symbol]["quantity"] += quantity
            elif activity_type == "SELL":
                holdings[symbol]["quantity"] -= quantity

        active = {k: v for k, v in holdings.items() if v["quantity"] > 0}
        return {
            "accounts_count": len(accounts),
            "activities_count": len(activities),
            "holdings": list(active.values()),
            "holdings_count": len(active),
        }
