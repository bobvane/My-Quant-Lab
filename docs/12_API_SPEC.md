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
`POST /strategies/{strategy_id}/versions` [已实现] —— 创建一个新的不可变策略版本。
`GET /strategies/{strategy_id}/versions` [已实现] —— 列出某个策略的版本。
`POST /strategies/validate` [已实现] —— 校验一份 DSL 文档（不落库）。

## Strategy Versions

`GET /strategy-versions` [已实现] —— 列出策略版本。
`GET /strategy-versions/{version_id}` [已实现] —— 取单个策略版本。
`PUT /strategy-versions/{version_id}/activate` [已实现] —— 激活某个版本。
`GET /strategy-versions/{version_id}/parameters` [已实现] —— 该版本已存储的参数。
`GET /strategies/versions/{version_id}/verify` [已实现] —— 校验某个策略版本没有被改动过。

## Backtests

`GET /backtests` [已实现] —— 列出回测运行。
`GET /backtests/{run_id}` [已实现] —— 取一次回测结果（`BacktestOut`）。
`GET /backtests/{run_id}/trades` [已实现] —— 列出一次回测的成交。
`GET /backtests/compare` [已实现] —— 并排对比若干次回测运行（`?ids=1,2`）。
`GET /backtests/comparisons/{comparison_id}` [计划] —— 对比为无状态即时计算，不持久化快照，v1.0 不做。
`POST /backtests` [已实现] —— 运行一次回测。
`DELETE /backtests/{run_id}` [已实现] —— 删除一次回测运行及其结果。
`POST /backtests/{run_id}/explain` [已实现] —— 解释一次已完成的回测。

回测摘要除指标外还返回 `dataset_version_id` / `symbol` / `timeframe`：只有 id 无法判断两次
回测是否可比（同一策略在不同标的/周期上的结果本就不同），集成对比表读这两个字段来标记
「不同数据窗口」。

创建回测与单次回测查询都返回 `warnings`，且**是同一份**：引擎的警告
（被忽略的参数覆盖、warm-up 长于数据）跟结果一起存进 `backtest_results.warnings_json`
（迁移 `0007_backtest_result_warnings`，ADR-054）。此前单次查询硬编码返回空列表，警告只在创建
响应里出现一次，刷新即消失。`warnings` 非空意味着下面的数字要打折扣阅读——例如
`only 400 bars available, warm-up needs 900` 对应的就是一次 `number_of_trades = 0` 的
「成功」回测。列表端点 `/backtests` 的摘要**不含** `warnings`。

## Backtest Metrics

`GET /backtest-metrics/backtest/{backtest_id}` [已实现] —— 一次回测运行的指标。

## Walk-forward/OOS

`POST /research/oos` [已实现]  (single holdout split: last N% or from a date)

`POST /research/walk-forward` [已实现]

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
- 相同 seed 必然得到相同分布；每次运行写审计事件 `monte_carlo_completed`。
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

## Paper Accounts

`GET /paper/accounts` [已实现] —— 列出模拟账户。
`POST /paper/accounts` [已实现] —— 创建模拟账户。
`GET /paper/accounts/{account_id}` [已实现] —— 取单个模拟账户。
`GET /paper/accounts/{account_id}/equity` [已实现] —— 账户权益曲线。
`GET /paper/accounts/{account_id}/trades` [已实现] —— 账户成交列表。
`POST /paper/accounts/{account_id}/reset` [已实现] —— 重置账户（强提醒并生成审计事件）。

账户响应里的资金字段是 `net_deposits`（净入金 = 入金 − 提现）与 `cash`（当前现金），
**没有** `initial_cash`：基准会随入金与提现一起上下移动，所以“初始资金”这个名字会说谎
（ADR-066）。创建时的请求体仍然是 `initial_cash`——那一刻它确实等于净入金；
创建、列表与单账户三个端点的响应字段都是 `net_deposits`（数据库列名 `initial_cash` 保留，
只是历史命名）。

入金/提现端点（`/paper/accounts/{account_id}/fund`）的 `amount` 正负都改变基准：入金抬高、
提现降低，返回 `{account_id, cash, net_deposits}`，审计 payload 同步记录 `net_deposits`。
于是不变量 `final_equity == net_deposits + 已实现盈亏`（空仓时等于 `cash`）恒成立，
取钱不会被记成亏钱。重置端点的响应也带 `net_deposits`（重置把基准一并设为期初现金）。

`net_deposits ≤ 0` 时没有收益率的分母：`/paper/accounts/{account_id}/performance`
的 `metrics.total_return`（以及其它比率类指标）为 `null`，原因写在新增的
`metric_notes` 数组里（例如 `initial capital is not positive, so ratio metrics have no
denominator`），前端据此显示「—」而不是 `NaN%`/`-100%`。

## Paper Positions

