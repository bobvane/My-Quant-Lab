# 12 API Specification / V1 Endpoint Contract

API base: `/api/v1`

状态标注：本文档每一行端点声明都带且只带一个标记 —— `[已实现]`（API 今天真的提供）、`[计划]`（尚未实现）、`[取消]`（从未实现，或已撤回）。`backend/tests/test_api_spec_truth.py` 把这三组声明与 `create_app().openapi()` 的真实路由表双向绑定：文档写了却没服务的 `[已实现]` 会失败，服务了却没写的路由也会失败。

## Health

`GET /healthz` [已实现] —— 仅存活探针（liveness）：不碰数据库、Redis、worker，也不读迁移版本。

`GET /health` [已实现] —— 存活 + 依赖健康。

返回：status、version、database、migration、redis、workers、environment、feature_version、engine_version。

每个依赖探针都有 1 秒上限（`PROBE_TIMEOUT_SECONDS`，ADR-069）：连不上时 `redis` 报 `unavailable`、`workers` 报 `unknown`，而不是让整个响应等十几秒。词表只有 `"N online"` / `"0 online"` / `"unknown"` 三种；`/healthz` 不碰任何依赖。

`migration` 是**数据库自己报出的** alembic 版本号（ADR-071），不是镜像里写的那个：`docker/entrypoint.sh` 先跑 `alembic upgrade head` 再起服务，但回滚或手工迁移之后两者可能不一致，所以这里问的是库。取不到版本表（表不存在、语句失败）报 `"unknown"`，版本表存在但没有行（迁移从未运行）报 `"none"`。

`status` 只有 `"healthy"` / `"degraded"` 两种，并且**要求 database 连得上且 migration 说得出名字**：库连得上但结构版本是 `unknown`/`none` 时报 `"degraded"` —— 能连上库不等于库结构是对的。

`GET /system/info` [已实现] —— 运行时与引擎版本（runtime / engine versions）。

## Assets

`GET /assets` [已实现] —— 列出资产。
`GET /assets/{asset_id}` [已实现] —— 取单个资产。
`POST /assets` [已实现] —— 创建资产。
`GET /assets/{asset_id}/series` [已实现] —— 列出某个资产下的行情数据序列。

## Market Data

`GET /market-data/latest/{symbol}` [已实现] —— 取某标的最新若干根 K 线。

`POST /market-data/sync` [已实现] —— 同步一个标的的 OHLCV 数据。

参数：symbol、timeframe、start、end、provider。这些是 **JSON 请求体**（`MarketDataSyncRequest`）的字段，**不是 query 参数** —— 曾经被印成 query 参数，按那份文档发请求只会拿到 422。

响应里的 `inserted` 是**新写入**的 K 线数，`updated` 是**被改写**的已存在 K 线数（ADR-118）：一根在同步时还没收盘的 K 线会在它收盘之后的下一次同步里被改写（收盘价、最高最低、`is_closed`），因此第二次同步的 `inserted` 常常是 0 而 `updated` 不是 0。`closed_bars_in_fetch` 与 `still_forming_bars` 是这一次取回的数据里已经收盘/仍在形成的根数。

`timeframe` 必须是**数据源真正服务的周期**：`SUPPORTED_TIMEFRAMES` 目前是 1m / 5m / 15m / 1h / 1d / 1w，而默认的 synthetic provider 只服务 `1d`；请求别的周期返回 **400**（`UnsupportedTimeframe`），不会静默回落成日线（ADR-120）。未知标的是 400（`SymbolNotServed`）。

序列解析（哪个标的、哪个周期用哪一份数据）由 `resolve_series` 一处回答（ADR-119）：请求里显式给 `series_id` 时它必须存在且与 `symbol`/`timeframe` 一致（矛盾 → 422，缺失 → 404），否则取该 `(asset, timeframe)` 下**未归档且覆盖到最远**的那一行（平手取最新一行），一行都没有 → 404 `no market data for '<symbol>' <timeframe>; sync first`。同一标的存在多份数据（不同来源/数据集版本）时，这是唯一一条回答。

## Market Data Series

`GET /market-data/series` [已实现] —— 列出行情数据序列。
`GET /market-data/series/{series_id}` [已实现] —— 取单条序列。
`GET /market-data/series/{series_id}/bars` [已实现] —— 列出该序列的 K 线。
`DELETE /market-data/series/{series_id}` [已实现] —— 归档一条序列，但绝不让可复现的回测变得不可复现：历史结果的 `backtest_runs.dataset_version_id` 就是「这份结果算自哪份数据」的证据。
`POST /market-data/series/{series_id}/restore` [已实现] —— 把已归档的序列恢复回来。

创建序列的请求体字段：asset_id、timeframe、provider、timezone。序列由同步创建，本族没有独立的创建端点。

## Features

`GET /features` [已实现] —— 返回本引擎实际计算的特征目录（`name` / `feature_type` / `feature_version` / `description` / `inputs` / `params` / `is_deterministic` / `lookahead_safe`，按 `name` 排序，**无 `id`**）。目录来自代码（`backend/app/features/catalogue.py`），不是数据库表：该端点曾经读一张从未被写入过的 `features` 表，因此它一直返回空列表（ADR-093）。目录与 `build_features()` 的真实产出由 `backend/tests/test_feature_catalogue.py` 双向校验。

特征版本端点（引擎构建的版本与已落库的快照）见文末「扩展端点」的 Feature Versions API。

## Strategies

`GET /strategies` [已实现] —— 列出策略。
`POST /strategies` [已实现] —— 创建策略。
`GET /strategies/{strategy_id}` [已实现] —— 取单个策略。
`DELETE /strategies/{strategy_id}` [已实现] —— 删除策略及其全部版本。
`POST /strategies/{strategy_id}/versions` [已实现] —— 创建一个新的不可变策略版本。`make_current` 默认为 `true`，但只有本次校验结论是 `valid` 时才会成为当前版本：非 `valid` 且 `make_current=true` 时返回 422（`{"detail": "strategy version is '<status>', not 'valid'; pass make_current=false to record it without making it current"}`）且**一行都不写**（既不写版本，也不动原当前版本）；`make_current=false` 时 `valid`/`pending`/`invalid` 都可以被记录（ADR-171）。
`GET /strategies/{strategy_id}/versions` [已实现] —— 列出某个策略的版本。
`POST /strategies/validate` [已实现] —— 校验一份 DSL 文档（不落库）。请求体就是**裸 DSL 对象**本身（不是 `{dsl: …}` 包裹，也没有别的字段）；响应是 `StrategyValidationOut`：`is_valid`（没有任何 `error` 级问题时为真）、`issues`（每条形如 `{severity, code, message, path}`，`path` 可为 null）、`available_columns`（校验器认识的列名，排序后给出）。DSL 连结构都解析不了时返回 200 且 `is_valid=false`，只给一条 `code="schema_error"`、`path=null` 的 error，`available_columns` 为空 —— 400/422 只留给请求体本身不是 JSON 的情况。前端「从 GitHub 导入」的第 6 步渲染的就是这两个字段（ADR-113）。

## Strategy Versions

`GET /strategy-versions` [已实现] —— 列出策略版本。
`GET /strategy-versions/{version_id}` [已实现] —— 取单个策略版本。
`PUT /strategy-versions/{version_id}/activate` [已实现] —— 激活某个版本。只有 `validation_status == "valid"` 的版本可以被激活；`pending`/`invalid` 返回 422（`{"detail": "strategy version is '<status>', not 'valid'"}`），并把这次拒绝记为审计事件 `strategy_version_activation_rejected`（ADR-171）。
`GET /strategy-versions/{version_id}/parameters` [已实现] —— 该版本已存储的参数。
`GET /strategies/versions/{version_id}/verify` [已实现] —— 校验某个策略版本没有被改动过。

## Backtests

`GET /backtests` [已实现] —— 列出回测运行。
`GET /backtests/{run_id}` [已实现] —— 取一次回测结果（`BacktestOut`）。
`GET /backtests/{run_id}/analysis` [已实现] —— 一次回测的绩效 / 风险 / 买入持有对照（`AnalysisOut`）。
`GET /backtests/{run_id}/trades` [已实现] —— 列出一次回测的成交。
`GET /backtests/compare` [已实现] —— 并排对比若干次回测运行（`?ids=1,2`）。
`GET /backtests/comparisons/{comparison_id}` [计划] —— 对比为无状态即时计算，不持久化快照，v1.0 不做。
`POST /backtests` [已实现] —— 运行一次回测。
`DELETE /backtests/{run_id}` [已实现] —— 删除一次回测运行及其结果。
`POST /backtests/{run_id}/explain` [已实现] —— 解释一次已完成的回测。
`POST /backtests/{run_id}/explain-performance` [已实现] —— 用普通人语言解释一次已完成回测的绩效 / 风险 / 买入持有对照（Phase C，`ExplainOut`）。数字全部来自 `GET /backtests/{run_id}/analysis`；AI 未配置、预算耗尽或被守卫拒绝（编造数字 / 预测措辞）时该端点返回错误，数字块照常显示。

