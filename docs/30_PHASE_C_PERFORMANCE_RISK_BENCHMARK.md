**文档：《My Quant Lab Phase C — Performance / Risk / Benchmark 设计报告》（正式落盘）**

本文件是已交付的 Phase C 设计报告的正式文档版本：内容以已完成的最终审计与设计报告为准，**未重新审计、未改变结论、未新增设计范围、未写任何代码**。

## 状态与基线

| 项目 | 当前状态 |
|---|---|
| Phase A+B | **已正式关闭** |
| Phase C 开发基线 | **`v2.5.0-rc.3`** |
| `version.txt` | Phase C 开发阶段**暂不修改**（当前仍为 `v2.4.6`；ADR-175：预发布不回写 `version.txt`） |
| Phase C 阶段 | **设计完成、尚未实施** |
| 落盘范围 | 只读审计结论 + 设计；发现的问题只记录、不修复 |

## 本文件的边界

1. 只允许新增本文件；未修改任何既有代码、其他文档或 `version.txt`。
2. 不写业务代码、不修改数据库 Schema、不创建 migration、不修改 API、不修改前端。
3. 不修改 Phase A+B 已验收功能，不做任何顺手重构。
4. 未 commit、未 push、未创建 Release、未部署 NAS。

---

# 《My Quant Lab Phase C — Performance / Risk / Benchmark 设计报告》

> 本次只审计、只设计、只写文档。未写任何业务代码，未改 schema / migration / API / 前端 / Phase A+B，未改 `version.txt`，未 commit / push / Release / 部署 NAS，未在 NAS 上执行任何操作。发现的问题只记录、不修复。

## 0. 结论摘要（先看这一段）

1. **最关键的架构事实**：当前指标**是从完整逐 bar 权益曲线现算的**（`backend/app/research/metrics.py:72 compute_metrics(equity_curve, trades, ...)`），而**这条完整逐 bar 权益曲线是持久化的**（`backend/app/domain/models.py:351 equity_curve_json`，写入点 `backend/app/data/backtest_service.py:334`，并被不可变触发器钉死：`backend/app/domain/immutability.py:78-80` / `:114-116`）。
   ⇒ **Phase C 可以零新表、零重跑、对 NAS 上已有的 15 条回测与 10 条实验立即生效**：绩效/风险/对照全部是「对已冻结证据的纯函数」。
2. **每一根 bar 的收盘价也已经存下来了**：`backend/app/research/engine.py:477-485` 每个点带 `{"timestamp", "equity", "cash", "position_value", "close"}`。
   ⇒ **买入持有对照不需要再读行情库**：对照曲线 = `initial_capital × close_t / close_0`，与策略曲线**逐 bar 同索引、同窗口、同日历**，`§七 时间区间公平性` 由此**结构性地消失**（不是靠约定，是靠复用了同一批 bar）。
3. **因此推荐方案**：`Experiment 数据方案 = C（只保存原始数据，需要时计算）`——不新建表、不扩 `experiment_results`、不加列。一个只读端点 + 一个纯函数模块 + 前端两个展示块。
4. **绝不改动 `metrics.py`**：它的输出参与 `result_hash`（`backend/app/research/engine.py:524-537`），任何字段增减都会改变以后每条新回测的指纹。新指标一律走**独立命名空间**，不进 `Metrics`、不进哈希。
5. **AI 只解释**：结构化分析结果 → 一次 `runtime.run_task()` → 结构化解释；AI 输出中的每个数字都必须在输入里出现过（可机检），否则拒绝。
6. **本阶段不引入**：新微服务 / 新数据库 / 新队列 / 新缓存 / 大数据框架 / 新 Celery 任务 / 新表 / 新前端路由。

---

## 1. 当前能力盘点

### 1.1 数据产出层（回测引擎到底产出了什么）

| 数据 | 是否产生 | 是否持久化 | 位置 |
|---|---|---|---|
| equity curve（逐 bar） | ✅ 每根 bar 一个点 | ✅ | `engine.py:477-485` → `models.py:351 equity_curve_json` |
| cash / position_value | ✅ 每点都有 | ✅（同上） | `engine.py:479-484` |
| close（mark price） | ✅ 每点都有 | ✅ | `engine.py:483` |
| trades（entry/exit、price、qty、fees、slippage、pnl、pnl_pct、R、MAE、MFE、reason、ambiguous_fill） | ✅ | ✅ `backtest_trades` | `engine.py:658-715`；`models.py:392-420` |
| daily / periodic returns | ❌ 不单独产出 | ❌ | 由 equity curve 派生（`metrics.py:109 np.diff(equity)/equity[:-1]`） |
| exposure（持仓时间占比） | ✅ | ✅ | `engine.py:521` + `:718-722`（**在引擎里补，不在 metrics.py**） |
| turnover（换手率） | ✅ | ✅ | `engine.py:522` + `:725-729`（同上；口径 = Σ(entry_price×qty)/capital） |
| drawdown curve | ✅ 内部算了数组 | ❌ 从不持久化 | `metrics.py:139-141` 只留 `max_drawdown` 标量 |
| drawdown duration | ✅（单位 = bar） | ✅ | `metrics.py:142` + `:170-179` |
| recovery period | ❌ | ❌ | 缺口 |
| start / end date | ⚠️ 请求里有，**`backtest_runs` 行上没有列** | ❌ | `models.py:295-338`；`backtest_service.py:240-254`。**权威来源 = `equity_curve_json` 首尾 timestamp** |
| initial capital | ✅ | ✅（`execution_model_json`） | `models.py:306` |
| final equity | ✅ | ✅ | `metrics.py:85` / `summary_json` |
| benchmark 所需数据 | ⚠️ 原始 OHLCV 已在 `market_data_bars`，且 `close` 已冗余进 equity curve | — | `models.py:158-190`；`engine.py:483` |

**结论（用户问的那个关键问题）**：**两个都对** —— 指标是**从完整 equity curve 现算**的，同时**完整曲线本身被保存**；`summary_json` / `backtest_metrics` 只是同一批数字的投影。Phase C 因此可以「不重跑也扩展分析」。

### 1.2 现有指标逐一建档（用户要求的字段格式）

`periods = BARRS_PER_YEAR[timeframe]`（`metrics.py:18-26`：1m 98280 / 5m 19656 / 15m 6552 / 1h 1638 / 4h 409.5 / **1d 252** / 1w 52；未知回退 252.0）；`years = len(equity)/periods`。

