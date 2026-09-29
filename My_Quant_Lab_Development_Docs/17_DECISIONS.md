# 17 Architecture Decision Records

## ADR-001: Ghostfolio via REST, not direct DB

Decision: use REST adapter first.

Reason: lower schema coupling and keeps Ghostfolio as an external system boundary.

## ADR-002: Custom deterministic backtest core

Decision: build a focused internal engine rather than make a third-party framework the business core.

Reason: strategy DSL, paper trading and signal engine need the exact same semantics.

## ADR-003: AI is advisory/intelligence layer

Decision: AI never owns numeric truth or execution.

Reason: reproducibility and auditability.

## ADR-004: GitHub strategies normalized into DSL

Decision: all strategies pass through Strategy DSL.

Reason: future community growth without architecture rewrites.

## ADR-005: Immutable strategy versions

Decision: every rule change creates a new version.

Reason: historical results must remain reproducible.

## ADR-006: Paper accounts are separate

Decision: no writes from Quant Lab to Ghostfolio real portfolio.

Reason: protect real portfolio data and keep simulation experiments clean.

## ADR-007: Closed bars by default

Decision: strategy scans and backtests default to closed candles.

Reason: avoid signals based on unfinished information and reduce live/backtest mismatch.

## ADR-008: Provider abstraction

Decision: AI, market data, Ghostfolio and notifications are adapters.

Reason: avoid lock-in and allow replacement providers.

## ADR-009: No automatic trading in V1

Decision: deliberately exclude broker execution.

Reason: this project's value is learning, validation and information; automatic execution is not required for that goal.