回测摘要除指标外还返回 `dataset_version_id` / `symbol` / `timeframe`：只有 id 无法判断两次
回测是否可比（同一策略在不同标的/周期上的结果本就不同），集成对比表读这两个字段来标记
「不同数据窗口」。

摘要还返回 `dataset_version` 与 `source`（ADR-119）：`dataset_version_id` 说的是「哪一行
`market_data_series`」，这两个字段说的是「哪个数据集版本、哪个数据来源」。同一
`(asset, timeframe)` 可以有多行序列（唯一键包含 `source_id` 与 `dataset_version`），所以
一次回测的结果必须能说明它读的是哪一行，否则 `dataset_hash` 有了、来源却没有。选择哪一行
由 `resolve_series` 一处回答（见 Market Data），创建时若 `series_id` 与 `symbol`/`timeframe`
自相矛盾会得到 **422**。

创建回测与单次回测查询都返回 `warnings`，且**是同一份**：引擎的警告
（被忽略的参数覆盖、warm-up 长于数据）跟结果一起存进 `backtest_results.warnings_json`
（迁移 `0007_backtest_result_warnings`，ADR-054）。此前单次查询硬编码返回空列表，警告只在创建
响应里出现一次，刷新即消失。`warnings` 非空意味着下面的数字要打折扣阅读——例如
`only 400 bars available, warm-up needs 900` 对应的就是一次 `number_of_trades = 0` 的
「成功」回测。列表端点 `/backtests` 的摘要**不含** `warnings`。

`engine_version` / `feature_version` 报告的是**真正跑过的那一次计算**（ADR-116：
`ENGINE_VERSION` 与 `FEATURE_VERSION` 由代码提供，不再由路由写死字面量）。两者都是
`result_hash` 的输入，所以换一个版本号就等于换一份计算：同一条策略在引擎语义变化后重跑会得到
另一个哈希，而**已经落库的历史回测保存的是当时的快照**，不受影响。

回测运行是可观察的（ADR-180）：`backtest_runs` 上有 `progress`（整数 0–100，默认 0）与
`current_step`（短句，可空），单次查询与列表摘要都返回这两个字段；失败的运行还返回
`error_message`（运行中与成功时为 `null`）。`progress` 是给界面看的粗刻度而不是精确百分比，
刻度是 `loading data` 5 → `computing features` 20 → `running strategy` 45 →
`evaluating exits` 70 → `computing metrics` 90 → `completed` 100。本期引擎是一趟调用
（ADR-174），可观测的跳变只有 `computing features` → `computing metrics`；`running strategy`
与 `evaluating exits` 是引擎自己的阶段却没有可提交的边界，所以没有为它们编造一个百分比。
`progress` 与 `current_step` **不进** `result_hash`：哈希只覆盖策略版本、数据集、引擎版本、
特征版本、参数、指标与交易数，把运行过程元数据算进去会让同策略同数据同参数的重跑得到不同哈希
（ADR-081 的「数据集 + 哈希即可复现」失效）。

取单次回测（`/backtests/{run_id}`）对**还没有结果的运行不再是 409**：行照常返回，结果字段
（`metrics` / `equity_curve` / `trades` / `result_hash` / `parameters` / `execution_model`）
一律**缺省**为 `null`，而不是给一份空结果——空权益曲线会被读成「一次平盘回测」。`status`、
`progress`、`current_step` 说明它在做什么，`error_message` 说明它为什么停下（ADR-180）。

创建回测有两条路径，由设置 `backtest_async`（环境变量 `BACKTEST_ASYNC`，也接受
`MQL_BACKTEST_ASYNC` 拼写，默认 `false`，见 `.env.example` 与 `docker-compose.yml`）选择：

- `false`（默认）：在请求内同步跑完，响应就是**已完成**的运行（`status = "completed"`）并直接
  带上结果字段；既有调用方的响应形状与耗时都不变。
- `true`：只做校验、写入运行行（`status = "running"`、`progress = 0`、
  `current_step = "loading data"`）并交给 Celery 任务 `quantlab.run_backtest`，随即返回 **200**
  且结果字段缺省。这条运行已经被持久化，客户端轮询 `/backtests/{run_id}` 看 `progress` 与
  `current_step` 前进；该路径需要容器里的 Celery worker（`quantlab-app` 的四个子进程之一，
  与 API 同镜像同容器）。失败时这一行落 `status = "failed"` 并把
  引擎的错误原文逐字写进 `error_message`，绝不留下半截结果（warnings 口径不变，ADR-054）。

**没有取消端点**：本轮不实现取消，接口里也不会预先写一个不存在的动作。

### 回测分析（Phase C）

绩效 / 风险 / 对照的唯一入口是上表那条分析端点，它**只读、零写入、零重算**：
它读的是这次运行已经落库的逐 bar 权益曲线（`backtest_results.equity_curve_json`）与指标块
（`metrics_json`），不读行情、不落新表、不改 `result_hash`（ADR-187、ADR-188）。

- `performance.stored` 是引擎那份指标快照**逐字原样**（引擎仍是唯一的比率计算处），
  `performance.derived` 是 Phase C 事后派生的数字（`calmar` / `downside_deviation` /
  `excess_return` / `final_equity_gap` / `worst_bar_return`）。两者分列，读者能分清哪个是
  「跑出来的」、哪个是「事后算的」。
- `risk` 给最大回撤、最长回撤持续（bar 数 + 折算天数）、恢复期（窗口结束仍未回本时
  `recovery_bars = null` 且 `recovered = false`）、最差单 bar / 最差自然月、最差一笔交易、
  最长连亏、下行波动。
- `benchmark` 是**买入持有对照**（`kind = "buy_and_hold"`，中文一律称「对照」——「基准」在本
  项目已指净入金分母，ADR-066）。主路径直接用曲线里每根 bar 已经存下的 `close`：
  `对照权益_t = 初始资金 × close_t / close_0`，与策略**同一批 bar、同一窗口、同一日历**，
  所以时间区间公平性不是一条约定而是结构性的（`source = "equity_curve_close"`）。只有曲线里
  没有可用收盘价时，才按曲线首尾时间戳回读该运行自己的序列（`source = "series_bars"`，
  `only_closed=true`），K 线对不齐时 `window_matched = false` 并给
  `caveat: benchmark_partial_window`。对照**不计手续费**（`fees_included = false`），这一点对
  策略是保守的，所以必须显式写出而不是藏着。
- `sample` 说明样本量（交易数 / bar 数 / 年数）与结论强度档位（`insufficient` /
  `preliminary` / `enough`），阈值就是项目已有的两道证据闸门（交易数 10 与 20），不新造。
- `caveats` 是机器可读的 `{code, message}` 列表：任何一项无法计算时，对应字段是 `null`
  **而不是 0**，并在这里说明原因。

状态码：运行不存在 → **404**；运行还没完成（或失败）导致没有结果行 → **409**
（`only a completed run can be analysed`）；**权益曲线为空不是错误** —— 返回 **200**，比率字段
为 `null` 并给出 `caveat: no_equity_curve`。实验侧不新增端点：`ExperimentSummaryOut` 已经带
`backtest_run_id`，实验详情复用同一个端点。

## Backtest Metrics

`GET /backtest-metrics/backtest/{backtest_id}` [已实现] —— 一次回测运行的指标。

## Walk-forward/OOS

`POST /research/oos` [已实现]  (single holdout split: last N% or from a date)

`POST /research/walk-forward` [已实现]

参数：`strategy_version_id`、`symbol`、`timeframe`、`train_bars`（默认 250，≥60）、
`test_bars`（默认 60，≥20）、`step`（≥1）。

返回：`windows`（**窗口总数**，语义不变）、`train_bars`、`test_bars`、`segments`
（每段含 `train`/`out_of_sample`，其中 `out_of_sample.warmup_unmet` 表示这一段没有真正被测量）、
`summary`（`mean_is_return`/`mean_oos_return`/`positive_oos_windows`/`consistency`）、
`measured_oos_windows`、`unmeasured_oos_windows`、`warnings`。

语义（ADR-196）：测试段短于策略预热期的窗口没有被真正测量，逐段标 `warmup_unmet`，
并且**不计入** `mean_oos_return` / `positive_oos_windows` / `consistency` 的分母；这几个读数
在没有任何窗口被真正测量时是 `null`（不是 `0`），`warnings` 会说明有几个窗口落在预热期里。

## Parameter Sensitivity

`POST /research/sensitivity` [已实现]

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

## Monte Carlo

`POST /research/monte-carlo` [已实现]

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
- 相同 seed 必然得到相同分布：重采样池按 `BacktestTrade.id` 升序读出（ADR-197），
  而 RNG 是按**下标**取样的，所以池子的顺序本身就是结果的一部分；不排序列会让同一
  个 seed 在同一份数据上给出不同的分布。每次运行写审计事件 `monte_carlo_completed`。
- 年化周期取自该次回测的数据集周期，不接受调用方声明。

## Backtest 执行模型（仓位管理）

创建回测时，`execution_overrides` 接受 `sizing`（docs/23）：
`{"mode": "fixed_fraction" | "risk_per_trade" | "atr_risk", "risk_pct": 0.01, "fraction": 0.5}`。