| 指标 | 计算位置 | 公式 | 输入 | 持久化 | API | 前端 | 复用 |
|---|---|---|---|---|---|---|---|
| total_return | `metrics.py:107` | `final/initial - 1` | equity | ✅ `metrics_json`+`summary_json`+`backtest_metrics(return)` | ✅ | ✅ 卡片「总收益率」 | ✅ 原样 |
| cagr | `metrics.py:111-113` | `(final/initial)^(1/years) - 1`，`years = n_bars/periods` | equity | ✅ | ✅ | ✅「年化复合收益率」 | ✅ 原样（口径按 bar 折算，非日历天数） |
| annualized_volatility | `metrics.py:118-119` | `std(returns, ddof=1)×√periods` | equity | ✅ | ✅ | ⚠️ 只有标签+二级明细 | ✅ 原样 |
| sharpe | `metrics.py:120-125` | `(mean(returns) − rf/periods)/std ×√periods`，需 `std>0` 且 `len(returns)≥2` | equity | ✅ | ✅ | ✅ | ✅ 原样 |
| sortino | `metrics.py:127-137` | 同 Sharpe，分母换成**负收益子样本**的 `std(ddof=1)`，需 `len(downside)≥2` 且 `>0` | equity | ✅ | ✅ | ✅ 二级明细 | ✅ 原样 |
| max_drawdown | `metrics.py:139-141` | `min(equity/running_max − 1)`，`running_max = np.maximum.accumulate` | equity | ✅ | ✅ | ✅ | ✅ 原样 |
| max_drawdown_duration_bars | `metrics.py:142` + `:170-179` | 最长**严格为负**的连续段长度 | equity | ✅ | ✅ | ✅ 二级明细 | ✅ 原样（**无 recovery**） |
| number_of_trades | `metrics.py:144-148` | `len(pnl_values)` | trades | ✅ | ✅ | ✅ | ✅ 原样 |
| win_rate | `metrics.py:149` | `wins/len(pnl)`，≥1 笔才算 | trades | ✅ | ✅ | ✅ | ✅ 原样 |
| avg_win / avg_loss | `metrics.py:150-151` | 均值；无样本时 `0.0` | trades | ✅ | ✅ | 二级明细 | ✅ 原样（注意 `0.0` 与 `null` 的区别） |
| profit_factor | `metrics.py:152-158` | `gross_profit/|gross_loss|`；无亏损 → `null`+note | trades | ✅ | ✅ | ✅「盈亏效率」 | ✅ 原样 |
| expectancy | `metrics.py:159` | `mean(pnl)` | trades | ✅ | ✅ | 二级明细 | ✅ 原样 |
| max_consecutive_losses | `metrics.py:182-191` | 最长连亏 | trades | ✅ | ✅ | 二级明细 | ✅ 原样 |
| average_holding_bars | `metrics.py:164-166` | `mean(holding_bars)` | trades | ✅ | ✅ | ✅ | ✅ 原样 |
| exposure | `engine.py:521` + `:718-722` | `count(in_position)/n`；空 → `None` | 内存 `in_position` | ✅ | ✅ | 二级明细 | ✅ **只读已存值**（见 §14 一致性要求） |
| turnover | `engine.py:522` + `:725-729` | `Σ(entry_price×qty)/capital`；无交易或 capital≤0 → `None` | trades | ✅ | ✅ | 二级明细 | ✅ 只读已存值 |
| notes（为什么是 N/A） | `metrics.py:53` 等 9 处 | 文本原因 | — | ⚠️ `as_dict()` 丢弃 | ❌ | ❌ | ♻️ 语义必须继承（新层也要给原因） |
| MAE / MFE / R-multiple | `engine.py:678-694` | 逐笔 | trades | ✅ `backtest_trades` | ✅ | ✅ 交易明细 | ✅ 原样 |

**Sharpe 的用户重点问题，逐条答复（以 `metrics.py:117-125` 为准）**：

- 无风险利率：**默认 0**（`compute_metrics(..., risk_free_rate: float = 0.0)`），全仓没有任何调用点传非 0；即当前实际口径是「超额收益 = 原始收益」。
- 收益频率：**逐 bar 简单收益** `np.diff(equity)/equity[:-1]`（不是对数收益、不是日频重采样）。
- 是否年化：**是**，乘 `√periods`。
- 年化因子：`BARRS_PER_YEAR[timeframe]`，`1d = 252`。**已知口径问题**：crypto 7×24 日线也按 252 折算（应为 365）——记录为债务，Phase C 不改（改它会改变新回的 `result_hash`）。因为策略与对照用**同一个因子**，对照比较仍然成立。
- 数据不足：`len(returns) < 2` 或 `std == 0` → `sharpe = None` + note `"not enough return samples for Sharpe"`。
- NaN / Infinity：**已闭合（ADR-195）** —— `metrics.py` 自己清洗：分母 ≤ 0 的 bar 与非有限的收益样本被剔除并记 `notes`，终值为负时不给 CAGR（负底数开分数次方是复数，`json.dumps` 直接抛 `TypeError`），所有浮动比率经 `_safe()`（不再是死函数）落成有限值或 `None`。新分析层仍自带 `math.isfinite` 兜底（§11），两条路互不依赖。

**Max Drawdown 的用户重点问题**：

- 用 **equity curve** 的滚动峰值算（不是交易结果求和）。
- **有 drawdown 数组，但不持久化**（`metrics.py:139-141`，出了函数即丢）。前端 `BacktestView.vue:756-765` 是**另算一遍**的（`dd = (p.equity − peak)/peak × 100`）；Monte Carlo 里还有**第三份**（`monte_carlo.py:65-73`）——共 **3 处重复实现**（Phase C 必须只挑一处，见 §14）。
- `drawdown duration` **有**（bar 口径）；`recovery period` **没有**。

---

## 2. 可以直接复用的代码（不要重写）

| 复用对象 | 位置 | 复用方式 |
|---|---|---|
| `compute_metrics(...)` | `backend/app/research/metrics.py:72-167` | **对照曲线的指标也用它算**（`compute_metrics(bench_equity, [])`）⇒ 与策略**同口径**（同 `periods`、同回撤定义、同 null 语义），这是「可比性」的唯一正确来源 |
| `BARRS_PER_YEAR` | `metrics.py:18-26` | 年化因子唯一出处 |
| 已持久化的 19 个指标 | `backtest_results.metrics_json` | **原样读取，不重算**（一个事实一个出口，ADR-070/095） |
| `equity_curve_json`（含 `close`） | `models.py:351` + `engine.py:477-485` | 策略曲线 + 对照曲线的**唯一数据源** |
| `BacktestTrade` 行 / 交易明细 API | `models.py:392-420`、`backtests.py:169-190` | 风险项的 worst trade / 连亏 |
| `load_bars(db, series, start=, end=, only_closed=True)` | `backend/app/data/market_data_repo.py:177-201` | **仅作 legacy 兜底**（老曲线缺 `close` 时）；与回测 `backtest_service.py:167` 完全同参 |
| `run.dataset_version_id` → `MarketDataSeries` | `models.py:302`、`:331-333`（`lazy="joined"`） | 定位「策略真正交易的那条序列」（**不要用 symbol 解析**，见 §6） |
| 样本量常量 | `monte_carlo.py:53 MIN_TRADES_FOR_CONFIDENCE = 20`；`backtest_service.py:64 MIN_BACKTEST_BARS = 60`；`metrics.py:28-29 MIN_SAMPLES_SHARPE = 2` / `MIN_SAMPLES_WINRATE = 1` | 可靠性判定只允许引用既有常量，不新造阈值 |
| `capabilities.py` | `backend/app/capabilities.py:145-177`（metrics 组）、`:251-260`（`var_cvar` / `calmar` 明确列为**未实现**） | 实现后必须同步该注册表，否则 `/ai/capabilities` 对 AI 说谎 |
| AI 管道 | `backend/app/ai/contracts/*.md`（角色契约，`role_contracts.py` 加载）、`backend/app/ai/runtime.py run_task()`（缓存身份+预算+`AITask` 审计）、`backend/app/ai/budget.py decide()`、`backend/app/ai/explain.py`（`BACKTEST_EXPLANATION_SCHEMA`）、`ai.py:237-241 POST /ai/backtests/{run_id}/explain` | 新增解释**只是加一个 `## Task:` 段 + 能力条目**，不新建管道 |
| AI 禁词/数字闸门思路 | `backend/app/ai/research_schemas.py:179-199`、`:815-817` | 解释输出的数字校验沿用同一思路 |
| 前端组件 | `frontend/src/components/StatCard.vue`（label/value/sub/tone + `MetricHint` + `metricPlain`）、**`MultiLineChart.vue`（已支持多序列 + legend + 逐点 null）** | 对照叠加**不需要改 EquityChart**（它只有单序列），直接用 `MultiLineChart` |
| 前端标签/单位注册表 | `frontend/src/wording.ts:197-218`、`frontend/src/metrics.ts:183/187`、`frontend/src/format.ts:38-` | 新指标三处登记规则见 §9 |
| 既有展示位 | `BacktestView.vue:1721-1765`（一级卡）、`:2746-2757`（二级表）、`ExperimentsView.vue:433-442/1169-1179`（结果块）、`:1332-1340`（比较表） | Phase C 只填这些位置 |

