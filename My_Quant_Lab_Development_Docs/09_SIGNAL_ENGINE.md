# 09 Signal Engine

## 1. 输出状态

V1：
- BUY
- SELL
- WAIT
- NO_SIGNAL

其中：
- BUY/SELL = 策略规则已经满足。
- WAIT = 有研究对象，但进入条件尚未确认或存在等待条件。
- NO_SIGNAL = 当前没有候选事件。

## 2. 信号生成

```text
scheduled scan
→ choose closed bars
→ compute features
→ execute strategies
→ validate portfolio context
→ de-duplicate
→ persist signal
→ AI explain
→ notify
```

## 3. Portfolio Context

从 Ghostfolio 获取：
- current quantity
- cost basis / investment context where available
- portfolio weight
- account/asset information

用途是解释和风险提示，不是偷偷修改策略历史规则。

例如：

```text
Strategy says BUY
Portfolio context:
Current NVDA weight = 18%
Policy max reference = 20%
→ Signal remains BUY but UI adds concentration warning
```

是否阻止信号应由用户配置的 risk guardrail 决定。

## 4. 去重

同一：
- symbol
- timeframe
- strategy version
- bar timestamp

默认只允许一个 primary signal event。

重复运行 worker 不能产生重复通知。

## 5. Signal Evidence

必须记录：
- exact strategy version
- exact bar timestamp
- rule IDs triggered
- input feature snapshot hash
- market data provider
- portfolio context snapshot

## 6. AI explanation

AI 只能基于 Signal Evidence 解释；不能重新计算或“改判”。

## 7. Alert noise control

用户可以设置：
- only BUY/SELL
- include WAIT
- quiet hours
- daily max notifications
- minimum signal cooldown

系统应避免每根 K 线重复提醒相同信号。

## 8. Strategy evidence layers

建议 UI 同时展示四层：

1. Rule Match：当前规则满足情况
2. Empirical Stats：历史统计
3. Recent Paper Stats：模拟盘情况
4. Portfolio Context：与用户当前真实组合的关系

“AI explanation”作为第五层，只负责解释以上数据。