- `fixed_fraction` 为默认，与历史行为完全一致。
- 风险型模式按「入场价到止损价的距离」反推数量；缺失止损距离时回退为 `fixed_fraction`。
- 所有模式都受 `qty ≤ cash × 0.999 / fill` 约束（不产生杠杆）。
- `execution_model_json` 会记录 `sizing`，因此升级后重跑同一策略 `result_hash` 会变；
  历史回测记录保存的是当时快照，不受影响。

## Strategy Ensemble

`POST /research/ensemble` [已实现]

把多个策略版本按**加权投票**合并成一个组合（docs/24）。

参数：`members`（`[{strategy_version_id, weight}]`，至少 1 个、最多 12 个）、`symbol`、
`timeframe`、`vote_threshold`（`[0,1)`，默认 0.5）、`execution_overrides`（组合的成本/资金/
仓位管理，键为 execution 字段，与创建回测的请求同形）。

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
- 成员的收益/回撤/夏普**不在本响应里**：它们来自 `/backtests`（各成员自己的历史回测），
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

`POST /research/ensemble/sweep` [已实现]

把**同一批成员**在多个 `vote_threshold` 上各跑一次，返回每个阈值下的结果（docs/24 第 7 节，
ADR-052）。这是**描述性**端点：它展示这个旋钮的台阶形状，**不推荐阈值**（与参数敏感性扫描
同族，理由相同：在同一个数据集上挑最好的那个就是过拟合）。

参数：与集成端点（`/research/ensemble`）相同的 `members` / `symbol` / `timeframe` /
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
  一条曲线会让响应体积失控）；需要曲线时用 `/research/ensemble`。

语义：

- 同一阈值下，扫描点与 `/research/ensemble` **逐项一致**：两者共用
  `_prepare_ensemble` + `_run_vote`，特征与成员决策只评估一次并复用。若能做到不一致，这张图
  描述的将是用户无法复现的集成。
- 票数比较按**发布的六位小数精度**进行（`possible_votes` / `effective_vote` / 引擎判定同口径）。
  否则 `1/12 = 0.0833333…` 会在原始浮点下越过发布的 `0.083333`，出现「报告说需要两个成员、
  模拟却让一个成员进场」。联盟总数只在求和结束后取整一次，逐级取整会累积漂移。
- 阈值升高时 `entries_taken` / `entry_bars` 单调不增（可交易 bar 不会变多）。
- `effective_vote` 不是「需要几个成员」，而是「第一个能过线的票数」：三成员各 `1/3` 时，
  阈值 `0.3` 的 `effective_vote` 是 `1/3`。
- 每次运行写审计事件 `ensemble_sweep_completed`。

## Strategy Experiments

`POST /experiments` [已实现] —— 创建一个实验（201）：把一次量化研究跑成**可回读的实体**，而
不只是一次 HTTP 响应；`kind` 决定跑哪一种既有引擎（**不重实现任何量化算法**）。body 带
`draft=true` 时建的是**草稿**：校验照做（版本不存在 404、参数非法 422），但只建行
（status=`draft`）、**不跑任何量化代码、不写结果行**，之后用 run 端点再执行。

`GET /experiments` [已实现] —— 实验历史，最新在前
（`?limit=20&strategy_version_id=&status=&kind=`）：`status` 只接受 draft / running / completed /
failed / archived，其它取值 422；默认**不过滤**归档的实验。

`POST /experiments/from-backtest/{run_id}` [已实现] —— 把一条**已完成**的回测收养成实验（201）：
参数、指标、`summary` 与复现信息（`result_hash`、`engine_version`、`feature_version`、
`dataset_hash`）**逐字复制该运行已存的值，绝不重算**，唯一结果行经 `backtest_run_id` 保留血缘；
body 可选 `{"name"?, "notes"?}`（缺省名 `回测 #{run_id} · {symbol}`）；运行不存在 404，未完成或没有
结果行 409，**同一运行重复收养也是 409**（detail 给出已有实验 id，同一份证据不分裂成双胞胎）。

`GET /experiments/{experiment_id}` [已实现] —— 单次实验及其全部结果行；POST 结束之后仍可回读。

`POST /experiments/{experiment_id}/run` [已实现] —— 执行一个草稿或失败的实验（200）：先用存下来的
`request_json` 重新校验、再改行（所以校验失败时实验**仍是 draft**），结果行按最新一次运行重建；
`completed` / `running` / `archived` 再来 run 是 409。

`POST /experiments/{experiment_id}/archive` [已实现] —— 归档实验（200，写 `archived_at`）：running
仍在跑不能归档、重复归档也是 409；归档只改状态，结果行与底层回测都保留，默认历史列表仍能看到。

`PATCH /experiments/{experiment_id}` [已实现] —— 改名字或备注（200）：只写 `name` / `notes` 与
`updated_at`，改不动参数、状态与结果（那些是快照）；两个字段都缺失 422，未知字段 422。

`GET /experiments/compare` [已实现] —— 逐个实验并排对比**已存**数据（`?ids=1,2` 或重复参数
`?ids=1&ids=2`，两种写法都接受），与回测
对比端点同形：`{"metrics": [...], "experiments": [...]}`，不重算任何量化值；每行**追加** `config`
（该实验当时那份配置：策略/版本/标的/时间范围/初始资金/参数），整体给出 `comparability`
（`same-config` / `different-config`）与 `differences`（人类可读的差异维度，由服务端比较**存储值**
得到）。指标列是 `total_return` / `max_drawdown` / `sharpe` / `win_rate` / `number_of_trades` /
`final_equity` / `cagr` / `total_fees`（ADR-185）；实验与结果行发布的 `final_equity` / `cagr` 是引擎
逐字存储值，`total_fees` 是已存逐笔 `fees` 的求和，没存到的列发布 `null`（界面写「未知」），不会
用 0 顶替。

列表与详情里扁平化的 `metrics` 同样保证带上这八个可比键：存过的逐字发布，没存过的是 `null`
（ADR-185 之前入库的行因此读作「未知」，而不是缺键或 0）；某个 `kind` 自带的指标键（例如
monte_carlo 的 `probability_of_profit`）照旧保留在该字段里。

`DELETE /experiments/{experiment_id}` [已实现] —— 204，级联删除该实验的结果行，**不删除底层
`BacktestRun`**（回测是它自己的产物，删除回测另有其门）。

五种 `kind`（都复用既有研究函数，实验层只负责记录与回读）：

- `backtest`：复用回测端点的同一持久化路径（真实 `BacktestRun` + `BacktestResult` + 指标 + 成交
  明细落库，单次回测查询照常可用），实验行经 `experiment_results.backtest_run_id` 建立
  Experiment→StrategyVersion→BacktestResult 血缘。
- `sensitivity`：`grid`（至少一个轴）逐点独立回测，**每个网格点一行**结果：`parameters_json`
  是该点参数、`metrics_json` 是该点指标、`payload_json` 是该点引擎对象。
- `monte_carlo`：**重采样已存回测的成交明细**（`backtest_run_id`，只接受 `completed` 运行），
  不重跑回测。
- `walk_forward`：`train_bars`（默认 250，≥60）/ `test_bars`（默认 60，≥20）/ `step`（≥1）。
- `oos`：`oos_pct`（默认 0.2，0–1 开区间）或 `oos_start`。

请求字段：`name`(1–120) / `kind` / `strategy_version_id` 必填，另有 `notes`、`symbol`、
`series_id`、`timeframe`、`start`、`end`、`parameters`、`grid`、`metric`、`backtest_run_id`、
`runs`(默认 1000，1–5000)、`trades_per_run`、`seed`。`request_json` 保存校验后的原始请求（复现）。

`extra="forbid"`：未知字段 422。kind 专属的必填项与越界值在**写任何行之前**校验，因此 422 是
**零写入**；未知 `strategy_version_id` 404；无法解析的 series / symbol 404/422。实验行先以
`status="running"` 落库，成功后置 `completed` 并写 `summary_json`；引擎抛错时**仍然创建**实验
行，落 `status="failed"` + `error_message`（审计 `experiment_failed`），POST 仍返回 201 ——
失败是一个实体，不是静默丢弃。

敏感性实验的 `summary_json` 额外给出 `grid_points` / `evaluated_points` / `ranked_points` /
`warmup_unmet_points` 与 best / worst / stable：**warm-up 未满足的点（从未交易、平坦 0.0 净值）
不得赢得排名**（ADR-055），它同时留在每个点的 `payload_json` 与 summary 的
`warmup_unmet_results` 里，任何一层都不许丢。两个新表 `strategy_experiments` /
`experiment_results`（迁移 `0016_strategy_experiments`，ADR-174）是这些结果的事实来源。

## Paper Accounts