---

## 3. 当前缺口（按优先级，全部实测）

**P0（Phase C 必须补，否则需求不成立）**
1. 无「对照（buy & hold）」：全仓 `benchmark|buy_?hold|alpha|beta` **零计算命中**（命中项全是 SSRF 保留网段与「净入金基准」语义）。
2. 无 `calmar` / 无 `recovery period`：`capabilities.py:256-260` 自己写着 `"not computed yet; max drawdown duration and recovery are also missing"`。
3. 无「样本是否足够」的**结论性字段**：只有 `metrics.notes` 文本（且 `as_dict()` 丢弃）；前端 `BacktestView.vue:1301 MIN_MEANINGFUL_TRADES = 10` 与 `monte_carlo.py:53 MIN_TRADES_FOR_CONFIDENCE = 20` 是**两个阈值**，一个页面可能同时说两种话。
4. ~~`metrics.py` 缺 `isfinite` 兜底（`_safe` 是死代码）⇒ 新层必须自带。~~ **已修（ADR-195）**：`compute_metrics` 现在自己清洗（见 §2 的 NaN/Infinity 一条）；新层仍自带兜底，不依赖它。
5. 对照需要一个**稳定的窗口定义**：`backtest_runs` 行上**没有** start/end 列，异步 run 的窗口只活在队列 payload（`backtests.py:65`、`workers/tasks.py:569`）⇒ 必须改用 `equity_curve_json` 首尾 timestamp。

**P1（影响质量，可在同一交付里顺手做，但不算新能力）**
6. 回撤算法 **3 份重复**（`metrics.py:139-142`、`monte_carlo.py:65-73`、`BacktestView.vue:756-765`）。
7. `backtest_metrics` 表**只写不读**（写：`backtest_service.py:524-546`；读端点 `backtest_metrics.py:16-28` 读的是 `summary_json`/`metrics_json`）⇒ 一份没人读的第二事实（ADR-095）。
8. `Metrics.as_dict()` 丢弃 `notes` ⇒ 「为什么是 N/A」在 API 上不可见（paper 端点靠单独 `metric_notes` 补救，回测没有）。
9. `BARRS_PER_YEAR["1d"] = 252` 对 crypto 不准（债务，不改）。
10. `exposure` / `turnover` 不在 `metrics.py` 而在引擎里补 ⇒ 想「重算」的人会拿到不同结果（所以 Phase C 一律读已存值）。

**P2（记录，不在 Phase C）**
11. 文档漂移：`docs/13_UI_UX.md:82` 声称有「月度/年度视图、参数信息」（代码里 0 命中）；`docs/11_DATA_MODEL.md:96` 写 `dataset_snapshot_id`（实际 `dataset_version_id`）；`docs/07_BACKTEST_ENGINE.md:78-86` 要求交易日历而代码未实现。
12. `sensitivity` / `walk_forward` / `oos` 的 `ExperimentResult.backtest_run_id` 恒为 `None` ⇒ 它们**没有权益曲线可分析**（Phase C 第一版不覆盖）。
13. `ExperimentResult` 没有 `result_hash` / `dataset_hash` 列（只在 JSON 里）。
14. 命名陷阱：`MarketDataSeries.__tablename__ == "market_data"`；`BacktestRun.dataset_version_id` 存的是 `market_data.id`（不是 dataset_version 字符串）；无 `BacktestDetailOut`（真名 `BacktestOut`）。

---

## 4. Performance 方案

原则：**先复用已存数字，再只补「新问题真正需要」的量**。不为了指标数量好看加东西。

### 4.1 判定表

| 指标 | 判定 | 理由 | 数据来源 |
|---|---|---|---|
| Total Return | **必须做** | 已有；回答「我赚了多少」 | 已存 `metrics_json` |
| CAGR / Annualized Return | **必须做** | 已有；跨窗口可比 | 已存（**带上「按 bar 折算、非日历」的注**） |
| Volatility（年化） | **必须做** | 已有但**几乎没露脸**（只有标签+二级明细）⇒ 提到风险层显性展示 | 已存 |
| Sharpe | **必须做** | 已有，风险调整收益的主指标 | 已存 |
| Sortino | **建议做（本版做）** | 已有；对「只在乎下跌」的用户更贴切 | 已存 |
| **Calmar** | **建议做（本版做）** | **零新数据**：`cagr / |max_drawdown|`；且 `capabilities.py` 已把它声明为缺口，补上要同步注册表 | 派生（`cagr`、`max_drawdown`） |
| Win Rate | **必须做** | 已有；但必须与「样本量」一起说，否则误导 | 已存 |
| Profit Factor | **必须做** | 已有；盈亏效率 | 已存 |
| Average Win / Loss | **建议做（本版做）** | 已有；解释盈亏效率的来源 | 已存 |
| Trade Count | **必须做** | 已有；**可靠性分母** | 已存 |
| Exposure | **必须做（只读）** | 已有；回答「钱在场内的时间」 | 已存（**不重算**，理由见 §3-10） |
| Turnover | **建议做（只读）** | 已有；回答「代价」；口径要写清（Σ入场名义额/资金，**是单边**） | 已存 |
| **对照的 6 项**（total_return / cagr / volatility / sharpe / max_drawdown / final_equity） | **必须做** | 用户明确要求「并进一步比较」 | 用 `compute_metrics` 对对照曲线算 |
| **Excess Return**（策略 − 对照） | **必须做** | 一句话就能说清「跑赢没有」 | 派生 |
| Expectancy / 平均持仓 / 最长连亏 | **建议做** | 已有，二级明细 | 已存 |
| **Beta / Alpha / Information Ratio / Tracking Error** | **暂不做** | 需要收益序列回归 + 非零无风险利率假设（当前 rf 恒 0），样本量普遍撑不起，且解释成本高于价值；第一版只做「并排 + 超额」 | — |
| **滚动绩效（rolling Sharpe / rolling vol）** | **暂不做** | 需要额外窗口约定与更多存储/展示位；第一版用「分段对照」代替 | — |
| **月度 / 年度收益表** | **建议做一半（worst month）** | 完整月表要新表/新图；但「最差的一个月」是普通人能理解的**单位**（§5） | 从 equity curve 的 timestamp 分组 |

### 4.2 新增派生指标的定义（写进实现契约，避免口径漂移）

```
calmar            = cagr / abs(max_drawdown)          # max_drawdown == 0 或 cagr == null → null
downside_dev_ann  = std(returns[returns < 0], ddof=1) × √periods   # 与 Sortino 分母同源，显性化
worst_bar_return  = min(returns)                      # 最差单根 bar
worst_month_return= min(逐自然月 (last/first - 1))     # 按 timestamp 的 year-month 分组
excess_return     = strategy.total_return - benchmark.total_return
final_equity_gap  = strategy.final_equity  - benchmark.final_equity
```

**展示口径**：所有衍生指标必须与「原始 19 项」分列在响应里（`performance.stored` vs `performance.derived`），并在 UI 上注明「派生」不算引擎原始输出。

---

## 5. Risk 方案

