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
