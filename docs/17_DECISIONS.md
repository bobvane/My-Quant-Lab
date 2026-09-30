# 架构决策记录（ADR）

本文件记录 My Quant Lab V1 已确定的架构决策。状态：**已冻结**，变更需新增 ADR。

## ADR-001：Ghostfolio 通过 REST 接入，不直连数据库

**决策**：V1 使用 Ghostfolio REST API。
**理由**：降低 schema 耦合，保持外部系统边界清晰。

## ADR-002：自研确定性回测内核

**决策**：不把第三方回测框架作为业务核心，自研受控引擎。
**理由**：Strategy DSL、模拟盘、信号扫描必须共享完全一致的执行语义。

## ADR-003：AI 是解释层，不是计算层

**决策**：收益率、CAGR、Sharpe、Sortino、最大回撤、胜率等全部由代码计算；LLM 只能解释既有事实。
**理由**：可复现、可审计。禁止 LLM 自行"计算并声称"统计结果。

## ADR-004：所有策略统一进入 Strategy DSL

**决策**：无论来自 GitHub、自定义还是 AI 生成，一律转换为 DSL。
**理由**：社区策略增长不引起架构重写。

## ADR-005：策略版本不可变（ADR-005）

**决策**：任何规则变更都生成新版本；已存在的 `strategy_versions` 行永不可修改。
**理由**：历史回测结果必须可复现。该约束同时由应用层与数据库触发器
（`backend/alembic/versions/0002_immutability.py`）双重保证。

## ADR-006：Paper 账户与真实持仓完全隔离

**决策**：Quant Lab 不向 Ghostfolio 写入任何数据；模拟账户使用独立表与虚拟资金。
**理由**：保护真实组合数据，保持实验环境干净。

## ADR-007：默认只用已收盘 K 线

**决策**：策略扫描与回测默认基于 closed bars；信号在 t 收盘确认，t+1 开盘成交。
**理由**：避免使用未完成信息做决策，降低实盘与回测的偏差。

## ADR-008：Provider 抽象（ADR-008）

**决策**：行情、AI、通知、组合数据全部为适配器协议。
**理由**：避免锁定单一供应商，新增 provider 不应修改核心领域逻辑。

## ADR-009：V1 不做自动交易（ADR-009）

**决策**：不实现任何券商下单端点、后台执行器或交易凭证集成。
**理由**：本项目价值在于研究、验证与信息提示，自动执行不是目标。

---

## ADR-010：模块化单体 + Docker 服务化基础设施（决策 Q1）

**决策**：业务域（Data / Feature / Strategy / Research / Simulation / AI）不拆微服务，
作为 Python 应用内部模块存在；Docker 层只部署 6 个服务：

```text
quantlab-web / quantlab-api / quantlab-worker
quantlab-scheduler / quantlab-postgres / quantlab-redis
```

**理由**：V1 团队规模小，过度微服务会显著增加部署、调试与一致性成本，
而收益为零。领域边界由包结构与类型约束保证，而非由网络边界保证。

## ADR-011：双网络隔离（决策 Q2）

**决策**：仅 `frontend` 与 `backend` 两个网络；PostgreSQL 与 Redis 不暴露到 LAN。
API 默认只绑定 `127.0.0.1`，由 web 容器代理 `/api`。
**理由**：满足"复杂但可运维"的分级隔离；V1 不需要更细的网络分区。

## ADR-012：PostgreSQL 单一数据库（决策 Q3/Q4）

**决策**：全部业务数据落在 PostgreSQL；`market_data_bars` 以 `(series_id, timestamp)`
为主键，表结构按未来可迁移 TimescaleDB 设计。
**理由**：规范化模型 + JSONB 汇总指标；不提前引入时序数据库复杂度。

## ADR-013：AI Provider 走 OpenAI 兼容协议（决策 Q5）

**决策**：核心代码不硬编码任何模型名；Provider / Model / Endpoint / 价格全部配置化。
**理由**：可接入 OpenAI、Anthropic 兼容网关、DeepSeek、Qwen、Kimi、GLM、OpenRouter 等。

## ADR-014：AI 任务综合路由（决策 Q6）

**决策**：按 `Task + Capability + Cost + Availability + Budget` 路由，而非仅按成本。
同时保留每日预算、单任务预算与 Provider/Model 成本统计。
**理由**：成本之外还要考虑任务能力匹配与供应商可用性。

## ADR-015：V1 只实现一个行情 Provider（决策 Q7/Q8）

**决策**：抽象层完整定义 `get_assets / get_quote / get_ohlcv`，V1 完整实现
`synthetic`（离线确定性演示数据）与 `yahoo_finance`（真实行情）。
资产模型覆盖 stock / ETF / crypto / index / future。
**理由**：先跑通「Provider → 标准化数据 → PostgreSQL」闭环，避免同时维护多个数据源。

