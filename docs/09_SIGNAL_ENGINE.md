# 09 Signal Engine

## 1. 输出状态

V1：
- BUY
- SELL
- WAIT
- NO_SIGNAL

其中：
- BUY = 买入规则在最新已收盘 K 线上成立。这是**电平**语义：成立期间每根 K 线都会再次报告，因为「规则已满足」本身就是可观察状态。
- SELL = 卖出指令在该根 K 线上**第一次**成立。这是**事件**语义：成立期间不重复报告（否则两个月的下跌会重复推 60 条平仓指令）。
- WAIT = 有研究对象，但进入条件尚未确认或存在等待条件。
- NO_SIGNAL = 当前没有候选事件。

SELL 的 `direction` 是 FLAT，因为它不表示方向，而表示平仓；被平掉的那一边记在 `closes_direction`（LONG / SHORT）。没有它，「卖出平多」和「开空」在数据里长得一模一样：前向收益评估会把平仓当成新空头、纸面账本会去卖并不持有的多头（ADR-115）。

入场是电平、出场是事件，这个非对称是刻意的：入场描述一个仍然成立的状态，出场描述一个刚刚发生的动作。两者同时成立时报出场（入场下一根 K 线仍在，事件不会）。

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

「一个事件一次」有两层含义：同一根 K 线只落一条（数据库唯一键 `uq_signal_event` 守住）；持续的出场条件只落它**第一次**成立的那一根（事件语义，见第 1 节），所以不需要靠去重去压掉重复的平仓指令。

不新鲜的东西不落库：手工扫描（`POST /signals/scan?persist=true`）与定时任务走同一条门禁，`NO_SIGNAL` 不是信号，不写行、也不进结果计数。

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

AI 只能基于 Signal Evidence 解释；不能重新计算或"改判"。

## 7. Alert noise control

用户可以设置：

- only BUY/SELL
- include WAIT
- quiet hours
- daily max notifications
- minimum signal cooldown

系统应避免每根 K 线重复提醒相同信号。

用户已经标记「已读」的信号也不再推送：确认过了就是处理过了，否则「确认」在面板上不产生任何效果。`notified_at` 仍旧记录「这条曾被推送过」（ADR-115）。

## 8. Strategy evidence layers

建议 UI 同时展示四层：

1. Rule Match：当前规则满足情况
2. Empirical Stats：历史统计
3. Recent Paper Stats：模拟盘情况
4. Portfolio Context：与用户当前真实组合的关系

"AI explanation"作为第五层，只负责解释以上数据。
