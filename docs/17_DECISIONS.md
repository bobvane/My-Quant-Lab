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
## ADR-024：内置轻量系统资源监控（docs/20_RESOURCE_MONITOR.md）

**决策**：
1. 不引入 Prometheus / Grafana / cAdvisor / InfluxDB / Elasticsearch 或任何
   独立监控平台容器。监控挂在现有 Celery beat 上（每 60s 一个轻量任务）。
2. 分两层权限：层 1（无 Docker 权限）覆盖 NAS 整体与 Quant Lab 自身，
   用 psutil 读 /proc 与各自 cgroup；层 2 覆盖全 NAS 所有容器，
   需要一个自写的只读过滤代理微容器。
3. Docker socket（等价宿主机 root）**不挂载进 api/worker**。它只挂载进
   独立的 quantlab-docker-proxy 容器，且该代理用白名单把请求限制在
   GET /containers/json 与 GET /containers/<id>/stats?stream=false 两条，
   其余一切（POST/DELETE/exec/build/stream）一律 403。
4. 容器识别按 Docker label（com.docker.compose.project）自动发现，
   不硬编码容器名，拓扑变化不影响监控。
5. 数据保留：原始 60s 采样 7 天 → 5 分钟聚合 30 天 → 之后每日任务自动清理。
   约 11 万行/周（数 MB），PostgreSQL 无压力，不引入时序扩展。
6. 前端不做高频轮询：后台 60s 采集，页面 30–60s 刷新一次最近采样。

**理由**：用户需要用实测数据判断 Quant Lab 是否值得做架构精简，
而监控系统自身不能成为新的资源负担。

## ADR-025：通知层 V1 只做 Generic Webhook，且必须可去重、可审计、可降噪

**决策**：
1. V1 只实现一个出站通知渠道：通用 Webhook（`NotificationProvider` 协议不变，
   未来新增飞书/Telegram/邮件只需新增适配器，不改信号引擎）。
2. 通知发生在信号流水线的 `persist → notify` 之后（docs/09 §2），由 Celery
   worker 执行，绝不在 API 请求内同步外呼，避免慢/失败的 webhook 拖垮接口。
3. 去重以 `Signal.notified_at` 为准：同一 (策略版本, 标的, 周期, K 线时间)
   只通知一次；每条信号发送后**立即提交**，崩溃也不会重发。
4. 只有真正发生在「最新已收盘 K 线」上的事件才会被持久化并通知
   （`is_fresh`）。历史的旧命中不会被逐根 K 线重复落库、重复提醒；
   NO_SIGNAL 也不落库，避免每根 K 线一行造成表膨胀。
5. 告警降噪（docs/09 §7）：默认只发 BUY/SELL（WAIT 可选）、免打扰时段、
   每日上限、同一系列冷却时间。免打扰/冷却期间只是延迟，不消费事件；
   每日上限触发的丢弃会标记为已处理并记录审计，避免次日补发老信号。
6. 启用时写入水位线（`notification_enabled_at`），只通知启用之后产生的
   信号——打开开关不会把历史存量一次性轰炸出去。
7. 配置存放在 `system_settings`（与运行时 proxy 同机制，UI 可改）；
   webhook URL 与签名密钥标记为 secret 且**加密存储**（复用
   `infrastructure.secrets`），写-only、列表只回显 scheme+host 掩码、
   审计与异常信息都不含明文；通用 `PUT /settings` 拒绝写 `notification_*`
   键，强制走专用校验端点。
8. 出站安全（docs/14 §4/§5）：仅接受 http/https；拒绝云元数据主机名
   并解析后拒绝 link-local（十进制/IPv6 写法也拦得住）；**不跟随重定向**；
   固定超时。私有/局域网地址按设计放行（NAS 用户常通知自家服务）。
   可选 `X-QuantLab-Signature: sha256=<HMAC>` 供接收方校验。
9. 载荷只包含引擎已算出的字段（symbol / signal / direction / strategy /
   timeframe / timestamp / reason / 触发规则 / link + 免责声明），
   AI 文案永远不作为收益承诺，且通知只是信息，不触发任何交易。

**理由**：通知是 Phase 7 的交付项，但此前仅有协议占位。缺少它，实时信号
只能在页面里看到；而一旦实现不当（重复轰炸、明文密钥、SSRF、同步阻塞），
又会引入新的噪声与安全面。以上约束把通知做成一个可预测、可追溯、默认关闭的
可选能力。

## ADR-026：策略生命周期由确定性证据门控，AI 无权晋级

**决策**：
1. 生命周期阶段沿用 `StrategyLifecycle` 枚举，推进路径为
   imported → normalized → validated → backtested → oos_tested → paper_trading；
   reference_signal / degraded / retired 是终止或人工阶段。
2. 晋级/降级规则**只读取引擎已记录的事实**：版本校验状态、已完成的
   BacktestRun、walk-forward 审计事件、PaperTrade 记录。阈值固定且有默认值
   （最少回测成交数、最少样本外窗口、最少模拟成交数、亏损降级阈值），
   不使用任何模型判断。
3. 每次只前进一个阶段，保留「Experimental → OOS → Paper」的可视路径；
   证据不足时给出 `blocked_reason` 而不是跳级。
4. **reference_signal 与 retired 只能手动应用**，自动化任务永不触及；
   参考信号还要求模拟盘有足够成交且累计为正。
5. 每次阶段变更都写 `strategy_lifecycle_changed` 审计事件，携带
   from/to/证据快照/时间；不存在「AI 一句话升级」的路径。
6. Celery beat 每日执行一次自动评估（`LIFECYCLE_AUTO_ENABLED` 可关），
   只应用证据满足的晋级与亏损降级。

**理由**：docs/15 Phase 8 要求晋级/降级有证据且排除 AI 干预。把规则写成固定
阈值 + 只读事实 + 审计快照，既满足验收，也让用户随时能看清一个策略为什么
停在某个阶段。

## ADR-027：通知升级为多渠道（docs/03 M11 P1）

**决策**：
1. 把「单个 webhook」抽象为**渠道列表**：Generic Webhook、飞书、Telegram、
   PushPlus、Email(SMTP) 五类，每个渠道可独立启用；全局降噪（状态过滤、
   免打扰、每日上限、冷却）对所有渠道统一生效。
2. 所有渠道实现同一个 `NotificationProvider.send(title, body, meta)` 协议，
   统一从引擎已算字段构造文案；AI 文案不作为收益承诺。
3. 渠道配置存在 `system_settings.notification_channels`（JSON 列表）；渠道内
   的密钥字段（webhook/飞书 URL 与签名、Telegram bot_token、PushPlus token、
   Email 密码）逐字段加密存储，读取只回显掩码 + `*_set` 标记。
4. 旧版单 webhook 的两个键（`notification_webhook_url` / `_secret`）继续读取：
   当没有渠道列表时，自动合成一个 webhook 渠道（向后兼容）。
5. Email 用标准库 `smtplib`，不新增依赖；其余渠道复用 `httpx`，统一固定超时、
   不跟随重定向，并沿用出站 URL 的 SSRF 校验。
6. 传递语义：一条信号在**任一渠道成功**即视为已送达（写 `notified_at`），
   失败的渠道单独记 `signal_notification_failed` 审计但不重发到已成功渠道，
   避免重复；全部失败则保留待下次重试。

**理由**：P1 要求接入飞书/Telegram/Email/PushPlus。把渠道做成可扩展的适配器
列表，既满足该清单，又保持「一次事件、一次通知」与「密钥不回显」两条既有
约束；不引入新依赖也符合 NAS 部署的轻量目标。

## ADR-028：可选 Bearer 鉴权（docs/14 §4）

**决策**：
1. 新增 `API_AUTH_TOKEN`（默认空）。**空 = 不鉴权**，与本项目「API 只绑
   127.0.0.1、经 Web 容器代理」的默认部署完全一致，现有安装零影响。
2. 设置后，`/api/v1/*` 下除 `/healthz` 与 `/health` 两个探针外，全部要求
   `Authorization: Bearer <token>`（常量时间比较）；`OPTIONS` 预检放行。
   `/docs` 与 `/openapi.json` 不在 `/api/v1` 下，保持可访问。
3. 内置 Web 容器在启动时把同一 token 注入 nginx 的 `/api` 代理头，因此
   浏览器端无需知道 token，UI 照常可用——token 只存在于容器内。
4. Token 需 ≥8 位且仅允许 `A-Z a-z 0-9 . _ ~ + / = -`，既避免弱口令，也
   避免在 nginx 配置/HTTP 头中出现需要转义的字符。
5. 这是对「把 API 暴露到局域网」场景的加固，不是访问控制体系的替代品：
   单用户 NAS 场景下，V1 仍以「只绑本机 + 网关」为主要边界。

**理由**：docs/14 §4 要求 bearer auth 与 rate limiting，此前缺失；两次安全
评审都指出无认证会放大 SSRF/端口探测面。以「默认关闭、按需开启、Web 代理
自动注入」的方式补齐，既满足文档要求，又不破坏既有的开箱即用体验。

## ADR-029：轻量进程内限流（docs/14 §4）

**决策**：
1. 对 `/api/v1` 下的**写请求**（POST/PUT/DELETE）按客户端 IP 做滑动窗口限流，
   默认 `RATE_LIMIT_PER_MINUTE=60`，`0` 关闭；只读请求（GET/HEAD/OPTIONS）
   与两个健康探针永不限流，保证仪表盘轮询与编排探针不受影响。
2. 超限返回 429 + `Retry-After: 60`，结构化错误体。
3. `APP_ENVIRONMENT=test` 时自动关闭（测试与本地开发零干扰）；限流器为
   **进程内**实现，多副本时各进程独立计数，反向代理仍是共享限流的正确位置。
4. 不引入 Redis 依赖做分布式限流：V1 单 API 容器，进程内实现足够钝化误触与
   暴力探测，且与「轻量 NAS 部署」的目标一致。

**理由**：通知测试、行情同步、导入分析等端点会触发外部请求或重计算，是明显
的滥用面。用一个显式、可关、可解释的阈值把它们保护起来，比引入一套独立的
限流基础设施更符合本项目规模。

## ADR-030：模拟盘执行引擎（补齐 docs/08 与 Phase 4）

**决策**：
1. 新增 `app/simulation/paper_engine.py`：确定性、无未来函数的虚拟成交。
   V1 **多头、单一持仓**；BUY 开仓、SELL 平仓，无券商路径。
2. 成交价 = 信号 `price_reference`（信号所依据的已收盘 bar 的收盘价）± 滑点；
   `fee_bps` / `slippage_bps` / `max_position_pct` 可配置（默认 10 / 5 / 1.0）。
   引擎只读取已持久化的信号行，不读取任何未来数据。
3. 记账：BUY 扣现金并建仓；SELL 回补现金，`PaperTrade.pnl` = 卖出现金净额 −
   （数量×成本 + 买入手续费），即已扣除两侧费用。空仓卖出、重复买入、关闭账户
   成交一律拒绝（422）。
4. 生命周期：账户可 `close` / `reopen`；`fund` 支持注入/提取虚拟资金（注入同时
   抬高 `initial_cash` 基准，避免把入金算成收益；透支拒绝），全部写审计。
5. 账户状态、持仓、成交、资金变动全部落在隔离的 paper_* 表，绝不触碰
   Ghostfolio 或任何真实持仓。

**理由**：此前只有账户簿记，`PaperTrade/PaperOrder/PaperPosition` 从无写入路径，
docs/15 Phase 4 的「买卖后现金/持仓正确、费用/滑点进入结果」实际无法成立。
补上执行引擎后 Phase 4 才真正闭环；同时把成交语义固定为「参考价±滑点」并写入
ADR，保证结果可复现、可解释。

## ADR-031：DSL 声明式指标真正物化 + 参数化 period_ref

**决策**：
1. `build_features(bars, spec=...)` 会遍历 `spec.indicators`，按 `id` 物化成特征列；
   支持 EMA / SMA / RSI / ATR / MACD / Bollinger。MACD 另出
   `<id>_signal`/`<id>_hist`，Bollinger 另出 `<id>_upper`/`<id>_lower`。
