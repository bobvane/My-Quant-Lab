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
- 网格可混入**一个执行轴** `risk_pct`（docs/23 §6）：它属于 `execution.sizing` 而非策略
  参数；若策略不是 `risk_per_trade`，该点按 `risk_per_trade` 计算（否则扫出来全是相同的
  点）。其他 `sizing` 字段会被当作未声明参数拒绝。

## Monte Carlo

`POST /research/monte-carlo`

对一次**已完成**回测已落库的交易做有放回重采样，报告结果分布。

参数：`backtest_run_id`（已存储的回测运行）、`runs`（1–5000，默认 1000）、
`seed`（默认 0）、`trades_per_run`（可选，默认等于观测笔数）。

返回：`method`、`summary`（`final_equity`/`total_return`/`max_drawdown`/`sharpe`/`sortino`
的 p5/p25/p50/p75/p95，以及 `probability_of_profit`/`probability_of_loss`/
`probability_of_ruin`/`expected_total_return`/`expected_max_drawdown`/`worst_max_drawdown`）、
`sample_equity_paths`（最多 100 条，供扇形图）、`warnings`。

约束与语义：

- **不重跑回测**：只读该次运行已有的交易，所以分布锚定在被考察的那份结果上。
- `method = trade_level_iid_bootstrap`：对历史的再抽样，**不是预测**。
- 交易假定独立同分布，因此**低估**连续亏损概率；观测笔数 < 20 时写入 `warnings`。
- 运行不存在 → 404；状态非 completed / 无已成交交易 / 无正初始资金 / runs 越界 → 422。
- 相同 seed 必然得到相同分布；每次运行写审计事件 `monte_carlo_completed`。
- 年化周期取自该次回测的数据集周期，不接受调用方声明。

## Backtest 执行模型（仓位管理）

`POST /backtests` 的 `execution_overrides` 接受 `sizing`（docs/23）：
`{"mode": "fixed_fraction" | "risk_per_trade" | "atr_risk", "risk_pct": 0.01, "fraction": 0.5}`。

- `fixed_fraction` 为默认，与历史行为完全一致。
- 风险型模式按「入场价到止损价的距离」反推数量；缺失止损距离时回退为 `fixed_fraction`。
- 所有模式都受 `qty ≤ cash × 0.999 / fill` 约束（不产生杠杆）。
- `execution_model_json` 会记录 `sizing`，因此升级后重跑同一策略 `result_hash` 会变；
  历史回测记录保存的是当时快照，不受影响。

## Strategy Ensemble

`POST /research/ensemble`

把多个策略版本按**加权投票**合并成一个组合（docs/24）。

参数：`members`（`[{strategy_version_id, weight}]`，至少 1 个、最多 12 个）、`symbol`、
`timeframe`、`vote_threshold`（`[0,1)`，默认 0.5）、`execution_overrides`（组合的成本/资金/
仓位管理，键为 execution 字段，与 `POST /backtests` 同形）。

返回：`members`（含归一化权重）、`bars_evaluated`、`agreement`
（`entry_bars` / `exit_bars` / `entries_taken`）、`metrics`、`trades`、`equity_curve`、
`final_equity`、`warnings`。

约束与语义：

- 票数判定为**严格大于**阈值。等权两成员各占 0.5，故 `> 0.5` 需要**两个都同意**；
  用 `>=` 会让单个成员单独通过「多数」，集成退化为并集。
- **不拼接**各成员成交记录：本引擎是单仓位、单一现金账户，投票产出的是**一条**决策序列。
- `entry_bars` 是票数过阈值的 bar 数，`entries_taken` 是真正开仓次数；只有后者与成员的
  入场数可比（`entries_taken <= min(成员 entry_bars)`）。
- 成员之间无共同 bar → 422；预热期部分重叠 → 在共同 bar 上评估并写入 `warnings`。
- 成员规则引用该成员特征里不存在的列 → 报错，不静默丢弃该成员。
- 同一组成员 + 权重 + 数据集 + 阈值 → 完全相同的决策与指标。
- 每次运行写审计事件 `ensemble_completed`。

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