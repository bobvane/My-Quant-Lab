# 03 功能模块规范

根据新的模块化单体设计，所有核心业务逻辑被整合为应用内部的六个核心模块。Docker 服务将专注于基础设施，应用内部采用六层架构。

## M01 Dashboard

目标：让不懂量化的用户一眼看到“现在有什么值得关注的事情”。

必须显示：今日信号、模拟账户概况、策略运行状态、数据健康、Ghostfolio 当前组合摘要。

## M02 Market Data

职责：
- 下载/更新 OHLCV
- 数据去重和排序
- 时区统一
- 缺失数据检测
- corporate action / adjusted price 标记
- provider source 和版本记录

数据必须带：symbol、asset_class、timeframe、timestamp、source、is_closed。

## M03 Feature Engine

初始指标：EMA20、EMA50、SMA20、ATR14、RSI14、MACD、Bollinger Bands。

初始 Price Action 特征：
- body_ratio
- upper_wick_ratio
- lower_wick_ratio
- close_position
- range_atr_ratio
- ema_relation
- overlap
- inside_bar_sequence
- outside_bar
- micro_double
- breakout
- breakout_follow_through
- breakout_failure
- distance_to_ema

所有特征都必须是 deterministic function。

## M04 Strategy Library

功能：
- 创建策略
- 导入策略
- 编辑元数据
- 版本化
- 激活/停用
- 查看血统
- 查看测试结果

策略本身不得把数据库访问、HTTP 调用和通知混在规则函数里。

## M05 GitHub Strategy Importer

见《05_GITHUB_STRATEGY_IMPORT.md》。

## M06 Backtest

见《07_BACKTEST_ENGINE.md》。

## M07 Paper Trading

见《08_PAPER_TRADING.md》。

## M08 Signal Engine

见《09_SIGNAL_ENGINE.md》。

## M09 AI

见《06_AI_LAYER.md》。

v1.9.7 起这一模块被拆成四层，边界按「谁能改事实」划：

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 角色契约 | `backend/app/ai/contracts/*.md`、`backend/app/ai/role_contracts.py` | 每个角色的职责、最低能力要求与输出语言；加载、解析、按 `(name, version)` 入库（ADR-150） |
| 能力注册表 | `backend/app/capabilities.py` | 系统到底支持哪些指标/算子/风控/执行/分析引擎，以及不支持时给什么理由（ADR-151） |
| 预算 | `backend/app/ai/budget.py` | global → provider → 单次研究 → 本请求成本 → 每日调用数，一条决策链（ADR-152） |
| Runtime | `backend/app/ai/runtime.py`、`backend/app/ai/provider.py` | 缓存身份、信任边界（外部资料永远不是指令）、`AITask` 审计与用量（ADR-153） |

外部资料（GitHub 源码、网页、PDF）只能通过 `UntrustedSource` 进入，永远排在 SYSTEM/ROLE 契约与任务说明之后；AI 不能直连数据库、不能执行任意代码、不能自己发明一个指标实现。

## M10 Ghostfolio Adapter

见《10_GHOSTFOLIO_INTEGRATION.md》。

## M11 Notification

V1 至少支持 Generic Webhook。
P1 支持 Feishu、Telegram、Email、PushPlus。

通知内容必须包含：symbol、signal、strategy、timeframe、timestamp、reason、link；禁止把 AI 文案当成收益承诺。

## M12 Audit & Observability

必须记录：
- job start/end/error
- strategy version
- data source/version
- backtest parameters
- AI provider/model/prompt version
- signal generation inputs hash
- user configuration changes

## M13 Settings

提供：
- Ghostfolio endpoint/token
- market data provider keys
- AI provider/key/model
- notification webhook
- timezone
- default currency
- paper account defaults
- fees/slippage defaults
- scan schedule

Secrets UI 只能显示掩码；日志绝不能输出完整 key。

## 新的模块化架构

所有核心业务逻辑被整合为应用内部的六个核心模块，这些模块运行在 `quantlab-api` 和 `quantlab-worker` 服务内部：

### Module Structure

```text
quantlab-api (FastAPI + Celery Worker)
├── domain/          # 领域模型和业务规则
├── data/            # 数据访问和持久化
├── features/        # 技术指标和价格行为特征
├── strategies/      # 策略执行和DSL解析
├── research/        # 回测、OOS和Walk-Forward分析
├── simulation/     # 模拟交易引擎和持仓管理
├── ai/             # 角色契约、AI 提供者适配器、runtime 与解释引擎
├── capabilities.py  # 能力注册表（AI 的知识边界）
└── infrastructure/ # 通用工具和支持服务
```

### 模块职责

**domain/**
- 核心业务实体定义
- 值对象和领域规则
- 策略DSL和参数管理

**data/**
- 数据库访问层
- 数据迁移和备份
- 数据质量验证

**features/**
- 指标计算引擎
- 价格行为特征提取
- 特征缓存和管理

**strategies/**
- 策略执行引擎
- 规则验证和优化
- 策略生命周期管理

**research/**
- 回测引擎核心
- OOS和Walk-Forward分析
- 绩效评估和报告生成

**simulation/**
- 模拟交易引擎
- 持仓管理和风险控制
- 交易记录和结算

**ai/**
- 角色契约（`contracts/` 的 markdown + `role_contracts.py` 的加载与入库）
- AI提供者适配器（`provider.py`，含不可信来源的数据边界）
- 解释和分析引擎（`explain.py`）
- Runtime：缓存身份、预算闸门、审计（`runtime.py`、`budget.py`）
- 提示模板管理和缓存

**capabilities.py**
- 能力注册表：系统支持什么、不支持什么（含理由），以及 `assess()` 三态

**infrastructure/**
- 日志和监控
- 配置管理和环境
- 安全和认证