2. 指标周期可写死 `period`，也可用 `period_ref` 指向 `spec.parameters` 中的参数
   （如 `period_ref: fast_period`），使同一策略可参数化而无需改规则。
3. 校验器同步：把 `id` 及派生列加入已知列集合，并对「未知指标类型 / 缺周期 /
   `period_ref` 指向不存在的参数」报错。
4. 引擎对所有调用方（回测、信号扫描、证据、AI 预览）统一传入 `spec`，保证
   「声明 → 计算 → 执行」使用同一套列。
5. 固定列（`ema20/ema50/atr14/rsi14/macd*/bb*`）继续保留，向后兼容既有策略。

**理由**：此前 `indicators` 只被校验器接受、从未被计算，声明式指标一用即
`KeyError`；`parameters` 也不参与计算。这使 DSL「声明式、可参数化」的核心承诺
落空。物化声明式指标后，用户才能真正定义自定义周期指标并参数化策略。

## ADR-032：WAIT 状态与 FeatureSnapshot 证据落地

**决策**：
1. **WAIT**：`all` 型入场组「部分条件满足、尚未全部满足」时输出 WAIT（候选可
   观察但入场未确认）。执行器 `_eval_state` 同时返回 `satisfied` 与 `partial`
   两种掩码；`any` 组不产生 partial。WAIT 在最新已收盘 bar 上判定。
2. **只评估最新收盘 bar**：`_last_intent` 不再回溯历史命中并把它投影到最新
   bar（此前的「旧信号反复出现」问题的根因）。在最新 bar 上没有命中即
   NO_SIGNAL。这使扫描结果语义正确，也让 `is_fresh` 判定可靠。
3. **FeatureSnapshot**：每次持久化信号时，把该 bar 的特征行写入
   `feature_snapshots`（唯一键 series+bar+feature_version，`input_hash` 与
   `Signal.feature_snapshot_hash` 一致），使信号可复现、可审计（docs/09 §5）。
4. **读接口**：新增 `GET /feature-snapshots/{series_id}` 与 `.../latest`。
5. BUY/SELL/WAIT 都可在最新 bar 上持久化；NO_SIGNAL 不落库。通知默认仍只发
   BUY/SELL（`include_wait` 可开）。

**理由**：docs/09 §1 要求 WAIT 作为输出状态，§5 要求特征快照证据；此前 WAIT
永不产生、快照表空转，扫描还把历史命中当作今日信号。补齐后「状态语义 + 证据
链 + 去重」三者一致。

## ADR-033：Ghostfolio 持仓解析健壮化 + 信号组合上下文

**决策**：
1. 修复 `get_portfolio_summary` 的真实缺陷：活动聚合里 `profile` 只在部分分支
   赋值，遇到「活动带直接 symbol」时抛 `UnboundLocalError`，导致
   `/settings/ghostfolio/holdings` 在真实组合上 500。
2. 优先使用 Ghostfolio 的 `/api/v1/portfolio/holdings`（含当前价与市值）；
   不可用时回退到 export 的活动聚合。两条路径都做「字段名多写法」容错
   （symbol/dataSourceSymbol、marketPrice/price、valueInBaseCurrency、
   allocationInPercentage 0–1 或 0–100 自动归一）。
3. `portfolio_context_for(symbol, holdings)` 产出 docs/09 §3 的组合上下文
   （持有数量/市值/占比 + 人类可读说明），扫描时**每轮只取一次**持仓，
   写入 `Signal.portfolio_context_json`；Ghostfolio 未配置或不可达时诚实标注
   `ghostfolio_connected=false`，绝不影响信号生成。
4. `/settings/ghostfolio/holdings` 不再返回原始 debug 快照，只返回聚合摘要。
5. Ghostfolio 始终只读；无任何写入或本地镜像表（本地镜像列为后续 P1）。

**理由**：Phase 3 的验收是「能测试连接 / 能同步活动与资产 / 原数据不被修改 /
失败有明确错误」。此前持仓聚合在真实数据上直接崩，`Signal.portfolio_context_json`
从不写入，证据层占比恒为 0。修好解析并落地组合上下文后，这三项才成立。

## ADR-034：OOS 单次留出（最后 N% 或指定日期）

**决策**：
1. 新增 `research/walk_forward.run_holdout`：把序列切成 in-sample 与
   out-of-sample 两段。测试窗 = 最后 `oos_pct`（默认 20%）或 `oos_start` 之后
   的全部 bar（两者取其一，`oos_start` 优先）。
2. 两段都跑同一份策略 spec（不做参数拟合），分别返回各段摘要
   （total_return / max_drawdown / sharpe / win_rate / trades / result_hash）。
3. 新增 `POST /research/oos`；切分不合法（空窗、pct 越界）返回 422；
   写入 `oos_completed` 审计事件。
4. 与滚动 walk-forward 并存：walk-forward 看稳健性，holdout 看「最后一段」的
   样本外表现。

**理由**：docs/07 §11 要求「用户可以指定最后 N% 或指定日期为 OOS」。此前只有
滚动 walk-forward，无法按用户指定的时间点做单次留出。补齐后研究流程完整。


## ADR-035：资源监控 5m/1h/1d 三档聚合

**决策**：`roll_up_recent` 每轮由原始 60s 采样**重算** 5m/1h/1d 三档聚合（无增量漂移）；`/resources/history` 按 range 选择最粗但仍够用的粒度（7d→1h、30d→1d），原始保留 7 天、聚合保留 30 天。

**理由**：长期图表不应读取海量原始点；重算保证聚合稳定，进程重启也不产生漂移。

## ADR-036：信号结果追踪与统计

**决策**：信号结果在价格前进后由定时任务回填 `pnl_pct`/`MAE`/`MFE` 及 `entry_time`/`exit_time`；`/signals/outcome-summary` 对已回填结果做**纯聚合**（整体 + 按方向/周期/状态/策略的胜率与 PnL），不重算、不涉及 AI。

**理由**：让「信号是否有用」可被客观衡量，且与其它统计一样只来自确定性计算。

## ADR-037：v1.0 里程碑与范围冻结

**决策**：
1. V1（v1.0）覆盖 docs/15 的 Phase 0–8 全部交付与验收，并额外完成 P1：通知多渠道、API 鉴权/限流、回测限价/停止入场、资源监控、GitHub 来源自动更新、AI 多供应商路由（ADR-014）。
2. 五条红线维持不变；V1 不含任何券商下单或自动交易。
3. 后续（v1.x）以打磨、文档与体验为主；结构性新能力（ML/LLM 辅助策略、Portfolio-aware sizing、Monte Carlo、Ensemble 等）列入 v2 路线，需新增 ADR 与 fixtures。
4. 变更管理：任何触及回测语义、DSL 契约、不可变性的改动，必须先更新 ADR 与 golden/回归测试。

**理由**：给项目一个明确的稳定基线，并约束后续演进的边界。

## ADR-039：v1.0.0 发布前的打磨收尾

**决策**：
1. **AI 任务面板**：设置页展示 `/ai/tasks`（任务类型/提示词/状态/费用/耗时），点击「详情」调 `/ai/tasks/{id}` 查看结构化输出、Token 用量与错误；任务详情为只读审计用途，密钥永不回显。
2. **Walk-Forward UI**：回测实验室接入滚动 Walk-Forward（后端 `POST /research/walk-forward` 早已就绪），与 OOS 并列展示「窗口数 / 平均样本外收益 / 一致性」及逐窗口明细。
3. **Ghostfolio 连接测试**：仪表盘持仓卡片增加「测试连接」（`GET /settings/ghostfolio/test`），仍为只读。
4. **轻量端点**：新增 `GET /ai/tasks/{task_id}/status`（状态轮询，含错误信息）。
5. **明确不做**（v1.0 范围冻结，避免臆造）：
   - `POST /ai/tasks/{task_id}/cancel`：AI 为同步执行、无排队任务可取消；
   - `GET /backtests/comparisons/{comparison_id}`：对比为无状态即时计算、不持久化；
   - `GET /features/{feature_id}/versions`：特征定义为单版本、按 name 唯一。
   - 三者保留 `[计划]` 标注并写入 docs/12 理由。

**理由**：补齐文档/后端已有但 UI 未暴露的能力，让 v1.0.0 的交付与已冻结范围一致；同时用 docs/12 的 `[计划]` 标注把语义不明或需持久化改造的端点显式排除在 v1.0 之外，避免为凑端点而臆造语义。

> 注：ADR-038 号未被使用（历史空缺）。v1.0 之后的变更从 ADR-040 继续编号。

## ADR-040：回测参数覆盖必须真正生效，且结果哈希只覆盖「有效参数」

**背景**：`run_backtest(spec, bars, parameters=...)` 一直把 `parameters` 折进 `result_hash`，却**从未把它应用到特征引擎**——特征始终按 `spec.parameters` 计算。后果有两个方向，都很严重：

1. **静默失效**：用 `parameters={"trend": 5}` 与 `{"trend": 40}` 跑同一策略，得到**完全相同的 25 笔交易**，但 `result_hash` 不同。使用者以为参数生效了，实际拿到的是同一份结果。
2. **哈希说谎**：结果哈希是 docs/07 可复现性契约的锚点（策略版本 + 数据集 + 参数 + 引擎版本 + 特征版本）。它声称"参数不同 → 结果不同"，而数字层面根本没有变化，等于把两份相同的结果登记成了不同结果；反向地，把 `spec.parameters` 原样回传也会平白改变哈希、让已存结果无法复现。

**决策**：

1. 新增 `resolve_parameters(spec, overrides) -> (effective, warnings)`，作为参数合并的**唯一入口**。有效参数 = `spec.parameters` 叠加 overrides。
2. `run_backtest` 用 `effective` 重写一份 `spec`（`model_copy`）后再交给特征引擎，**并**用同一个 `effective` 计算 `result_hash`。特征与哈希从此不可能不一致。
3. **未知键降级为 warning，不抛错**：override 可能合法地携带策略不需要的键（调用方整体回传 `spec.parameters` 是常见写法），报错会破坏既有调用；同时**未知键不进入哈希**，避免"被忽略的键伪装成另一次计算"。warning 经 `BacktestResult.warnings` → API `warnings` 字段回传，用户看得到。
4. 不把成本（`fee_bps`/`slippage_bps`）纳入 `parameters`：成本属于 `execution`，用 `execution_overrides` 调整。原先那条 `test_result_hash_changes_with_parameters` 用 `parameters` 传成本，只证明了"哈希会混入任意字典"，已改为 `test_result_hash_changes_with_costs`。

**理由**：可复现性契约要求「同样的输入 → 同样的数字」，也要求「数字不同 → 哈希不同」。修复前两条都破了。此修复是 docs/21 参数敏感性分析的前提：不修的话扫一整片参数网格会得到一排完全相同的结果，还会被报告成"策略极其稳定"。

## ADR-041：参数敏感性分析（docs/21）

**决策**：

1. 新增 `app/research/sensitivity.py` + `POST /research/sensitivity`：把一个或多个**已声明参数**（`period_ref` 指向的键）扫成笛卡尔网格，逐点独立回测，回报每个点的指标、目标指标的分布统计（mean/median/stdev/min/max/range/positive_ratio）、最佳/最差点与 `stable` 判定。
2. **纯描述性，不做参数寻优**：报告给"这个邻域长什么样"，不给"应该用哪组参数"。`best`/`worst` 只是排序结果，不是推荐；AI 也不得改写这些数字（docs/02 §3）。
3. `grid` 的轴必须是策略已声明的参数，未知轴 → 422（否则会扫出一份"只有一个点"的假报告）。
4. 网格点数上限 `MAX_GRID_POINTS = 144`，超出 → 422。一次扫描 = 网格点数 × 一次回测的成本，必须防止一个手误把 NAS 的 CPU 占满一小时。
5. 目标指标未定义（样本不足 / 无交易）时保留该点但 `objective = None`，并从排序与分布统计中剔除；`stable` 返回 `None`。**不把未知当 0**。
6. `stable` 用「所有已评估点目标值同号」这一朴素但诚实的判据，不做样本量支撑不了的显著性检验。
7. 每次扫描写审计事件 `sensitivity_completed`，含网格轴与统计摘要，使报告事后可复现。

