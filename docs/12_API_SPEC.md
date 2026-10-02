# 12 API Specification / V1 Endpoint Contract

API base: `/api/v1`

## Health

`GET /health`

返回：status、version、db、redis、workers。

## Assets

`GET /assets`
`GET /assets/{id}`

## Market Data

`GET /market-data/{symbol}`
`POST /market-data/sync`

参数：symbol、timeframe、start、end、provider。

## Market Data Series

`GET /market-data-series`
`GET /market-data-series/{id}`
`POST /market-data-series`

参数：asset_id、timeframe、provider、timezone。

## Market Data Snapshots

`GET /market-data-snapshots/{series_id}`
`GET /market-data-snapshots/latest/{series_id}`

## Features

`GET /features`
`GET /features/{id}`
`POST /features`

参数：name、type、inputs、outputs。

## Feature Snapshots

`GET /feature-snapshots/{series_id}/bar/{timestamp}`

## Strategies

`GET /strategies`
`POST /strategies`
`GET /strategies/{id}`
`POST /strategies/{id}/versions`
`GET /strategies/{id}/versions`
`GET /strategies/{id}/versions/{version}`
`POST /strategies/{id}/validate`
`POST /strategies/{id}/backtest`
`POST /strategies/import/github`

## Strategy Versions

`GET /strategy-versions`
`GET /strategy-versions/{id}`
`PUT /strategy-versions/{id}/activate`

## Strategy Parameters

`GET /strategy-parameters`
`GET /strategy-parameters/{id}`

## Backtests

`GET /backtests`
`GET /backtests/{id}`
`GET /backtests/{id}/trades`
`POST /backtests/{id}/compare`

## Backtest Results

`GET /backtest-results`
`GET /backtest-results/{id}`

## Backtest Metrics

`GET /backtest-metrics/backtest/{backtest_id}`

## Walk-forward/OOS

`POST /research/oos`  (single holdout split: last N% or from a date)

`POST /research/walk-forward`
`GET /research/runs/{id}`

## Parameter Sensitivity

`POST /research/sensitivity`

把一个或多个**已声明参数**（DSL 中 `period_ref` 指向的键）扫成笛卡尔网格，逐点独立回测。

参数：`strategy_version_id`、`symbol`、`timeframe`、`grid`（参数名 → 取值列表）、
`base_parameters`（可选，扫之前先套用的基线）、`metric`（排序/统计使用的目标指标，默认 `sharpe`）。

返回：`axes`、`points`（每点含 `parameters`/`metrics`/`objective`/`result_hash`）、
`summary`（mean/median/stdev/min/max/range/positive_ratio）、`best`、`worst`、`stable`。

约束与语义：

- 纯描述性，**不做参数寻优**；`best`/`worst` 是排序结果，不是推荐。
- 未知网格轴 → 422；网格点数 > 144 → 422；目标指标不在白名单 → 422。
- 目标指标未定义时该点 `objective = null`，并从排序与统计中剔除（未知不当 0）。
- 每次扫描写审计事件 `sensitivity_completed`。

## Paper Accounts

`GET /paper/accounts`
`POST /paper/accounts`
`GET /paper/accounts/{id}`
`GET /paper/accounts/{id}/equity`
`GET /paper/accounts/{id}/trades`
`POST /paper/accounts/{id}/reset`（强提醒并生成审计事件）

## Paper Positions

`GET /paper/accounts/{account_id}/positions`
`GET /paper/accounts/{account_id}/positions/{asset_id}`
`GET /paper/accounts/{account_id}/performance`  (equity-derived metrics)
`GET /paper/accounts/{account_id}/orders`
`POST /paper/accounts/{account_id}/execute`  (execute a persisted signal; virtual fill)
`POST /paper/accounts/{account_id}/close`
`POST /paper/accounts/{account_id}/reopen`
`POST /paper/accounts/{account_id}/fund`

## Paper Orders

`GET /paper/orders`
`GET /paper/orders/{id}`
`POST /paper/orders`

## Paper Trades

