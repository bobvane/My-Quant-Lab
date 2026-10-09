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
import math
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

    def get_portfolio_summary(self, *, include_dividends: bool = False) -> dict[str, Any]:
        """Current holdings with market value and weight, for the evidence layer.

        Prefers Ghostfolio's ``/portfolio/holdings`` endpoint (it carries the
        current price and value); falls back to aggregating the export's
        activities when that endpoint is unavailable. Never trusts a single field
        name: Ghostfolio has shipped several spellings across versions.
        """

        try:
            payload = self.get_holdings()
            holdings = self._parse_holdings_payload(payload)
            accounts_count = _count_accounts(payload)
        except GhostfolioError as exc:
            logger.warning("portfolio/holdings unavailable (%s); using activities", exc)
            holdings, accounts_count = [], None

        if not holdings:
            # The holdings endpoint shape varies between Ghostfolio versions; the
            # export is the long-standing, documented source. Always fall back so
            # the caller never gets an empty portfolio when activities exist.
            export = self.get_export()
            holdings = self._parse_activities(export.get("activities", []))
            summary = self._summarise(
                holdings, accounts_count=_count_accounts(export), source="activities"
            )
        else:
            summary = self._summarise(
                holdings, accounts_count=accounts_count or 0, source="holdings"
            )

        if include_dividends:
            self._merge_dividends(summary)
        return summary

    def _merge_dividends(self, summary: dict[str, Any]) -> None:
        from app.data.symbols import canonical_symbol

        history = self.get_dividend_history()
        if not history:
            return
        for holding in summary.get("holdings", []):
            info = history.get(canonical_symbol(str(holding.get("symbol"))))
            if info:
                holding["last_dividend_date"] = info.get("last_dividend_date")
                holding["last_dividend_per_share"] = info.get("last_dividend_per_share")
                holding["dividends_total"] = round(info.get("dividends_total", 0.0), 2)

    # -- parsing helpers -------------------------------------------------- #
    def _parse_holdings_payload(self, payload: Any) -> list[dict[str, Any]]:
        raw = payload.get("holdings") if isinstance(payload, dict) else None
        # Some versions wrap the payload (e.g. {"data": {"holdings": ...}}).
        if raw is None and isinstance(payload, dict):
            for wrapper in ("data", "portfolio", "result"):
                inner = payload.get(wrapper)
                if isinstance(inner, dict) and isinstance(inner.get("holdings"), (dict, list)):
                    raw = inner["holdings"]
                    break
        items: list[tuple[str, dict[str, Any]]] = []
        if isinstance(raw, dict):
            items = [(key, value) for key, value in raw.items() if isinstance(value, dict)]
        elif isinstance(raw, list):
            items = [
                (str(item.get("symbol") or ""), item) for item in raw if isinstance(item, dict)
            ]

        holdings: list[dict[str, Any]] = []
        for key, item in items:
            if isinstance(item.get("holding"), dict):
                item = {**item, **item["holding"]}
            # Ghostfolio keeps the identity in a nested profile; the symbol is
            # not a top-level field on holdings items.
            profile = (
                item.get("assetProfile")
                or item.get("symbolProfile")
                or item.get("SymbolProfile")
                or item.get("profile")
                or {}
            )
            if not isinstance(profile, dict):
                profile = {}
            symbol = str(item.get("symbol") or profile.get("symbol") or key or "").upper()
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
            cost_total = _to_float(item.get("investment"))
            cost_per_share = cost_total / quantity if cost_total is not None and quantity else None
            annual_dividend = _to_float(item.get("dividend"))
            dividend_yield = (
                annual_dividend / price * 100 if annual_dividend is not None and price else None
            )
            holdings.append(
                {
                    "symbol": symbol,
                    "name": str(item.get("name") or profile.get("name") or symbol),
                    "asset_class": profile.get("assetClass") or profile.get("assetSubClass"),
                    "quantity": quantity,
                    "cost_per_share": cost_per_share,
                    "price": price,
                    "value": value,
                    "investment": cost_total,
                    # Raw as sent: the payload's unit is decided once, below.
                    "allocation_pct": allocation,
                    "unrealized_pnl": _to_float(item.get("netPerformance")),
                    "unrealized_pnl_pct": _to_float(item.get("netPerformancePercent")),
                    "first_activity_date": item.get("dateOfFirstActivity"),
                    "annual_dividend_per_share": annual_dividend,
                    "dividend_yield_pct": dividend_yield,
                    "currency": str(item.get("currency") or profile.get("currency") or "USD"),
                }
            )
        _normalise_percentages(holdings)
        return holdings

    def get_dividend_history(self) -> dict[str, dict[str, Any]]:
        """Per-symbol dividend info derived from the export's DIVIDEND activities.

        Holdings themselves do not carry a pay date; the activity log does. Keys
        are canonical symbols so they line up with our market tickers.
        """

        from app.data.symbols import canonical_symbol

        try:
            export = self.get_export()
        except GhostfolioError as exc:
            logger.warning("dividend history unavailable (%s)", exc)
            return {}

        out: dict[str, dict[str, Any]] = {}
        for activity in export.get("activities", []):
            if not isinstance(activity, dict):
                continue
            if str(activity.get("type") or activity.get("Type") or "").upper() != "DIVIDEND":
                continue
            profile = activity.get("SymbolProfile") or activity.get("symbolProfile") or {}
            symbol = str(activity.get("symbol") or (profile or {}).get("symbol") or "").upper()
            if not symbol:
                continue
            key = canonical_symbol(symbol)
            date = activity.get("date")
            per_share = _to_float(activity.get("unitPrice"))
            quantity = _to_float(activity.get("quantity")) or 0.0
            entry = out.setdefault(
                key,
                {
                    "last_dividend_date": None,
                    "last_dividend_per_share": None,
                    "dividends_total": 0.0,
                },
            )
            if date and (
                entry["last_dividend_date"] is None or str(date) > str(entry["last_dividend_date"])
            ):
                entry["last_dividend_date"] = date
                entry["last_dividend_per_share"] = per_share
            if per_share is not None:
                entry["dividends_total"] += per_share * quantity
        return out

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
        total_value = _active_total_value(active)
        for holding in active:
            if holding.get("allocation_pct") is None and total_value > 0 and holding.get("value"):
                holding["allocation_pct"] = round(holding["value"] / total_value * 100, 4)

        total_cost = sum(h["investment"] for h in active if h.get("investment") is not None)
        realized = sum(h["unrealized_pnl"] for h in active if h.get("unrealized_pnl") is not None)
        has_pnl = any(h.get("unrealized_pnl") is not None for h in active)
        total_pnl = realized if has_pnl else (total_value - total_cost if total_cost else None)
        total_pnl_pct = (
            round(total_pnl / total_cost * 100, 4) if total_pnl is not None and total_cost else None
        )
        return {
            "accounts_count": accounts_count,
            "holdings": active,
            "holdings_count": len(active),
            "total_value": round(total_value, 2) if total_value else None,
            "total_cost": round(total_cost, 2) if total_cost else None,
            "total_pnl": round(total_pnl, 2) if total_pnl is not None else None,
            "total_pnl_pct": total_pnl_pct,
            "source": source,
        }


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_pct(value: float | None, scale: float | None = None) -> float | None:
    if value is None:
        return None
    if scale is None:
        # No evidence in the payload about how it writes percentages; fall back
        # to the legacy per-value reading.
        return round(value * 100, 4) if value <= 1 else round(value, 4)
    return round(value * scale, 4)