**理由**：docs/15 把参数敏感性分析列为 P2。在 ADR-040 之后它才真正可得。用「跨邻域是否稳健」回答"这个策略是不是只在某一组精确参数下成立"，比单点最优更接近研究要问的问题。

## ADR-042：v1.1 迭代计划（体验与一致性收尾）

**决策**：v1.0.0 已在 NAS 上验证（Web/API/DB/Redis/worker 全绿，12/12 端到端检查通过）。v1.1 的范围限定为"不改变回测语义、不新增结构性能力"的修复与补全：

1. **行情 provider 语义写进文档与 UI**：`.env` 为 `yahoo_finance` 时 `DEMO-AAPL` 必然 0 根 K 线（这是正确行为，报错文案也已解释），但 README 仍把 `synthetic` 说成默认且未说明"换 provider 后演示代码会失效"。补文档 + 同步页给明确指引。
2. **参数敏感性分析前端**：回测实验室接入 `POST /research/sensitivity`，用热力图/折线展示目标指标随参数的走势与稳健性。
3. **清理测试告警**：`fastapi.testclient` 的 `StarletteDeprecationWarning`（httpx）长期存在，CI 输出不干净。
4. **`backend/pyproject.toml` 与镜像的关系**：该文件是 pytest/ruff/mypy 配置的唯一来源，但 `Dockerfile.backend` 只装 `requirements.txt`。明确它是开发期配置并写入文档，避免"改了 pyproject 以为镜像行为会变"。

**理由**：v1.0.0 是一个已验证的稳定基线。继续推进时先修一致性与体验问题、再开结构性能力，且每个大版本都停在 NAS 上做一次真实端到端验证（用户明确要求的节奏）。

## ADR-043：Monte Carlo 采用交易级 IID Bootstrap（docs/22）

**背景**：docs/15 把 Monte Carlo 列入 P2。一次回测只是**一条**历史路径，报告里的收益率是单次抽样而不是期望值；结构性上看不到「同一批交易换个顺序会不会回撤更深」。

**决策**：

1. 新增 `app/research/monte_carlo.py` + `POST /research/monte-carlo`：从一次**已完成**回测已落库的交易中有放回抽样，复利成权益路径，回报分位数、盈利概率、清零概率与尾部风险。
2. **不重跑回测**：只读该次运行已有的交易，使分布锚定在被考察的那份结果上。也因此不需要行情数据，接口是纯计算。
3. **方法在响应里自述**：`method = "trade_level_iid_bootstrap"`。这是对历史的再抽样，不是价格模型也不是预测，消费方不应把它当成预测。
4. **复利语义写清楚**：一笔 `+100` 在 10,000 本金上视为 +1%，路径是这些收益率按抽到的顺序累乘。（早期草稿写成 `prev * (1 + pnl/prev)`，代数上退化为 `prev + pnl`，即完全不复利——已修正并用「只有盈利交易时收益必须等于各笔收益率的乘积」的测试锁死。）
5. **样本过小必须自曝**：观测交易少于 `MIN_TRADES_FOR_CONFIDENCE = 20` 时写入 `warnings`。只交易过 2 次的策略，其分布就是基于 2 个观测构建的，报告不能装作有精度。
6. `runs` 上限 `MAX_RUNS = 5000`，超出 → 422；相同 `seed` 必然得到相同分布。
7. 年化周期取自该次回测实际使用的数据集（`MarketDataSeries.timeframe`），**不接受调用方声明**——`BacktestRun` 没有 timeframe 列，让调用方传入只会让 Sharpe 与实际不一致。
8. 每次运行写审计事件 `monte_carlo_completed`。

**理由**：交易级 bootstrap 的假设少、成本可控（毫秒级，适合 NAS 同步请求），并且直接回答「这个结果有多脆弱」。它明确承认低估连续亏损（交易并非独立），这一点写进 `docs/22` 而不是藏在实现里。

## ADR-044：可选监控写入失败不得破坏主流程

**背景**：回测成功后会记录一条 `resource_events` 资源事件。`record_resource_event` 内部 `db.flush()`，一旦失败（例如 SQLite 下 `resource_events.id` 为 `BIGINT` 不自增而触发 NOT NULL），会话进入 rollback-pending。原代码 `except Exception: logger.warning(...)` **吞掉异常却不回滚**，于是整个请求随后抛 `PendingRollbackError`，一次本已完成的回测被报成 500。

**决策**：

1. 资源事件的写入**移到回测提交之后**，并拥有自己的提交/回滚边界。
2. 失败时显式 `db.rollback()`，把影响面限制在那一条监控记录上。
3. 提交先行的意义：rollback 最多只丢弃监控行，**永远不可能丢弃回测结果**（原先若直接在结果写入前 rollback，会连本次运行一起丢掉）。
4. 新增回归测试：monkeypatch 让 `record_resource_event` 抛错，断言回测仍返回 200 `completed`，且**后续请求的会话依然可用**。

**理由**：可观测性是可选装饰，不能成为关键路径的故障点。原实现的失败模式恰好相反：最不重要的一次写入决定了整个请求的成败。

**已知的独立问题**：`alembic/versions/0004_resource_monitor_tables.py` 用裸 `sa.BigInteger()` 建 `resource_events.id`，在 SQLite 上 `BIGINT PRIMARY KEY` 不自增（模型侧 `_BigIntegerPK` 用 `with_variant(Integer, "sqlite")`，但迁移没有），因此**纯 SQLite 环境**下资源事件必然写入失败；生产是 PostgreSQL，未触发。ADR-044 让该失败不再扩散。迁移一旦执行便不应改写（会与已存在的库产生分叉），故不动 0004。

## ADR-045：仓位管理加入风险型模式（docs/23）

**背景**：docs/15 把 Portfolio-aware position sizing 列为 P2。引擎此前只有一种下注方式：投入当前现金的固定比例（`risk.max_position_pct`）。这使**止损距离与风险脱钩**——止损放宽一倍，单笔亏损就放大一倍。对「每笔风险大致恒定」的研究意图来说这是结构性缺陷。

**决策**：

1. `execution.sizing` 新增 `SizingSpec`，`mode` 取 `fixed_fraction`（默认）/ `risk_per_trade` / `atr_risk`。
2. 定量逻辑收敛到**唯一入口** `_position_quantity`，market 入场与限价/停止挂单两条路径共用（此前两处各写一遍相同的 `budget/fill`，任何改动都要改两遍，是重复代码的典型风险点）。
3. **默认 `fixed_fraction`，既有数字不变**：只新增字段、不改原路径算术，由既有 backtest / examples / sensitivity 套件全绿保证，并显式断言「默认 = 显式 fixed_fraction」。
4. 风险型模式统一受**现金上限**约束 `qty ≤ cash × 0.999 / fill`。研究引擎不是保证金账户，无论风险参数多激进，名义敞口不得超过可用现金。（顺带修掉一个既有隐患：`max_position_pct = 1.0` 时旧代码按满仓买入后再扣手续费，现金会变成负数。）
5. **缺失止损距离时回退为 `fixed_fraction`，而不是跳过交易**：静默不交易会让回测结果无法解释。
6. 验证分两层：单元层精确断言风险算术；集成层断言**文档承诺的意图**——止损距离减半则数量变大，**且**止损出场亏损仍≈风险预算（后者才排除「买得多是因为亏得更多」）。
7. `execution_model_json` 因此包含 `sizing`，升级后重跑同一策略 `result_hash` 会变；历史回测记录保存的是当时快照，不受影响。这一点写进 `docs/23`。

**理由**：风险型仓位是让「止损宽度」与「单笔风险」解耦的唯一办法，也是组合级风险管理的先决条件。把它做成默认不变的可选模式，避免破坏已冻结的可复现性基线（ADR-037 §4）。

## ADR-046：覆盖合并必须经过校验（`merge_spec_overrides`）

**背景**：`model_copy(update=...)` **不做校验**。当被覆盖的字段是嵌套模型时，
`execution.model_copy(update={"sizing": {...}})` 会把一个普通 `dict` 直接放进
`execution.sizing`。引擎随后 `getattr(sizing, "mode", "fixed_fraction")` 在 dict 上取不到
属性，于是**静默回退到默认仓位模式** —— 调用方以为覆盖生效了，其实没有。

这个坑在 ADR-045 引入 `sizing`（`execution` 里第一个嵌套模型）之前一直是休眠的：
此前 `execution_overrides` 的字段都是标量，dict 与模型没有区别。
实测确认：`model_copy` 后 `type(spec.execution.sizing)` 是 `dict`，`sizing.mode` 为 `None`。

**决策**：

1. 新增 `merge_spec_overrides(spec, overrides)` 作为**唯一**的覆盖合并入口：
   把 spec 转成纯数据、按 key 浅合并（值本身是 dict 时向下合并一层）、再
   `StrategySpec.model_validate` 重新校验。嵌套字段因此一定是模型实例。
2. `POST /backtests` 的 `execution_overrides` 与集成引擎都改用它。
3. 新增回归测试：通过 API 传 `{"sizing": {"mode": "risk_per_trade", ...}}`，断言
   `execution_model.sizing.mode` 被保留为 `risk_per_trade`，**且** `result_hash` 与不带
   覆盖时不同（即覆盖真的参与了计算）。

**理由**：静默忽略用户显式给出的参数，比报错更糟——它让「我调过了」变成一句假话。
凡是可能含嵌套对象的覆盖，都必须走校验路径。

## ADR-047：策略集成采用「决策加权投票 + 单组合执行」（docs/24）

**背景**：docs/15 把 Strategy Ensemble 列为 P2。

**决策**：

1. 新增 `app/research/ensemble.py`：各成员在同一份行情上各自评估规则，得到逐 bar 决策；
   逐 bar 加权投票；合并后的决策序列驱动**一个**组合执行。
2. **不拼接各成员的成交记录**：那会得到一个同时持有多笔仓位的组合，而本引擎是单仓位、
   单一现金账户，无法诚实执行。投票产出的是**一条**决策序列。
3. **票数判定用严格大于**：`sum(w_i × flag_i) > vote_threshold`，有效范围 `[0, 1)`。
   等权两成员时每票恰好 0.5，若用 `>= 0.5` 则**单个成员即可单独通过**「多数」判定，
   集成退化为各成员的并集（实测：两个交集为 0 的成员产生了 11 个入场）。多数 = 超过一半。
4. 权重归一化后记录，`[2,1]` 与 `[4,2]` 等价。
5. `entry_bars`（票数过阈值的 bar 数）与 `entries_taken`（真正开仓次数）**分开报告**：
   持仓期间的重复触发不是新仓位，混为一谈会高估信号质量；只有后者与成员的入场数可比。
6. 组合的成本/资金/仓位管理默认继承第一个成员并可用 `execution_overrides` 覆盖
   （它们属于组合，不属于单个成员）；风险线取第一个定义了它的成员 —— 一个组合只能带
   一个止损，逐 bar 混用各成员止损是任意且不可解释的。
7. 成员之间没有共同 bar → 422；预热期部分重叠 → 在共同 bar 上评估并写入 `warnings`。
8. 成员规则引用了该成员特征里没有的列 → **报错**，不静默丢弃该成员（那会误报集成结果）。

**理由**：集成的价值在于「互相认同」，而认同必须用一条可执行的组合去度量，
不能靠拼接互不相容的成交记录来制造一个本引擎无法持有的组合。

## ADR-048：资源监控表主键在 SQLite 上不自增（修正迁移 0006）

**背景**：`0004_resource_monitor` 用裸 `sa.BigInteger()` 建了四张资源表的主键，而 ORM 模型用的是
`BigInteger().with_variant(Integer, "sqlite")`。差别是致命的：SQLite 只有 **`INTEGER PRIMARY KEY`**
才是 rowid 的别名、才会自动生成值，`BIGINT PRIMARY KEY` 不会。于是 SQLite 上每一次资源写入都
`NOT NULL constraint failed: resource_events.id`。在一次请求里它表现为「回测已经跑完、却被报成
HTTP 500」（ADR-044 已让这个失败不再扩散，但没有消除根因）。

