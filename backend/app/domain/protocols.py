"""Pluggable provider protocols.

Adding a new market-data source, AI vendor, notification channel or portfolio
source must not require changes in the domain/quant core (see ADR-008).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class MarketDataProvider(Protocol):
    """Read-only access to quotes and OHLCV history."""

    name: str

    def list_assets(self) -> list[dict[str, Any]]:
        """Return supported assets with symbol/name/asset_class/currency."""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: dt.datetime,
        end: dt.datetime,
        *,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """Return OHLCV bars with UTC index named ``timestamp``."""

    def get_quote(self, symbol: str) -> dict[str, Any]:
        """Return the latest quote for ``symbol``."""


@runtime_checkable
class AIProvider(Protocol):
    """LLM access. The AI layer may only *explain* pre-computed facts."""

    name: str

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float = 0.1,
        max_tokens: int | None = None,
    ) -> str: ...

    def structured_output(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...

    def list_models(self) -> list[str]: ...

    def health_check(self) -> bool: ...


@runtime_checkable
class NotificationProvider(Protocol):
    """Outbound notification channel (webhook, Feishu, Telegram, ...)."""

    name: str

    def send(self, title: str, body: str, *, meta: dict[str, Any] | None = None) -> bool: ...


@runtime_checkable
class PortfolioAdapter(Protocol):
    """Read-only view of the real portfolio (Ghostfolio)."""

    name: str

    def test_connection(self) -> bool: ...

    def sync(self) -> dict[str, Any]: ...

    def holdings(self) -> list[dict[str, Any]]: ...


@runtime_checkable
class StrategyExecutor(Protocol):
    """Deterministic strategy evaluation contract."""

    def evaluate(self, frame: Any, context: Any) -> Any: ...


__all__ = [
    "AIProvider",
    "Decimal",
    "MarketDataProvider",
    "NotificationProvider",
    "PortfolioAdapter",
    "StrategyExecutor",
]
