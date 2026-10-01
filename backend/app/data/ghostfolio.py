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

    def get_holdings(self) -> dict[str, Any]:
        """Raw ``GET /api/v1/portfolio/holdings`` (current positions + prices)."""

        jwt = self._get_jwt()
        return self._get("/v1/portfolio/holdings", jwt)

    def get_portfolio_summary(self) -> dict[str, Any]:
        """Current holdings with market value and weight, for the evidence layer.

        Prefers Ghostfolio's ``/portfolio/holdings`` endpoint (it carries the
        current price and value); falls back to aggregating the export's
        activities when that endpoint is unavailable. Never trusts a single field
        name: Ghostfolio has shipped several spellings across versions.
        """

        try:
            payload = self.get_holdings()
            holdings = self._parse_holdings_payload(payload)
        except GhostfolioError as exc:
            logger.warning("portfolio/holdings unavailable (%s); aggregating activities", exc)
            export = self.get_export()
            holdings = self._parse_activities(export.get("activities", []))
            accounts = export.get("accounts", [])
            return self._summarise(holdings, accounts_count=len(accounts), source="activities")

        accounts = payload.get("accounts") if isinstance(payload, dict) else None
        accounts_count = len(accounts) if isinstance(accounts, list) else 0
        return self._summarise(holdings, accounts_count=accounts_count, source="holdings")

    # -- parsing helpers -------------------------------------------------- #
    def _parse_holdings_payload(self, payload: Any) -> list[dict[str, Any]]:
        raw = payload.get("holdings") if isinstance(payload, dict) else None
        items: list[tuple[str, dict[str, Any]]] = []
        if isinstance(raw, dict):
            items = [(key, value) for key, value in raw.items() if isinstance(value, dict)]
        elif isinstance(raw, list):
            items = [
                (str(item.get("symbol") or ""), item) for item in raw if isinstance(item, dict)
            ]

        holdings: list[dict[str, Any]] = []
        for key, item in items:
            symbol = str(item.get("symbol") or key or "").upper()
            if not symbol:
                continue
            quantity = _to_float(item.get("quantity"))
            price = _to_float(
                item.get("marketPrice")
                or item.get("marketPriceInBaseCurrency")
                or item.get("price")
            )
            value = _to_float(item.get("valueInBaseCurrency") or item.get("value"))
            if value is None and price is not None:
                value = quantity * price
            allocation = _to_float(item.get("allocationInPercentage") or item.get("allocation"))
            holdings.append(
                {
                    "symbol": symbol,
                    "name": str(item.get("name") or item.get("asset") or symbol),
                    "quantity": quantity,
                    "price": price,
                    "value": value,
                    "investment": _to_float(item.get("investment")),
                    "currency": str(item.get("currency") or "USD"),
                    "allocation_pct": _as_pct(allocation),
                }
            )
        return holdings

    def _parse_activities(self, activities: list[Any]) -> list[dict[str, Any]]:
        """Fallback: net quantity per symbol from the export's activities."""

        agg: dict[str, dict[str, Any]] = {}
        for activity in activities:
            if not isinstance(activity, dict):
                continue
            profile = activity.get("SymbolProfile") or activity.get("symbolProfile") or {}
            if not isinstance(profile, dict):
                profile = {}
            symbol = str(
                activity.get("symbol")
                or profile.get("symbol")
                or activity.get("dataSourceSymbol")
                or ""
            ).upper()
            if not symbol:
                continue
            entry = agg.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "name": str(profile.get("name") or symbol),
                    "quantity": 0.0,
                    "price": None,
                    "value": None,
                    "investment": 0.0,
                    "currency": str(activity.get("currency") or profile.get("currency") or "USD"),
                    "allocation_pct": None,
                },
            )
            quantity = _to_float(activity.get("quantity")) or 0.0
            activity_type = str(
                activity.get("type") or activity.get("Type") or activity.get("activityType") or ""
            ).upper()
            if activity_type == "BUY":
                entry["quantity"] += quantity
            elif activity_type == "SELL":
                entry["quantity"] -= quantity
        return [entry for entry in agg.values() if entry["quantity"] > 0]

    def _summarise(
        self, holdings: list[dict[str, Any]], *, accounts_count: int, source: str
    ) -> dict[str, Any]:
        active = [h for h in holdings if (h.get("quantity") or 0) > 0]
        total_value = sum(h["value"] for h in active if h.get("value") is not None)
        for holding in active:
            if holding.get("allocation_pct") is None and total_value > 0 and holding.get("value"):
                holding["allocation_pct"] = round(holding["value"] / total_value * 100, 4)
        return {
            "accounts_count": accounts_count,
            "holdings": active,
            "holdings_count": len(active),
            "total_value": round(total_value, 2) if total_value else None,
            "source": source,
        }


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_pct(value: float | None) -> float | None:
    if value is None:
        return None
    # Ghostfolio reports allocation as a 0-1 fraction in some versions and as a
    # percentage in others; normalise to 0-100.
    return round(value * 100, 4) if value <= 1 else round(value, 4)
