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

回测摘要除指标外还返回 `dataset_version_id` / `symbol` / `timeframe`：只有 id 无法判断两次
回测是否可比（同一策略在不同标的/周期上的结果本就不同），集成对比表读这两个字段来标记
「不同数据窗口」。

`POST /backtests` 与 `GET /backtests/{id}` 都返回 `warnings`，且**是同一份**：引擎的警告
（被忽略的参数覆盖、warm-up 长于数据）跟结果一起存进 `backtest_results.warnings_json`
（迁移 `0007_backtest_result_warnings`，ADR-054）。此前 `GET` 硬编码返回空列表，警告只在创建
响应里出现一次，刷新即消失。`warnings` 非空意味着下面的数字要打折扣阅读——例如
`only 400 bars available, warm-up needs 900` 对应的就是一次 `number_of_trades = 0` 的
「成功」回测。列表端点 `GET /backtests` 的摘要**不含** `warnings`。

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

返回：`axes`、`points`（每点含 `parameters`/`metrics`/`objective`/`result_hash`/`warnings`/
`warmup_unmet`）、`grid_points`、`evaluated_points`、`ranked_points`、`warmup_unmet_points`、
`warnings`、`summary`（mean/median/stdev/min/max/range/positive_ratio）、`best`、`worst`、
`stable`。

约束与语义：

- 纯描述性，**不做参数寻优**；`best`/`worst` 是排序结果，不是推荐。
- 未知网格轴 → 422；网格点数 > 144 → 422；目标指标不在白名单 → 422。
- 目标指标未定义时该点 `objective = null`，并从排序与统计中剔除（未知不当 0）。
- **整段落在策略预热期内的点不会被当成结果**（ADR-055）：它的指标是「没跑起来」的扁平
  0，因此 `warmup_unmet = true`，从 `best`/`worst`/`summary`/`stable` 中剔除，并在
  `warnings` 里说明。`ranked_points` 才是排名实际依据的样本数；`evaluated_points` 仍
  包含这些点（它们的 `objective` 是 0 而不是 `null`）。
- `sensitivity_version` 当前为 `1.1.0`。
- 每次扫描写审计事件 `sensitivity_completed`。
- 网格可混入**一个执行轴** `risk_pct`（docs/23 §6）：它属于 `execution.sizing` 而非策略
  参数；若策略不是 `risk_per_trade`，该点按 `risk_per_trade` 计算（否则扫出来全是相同的
  点）。其他 `sizing` 字段会被当作未声明参数拒绝。
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

返回：`members`（含归一化权重与认同统计）、`member_runs`（每个成员在**同一批 bar、同一套成本
模型**下的独立跑分，见下）、`bars_evaluated`、`agreement`、`metrics`、`trades`、`equity_curve`、
`final_equity`、`warnings`，以及 `dataset_version_id` / `symbol` / `timeframe` /
`engine_version` / `feature_version`（标识本次投票所在的数据集与引擎版本）。

同口径成员跑分（`member_runs`，与 `members` 等长同序，见 docs/24 第 6 节）：

- `label` / `weight` / `initial_capital`（= 组合初始资金 × 归一化权重，各成员之和恰为组合
  初始资金）/ `final_equity` / `entries_taken` / `metrics` / `equity_curve`。
- 由**与组合相同的模拟器**（`ensemble.py` 的 `_simulate`）算出，故与组合完全可比：差异只能
  来自决策序列，不能来自执行假设。
- 每个成员是**独立账户**的模拟，不是「组合同时持有多个仓位」（引擎单仓位单现金账户）。

认同统计（用于回答「谁被投票否决了」，见 docs/24 第 5 节）：

- `agreement.signalled_bars` / `solo_signalled_bars` / `entry_support_rate` /
  `exit_support_rate`：至少一个成员想入场的 bar 数 / 其中只有单个成员想入场（因而被否决）的
  bar 数 / 入场意愿的存活比例 / 退出信号的存活比例。
- `members[].entry_votes` / `entry_agreed` / `solo_entries` / `entry_support_rate` /
  `vote_agreement_rate`：该成员提议的入场根数 / 其中票数过阈值的根数 / 单独提议被否决的根数 /
  `entry_agreed / entry_bars`（无提议时为 `null`）/ 与最终结果一致的全部 bar 占比。

约束与语义：

- 票数判定为**严格大于**阈值。等权两成员各占 0.5，故 `> 0.5` 需要**两个都同意**；
  用 `>=` 会让单个成员单独通过「多数」，集成退化为并集。
- **不拼接**各成员成交记录：本引擎是单仓位、单一现金账户，投票产出的是**一条**决策序列。
- `entry_bars` 是票数过阈值的 bar 数，`entries_taken` 是真正开仓次数；只有后者与成员的
  入场数可比（`entries_taken <= min(成员 entry_bars)`）。
- 成员的收益/回撤/夏普**不在本响应里**：它们来自 `GET /backtests`（各成员自己的历史回测），
  其数据集/成本模型可能与本次集成不同；要跟集成比就用 `member_runs`，它才是同口径的。
- 成员之间无共同 bar → 422；预热期部分重叠 → 在共同 bar 上评估并写入 `warnings`。
- **同一版本重复出现 → 422**：两条同版本会归一化成 0.5+0.5，使「严格超过阈值」被该版本
  自己的信号满足，报告会显示一次实际只有一个参与者的「投票」。想加大某策略话语权请调高
  它的权重，而不是重复添加。
