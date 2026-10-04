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

v1.9.7 起这一层内部按「谁能改事实」分成四块（ADR-150 至 ADR-153），边界比调用关系更重要：

```text
外部资料（GitHub 源码 / 网页 / PDF / 用户输入）  ← 不可信输入
        │  只作为 UntrustedSource 进入，永远排在契约与任务说明之后
        ▼
AI Runtime（runtime.py：缓存身份 / 预算闸门 / 审计 / 用量）
        │  读
        ├── 角色契约（ai/contracts/*.md + role_contracts.py）：「你是什么角色、最低能力、输出语言」
        ├── 能力注册表（capabilities.py）：「系统支持什么、不支持什么、为什么」
        └── 预算（budget.py）：global → provider → 单次研究 → 成本 → 调用数
        │  调用
        ▼
Provider 适配器（provider.py：OpenAI-compatible，超时可配）
        │  只能产出结构化文本或 StrategySpec 草稿
        ▼
Validator → 确定性引擎（指标 / 信号 / 回测 / 风险）→ 数字只能来自这里
```

三条不许越过的线：AI 不能直连数据库（只能走工具与 API）、不能执行任意代码（外部策略必须变成声明式 StrategySpec 过 Validator）、不能自己发明一个系统不支持的指标实现（不支持就报 `NEEDS_CAPABILITY`，由系统先扩 `capabilities.py` 与引擎）。

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

### 6.1 核心基础设施服务

六个核心服务，符合模块化单体 + Docker 服务化基础设施的设计原则：

- `quantlab-web`：Vue 3 + TypeScript 前端，提供仪表盘、策略管理、回测、模拟交易和信号监控等功能
- `quantlab-api`：FastAPI API 网关，提供 REST/WebSocket 接口，统一服务路由和认证
- `quantlab-worker`：Celery 异步任务处理，支持市场数据同步、回测和扫描等任务
- `quantlab-scheduler`：Celery Beat 定时任务，定时执行策略扫描和数据更新
- `quantlab-postgres`：PostgreSQL 数据库，存储所有业务数据，包括策略、回测、信号、用户数据等
- `quantlab-redis`：Redis 缓存和分布式任务队列，支持会话管理、缓存和Celery任务Broker

### 6.2 网络架构

采用两层网络架构实现安全隔离：

- **frontend network**：仅允许 Web 应用和 API 网关访问
- **backend network**：包含所有业务逻辑层、异步任务处理和基础设施服务

服务访问关系：
- `quantlab-web` 和 `quantlab-api` 位于 `frontend` network
- `quantlab-api`、`quantlab-worker`、`quantlab-scheduler`、`quantlab-postgres`、`quantlab-redis` 位于 `backend` network

### 6.3 可选服务

增强安全性和性能时，可选部署：
- `quantlab-nginx`：反向代理和SSL终止
- `quantlab-data-worker`：专用数据处理 worker

### 6.4 健康检查

所有服务必须支持健康检查，确保系统稳定性和可监控性。

### 6.5 应用内部架构

**业务逻辑采用六层架构模块化设计**，避免 V1 过度微服务化：

```text
domain/          # 领域模型和业务规则
data/            # 数据访问和持久化
features/        # 技术指标和价格行为特征
strategies/      # 策略执行和DSL解析
research/        # 回测、OOS和Walk-Forward分析
simulation/     # 模拟交易引擎和持仓管理
ai/             # AI 提供者适配器和解释引擎
infrastructure/ # 通用工具和支持服务
```

这六个模块作为 Python 应用的一部分运行在 `quantlab-api` 和 `quantlab-worker` 内部，实现"模块化单体 + Docker 服务化基础设施"的设计原则。

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