| 风险项 | 判定 | 依据 / 备注 |
|---|---|---|
| Maximum Drawdown | **必须做** | 已有（`metrics.py:139-141`） |
| Drawdown Duration | **必须做** | 已有 `max_drawdown_duration_bars`（**bar 口径**；同时给「≈ 天/月」的人话换算，换算只做乘法，不引入日历） |
| **Recovery Period** | **必须做** | 新：从最深谷回到「此前峰值」所需的 bar 数；**到窗口结束仍未回本 → `null` + `recovered: false`**（不能假装已恢复） |
| Worst Trade | **必须做** | 新：`min(pnl)` + 该笔时间/方向；数据在 `BacktestTrade` |
| Worst Period | **建议做（本版做）** | 最差单根 bar + 最差自然月（§4.2） |
| Consecutive Losses | **建议做** | 已有 `max_consecutive_losses` |
| Downside Volatility | **建议做（本版做）** | 新：把 Sortino 的分母显性化（§4.2） |
| Exposure | **建议做（只读）** | 已有；解释「回撤小是因为大部分时间没在场」 |
| **Concentration** | **不做** | 本系统是单标的策略，没有持仓分布可谈；强行做是假的专业感 |
| **VaR** | **第一版暂不实现** | 历史 VaR 需要收益分布假设与足够样本，且极易被误读为「最大可能损失」；当前真正可用的分位信息已经在 Monte Carlo（`monte_carlo.py:207-211` 的 p5…p95）里，**高级模式已有**，不重复造 |
| **CVaR** | **第一版暂不实现** | 同上；且 `capabilities.py:251-255` 明确把两者列为未实现，v1 不动它 |
| 尾部风险的人话替代 | **必须做** | 用「最差的一个月 / 最差的一笔 / 最深的一跤 + 多久没爬起来」替代 VaR 的沟通职能 |

---

## 6. Benchmark 方案（Phase C 的重点）

### 6.1 数据来源：**优先零成本路径，其次复用行情库**

| 方案 | 数据来源 | 是否新增读取 | 是否可行 | 采用 |
|---|---|---|---|---|
| **B1（首选）** | `equity_curve_json[*]["close"]`（`engine.py:483`） | **零** | ✅ 曲线里每根 bar 都有收盘价 | ✅ **主路径** |
| B2（兜底） | `load_bars(db, run.dataset, start=curve[0].timestamp, end=curve[-1].timestamp, only_closed=True)`（`market_data_repo.py:177-201`） | 一次只读查询 | ✅ 语义与回测完全同参（`backtest_service.py:167`） | ✅ 仅当曲线缺 `close`（老引擎数据） |
| B3 | 新建行情服务 / 新 provider / 新增基准表 | 新服务 | ❌ 违反 §12 约束 | ❌ 明确不做 |

**对照曲线定义（两条路径都必须产出同一形状）**：

```
close_0 = 首根 bar 的 close（= 策略曲线的第 0 点）
bench_equity_t = initial_capital × close_t / close_0
曲线点：{"timestamp": 与策略逐字相同的 iso 串, "equity": ..., "close": ...}
指标：compute_metrics(np.array(bench_equity), [])   ← 复用，保证同口径
```

### 6.2 时间区间公平性（用户 §七 的全部要求，逐条给规则）

| 问题 | 第一版规则 |
|---|---|
| 起止区间 | **由 `equity_curve_json` 首尾 timestamp 决定**（`backtest_runs` 行上无列，异步窗口只活在 payload） |
| 初始资金 | 与策略**同一个值**（`execution_model_json.initial_capital`） |
| 缺失交易日 | **不需要处理**：主路径下对照与策略用的是**同一批 bar**（同一 `close` 序列），证券交易日历差异在结构上不存在 |
| 股票 vs Crypto 日历 | 同上，**逐 bar 同索引**；不为对照引入任何日历/补日/插值（本仓库也确实没有任何日历代码） |
| 数据源失败 / 无 bars | 主路径不读库 ⇒ 不受 provider 影响；B2 兜底失败（空 frame，`market_data.py:193-206` 已证明空结果是合法返回）→ `benchmark = null` + `caveats: ["benchmark_unavailable"]`，**绝不画假曲线** |
| 未闭合 bar | 主路径天然不含；B2 必须传 `only_closed=True`（与 `backtest_service.py:167` 一致），否则会多 1 根 |
| 多 series 消歧 | 需要读库时**必须**用 `run.dataset_version_id`（`models.py:302`），**禁止** `resolve_series(symbol=...)`（同 symbol 可有多 series，`models.py:147-153`；`market_data_repo.py:390-403` 的 tie-break 不保证选中该 run 的那条） |
| 费用口径 | 策略收益**含** fee/slippage（`engine.py:141-142`）；对照是**纯价格**（不计费用）。⇒ 必须在 UI 与 API 里**明写**「对照按收盘价买入并持有，未计费用」；这个方向对策略是**保守**的（对照偏高），可接受 |
| 同名不同标的 | 对照只用**该 run 自己的序列**（不是「指数」）。v1 **不做**市场指数对照（会引入第二条 series 的日历/币种/时区问题） |

### 6.3 命名（重要）

「基准」二字在本仓库**已被占用**：`net_deposits`＝净入金＝收益率的分母（ADR-066，`paper.py:447-449`、`docs/12_API_SPEC.md:394-434`、`frontend/src/metrics.ts:43-46`、`PaperView.vue:1026`）。
⇒ **UI/文档一律用「对照」「买入持有对照」**；数据字段用英文 `benchmark`；**不要**再在中文里用「基准」指 buy & hold。

---

## 7. Experiment 数据方案（A/B/C/D 比较与推荐）

| 维度 | A 扩 `experiment_results`（存分析结果） | B 新建 performance/risk 表 | **C 只存原始数据、需要时算（推荐）** | D 其他（挂 `summary_json` / 复活 `backtest_metrics`） |
|---|---|---|---|---|
| 数据一致性 | 中：实验行与回测行各存一份 ⇒ 两份事实风险 | 差：第三份事实 | **最好：不产生第二份事实（ADR-070/095）** | 差：`backtest_metrics` 已是只写不读的负债 |
| 查询效率 | 好（读列） | 好 | 中（每次请求 O(bars) 计算，实测微秒级；JSON 解析是主成本） | 好 |
| 数据可重现性 | 需靠 `analysis_version` 补 | 同 | **最好：由不可变证据（`equity_curve_json` + `trades`）+ 纯函数决定；同一 run 恒定输出** | 中 |
| Schema 复杂度 | 需新迁移（可空 JSON 列） | **最高**（新表+迁移+回填） | **最低：零迁移、零列** | 零迁移但语义混入快照列 |
| NAS 资源 | 存储 × N 实验 | 存储 × N + 索引 | **零新增存储**；算力可忽略 | 零新增 |
| 后续 AI 分析 | 好 | 好 | **好**（AI 输入就是一次响应，天然结构化） | 中 |
| 未来版本兼容 | 需要 `analysis_version` 失效策略 | 同 | **最省心：升级 `ANALYSIS_VERSION` 后所有历史自动用新口径，无需回填** | 差 |
| 旧数据可用性 | **已存在 15 条 run 无分析结果 ⇒ 必须回填或双路径** | 同 | **天然覆盖所有历史（含 NAS 上已有数据）** | 天然覆盖 |

**推荐：C（只保存原始数据，需要时计算）。**
理由：① 输入是不可变证据（`immutability.py` 强制），输出是纯函数 ⇒ 一致性/可重现性零成本；② 对已有数据立即生效，无需迁移与回填 —— 这在 NAS 环境里是最重要的；③ 避免第二份事实（本仓库为此已写过 ADR-070/095）；④ 未来改口径只需递增 `ANALYSIS_VERSION`。

