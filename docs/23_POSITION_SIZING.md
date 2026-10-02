# 23 仓位管理（Position Sizing）

> 状态：v1.2 已实现。决策见 `docs/17_DECISIONS.md` ADR-045。

## 1. 它解决什么问题

在此之前引擎只有一种下注方式：**把当前现金的固定比例投进去**（`risk.max_position_pct`）。
这带来一个结构性缺陷：止损放宽一倍，单笔亏损就跟着放大一倍 —— **止损距离与风险脱钩**。

风险型仓位管理反过来：先决定「这一笔最多亏多少」，再据此反推能买多少。
止损越近，买得越多；止损越远，买得越少。每笔交易的风险保持大致恒定。

## 2. 三种模式

写在 `execution.sizing` 里：

```json
"execution": {
  "initial_capital": 10000,
  "sizing": { "mode": "risk_per_trade", "risk_pct": 0.01 }
}
```

| `mode` | 含义 | 用到的字段 |
|---|---|---|
| `fixed_fraction` | **默认**，与历史行为完全一致：投入当前现金的固定比例 | `fraction`（可选，覆盖 `max_position_pct`） |
| `risk_per_trade` | 按「入场价到止损价的距离」反推数量，使单笔亏损 ≈ 权益 × `risk_pct` | `risk_pct` |
| `atr_risk` | 同上，风险距离取 ATR 口径 | `risk_pct`、`atr_multiple` |

### 计算方式

```text
fixed_fraction :  qty = cash × min(fraction ?? max_position_pct, 1) / fill
risk_per_trade :  qty = cash × risk_pct / |fill − stop|
atr_risk       :  qty = cash × risk_pct / (atr_multiple × ATR)
```

三种模式都会再受同一个上限约束：

```text
qty ≤ cash × 0.999 / fill
```

这是一条**研究引擎而非保证金账户**的硬边界 —— 无论风险参数多激进，名义敞口都不可能超过可用现金。

### 缺失止损时的行为

若该笔没有可用的止损距离（策略未定义止损、或 ATR 为 NaN），风险型模式**回退**为
`fixed_fraction`，而**不是跳过这笔交易**。理由：静默不交易会让回测结果变得无法解释
（「为什么这笔没做？」），而回退是可见且可复现的。

## 3. 兼容性

`fixed_fraction` 是默认值，**既有策略版本的数字完全不变**：
`SizingSpec` 只新增字段，不改动原有路径的算术。回归由既有的
`test_backtest.py` / `test_examples.py` / `test_sensitivity.py` 全绿保证，
并有 `test_default_sizing_is_fixed_fraction` 显式断言「默认 = 显式 fixed_fraction」。

> 注意：`execution_model_json` 现在会包含 `sizing`，因此**同一策略在升级后重新回测**，
> `result_hash` 会变化。这是预期行为 —— 执行模型是哈希的一部分，而它确实变了。
> 历史回测记录不受影响（它们保存的是当时的快照）。

## 4. 写在 DSL 里 vs 用 `execution_overrides`

- 写进策略 DSL：成为该策略版本的一部分（受不可变保护），适合「这个策略就该这样下注」。
- 用 `POST /backtests` 的 `execution_overrides`：一次性试验，不改策略版本。

```json
{
  "strategy_version_id": 12,
  "symbol": "AAPL",
  "execution_overrides": { "sizing": { "mode": "risk_per_trade", "risk_pct": 0.02 } }
}
```

## 5. 验证要点

测试（`backend/tests/test_sizing.py`）分两层：

- **单元层**直接驱动 `_position_quantity`，风险算术是精确可知的
  （例如 10,000 本金、1% 风险、5 元止损距离 → 恰好 20 单位，止损亏 100）。
- **集成层**跑真实回测断言**文档承诺的意图**：
  - 止损距离减半 → 建仓数量变大；
  - **且**止损出场的亏损仍大致等于风险预算（这一条才排除「买得多是因为亏得更多」）；
  - 默认模式与显式 `fixed_fraction` 结果完全一致；
  - 风险参数非法（`risk_pct ≤ 0`、`> 1`）被拒绝。

## 6. 把风险预算也扫一遍（参数敏感性分析）

`POST /research/sensitivity` 的 `grid` 除了策略参数，还接受一个**执行轴**：

```json
"grid": { "fast_period": [5, 10, 20], "risk_pct": [0.005, 0.01, 0.02] }
```

- 目前只有 `risk_pct` 是允许的执行轴（`EXECUTION_AXES`），它落在 `execution.sizing`
  而不是 `parameters`，所以不属于 `period_ref` 体系。
- 扫 `risk_pct` 时，若策略本身不是 `risk_per_trade`，该点会**按 `risk_per_trade`
  计算**（否则 `risk_pct` 对固定比例模式毫无作用，扫出来的会是一排相同的点）。
- 每次扫描**最多一个**执行轴；其他 `sizing` 字段（如 `atr_multiple`）会被当作未声明的
  参数拒绝，而不是静默忽略。
- 未知轴的报错会一并列出可用的执行轴，便于发现拼写错误。

这样就能同时回答两类问题：「入场参数变一点结论会不会翻转」与「换个风险预算结论会不会翻转」。

## 7. 实现位置

| 位置 | 作用 |
|---|---|
| `backend/app/strategies/dsl.py` | `SizingSpec` / `ExecutionSpec.sizing` / `merge_spec_overrides` |
| `backend/app/research/engine.py` | `_position_quantity`（唯一的定量入口，market 与挂单两条路径共用） |
| `backend/app/research/sensitivity.py` | `EXECUTION_AXES`：把 `risk_pct` 接到网格扫描 |
| `backend/tests/test_sizing.py` | 单元 + 集成测试 |
| `backend/tests/test_sensitivity.py` | 执行轴扫描、未知轴拒绝、组合扫描 |
