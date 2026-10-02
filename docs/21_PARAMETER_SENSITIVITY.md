# 21 参数敏感性分析（Parameter Sensitivity）

> 状态：v1.1 已实现（后端 + API）。决策见 `docs/17_DECISIONS.md` ADR-040 / ADR-041；
> 未测得点不参与排名见 ADR-055（v1.4.5，`sensitivity_version` `1.0.0` → `1.1.0`）。

## 1. 它回答什么问题

单次回测只告诉你「在这组参数下发生过什么」。研究时真正要问的是：

- 这组参数**附近**表现如何？还是只有这一个点好看？
- 换一组参数，结论是**平移**还是**翻转**（由赚变亏）？
- 我是在看一个稳健的策略，还是在看一条拟合出来的曲线？

参数敏感性分析把策略**已声明的参数**扫成一整片网格，逐点独立回测，把整片曲面的形状呈现出来。

## 2. 它不是优化器

这是一个刻意的边界（ADR-041）：

- 报告**不给**「应该用哪组参数」的建议。
- `best` / `worst` 只是按目标指标排序的结果，用于定位曲面两端，**不是推荐**。
- AI 层不得改写、生成或解释这些数字（`docs/02 §3`：所有指标由确定性引擎计算）。

换句话说：工具帮你**看见**邻域，判断仍然由你做出。

## 3. 前置条件：参数必须真正生效

本功能依赖 ADR-040 的修复。在此之前 `run_backtest` 的 `parameters` 参数被折进结果哈希却**从未应用**到特征引擎，于是：

- 扫一整片网格 → 每个点得到**完全相同**的结果；
- 报告会给出 `stdev = 0`、`stable = true`，看起来像一个"极其稳健"的策略。

这正好是敏感性分析最危险的一种错误：**把一个 bug 报告成一种优点**。因此本功能与 ADR-040 必须同时存在。

参数要能被扫，策略必须用 `period_ref` 声明：

```json
{
  "parameters": { "trend_period": 20 },
  "indicators": [
    { "id": "trend", "type": "EMA", "period_ref": "trend_period" }
  ],
  "entry": { "long": { "all": [{ "op": "crosses_above", "left": "close", "right": "trend" }] } }
}
```

> 注意：`indicators[].id` 是特征引擎物化出的**列名**（上例为 `trend`），规则里的
> `left`/`right` 必须引用这个列名，而不是 `period_ref` 的键名。

## 4. API

```http
POST /api/v1/research/sensitivity
```

```json
{
  "strategy_version_id": 8,
  "symbol": "AAPL",
  "timeframe": "1d",
  "grid": { "trend_period": [5, 10, 20, 40, 60] },
  "metric": "sharpe"
}
```

响应（节选）：

```json
{
  "sensitivity_version": "1.1.0",
  "metric": "sharpe",
  "axes": { "trend_period": [5, 10, 20, 40, 60] },
  "grid_points": 5,
  "evaluated_points": 5,
  "ranked_points": 5,
  "warmup_unmet_points": 0,
  "warnings": [],
  "points": [
    { "parameters": { "trend_period": 5 },
      "metrics": { "total_return": 0.12, "sharpe": 0.8, "...": "..." },
      "objective": 0.8,
      "result_hash": "…",
      "warnings": [],
      "warmup_unmet": false }
  ],
  "summary": {
    "mean": 0.61, "median": 0.7, "stdev": 0.24,
    "min": 0.2, "max": 0.9, "range": 0.7, "positive_ratio": 1.0
  },
  "best":  { "parameters": { "trend_period": 20 }, "objective": 0.9, "result_hash": "…" },
  "worst": { "parameters": { "trend_period": 5 },  "objective": 0.2, "result_hash": "…" },
  "stable": true
}
```

### 多轴

```json
"grid": { "fast": [5, 10], "slow": [20, 30, 40] }
```

网格大小 = 各轴长度之积（上例 6 点）。

## 5. 约束