**如果**将来实测发现某个页面需要列级缓存（例如实验列表要显示对照收益），**唯一允许的加法**是给 `backtest_results` 加一列：
`analysis_json JSON NULL` + `analysis_version String(16) NULL`（新迁移 `0021_*`，`down_revision = "0020_audit_log_action_width"`，链在尾部、不断分支），读时若 `analysis_version != ANALYSIS_VERSION` 就重算且**不写回**（保持只读端点）。**第一版不做这一步。**

---

## 8. AI Explanation 接口设计

### 8.1 数据流（AI 只碰「已经算好的结构化结果」）

```
BacktestResult（不可变证据）
   ├── metrics_json（19 项，引擎已算）
   ├── equity_curve_json（逐 bar）
   └── BacktestTrade 行
        ↓  纯函数（backend/app/research/analysis.py，ANALYSIS_VERSION）
结构化分析结果 analysis_json（performance / risk / benchmark / sample / caveats）
        ↓  （只读 API，§11）
        ↓  作为【输入事实包】喂给 AI
runtime.run_task(task_type="performance_explanation", role_contract, input=事实包)
        ↓  BACKTEST_EXPLANATION 风格的严格 schema
结构化解释（conclusion / drivers / risks / confidence / next_step）
```

### 8.2 事实包内容（AI 的输入，全部是数字与标签，不含原始曲线）

```
strategy: {name, version, symbol, asset_class, timeframe, window_start, window_end, bars}
performance: {total_return, cagr, annualized_volatility, sharpe, sortino, calmar, win_rate,
              profit_factor, avg_win, avg_loss, expectancy, trade_count, exposure, turnover}
risk: {max_drawdown, max_drawdown_duration_bars, recovery_bars, recovered, worst_bar_return,
       worst_month_return, worst_trade, max_consecutive_losses, downside_deviation}
benchmark: {label:"买入持有对照", total_return, cagr, annualized_volatility, sharpe,
            max_drawdown, final_equity, excess_return, window_matched:true}
sample: {trades, bars, years, tier:"insufficient|preliminary|enough", tier_text}
caveats: [{code, message}]
```

### 8.3 强制「只解释、不计算」的机检规则

1. 分析结果**必须**由 `backend/app/research/` 产出（模块 docstring 已立此约定：`experiment_service.py:17-18`、`experiments.py:17-18`「no quant value is computed outside `app.research`」）。
2. AI 调用必须走 `runtime.run_task()`（缓存身份、预算闸门、`AITask` 审计都在里面）。
3. 输出是**严格结构化 schema**（新增 `## Task: performance_explanation` 段进 `backend/app/ai/contracts/*.md`），不是自由文本。
4. **数字准入校验**：对输出里所有数值 token 做「必须在事实包里出现过（容差：四舍五入到展示精度）」校验，任一不匹配即判定失败并降级为「只显示数字，不显示解释」。
5. 沿用 `backend/app/ai/research_schemas.py:179-199/815-817` 的禁词/正则思路，禁止出现「预计」「应该会」「将会」类预测性措辞。
6. 实现 Calmar/对照后**必须**同步 `backend/app/capabilities.py`（`:251-260` 目前把 calmar 列为未实现、把风险能力描述为「stop at volatility, drawdown and Sortino」）。
7. **UI 不得依赖 AI**：AI 不可用（未配置 provider/预算耗尽/超时 600s）时，数字块与对照块照常完整显示。

**用户示例的口径对照**（确保输入能支撑这句话）：

> 「获得 18.2% 收益，但没有跑赢同期 Buy & Hold 的 24.7%；优势是最大回撤只有 8.7%，低于对照的 31.4%；但只有 18 笔交易，还不足以证明稳定性。」

需要的正是 `strategy.total_return` / `benchmark.total_return` / 两侧 `max_drawdown` / `sample.trades` + `sample.tier_text`。8.2 的事实包已全部覆盖。

---

## 9. 前端 UX 方案（本阶段只设计，不改 UI）

### 9.1 信息层级（回答四个问题，而不是先上 JSON）

```
① 核心结论（一句话 + 一个判词）        「赚了 18.2%，但没跑赢同期买入持有（+24.7%）」
② 收益（总收益 / 年化 / 期末权益 / 波动）
③ 风险（最大回撤 / 回撤持续 / 恢复 / 最差一笔 / 最差一个月 / 连亏）
④ 对照（并排两列：策略 vs 买入持有；超额；回撤对比；附「对照未计费用」）
⑤ 稳定性（交易笔数 / 暴露时间 / 换手 / 判词：样本够不够）
⑥ 详细指标（现有二级指标表 + 口径注）
```

### 9.2 落点（不新增导航页）

| 页面 | 位置 | 说明 |
|---|---|---|
| 回测页 | **`BacktestView.vue:1835`（可信程度怎么样？）之后、`:1860`（高级提示）之前** 插一个「绩效 / 风险 / 对照」卡组；权益曲线用 **`MultiLineChart`**（已支持多序列 + legend + `value: null`）叠加对照，或把对照作为第二条序列加在 `:2528-2530` 区块下 | 这是页面上唯一「结论之后、研究工具之前」的空档；且**不触碰**结论卡必须早于权益曲线/高级段的硬约束（`test_frontend_contracts.py:492-497`） |
| 实验详情 | `ExperimentsView.vue:1169-1179` 结果块旁增「对照」两行 + 一个「看绩效/风险明细」链接（指向回测页/新块） | 详情已有 `backtest_run_id`，可直接取分析 |
| 实验列表 | `:979-990` 只加「对照收益」「超额」两列（可选，只对 `kind=backtest`）；不加就保持现状 | 列表不做重计算（避免 N+1） |
| 首页 | `DashboardView.vue:532-570` 结论卡加一句「最近实验：策略 X% vs 对照 Y%」 | 只显示服务端数字 |
| 高级模式 | 二级指标表 `:2746-2757` 增加 calmar / downside vol / worst month；Monte Carlo/集成区不动 | 专业字段不占第一屏 |

### 9.3 硬性登记规则（否则守卫直接红）

1. 新指标标签必须**双登记**：`frontend/src/wording.ts:197-218` 加 `{ label, alias? }`，且在 `frontend/src/metrics.ts` 的 `METRIC_NOTES`（`:183`）里出现该中文标签（或进 `NOT_A_METRIC` `:187`），否则 `test_ui_promises.py:268-280` 红。
2. 单位只能由 `frontend/src/format.ts` 的 `RATIO_METRICS` 决定；**`sharpe` / `profit_factor` / `number_of_trades` 不得加入**（`test_frontend_contracts.py:126-156`）。
3. 新增 `<StatCard ... />` 必须自闭合（`test_frontend_contracts.py:366-370` 用 `re.findall(r"<StatCard\b.*?/>")`）。
4. **不得**在视图内再定义 `formatMetric` / `RATIO_METRICS`（同上）。
5. 缺值语义：数字块一律 **`未知` / `N/A`，绝不写 0**（`ExperimentsView.vue:121-124/455-474` 已经是这个口径，回测页 `metricValue` `:1330-1333` 同）。
6. 若最终选择新增导航页，则必须**三处同步**：`main.ts:20-31` + `App.vue:70-85` + `docs/13_UI_UX.md` §1 围栏（`:17-29`）且 `:15`「共 11 条」改数字（`test_ui_promises.py:105-130` 与 `test_frontend_contracts.py:601-627` 双红风险）。**推荐 v1 不新增页面。**
7. `docs/13_UI_UX.md` 新增节必须恰好一行 `状态：…`，且 `已实现（路径…）` 里的路径必须存在；「部分实现/尚未实现」必须在 §15 欠账里引用节号（`test_ui_promises.py:133-167/393-408`）。
8. **注意**：`ExperimentsView.vue` 目前**没有任何源码文本守卫** ⇒ 改实验页展示时没有测试会提醒你同步 `docs/13 §14`，必须人工同步。