- 已被取代的版本（非 current）**仍可作为成员**：策略版本是不可变快照。
- 成员规则引用该成员特征里不存在的列 → 报错，不静默丢弃该成员。
- 同一组成员 + 权重 + 数据集 + 阈值 → 完全相同的决策与指标。
- 每次运行写审计事件 `ensemble_completed`。

## Ensemble Vote-Threshold Sweep

`POST /research/ensemble/sweep`

把**同一批成员**在多个 `vote_threshold` 上各跑一次，返回每个阈值下的结果（docs/24 第 7 节，
ADR-052）。这是**描述性**端点：它展示这个旋钮的台阶形状，**不推荐阈值**（与参数敏感性扫描
同族，理由相同：在同一个数据集上挑最好的那个就是过拟合）。

参数：与 `POST /research/ensemble` 相同的 `members` / `symbol` / `timeframe` /
`execution_overrides`（基类的 `vote_threshold` 被忽略），外加可选 `thresholds`
（显式阈值列表，各值在 `[0, 1)`，不可重复）。

- 省略 `thresholds` → 服务端只用**答案会发生变化**的阈值，即 `possible_votes` 中严格落在
  `(0, 1)` 内的值。加权票是成员权重之和，只能落在联盟总数上，所以两个相邻票数之间的阈值
  行为完全相同。
- `thresholds` 为空列表 / 超过 `max_thresholds` 个 / 有重复 / 有值不在 `[0, 1)` → `422`。
- 默认网格的联盟边界多于 `max_thresholds`（成员权重互不相同时很容易发生）→ `422`，报错会给出
  边界个数与上限，并指向显式 `thresholds`。**上限是拒绝而不是截断**：截断会悄悄丢掉台阶，而
  这张图的意义正是「哪些台阶被跳过」。

返回：

- `thresholds`（实际评估的阈值，升序）、`possible_votes`（加权票的所有可能取值，升序）、
  `max_thresholds`（一次扫描的阈值个数上限；客户端不要硬编码它，这个数字会随实现变化）、
  `members`（`label` / `weight` / `weight_share`）、`bars_evaluated`、`initial_capital`、
  `warnings`，以及 `dataset_version_id` / `symbol` / `timeframe` / `engine_version` /
  `feature_version`。
- `points[]`：每个阈值一项 —— `vote_threshold`、`effective_vote`（`possible_votes` 中第一个
  **严格大于**该阈值的票数，即该阈值实际在等哪个联盟）、`entries_taken`、`entry_bars`、
  `signalled_bars`、`solo_signalled_bars`、`final_equity`、`total_return`、`max_drawdown`、
  `sharpe`、`win_rate`、`number_of_trades`。
- **不返回** `equity_curve` / `trades` / `member_runs`：曲线不在本端点契约内（每个点各带
  一条曲线会让响应体积失控）；需要曲线时用 `POST /research/ensemble`。

语义：

- 同一阈值下，扫描点与 `POST /research/ensemble` **逐项一致**：两者共用
  `_prepare_ensemble` + `_run_vote`，特征与成员决策只评估一次并复用。若能做到不一致，这张图
  描述的将是用户无法复现的集成。
- 票数比较按**发布的六位小数精度**进行（`possible_votes` / `effective_vote` / 引擎判定同口径）。
  否则 `1/12 = 0.0833333…` 会在原始浮点下越过发布的 `0.083333`，出现「报告说需要两个成员、
  模拟却让一个成员进场」。联盟总数只在求和结束后取整一次，逐级取整会累积漂移。
- 阈值升高时 `entries_taken` / `entry_bars` 单调不增（可交易 bar 不会变多）。
- `effective_vote` 不是「需要几个成员」，而是「第一个能过线的票数」：三成员各 `1/3` 时，
  阈值 `0.3` 的 `effective_vote` 是 `1/3`。
- 每次运行写审计事件 `ensemble_sweep_completed`。

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

`POST /importer/github/analyze` is read-only and returns the review surface: the
findings, the draft DSL, and a `coverage` block (ADR-056, docs/05 §4.1).

- `analysis_version` (currently `1.2.0`) rises whenever the report's field semantics
  change. It moved to `1.1.0` when `files_scanned` was split into `files_parsed` and
  `files_inventoried` (non-Python files are inventoried, not parsed) and
  `files_skipped` became `[{path, reason}]` instead of bare paths; to `1.2.0` when
  `coverage` learned to separate "the cap stopped it" from "the time budget stopped it".
- `max_files` (1–30, default 12) caps how many candidates are fetched; `max_seconds`
  (10–600, default 120) is the wall-clock budget for the whole fetch (ADR-057). The
  per-request timeout cannot bound a loop of 30 files with retries, so the loop has a
  budget of its own and stops when it is spent.
- `coverage` = `candidate_files`, `candidate_python_files`, `cap`, `attempted_files`,
  `downloaded_files`, `parsed_files`, `inventoried_files`, `skipped_files`,
  `not_attempted_files`, `unread_python_files`, `complete`, `max_seconds`,
  `budget_exhausted`. The numbers describe the fetch, not the repo's docs: `1` candidate
  file and `cap = 1` are different facts. `budget_exhausted` decides which advice
  applies — raise the budget, or raise `max_files` (the cap was never reached).
- `warnings` carries the coverage sentences (never fetched / stopped after the budget /
  unread Python / unread after fetch) plus the DSL-builder warnings. They are not
  advisory decoration: a non-empty list means the findings may be missing rules that
  live in files the analysis never read.
- The last_import_status of a watched source can be `incomplete`: the watcher refuses
  to import from a partial read and records the coverage gap in the snapshot instead.
  A gap caused by the budget or the network is recorded as `transient` and does **not**
  advance the source's `current_commit`, so the next scheduled check retries it.

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