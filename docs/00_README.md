# My Quant Lab / 个人量化策略实验室

> 版本：V1.0 Product & Development Specification
> 目标：NAS Docker 自托管的 AI 辅助个人量化策略实验室

## 1. 一句话定义

My Quant Lab 是一个运行在个人 NAS 上的、以**策略验证和模拟交易为核心、AI 为智能辅助层、Ghostfolio 为真实资产事实来源**的个人量化研究系统。

系统持续吸收开源社区的量化/K线策略，统一转换为内部 Strategy DSL，经历史回测、样本外验证、模拟盘观察后，才可以进入实时信号观察范围。系统只提供 BUY / SELL / WAIT 等信息和解释，**第一阶段不得自动下单**。

## 2. 核心原则

1. 量化引擎负责计算，AI 负责理解、解释、研究和辅助决策。
2. AI 不能伪造或修改回测统计结果。
3. GitHub 策略不能未经验证直接进入正式信号系统。
4. 每个策略必须版本化、可追溯、可复现。
5. 默认避免未来函数、数据泄漏、幸存者偏差和不现实成交假设。
6. Ghostfolio 与模拟账户严格隔离。
7. V1 只做信息、回测、模拟和提醒，不连接券商，不自动执行真实交易。
8. 用户最终自行决定是否采取实际投资行动。
9. 系统必须支持 WAIT / NO SIGNAL，而不是为了产生信号而产生信号。
10. 以后增加策略来源不能要求重写回测、模拟盘或信号系统。

## 3. 产品循环

```text
GitHub / 自定义策略
        ↓
Strategy Importer
        ↓
AI Strategy Understanding
        ↓
Unified Strategy DSL
        ↓
Static Validation / Data-Lookahead Check
        ↓
Backtest
        ↓
Walk-forward / Out-of-sample
        ↓
Paper Trading
        ↓
Live Signal Scanner
        ↓
AI Explanation
        ↓
BUY / SELL / WAIT
        ↓
Signal Outcome Tracking
        ↓
Strategy Re-evaluation
        ↺
```

## 4. 数据事实来源

- Ghostfolio：真实持仓、交易活动、账户、资产配置和相关资产元数据。
- Market Data Providers：OHLCV 历史/实时行情。
- Quant Engine：从 OHLCV 计算指标、特征和回测结果。
- Paper Accounts：虚拟资金和虚拟持仓，独立于 Ghostfolio。
- AI Provider：分析和解释层，不作为事实数据库。

## 5. 推荐 V1 技术栈

- Backend：Python 3.12 + FastAPI + SQLAlchemy 2 + Alembic
- Data/Quant：NumPy + pandas；可逐步引入 Polars 优化批量计算
- Database：PostgreSQL
- Queue/Cache：Redis
- Worker：Celery
- Frontend：Vue 3 + TypeScript + Vite + ECharts
- Deployment：Docker Compose
- Auth：本地账号/可选反向代理认证；API Key 加密存储
- Config：`.env` + UI 配置；禁止把 secrets 写入 Git

## 6. V1 交付边界

### P0
- Docker 一键部署
- Ghostfolio REST Adapter
- OHLCV provider interface + 至少一个股票/加密货币 provider
- Indicator/Price Action feature engine
- Strategy DSL
- 策略注册与版本管理
- Backtest Engine
- 基本 Walk-forward / OOS
- Paper Trading
- Signal Scanner
- AI Provider abstraction
- AI explanation
- Web UI
- 日志、审计、测试

### P1
- GitHub Strategy Importer
- 自动检测 GitHub 更新
- 策略来源血统
- 多模拟账户对比
- Feishu / Telegram / Webhook 通知
- 策略生命周期自动晋级/降级

### P2
- 更多市场数据源
- Pine Script 等语言解析
- 更丰富的 Price Action 策略
- Portfolio-aware position sizing
- Regime-based strategy routing
- 参数敏感性分析
- Monte Carlo
- Strategy ensemble

### 明确不做
- 自动真实下单
- 券商 API 执行
- 用 LLM 直接决定成交价格或统计结果
- 用 AI 胜率文案当作真实概率
- 允许任意 GitHub Python 代码无沙箱执行

## 7. AI 量化研究智能层（进行中）

2026-10 起，项目按用户与 ChatGPT 一起定下的方向（八十二节计划，落在 `docs/25_AI_QUANT_RESEARCH_LAYER_PLAN.md`）把定位从「AI 辅助」推进到 **AI-Powered Quantitative Research Lab**：用户可以投喂投资想法、GitHub 项目、PDF、论文、网页与文章，AI 负责**理解、研究、形式化、解释**，确定性引擎继续负责**数据、指标、信号、回测、风险、参数敏感性、Monte Carlo、仓位与模拟盘**。铁律不变：**AI 负责研究，程序负责计算**——「历史年化 23%」这类数字只能来自 Backtest Engine。

`docs/26_AI_QUANT_LAYER_GAP_ANALYSIS.md` 是 Phase 0 的差异清单（现有能力 / 新增 / 可复用 / 需扩展 / 需重构 / 冲突 / 迁移 / API / UI / 测试，以及用户对 10 个开放问题的决定）。v1.9.7 已经立起地基：角色契约（`backend/app/ai/contracts/`）、AI Runtime（`backend/app/ai/runtime.py`）、能力注册表（`backend/app/capabilities.py`）与三层预算（`backend/app/ai/budget.py`）；后续版本按计划的 Phase 3–12 依次交付，每个 Phase 单独测试、单独发版。
