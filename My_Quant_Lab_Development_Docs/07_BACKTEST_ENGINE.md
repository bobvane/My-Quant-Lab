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

## 13. 禁止的“回测作弊”

- 未来价格参与当前信号
- 用最终数据反推历史参数
- 无视手续费/滑点却宣称真实收益
- 删除失败交易
- 只展示最优参数而不展示参数稳定性
- 自动覆盖旧 backtest result