---

## 10. 数据模型建议

**第一版：零 Schema 变更。**

| 建议 | 内容 |
|---|---|
| 不新建表 | 无 `performance_*` / `risk_*` / `benchmark*` 表 |
| 不扩 `experiment_results` / `strategy_experiments` | 避免第二份事实；`summary_json` 已有 `metrics` 投影 |
| 不复活 `backtest_metrics` | 它已只写不读（`backtest_service.py:524-546` 写、无读路径），继续写只会加深负债 |
| 不存 drawdown/benchmark 曲线 | 全部可确定性地从 `equity_curve_json` 重建 |
| 若必须缓存（第二版，可选） | `backtest_results.analysis_json JSON NULL` + `analysis_version String(16) NULL`；迁移 `0021_*`（`down_revision="0020_audit_log_action_width"`，**必须**链在尾部保持单 head，revision id ≤ 32 字符；`strategy_experiments` 若加列按 0019 先例**必须可空**） |

`docs/11_DATA_MODEL.md` 的建议增量（无测试强制，但按约定）：在新节里写明「绩效/风险/对照是**派生视图**，不是实体」，并顺手修 `:96` 的 `dataset_snapshot_id` → `dataset_version_id` 漂移。

---

## 11. API 建议

**只加一个只读端点**（最少面、最少重复）：

```
GET /api/v1/backtests/{run_id}/analysis        → 200 AnalysisOut
```

响应（示意，全部 `snake_case`，缺值一律 `null`）：

```jsonc
{
  "run_id": 13,
  "result_hash": "b61c1c78…",           // 与 run 一致，证明分析绑定的是同一证据
  "analysis_version": "1.0.0",
  "window": { "start": "…+00:00", "end": "…+00:00", "bars": 400 },
  "performance": {
    "stored":  { /* 19 项原样，含 exposure/turnover */ },
    "derived": { "calmar": 0.19, "downside_deviation": 0.061, "excess_return": -0.023, "final_equity_gap": -230.5 }
  },
  "risk": {
    "max_drawdown": -0.0878, "max_drawdown_duration_bars": 120,
    "recovery_bars": 88, "recovered": true,
    "worst_bar_return": -0.0412, "worst_month_return": -0.0731,
    "worst_trade": { "pnl": -203.61, "exit_time": "…", "direction": "long" },
    "max_consecutive_losses": 3, "downside_deviation": 0.061
  },
  "benchmark": {
    "label": "买入持有对照", "kind": "buy_and_hold", "source": "equity_curve_close",
    "fees_included": false,
    "window_matched": true, "bars_matched": 400,
    "total_return": 0.247, "cagr": 0.256, "annualized_volatility": 0.211,
    "sharpe": 1.21, "max_drawdown": -0.314, "final_equity": 12470.0,
    "excess_return": -0.228
  },
  "sample": { "trades": 18, "bars": 400, "years": 1.587,
              "tier": "preliminary", "tier_text": "18 笔交易：可以看，但还不足以证明稳定" },
  "caveats": [ { "code": "benchmark_excludes_fees", "message": "对照按收盘价买入持有，未计费用。" } ]
}
```

设计规则：

- **必须同步 `docs/12_API_SPEC.md`**：写成反引号包裹的 `` `GET /backtests/{run_id}/analysis` `` 并标 `[已实现]`，否则 `test_api_spec_truth.py:129/142/154/167` 红（这是本仓库唯一双向绑定的文档）。
- 错误码沿用既有习惯：`run` 不存在 → 404；`status != completed` 或 `result is None` → 409（参照 `backtests.py:128-166` 与 `experiment_service.py:767-923` 的 404/409 划分）；曲线为空 → 200 + 全 `null` + `caveats`（**不要 500**，尊重「样本不足就 N/A」的既有契约）。
- **不加** `POST`、不加写路径、不加 Celery 任务：计算量在请求内可忽略（§13）。
- 实验侧**不新增端点**：`ExperimentSummaryOut` 已带 `backtest_run_id`（`schemas.py:450-484`），前端直接调同一个回测分析端点（DRY）。
- `paper` 端点 `GET /paper/accounts/{id}/performance`（`paper.py:460-464`）**不动**（不属于 Phase C，且它已有自己的口径）。

---

## 12. 测试矩阵

### 12.1 正常

| 场景 | 断言 |
|---|---|
| 正收益策略 | performance/risk/benchmark 全部有值；`stored` 与 `metrics_json` 逐字相等 |
| 负收益策略 | 同上；`excess_return` 符号正确 |
| 盈亏交替 | `max_consecutive_losses`、`worst_trade` 正确 |
| **无交易策略** | 交易类全 `null` + `caveat`；`exposure`/`turnover` 仍是已存值；对照**照常给出**（这是「没交易 vs 有钱不赚」的关键场景） |
| 完整对照数据 | `benchmark.source == "equity_curve_close"`，`bars_matched == window.bars` |

### 12.2 边界

| 场景 | 期望 |
|---|---|
| 1 笔交易 | `win_rate` ∈ {0,1}；无亏损 → `profit_factor = null`；`sample.tier == "insufficient"` |
| 2 笔交易 | Sharpe 门槛（`MIN_SAMPLES_SHARPE=2`）边界；不得抛错 |
| 单日数据 / 极短曲线（len < 2） | 比率类 `null` + `caveat`；`total_return` 仍可给 |
| 空 equity curve | 全 `null` + `caveat`，**HTTP 200** |
| zero volatility（常数收益） | `annualized_volatility = 0`、`sharpe = null`（与 `metrics.py` 一致）、`sortino = null` |
| zero downside | `sortino = null` + 原因；`downside_deviation = 0` |
| `max_drawdown == 0` | `calmar = null`（不能出现 `inf`） |
| 100% 回撤（权益归零） | `max_drawdown = -1`；`recovery_bars = null`、`recovered = false`；`calmar = null` |
| NaN / Inf 注入 | 该字段 `null` + `caveat: non_finite_value`；**不得让 `inf` 或 `nan` 进入 JSON** |
| 负 initial capital / 0 | 比率类 `null`（对齐 `metrics.py:92-101` ADR-066 语义） |
| 未恢复的回撤 | `recovery_bars = null` + `recovered = false` |

### 12.3 Benchmark

| 场景 | 期望 |
|---|---|
| 完整数据（主路径） | 与策略**逐 bar 同 timestamp**；`bars_matched` 相等 |
| 曲线缺 `close`（legacy） | 走 `load_bars(run.dataset_version_id, start=首, end=尾, only_closed=True)`；`source == "series_bars"` |
| bars 为空 / provider 失败 | `benchmark = null` + `caveat: benchmark_unavailable`；**不得伪造** |
| 日期不一致（兜底路径 bar 数少于策略） | 按 timestamp 对齐；`window_matched=false` + `bars_matched` 实际值 + `caveat: benchmark_partial_window` |
| 无对照（explicit none） | `benchmark = null`；UI 显示「没有可比的对照」而不是 0 |
| 同 symbol 多 series | 断言取的是 `run.dataset_version_id`（构造两条 series，只有一条是 run 的） |
| crypto（7×24）| 与策略同 bar 集；`caveat: bars_per_year_252_for_crypto`（口径注，不是错误） |
| 股票（周末/假期缺口） | 主路径无缺口问题（同 bar 集），断言不引入任何补日 |

### 12.4 数据一致性（用户明确要求）

