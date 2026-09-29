# My Quant Lab — 个人量化策略实验室

> 版本：V0.0.1 Product & Development Specification
> 目标：NAS Docker 自托管的 AI 辅助个人量化策略实验室

## 📋 项目概述

My Quant Lab 是为非程序员设计的个人量化策略研究实验室，专注于策略验证、模拟交易和实时信号生成。系统基于六层模块化架构，采用 6 个 Docker 服务，遵循"模块化单体 + Docker 服务化基础设施"的设计原则。

## 🔧 核心架构决策 (Q1-Q10)

### Q1 Docker 服务架构
**采用 6 个核心服务：**
- `quantlab-web`：Vue 3 + TypeScript 前端
- `quantlab-api`：FastAPI API 网关
- `quantlab-worker`：Celery 异步任务处理
- `quantlab-scheduler`：Celery Beat 定时任务
- `quantlab-postgres`：PostgreSQL 数据库
- `quantlab-redis`：Redis 缓存和分布式任务队列

**网络架构：**
- **frontend network**：Web 应用和 API 网关
- **backend network**：所有业务逻辑层和服务基础设施

### Q2 数据库设计
**17 个核心表，包括：**
- Assets, MarketData, MarketDataSources
- Strategies, StrategyVersions, StrategyParameters
- Features, FeatureSnapshots
- BacktestRuns, BacktestResults, BacktestMetrics
- PaperAccounts, PaperPositions, PaperOrders, PaperTrades
- AIProviders, AIModels, AITasks, AIUsage, AIPrompts
- Jobs, JobLogs, AuditLogs, SystemSettings

**策略版本不可变性**：历史回测关联 Strategy Version + Dataset Version + Parameters + Engine Version + Feature Version

### Q5 AI 提供者
**采用 Provider Adapter 架构，支持：**
- OpenAI、Anthropic、Google Gemini、DeepSeek、Qwen、Kimi、GLM、OpenRouter 等
- OpenAI-compatible API 适配
- 预算管理和任务路由

### Q7 市场数据
**采用 MarketDataProvider 抽象，统一接口：**
```python
MarketDataProvider
├── get_assets()
├── get_quotes()
├── get_ohlcv()
├── get_intraday()
└── get_fundamentals()
```

## 🎯 项目目标

My Quant Lab V1 专注于：

- **策略验证和模拟交易**
- **AI 辅助策略发现**
- **实时信号生成**
- **完整审计跟踪**

**不**提供自动交易功能，专注于研究和分析。

## 🚀 部署到 NAS

### 1. 克隆仓库
```bash
git clone https://github.com/bobvane/My-Quant-Lab.git
cd My-Quant-Lab
```

### 2. 设置环境
```bash
# 复制环境模板并配置
source .env.example
# 编辑 .env 文件
# 设置数据库密码、Redis 密码等
```

### 3. 构建和运行
```bash
# 构建镜像和启动服务
docker compose build
docker compose up -d
```

### 4. 访问服务
- Web 界面：NAS IP
- API 健康检查：NAS IP:8080/api/v1/health

### 5. 测试
```bash
curl -f http://localhost:8080/api/v1/health
curl -f http://localhost/health
```

## 📁 文件结构

```
My-Quant-Lab/
├── My_Quant_Lab_Development_Docs/
│   └── 00_README.md              # 项目总纲
├── MY_QUANT_LAB_Docs/            # 设计文档汇总
│   └── summary.md               # 架构决策总结
├── scripts/                     # 版本管理工具
│   └── version.sh
├── NAS_DEPLOYMENT_GUIDE.md      # NAS 部署指南
├── README.md                    # 本 README 文件
└── .env.example                 # 环境配置示例
```

## 📋 主要设计文件

- **00_README.md** - 项目总纲和 V1 技术栈
- **01_PRODUCT_SPEC.md** - 产品需求和用户流程
- **02_ARCHITECTURE.md** - 系统架构和模块设计
- **03_MODULES.md** - 功能模块规范
- **04_STRATEGY_DSL.md** - 统一策略规范
- **05_GITHUB_STRATEGY_IMPORT.md** - GitHub 策略导入
- **06_AI_LAYER.md** - AI 智能层设计
- **07_BACKTEST_ENGINE.md** - 回测引擎
- **08_PAPER_TRADING.md** - 模拟交易
- **09_SIGNAL_ENGINE.md** - 信号引擎
- **10_GHOSTFOLIO_INTEGRATION.md** - Ghostfolio 集成
- **11_DATA_MODEL.md** - 数据模型设计
- **12_API_SPEC.md** - API 契约
- **14_SECURITY_LICENSE.md** - 安全和许可证
- **15_ROADMAP_ACCEPTANCE.md** - 路线图和验收标准
- **19_DEVELOPMENT_PLAYBOOK.md** - 分阶段实施指南

## 🔧 开发流程

### Phase 0 – Foundation (Weeks 1-4)
- Docker Compose、DB、Redis、API、Web、基础认证、迁移、日志

### Phase 1 – Market Data + Quant Core (Weeks 5-10)
- OHLCV、EMA/ATR/RSI/MACD/Bollinger、PA features、Strategy DSL

### Phase 2 – Backtest Lab (Weeks 11-16)
- 核心回测引擎、trade log、metrics、OOS

### Phase 3 – Ghostfolio (Weeks 17-22)
- REST adapter、sync、portfolio context

### Phase 4 – Paper Trading (Weeks 23-28)
- 虚拟现金、持仓、权益曲线、交易记录

### Phase 5 – AI Layer (Weeks 29-36)
- Provider abstraction、OpenAI-compatible API、预算、缓存、解释

### Phase 6 – GitHub Strategy Importer (Weeks 37-44)
- 仓库导入、策略提取、provenance、license、version diff

### Phase 7 – Live Signal (Weeks 45-52)
- scheduler、scanner、notification、signal outcomes

### Phase 8 – Strategy Lifecycle (Weeks 53-60)
- automated promotion/degradation rules、strategy dashboard

## 🔴 五条架构红线

1. **不自动交易** - V1 是研究实验室，不是自动交易系统
2. **AI 不决定量化结果** - AI 只解释，不计算
3. **回测可复现** - 策略版本 + 数据集 + 参数 + 引擎 + 特征
4. **Real Portfolio 与 Paper Trading 隔离** - Ghostfolio 数据只读
5. **GitHub 导入代码视为不可信输入** - 使用 AST/文本/AI 提取

## 📊 项目状态

| 阶段 | 状态 | 备注 |
|-------|------|------|
| 架构设计 | ✅ 已完成 | 所有 Q1-Q10 决策已确定 |
| 设计文档 | ✅ 已完成 | 14 个文件已更新 |
| Phase 0 实施 | ⏳ 待开始 | 完成后立即开始 |

## 🚀 下一步

1. **克隆仓库** 并设置环境
2. **启动服务**
3. **进行功能测试**
4. **验证架构设计**

## 📞 技术支持

如遇问题，请检查：
1. **环境配置** - Docker、环境变量、端口冲突
2. **日志** - 服务日志、docker compose logs
3. **网络** - Docker 网络、连接状态

## 🏷️ 版本信息

当前版本：v0.0.1
下一版本将基于开发进度发布

> GitHub 仓库：https://github.com/bobvane/My-Quant-Lab
> NAS 部署指南：NAS_DEPLOYMENT_GUIDE.md