生产是 PostgreSQL，`BIGINT`/`BIGSERIAL` 自增正常 —— 已在 NAS 上核实：`/resources/events` 能读到
`backtest:8` 的记录。因此**这不是线上故障，而是纯 SQLite 环境（本项目的开发/验证路径）的故障**。

**决策**：

1. **不改写 `0004`**。它已在生产应用；改写已执行的 revision 会让历史分叉。
2. 新增 `0006_resource_pk_sqlite`：**仅在 SQLite 上**用 `batch_alter_table` 把四张表的主键改为
   `BigInteger().with_variant(Integer, "sqlite")`（SQLAlchemy 在 SQLite 上会重建表并搬运数据）。
   非 SQLite 直接 return，PostgreSQL 完全不受影响。
3. 新增 `tests/test_migrations_sqlite.py`：对真实 SQLite 跑**完整的 alembic 链**，然后
   **不给 id** 插入 `ResourceEvent`（即当初失败的那一步），并断言四张表都能自增、且
   `resource_events.id` 的 DDL 类型确实是 `INTEGER`（行为与根因一起锁死）。

**理由**：这是「ORM 与迁移对同一列给出不同类型」这类漂移的典型案例——只要两条路径分别定义
schema，就可能不一致，而且只在其中一个方言上暴露。所以要测**真实迁移链 + 真实插入**，
而不是只测 `Base.metadata.create_all()`（后者用模型定义建表，永远看不到迁移的问题）。

**注意**：迁移用 `existing_type=BigInteger` 声明旧类型，因此对 0006 之前建立的 SQLite 库也能执行；
若在旧库上遇到异常，删除该 SQLite 文件重新迁移即可（它是开发/验证用临时库，无需要保留的数据）。
## ADR-049：集成报告必须给出「认同归因」，成员对比必须标注数据窗口（docs/24）

**背景**：集成只报组合的收益/回撤和 `entries_taken` 时，两种完全不同的情形看起来一样：

1. 成员高度一致，组合只在大家都有把握时开仓（这才是集成的价值）；
2. 成员几乎从不同时开仓，组合**几乎没交易**，于是收益曲线平坦、回撤极小，看起来像「稳健」。

同时前端的成员对比表把「成员各自最近一次单策略回测」的收益/回撤/夏普和组合并排显示，而那些
回测的标的、周期、数据集版本、成本模型和初始资金都可能与本次集成不同 —— 差异可能只来自数据
窗口，却被读成策略优劣。

**决策**：

1. 引擎在集成的**共同 bar** 上计算归因：`agreement.signalled_bars`（至少一个成员想入场）、
   `agreement.solo_signalled_bars`（只有单个成员想入场、因而被否决）、
   `agreement.entry_support_rate` / `exit_support_rate`；每个成员给出
   `entry_votes` / `entry_agreed` / `solo_entries` / `entry_support_rate` / `vote_agreement_rate`。
   全部在集成自己的数据与成本假设下算出，不与成员的历史回测混合。
2. 响应返回 `dataset_version_id` / `symbol` / `timeframe`（以及此前被 Pydantic 静默丢弃的
   `engine_version` / `feature_version`），让调用方判断成员的历史回测是否可比。
3. 前端对比表增加「认同/否决」列，对数据集不同的成员行打标记并给出整体警告；
   零交易时明确说明「这是没交易，不是稳健」，并列出被否决的信号根数。

**理由**：集成的核心主张是「互相认同」，那就必须把「认同了多少、否决了多少」当作一等输出，
否则用户无法区分分散化收益和「没交易」。同理，跨数据窗口的数字并排显示必须自带警示，否则
工具会主动制造错误结论。

**附带修正**：做空路径的退出判定 `votes("exit_short") >= vote_threshold` 是修 `>=` 问题时漏掉的
一处，与同函数明确论证的严格 `>` 自相矛盾，只在所有成员都允许做空时可达。已改为 `>`。

**测试**：`test_ensemble.py` 增加三条（成员全同时 `entry_support_rate == 1.0` 且 `solo_entries == 0`；
两个同策略成员 + 一个异策略成员时，被否决方的 `solo_entries == entry_bars` 而组合仍按多数方开仓；
支持率按并集而非交集计算）；`test_ensemble_api.py` 增加数据集身份与归因自洽断言；
`test_api.py` 断言回测摘要返回 `symbol` / `timeframe`。

## ADR-050：成员对比由服务端按同一模拟器算出（`member_runs`，docs/24）

**背景**：ADR-049 让前端**标注**了成员历史回测与集成的不可比（不同数据集/成本模型/资金），但那
只是「据实说明」，没有解决问题：用户仍然拿不到一个同口径的成员对照，于是「集成比单干好吗」这个
集成最该回答的问题，无法用界面上的数字回答。可用的回退办法（要求用户先在当前标的/周期上跑一次
该成员的回测）把口径一致性变成用户的义务，且它依赖 UI 状态，无法审计。

**决策**：

1. 把逐 bar 模拟循环从 `run_ensemble` 抽成 `_simulate(*, frame, index, entry_long, exit_long,
   entry_short, exit_short, stop_long, target_long, stop_short, target_short, execution,
   fee_rate, slippage_rate, max_position_pct, capital, timeframe, strategy_version)`，返回
   `_SimResult(equity_curve, trades, metrics, final_equity, entries_taken, in_position)`。
   组合与成员跑分**走同一个函数**，因此「可比」是结构保证，不是约定。
2. 响应新增 `member_runs`（与 `members` 等长同序）：每项 `label` / `weight` /
   `initial_capital`（= 组合初始资金 × 归一化权重，各项之和恰为组合初始资金）/ `final_equity` /
   `entries_taken` / `metrics` / `equity_curve`。每个成员用**自己那份资金**独立模拟。
3. 成员跑分用**各成员自己的**风控线与规则，不用组合继承的那一套（否则「成员单干」名不副实）。
4. 语义边界写进文档：成员曲线是**独立账户**的模拟，不是「组合同时持有多笔仓位」——引擎是单
   仓位单现金账户。它回答「投票有没有比单干更好」，不回答「某成员在投票运行时贡献了多少」。
5. `ENSEMBLE_VERSION` 与返回的 `engine_version` 由 `1.0.0` / `ensemble-1.0.0` 提升为
   `1.1.0` / `ensemble-1.1.0`：响应形状变了，调用方需要能分辨。
6. 因为响应从几十 KB 涨到 163 KB 级（每人一条曲线），加入 `GZipMiddleware`。
7. 前端对比表成员列改为优先取 `member_runs`；权益曲线由单线 `EquityChart` 改为
   `MultiLineChart`，叠加组合（加粗）与各成员（最多 4 条，其余见表格），并删除 v1.3.9 那个
   「不可比」警告（同口径跑分存在时它已无意义，只在回退到历史回测时标注）。

**理由**：把一个「只存在于用户纪律里」的口径一致性要求，变成服务端一次调用就成立的**结构**保证。
成员 solo run 复用组合的执行路径，也顺带消除了「成员数字与组合数字来自两套代码」这类漂移风险。

**测试**：`test_ensemble.py` 四条（单成员且权重 1 时 solo 与组合逐项相同；`initial_capital` 按权重
分配且和为总额；顺序与可复现；成员用自己的止损线）；`test_ensemble_api.py` 三条（同 bar 跑分的
形状与自洽、组合收益落在成员收益区间内、单成员时与组合一致）+ 一条 gzip 行为测试。

## ADR-051：日志脱敏必须按记录形态分流（普通记录整条覆盖，`uvicorn.access` 逐参数改写；docs/14）

**背景**：v1.4.0 的 release 流程在「Smoke test the released images」阶段出现
`quantlab-api | --- Logging error ---`，栈底是
`uvicorn/logging.py:99 formatMessage` → `ValueError: not enough values to unpack (expected 5, got 0)`。
根因在自家代码：`RedactingFilter`（`backend/app/core/logging.py`）为了脱敏会把整条消息预先渲染后写入
`record.msg` 并**清空 `record.args`**；而它同时挂在 `uvicorn.access` 上，uvicorn 的
`AccessFormatter.formatMessage` 恰恰要把 `record.args` 解包成
`(client_addr, method, full_path, http_version, status_code)` 五元组。args 被清空后格式化必然抛异常，
于是**每个请求的访问日志整条丢失**，只在 stderr 留下 `--- Logging error ---`。单元测试没挡住它，因为
`test_logging_redact.py` 里那条测试只断言 `filter(record) is True`，没有任何断言覆盖 args 的保留。

**决策**：

1. `RedactingFilter` 按 `record.name` 分流。默认路径不变：`record.msg = _redact(record.getMessage())`、
   `record.args = ()`（因为消息已渲染完，再渲染一次会把 `%s` 原样打出来，并让消息里出现的裸 `%`
   变成格式说明符）。
2. `structured_arg_loggers = frozenset({"uvicorn.access"})` 的**日志器不走覆盖路径**，而是把
   `record.args` 里的字符串**逐个** `_redact`，元组的长度与顺序保持不变，uvicorn 的
   `AccessFormatter` 仍能正常解包。这样既修好崩溃，又不会为了修崩溃而放弃脱敏——查询串里的凭据
   （`?token=...`、`?api_key=...`）依然会被抹成 `***`。
3. 新增三条回归测试：`test_uvicorn_access_record_keeps_a_five_tuple_of_args`（过滤器后仍能用一个真的
   `AccessFormatter` 渲染出请求行，且查询串里的密钥变成 `***`）、
   `test_filter_does_not_crash_the_real_access_formatter_path`（按 `logging.Handler.handle` 的真实
   顺序「先过滤器后格式化」走一遍，复现旧行为会抛的那个 `ValueError`）、
   `test_filter_keeps_resolved_message_from_double_formatting`（普通记录只渲染一次，`%s` 不会原样输出）。
4. docs/14 §1 补上这段实现说明与「为什么访问日志要特殊处理」，让下一次改动不会把它当成冗余特例删掉。

**理由**：日志脱敏是安全需求，访问日志可用性是运维需求，二者在 `record.args` 上冲突；按记录形态分流
是唯一能同时满足两者的做法。修复代价很小（一个启动期过滤器），但如果只做「跳过 `uvicorn.access`」的
最小修复，就会留下「查询串里的凭据进访问日志」这个安全缺口。

**测试**：`backend/tests/test_logging_redact.py` 三轮（普通记录渲染一次、访问记录保持五元组且脱敏、
按真实 handler 顺序不抛异常），全套 503 passed / 4 skipped。

## ADR-052：集成的唯一旋钮必须能看到台阶（投票阈值扫描，docs/24 §7）

**背景**：`vote_threshold` 是集成**唯一的可调项**，而它此前只能一次跑一个值——用户只能猜，
且猜错的方式是隐形的。更根本的问题是它的形状：加权票是成员权重之和，因此票数**只能落在联盟
总数**（coalition totals）上。两成员等权时可能票数只有 `0 / 0.5 / 1.0`，于是**阈值 0.4 与
0.5 的行为完全相同**——两者等待的都是「两个成员都同意」。这个曲面是**阶梯**而不是曲线。一次
跑一个值会隐藏「哪些联盟被跳过了」，而用户在两个相邻票数之间细调阈值时，会误以为自己在做
连续的权衡（那是把噪声当成信号）。

**决策**：

1. 新增 `POST /research/ensemble/sweep`：同一批成员在多个阈值上各跑一次，返回每个阈值下的
   指标与开仓情况（docs/24 §7）。省略 `thresholds` 时服务端只用**答案会发生变化**的阈值，
   即 `possible_votes` 中严格落在 `(0, 1)` 内的值；显式给出阈值时也校验（`[0, 1)`、不重复、
   ≤ 12 个）。
2. 每个扫描点带上 `effective_vote`（`possible_votes` 中第一个**严格大于**该阈值的票数）与
   共享的 `possible_votes`。这是把「阶梯」讲清楚的字段：相邻两点 `effective_vote` 相同就说明
   它们之间没有台阶。`effective_vote` 的含义是「第一个能过线的票数」，不是「需要几个成员」
   （三成员各 `1/3` 时阈值 `0.3` 的 `effective_vote` 是 `1/3`）。