| 断言 | 方法 |
|---|---|
| 同一 Experiment：Backtest → Performance → Risk → Benchmark 一致 | 对同一 `run_id`：`analysis.performance.stored` **逐字等于** `metrics_json`；`analysis.result_hash == run.result_hash`；实验详情里的指标 == 分析端点里的同一指标 |
| 纯函数确定性 | 同一 `run_id` 连续两次请求 → 响应**逐字节相同**（序列化后比较）；排序/浮点格式不得依赖字典顺序 |
| 只读保证 | 调分析端点前后，`backtest_results` / `backtest_runs` / `experiment_results` 行数与内容不变（含 `updated_at`） |
| 版本失效 | 把 `ANALYSIS_VERSION` 改一位后重跑：输出可不同，但**不写库**；旧 `result_hash` 不变 |

### 12.5 守卫（新增能力必须同时满足）

- `docs/12_API_SPEC.md` 已登记 `[已实现]` 且路由真实存在（`test_api_spec_truth.py`）。
- 新指标标签在 `wording.ts` + `metrics.ts`/`NOT_A_METRIC` 双登记；单位只进 `format.ts`（`test_ui_promises.py` / `test_frontend_contracts.py`）。
- 若动了实验页展示：人工同步 `docs/13 §14`（无守卫，风险点）。
- `capabilities.py` 与真实实现一致（`AGENTS.md` 测试最低要求：每个「已支持」的指标真的能算出列）。
- AI 数字准入校验的负例测试（故意让模型吐一个不存在的数字 → 判定失败并降级）。

---

## 13. 性能与资源评估（个人 NAS，资源有限）

| 项 | 评估 |
|---|---|
| 指标计算 CPU | 全部是 `numpy` 上的 O(n) 向量运算（`np.diff`/`np.maximum.accumulate`/`np.std`）。500–2000 根 bar ⇒ **亚毫秒～几毫秒**；最贵的其实是 `json` 解析 `equity_curve_json`（数百 KB 文本），仍在**单请求 10 ms 量级** |
| 对照计算 | 主路径**零额外查询**：`close` 已在曲线里；对照的指标复用 `compute_metrics`（同量级） |
| 存储成本 | **零新增**（选项 C 不落库）。若做第二版缓存：每 run 约 1–3 KB JSON ⇒ 可忽略 |
| equity curve 存储成本（既有） | 约 100–150 B/点；400 根 ≈ 60 KB，2000 根 ≈ 240 KB；已存在，Phase C 不再增加 |
| 对照数据存储成本 | **零**（不落库、不建表） |
| Experiment 数量增长后的查询成本 | 分析端点按 `run_id` 单行 + 关联单行读取（`lazy="joined"` 的 `dataset` 已经在上游）；**列表页绝不做 N+1 分析**：列表只用已存 `metrics_json`，对照/风险只在详情与回测页取 |
| 并发/队列 | 同步请求内完成，**不引入 Celery 任务**；也不引入 Redis 缓存（bars 本来就没缓存，只有 AI 答案缓存） |
| 最坏情况 | 若有人对**每条实验**都点开分析：每次 1 次 run 读 + 1 次 result 读 + 十毫秒级计算；NAS 上是可接受的交互负载 |

---

## 14. 风险与兼容性

1. **冻结基线**：`metrics.py`、`engine.py` 的指标语义、`result_hash` 组成（`engine.py:524-537`）**一律不动**。新指标放独立命名空间 ⇒ 既不改变既有 run 的指纹，也不影响 `test_frontend_contracts.py:492-497` 等冻结断言。
2. **不可变性是朋友**：`immutability.py:78-80/114-116` 保证 `equity_curve_json` 不会被改 ⇒ 「需要时计算」天然可复现。**不要**为了性能去写回结果行（会撞触发器，也破坏可复现性）。
3. **一个事实一个出口（ADR-070/095）**：`exposure` / `turnover` 只读已存值；`total_return`/`sharpe` 等只读已存值；只有**新增**概念（calmar / recovery / worst_* / downside_dev / benchmark）由新模块负责，并在响应里标注 `derived`。
4. **文档守卫是硬约束**：`docs/12_API_SPEC.md` 双向绑定（`test_api_spec_truth.py:129/142/154/167`）；`docs/13_UI_UX.md` 的 `状态：` 行与欠账清单（`test_ui_promises.py:133-167/393-408`）。新增路由/UI 不同步文档 ⇒ CI 直接红。
5. **迁移纪律（若走第二版缓存）**：新迁移必须接在 **`0020_audit_log_action_width`** 之后（单 head，`test_migration_revisions.py:85-91`），revision id ≤ 32 字符（`:63-72`），`create_table` 的先后顺序受 `:154-183` 约束；**不得回改 0016/0019/0020**，`strategy_experiments` 的新列必须可空（`test_strategy_experiments_migration.py:14-18`）。
6. **悬空引用**：`ExperimentResult.backtest_run_id` **无 ondelete**（`models.py:506`，ADR-174 有意为之）⇒ 删 `BacktestRun` 不会清实验行。分析端点遇到「`backtest_run_id` 指向已删除 run」必须返回明确的 404/409，而不是空分析。
7. **`kind` 覆盖范围**：只有 `kind=backtest`（以及 `monte_carlo` 有 `backtest_run_id`）才有曲线可分析；`sensitivity`/`walk_forward`/`oos` 的 `backtest_run_id` 恒 `None` ⇒ 前端必须**只在有 `backtest_run_id` 时**显示分析入口（否则用户会看到 404）。
8. **命名与漂移**：中文用「对照」不用「基准」；`MarketDataSeries.__tablename__` 是 `market_data`；`BacktestRun.dataset_version_id` 存的是 series id；不要新建 `BacktestDetailOut` 之类的二次命名。
9. **AI 兼容**：AI 不可用时功能必须完整（数字块独立成立）；实现 calmar/对照后同步 `capabilities.py`，否则 AI 层的「能力清单」会对模型撒谎（`AGENTS.md` AI contract）。
10. **已知口径债（记录、本阶段不改）**：`BARRS_PER_YEAR["1d"] = 252` 对 crypto；`cagr` 按 bar 折算；`_safe()` 死代码（**已于 ADR-195 修掉，不再是债**）；回撤算法三份实现；`backtest_metrics` 只写不读；`docs/13:82` 的月度/年度视图并不存在。

---

## 15. 实施顺序（一次交付内的顺序，不是碎任务）

```
W1  backend/app/research/analysis.py（纯函数 + ANALYSIS_VERSION + caveats 语义）
      ↑ 依赖：无（只读已存证据）
W2  单测矩阵（§12 全部，含 NaN/Inf、空曲线、1 笔、zero vol、100% DD、确定性、只读保证）
W3  GET /backtests/{run_id}/analysis（薄路由，只做加载 + 组响应）
      + docs/12_API_SPEC.md 登记 [已实现]
W4  前端：回测页绩效/风险/对照块 + 对照曲线（MultiLineChart）+ 实验详情/列表两行
      + wording.ts / metrics.ts 登记 + docs/13 新节（含 状态： 行）
W5  AI：contracts 加 `## Task: performance_explanation` + capabilities 同步
      + runtime 调用 + 数字准入校验 + 无 AI 降级路径
W6  文档收口：docs/11「派生视图」说明、docs/17 ADR-187…（建议 2–3 条：
      「对照从已存曲线派生」「分析是纯函数、不落库」「AI 只解释结构化分析」）