`GET /paper/accounts` [已实现] —— 列出模拟账户。
`POST /paper/accounts` [已实现] —— 创建模拟账户。
`GET /paper/accounts/{account_id}` [已实现] —— 取单个模拟账户。
`GET /paper/accounts/{account_id}/equity` [已实现] —— 账户权益曲线：响应除快照字段
（`cash`、`net_deposits`、`realized_pnl`、`positions`、`trades_count`）外还带
`equity_curve`（按时间升序的 `[{timestamp, equity}]`）与 `curve_note`。
曲线是**重放出来的历史**，不是拿今天的净入金倒推：起点是账户创建（或上次重置）时的
期初现金，入金/提现按发生时抬高或压低基准，每笔已平仓交易加上它的 `pnl`，
最后一点等于 `net_deposits + realized_pnl`。入金在曲线上是一级台阶，不是收益；
重置会删掉全部交易并换一个新基准，所以曲线只覆盖账户当前这段生命周期（ADR-108）。
`GET /paper/accounts/{account_id}/trades` [已实现] —— 账户成交列表。
`POST /paper/accounts/{account_id}/reset` [已实现] —— 重置账户（强提醒并生成审计事件）。

账户响应里的资金字段是 `net_deposits`（净入金 = 入金 − 提现）、`cash`（当前现金）与
`realized_pnl`（已平仓交易的盈亏合计），**没有** `initial_cash`：基准会随入金与提现一起
上下移动，所以“初始资金”这个名字会说谎（ADR-066）。`realized_pnl` 是**纯增量**字段
（ADR-124）：`cash - net_deposits` 只在账户没有持仓时才等于盈亏，一笔用满余额的买入会让
现金变成 0、持仓还在账上，于是「盈亏」被读成 -100%；列表、单账户与创建三个端点都返回它
（列表用一条聚合查询，不是逐账户查询）。它不含未实现盈亏（持仓市值）。
创建时的请求体仍然是 `initial_cash`——那一刻它确实等于净入金；
创建、列表与单账户三个端点的响应字段都是 `net_deposits`（数据库列名 `initial_cash` 保留，
只是历史命名）。

创建账户时可以（也可以不）绑定这次要验证的对象：请求体新增 `strategy_version_id`、
`backtest_run_id` 与 `parameters`（ADR-181）。给 `backtest_run_id` 时该运行必须存在，且
`status = "completed"`，否则 **422**（`backtest run {id} is '{status}', not 'completed'`）；
版本与参数默认从这次回测推导（显式传入优先），所以「用这次回测创建模拟账户」只需要给一个 id。
给 `strategy_version_id` 时该版本必须存在（否则 422）；legacy 的 `strategy_id` 仍然接受，但它与
版本冲突时返回 **422**——版本决定策略，不是反过来（`strategy {strategy_id} does not own
strategy version {strategy_version_id}; the version decides the strategy`）。`strategy_id` 单独
回答不了「哪个版本、哪套参数」，所以账户响应与创建响应都带上 `strategy_version_id`、
`backtest_run_id`、`parameters` 与解析出来的 `strategy_name`；`backtest_run_id` 是
`ondelete="SET NULL"` 外键——退役一次回测不会连带删掉用它建出来的账户。

请求体还可选带 `experiment_id`：账户是从哪条实验建的就记哪条（ADR-209）。这条实验必须存在
（否则 422 `experiment {id} not found`）；同时给了 `backtest_run_id` 时，那条实验必须**真的跑过**
这次回测——血缘在 `experiment_results.backtest_run_id` 上，不在实验自己身上，所以服务端查的是结果行，
查不到就 **422**（`experiment {id} ran backtest run {run}, not {given}`，实验一条回测都没跑过时是
`experiment {id} has no backtest run to bind to`）。原因很直白：一条把 A 实验和 B 回测配在一起、
却对外声称「我来自 A」的账户，比一条拒绝创建的账户更糟。只给 `experiment_id` 时，版本与参数从这条
实验推导（显式传入优先），`backtest_run_id` **保持为 `null`**——不凭空补一个没人要的绑定。
响应里 `experiment_id` 同样是 `ondelete="SET NULL"` 外键：删除实验时服务端显式把引用清成 `null`
（不指望方言替它做这件事），账户本身不动；`null` 的诚实含义是「不是从实验建的」或「那条实验已经删了」。

账户响应还给出总览口径的一组数字（ADR-181）：`net_deposits`、`cash`、`market_value`（持仓市值）、
`unrealized_pnl`（未实现盈亏）、`realized_pnl`（已实现盈亏）、`total_equity`（总资产）、
`total_pnl`（总盈亏）与 `total_pnl_pct`（总收益率，比率不是百分数，ADR-087）。标记价一律取该标的
**最新一根已收盘 K 线**的收盘价（ADR-119）；任何一笔持仓取不到合格收盘价时，`market_value`、
`unrealized_pnl`、`total_equity`、`total_pnl`、`total_pnl_pct` **全部**为 `null`，原因逐条写进
`metric_notes`——**绝不编一个价格**：待标记的持仓不计入总额，半算出来的总资产比没有总资产更糟
（ADR-006/ADR-007）。恒等式是 `total_equity = cash + market_value`、
`total_pnl = realized_pnl + unrealized_pnl`；盈亏只能由「数量 × 标记价」得出，**不得从现金变动
倒推**（ADR-124），`net_deposits ≤ 0` 时总收益率同样没有分母。

入金/提现端点（`/paper/accounts/{account_id}/fund`）的 `amount` 正负都改变基准：入金抬高、
提现降低，返回 `{account_id, cash, net_deposits}`，审计 payload 同步记录 `net_deposits`。
于是不变量 `final_equity == net_deposits + 已实现盈亏`（空仓时等于 `cash`）恒成立，
取钱不会被记成亏钱。重置端点的响应也带 `net_deposits`（重置把基准一并设为期初现金）。

重置账户也会删掉该账户的**模拟订单**（ADR-181）：持仓与成交删掉、订单留着的话，订单列表会继续
返回一些已经不属于任何盈亏的孤儿委托。`initial_cash` 传 `0` 按**显式传入**处理（过去
`initial_cash or …` 把 0 当成「没给」，于是重置回不去 0）。

`net_deposits ≤ 0` 时没有收益率的分母：`/paper/accounts/{account_id}/performance`
的 `metrics.total_return`（以及其它比率类指标）为 `null`，原因写在新增的
`metric_notes` 数组里（例如 `initial capital is not positive, so ratio metrics have no
denominator`），前端据此显示「—」而不是 `NaN%`/`-100%`。

分母为正时，`/performance` 报的是**资金中性收益率**（ADR-121）：入金与提现在指标序列里
是一级台阶（并入当刻权益），既不算收益也不算回撤，每笔交易按它运行时的权益计算收益率再
连乘。`cagr` 用**真正经过的时间**年化（最后一笔 `exit_time` 减最早一笔 `entry_time`，
每笔交易对应一个观测点，`bars_per_year = 观测点数 / 年数`），所以两笔交易各 +1.5% 不再
等于「两个交易日 +1.5%」；已平仓交易的时间跨度为零时 `cagr` 为 `null`，note 说明没有可
年化的区间。`final_equity` 仍然是 `net_deposits + 已实现盈亏`，金额口径不变。

## Paper Positions

`GET /paper/accounts/{account_id}/positions` [已实现] —— 列出当前持仓。
`GET /paper/accounts/{account_id}/positions/{asset_id}` [已实现] —— 取单个持仓。
`GET /paper/accounts/{account_id}/performance` [已实现]  (equity-derived metrics)
`GET /paper/accounts/{account_id}/orders` [已实现] —— 账户订单列表。
`POST /paper/accounts/{account_id}/execute` [已实现]  (execute a persisted signal; virtual fill)

成交时刻取自**成交价那一根 K 线**（ADR-123）：成交价是信号那根 K 线的收盘价，所以
`PaperTrade.entry_time` / `exit_time` 与 `PaperOrder.filled_at` 默认就是那根 K 线的时间，
而不是「执行被点下的那一刻」——补执行一条历史信号不会再被记成今天，权益重放（按
`exit_time` 排序）也不会把它插进历史的中间。调用方仍可显式传入时刻来覆盖它（测试与手工
补录）。成交数量按 `Numeric(24, 10)` 向下取整后再计算手续费与现金余额，因此一笔用满余额
的买入不会被 Decimal 末位误差拒成 `insufficient cash`（ADR-122）。

持仓响应每行新增 `symbol`、`mark_price`、`mark_time`、`mark_note`，以及由标记价算出的
`market_value`、`unrealized_pnl`、`unrealized_pnl_pct`（比率不是百分数，ADR-087）：标记价取该
资产**最新一根已收盘 K 线**的收盘价（`resolve_series` 解析序列、`load_bars(only_closed=True)`
取数，ADR-119），`mark_time` 是那根 K 线的时间。取不到合格收盘价（标的没有序列、还没有已收盘
K 线、或那根 K 线没有收盘价）时，价格与盈亏字段为 `null`，`mark_note` 说明是哪种情况——界面据此
显示「未知」，而不是把缺失读成 0（ADR-006/ADR-007）。持仓列表与取单个持仓
（`/paper/accounts/{account_id}/positions/{asset_id}`）两个端点口径一致。持仓数量为 0（已经全部
平掉）时，`market_value` 与 `unrealized_pnl` 是 0、`unrealized_pnl_pct` 是 `null`：没有敞口就没有
开仓收益率可发布，用逐股价差顶上只会让一条已清仓的记录显示一笔其实不存在的亏损。