`GET /paper/accounts/{account_id}/positions` [已实现] —— 列出当前持仓。
`GET /paper/accounts/{account_id}/positions/{asset_id}` [已实现] —— 取单个持仓。
`GET /paper/accounts/{account_id}/performance` [已实现]  (equity-derived metrics)
`GET /paper/accounts/{account_id}/orders` [已实现] —— 账户订单列表。
`POST /paper/accounts/{account_id}/execute` [已实现]  (execute a persisted signal; virtual fill)
`POST /paper/accounts/{account_id}/close` [已实现] —— 关闭（冻结）一个模拟账户。
`POST /paper/accounts/{account_id}/reopen` [已实现] —— 重新打开已关闭的模拟账户。
`POST /paper/accounts/{account_id}/fund` [已实现] —— 入金/提现（有审计）。

## Paper Orders

`GET /paper/orders` [已实现] —— 列出跨账户的模拟订单。
`GET /paper/orders/{order_id}` [已实现] —— 取单个模拟订单。

## Paper Trades

`GET /paper/trades` [已实现] —— 列出跨账户的模拟成交。

## Signals

`GET /signals` [已实现] —— 列出已落库的信号。
`GET /signals/{signal_id}` [已实现] —— 取单个信号。
`GET /signals/{signal_id}/evidence` [已实现]  (feature snapshot + portfolio context)
`GET /signals/evidence/{strategy_version_id}` [已实现] —— 规则命中 + 实证统计 + 模拟统计 + 组合上下文。
`GET /signals/preview/{strategy_version_id}` [已实现] —— 预览某个策略的信号（不落库）。
`POST /signals/preview-explain` [已实现] —— 解释一次扫描意图而不落库 Signal 行。
`GET /signals/outcomes` [已实现] —— 信号结果跟踪（信号到底work不work）。
`GET /signals/outcome-summary` [已实现]  (win rate / avg PnL grouped by direction/timeframe/state/strategy)
`POST /signals/scan` [已实现] —— 干跑一次扫描；`persist=false`（默认）只算不落库，`persist=true` 才写入信号（去重）。
`POST /signals/{signal_id}/explain` [已实现] —— 解释一个已落库的信号。
`POST /signals/{signal_id}/acknowledge` [已实现] —— 确认一个信号。

`/signals/outcome-summary` 的胜率永远跟它的分母一起返回（ADR-065）：

- `symbol`：这些数字描述的范围（`null` 为全部标的）；端点接受 `?symbol=`，与 `/signals/outcomes`
  同口径，未知标的返回空范围而不是全局平均。
- `signals` / `decided` / `undecided`：范围内信号总数 / 已有可用结果的数量 / 其余数量。
  `decided` 是每个 `count` 与 `win_rate` 的分母，且 `decided == groups.ALL.count`。
- `bars_after`：评估器要在信号之后看到多少根 K 线才回填结果（`DEFAULT_BARS_AFTER = 10`）；
  `undecided` 就是还没等到这些 K 线、或该标的还没有 K 线序列的信号。
- 未决信号既不计入胜率，也不算亏损 —— 它们只是还没有结果。
- 状态字段 `evaluated` 已改名为 `decided`（同一个数字只留一个名字）。

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

`GET /ai/status` [已实现] —— AI 配置与预算。

## AI Providers

`GET /settings/ai/providers` [已实现] —— 列出 AI 供应商（key 永不返回）。
`POST /settings/ai/providers` [已实现] —— 创建 AI 供应商（key 加密存储、永不返回）。
`PUT /settings/ai/providers/{provider_id}` [已实现] —— 更新一个 AI 供应商。
`DELETE /settings/ai/providers/{provider_id}` [已实现] —— 删除一个未被使用的 AI 供应商。
`POST /settings/ai/providers/test` [已实现] —— 保存前先测试凭据。
`POST /settings/ai/providers/{provider_id}/test` [已实现] —— 测试一个已存储的供应商。

## AI Models

`GET /ai/models` [已实现] —— 跨供应商的 AI 模型；按供应商过滤用 `provider_id` query 参数。

## AI Tasks

`GET /ai/tasks` [已实现] —— 最近的 AI 任务。
`GET /ai/tasks/{task_id}` [已实现] —— 单个 AI 任务及其已存储的输出。

## AI Usage

`GET /ai/usage` [已实现] —— AI 用量行（按日期/供应商；按供应商过滤用 `provider_id` query 参数）。
`GET /ai/usage-today` [已实现] —— 今天的 AI 花费（UTC）。

## AI Prompts

`GET /ai/prompts` [已实现] —— AI 提示词模板。

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
`GET /settings/ghostfolio/holdings` [已实现] —— Ghostfolio 持仓（只读）。

## System Resources

`GET /resources/current` [已实现] —— 最新采样：NAS 概览 + Quant Lab 份额 + 容器。
`GET /resources/history` [已实现] —— CPU/内存时间序列（1h/24h/7d/30d）。
`GET /resources/events` [已实现] —— 任务资源事件（回测峰值等）。
`GET /resources/summary` [已实现] —— 直接答案：NAS 总量、Quant Lab 份额、最大占用者。

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
