# 02 系统架构

## 1. 总体架构

```text
┌──────────────────────────────────────────────────────────┐
│                       Web UI                            │
│ Dashboard / Strategies / Backtest / Paper / Signals    │
└──────────────────────┬───────────────────────────────────┘
                       │ REST / WebSocket
┌──────────────────────▼───────────────────────────────────┐
│                    FastAPI Backend                       │
├────────────┬────────────┬────────────┬───────────────────┤
│ Portfolio  │ Strategy   │ Backtest   │ Signal            │
│ Service    │ Service    │ Service    │ Service           │
├────────────┴───────┬────┴────────────┴───────────────────┤
│             Domain / Quant Core                         │
│ Indicators / PA Features / Rules / Risk / Metrics       │
└──────────────────────┬───────────────────────────────────┘
                       │
       ┌───────────────┼────────────────┐
       ▼               ▼                ▼
 Market Data        Ghostfolio        AI Provider
 Providers          Adapter           Adapter
       │               │                │
       ▼               ▼                ▼
    OHLCV          Real Portfolio     LLM API

             ┌───────────────────────────────┐
             │ Redis + Celery Worker         │
             │ sync / backtest / scan / AI  │
             └───────────────────────────────┘

             ┌───────────────────────────────┐
             │ PostgreSQL                    │
             │ strategies / runs / signals   │
             │ paper accounts / audit        │
             └───────────────────────────────┘
```

## 2. 六层职责

### Layer 1：Data
负责行情、资产信息、Ghostfolio 数据和缓存。

### Layer 2：Feature
所有指标和 K 线结构特征必须由确定性代码计算。

### Layer 3：Strategy
读取标准化输入并输出结构化 signal intent，不负责数据库、AI、通知。

### Layer 4：Research
负责 backtest、OOS、walk-forward、reporting。

### Layer 5：Simulation & Signal
负责 paper portfolio、live scanner、signal lifecycle。

### Layer 6：AI / Presentation
负责解释、策略导入辅助、报告生成和用户自然语言交互。

## 3. AI 与量化引擎边界

允许 AI：
- 阅读和解释开源仓库
- 将策略转换为 DSL
- 解释指标和回测结果
- 分析结构化市场特征
- 生成用户可读的 BUY/SELL/WAIT 原因
- 协助提出研究假设

禁止 AI：
- 直接写入真实回测数字
- 直接修改历史交易记录
- 绕过 Strategy DSL 执行任意策略
- 在没有可验证输入的情况下生成价格/收益等事实
- 自动下真实交易

## 4. 后端模块

推荐包结构：

```text
app/
  api/
  core/
  domain/
  models/
  repositories/
  services/
    market_data/
    ghostfolio/
    strategies/
    backtest/
    paper/
    signals/
    ai/
    notifications/
  workers/
  schemas/
  security/
  tests/
```

## 5. 前端模块

```text
src/
  pages/
    dashboard
    strategies
    strategy-detail
    backtest
    paper-trading
    signals
    portfolio
    settings
  components/
  stores/
  services/api
  types/
```

## 6. Docker 服务

最小生产组合：

- quantlab-api
- quantlab-worker
- quantlab-scheduler
- quantlab-web
- quantlab-postgres
- quantlab-redis

可选：
- quantlab-nginx
- quantlab-data-worker

所有服务必须支持 healthcheck。

## 7. 可插拔接口

以下接口必须定义为抽象协议：

```python
class MarketDataProvider(Protocol): ...
class PortfolioAdapter(Protocol): ...
class AIProvider(Protocol): ...
class NotificationProvider(Protocol): ...
class StrategyExecutor(Protocol): ...
```

以后新增 provider 不得修改核心领域逻辑。
