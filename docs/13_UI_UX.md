# 13 UI/UX Specification

## 1. Navigation

```text
Dashboard
Strategies
  ├─ Strategy Library
  ├─ GitHub Sources
  ├─ Experimental
  └─ Strategy Detail
Backtest Lab
Paper Trading
Signals
Portfolio Context
Data Health
Settings
```

## 2. Dashboard

### 今日
- BUY/SELL signals count
- high-priority alerts
- WAIT observations

### 我的资产
- Ghostfolio current value/weight summary
- concentration warnings

### 我的策略
- strategy status changes
- new GitHub imports
- recent backtest/paper updates

## 3. Strategy Detail

分成：

- Overview
- Rules
- Provenance
- Backtest
- OOS / Walk-forward
- Paper Trading
- Current Signals
- AI Explanation
- Version History

## 4. Backtest UI

顶部配置：
- strategy
- symbol(s)
- timeframe
- date range
- initial capital
- fees
- slippage

结果：
- equity curve
- drawdown curve
- trades
- monthly/yearly view
- metric cards
- OOS split
- parameter info

必须有“查看假设”区域，列出成交模型、费用和滑点。

## 5. Paper Trading UI

显示：
- cash
- equity
- unrealized P/L
- realized P/L
- positions
- equity curve
- latest signals

## 6. Signal Detail UI

使用以下顺序：

1. 当前状态
2. 发生了什么
3. 为什么触发
4. 策略历史统计
5. 最近模拟情况
6. 真实持仓上下文
7. 风险/失效条件
8. 专业技术数据

## 7. 初学者友好

所有专业指标提供 tooltip：
- 是什么
- 怎么算
- 为什么看它
- 注意什么

不要默认让用户看到公式墙。

## 8. GitHub Import UI

流程向导：

```text
1. Repository
2. Analysis
3. Detected Strategies
4. Warnings
5. DSL Preview
6. Validation
7. Import
```

## 9. Mobile/desktop

优先 desktop，但 signal/detail pages 应适配手机宽度，方便查看 NAS 发来的通知链接。