执行信号（`/paper/accounts/{account_id}/execute`）新增两个**可选**且**互斥**的 sizing 字段
`quantity`（成交数量）与 `notional`（成交金额），都必须 > 0；`notional` 按**成交价**折算成数量，
超过账户当前现金时返回 **422**（`notional exceeds available cash`），两者同时给出也返回 422
（`specify either quantity or notional, not both`）。都不传时行为不变：引擎按 ADR-030 的默认整仓
口径下单，预算按 `成交价 × (1 + 费率)` 折算。

`POST /paper/accounts/{account_id}/close` [已实现] —— 关闭（冻结）一个模拟账户。
`POST /paper/accounts/{account_id}/reopen` [已实现] —— 重新打开已关闭的模拟账户。
`POST /paper/accounts/{account_id}/fund` [已实现] —— 入金/提现（有审计）。

## Paper Orders

`GET /paper/orders` [已实现] —— 列出跨账户的模拟订单。
`GET /paper/orders/{order_id}` [已实现] —— 取单个模拟订单。

## Paper Trades

`GET /paper/trades` [已实现] —— 列出跨账户的模拟成交。

逐笔成交除了价格、数量与盈亏之外还带 `fees`、`slippage`、`order_id` 与 `signal_id`：
前两个是这一笔真实付出的手续费与滑点，后两个是这一笔的来历（它由哪张成交单产生、
那张成交单当时是为哪条信号下的）。`signal_id` 为 `null` 表示这笔成交**不是从信号来的**
（成交单没带信号，或者这笔成交没有成交单），不是「读不到」。归因仍然按账户：这里公布的
是**这一行自己的出处**，不是把账户里的盈亏算到某一版策略头上（ADR-114、ADR-204）。

## Signals

`GET /signals` [已实现] —— 列出已落库的信号（query `state`、`asset_id`、`symbol`、
`strategy_version_id`、`limit` 默认 50（上限 500）、`offset` 默认 0）。
`GET /signals/{signal_id}` [已实现] —— 取单个信号。
`GET /signals/{signal_id}/evidence` [已实现]  (feature snapshot + portfolio context)
`GET /signals/evidence/{strategy_version_id}` [已实现] —— 规则命中 + 实证统计 + 模拟统计 + 组合上下文。
`GET /signals/preview/{strategy_version_id}` [已实现] —— 预览某个策略的信号（不落库）。
`POST /signals/preview-explain` [已实现] —— 解释一次扫描意图而不落库 Signal 行。
`GET /signals/outcomes` [已实现] —— 信号结果跟踪（信号到底work不work）。
`GET /signals/outcome-summary` [已实现]  (win rate / avg PnL grouped by direction/timeframe/state/strategy)
`POST /signals/scan` [已实现] —— 干跑一次扫描；`persist=false`（默认）只算不落库，`persist=true` 才写入信号（去重）。
`POST /signals/outcomes/evaluate` [已实现] —— 现在就把等待中的信号回填一次结果（ADR-202）。
`POST /signals/{signal_id}/explain` [已实现] —— 解释一个已落库的信号。
`POST /signals/{signal_id}/acknowledge` [已实现] —— 确认一个信号；已确认的信号不再被通知任务推送。

信号列表新增查询参数 `strategy_version_id`：绑定策略版本的模拟账户要的正是那个版本的信号，先按
`limit` 取最新 N 行再在客户端过滤会**静默丢掉**它们（ADR-181）。过滤发生在这条聚合语句里，
`symbol` 仍然走 `Asset` 的精确匹配（未知标的返回空列表，不是全局结果）。

同样的 `strategy_version_id` 也是 `/signals/outcomes` 与 `/signals/outcome-summary` 的查询参数
（ADR-201，同一个理由）：只看某一版策略发过的信号，及其结果统计。`/signals/outcome-summary`
把范围**回显**在响应里（`strategy_version_id`，未收窄时为 `null`），和三个计数一样属于「数字必须
带着它的分母与范围一起发布」（ADR-065）—— 页面因此不必拿本地状态替它说范围。

两种模式共用同一条新鲜度门禁：`NO_SIGNAL` 不是信号，既不落库也不计入 `created`。`persist=true`
返回的 `created` 是**真的新建**了几行（过去它靠「最老一条 signal 的 id 有没有变」猜，既算错又会在
并发扫描撞上 `uq_signal_event` 时 500）。每个 series × current 版本最多一次评估，返回的 `signals`
里每行带 `persisted`：`true` 表示这一行是一个真实事件、此刻已经在库里（可能是本次新建，也可能是
此前那次扫描已经写过 —— 新建了几行只看 `created`），`false` 表示这次扫描没有任何东西可以记录（ADR-115）。

信号行上的 `direction` 与 `closes_direction`：`BUY` 是 `LONG`，做空入场是 `SHORT`，平仓是
`SELL` + `direction=FLAT` + `closes_direction=LONG|SHORT`（说明它平掉的是哪一边）。入场是电平（成立
期间每根 K 线都报），出场是事件（只在第一次成立的那根报）。`/signals/outcomes` 只评估入场信号，
`not_an_entry` 计的是被跳过的平仓指令。

`/signals/outcome-summary` 的胜率永远跟它的分母一起返回（ADR-065）：

- `symbol`：这些数字描述的范围（`null` 为全部标的）；端点接受 `?symbol=`，与 `/signals/outcomes`
  同口径，未知标的返回空范围而不是全局平均。
- `strategy_version_id`：同样是范围的一部分（`null` 为全部策略版本）；端点接受
  `?strategy_version_id=`，与上面两个端点同口径，某一版没有信号时返回空范围而不是全局平均（ADR-201）。
- `signals` / `decided` / `undecided`：范围内信号总数 / 已有可用结果的数量 / 其余数量。
  `decided` 是每个 `count` 与 `win_rate` 的分母，且 `decided == groups.ALL.count`。
- `bars_after`：评估器要在信号之后看到多少根 K 线才回填结果（`DEFAULT_BARS_AFTER = 10`）；
  `undecided` 就是还没等到这些 K 线、或该标的还没有 K 线序列的信号。
- 未决信号既不计入胜率，也不算亏损 —— 它们只是还没有结果。
- 状态字段 `evaluated` 已改名为 `decided`（同一个数字只留一个名字）。

`/signals/outcomes/evaluate` 是那条回填的**入口**，不是第二套规则：它调用定时任务每 30 分钟
调用的同一个幂等函数（`evaluate_pending_outcomes`），所以按第二次只会返回 `evaluated: 0`。
它存在的理由是那种没有调度器的运行方式（单容器、单进程本机跑）—— 否则页面上永远只能是
「已评估 0 条」，而没有任何办法请它去算（ADR-202）。响应：

- `evaluated` / `insufficient_data` / `skipped` / `not_an_entry`：本次真正新增的结果数 /
  信号之后不足 `bars_after` 根 K 线的数量 / 读不到该标的 K 线序列的数量 /
  本次范围内被排除的平仓指令数量（退出不是一个持仓，回填它问的是另一个问题）。
- `bars_after`：固定为 `DEFAULT_BARS_AFTER`，**不是**请求参数 —— 统计对外公布的窗口就是它，
  用别的窗口回填会让那个已公布的口径对不上它描述的行。
- `strategy_version_id`：与上面两个端点同口径的范围回显（`null` 为全部策略版本，ADR-201）。

## Feature Snapshots

The exact feature row each signal was computed from (reproducible evidence).

`GET /feature-snapshots/{series_id}` [已实现] —— 某个序列最近的特征快照。
`GET /feature-snapshots/{series_id}/latest` [已实现] —— 某个序列最新的特征快照。

## Notifications

Multi-channel delivery: `webhook`, `feishu`, `telegram`, `pushplus`, `email`.
Channel secrets are write-only and only ever returned masked. `PUT` with
`channels` replaces the whole list; omitting a secret field keeps the stored
value.

`GET /notifications/config` [已实现] —— 通知设置。
`PUT /notifications/config` [已实现] —— 更新通知设置。
`POST /notifications/test` [已实现] —— 发一条测试通知。
`GET /notifications/events` [已实现] —— 最近的通知投递、失败与抑制记录。

## Strategy Lifecycle

Deterministic, evidence-gated promotion/degradation. No AI is involved.

`GET /lifecycle/strategies` [已实现] —— 每个策略的生命周期总览。
`GET /lifecycle/strategies/{strategy_id}` [已实现] —— 单个策略的生命周期与证据。
`POST /lifecycle/strategies/{strategy_id}/apply` [已实现] —— 证据足够时应用一次生命周期迁移。

## AI Status

`GET /ai/status` [已实现] —— AI 配置与预算（`configured:false` 时 `key_error` 说明原因：`"undecryptable"` = 有启用的 Provider 但密钥解不开，`"empty"` = 密钥为空，`null` = 不是这两种情况；见 ADR-194）。

## AI Providers

