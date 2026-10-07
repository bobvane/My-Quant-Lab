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
- progress（整数 0–100，默认 0；给界面看的粗刻度，不是精确百分比）
- current_step（短句，可空；就是下面刻度里的那一档名字）

`progress` 与 `current_step` 描述的是一次运行**正在做什么**，所以它们**不进** `result_hash`
（ADR-180，迁移 `0018_run_progress_paper_binding`）：哈希只覆盖策略版本、数据集、引擎版本、
特征版本、参数、指标与交易数，把运行过程元数据算进去会让同策略、同数据、同参数的重跑得到不同哈希，
ADR-081 的「数据集 + 哈希即可复现」随即失效。刻度是 `loading data` 5 → `computing features` 20 →
`running strategy` 45 → `evaluating exits` 70 → `computing metrics` 90 → `completed` 100
（`backend/app/data/backtest_service.py` 的 `PROGRESS_LADDER`）；`running strategy` 与
`evaluating exits` 是引擎自己的阶段、但没有可提交的边界，所以没有为它们编造百分比。

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

## StrategyExperiment

把一次量化研究存成**可回读的实体**（表 `strategy_experiments`）。`kind` 决定复用哪一种既有引擎，
实验层只负责记录与回读，不重实现任何量化算法。

- id
- name
- notes
- status（`draft` / `running` / `completed` / `failed` / `archived`）
- kind（`backtest` / `sensitivity` / `monte_carlo` / `walk_forward` / `oos`）
- strategy_version_id（FK `strategy_versions`，NOT NULL —— 血缘的起点）
- series_id（FK `market_data`）
- symbol
- timeframe
- parameters_json（**快照**：创建时写死）
- request_json（**快照**：创建时的完整请求，`POST /experiments/{experiment_id}/run` 用它重放；
  由旧回测收养时额外带 `adopted_from_backtest_run`）
- summary_json（标签、结果计数、`backtest_run_id`；收养时还逐字带上回测的 `result_hash` /
  `engine_version` / `feature_version` / `dataset_hash`）
- error_message（失败原因，引擎原文）
- created_at
- started_at
- completed_at
- updated_at
- archived_at
- initial_capital（**快照**：迁移 `0019_experiment_lifecycle` 新增）
- start_date（**快照**：迁移 `0019_experiment_lifecycle` 新增）
- end_date（**快照**：迁移 `0019_experiment_lifecycle` 新增）

`parameters_json` / `initial_capital` / `start_date` / `end_date` 是**快照列：只写一次**，之后不随
策略改动变化 —— 改策略版本的默认参数、改 `strategy_parameters` 行、或发布一个资金不同的新版本，
回读实验拿到的仍是当时那份配置。实验记录的是「当时用什么跑出来的」，不是「现在拿它会怎么跑」。
迁移 `0019_experiment_lifecycle` 给这张表加了 `updated_at` / `archived_at` / `initial_capital` /
`start_date` / `end_date` 五列，全部可空，所以已有的实验行照常可读。

## ExperimentResult

- id
- experiment_id（FK `strategy_experiments`，级联删除）
- kind
- label
- parameters_json（**快照**：该结果行自己那份参数）
- backtest_run_id（FK `backtest_runs`，可空 —— 经它建立 Experiment→StrategyVersion→BacktestResult
  血缘，删除实验不删除底层回测）
- metrics_json
- payload_json
- created_at

## PaperAccount

- id
- name
- base_currency
- initial_cash（**历史命名**：入金与提现都会加减它，所以它存的其实是净入金；对外发布的
  名字是 `net_deposits`，见 ADR-066）
- cash
- status
- created_at
- strategy_version_id（nullable，FK → `strategy_versions.id`）
- backtest_run_id（nullable，FK → `backtest_runs.id`，`ondelete="SET NULL"`）
- parameters_json（JSON，默认 `{}`）

`strategy_id` 单独回答不了「这个账户在验证哪个版本、哪套参数」，所以 ADR-181 加了上面三列
（迁移 `0018_run_progress_paper_binding`）：被验证的策略版本、建账户时复制的那次回测、以及当时的
参数快照。`backtest_run_id` 是 `SET NULL`——退役一次回测不能让一个复制了它结果的模拟账户消失，
账户里已经发生的成交也不会因为版本或回测被删而改变。三列都可空：手工建的账户不必绑定任何东西，
legacy 的 `strategy_id` 保留不变。

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

## AIRoleContract（表名 `ai_role_contracts`，v1.9.7 / ADR-150）

磁盘上的角色契约（`backend/app/ai/contracts/*.md`）在数据库里的索引；契约文件是事实来源，这一行只是「这个版本加载过、内容哈希是多少、用哪份输出 schema」的账本。