| 约束 | 行为 |
|---|---|
| 网格轴未在策略中声明 | `422`，并列出策略实际声明的参数 |
| 网格点数 > `144` | `422`（一次扫描 = 点数 × 一次回测的成本） |
| `metric` 不在白名单 | `422` |
| `grid` 为空 | `422`（schema 层 `min_length=1`） |
| 目标指标未定义（样本不足 / 无交易） | 该点保留、`objective = null`，从排序与统计中剔除 |
| 整段落在预热期内 | 该点保留、`objective = 0`、`warmup_unmet = true`，从排序与统计中剔除，并在 `warnings` 里说明 |

可用的目标指标（`TRACKED_METRICS`）：
`total_return`、`cagr`、`sharpe`、`sortino`、`max_drawdown`、`win_rate`、
`profit_factor`、`expectancy`、`number_of_trades`、`exposure`。

## 6. 没跑起来的点不能赢排名

`run_backtest` 在**整段数据都落在指标预热期内**时会发一条警告（`docs/17` ADR-054），
此时策略一根可评估的 bar 都没有，决策全是 `False`，于是指标是**扁平的一串 0**。

问题在于 0 看起来是个好数字。把这样的点放进排序，结果是：

- `best` 变成「没跑起来」的那个点——只要其它点都在亏钱，**0 就永远赢**；
- `summary` 的均值、极差、`positive_ratio` 被 0 稀释；
- `stable` 把「全网格一致亏损」误报成「符号翻转」（因为 0 既不正也不负）。

所以聚合时把它们分出来（ADR-055）：

| 字段 | 含义 |
|---|---|
| `points[].warmup_unmet` | 该点整段在预热期内，上面的指标不是测量结果 |
| `ranked_points` | **真正参与**排序 / `summary` / `stable` 的点数 |
| `warmup_unmet_points` | 被排除的点数 |
| `warnings` | 非零时说明有多少点被排除、以及是不是一个点都没测到 |

`evaluated_points` 仍然包含这些点（它们的 `objective` 是已定义的 0，不是 `null`），
所以 `ranked_points + 未定义点数 + warmup_unmet_points == grid_points`。
`best` / `worst` 全都取自 `ranked_points`；一个点都没测到时两者都是 `null`。

前端把这类点在表格里标为「整段在预热期内」、指标显示 `—`，并且**不把它们画进图里**——
一条躺在 0 上的「曲线」看起来像是被测量过的。

## 7. `stable` 的含义

`stable` 为 `true` 当且仅当**所有参与排名的点**的目标指标同号（全为正或全为负）。

这是一个朴素但诚实的判据：它回答"整个邻域是同向的，还是符号会随参数翻转"，
而不去做样本量支撑不了的显著性检验。`stable` 为 `null` 表示没有可评估的点。

> `stable = true` 只说明**符号一致**，不说明策略好。例如全网格都稳定亏损也是 `stable = true`。
> 判断好坏要看 `summary` 与 `points` 里的具体指标，而不是这一个布尔值。

## 8. 可复现性

- 每个点回报自己的 `result_hash`，覆盖「策略版本 + 数据集 + **有效参数** + 引擎版本 + 特征版本」。
- 网格按 `grid` 的插入顺序与各轴取值顺序展开，同一请求永远以同一顺序遍历（`expand_grid`）。
- 相同 spec + 数据集 + 网格 → 相同的完整报告（`test_sweep_result_is_reproducible`）。
- 每次扫描写审计事件 `sensitivity_completed`，含网格轴与统计摘要。

## 9. 实现位置

| 位置 | 作用 |
|---|---|
| `backend/app/research/sensitivity.py` | 网格展开、逐点回测、统计与稳健性判定、未测得点分拣（ADR-055） |
| `backend/app/research/engine.py` | `resolve_parameters`（ADR-040 的唯一参数合并入口）、`warmup_unmet` 标记 |
| `backend/app/api/routers/research.py` | `POST /research/sensitivity` + 审计 |
| `backend/tests/test_sensitivity.py` | 模块级：网格、统计、边界、可复现性 |
| `backend/tests/test_sensitivity_api.py` | 端点级：契约、422、404、审计、未测得点不参与排名 |
| `frontend/src/views/BacktestView.vue` | 结果面板：`ranked_points` 卡片、`warnings`、表格标记、图表过滤 |
| `frontend/src/components/SensitivityChart.vue` | 单轴折线 / 双轴热力图（只画测得点） |