3. **扫描点必须与单阈值端点逐项一致**。为此把 `run_ensemble` 抽成
   `_prepare_ensemble`（阈值无关：成员校验、特征、成员决策、加权票求和、成本/风控继承）+ 
   `_run_vote`（单阈值：决策合并、归因、一次 `_simulate`），`run_ensemble` 与
   `run_ensemble_sweep` 都走这两个函数。这样「可比」是**结构保证**而不是纪律：特征只评估一次
   并复用，扫描点不可能与用户随后直接跑的同阈值结果不一致。
4. 扫描端点**不返回权益曲线、不返回 `member_runs`**：曲线不在本端点契约内，12 个点各带一条
   曲线会让响应体积失控。要看曲线就用 `POST /research/ensemble`。
5. 定位为**描述性**：与参数敏感性扫描（docs/21）同族，只描述形状，**绝不推荐阈值**。跨台阶
   比较收益并挑最高的那个，就是在同一个数据集上做选择，属于过拟合。
6. `ENSEMBLE_VERSION` 由 `1.1.0` 提升为 `1.2.0`；同时把 `engine_version` 从硬编码字符串改为
   派生值 `f"ensemble-{ENSEMBLE_VERSION}"`（原先 `"ensemble-1.1.0"` 与常量是两个会各自漂移的
   事实来源）。
7. 前端新增 `ThresholdSweepChart.vue`（x 轴阈值、左轴总收益 %、右轴交易数虚线、0% 参考线），
   收益线用 `step: 'end'` 绘制——值确实保持到下一个台阶，不能在评估过的阈值之间插值；并在
   开仓次数在所有阈值下相同时明说「这个旋钮不改变结果，别在这里调参」。

**理由**：单阈值运行回答的是「这个设置下如何」，而调参需要回答的是「这个旋钮能改变什么、
在哪一段改变」。因为票数只能取有限个联盟总数，后一个问题的答案是一个有限集合，就应该一次
呈现出来，而不是让用户用一串单独的请求去逼近。把它做成描述性而非推荐性，是因为阈值选择本身
就是一次在数据集上的选择：报告形状，把决定留给用户。

**测试**：`backend/tests/test_ensemble_sweep.py` 16 条（扫描点与直接运行逐项一致、联盟总数、
`effective_vote` 指向正确的联盟、权重改变联盟总数、阈值升高时开仓单调不增、可复现、4 条参数
校验、单成员无内部边界）；`backend/tests/test_ensemble_sweep_api.py` 10 条（与单阈值端点逐项
一致、默认阈值即联盟总数、成员权重份额、单调性、审计事件、422/404 分支）。

---

## ADR-053：扫描的阈值网格必须精确，且必须总能被评估（`max_thresholds`，docs/24 §7）

**状态**：已采纳（v1.4.3）

**背景**：ADR-052 把投票阈值扫描做成端点后，用探针（`backend/scripts/probe_sweep_grid.py`）对
各种成员权重组合检查默认网格，发现三个各自独立的缺陷，其中第二个是**可见故障**：

1. **浮点漂移**。`_coalition_totals` 在集合扩张的循环里对每个部分和取整，误差逐级累积。十二个
   等权成员的联盟总数实测为 `[0.0, 0.083333, 0.166666, 0.249999, 0.333332, 0.416665, 0.499998,
   0.583331, 0.666664, 0.749997, 0.83333, 0.916663, 0.999996]`——本应是 `1/12` 的整数倍，
   `0.25 / 0.5 / 0.75` 全部偏了一位。
2. **默认网格超过上限**。因为漂移出的最大值是 `0.999996 < 1.0`，它被当成一个内部边界，默认
   网格变成 **13 点**，而 `MAX_SWEEP_THRESHOLDS = 12` → `ValueError` → 端点 **422**。十二个
   等权成员正是 `MAX_MEMBERS = 12`，也就是 **API 允许的最宽集成**：文档化的「留空 = 自动」
   默认路径对它完全不可用。其它超限组合：权重 1..5（19 点）、1..6（22 点）、6 个互异质数
   （41 点）。上限选 12 时，连「五个成员、权重 1..5」这种正常组合都跑不了。
3. **报告与模拟不一致**。`possible_votes` 是取整到六位的值，而 `_run_vote` 用**原始** float
   票数与阈值比较。十二等权成员、默认网格点 `0.083333`：单个成员的原始票数 `0.0833333333`
   大于 `0.083333` 为真 → 模拟让**一个**成员通过；而 `_effective_vote` 在取整后的总数里找
   「第一个严格大于 `0.083333` 的值」得到 `0.166667` → **报告说需要两个成员**。默认网格上大约
   一半的格子会偏一个联盟。

**决策**：

1. **联盟总数只在求和结束后取整一次**，并对取整结果去重（`sorted({round(v, 6) for v in totals})`
   ——`round` 后仍可能有两个原始总数合并）。循环内不再取整。
2. **上限从 12 提到 64**。它是工作量上限而不是口味：精确网格就是联盟边界数，等权最宽集成是
   12 点，但权重不等时它随不同子集和的数量增长，64 覆盖所有现实成员组合，同时把一次扫描限制
   在 64 次模拟。同时新增 `_within_sweep_budget`，把「默认网格仍放不下」变成**可操作的报错**：
   说明有多少个联盟边界、上限是多少、请显式传 `thresholds` 挑你关心的边界。
3. **比较发生在发布的精度上**。新增 `_clears(votes, vote_threshold)` =
   `np.round(votes, 6) > round(vote_threshold, 6)`，`_run_vote` 的四处比较全部改走它，
   `_effective_vote` 用同一个 `limit`。去掉原先设想的 `1e-9` epsilon：它比 `1e-6` 的发布步长
   小三个数量级，实测无效。
4. **响应新增 `max_thresholds`**。上限必须随响应发布，否则每个客户端都要把它抄一份（它会变
   ——v1.4.2 时是 12）。`scripts/Test-EnsembleAttribution.ps1` 里那个硬编码的「给 13 个阈值
   必须被拒绝」也改成从响应派生。
5. **超限是 422 而不是截断**。截断会悄悄丢掉台阶，而这张图存在的意义正是「哪些台阶被跳过」。

**理由**：这三个缺陷指向同一个根因——**网格的生成、报告的精度、模拟的判定是三套各自运行的
算术**，只要它们能彼此不一致，扫描就在描述一个用户无法复现的集成（这正是 ADR-052 决策 3 要
防止的事，但只在「同阈值」这一个方向防住了）。第 2 条更严重：它让一个文档化的默认路径对
最大规模输入直接报错，而且报错信息（「最多 12 个」）对调用方没有任何帮助——用户不知道自己是
哪个数字超了，也不知道该怎么办。

**测试**：`backend/tests/test_ensemble_sweep.py` 新增
`test_coalition_totals_do_not_drift_with_member_count`、
`test_the_widest_ensemble_can_still_use_the_default_grid`（12 成员默认网格回归，并断言每个点
自洽、12 个 `effective_vote` 互不相同、开仓单调不增）、
`test_a_default_grid_too_large_to_evaluate_says_what_to_do`、
`test_a_rounded_boundary_is_still_a_boundary`；`backend/tests/test_ensemble_sweep_api.py` 新增
`test_sweep_reports_its_threshold_budget`、`test_the_widest_ensemble_can_use_its_default_grid`、
`test_an_unaffordable_default_grid_is_a_422_that_says_what_to_do`。

---

## ADR-054：引擎的警告必须跟结果一起落库（`warnings_json`，docs/12）

**背景**：`run_backtest` 一直在报告警告，但报告完就丢了。

- `POST /backtests` 会返回 `warnings`，例如
  `ignored unknown parameter override(s): typo_period; this strategy declares: fast_period, slow_period`，
  或 `only 400 bars available, warm-up needs 900`。
- `GET /backtests/{id}` 却**硬编码**返回 `warnings: []`（`backtests.py` 的 `_to_out(..., [])`）。
  警告只在创建响应里存在过一次，刷新页面就消失。
- 前端从来没有渲染过它——`BacktestView.vue` 只渲染了 Monte Carlo 与集成的警告。

两条警告都真实可达：`create_backtest` 只校验 `len(frame) >= 60`，所以任何「数据够 60 根但不够
策略 warm-up」的组合都会走到第二条；`BacktestCreate.parameters` 接受任意键，所以第一条对任何
打错参数的调用都可达。第二条尤其危险——实测 `slow_period=900` 跑在 400 根上会返回
`number_of_trades = 0`、`total_return = 0`，**一个毫无意义的结果被当成正常完成的结果返回**，
而唯一的提示在刷新后就没了。

**决策**：

1. **警告跟结果一起存**。`backtest_results` 新增 `warnings_json`（JSON，`default=list`），
   迁移 `0007_backtest_result_warnings` 用 `server_default='[]'` 补既有行。
   理由：读一次回测有两条路径（创建响应 / `GET`），任何只挂在其中一条上的信息都不可靠。
2. **`GET /backtests/{id}` 返回存下来的那份**，不再硬编码空列表。
3. **前端渲染它**。单策略回测的详情面板在四个指标卡**上方**显示警告（先看到提醒再看数字），
   扫描面板也显示自己的 `warnings`。集成与 Monte Carlo 早已渲染，本版只是把缺口补齐。
4. **不因为「warm-up 长于数据」就拒绝请求**。引擎诚实地评估它能评估的部分，警告才是正确的
   信号；把「数据太短」变成 422 会误伤那种「我就想看看窗口不够时会发生什么」的调用，而且
   没有任何阈值能划清界限（warm-up 与数据长度的关系是策略属性，不是输入合法性）。

**理由**：不落库就等于把「这次结果不可信」这件事变成了**一次性提示**，而一次性提示的价值恰好
在用户最需要它的时候（事后回看某次回测）归零。警告属于**不可变结果载荷**的一部分，和
`metrics_json`、`result_hash` 同级。

**测试**：`backend/tests/test_api.py` 新增 `test_backtest_warnings_survive_a_reload`（同一份
列表，逐项相等）、`test_a_warm_up_longer_than_the_data_is_persisted`（含 `number_of_trades == 0`）、
`test_a_clean_run_reports_no_warnings`。

## ADR-055：没跑起来的网格点不能赢排名（`warmup_unmet`，docs/21 §6）

**背景**：`run_backtest` 在整段数据都落在指标预热期内时会发一条警告（ADR-054），
此时策略一根可评估的 bar 都没有，决策全是 `False`，指标是**扁平的一串 0**。

敏感性聚合只按 `objective is not None` 筛选，于是这些点**同时污染三处**：

1. `best`：0 永远打败真正亏钱的有效点，所以「没跑起来」看起来永远比「跑了但亏」好。
   实测（DEMO-AAPL 400 根，EMA `trend_period` 扫 `[100, 300, 500, 900]`，`metric=total_return`）：
   100 → −0.1116（12 笔）、300 → −0.1249（6 笔）、500 / 900 → **0（0 笔）**，
   而报告的 `best` 是 `trend_period=500`。
2. `summary`：均值、极差、`positive_ratio` 被 0 稀释。
3. `stable`：`_is_stable` 要求全为正或全为负，0 既不正也不负，于是一次**一致亏损**被报成
   「符号翻转」（同一实测里 `stable=False`，而两个真正测到的点都是负的）。

三处都是同一个错误：**把「没测量」当成了「测量到 0」**。它比缺数据更危险，因为它看起来是个结论。

**决策**：

1. **引擎标记**：`BacktestResult` 新增 `warmup_unmet: bool = False`。该标志**刻意不进
   `as_dict()`** —— HTTP 面用警告字符串表达它（ADR-054）—— 但研究聚合器必须能据此分支。
2. **聚合分拣**：`sensitivity.py` 把点分成 `defined`（objective 非空）与 `runnable`
   （`defined` 且非 `warmup_unmet`）。`best` / `worst` / `summary` / `stable` **全部只看
   `runnable`**；一个点都没测到时 `best` / `worst` / `stable` 都是 `null`。
3. **如实报告**：响应新增 `ranked_points`、`warmup_unmet_points` 与 `warnings`，说明有多少点
   被排除、以及是不是一个点都没测到。`evaluated_points` 语义不变（仍含未测得点），所以
   `ranked_points + 未定义点数 + warmup_unmet_points == grid_points`。