`GET /settings/ai/providers` [已实现] —— 列出 AI 供应商（key 永不返回；每行带 `key_status`：`"ok"` / `"empty"` / `"undecryptable"`，见 ADR-194）。
`POST /settings/ai/providers` [已实现] —— 创建 AI 供应商（key 加密存储、永不返回；`manual_models` 逐字保存完整模型 ID）。
`PUT /settings/ai/providers/{provider_id}` [已实现] —— 更新一个 AI 供应商。
`DELETE /settings/ai/providers/{provider_id}` [已实现] —— 删除一个 AI 供应商（历史 AI 任务与用量保留，并保留其名称快照）。
`PUT /settings/ai/providers/{provider_id}/models` [已实现] —— 保存该供应商的模型选择（body `{"models": [...], "manual_models": [...]}`；被选中的启用、缺失的创建、未选中的置为停用，从不删除）。
`POST /settings/ai/providers/test` [已实现] —— 保存前先测试凭据。
`POST /settings/ai/providers/{provider_id}/test` [已实现] —— 测试一个已存储的供应商（`models_total` 是 `/models` 返回的真实数量，不做截断）。

## AI Models

`GET /ai/models` [已实现] —— 跨供应商的 AI 模型；按供应商过滤用 `provider_id` query 参数。
`PUT /settings/ai/models/{model_id}` [已实现] —— 启用/停用一个模型（body `{"is_active": true|false}`；停用不删除任何行，供应商最后一个可路由的 `default_model` 会被 409 拒绝）。
`DELETE /settings/ai/models/{model_id}` [已实现] —— 删除一个模型配置（历史 AI 任务与用量保留，并保留其名称快照）。

## AI Tasks

`GET /ai/tasks` [已实现] —— 最近的 AI 任务。
`GET /ai/tasks/{task_id}` [已实现] —— 单个 AI 任务及其已存储的输出。

## AI Usage

`GET /ai/usage` [已实现] —— AI 用量行（按日期/供应商；按供应商过滤用 `provider_id` query 参数）。
`GET /ai/usage-today` [已实现] —— 今天的 AI 花费（UTC）。

## AI Prompts

`GET /ai/prompts` [已实现] —— AI 提示词模板。

## AI Capabilities

`GET /ai/capabilities` [已实现] —— 能力注册表：系统真正能算什么（指标/特征/算子/成交模型/风控模型/仓位模式/指标口径/数据源/分析引擎），以及明确不支持的能力与原因（ADR-151）。AI 生成前必须先读它。

## AI Roles

`GET /ai/roles` [已实现] —— 磁盘上的角色契约（`backend/app/ai/contracts/*.md`）解析结果：name/role/version/task_types/prompt_names/required_capabilities/output_language/content_hash/source_path，以及被登记进 `ai_role_contracts` 的 `indexed` 标记（ADR-150）。

## AI Audit

`GET /ai/audit/{task_id}` [已实现] —— 一次 AI 调用的可追溯记录：provider/model/role/prompt 版本与哈希、输入/输出哈希、token 与成本、使用的来源（`source_ids`）、关联的策略版本（ADR-153）。

## AI Research

v1.9.8 的研究层：研究输入 → RESEARCHER 的结构化理解（StrategyHypothesis）→ STRATEGY_ARCHITECT 的候选形式化（StrategyDraft）→ 服务端能力校验。草案**不可执行**，本层不跑回测、不编译 DSL、不下单（ADR-154/155/156/157）。

v2.0.0 的验收修复把四件事收紧：EXPLICIT 规则的引文由服务端逐字核对（ADR-159）、未解问题可以点名 `rule_id`（ADR-160）、第三方材料与用户自有材料分开保留并各记两个 hash（ADR-161）、模型调用只有 `run_task()` 一条路并有静态守卫看着（ADR-162）。

v2.1.0 起平台可以自己读一份材料：抓取（guard → fetch → parse）先写一条 append-only 的 source snapshot，再把它当作**不可信材料**交给研究者（ADR-163/164/165/166）。

`POST /ai/research` [已实现] —— 一次完整研究运行。请求 `{question, sources: [{label?, kind?, source_ref, text?, uri?, snapshot_id?, retention?, license_note?}], model?}`（`question` 3–4000 字；`sources` 1–8 条，每条 `text` 非空且 `source_ref` 唯一标识本次材料；`retention` 取 `excerpt` / `full`，缺省按 `kind` 决定——`user_input` 是用户自己的材料，整份保留；其余按第三方处理，默认只留 metadata 与 ≤500 字符摘录，要整份保留必须同时给 `license_note`，取值不合法或缺 license_note 返回 400）。`kind` 取 `user_input` / `text` / `github_file` / `url` / `pdf`：前三类要求 `text`；`url` / `pdf` 可以改给 `uri`（平台自己抓取、解析并先写一条 source snapshot）或 `snapshot_id`（复用已经观测过的那一份）；`text` 与 `uri`/`snapshot_id` 同时给出时以 `text` 为准并记一条 `text_preferred` 警告，绝不会偷偷联网（ADR-163）。**默认异步（v2.5.0）**：请求内只做校验、建 run 行与来源摄取（`prepare_research()`），随即入队 Celery 任务 `quantlab.run_research` 并返回 **202** + 同一个 run payload，其中 `status = "queued"`、`current_step = "queued"`——真正的 RESEARCHER 与 STRATEGY_ARCHITECT 两步在 worker 进程里由 `execute_research()` 完成，前端轮询 `GET /ai/research/{run_id}` 观察进展（阶段跳过 `queued` → `ingest` → `researcher` → `architect` → 终局）；只有把 `AI_RESEARCH_ASYNC` 设为 `false`（无 worker 的部署、测试）才恢复「请求内同步跑完两步再返回 200」的旧行为。无论哪条路径，返回的都是同一个 run payload：`run_id`、`question`、`status`（`queued`/`pending`/`running`/`completed`/`rejected`/`failed`）、`current_step`、`capability_status`、`attempts`、`sources`、`warnings`、`violations`、`error_message`、`researcher_task_id`、`architect_task_id`、`created_at`、`completed_at`，以及 `hypothesis`（`hypothesis_id`/`status`/`confidence`/`role`/`prompt_version`/`provider`/`model`/`ai_task_id` + `content`）与 `draft`（`draft_id`/`version`/`status`/`capability_status`/`model_status`/`executable`/`compiled_strategy_version_id` + `content` + `capability_report` + `confirmation`）。模型答得不合格时**仍返回 200**，`status = "rejected"` 且 `violations[]` 逐条给出违规码与原因（不自动修正，不做第二次语义尝试）；未配置 AI provider 时 503。抓取类来源另有两条**明确分层**的失败语义：任一来源被安全策略拒绝（私网/回环/link-local/非 http(s)/robots 禁止）时整次运行以 `rejected` 结束并返回 **422**（`detail.error = "source_blocked"`，绝不静默降级成「少一个来源的答案」）；来源抓不到或读不出时返回 **502**（`detail.error = "source_unavailable"`），两者都会留下 run 行与 snapshot 行供事后查证（ADR-164）。

`GET /ai/research` [已实现] —— 最近研究运行的摘要列表（query `limit` 默认 20、上限 100）：`run_id`/`question`/`status`/`current_step`/`capability_status`/`attempts`/`violation_count`/`warning_count`/`created_at`/`completed_at`，不含假设与草案正文。

`GET /ai/research/{run_id}` [已实现] —— 单次运行的完整 payload（字段同 `POST /ai/research`）；未知 id 返回 404。`sources[]` 里带 `snapshot_id` 的来源会多一个 `snapshot` 字段（该次观测的完整快照摘要，形状同 `GET /ai/sources/{snapshot_id}`），所以一次 GET 就能回答「这次研究依据的材料是哪一份、状态如何」（ADR-166）。

### AI Sources（v2.1.0）

`POST /ai/sources/url` [已实现] —— 抓取并解析一个 `http(s)` 页面，落一条 source snapshot。请求 `{uri, source_ref?, label?, retention?, license_note?}`：**不调用任何模型、不消耗 AI 预算**，因此也不创建 `AITask`。响应是快照摘要 `{snapshot_id, source_kind, snapshot_status, parse_status, source_ref, label, original_uri, final_uri, status_code, content_type, bytes_read, chars_read, source_hash, text_hash, parser, parser_version, robots_ok, retention{policy, retained_chars, truncated}, excerpt[], warnings[], redirects[], error_code, error_message, fetched_at, created_at}`——**绝不返回第三方全文**（ADR-161/ADR-166）。请求形状不合法（未知 scheme、`retention` 取值非法、`full` 缺 `license_note`）返回 400；被安全策略拒绝返回 **422**（`detail.snapshot_status = "blocked"`、`detail.code` 给原因码，快照行仍然落库，`snapshot_status = "blocked"` 且 `parse_status = "not_parsed"`）；抓取失败或解析失败返回 **502**（同样落库，`snapshot_status = "fetch_failed"`）。同一 URL 抓取两次写两条快照，永不复写（append-only，ADR-166）。

