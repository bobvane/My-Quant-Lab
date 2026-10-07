# 07 Backtest Engine

## 1. 核心原则

回测引擎必须是确定性的。相同：

- strategy version
- dataset snapshot
- parameters
- execution model

必须得到相同的结果（允许极小浮点误差）。

## 2. 时间推进

默认按 closed bar 推进：

```text
Bar t closes
→ evaluate signal from information up to t
→ order becomes eligible
→ default fill at bar t+1 open
```

不得读取未来 K 线数据。

这条口径对进场和出场是同一条（ADR-116）：bar t 收盘成立的**出场**规则也只在 bar t+1
开盘成交，并同样计入滑点。建仓路径一直是这么做的，出场曾经是例外（用决策 bar 自己的
收盘价成交），现在两边一致。

bar t 之内能平仓的只有 bar t 之前就已经存在的价位。止损/止盈线是策略用收盘价推出来的
（`close - n * atr`），所以引擎在一根 bar 上测试的是**上一根 bar 的值**；拿本根的 close
算本根的止损，等于让 bar t 先知道自己的收盘再决定在哪止损 —— 这是本引擎曾经犯过的错，
现在由 `backend/tests/test_fill_timing.py` 钉住。出场信号因此分两种时序：止损/止盈的
`bar_time` 就是成交那根 bar（价位本来就存在）；规则出场记下决策的 `bar_time`，再补上
`fill_time` / `fill_price`（t+1 开盘那一根）。

## 3. Lookahead 防护

测试必须覆盖：

- rolling window 正确 shift
- target/label 不回流到 features
- current forming bar 禁止作为已知数据
- indicator warmup 区间处理
- corporate action timing

建议为 FeatureFrame 增加 `available_at` / `computed_from_bar` 元数据。

## 4. 成交模型

默认：

- next_bar_open
- fee_bps configurable
- slippage_bps configurable
- fractional shares optional

Stop/Take Profit 在同一根 K 线同时触发时，V1 使用保守规则并明确标记 ambiguous fill；不能选择对结果更有利的顺序。

止损与止盈的价位只在它们**已经存在**的那根 bar 上生效：bar i 的 high/low 与 bar i−1 收盘后
算出的那条线比较（见 §2），所以建仓那根 bar 只受入场决策冻结的那条线约束。

成交语义变化时 `ENGINE_VERSION` 必须提升（ADR-116 把 1.0.0 提到 1.1.0），并且 API 与
`BacktestRun` 记录的是真正跑过的版本号 —— 不是写死的字符串，因为 `result_hash` 里就钉着
这个版本号，回显一个别的值等于把两份不同的计算说成同一份。

## 5. 订单类型

V1：

- Market at next bar open
- Stop loss
- Take profit

P1：Limit、Stop entry。

## 6. 市场时段

股票策略必须知道：

- timezone
- trading session
- holiday/gap

加密资产支持 24/7。

## 7. 费用与滑点

每个 backtest run 必须保存：

- fee model
- fee rate
- slippage model
- slippage rate
- currency

## 8. 输出指标

基础：

- initial capital
- final equity
- total return
- CAGR（适用时）
- number of trades
- win rate
- average win/loss
- profit factor
- max drawdown
- average holding period
- exposure
- turnover

风险调整：

- Sharpe
- Sortino

指标缺少足够样本时应显示 `N/A`，不得强行计算。

## 9. 交易级记录

每笔交易：

- entry timestamp
- entry price
- exit timestamp
- exit price
- quantity
- fees
- slippage
- reason
- MAE
- MFE
- R multiple
- strategy version

## 10. Walk-forward

至少支持：

```text
Train window
→ Test window
→ roll forward
→ repeat
```

报告每段 test period 的结果，而不是只展示总结果。

## 11. OOS

用户可以指定最后 N% 或指定日期为 OOS。参数优化只能使用 train 区间。

## 12. Golden Tests

内置小型固定 OHLCV fixtures，验证：

- EMA
- ATR
- breakout
- stop loss
- take profit
- fees/slippage
- no-lookahead
- portfolio accounting

## 13. 禁止的"回测作弊"

- 未来价格参与当前信号
- 用最终数据反推历史参数
- 无视手续费/滑点却宣称真实收益
- 删除失败交易
- 只展示最优参数而不展示参数稳定性
- 自动覆盖旧 backtest result

## 14. 运行状态与异步执行（ADR-180）

一次回测在库里就是一行 `backtest_runs`，它同时是**输入**（策略版本、数据集、参数、已解析的执行
模型都从这一行读回）和**锁**（已经到终局的行不再被重跑，重复投递只浪费一次 CPU）。

`status` 之外还有两个字段描述它正在做什么：`progress`（整数 0–100）与 `current_step`，刻度写在
`backend/app/data/backtest_service.py` 的 `PROGRESS_LADDER`：

```text
loading data       5
computing features 20
running strategy   45
evaluating exits   70
computing metrics  90
completed          100
```

`progress` 是给界面看的粗刻度，不是精确百分比。引擎当前是一趟调用（ADR-174），可观测的跳变只有
`computing features` → `computing metrics`；`running strategy` 与 `evaluating exits` 是引擎自己的
阶段、没有可提交的边界，所以没有为它们编造百分比。运行行在请求内**创建并提交**（`progress = 0`、
`current_step = "loading data"`），所以「它在跑」从落库那一刻起就是一个可轮询的事实。

`progress` 与 `current_step` **不进** `result_hash`：哈希只覆盖策略版本、数据集、引擎版本、
特征版本、参数、指标与交易数；把运行过程元数据算进去会让同策略、同数据、同参数的重跑得到不同哈希，
ADR-081 的「数据集 + 哈希即可复现」随即失效。

两条执行路径由设置 `BACKTEST_ASYNC`（环境变量也接受 `MQL_BACKTEST_ASYNC` 拼写，默认 `false`）选择：

- `false`（默认）：请求内同步跑完（`prepare_backtest` → `execute_backtest`），响应直接带上结果；
  `store_backtest` 保留为「同步跑完」的组合入口，对外行为不变。
- `true`：请求内只 `prepare_backtest`（校验 + 落行）并入队 Celery 任务
  `quantlab.run_backtest`（`backend/app/workers/tasks.py`）；worker 用 `rebuild_backtest_inputs`
  从行里重建输入，再 `execute_backtest`。没有 worker 的部署与测试继续用同步路径。

失败就是失败：`status = "failed"`、`error_message` 是引擎给出的**逐字**原因，不留半截 result；
终局的行被重复投递时原样保留。本轮**不实现取消**。
