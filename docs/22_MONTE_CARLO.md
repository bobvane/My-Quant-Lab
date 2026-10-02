# 22 Monte Carlo 重采样（Monte Carlo Resampling）

> 状态：v1.2 已实现（后端 + API + 前端）。决策见 `docs/17_DECISIONS.md` ADR-043。

## 1. 它回答什么问题

一次回测只是**一条**历史路径。它告诉你「发生了什么」，不告诉你「还可能发生什么」。
报告里那个 +12% 是唯一的一次抽样，不是这个策略的期望值。

Monte Carlo 重采样把该策略**自己已实现的交易**反复重新抽样，构造出一整片结果分布：

- 同样的 +12%，如果重采样路径分布在 **-20% … +60%**，和分布在 **+8% … +16%**，
  是完全不同的两件事。
- 同一批交易**换个顺序**就可能回撤深得多 —— 这正是单次回测结构上看不到的东西。

## 2. 它不是预测

这是一个刻意的边界（ADR-043）：

- 它是对**历史的再抽样**，不是对未来的预测，也不是价格模型。
- 响应里带 `method` 字段（`trade_level_iid_bootstrap`），消费方无法把它误当成预测。
- 它只用该策略**自己已实现的交易**，不引入任何外部假设或生成的收益。
- AI 层不得生成或改写这些数字（`docs/02 §3`）。

## 3. 方法

1. 取出一次已完成回测的交易 `pnl` 列表与初始资金。
2. 从该列表中**有放回**地抽取 `trades_per_run` 笔（默认等于观测笔数）。
3. 复利推进权益：一笔 `+100` 的交易在 10,000 本金上视为 **+1%**，
   于是重采样路径就是这些收益率按**抽到的顺序**做累乘。
4. 记录每条路径的最终收益、最大回撤、Sharpe / Sortino。
5. 汇总分位数、盈利概率、清零概率与尾部风险。

### 明确的假设（报告不会掩盖）

| 假设 | 含义 |
|---|---|
| 交易**独立同分布** | 真实交易存在自相关（制度切换、波动率聚集），因此本方法会**低估**连续亏损的概率 |
| 只重排交易顺序 | 这正是让回撤离散度可见的原因：同样的交易换个顺序，回撤可以差很多 |
| 只用已实现的交易 | 只交易过 2 次的策略，其分布就是基于 2 个观测构建的 |

第三条不会静默发生：观测笔数少于 `MIN_TRADES_FOR_CONFIDENCE`（20）时，
响应 `warnings` 会明确写出「distribution is built from a very small sample」。

## 4. API

```http
POST /api/v1/research/monte-carlo
```

```json
{
  "backtest_run_id": 12,
  "runs": 1000,
  "seed": 0,
  "trades_per_run": 60
}
```

- `backtest_run_id`：**已存储**的回测运行 id。本接口**不会**重跑回测，
  它读该次运行已落库的交易，因此分布锚定在被考察的那份结果上。
- `runs`：1–5000（`MAX_RUNS`）。每次请求 = `runs` × `trades_per_run` 的抽样，
  上限用于防止一次误操作占满 NAS 的 worker。
- `seed`：相同 seed → 完全相同的分布（可复现）。
- `trades_per_run`：可选；不传则等于观测到的交易笔数（保持与历史相同的交易次数）。

响应（节选）：

```json
{
  "monte_carlo_version": "1.0.0",
  "seed": 0,
  "timeframe": "1d",
  "method": "trade_level_iid_bootstrap",
  "summary": {
    "runs": 1000,
    "observed_trades": 60,
    "trades_per_run": 60,
    "initial_capital": 10000.0,
    "final_equity":     { "p5": 9120.0, "p25": 9980.0, "p50": 10810.0, "p75": 11720.0, "p95": 12940.0 },
    "total_return":     { "p5": -0.088, "p25": -0.002, "p50": 0.081, "p75": 0.172, "p95": 0.294 },
    "max_drawdown":     { "p5": -0.201, "p25": -0.121, "p50": -0.083, "p75": -0.051, "p95": -0.019 },
    "sharpe":           { "p5": -0.9, "p25": 0.1, "p50": 0.8, "p75": 1.5, "p95": 2.4 },
    "sortino":          { "...": "..." },
    "probability_of_profit": 0.61,
    "probability_of_loss": 0.39,
    "probability_of_ruin": 0.0,
    "expected_total_return": 0.086,
    "expected_max_drawdown": -0.091,
    "worst_max_drawdown": -0.31
  },
  "sample_equity_paths": [[10000.0, 10042.0, "..."]],
  "warnings": []
}
```

### 关于概率的含义

`probability_of_profit` 等是**本次模拟中路径的频率**，不是对未来的预测。
`probability_of_ruin` 指权益跌到 ≤ 0 的路径占比。

`sample_equity_paths` 最多返回 100 条路径（供前端画扇形图），
不是全部 `runs` 条 —— 全量返回只会让响应膨胀而没有额外视觉价值。

## 5. 约束

| 情况 | 行为 |
|---|---|
| 回测运行不存在 | `404` |
| 回测状态不是 `completed` | `422` |
| 该次回测没有已成交交易 | `422` |
| 运行未记录正的 `initial_capital` | `422` |
| `runs` 超出 1–5000 | `422` |
| `trades_per_run` < 1 | `422` |

年化（Sharpe / Sortino）使用的周期取自该次回测实际使用的数据集周期
（`MarketDataSeries.timeframe`），不由调用方声明 —— `BacktestRun` 本身没有
timeframe 列，让调用方传入只会让数字与实际不一致。

## 6. 可复现性

- 相同 `backtest_run_id + runs + seed + trades_per_run` → 完全相同的响应。
- 单条路径的复利是确定性的；随机性只来自 `numpy.random.default_rng(seed)`。
- 每次运行写审计事件 `monte_carlo_completed`（含 runs / seed / method / timeframe）。

> 注意：观测笔数极少时（例如 2 笔），不同 seed 的**分位数摘要**可能恰好相同
> —— 因为可选结果本来就很少。这是样本量的必然结果，`warnings` 会提示样本过小。

## 7. 实现位置

| 位置 | 作用 |
|---|---|
| `backend/app/research/monte_carlo.py` | 重采样、复利路径、分位数与概率 |
| `backend/app/api/routers/research.py` | `POST /research/monte-carlo` + 审计 |
| `backend/tests/test_monte_carlo.py` | 模块级：复利、清零、离散度、边界、可复现性 |
| `backend/tests/test_monte_carlo_api.py` | 端点级：契约、422/404、审计、可复现性 |
| `frontend/src/components/MonteCarloChart.vue` | 扇形图（样本路径 + p5/p50/p95） |
