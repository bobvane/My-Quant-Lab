# 11 数据模型

本数据模型实现了新的架构决策，遵循 PostgreSQL 关系数据库设计，并确保策略版本不可变性。模型覆盖资产管理、市场数据、策略版本、回测结果、模拟交易、AI 提供商与资源监控等领域；**表的完整清单以 `backend/app/domain/models.py` 的 `Base.metadata` 为准**（此处不再复述表的数量，避免与代码漂移，见 ADR-095）。

## 核心实体

### Assets
存储所有支持的交易资产信息，包括股票、ETF、加密货币等。

```sql
id SERIAL PRIMARY KEY,
symbol VARCHAR(20) NOT NULL UNIQUE,
display_name VARCHAR(100),
asset_class VARCHAR(20) NOT NULL, -- stock, crypto, etf


## MarketDataSeries

- id
- asset_id
- timeframe
- source_id
- timezone
- adjusted
- dataset_version
- series_start
- series_end
- last_sync_at
- quality_status
- content_hash nullable
- is_archived

`content_hash` is the SHA-256 of every *closed* bar of the series, in timestamp order,
rendered exactly as `series_content_hash()` renders a frame (`frame.to_csv(float_format="%.10g")`).
It is refreshed by `upsert_bars()` — the one function both bar-writing paths go through —
so it always describes the rows that are actually stored (ADR-092). A series with no
closed bars has `NULL`, which is not the same thing as the hash of an empty frame.
Because a backtest without an explicit window loads every closed bar of its series, this
value equals the `backtest_runs.dataset_hash` a completed run records: a caller can
therefore confirm that the bars it is looking at are the bars a stored result was
computed from.

## OHLCVBar

- id
- series_id
- timestamp
- open
- high
- low
- close
- volume
- amount nullable
- is_closed
- source_hash

unique(series_id, timestamp)

## FeatureSnapshot

- id
- series_id
- bar_timestamp
- feature_version
- values_json
- input_hash

## Strategy

- id
- name
- description
- source_type
- source_url
- license
- status
- created_at

## StrategyVersion

- id
- strategy_id
- version
- dsl_json
- source_commit
- prompt_version nullable
- created_at
- immutable_hash

unique(strategy_id, version)

## BacktestRun

- id
- strategy_version_id
- dataset_snapshot_id
- parameters_json
- execution_model_json
- started_at
- finished_at
- status
- result_summary_json
- result_hash

## BacktestTrade

- id
- backtest_run_id
- symbol
- entry_time
- entry_price
- exit_time
- exit_price
- quantity
- fees
- slippage
- pnl
- r_multiple
- reason

## PaperAccount

- id
- name
- base_currency
- initial_cash（**历史命名**：入金与提现都会加减它，所以它存的其实是净入金；对外发布的
  名字是 `net_deposits`，见 ADR-066）
- cash
- status
- created_at

## PaperPosition

- id
- paper_account_id
- asset_id
- quantity
- avg_cost
- market_value

## Signal

- id
- strategy_version_id
- asset_id
- timeframe
- bar_timestamp
- state
- direction
- trigger_rules_json
- feature_snapshot_id
- portfolio_context_json
- generated_at
- notified_at
- status

## SignalOutcome

- id
- signal_id
- entry_time
- entry_price
- exit_time
- exit_price
- pnl
- mfe
- mae
- status

## AIJob

- id
- task_type
- provider
- model
- prompt_version
- input_hash
- output_json
- token_usage_json
- cost_estimate
- status
- created_at

## GitHubSource

- id
- repository_url
- default_branch
- current_commit
- license
- last_checked_at

## GitHubSnapshot

- id
- source_id
- commit
- fetched_at
- manifest_json
- content_hash

## AuditEvent

- id
- event_type
- actor
- entity_type
- entity_id
- payload_json
- created_at

## Data integrity rules

1. StrategyVersion immutable（`dsl_json` / `version` / `immutable_hash` 由数据库触发器拒绝改写）。触发器定义在 `backend/app/domain/immutability.py`，由 `Base.metadata` 的 `after_create` 事件安装 —— 任何用 `create_all()` 建出来的库（单元测试、探针脚本、开发者临库）与迁移出来的库都带同一份守卫（ADR-094）。
2. BacktestRun immutable after completed（允许追加 metadata，不允许改结果；`backtest_results` 一旦有 `result_hash`，summary/metrics/equity_curve 由同一份触发器锁住）。
3. Signal immutable core evidence。
4. Ghostfolio data is read-only mirror。
5. Paper data cannot alter real portfolio data。
6. All timestamps stored UTC; UI converts to user timezone.