## ADR-016：UI 从第一阶段就存在（决策 Q9）

**决策**：Web UI 不是最后的装饰层，而是与后端同步迭代的最小可用界面。
**理由**：目标用户是非程序员，UI/UX 属于核心产品能力。

## ADR-017：版本号每段 0–9，到 10 进位

**决策**：`v0.0.1 → v0.0.9 → v0.0.10 → v0.1.0`，由 `scripts/version.sh` 统一维护，
同步写入 `version.txt`、`backend/pyproject.toml`、`backend/app/__init__.py`、
`frontend/package.json` 与 `.env.example`。
**理由**：版本号来源唯一，避免各处手工修改导致不一致。

## ADR-018：标签驱动发布到 GHCR

**决策**：推送 `v*` 标签触发 `.github/workflows/release.yml`：构建并推送
`ghcr.io/bobvane/my-quant-lab-backend` 与 `...-web`，用该版本跑冒烟测试，
再创建 GitHub Release。
**理由**：NAS 侧只需 `MQL_VERSION=<tag>` 即可拉取已构建镜像，无需本地构建。

## ADR-019：生产 compose 只引用预构建镜像

**决策**：`docker-compose.yml` 不含 `build:` 段，只写
`image: ghcr.io/bobvane/my-quant-lab-backend:${MQL_VERSION:-latest}`；
本地构建能力移到 `docker-compose.build.yml` 覆盖文件，仅供 CI 与开发者使用。
**理由**：图形化 NAS 的 Compose 项目是"选目录 + 读单文件"，多文件叠加用不上；
NAS 部署只需要 `docker-compose.yml` + `.env` 两个文件。

## ADR-020：许可为"使用须经作者同意"，镜像公开

**决策**：项目采用根目录 `LICENSE` 中的自定义许可（保留所有权利，
查看/部署/使用/引用均须事先获得作者书面同意），不是开源许可；
同时把两个 GHCR 镜像包设为公开以便 NAS 直接拉取（镜像内不含任何密钥，
密钥只存在于用户 NAS 本地的 `.env`）。
**理由**：代码仓库已公开，但作者要求对使用加以控制；镜像公开仅为部署便利，
不改变许可性质。

## ADR-021：开发仓库与部署仓库分离

**决策**：`My-Quant-Lab`（本仓库）设回私有，保留全部源码、文档、CI 与
发布流水线；另建公开仓库 `My-Quant-Lab-Deploy`，仅含 4 个文件：
`docker-compose.yml`、`.env.example`、`README.md`、`LICENSE`，
无源码、无开发文档、无实现注释。
**理由**：作者不希望公开源码被 fork 或作为二次开发参考；NAS 部署本来就
只需要 compose 文件与环境模板。GHCR 镜像保持公开（部署便利），法律约束
由 LICENSE 承担——技术上无法做到"镜像可拉但代码不可见"（镜像层可解包），
这一点已向作者明确，接受该权衡。

## ADR-022：单仓库 MIT 开源（取代 ADR-020 / ADR-021）

**决策**：取消双仓库拆分计划，不再新建 `My-Quant-Lab-Deploy`；
本仓库保持公开并按标准公共开源项目开发，采用 MIT 许可；
GHCR 镜像保持公开供 NAS 直接拉取。
**理由**：作者决定正常以公共开源方式推进，部署便利性（两文件拉取）
已由 ADR-019 解决，不再需要第二个仓库；MIT 为最通用的默认选择。

## ADR-023：行情源命名、收盘语义与「不得编造数据」

**决策**：
1. MARKET_DATA_PROVIDER 决定默认源，单次同步可用 provider 参数覆盖
   （此前该参数被接收后忽略，等于无法选择数据源）。
2. synthetic 只服务 DEMO-AAPL / DEMO-BTC。用随机游走数据冒充实盘代码
   会被拒绝（HTTP 4xx）——**研究工具绝不允许把编造的价格标注成真实标的**。
3. 未收盘 K 线一律以 is_closed=false 落库，而不是丢弃；特征计算与回测
   只取 is_closed=true。日线覆盖「今天」即视为未收盘（按系列时区判断）。
4. 定时同步的标的清单优先取 MARKET_DATA_WATCHLIST，未配置时取当前 provider
   声明的清单，不再硬编码 DEMO-*。
5. Yahoo Finance 通过 yfinance 内置进镜像（约 +25MB），限流时它返回空表而非
   抛错，因此「返回 0 根 K 线」的提示必须明确列出限流这一可能。

**理由**：行情数据是整个研究链路的事实来源。数据源不可选择、未收盘 bar 被
当成已收盘、或真实代码下挂着随机数据，都会让后续回测与信号结论失去意义。
