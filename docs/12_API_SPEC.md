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

`POST /research/walk-forward`
`GET /research/runs/{id}`

## Paper Accounts

`GET /paper/accounts`
`POST /paper/accounts`
`GET /paper/accounts/{id}`
`GET /paper/accounts/{id}/equity`
`GET /paper/accounts/{id}/trades`
`POST /paper/accounts/{id}/reset`（强提醒并生成审计事件）

## Paper Positions

`GET /paper/positions`
`GET /paper/positions/{id}`

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
`POST /signals/scan`
`POST /signals/{id}/explain`
`POST /signals/{id}/acknowledge`

## Notifications

Generic webhook channel (V1). Secrets (URL, signing key) are write-only and
only ever returned masked.

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

`GET /github/sources`
`POST /github/sources`
`GET /github/sources/{id}`
`POST /github/sources/{id}/sync`
`GET /github/sources/{id}/history`

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

根据新的 17 表结构，可能需要以下附加端点：

### Market Data Sources API

`GET /data-sources`
`POST /data-sources`

### Feature Versions API

`GET /features/versions`
`GET /features/{feature_id}/versions`

### Strategy Bloodline API

`GET /strategies/{strategy_id}/bloodline`
`GET /strategies/{strategy_id}/lineage`

### AI Task Monitoring API

`GET /ai/tasks/{task_id}/status`
`POST /ai/tasks/{task_id}/cancel`

### Paper Trading API

`POST /paper/accounts/{account_id}/orders`
`GET /paper/accounts/{account_id}/positions/{asset_id}`
`POST /paper/accounts/{account_id}/orders/{order_id}/cancel`

### Backtest Comparison API

`POST /backtests/compare`
`GET /backtests/comparisons/{comparison_id}`