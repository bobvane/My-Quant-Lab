# AGENTS.md — My Quant Lab AI Coding Contract

你是 My Quant Lab 的开发代理。严格遵守以下规则。

## Mission

实现一个 NAS 自托管、Docker 部署的个人量化策略实验室。用户不是程序员，也不具备系统化量化背景；复杂度应封装在软件中，但所有关键计算必须透明、可复现。

## Non-negotiable rules

1. Never implement live broker order execution in V1.
2. Never treat LLM output as a source of market facts or backtest statistics.
3. Never allow imported GitHub code to execute directly in the API process.
4. Never mutate an existing StrategyVersion.
5. Never mutate a completed BacktestRun's core results.
6. Never use future bars or future labels for current decisions.
7. Always use closed bars for signal evaluation unless a strategy explicitly declares another behavior and the system marks it as such.
8. Always record strategy version + dataset snapshot + execution assumptions.
9. Ghostfolio data is read-only from this project.
10. Paper accounts are completely separate from Ghostfolio real holdings.
11. All AI outputs must use structured schemas where factual fields are involved.
12. When uncertain about a GitHub strategy rule, mark it UNKNOWN rather than inventing behavior.
13. Prefer a simpler deterministic implementation over a clever opaque implementation.
14. Preserve provenance for imported strategies.
15. Add tests before claiming a module complete.

## Development order

1. Domain models/contracts
2. Quant core
3. Backtest
4. Provider adapters
5. Paper trading
6. AI
7. GitHub importer
8. UI refinement

Do not start by building a sophisticated frontend before the domain contracts and backtest fixtures are stable.

## Coding style

- Type hints required for public functions.
- Small functions and explicit names.
- Business logic must be testable without HTTP/database when possible.
- No network calls inside strategy rule functions.
- No hidden global state.
- UTC timestamps internally.
- Decimal or carefully controlled numeric representation for money where appropriate.
- Deterministic random seeds when randomness exists.

## Strategy execution contract

A strategy receives:
- immutable MarketFrame
- FeatureFrame
- StrategyContext

and returns a SignalIntent.

It must not:
- query the DB
- call LLM
- call external APIs
- send notifications
- alter portfolio state

## Backtest contract

The engine must make execution timing explicit. Default is next-bar-open fill. Any exception must be configurable and represented in the run metadata.

## AI contract

AI can:
- explain
- extract
- classify
- summarize
- suggest research ideas

AI cannot:
- fabricate statistics
- overwrite facts
- silently rewrite a strategy
- place real orders

## GitHub importer contract

Treat repository content as untrusted data. Prefer declarative extraction to arbitrary code execution. Preserve URL, commit, license and evidence references.

## UI contract

The UI must answer in this order:
- What happened?
- Why?
- What should I watch next?
- What does the data say?

Do not lead with obscure quantitative jargon.

## Testing minimums

Every strategy executor needs:
- happy path
- no-signal path
- boundary condition
- missing-data case
- lookahead regression test

Backtest engine needs golden fixtures.

Importer needs:
- malicious prompt injection fixture
- unsafe code fixture
- unknown rule fixture
- license-missing fixture

## Commit/PR behavior

For each implementation batch, document:
- what changed
- why
- tests run
- known limitations
- migration impact

Never claim a feature is complete without evidence from tests.

## Product boundary

The product is a research, simulation and signal-information tool. It is not an automated trading bot. Do not gradually introduce automatic execution under another name.