- id
- name
- version
- role
- task_types_json
- required_capabilities_json
- output_language
- content_hash
- output_schema_json
- source_path
- is_active
- created_at / updated_at

`(name, version)` 唯一。`AITask` 同时新增五列，把一次 AI 调用绑回它读过的来源与产出的策略：`role`、`output_hash`、`source_ids_json`、`research_run_id`、`strategy_version_id`（见 ADR-153）。

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

## AISourceSnapshot（表名 `ai_source_snapshots`，v2.1.0 / ADR-166）

平台自己读一份外部材料（网页 / PDF）时留下的**只追加**观测记录：每一次抓取写一行，同一个 URL 抓两次就是两行，永不覆盖。它回答的是「这次研究依据的是哪一份材料」，而不回答「AI 看到了多少」——给模型看的永远是 `excerpt` 里的片段。

- id
- source_type（`url` / `pdf`）
- url（请求的原始地址）
- final_url（跟随 redirect 之后的地址）
- status（`requested` / `blocked` / `fetch_failed` / `not_fetched` / `retained`）
- http_status
- content_type
- size_bytes
- source_hash（读到的**原始字节**的 sha256）
- text_hash（实际进入研究流程的**文本**的 sha256）
- parser / parser_version（`stdlib.html.parser` / `stdlib.text` / `pypdf`）
- robots_ok
- retention（`excerpt` / `full`）
- retained_chars
- truncated
- excerpt（第三方默认 ≤ 500 字符，见 ADR-161）
- license_note
- error
- metadata_json（`source_ref`、`label`、`warnings`）
- fetched_at
- created_at / updated_at

索引 `ix_ai_source_snapshots_url_time (url, created_at)`。**没有全文列**（不存在 `full_text` / `content` / `body` / `raw_text` / `raw` / `blob` / `payload`），这条黑名单由 `backend/tests/test_source_snapshot_migration.py` 断言。

`parse_status` 与 `status` 是两个维度：`status` 说抓取走到哪一步，`parse_status`（`ok` / `unsupported` / `parse_failed` / `not_parsed`）说解析结果；`status = blocked` 时 `parse_status = not_parsed`。`status = retained` 可以配 `parse_status = unsupported`（例如扫描件——留了观测，但没交给研究者）。

三个 hash 各回答一个问题，**不合并**：

- `source_hash`：材料被改过没有（原始字节）；
- `text_hash`：这一版引文是拿哪一版文本对的（读入文本）；
- `source_snapshot_hash`：AI runtime / cache 的研究上下文身份——它只属于 `backend/app/ai/runtime.py`，**不在这张表里，也不得改名成 `source_hash`**。

`research_artifacts` 新增可空外键 `snapshot_id` → `ai_source_snapshots.id`（迁移 `0015_source_snapshots`），指向**本次实际使用**的那一行；调用方自己提供文本（`user_input` / `text` / `github_file`）的来源没有抓过，因此可为 NULL。

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
7. Source snapshots are append-only observations：同一 URL 抓两次写两行，不覆盖旧行；`research_artifacts.snapshot_id` 只指向本次实际使用的那一行，且第三方全文没有可写的列（ADR-166）。

## 派生视图（Phase C：不改表、不加表）

绩效 / 风险 / 对照**没有自己的表**，它们是 `backtest_results` 的**派生视图**（ADR-187、ADR-188）：

- 输入就是已经存下来的东西——`backtest_results.metrics_json`、`equity_curve_json`（逐 bar 的 `timestamp/equity/cash/position_value/close`）与 `backtest_trades`；对照那一列由同一段曲线的 `close` 推导（`initial_capital × close_t / close_0`），或在曲线没有收盘价时按同一 `dataset_version_id` 与同一时间区间读一次行情。
- 计算发生在 `backend/app/research/analysis.py` 的纯函数里（`ANALYSIS_VERSION` 标出口径版本），结果**只进响应、不落库**：`GET /api/v1/backtests/{run_id}/analysis` 是只读端点，零写入。因此同一条 run 反复请求得到逐字相同的数，`result_hash` 不受影响，`Metrics` 的语义与哈希组成也不动。
- 引擎已经算过的指标（总收益、年化、波动率、夏普、索提诺、最大回撤、交易类指标）一律读存储值，分析层只补引擎没有的那些（卡玛比率、下行波动率、回撤持续天数、恢复期、最差单月、样本档位）；**同一个数字只有一个出口**，前端不重算。
- 派生读数在 `backend/app/capabilities.py` 里登记为 `analysis_metrics` 与 `comparisons` 两组（能力清单必须能从代码派生，不许手抄），未实现的缺口（VaR/CVaR 等）仍留在 `UNSUPPORTED_CAPABILITIES` 里。