`POST /ai/sources/pdf` [已实现] —— 同一件事，但对象是一份 PDF，只读文本层：请求 `{uri?, content_base64?, filename?, source_ref?, label?, retention?, license_note?}`，`uri` 与 `content_base64` **必须二选一**（都给或都不给返回 400；`content_base64` 是本版对「上传一份 PDF」的加法——本仓没有 multipart 与对象存储，故用内联 base64，上限 3,000,000 字符、解码后上限 2 MiB）。无文本层（扫描件）不是错误：返回 200、`parse_status = "unsupported"`，`error_message` 点名「没有文本层、本版无 OCR」，绝不猜内容（ADR-165）。加密、畸形或非 PDF 字节解析失败同样是 200 + `parse_status = "parse_failed"`（抓取与解析的失败与「策略拒绝」分开表达）。

`GET /ai/sources/{snapshot_id}` [已实现] —— 只读回看一条快照（字段同两个 POST 的响应）；未知 id 返回 404。这是「这次研究到底依据哪一份材料」的追溯入口（ADR-166）。

`POST /ai/strategy/formalize` [已实现] —— 单独让 STRATEGY_ARCHITECT 再形式化一次：请求 `{run_id}` 或 `{hypothesis_id}` → `{"draft": {...}}`（字段同 run payload 里的 `draft`）。回答不是草案时 422，detail 带 `step` 与 violations；两个 id 都没给返回 400；id 未知返回 404。本端点是研究层内部的重跑入口，五步主链路已包含该步。

`POST /ai/strategy/drafts/{draft_id}/compile` [已实现] —— 把已存草案编译成策略版本：请求 `{strategy_id}`（只有目标，不接受 spec / `compile_hash` / `compiler_version`）→ 201 `{result, strategy_id, strategy_version_id, version, compile_hash, report}`，其中 `report` 就是编译器返回的那一份；`NEEDS_USER_DECISION` / `REJECTED` 返回 422 且不创建任何行；draft 或 strategy 未知返回 404；草案已绑定、**草案未经人工确认**、版本号不可自增、目标版本号已被占用返回 409（docs/29 §16.7）。**人工确认门（v2.5.0）**：调用编译器之前先读最新人工决定（`backend/app/ai/confirmation.py` 的 `require_confirmation`）——没有答复、或最新答复不是 `confirmed` 时返回 409 `draft_not_confirmed`，`details = {draft_id, decision}`（`decision` 为 `null` / `"rejected"` / `"needs_revision"`），**不写任何行、不带 `report`**。这是服务端强制门：绕过页面直接调本端点同样被拒；顺序固定为 404 → `draft_already_compiled` → `draft_not_confirmed` → 版本号类 409。

`POST /ai/strategy/drafts/{draft_id}/confirmations` [已实现] —— 记录**人工确认**（v2.4.0）：请求 `{decision: "confirmed" | "rejected" | "needs_revision", note?}`（`note` ≤ 2000 字，取值不合法返回 422）→ 201 `{draft_id, run_id, decision, label, confirmation, strategy_version_created: false, compiled_strategy_version_id}`；draft 未知返回 404。`confirmation` 就是 `GET /ai/research/{run_id}` 里 `draft.confirmation` 的形状（`decision`/`label`/`note`/`decided_by`/`decided_at`/`audit_id`/`is_human_decision`/`strategy_version_created`），未确认时为 `null`。它**只追加一条审计事件**（`strategy_draft_confirmed` / `strategy_draft_rejected` / `strategy_draft_needs_revision`，`entity_type=strategy_draft`）：不创建 `StrategyVersion`、不写 `validation_status`、不碰 `is_current`——人工确认是关于草案的证据，让策略上线仍然只有确定性编译器与 ADR-171 激活门两条路；重复提交即追加新事件，最新一条就是当前决定。

**证据的核对结果与新增违规码** [已实现] —— 假设与草案正文里每条规则的 `evidence[]` 会被服务端补上四个**只读**字段：`verified`（布尔，是否在材料里逐字找到）、`char_start` / `char_end`（在读入文本里的字符区间，从 0 起）、`verified_against`（被核对的那份读入文本的 sha256）。请求不接受这四个字段，语义就是「这句话在原文的哪一段被找到了」（ADR-159）。违规码新增四个：`evidence_missing_quote`（EXPLICIT 规则没给引文）、`evidence_quote_too_short`（引文规范化后不足 2 字符）、`evidence_mismatch`（引文在读入材料里找不到——编造引文在这里被拒）、`unknown_rule_unknown`（unknown 点名了本次假设里不存在的 `rule_id`）。研究运行的 `sources[]` 每条带 `source_hash`（原文 hash）、`text_hash`（读入文本 hash）、`stored_chars` 与 `retention{policy, excerpt_budget, stored_chars, full_text_stored}`；材料没被存全时 `warnings[]` 出现 `excerpt_limited`（ADR-161）。

## GitHub Sources

Implemented under `/importer/github/sources` (persisted on import; watched and
re-imported automatically when a new commit lands):

`GET /importer/github/sources` [已实现] —— 列出被监视的 GitHub 源。
`GET /importer/github/sources/{source_id}` [已实现] —— 取一个被监视的 GitHub 源。
`GET /importer/github/sources/{source_id}/check` [已实现]     (live commit check -> has_update)
`GET /importer/github/sources/{source_id}/snapshots` [已实现] —— 某个源的历史快照。
`GET /importer/github/versions` [已实现]      (version ledger for a name -> next_version；用 `?name=...` 指定)
`POST /importer/github/analyze` [已实现]      (read-only repo analysis)
`POST /importer/github/import` [已实现]       (persist a reviewed DSL as a strategy)

分析端点（`/importer/github/analyze`）是只读的，返回 review surface：findings、草稿 DSL，以及
`coverage` 块（ADR-056, docs/05 §4.1）。

- `ref` is the name that was asked for; `commit` is the revision the report actually
  describes. The client sends `ref` (e.g. `main`), the server resolves it against
  GitHub 的提交路径 `/repos/{owner}/{repo}/commits/{ref}` and then reads the tree and every file by
  that SHA, so a push landing mid-fetch can no longer mix two revisions into one report
  (ADR-060, docs/05 §4.4). A ref that does not resolve is an error, never a stand-in for
  a commit. A SHA passed in as `ref` is used as-is (no extra request — the watcher always
  passes the head SHA it just read).
- 导入端点（`/importer/github/import`）**requires** `commit` (7–64 hex characters, e.g.
  `9f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f60718293`); a missing or non-SHA value is a 422,
  because the human reviewed one revision and not "whatever the branch was at the time".
  The SHA is stored as the new `StrategyVersion.source_commit`, in `evidence_json`
  (`{importer, repository, ref, commit}`), in the audit record, and on the source's
  `current_commit`; the import also writes a snapshot for that commit even when no `ref`
  was sent, and the response echoes `source_commit`.

- `analysis_version` (currently `1.4.0`) rises whenever the report's field semantics
  change. It moved to `1.1.0` when `files_scanned` was split into `files_parsed` and
  `files_inventoried` (non-Python files are inventoried, not parsed) and
  `files_skipped` became `[{path, reason}]` instead of bare paths; to `1.2.0` when
  `coverage` learned to separate "the cap stopped it" from "the time budget stopped it";
  to `1.3.0` when a Python file that was downloaded but did not parse stopped being
  counted as parsed (`files_parsed`, `coverage.unparsed_python_files`, ADR-059); to
  `1.4.0` when the report started naming the `commit` it read instead of only the `ref`
  it was asked for (ADR-060).
- `version` on the import endpoint (`/importer/github/import`) is **optional**: omitted means "the server
  that owns the version ledger assigns the next free patch version" (`1.0.0` for a new
  strategy, `1.0.1` after `1.0.0`, compared as three integers so `1.0.9` → `1.0.10`).
  The response reports `version_assigned` (`true` when the server chose it). A version
  the caller names is used verbatim and still has to be free (a duplicate is a 422), and
  a ledger whose versions cannot be read as `major.minor.patch` makes an unnamed import a
  422 that names the offending version instead of guessing (ADR-061, docs/05 §4.5).
- `/importer/github/versions`（`?name=...`）answers what an import of that name would do
  before anything is written: `{name, slug, strategy_id, versions, next_version,
  can_assign, reason}` (`strategy_id` is null when the slug is still unclaimed,
  `next_version` is null when `can_assign` is false).
- `files_unparsed` = `[{path, reason}]` for files that were fetched but whose parse
  failed. They contributed nothing to the findings, so the draft cannot contain the rules
  they declare; the `reason` is the parser's own error, sanitised.
- `max_files` (1–30, default 12) caps how many candidates are fetched; `max_seconds`
  (10–600, default 120) is the wall-clock budget for the whole fetch (ADR-057). The
  per-request timeout cannot bound a loop of 30 files with retries, so the loop has a
  budget of its own and stops when it is spent.
- `coverage` = `candidate_files`, `candidate_python_files`, `cap`, `attempted_files`,
  `downloaded_files`, `parsed_files`, `inventoried_files`, `skipped_files`,
  `unparsed_python_files`, `not_attempted_files`, `unread_python_files`, `complete`,
  `max_seconds`, `budget_exhausted`. The numbers describe the fetch, not the repo's docs:
  `1` candidate file and `cap = 1` are different facts. `budget_exhausted` decides which
  advice applies — raise the budget, or raise `max_files` (the cap was never reached).