`GET /paper/trades`
`GET /paper/trades/{id}`
`GET /paper/trades/account/{account_id}`

## Signals

`GET /signals`
`GET /signals/{id}`
`GET /signals/{id}/evidence`   (feature snapshot + portfolio context)
`GET /signals/outcomes`
`GET /signals/outcome-summary`   (win rate / avg PnL grouped by direction/timeframe/state/strategy)
`POST /signals/scan`
`POST /signals/{id}/explain`
`POST /signals/{id}/acknowledge`

## Feature Snapshots

The exact feature row each signal was computed from (reproducible evidence).

`GET /feature-snapshots/{series_id}`
`GET /feature-snapshots/{series_id}/latest`

## Notifications

Multi-channel delivery: `webhook`, `feishu`, `telegram`, `pushplus`, `email`.
Channel secrets are write-only and only ever returned masked. `PUT` with
`channels` replaces the whole list; omitting a secret field keeps the stored
value.

`GET /notifications/config`
`PUT /notifications/config`
`POST /notifications/test`
`GET /notifications/events`

## Strategy Lifecycle

Deterministic, evidence-gated promotion/degradation. No AI is involved.

`GET /lifecycle/strategies`
`GET /lifecycle/strategies/{strategy_id}`
`POST /lifecycle/strategies/{strategy_id}/apply`

## AI Providers

`GET /ai/providers`
`POST /ai/providers`
`GET /ai/providers/{id}`
`PUT /ai/providers/{id}`

## AI Models

`GET /ai/models`
`GET /ai/models/provider/{provider_id}`

## AI Tasks

`GET /ai/tasks`
`GET /ai/tasks/{id}`
`POST /ai/tasks`

## AI Usage

`GET /ai/usage`
`GET /ai/usage/provider/{provider_id}`

## AI Prompts

`GET /ai/prompts`
`GET /ai/prompts/{id}`
`POST /ai/prompts`

## GitHub Sources

Implemented under `/importer/github/sources` (persisted on import; watched and
re-imported automatically when a new commit lands):

`GET /importer/github/sources`
`GET /importer/github/sources/{id}`
`GET /importer/github/sources/{id}/check`     (live commit check -> has_update)
`GET /importer/github/sources/{id}/snapshots`

## GitHub Snapshots

`GET /github/snapshots`
`GET /github/snapshots/{id}`

## Audit Logs

`GET /audit/logs`
`GET /audit/logs/entity/{entity_type}/{entity_id}`

## System Settings

`GET /settings`
`PUT /settings`

## API 规则

- 所有变更端点都需要幂等性（可重试）
- 长时间运行的任务返回作业 ID
- 错误使用结构化 JSON（code/message/details）
- 从不返回 API key
- 列表需要分页
- 所有策略和信号输出包含明确的版本和时间戳

## 扩展端点

状态标注：`[已实现]` 已在 API 中，`[计划]` 尚未实现。

### Market Data Sources API

`[已实现]` `GET /market-data/data-sources`（只读；源在同步时自动创建）

### Feature Versions API

`[已实现]` `GET /features/versions`
`[计划]` `GET /features/{feature_id}/versions`（特征定义为单版本、按 name 唯一，v1.0 不做）

### Strategy Lineage API

`[已实现]` `GET /strategies/{strategy_id}/lineage`

### AI Task Monitoring API

`[已实现]` `GET /ai/tasks`
`[已实现]` `GET /ai/tasks/{task_id}/status`（轻量状态轮询，含错误信息）
`[计划]` `POST /ai/tasks/{task_id}/cancel`（AI 调用为同步执行、无排队，v1.0 不做）

### Paper Trading API

`[已实现]` `GET /paper/accounts/{account_id}/orders`
`[已实现]` `GET /paper/accounts/{account_id}/positions`
`[已实现]` `GET /paper/accounts/{account_id}/positions/{asset_id}`

### Backtest Comparison API

`[已实现]` `GET /backtests/compare?ids=1,2`
`[计划]` `GET /backtests/comparisons/{comparison_id}`（对比为无状态即时计算，不持久化快照，v1.0 不做）