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

## Strategies

`GET /strategies`
`POST /strategies`
`GET /strategies/{id}`
`POST /strategies/{id}/versions`
`GET /strategies/{id}/versions`
`POST /strategies/{id}/validate`
`POST /strategies/{id}/backtest`
`POST /strategies/import/github`

## Backtests

`GET /backtests`
`GET /backtests/{id}`
`GET /backtests/{id}/trades`
`POST /backtests/{id}/compare`

## Walk-forward/OOS

`POST /research/walk-forward`
`GET /research/runs/{id}`

## Paper

`GET /paper/accounts`
`POST /paper/accounts`
`GET /paper/accounts/{id}`
`GET /paper/accounts/{id}/equity`
`GET /paper/accounts/{id}/trades`
`POST /paper/accounts/{id}/reset`（强提醒并生成审计事件）

## Signals

`GET /signals`
`GET /signals/{id}`
`POST /signals/scan`
`POST /signals/{id}/explain`
`POST /signals/{id}/acknowledge`

## Ghostfolio

`POST /integrations/ghostfolio/test`
`POST /integrations/ghostfolio/sync`
`GET /integrations/ghostfolio/status`

## AI

`GET /ai/providers`
`POST /ai/test`
`GET /ai/usage`
`POST /ai/explain-backtest`
`POST /ai/explain-signal`

## GitHub

`POST /github/import`
`GET /github/sources`
`POST /github/sources/{id}/sync`
`GET /github/sources/{id}/history`

## Settings

`GET /settings`
`PUT /settings`

## API rules

- All mutation endpoints require idempotency where jobs can be retried.
- Long-running tasks return job ID.
- Errors use structured JSON with code/message/details.
- Never return API keys.
- Pagination required for lists.
- All strategy and signal outputs include explicit versions and timestamps.