- `warnings` carries the coverage sentences (never fetched / stopped after the budget /
  unread Python / unread after fetch / downloaded but did not parse) plus the DSL-builder
  warnings. They are not advisory decoration: a non-empty list means the findings may be
  missing rules that live in files the analysis never read — or never understood.
- The last_import_status of a watched source can be `incomplete`: the watcher refuses
  to import from a partial read, or from a read in which a Python file did not parse, and
  records the gap in the snapshot instead (`reason` is `incomplete_analysis` for the
  former, `unparseable_python` for the latter). A gap caused by the budget or the network
  is recorded as `transient` and does **not** advance the source's `current_commit`, so
  the next scheduled check retries it; a cap-limited read or a file that did not parse is
  structural and is marked as seen.
- `last_import_status` uses the watcher's outcome vocabulary (ADR-058): `unchanged`
  (the commit was already seen, nothing was fetched), `no_change` (a new commit was
  fetched and analysed, the DSL did not change), `imported`, `incomplete`,
  `review_required`, `error`. Rows written before v1.4.8 still say `checked`, which
  collapsed all three non-events; clients should render it as a historical value rather
  than reinterpreting it.
- `review_required` (ADR-062) means the watcher fetched a new commit, built a draft, and
  refused to import it because `strategy_dsl_problem` (the gate the manual import path
  uses: `parse_spec` + `validate_strategy`) rejected it — normally because the builder
  never invents exit rules, so the draft has an empty `exit`. The run does not raise, the
  refusal is recorded as a snapshot, and the commit is stored in
  `pending_review_commit`: the next check of that same commit returns `review_required`
  from a single HEAD request without re-fetching. Every source response
  (list, single, `check`) carries `pending_review_commit` (null when nothing is waiting).
  Importing that commit through `/importer/github/import` clears it; importing a
  different commit does not — the wait belongs to one revision.
- `/importer/github/sources/{source_id}/snapshots` returns `id`, `source_id`, `commit`,
  `content_hash`, `fetched_at` and `extraction`. `extraction` is the stored reason
  (`imported`, `reason`, `transient`, `coverage`, `files_unparsed`, `warnings`);
  `reason` is one of `manual_import`, `imported`, `dsl_unchanged`, `no_linked_strategy`,
  `incomplete_analysis`, `unparseable_python`, `requires_review`, `import_failed`.
  `requires_review` and `import_failed` also carry `detail`, the gate's or the
  exception's own words (sanitised), because "waiting for a human" has to say what for.
  Snapshots written before v1.4.8 from the manual import path may be `{}`.

## Audit Logs

`GET /audit/logs` [已实现] —— 最近的审计事件。
`GET /audit/logs/entity/{entity_type}/{entity_id}` [已实现] —— 某个实体的审计事件。

- `total` 是「符合条件的事件总数」，不是这一页的条数：`limit` 只减 `events`，不减 `total`，
  所以调用方能分清「账本里只有 3 条」和「只取回了 3 条」（ADR-070）。
- 每条事件都带 `actor`（`system` 表示定时任务或服务自己写的，`user` 表示由人工动作写出）。
- `action` 是审计记录里「这条记录说了什么发生了」的字段，界面直接渲染它。
  对 `strategy_lifecycle_changed` 它只有四个取值：`promote`（流水线内前进一步）、
  `degrade`（被标记为降级）、`retire`（人工退休）、`restore`（从终态回到流水线）。
  `retired` 不是「前进到最高阶段」，所以退休**不得**记为 `promote`（ADR-063）。
- 审计只有这一个出口。旧的 `/settings/audit` 曾经返回同一批记录、但**不带** `actor`，
  已删除（ADR-070）——同一个事实有两个出口，就会有两个慢慢长歪的答案。

## System Settings

`GET /settings` [已实现] —— 列出设置。
`PUT /settings` [已实现] —— 创建或更新一条设置。
`GET /settings/ghostfolio/test` [已实现] —— 测试 Ghostfolio 连接。
`GET /settings/ghostfolio/holdings` [已实现] —— Ghostfolio 持仓（只读）；`allocation_pct` 与 `unrealized_pnl_pct` 一律是**百分点**（0–100），单位（0–1 分数还是 0–100）由整份 payload 的金额自行判定，不逐值猜（ADR-199）。

## System Resources （v2.6.0 撤回）

`GET /resources/current` [取消] —— 资源监控在 v2.6.0 被整体删除。
`GET /resources/history` [取消] —— 资源监控在 v2.6.0 被整体删除。
`GET /resources/events` [取消] —— 资源监控在 v2.6.0 被整体删除。
`GET /resources/summary` [取消] —— 资源监控在 v2.6.0 被整体删除。

## API 规则

- 所有变更端点都需要幂等性（可重试）
- 长时间运行的任务返回作业 ID
- 错误使用结构化 JSON（code/message/details）
- 从不返回 API key
- 列表需要分页
- 所有策略和信号输出包含明确的版本和时间戳

## 扩展端点

状态标注：`[已实现]` 已在 API 中，`[计划]` 尚未实现，`[取消]` 从未实现或已撤回。

### Market Data Sources API

`[已实现]` `GET /market-data/data-sources`（只读；源在同步时自动创建）

### Feature Versions API

`[已实现]` `GET /features/versions` —— 返回 `engine_feature_version`、`indicator_version`、`price_action_version` 与 `snapshot_versions`（已落库的特征版本，来自 `feature_snapshots`，是唯一有真实写入的证据）。旧的 `definition_versions` 字段随死表 `features` 一起删除（ADR-093）。
`[取消]` `GET /features/{feature_id}/versions`（特征由代码定义、没有可寻址的行）

### Strategy Lineage API

`[已实现]` `GET /strategies/{strategy_id}/lineage`

### AI Task Monitoring API

`[已实现]` `GET /ai/tasks/{task_id}/status`（轻量状态轮询，含错误信息）
`[计划]` `POST /ai/tasks/{task_id}/cancel`（AI 调用为同步执行、无排队，v1.0 不做）

## 撤回的端点

以下路径从未被服务过（或已被取代），已从各类别中移除；每行留下一个 `[取消]` 声明与一行历史，
完整审计见 v1.6.7 / ADR-096：

- `[取消]` `GET /market-data-series`（以及 `/market-data-series/{id}`、`/market-data-series` 的创建形状）：序列的真实挂载是 `/market-data/series`，且由同步端点创建。
- `[取消]` `GET /market-data-snapshots/{series_id}`（以及 `/market-data-snapshots/latest/{series_id}`）：从未存在；最新行情是 `/market-data/latest/{symbol}`。
- `[取消]` `GET /market-data/{symbol}`：旧形状，真实路径是 `/market-data/latest/{symbol}`。
- `[取消]` `GET /strategy-parameters`（以及 `/strategy-parameters/{id}`）：参数挂在 `/strategy-versions/{version_id}/parameters`。
- `[取消]` `GET /backtest-results`（以及 `/backtest-results/{id}`）：从未存在；结果详情是 `/backtests/{run_id}`。
- `[取消]` `GET /research/runs/{id}`：从未存在。
- `[取消]` `GET /strategies/{id}/versions/{version}`：从未存在；单版本走 `/strategy-versions/{version_id}`。
- `[取消]` `POST /strategies/{id}/backtest`：从未存在；回测统一走 `/backtests`。
- `[取消]` `POST /backtests/{id}/compare`：旧形状，真实对比端点是 `/backtests/compare`。
- `[取消]` `GET /ai/providers`（以及 `/ai/providers/{id}`、创建与更新形状）：真实端点在 `/settings/ai/providers` 之下。
- `[取消]` `GET /ai/models/provider/{provider_id}`（以及 `/ai/usage/provider/{provider_id}`）：旧形状；provider 过滤是 `provider_id` query 参数。
- `[取消]` `GET /ai/prompts/{id}`（以及 `/ai/prompts` 的创建形状）：从未存在；只有 `/ai/prompts`。
- `[取消]` `POST /ai/tasks`：从未存在；AI 任务由内部创建，只读。
- `[取消]` `GET /feature-snapshots/{series_id}/bar/{timestamp}`：从未存在。
- `[取消]` `GET /github/snapshots`（以及 `/github/snapshots/{id}`）：旧挂载；GitHub 快照在 `/importer/github/sources/{source_id}/snapshots`。
- `[取消]` `GET /settings/audit`：曾返回同一批记录但不带 `actor`，已删除（ADR-070）。
- `[取消]` `GET /paper/trades/{id}`（以及 `/paper/trades/account/{account_id}`）：从未存在；账户维度用 `/paper/accounts/{account_id}/trades`。
- `[取消]` `POST /paper/orders`：从未存在；下单走 `/paper/accounts/{account_id}/execute`。
- `[取消]` `GET /features/{id}`（以及 `/features` 的创建形状）：特征由代码定义，没有可寻址的行（ADR-093）。
