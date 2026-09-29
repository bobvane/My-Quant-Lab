# 08 Paper Trading / 模拟盘

## 1. 目标

用虚拟资金验证“系统实际生成的信号”在现实时间中的表现，不污染 Ghostfolio 真实资产。

## 2. Paper Account

字段：
- id
- name
- base_currency
- initial_cash
- cash
- equity
- created_at
- status
- strategy_set
- fee_model
- slippage_model

示例账户：
- PA Strategy
- Technical Strategy
- AI Combined

## 3. 账户隔离

Paper Account 不能写入 Ghostfolio activities。

## 4. 实时模拟流程

```text
market data update
→ closed bar check
→ strategy scan
→ order intent
→ paper fill model
→ portfolio accounting
→ signal lifecycle update
→ metrics update
```

## 5. 模拟成交

默认使用真实当时可获得的数据，并模拟：
- 下一根 K 线开盘成交
- fee
- slippage
- market hours

## 6. 禁止未来信息

Paper engine 使用实时数据时，只能使用 signal timestamp 之前已经闭合的数据。

## 7. Signal lifecycle

```text
GENERATED
→ NOTIFIED
→ ENTRY_PENDING
→ ENTERED
→ OPEN
→ EXIT_PENDING
→ CLOSED
```

也允许：
- EXPIRED
- INVALIDATED
- CANCELLED

## 8. Outcome Tracking

每个 signal 必须能够在事后回答：

> “如果当时按系统规则执行，后来发生了什么？”

保存：
- first touched entry
- max favorable excursion
- max adverse excursion
- exit reason
- P/L
- duration
- fees/slippage

## 9. Counterfactual

可提供“如果过去跟随这个策略”模拟，但必须明确标记为 hypothetical，不得改变真实 Ghostfolio 数据。