4. **前端不把未测得点画成曲线**：表格里标为「整段在预热期内」、指标显示 `—`，图表只画测得点，
   `ranked_points` 取代 `evaluated_points` 出现在统计卡片上。理由：一条躺在 0 上的线看起来像
   被测量过。
5. **`SENSITIVITY_VERSION` 由 `1.0.0` 提升为 `1.1.0`**：点的形状（新增 `warmup_unmet`）与
   响应形状都变了。

**理由**：敏感性分析的整个卖点就是「这片曲面的形状值得信任」。允许一个从未运行的格子参与排序，
工具就会**主动**把一个 bug 报告成一种优点 —— 这与 docs/21 §3 记的那次 ADR-040 事故是同一类
错误（把 bug 报告成优点），所以处理方式也一致：结构上不可能发生，而不是文档里提醒一句。

**测试**：`backend/tests/test_backtest.py` 新增 `test_a_run_that_never_left_its_warm_up_is_flagged`
（正常运行为 `False`、预热期未走完为 `True`、警告存在、且该键不出现在 `as_dict()` 里）；
`backend/tests/test_sensitivity_api.py` 新增 `test_points_that_never_ran_cannot_win_the_ranking`
（`ranked_points == 2` / `warmup_unmet_points == 2` / `best` 是测得点而非旧的 500 /
`stable is True` 而旧代码是 `False`）。

## ADR-056：分析必须说明它读了多少、跳过了什么、为什么（coverage，docs/05 §4.1）

**背景**：GitHub 是不可信输入（docs/05 §4），导入器的人工前提是**先审阅 findings 与草案 DSL**。
可整个契约里没有一句要求报告**覆盖面**，实测（`backend/scripts/probe_github_coverage.py`，
20 个 `.py` + 10 个 `.md`、一个文件超字节上限、一个下载失败）三处缺口：

1. **`skipped_reason` 算出来又丢掉**：`github_client.py` 为超限文件写 `"file too large"`、
   为下载失败写异常文本，而 `extract.py:505` 只把 `repo_file.path` 追加进 `files_skipped`，
   `GithubAnalyzeOut.files_skipped` 只有路径 —— 全仓没有任何消费者（`grep skipped_reason` 只有
   生产者）。用户看得到"跳过了 2 个文件"，看不到"为什么"。
2. **超出抓取上限的文件整批消失**：`github_client.py:249-252` 截断候选后只在 `logger.info`
   里报数量，从不出现在响应里。cap=5 的实测：返回 5 条、25 个候选**没有任何痕迹**；
   `backend/tests/test_importer.py:270 test_fetch_respects_cap_and_prefers_python` 恰好把这种
   静默丢弃固化成了断言（`assert len(files) == 5`）。
3. **`files_scanned` 把"登记"当成"分析"**：非 `.py` 文件也进 `files_scanned`
   （`extract.py` 自己的 docstring 写着「non-Python files are inventoried, not parsed」），
   而 UI 只显示 `files_scanned.length`。cap=22 的实测：`files_scanned = 20`，其中只有 18 个真被解析。

后果是报告**高估**自己的覆盖面，而且高估的正是安全相关的量：没读到的文件就是没人审阅的代码。
更糟的是无人值守路径：watcher 会**自动导入**，此前从不完整读取里同样自动导入。

**决策**：

1. **抓取阶段一次算清**：`github_client.py` 新增 `@dataclass(frozen=True) FetchCoverage`
   （候选数、`.py` 候选数、尝试数、下载成功数、跳过数、跳过 `.py` 数、未尝试数、未尝试 `.py` 数、
   cap），派生 `complete`（`downloaded_files == candidate_files`）与 `unread_python_files`
   （未尝试 `.py` + 跳过 `.py`）；`fetch_repository` 由二元组改为返回 `(meta, files, coverage)`，
   强制每个调用方处理它。
2. **分析阶段保留原因**：`extract.py` 新增 `SkippedFile(path, reason)`，`AnalysisResult` 把
   `files_scanned` 拆成 `files_parsed` 与 `files_inventoried`。`ANALYSIS_VERSION` 提升为 `1.1.0`。
3. **一个 `build_coverage()` 给两个调用方用**：端点与 watcher 共用同一段算术与
   `coverage_warnings()`。两套算术迟早会对"这个仓库有没有被完整审阅"给出不同答案，而这个答案
   决定无人值守时能不能导入。
4. **无人值守拒绝从不完整读取中导入**：`check_source` 在 `coverage["unread_python_files"] > 0` 时
   记 `last_import_status = "incomplete"`、写快照
   `extraction_json = {"imported": False, "reason": "incomplete_analysis", "coverage": ..., "warnings": ...}`、
   **不新建策略版本**（变化的规则可能就在没读到的 `.py` 里，导入部分草案等于静默降级策略）。
   只登记不解析的 `.md`/`.json` 不阻断导入 —— 那是常见情况，不是风险。
5. **报告面**：`GithubAnalyzeOut` 新增 `analysis_version` / `coverage`，`files_skipped` 变为
   `[{path, reason}]`，覆盖率结论并入 `warnings`；前端头部改成"读取 X / Y 个候选文件（解析 N 个
   Python、登记 M 个非 Python）"、非完整读取时显示错误色结论、并列出被跳过文件及原因。

**理由**：人工审阅是这道防线的全部，而审阅的前提是知道报告覆盖了多少。把"未尝试的文件数"留在
日志里，等于把最需要人看的东西放在人不会看的地方 —— 与 ADR-055 同一类错误（把缺口报告成正常）。
另外这条缺口直接改变无人值守行为：不完整读取下自动导入，会让一个策略在没有任何人察觉的情况下
被降级。

**测试**：`backend/tests/test_github_watch.py` 新增
`test_unread_python_files_block_an_unattended_import`（状态为 `incomplete`、版本数不变、快照里
`reason == "incomplete_analysis"` 且 `coverage["unread_python_files"] == 5`）与
`test_unread_non_python_files_do_not_block_an_import`（同样不完整但 `.py` 都读到 → 正常导入，
快照仍记录 `not_attempted_files == 10`）；`backend/tests/test_importer.py` 新增
`test_analyze_endpoint_reports_its_coverage`（cap=5 对 14 个候选 → `not_attempted_files == 9`、
`files_parsed` 全是 `.py`、`files_inventoried` 全是 `.md`、警告含 `never fetched`），并改写
`test_analyze_repository_files_skips_non_python`（断言 `files_parsed` / `files_inventoried` /
`files_skipped[0].reason`）与 `test_fetch_respects_cap_and_prefers_python`（断言
`not_attempted_files == 15`、`unread_python_files == 5`）。

## ADR-057：抓取必须有墙钟预算，且必须说实话（`max_seconds`，docs/05 §4.2）

**背景**

ADR-056 让报告能说出"读了多少、跳过了什么"，但抓取本身仍然可以无限期地跑下去，而且它对配置的超时并不诚实：

- `GitHubClient(timeout=15.0)` 的超时只传给了 `get_text`（文件下载）。`get_json` 调 `httpx.get(url, headers=...)` 时**没有传 timeout**，所以 repo / tree / commit 三类请求静默使用 httpx 的默认值（0.28.1 实测 `DEFAULT_TIMEOUT_CONFIG = Timeout(timeout=5.0)`）。配置的旋钮对 3/5 的网络调用无效。
- 更严重的是**没有任何整体预算**：`fetch_repository` 对最多 `cap = min(max_files, 30)` 个文件、每个文件 `FETCH_RETRIES + 1 = 2` 次尝试、每次最多 `self._timeout`，最坏 30 × 2 × 15 = **900s**，再加 3 个 JSON 调用。前端没有请求超时，UI 只写着"分析中…（视网络情况可能需要一两分钟）"。实测（本机直连 GitHub）一次默认 `max_files=12` 的分析耗时 **640s**：用户既不知道它在跑，也不知道它卡住了。单请求超时按定义约束不了循环。
- 这个缺口还把 ADR-056 的安全设计变成了陷阱：watcher 在读到不全时记 `incomplete` 并 `source.current_commit = head`，于是**因网络或预算造成的瞬时缺口被永久记为"已见"**，下一次调度看到 `head == current_commit` 直接返回 `unchanged`，这次更新就再也不会被重试。而只有上限造成的缺口才是"重试也没用"的那一类。

**决策**

1. `get_json` 传 `timeout=self._timeout`：配置的超时必须作用于每一个 HTTP 调用。
2. `GitHubClient.__init__` 新增 `total_budget`（默认 `DEFAULT_FETCH_BUDGET_SECONDS = 120.0`），`fetch_repository` 新增 `max_seconds`（为 `None` 时取客户端预算）；循环在每个候选前检查 `time.monotonic() - started >= budget`，用完即停。
3. 预算用尽时**不假装没发生**：剩余候选进入 `coverage.not_attempted_files`，并新增 `budget_exhausted` 与 `max_seconds` 两个字段；`coverage_warnings()` 据此改说"the fetch stopped after 120s: N candidate file(s) were left unread. Raise the time budget or lower max_files…"。**上限与预算不能共用一句建议**——`max_files` 根本没被碰到时叫用户去调它是错的。
4. 端点 `POST /importer/github/analyze` 暴露 `max_seconds`（`ge=10, le=600`，默认 120），前端给两个输入（最多读取文件数、最长等待秒数），让警告里的建议可执行：此前 UI 既没有 `max_files` 也没有预算，"Raise max_files"是一句界面无法执行的建议。
5. `analysis_version` 升为 `1.2.0`（`coverage` 的字段语义变了）。
6. watcher 只在**非瞬时**缺口时推进 `current_commit`：`transient = bool(coverage["budget_exhausted"])` 时保留旧 commit（下轮重试）、写进快照 `extraction_json["transient"]`。上限造成的结构性缺口仍然标记已见，否则每次调度都会重复几十次注定相同的请求。

**理由**

等待时间不是"体验问题"，而是**报告能否被信任的一部分**：ADR-056 规定报告必须说明它没读到什么，而一个没有预算的抓取会让用户用"它是不是死了"来代替"它读了多少"。第 6 条是同一件事的另一面：一个诚实报告缺口、却把瞬时缺口当永久结论的系统，会把网络抖动变成静默的更新丢失。把两件事都用同一套算术表达（预算写进 coverage、瞬时性写进快照），是为了让"要不要重试""要不要相信这份报告"都有据可依。

**测试**

`backend/tests/test_importer.py` 新增 `test_a_fetch_that_runs_out_of_its_budget_stops_early`（`max_seconds=0` → `attempted_files == 0`、全部候选 not-attempted、`budget_exhausted is True`，且警告含 `stopped after` 而**不含** `the cap is`）与 `test_get_json_honours_the_configured_timeout`（monkeypatch `httpx.get` 断言 `timeout == 2.5`）；`test_fetch_respects_cap_and_prefers_python` 补断言 `budget_exhausted is False` / `max_seconds == DEFAULT_FETCH_BUDGET_SECONDS`；`test_analyze_endpoint_reports_its_coverage` 断言 `analysis_version == "1.2.0"` / `coverage["max_seconds"] == 120` / 不含 `stopped after`。`backend/tests/test_github_watch.py` 新增 `test_a_fetch_that_ran_out_of_time_is_retried_next_run`（`budget_exhausted=True` → `current_commit` 停在 `oldsha`、快照 `transient is True`），并在 `test_unread_python_files_block_an_unattended_import` 里断言上限型缺口的 `transient is False`。`backend/scripts/probe_github_coverage.py` 增加 `max_seconds = 0` 场景，打印预算中断的 coverage 与措辞。

## ADR-058：被监视的来源必须解释自己的状态（`last_import_status` 词表 + 快照可读，docs/05 §7.1）

**背景**

ADR-056/057 让「读了多少、为什么停下」有了算术表达，但用户从 UI 上看到的那一行仍然是 `check_source` 写下的机器字符串。四条实测缺陷（第 4 条是本地真栈验证时抓到的，单元测试没覆盖到）：

