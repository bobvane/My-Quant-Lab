"""Ghostfolio REST adapter (read-only).

Quant Lab reads portfolio data from Ghostfolio to provide the "Portfolio
Context" layer for signals. It never writes to Ghostfolio (ADR-006).

Authentication: Bearer token from Ghostfolio Settings → Access Token.
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
    """Read-only REST client for the Ghostfolio API."""

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = (base_url or settings.ghostfolio_base_url or "").rstrip("/")
        self._token = token or settings.ghostfolio_api_key or ""
        if not self.base_url:
            raise GhostfolioError("GHOSTFOLIO_BASE_URL is not configured")
        if not self._token:
            raise GhostfolioError("GHOSTFOLIO_API_KEY is not configured")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

    def _get(self, path: str) -> Any:
        url = f"{self.base_url}/api{path}"
        try:
            response = httpx.get(url, headers=self._headers(), timeout=_TIMEOUT)
        except Exception as exc:
            raise GhostfolioError(
                f"cannot reach Ghostfolio at {self.base_url}: {type(exc).__name__}"
            ) from exc
        if response.status_code == 401:
            raise GhostfolioError("401 unauthorized — check GHOSTFOLIO_API_KEY")
        if response.status_code == 403:
            raise GhostfolioError("403 forbidden — token may be expired")
        if response.status_code >= 400:
            raise GhostfolioError(f"Ghostfolio returned HTTP {response.status_code} for {path}")
        try:
            return response.json()
        except Exception as exc:
            raise GhostfolioError("Ghostfolio response is not valid JSON") from exc

    def test_connection(self) -> dict[str, Any]:
        """Verify connectivity and return basic info."""

        data = self._get("/v1/portfolio/holdings")
        holdings = data if isinstance(data, list) else data.get("holdings", [])
        return {
            "ok": True,
            "detail": f"connected — {len(holdings)} holding(s) found",
            "holdings_count": len(holdings),
        }

    def get_holdings(self) -> list[dict[str, Any]]:
        """Current portfolio holdings: symbol, quantity, value, allocation."""

        data = self._get("/v1/portfolio/holdings")
        holdings = data if isinstance(data, list) else data.get("holdings", [])
        result = []
        for item in holdings:
            if not isinstance(item, dict):
                continue
            result.append(
                {
                    "symbol": item.get("symbol") or item.get("dataSourceSymbol") or "",
                    "name": item.get("name") or "",
                    "quantity": float(item.get("quantity") or 0),
                    "value": float(item.get("valueInBaseCurrency") or item.get("value") or 0),
                    "allocation_pct": float(item.get("allocationInPercentage") or 0) * 100,
                    "currency": item.get("currency") or "",
                    "asset_class": item.get("assetClass") or item.get("assetSubClass") or "",
                }
            )
        return result

    def get_accounts(self) -> list[dict[str, Any]]:
        """Accounts with balances."""

        data = self._get("/v1/accounts")
        accounts = data if isinstance(data, list) else data.get("accounts", [])
        result = []
        for item in accounts:
            if not isinstance(item, dict):
                continue
            result.append(
                {
                    "id": item.get("id") or "",
                    "name": item.get("name") or "",
                    "balance": float(item.get("balance") or 0),
                    "currency": item.get("currency") or "",
                }
            )
        return result

    def get_portfolio_summary(self) -> dict[str, Any]:
        """Aggregate portfolio summary for the signal evidence layer."""

        holdings = self.get_holdings()
        total_value = sum(h["value"] for h in holdings)
        by_symbol = {h["symbol"]: h for h in holdings}
        return {
            "total_value": round(total_value, 2),
            "holdings_count": len(holdings),
            "holdings": holdings,
            "by_symbol": by_symbol,
        }