def _active_total_value(holdings: list[dict[str, Any]]) -> float:
    """Market value the payload itself knows about, over the rows it carries."""

    return sum(h["value"] for h in holdings if h.get("value") is not None)


def _normalise_percentages(holdings: list[dict[str, Any]]) -> None:
    """Turn the payload's raw percentage fields into percent points, once.

    Ghostfolio has shipped both conventions: ``allocationInPercentage: 0.25``
    meaning 25 % and ``allocationInPercentage: 25`` meaning 25 %. Deciding per
    value cannot work — ``0.5`` is a legitimate 0.5 % in one convention and 50 %
    in the other — so the unit is decided once for the whole payload, from the
    money amounts the payload carries alongside the percentages.
    """

    total_value = _active_total_value(holdings)
    allocation_refs = [
        (h["value"] / total_value * 100) if total_value > 0 and h.get("value") else None
        for h in holdings
    ]
    pnl_refs = [
        (h["unrealized_pnl"] / h["investment"] * 100)
        if (h.get("investment") or 0) > 0 and h.get("unrealized_pnl") is not None
        else None
        for h in holdings
    ]
    allocation_scale = _percent_scale([h.get("allocation_pct") for h in holdings], allocation_refs)
    pnl_scale = _percent_scale([h.get("unrealized_pnl_pct") for h in holdings], pnl_refs)
    for holding in holdings:
        holding["allocation_pct"] = _as_pct(holding.get("allocation_pct"), allocation_scale)
        holding["unrealized_pnl_pct"] = _as_pct(holding.get("unrealized_pnl_pct"), pnl_scale)


def _percent_scale(raws: list[float | None], references: list[float | None]) -> float | None:
    """Return 100.0 (values are 0–1 fractions), 1.0 (already percent points), or
    None when the payload offers no evidence and the legacy per-value reading
    has to decide.

    ``references`` are the same quantity computed from the payload's own money
    amounts (``value / total * 100``, ``unrealized_pnl / investment * 100``):
    comparing the two candidate readings against them is what lets a payload
    whose holdings are all under 1 % be read correctly.
    """

    pairs = [
        (raw, reference)
        for raw, reference in zip(raws, references, strict=True)
        if raw is not None and reference is not None and raw > 0 and reference > 0
    ]
    if pairs:

        def error(scale: float) -> float:
            return sum(
                abs(math.log10(raw * scale) - math.log10(reference)) for raw, reference in pairs
            )

        as_fraction = error(100.0)
        as_percent = error(1.0)
        if as_fraction != as_percent:
            return 100.0 if as_fraction < as_percent else 1.0

    known = [raw for raw in raws if raw is not None]
    if not known:
        return None
    # A 0–1 fraction cannot exceed 1, so a single such number settles the unit.
    if any(raw > 1 for raw in known):
        return 1.0
    # Fractions sum to ≈1 across a portfolio, percent points to ≈100.
    total = sum(known)
    if total >= 50:
        return 1.0
    if total <= 1.5:
        return 100.0
    return None


def _count_accounts(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    accounts = payload.get("accounts")
    if isinstance(accounts, dict):
        return len(accounts)
    if isinstance(accounts, list):
        return len(accounts)
    return 0