```

依赖关系：W1 → W2/W3 → W4 → W5；W6 与 W4/W5 并行。**W5 可以最后做，且不做也不影响 W1–W4 的独立价值**（这是「AI 只是解释层」的架构红利）。

---

## 16. 明确「不做什么」

- ❌ 不引入新微服务 / 新数据库 / 新消息队列 / 新缓存系统 / 大数据框架（NAS 资源约束）。
- ❌ 不新建 `performance_*` / `risk_*` / `benchmark*` 表；不扩 `experiment_results`；不复活 `backtest_metrics`。
- ❌ 不重跑回测、不回填历史数据、不在读端点里写库。
- ❌ 不改 `metrics.py` 的既有语义、不改 `result_hash` 组成、不改 `engine.py` 的执行/成本模型。
- ❌ 不重新设计 Experiment（Phase B 冻结）；不引入第二个回测结果模型。
- ❌ 不做 VaR / CVaR（第一版）；不做 Beta / Alpha / Information Ratio / Tracking Error；不做集中度（单标的系统里是伪指标）；不做滚动窗口绩效；不做市场指数对照。
- ❌ 不新增 Celery 任务；不把分析做成异步。
- ❌ 不新增前端导航页（v1）；不在前端重算任何指标；不用 0 顶替缺失值。
- ❌ 不顺手修复本报告 §3 P1/P2 的技术债（回撤三份实现、`_safe` 死代码、`barss_per_year` crypto 口径、`backtest_metrics` 只写不读、文档漂移）——只记录。
- ❌ 不实现 AI 之外的任何「智能打分」；不引入第二个「稳健性打分」（现有唯一语义是 `sensitivity._is_stable`）。
- ❌ 不改 `version.txt`、不 commit / push / Release / 部署 NAS（本阶段）。

---

## 17. 最终推荐架构

```
                        ┌──────────────────────────────────────────────┐
不可变证据（已存在）      │ backtest_results.equity_curve_json (逐 bar)   │
                        │ backtest_results.metrics_json   (19 项)      │
                        │ backtest_trades                 (逐笔)        │
                        └───────────────┬──────────────────────────────┘
                                        │ 只读
                       ┌────────────────▼───────────────────────────────┐
                       │ backend/app/research/analysis.py                │
                       │   ANALYSIS_VERSION = "1.0.0"                    │
                       │   纯函数：drawdown/recovery/worst_*/calmar/      │
                       │           downside_dev/sample_tier              │
                       │   buy_and_hold_from_curve()  ← 零额外查询        │
                       │   指标一律复用 compute_metrics()                 │
                       │   出口：结构化 dict（stored/derived/caveats）     │
                       └────────────────┬───────────────────────────────┘
                                        │
        ┌───────────────────────────────┴────────────────────────────┐
        │ GET /api/v1/backtests/{run_id}/analysis   （只读，无写入）    │
        └───────┬───────────────────────────┬────────────────────────┘
                │                           │
   ┌────────────▼───────────┐    ┌──────────▼───────────────────────────┐
   │ 前端展示（不重算）       │    │ AI 解释（只解释）                     │
   │ 回测页绩效/风险/对照块   │    │ runtime.run_task(                    │
   │ MultiLineChart 叠加对照  │    │   task_type="performance_explanation")│
   │ 实验详情/列表两行        │    │  输入=事实包；输出=严格 schema        │
   │ 首页结论卡一句话         │    │  数字准入校验；无 AI 也能用           │
   └────────────────────────┘    └──────────────────────────────────────┘
        存储：新增 0 张表 / 0 列 / 0 个任务
```

**架构要点一句话**：**证据不可变 + 分析是纯函数 + 只有一条只读出口 + AI 只做翻译**。

---

# Phase C 最小完整交付范围

> 目标：**一次完整开发、一次完整测试、一次完整验收**即可关闭 Phase C。不做碎任务拆分。

### 交付物（必须全含，缺一不可验收）

| # | 交付物 | 范围 |
|---|---|---|
| 1 | `backend/app/research/analysis.py` | `ANALYSIS_VERSION`；drawdown 序列/最长回撤持续/恢复期；worst bar / worst month / worst trade；连亏；downside deviation；calmar；样本可靠性 tier；`caveats`（机检 code + 人话）；NaN/Inf 兜底 |
| 2 | 买入持有对照 | 从已存曲线 `close` 派生（主路径），`load_bars(run.dataset_version_id, …)` 兜底；**同窗口、同初始资金、逐 bar 同索引**；6 项对照指标复用 `compute_metrics`；`fees_included=false` 明示 |
| 3 | 一个只读 API | `GET /api/v1/backtests/{run_id}/analysis`（§11 形状；404/409/200+null 语义；**零写入**）+ `docs/12_API_SPEC.md` 的 `[已实现]` 行 |
| 4 | 前端展示 | 回测页绩效/风险/对照块 + 对照曲线（`MultiLineChart`，普通模式可见）；实验详情两行 + 列表两列（仅 `kind=backtest`）；首页一句话；标签双登记；**不新增导航页**；`docs/13_UI_UX.md` 新节带 `状态：` |
| 5 | AI 解释 | `contracts` 新 `## Task: performance_explanation`；`run_task()` 调用；严格 schema；**数字准入校验**；无 AI 降级；`capabilities.py` 同步 |
| 6 | 测试 | §12 全矩阵（正常/边界/对照/一致性/确定性/只读/守卫负例）；后端全量 + ruff；前端 typecheck + build |
| 7 | 文档 | `docs/11`（派生视图说明 + 命名漂移修正）、`docs/13`、`docs/12`、`docs/17` ADR-187…（2–3 条） |

### 明确不含（本阶段范围外）

- VaR / CVaR、Beta / Alpha / IR / Tracking Error、集中度、滚动绩效、市场指数对照、月度/年度完整收益表；
- 对 `sensitivity` / `walk_forward` / `oos` 型实验做分析（它们没有权益曲线）；
- 任何落库/缓存/异步/新表/新迁移；
- §3 的技术债修复（只记录）；
- 版本号与发布动作（`version.txt` 当前仍是 `v2.4.6`、Phase A+B 仍是 `v2.5.0-rc.3` 预发布 —— 何时正式发 `v2.5.0`、Phase C 用哪个版本号，由用户决定）。

### 验收标准（一次过或不过）

1. 后端全量测试绿 + ruff clean；前端 typecheck + build 绿；文档守卫（`test_api_spec_truth` / `test_ui_promises` / `test_frontend_contracts`）绿。
2. 浏览器端真实 E2E：打开一条 NAS 上**已存在**的回测（例如 run 13）→ 看到绩效/风险/对照三块；**对照收益与策略收益同时可见且标注「未计费用」**；刷新两次数值逐字节一致。
3. 同一实验在「回测页 / 实验详情 / 首页」三处显示的同一指标**完全一致**（服务端唯一出口）。
4. 边界 E2E：无交易的那条 run → 交易类指标显示「未知」而不是 0；对照仍显示；无 5xx、无 console error。
5. AI 解释：能生成一段四问式解释；**人为篡改一个数字后校验失败并被拦截**；关掉 AI provider 时页面数字块仍完整。
6. 只读性：整个验收过程不产生任何写入（除既有页面操作外），`result_hash` 不变。

---

**合规声明**：本报告为只读审计与设计输出。未创建或修改任何文件（含文档），未写业务代码，未改 schema / migration / API / 前端 / Phase A+B 逻辑，未改 `version.txt`，未 commit / push / Release，未部署或操作 NAS，未修复任何已发现问题（§3 全部仅记录）。

> **落盘说明（补充，不改动上述报告内容）**：以上「未创建或修改任何文件」描述的是 Phase C 的**只读审计与设计阶段**本身。本次落盘是该阶段唯一的产物——**除本文件外未创建或修改任何文件**，`version.txt` 未改，未 commit / push / Release，未部署 NAS。