1. **三种截然不同的结果共用一个状态**：`check_source` 对「head 与 `current_commit` 相同，什么都没抓」「抓取了新 commit 但没有草案」「抓取了新 commit 且 DSL 未变」三条路径**全部**写 `last_import_status = "checked"`（`backend/app/workers/tasks.py` 原第 252/269/330 行），而它的**返回值**却已经区分 `unchanged` / `no_change`。于是 UI 表格里一行「已检查」既可能是「没有新东西可看」，也可能是「抓取并分析过了，策略没变」——用户无法判断 watcher 是否真的干活，而且状态列直接渲染这个英文枚举（`frontend/src/views/StrategiesView.vue` 原第 764 行）。
2. **原因被写下来，却读不出来**：`GitHubSnapshot.extraction_json` 里存着 `imported` / `reason` / `transient` / `coverage` / `warnings`——即「为什么是这个状态」——但 `GET /importer/github/sources/{id}/snapshots` 只返回 `id/source_id/commit/content_hash/fetched_at`，把 `extraction_json` 丢掉；前端也从未调用过这个端点。用户看到 `incomplete`，没有任何入口能查到漏了什么。人工导入路径更彻底：快照写的是 `extraction_json={}`，连 row 存在的原因都没有。
3. **汇总会各自漂移**：`check_github_sources` 手写 `{"checked", "imported", "incomplete", "error"}` 并逐个 `if` 累加，词表一变就会漏计（`unchanged` / `no_change` 从未出现在汇总里）。
4. **同一个 commit 被看两次会让整轮调度崩掉**：`github_snapshots` 上有 `UniqueConstraint("source_id", "commit", name="uq_github_snapshot")`（`backend/app/domain/models.py:809`），但 `check_source` 的两处写入都是**无条件 insert**，人工导入路径则用 `exists` 检查静默跳过。本地真栈验证（先人工导入 `776c9884e`，再让 watcher 去检查同一个 commit）得到 `sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError) UNIQUE constraint failed: github_snapshots.source_id, github_snapshots.commit`，异常在 `session_scope` 退出时抛出，**整轮 `check_github_sources` 的结果全部回滚**。这个 bug 与 ADR-057 相互放大：瞬时缺口刻意不推进 `current_commit`，于是**下一轮必然重查同一个 commit**，也就必然再次撞上唯一约束——一次网络抖动会让每日调度永久失败，而不是重试成功。


**决策**

1. `check_source` 的返回值与 `source.last_import_status` 使用**同一套词表**：`unchanged`（head 未变，未抓取）/ `no_change`（抓了新 commit，DSL 未变或没有草案）/ `imported` / `incomplete` / `error`。`current_commit` 的推进规则不变：`unchanged` 不动，`no_change`/`imported` 推进，`incomplete` 只有结构性缺口推进（ADR-057）。
2. 旧记录里的 `checked` **不重写、不猜测**：它同时可能是三种含义，UI 明确标为「已检查（旧记录）」，未知值原样显示。
3. `GET /importer/github/sources/{id}/snapshots` 增加 `extraction`（即 `extraction_json`），让「为什么」有出口。
4. 人工导入产生的快照写入 `{"imported": true, "reason": "manual_import"}`，不留空对象。
5. `check_github_sources` 的汇总改为**派生**：`checked` 仍是本轮检查过的来源数，其余按本轮实际产生的结果原样累加（`{"checked": len(sources), **outcomes}`），因此与 `check_source` 的词表不会漂移。
6. UI 把状态翻译为可读文案，并在「详情」里展示最近一条快照：原因、覆盖数字（读取 X / Y、其中 N 个 Python 没被读到）、警告列表，以及瞬时缺口的重试承诺（「下一轮检查会重试」）。详情行标的是**本次检查的 commit**，它可能与表格里的「当前 commit」不同——那不是笔误，正是「这个 commit 还没被标记为已处理」的表现。
7. 两处写入合并为一个 upsert（`backend/app/data/github_source_service.py` 的 `record_snapshot(db, source_id, commit, content_hash, extraction, manifest=None)`）：先按 `(source_id, commit)` 查，存在就**更新** `content_hash` / `manifest_json` / `extraction_json` / `fetched_at`，不存在才 insert。人工导入与 watcher 都走它，因此两条路径不会各自记住不同的规则。**最新一次观察覆盖旧的解释**：快照的职责是说明来源**当前**的状态，而 `(source, commit)` 上的第二行在 schema 上就不存在。

**理由**

「已检查」这种状态对无人值守的监视功能来说是伪信息：用户真正需要知道的是**它做了什么、为什么没导入、下次会不会再试**。这与 ADR-056/057 是同一条线——先保证系统内部的算术诚实（coverage/预算），再保证这份诚实能到达用户（词表/快照/UI）。把状态词表统一在 `check_source` 一处、让汇总从中派生，是为了让「一个状态」只有一个含义。旧值不迁移是刻意的：数据迁移会**编造**历史，而 `checked` 客观上无法区分三种含义，标注为旧记录比假装知道更诚实。

第 7 条是「解释必须写得下去」的前提：一个会抛 `IntegrityError` 的解释通道等于没有通道，而且它恰好会在 ADR-057 设计的重试路径上必现。把 upsert 放进共享服务而不是在两个调用点各写一遍，与 ADR-056 把 coverage 算术共享给端点和 watcher 是同一个理由：两条路径不能各自记住不同的规则。

**测试**

`backend/tests/test_github_watch.py` 新增 `test_a_new_commit_with_an_unchanged_dsl_is_stored_as_no_change`（新 commit + DSL 未变 → 返回并存储 `no_change`、`current_commit` 推进、不新建版本、快照 `imported is False`）、`test_the_task_summary_names_the_statuses_the_run_produced`（monkeypatch `session_scope`，两个 `unchanged` 来源 → 汇总含 `{"checked": 2, "unchanged": 2}`）与 `test_re_checking_a_commit_refreshes_its_snapshot`（先写入同一 commit 的人工导入快照，再让 watcher 得到瞬时缺口 → 不抛异常、快照仍只有一行且 `reason` 更新为 `incomplete_analysis`）；`test_check_source_imports_new_commit` / `test_check_source_unchanged_when_commit_same` / `test_unread_non_python_files_do_not_block_an_import` 增补「存储值等于返回值」的断言。`backend/tests/test_github_sources.py` 的 `test_import_persists_github_source_and_snapshot` 断言快照含 `extraction.imported is True` / `extraction.reason == "manual_import"`，新增 `test_a_second_record_for_the_same_commit_updates_the_first`（两次 `record_snapshot` → 同一行 id、`content_hash` 与 `extraction_json` 取最新）。本地真栈验证：`backend/scripts/probe_watch_status.py` 对真实仓库跑 `check_source`，同一 commit 返回并存储 `unchanged`；把 `current_commit` 改成旧值后真实抓取在 120s 预算处停下 → 返回并存储 `incomplete`、`current_commit` 不推进、对该 commit 的既有快照被刷新为 `incomplete_analysis` + `transient=True` + `coverage=1/179` + `unread_python=138` + `budget_exhausted=True`；真实浏览器（12 条断言 + 详情面板）全通过。


## ADR-059：读到了不等于看懂了（`files_unparsed`，docs/05 §4.3）

**背景**

覆盖率（ADR-056）回答的是"读到了多少"，它不回答"看懂了没有"。ADR-056/057/058 之后报告已经会说自己没读哪些文件、为什么没读、缺口是结构性的还是瞬时的——但仍然会**把读不懂的文件算成读懂了**：

1. `analyze_repository_files` 无条件把每个有内容的 `.py` 写进 `files_parsed`，而 `analyze_python_source` 在解析失败时只是往 `unknowns` 塞一条 `category="unparseable"` 就返回空结果。于是下载成功但根本无法解析的文件贡献为零（没有规则、没有指标、连 unknown 都只算"一条无法映射的构造"），却被计入 `parsed_files`：`coverage["complete"]` 仍为 `true`、`unread_python_files` 为 0、`coverage_warnings()` 返回空列表，`check_source` 照常无人值守导入——规则写在这个文件里的策略被静默降级。
2. 实测（本机真实代码，非推断）：`[RepoFile("good.py", …, "FAST = 5\n"), RepoFile("legacy.py", …, "print 'py2'\n")]` → `analyze_repository_files(...).files_parsed == ['good.py', 'legacy.py']`，`files_unparsed` 这个字段根本不存在，`build_coverage(...)` 报 `parsed_files 2 / complete True`，`coverage_warnings(...)` 返回 `[]`——一句都不说。
3. 解析失败被归成"无法映射的构造"（`unknowns`）是错误归类：那句 `N construct(s) could not be mapped to the DSL and need human review` 是**映射报告**，而这里根本没有可映射的对象。它也曾经是这种文件唯一的痕迹。
4. 解析期异常并不只有 `SyntaxError`。实测 `ast.parse`：NUL 字节 → `SyntaxError: source code string cannot contain null bytes`；Python 2 的 `print 'x'` → `SyntaxError: Missing parentheses in call to 'print'`；20000 层括号 → `SyntaxError: too many nested parentheses`；深缩进 → `IndentationError`（`SyntaxError` 子类）。仓库代码是不可信输入，任何解析期异常都必须变成"这个文件没被看懂"，而不是让整份报告 500。

**决策**

1. `AnalysisResult` 新增 `files_unparsed: list[SkippedFile]`（`{path, reason}`）与 `parse_error: str | None`；`build_coverage()` 新增 `coverage.unparsed_python_files`，`coverage_warnings()` 追加 `N Python file(s) were downloaded but did not parse, so nothing in them was understood and the rules they declare are missing (see files_unparsed).`
2. `analyze_python_source` 捕获 `Exception`（`# noqa: BLE001 - repository code is untrusted input`，不只捕 `SyntaxError`），把 `sanitize_untrusted_text(f"file does not parse as Python ({type(exc).__name__}): {exc}")` 写进 `parse_error` 后返回空结果，**不再**往 `unknowns` 塞 `category="unparseable"`。
3. `analyze_repository_files` 对 `parse_error` 非空的文件 `continue`：不进 `files_parsed`、不 extend 任何 finding 列表，只进 `files_unparsed`。
4. watcher 的拒绝条件改为 `unread_python_files > 0 or unparsed_python_files > 0`（原来的 `not coverage["complete"] and …` 合取是冗余的：有未读 Python 必然不 complete）；快照 `reason` 二选一：`incomplete_analysis`（有没读到的）或 `unparseable_python`（读到了但没看懂），并把 `files_unparsed` 写进 `extraction_json`。
5. 解析失败与上限缺口同属**结构性**缺口：`transient = false`，照常推进 `current_commit`（重读不会让一个解析不了的文件变得可解析，每轮重试只会重复几十次请求）；只有预算/网络型缺口才是瞬时的（ADR-057）。
6. `ANALYSIS_VERSION` 升为 `1.3.0`；`GithubAnalyzeOut` 新增 `files_unparsed`；前端头部行列出"解析 P 个 Python、N 个解析失败"、新增独立的解析失败 `<details>` 表格与警告行，来源详情面板对 `unparseable_python` 给出解释。

**理由**

报告的可信度只能靠它对自己不知道的东西说实话来支撑。`coverage.complete` 断言的是**抓取完整性**，不是理解完整性；把两者混在一起，"已完整读取"就变成了一句假话——而且是在 ADR-056/057/058 刚刚把这句话修准之后，问题往下深了一层：上一版修的是"没读到的文件不算读过"，这一版是"没看懂的文件不算看懂"。

对策略的影响上，"读到了但没看懂"与"根本没读到"是同一件事：规则缺失。但对**重试**的意义完全不同，这才是必须区分 `transient` 的原因：网络/预算造成的缺口重读可能补上，解析失败重读一定补不上。

选择捕获 `Exception` 而不是 `SyntaxError`，是因为触发点属于不可信输入：报告生成器不能因为仓库里有一个畸形文件就整份失败，那等于把"拒绝导入"变成了"看不到报告"。

**测试**

`backend/tests/test_importer.py` 的 `test_unparseable_file_becomes_unknown` 改名为 `test_unparseable_file_is_reported_as_unparsed_not_understood`（断言 `parse_error` 含 `does not parse as Python`、`unknowns == []`）；新增 `test_a_file_that_does_not_parse_is_not_counted_as_parsed`（`files_parsed == ['strat.py']`、`files_unparsed` 只有 `legacy.py`、`coverage["unparsed_python_files"] == 1`、警告含 `did not parse`、且 `build_draft_dsl` 的"无法映射"计数仍是 `1 construct(s)`——那条来自 `strat.py` 自己的未解析引用，不是这个文件）；`test_analyze_endpoint_reports_its_coverage` 断言 `analysis_version == "1.3.0"`、`coverage["unparsed_python_files"] == 0`、`files_unparsed == []`、警告不含 `did not parse`。`backend/tests/test_github_watch.py` 的 `_install_fake_client` 增加 `findings` 参数；新增 `test_a_python_file_that_did_not_parse_blocks_an_unattended_import`（`AnalysisResult(files_unparsed=[SkippedFile("legacy.py", …)])` → 返回并存储 `incomplete`、不新建版本、`current_commit` 推进到 `newsha`（结构性）、快照 `reason == "unparseable_python"` / `transient is False` / `files_unparsed[0]["path"] == "legacy.py"`）。`backend/scripts/probe_parse_honesty.py` 用三个内存树（干净 / 两个解析失败 / 解析失败 + 上限缺口）打印 `files_parsed`、`files_unparsed`、coverage 计数器与全部警告，并断言解析失败者**不在** `files_parsed` 里且没有泄漏进 `unknowns`。

## ADR-060：一个修订必须用 commit 命名（`RepoMeta.commit`、`ref` 与 `commit`，docs/05 §4.4）

**背景**

1. `fetch_repository` 把调用方给的 `ref`（或默认分支名）直接当成修订标识：`get_tree(owner, name, resolved_ref)` 与 `get_raw_file(owner, name, path, resolved_ref)` 都用分支名请求，`RepoMeta` 只有 `ref` 一个字段。于是报告只能说"我读了 main"。分支名会移动：同一份报告在两个时间点无法指向同一份代码；抓取进行到一半时仓库被推了新提交，tree 与文件就可能来自两个不同修订，而报告里没有任何东西能暴露这一点。
2. 导入路径把分支名当成修订记了下来：`import_strategy` 写 `StrategyVersion.source_commit = payload.ref`、`_persist_github_source` 写 `source.current_commit = ref`，快照也记在 `commit="main"` 上，而 watcher 拿 `get_head_commit()` 的 SHA 与这个值比较——两类不同的字符串被存在同一列里。前端 `frontend/src/views/StrategiesView.vue` 的表头写着「来源 commit」，单元格却是 `{{ v.source_commit || '—' }}`，于是 `main` 看起来像一个 commit。
3. `_persist_github_source` 的 `if ref and ref != "HEAD"` 守卫让"没传 ref（或传 HEAD）"的导入**一条快照都不记**：策略建出来了，来源历史里却什么都没有。
4. `GithubImportRequest` 只有 `repo_url` / `ref` / `name` / `version` / `dsl`：请求根本不必说出审阅的是哪个 commit，服务端也就无从记录它。

**决策**

1. `GitHubClient` 新增 `resolve_commit(owner, repo, ref) -> str`：已经是 7–40 位十六进制 SHA 就原样返回；否则 `GET /repos/{owner}/{repo}/commits/{ref}` 取 `sha`，拿不到就 `raise GitHubError("could not resolve ref {ref!r} to a commit in {owner}/{repo}")`——绝不把一个名字当成修订。
2. `fetch_repository` 先 `commit = self.resolve_commit(...)`，tree 与每个文件的读取都用这个 SHA；`RepoMeta` 新增必填字段 `commit`（`ref` 保留为"当初要的是哪个名字"）。
3. `ANALYSIS_VERSION` 升为 `1.4.0`；`GithubAnalyzeOut` 新增 `commit`。
4. `GithubImportRequest` 新增必填 `commit`（`min_length=7, max_length=64, pattern=r"^[0-9a-fA-F]{7,64}$"`）；`import_strategy` 用它写 `source_commit`、`evidence_json`（`{importer, repository, ref, commit}`）与审计记录，响应回显 `source_commit`。
5. `_persist_github_source(db, owner, repo, commit, content_hash)` 改成接收 SHA，**无条件**记快照（删掉 `HEAD` 守卫），`current_commit` 与快照 commit 都写这个 SHA。
6. 前端：新增 `commitLabel`（不像 SHA 的值渲染成 `main（ADR-060 之前记的是分支名）`）与 `shortCommit`（取前 12 位）；分析卡片头部显示 `owner/repo @ ref · commit <短 SHA>`；来源版本列与导入成功提示都显示 commit。

**理由**

这条与"回测必须可复现"是同一条红线：**只有不可变的标识才允许事后复查**。ADR-056/057/058/059 修的都是"报告有没有夸大它做过的事"，这一条修的是"报告指向的是不是同一份东西"——一份说自己读了 `main` 的报告，在 `main` 移动之后就再也无法核对，策略的 `source_commit` 也因此没有证据价值。

watcher 路径不需要额外请求：它比较的本来就是 `get_head_commit()` 返回的 SHA，传进 `fetch_repository` 时已经是 commit，`resolve_commit` 只是原样放行。把"解析 ref"放进抓取器内部而不是要求每个调用方自己解析，是为了让两条路径（端点与 watcher）不可能各自记住不同的规则——同 ADR-056 把 coverage 算术共享给两处、ADR-058 把 upsert 放进共享服务。

对旧数据不做迁移：`source_commit = "main"` 是历史事实（当时确实只记了这个），改写成某个 SHA 等于编造证据；前端标注它"不是 commit"比假装它是更诚实——与 ADR-058 保留 `checked` 同理。

**测试**

`backend/tests/test_importer.py` 新增 `test_fetch_reads_the_commit_the_ref_pointed_at`（记录 tree 与文件请求的 ref：`meta.ref == "main"`、`meta.commit == _FAKE_COMMIT`、两次文件请求都带该 SHA、`/commits/` 只查一次、没有 `/git/trees/main`）与 `test_a_commit_sha_is_taken_as_is_and_an_unresolvable_ref_fails`（传 SHA → 不发 `/commits/`；仓库返回 `{"message": "Not Found"}` → `GitHubError("could not resolve ref …")`）；`test_analyze_endpoint_reports_its_coverage` 断言 `analysis_version == "1.4.0"` 与响应 `commit`；新增 `test_import_endpoint_requires_the_commit_it_was_reviewed_at`（缺 `commit` / `commit="main"` / `commit="abc12"` 都是 422）。`backend/tests/test_github_sources.py` 的导入测试改发 `ref` + `commit`，断言 `source_commit`、`evidence_json["commit"]`、快照 commit 都是该 SHA，新增 `test_an_import_without_a_ref_still_records_its_commit`（不传 ref 也照样记快照）。`backend/scripts/probe_commit_provenance.py` 用内存客户端打印每次请求用的修订并断言全部带 SHA，另测 watcher 路径零查询与不可解析 ref 的拒绝。

## ADR-061：版本号由拥有账本的 service 分配（`next_version`、`strategy_version_plan`、docs/05 §4.5）

**背景**

1. `GithubImportRequest.version` 的默认值是 `"1.0.0"`（`backend/app/api/schemas.py`），而 Web UI 的 `importReviewed` 干脆硬编码 `'1.0.0'`（`frontend/src/views/StrategiesView.vue:485`）。服务端对同名策略按 slug 复用（`import_strategy` 里 `slugify(payload.name)` 查 `Strategy`），于是**同一个仓库第二次导入必然**撞上 `create_strategy_version` 的 `version '{version}' already exists for this strategy`（422，`backend/app/data/strategy_service.py:87`）。
2. 页面上没有任何地方能改这个版本号：`importName` 有输入框，版本号没有。调用方既看不到这个名字已有哪几版，也无从推断该填什么——它唯一能做的是猜。
3. 版本号是**不可变账本**的一部分（策略版本一旦创建不可修改），但决定权被放在了调用方，而账本在服务端手里。这是 ADR-060 的同族问题：事实由谁拥有，就该由谁命名。
4. v1.5.0 的真栈验收里，我自己的验证脚本也撞了 422，第一反应是"产品缺陷还是脚本写错"——这正说明这个接口把不该由调用方知道的事情推给了调用方。

**决策**

1. `strategy_service` 新增 `next_version(existing: Iterable[str]) -> str`：空 → `1.0.0`；否则取 `major/minor/patch` **整数**三元的最大值再补丁 +1（`1.0.9` → `1.0.10`，`1.9.0` → `1.10.0`——字符串比较会得到 `1.9.1`）。出现读不成 `major.minor.patch` 的版本时 `raise ValueError("cannot assign a version: existing version 'v2-beta' is not major.minor.patch, so name the version explicitly")`。
2. `strategy_service` 新增 `strategy_version_plan(db, name) -> dict`：返回 `{name, slug, strategy_id, versions, next_version, can_assign, reason}`——账本在哪里、下一个是什么、能不能自动分配。`can_assign` 为假时 `next_version` 为 `None`、`reason` 说明原因。
3. `GithubImportRequest.version` 由 `str = "1.0.0"` 改为 `str | None = None`；`import_strategy` 在省略时走 `strategy_version_plan`，`can_assign` 为假就 422（把 `reason` 当成 detail），否则用 `next_version`。人工命名的版本仍按原样使用。
4. 导入响应新增 `version_assigned`（布尔），`evidence_json` 与审计 payload 也记录它——事后能分辨"这一版是谁定的号"。
5. 新增 `GET /importer/github/versions?name=...`（`response_model=GithubVersionPlanOut`），在写任何东西之前回答上述计划。
6. 前端 `importGithubStrategy(repoUrl, name, dsl, commit, ref?, version?)`（版本号后置为可选）；`StrategiesView.vue` 新增版本号输入框 + `watch(importName)`（300ms 防抖）查询账本，显示"将新建策略（slug …），版本 1.0.0"或"将在已有策略 #N（slug …）上创建新版本 1.0.1；已有版本：…"；手填的版本号已存在时**禁用**导入按钮并给出错误提示；导入失败后重新查询账本，让提示与服务器刚才说的话一致。

**理由**

"谁拥有账本，谁分配号码"是唯一能让调用方不需要猜测的安排：服务端已经知道这个 slug 有哪些版本，而调用方只能看到自己手里的那一次分析。把默认值设成 `1.0.0` 不是"给个方便的默认值"，而是**在没有依据的情况下替服务端做了一个会被拒绝的决定**。

不自动迁移、也不"分配一个能用的号"绕过不可读版本：`v2-beta` 是人工命名的合法版本，服务端读不懂它就不该假装能排在它后面；报错并点名它，比生成 `1.0.1` 然后与某个未来版本相撞更诚实（同 ADR-058 保留 `checked`、ADR-060 保留分支名）。

前端禁用按钮基于"最近一次读到的账本"，服务端仍然最终裁决：这是提示，不是保证。UI 在"点下去必然 422"的情况下还让按钮可点，等于把服务端的约束藏起来。

**测试**

`backend/tests/test_importer.py` 新增 `test_import_endpoint_assigns_the_next_version_when_none_is_named`（两次不带 `version` 的导入 → `1.0.0`、`1.0.1`，`version_assigned` 皆为真，且 `strategy_id` 相同）、`test_import_endpoint_keeps_the_version_the_reviewer_names`（`2.5.0` 原样使用且 `version_assigned` 为假，重复 → 422，随后不带版本 → `2.5.1`）、`test_version_ledger_reports_what_would_be_assigned`（导入前后 `GET /versions` 的完整字典与 `1.0.0` → `1.0.1`）、`test_version_ledger_refuses_to_increment_a_version_it_cannot_read`（种入 `v2-beta` → `can_assign` 假、理由点名它、不带版本 422、显式 `1.0.0` 仍 201）。`backend/scripts/probe_version_ledger.py` 离线用内存 SQLite 打印五个 `next_version` 用例（含 `1.9.0` / `1.10.0` 的整数比较）、不可读版本的拒绝，以及三次账本快照。
