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

1. `strategy_service` 新增 `next_version(existing: Iterable[str]) -> str`：空 → `1.0.0`；否则取 `major/minor/patch` **整数**三元的最大值再补丁 +1（`1.0.9` → `1.0.10`；同时存在 `1.9.0` 与 `1.10.0` 时下一个是 `1.10.1`，而按字符串比较会得到 `1.9.1`）。出现读不成 `major.minor.patch` 的版本时 `raise ValueError("cannot assign a version: existing version 'v2-beta' is not major.minor.patch, so name the version explicitly")`。
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

## ADR-062：草案不合格时无人值守的导入必须停下来说明（`review_required`、`pending_review_commit`、docs/05 §7.2）

**背景**

1. `dsl_builder` **从不生成离场规则**（ADR-059 同族：读到了 ≠ 看懂了 → 这里更进一步，"抽出来了" ≠ "能导入"）：`backend/app/importer/dsl_builder.py:159` 无条件写 `"exit": {}`，只在警告里说 `no exit rules are auto-generated; add exit conditions manually before importing`。所以一份机器草案**必然**通不过 `parse_spec`（`app/strategies/dsl.py:278`：`at least one exit rule is required`），`"if not draft"` 那条分支实际上永远不会命中——草案 dict 永远非空。
2. `check_source` 仍然把这份草案直接交给 `create_strategy_version`，而这个调用**不在任何 try/except 里**（同一函数里包住 fetch/analyze 的 try 在更上面）。实测（`C:\Users\bobvane\AppData\Local\Temp\mql_probe_ownership_today.py`，内存 SQLite + 真 `check_source`）：`ValueError: invalid strategy DSL -> : Value error, at least one exit rule is required`，栈 `app/workers/tasks.py:333` → `app/data/strategy_service.py:163`。
3. 后果不只是"这一轮没导入"：异常逃出 `check_source` → Celery 任务崩掉，**不写快照、不更新 `last_import_status`**，来源行看起来什么都没发生；而 `check_github_sources` 的 `for source in sources` 循环随之中断，**排在坏来源后面的来源再也不会被检查**——一个仓库的失败变成了整轮监视的失败。
4. 触发条件不是罕见输入：`Strategy.source_url == GitHubSource.repository_url`（人工导入会写这个字段）加上上游有新 commit，就必然走到这里。
5. 同族还有第二条"两套规则"：`check_source` 自己用 `_bump_version` 编号，它套的是项目**发布**节奏（`patch >= 10 → minor += 1`，实测 `1.0.9 → 1.1.0`），而 ADR-061 刚把同一列的编号权交给 `strategy_service.next_version`（整数补丁，`1.0.9 → 1.0.10`）。
6. 还有一半是**安静**的：`create_strategy_version` 里 `validate_strategy` 判 `invalid` 并不抛错，只把 `validation_status="invalid"` 落库。只挡 `parse_spec` 的异常，等于放行"能解析但不合规"的草案。

**决策**

1. 无人值守导入与人工导入共用**同一个裁决者**：`strategy_service.strategy_dsl_problem(dsl)`（`parse_spec` + `validate_strategy`），在写任何东西之前先问；返回 `None` 才继续导入，否则把裁决原话记下来。两条路径问同一个函数，才不会出现"人工导入被拒绝、watcher 却写进去了"这种分叉。
2. 裁决不通过 → 结果记为新的 `review_required`：快照 `reason="requires_review"`、`detail` 是裁决文本、`transient=false`、`imported=false`，`last_import_status="review_required"`，**不创建任何版本**。
3. 该 commit 写进新的可空列 `GitHubSource.pending_review_commit`（迁移 `0008_github_pending_review`；v1.5.2 里这个迁移的 id 有 33 个字符，超过 Alembic 在 PostgreSQL 上建的 `VARCHAR(32)` 列，改名经过见 ADR-064）。`current_commit` 同时推进（同一个 commit 再抓一次也只会得到同一个结论），但"还在等人工"必须由独立列表达：否则下一次 beat 看到 `head == current_commit` 直接报 `unchanged`，把等待状态抹掉。
4. 等待中的 commit 不再抓取：`check_source` 在 `head == current_commit` 判定**之前**先比 `pending_review_commit == head`，命中即返回 `review_required`（只花一次 HEAD 请求）。顺序是关键——放在后面就等于允许"unchanged"覆盖等待。
5. 人工导入**那个** commit 会清空 `pending_review_commit`（`_persist_github_source` 里逐 commit 比较）；导入别的 commit 不清空——等待属于某个修订，不属于仓库。
6. 更新的 commit 取代旧等待：head 变了就走常规路径，能导入则一并清空等待。
7. 删掉 `_bump_version`，watcher 改用 `strategy_service.next_version`，同一列只有一套编号规则。
8. 导入期任何异常都不再逃出 `check_source`：记录 `last_import_status="error"` + 快照 `reason="import_failed"` + `detail`（`sanitize_untrusted_text`），且**不推进** `current_commit`（下一轮重试）；`check_github_sources` 逐来源再兜一层（`db.rollback()` + `logger.warning` + 记 `error`），一个来源崩掉不影响后面的来源。
9. sources 的三个响应（列表、详情、`/check`）都返回 `pending_review_commit`；前端状态列显示「待人工审阅」+ 待审阅 commit 短 SHA（`wait` 色调，不是错误色），快照详情用 `detail` 说明"补上缺失的规则后人工导入这个 commit"。

**理由**

"拒绝导入"和"导入失败"是两种不同的事，混成一种会让两边的用户都看不懂：前者是**设计**（草案本来就不该由机器补出离场规则，那是一个交易决策），后者是**故障**。所以前者要有自己的持久状态和解释文本，而不是一个 exception traceback。

把等待放进独立列，是因为 `current_commit` 语义上只说"我读到过哪个修订"，它无法同时表达"我拒绝了这个修订、并且在等一个人"。硬把它塞进 `current_commit`（比如不推进）会让每轮重复抓取同一份东西、每轮再拒绝一次，把一次性的等待变成持续的流量；塞进 `last_import_status` 又会被下一轮的 `unchanged` 冲掉。ADR-057 已经有过同型的结论：**状态需要一列就给它一列**。

"先问再写"而不是"写完再修"：`create_strategy_version` 的 `validation_status="invalid"` 会把一个不合规的版本留在不可变账本里（策略版本一旦创建不可修改）。机器没有资格往账本里写它自己都不信的东西。

**测试**

`backend/tests/test_github_watch.py`：新增 `test_the_import_gate_names_what_blocks_an_unattended_import`（合法草案 `None`；缺离场规则 → `invalid strategy DSL -> : Value error, at least one exit rule is required`；引用 `future_close` 的可解析草案 → `invalid strategy DSL -> entry.long.right:` 且含 `unavailable future data`，即校验器那条**安静**分支也被挡住）、`test_an_exit_less_draft_is_refused_instead_of_crashing`（`review_required` + 快照 `requires_review` + `detail` 含离场规则 + `pending_review_commit`、版本数不变、`current_commit` 推进）、`test_a_pending_review_survives_the_next_run_without_refetching`（假 client 的 `fetch_repository` 直接 `AssertionError` → 仍然 `review_required`、零快照）、`test_a_new_commit_clears_a_review_that_was_never_done`（head 变新 → 正常导入且清空等待）、`test_an_import_that_raises_is_recorded_not_raised`（`create_strategy_version` 抛 `RuntimeError("ledger exploded")` → `error`、`import_failed`、`current_commit` 不变）、`test_a_source_with_nothing_linked_says_so`（`no_linked_strategy` 与 `dsl_unchanged` 区分开）、`test_a_source_that_crashes_does_not_stop_the_run`（两个来源，第一个抛错 → `{"checked": 2, "error": 1, "unchanged": 1}` 且第二个仍被检查）、`test_the_watcher_numbers_versions_with_the_ledger`（删掉 `test_bump_version_scheme`，改断言账本的整数编号）。`backend/tests/test_github_sources.py`：新增 `test_importing_the_pending_commit_clears_the_review` 与 `test_importing_another_commit_keeps_the_pending_review`。`backend/scripts/probe_watch_refusal.py` 离线跑真 `check_source` 三次（拒绝 → 再问一次不重抓 → 新 commit 取代并清空等待）并打印快照原因与 `detail`。

## ADR-063：生命周期审计必须说出它实际做的动作（`retire`/`restore`，`_transition_action`，docs/12 Audit Logs）

### 背景

- `apply_lifecycle` 每次阶段变更都写 `strategy_lifecycle_changed` 审计事件，`action` 字段用
  `"promote" if _rank(target_stage) > _rank(previous) else "degrade"` 推出方向。
- `_rank(stage)` 的名字排成 `(*PIPELINE, *MANUAL_ONLY)`，即
  `imported, normalized, validated, backtested, oos_tested, paper_trading, reference_signal, retired`。
  于是 **`retired` 排在整个序的最高位**，而 `degraded` 根本不在序里（返回 `-1`）。
- 结论是：把策略退休 —— 生命周期里最重的动作 —— 被记成 `"promote"`，即「晋级」。
  `degraded -> retired` 也同样是 `"promote"`。实测（一次性探针，真 `apply_lifecycle`）：
  `paper_trading -> retired` 写 `promote`，`degraded -> retired` 写 `promote`，
  `paper_trading -> degraded` 写 `degrade`，`validated -> backtested` 写 `promote`。
- 这不是内部小事：审计表 UI（`frontend/src/views/SettingsView.vue:832`）把 `action` 原样渲染在
  「动作」列，所以页面上一次「退休」显示为 `promote`；`GET /audit/logs` 的 `action` 也是同一个值。
- 没有任何测试看过这个字段：全仓只有 `backend/app/strategies/lifecycle.py:325` 与 `:340` 提到
  `_rank`，测试里 promote/degrade 只作为函数名出现（`test_promotes_one_step_at_a_time_with_evidence`
  `backend/tests/test_lifecycle.py:158`），没有断言 `action` 的用例。
- 根因不是笔误，而是**用排名比较表达方向**：流水线序表达「谁在谁前面」，表达不了
  「离开流水线」和「被标记」——`retired` 在流水线之外，`degraded` 在两条元组之外。

### 决策

1. 用 `_transition_action(previous: str, target: str) -> str` 取代 `_rank` 比较，并删除 `_rank`
   （`backend/app/strategies/lifecycle.py`）。
2. 词表固定为 `LIFECYCLE_ACTIONS = ("promote", "degrade", "retire", "restore")`，并从模块导出，
   供测试与文档引用。
3. 裁决顺序：`target == degraded -> "degrade"`；`target == retired -> "retire"`；
   `previous in (degraded, retired) -> "restore"`；其余 `"promote"`。
   即：**先看落到哪里**（被标记 / 退休），再看是不是从终态回到流水线，剩下的才是前进一步。
4. `retired -> degraded` 记 `degrade`（落到被标记态），`retired -> reference_signal` 记 `restore`。
5. 自动化路径不变：`degraded` 仍然只由规则给出（`degrade_pnl_threshold`），`retired` 永远只能人工；
   `actor` 也照旧（人 `user`、规则 `system`）。
6. 测试（`backend/tests/test_lifecycle.py`，新增 5 条）：退休记 `retire`（两种来路）、
   亏损记 `degrade`、前进记 `promote`、从 `retired` 回到 `reference_signal` 记 `restore`；
   原有的审计测试补一条 `action in LIFECYCLE_ACTIONS`，防止未来新增词表外的取值。
7. 探针 `backend/scripts/probe_lifecycle_direction.py`（离线、内存 SQLite）把五种走法打印成一张表，
   任何一格与词表不符即 exit 1。
8. `docs/12_API_SPEC.md` 的 Audit Logs 小节写明 `action` 的四个取值与「退休不得记为 promote」。

### 理由

- **审计记录存在的意义是回答问题**。当有人翻到「谁把这套策略停了」，它必须能回答；把退休写成
  「晋级」比没有记录更糟：它让停止看起来像鼓励，而这个字段正在页面上被直接阅读。
- **方向不是序关系**。流水线是一条链，「离开链」「被标记」不是链上的位置；`degraded` 甚至不在
  任何元组里。要么把方向写成显式的裁决（本决策），要么就得先把 `degraded`/`retired` 硬塞进序里
  —— 那等于用排名的假象继续掩盖语义。
- **词表是契约**。`action` 已被 UI 与 API 消费，取值应当是有限、可枚举、可断言的；把它写成常量并
  让测试引用，才能在下一次改生命周期时立刻发现越界。
- 与 ADR-062 同一族：**记录必须说出实际发生的事**，宁可多一个新词（`retire`），也不要复用
  「晋级」去描述停止。

## ADR-064：迁移 id 必须放得进 Alembic 自己的 32 字符列（`0008_github_pending_review`，ADR-062 的迁移改名）

### 背景

- v1.5.2 的迁移 id 是 `0008_github_source_pending_review`，**33 个字符**。
- Alembic 在 PostgreSQL 上把版本表建成 `alembic_version.version_num VARCHAR(32)`，而迁移执行的最后一步是
  `UPDATE alembic_version SET version_num = '<新 id>' WHERE ...`。于是这条迁移在 PostgreSQL 上必然失败：

  ```
  psycopg.errors.StringDataRightTruncation: value too long for type character varying(32)
  sqlalchemy.exc.DataError: (psycopg.errors.StringDataRightTruncation) value too long ...
  [SQL: UPDATE alembic_version SET version_num='0008_github_source_pending_review'
        WHERE alembic_version.version_num = '0007_backtest_result_warnings']
  ```

- 后果不是「迁移慢一点」，而是**Postgres 部署起不来**：整个迁移事务回滚（`add_column` 与版本更新同事务，
  库仍停在 `0007_backtest_result_warnings`），`compose` 的 entrypoint 重试三次后
  `[entrypoint] ERROR: migrations failed; refusing to start`，容器被判 unhealthy。
- v1.5.2 的 CI 因此变红（run `37084884774`，headSha `b6e2c6d3c`）：`backend tests + lint` 的
  「Run PostgreSQL regression tests」四条 setup error 全是这条 `DataError`，
  `docker compose smoke test` 的「Boot the stack」也是同一个原因；同一提交的 `frontend build` 与 release 流水线是绿的。
- 为什么本地一路看不出来：本地 581 条测试跑在 **SQLite** 上（`alembic upgrade head` 到临时文件），SQLite
  不强制列长度，迁移成功；而唯一会碰 PostgreSQL 的 `backend/tests/test_postgres_triggers.py` 四条在本地因
  `TEST_POSTGRES_URL` 未设置被 **skip**。也就是说「只在 Postgres 上必炸」的缺陷在本地没有任何一条路径能暴露它。

### 决策

1. 迁移 id 改为 `0008_github_pending_review`（25 字符）。文件名保留描述性的
   `backend/alembic/versions/0008_github_source_pending_review.py`，沿用仓库既有先例
   （`0001_initial_schema.py` 的 id 是 `0001_initial`、`0004_resource_monitor_tables.py` 的 id 是
   `0004_resource_monitor`：文件名说人话，id 只求短且唯一）。
2. 新增守卫测试 `backend/tests/test_migration_revisions.py`，不需要任何数据库即可运行（因此本地与 CI 都会跑）：
   - 每个迁移的 `revision` 长度 ≤ 32（常量 `ALEMBIC_VERSION_COLUMN = 32`，注释写明它来自 Alembic 在
     PostgreSQL 上建的 `version_num VARCHAR(32)`）；
   - `revision` 唯一；
   - 每个 `down_revision` 都能解析到一条存在的迁移；
   - 整条链恰好一个 head。
3. ADR-062 与 `docs/15` 里指向这条迁移的旧 id 同步改名，并留一句「改名经过见 ADR-064」，避免文档指向一个
   不存在的 revision。

### 理由

- **上限不是我们的选择**：`VARCHAR(32)` 由 Alembic 自己建，我们能选的只有名字长度。把「能存进去」写成断言，
  比让每个人记住这个上限可靠。
- **守卫必须离线可跑**：CI 里唯一真正的 Postgres 检查依赖 `TEST_POSTGRES_URL`，本地默认没有；如果守卫只能
  在 Postgres 上跑，它就会和这次一样在本地被 skip 掉。所以新测试读迁移文件本身（`ast` 解析，不需要数据库）。
- **改 id 而不是改列宽**：`alembic_version` 是 Alembic 的表，扩大列宽需要先跑一条迁移——而迁移本身要先写进
  这张表，鸡生蛋问题；改名是唯一没有自举问题的修法。
- 与 ADR-060/061/062 同一族：**记录里写的东西必须是系统真能承担的东西**——这次是版本号，不是分支名或状态。

### 影响与兼容

- PostgreSQL 部署不受影响：旧 id 的迁移**从未成功过**，数据库仍是 `0007_backtest_result_warnings`，重新拉取
  新镜像会直接走到 `0008_github_pending_review`。
- 已经用 v1.5.2 在 **SQLite** 上迁移过的本地库会停在旧 id 上，`alembic upgrade head` 报
  `Can't locate revision identified by '0008_github_source_pending_review'`。修法二选一：
  `UPDATE alembic_version SET version_num='0008_github_pending_review'`，或删掉本地库重建（本地库都是可丢弃的）。

### 测试

- `test_every_revision_id_fits_the_alembic_version_column`、`test_every_down_revision_resolves_to_a_migration`、
  `test_the_chain_has_exactly_one_head`、`test_versions_directory_is_not_empty`
  （`backend/tests/test_migration_revisions.py`，4 passed）。
- 真栈复核：删掉验证库后 `alembic upgrade head` 末行
  `Running upgrade 0007_backtest_result_warnings -> 0008_github_pending_review`。

## ADR-065：胜率必须跟它的分母一起发布（`signals`/`decided`/`undecided`，docs/12 Signals）

### 背景

`GET /signals/outcome-summary` 只聚合**已经有结果行**的信号：
`rows = db.execute(select(SignalOutcome, Signal).join(Signal, Signal.id == SignalOutcome.signal_id))`
（`backend/app/api/routers/signals.py:120-161`），返回 `{"evaluated": len(rows), "groups": {...}}`。
没有结果行的信号在这个响应里**完全不出现**，而它们通常才是多数：评估器要等信号之后
`DEFAULT_BARS_AFTER = 10` 根 K 线（`backend/app/simulation/outcome_evaluator.py:25`）才回填，
该标的根本没有 K 线序列的信号也永远没有结果行。于是 400 条信号里 327 条未决时，页面只写
「整体胜率 72%」，读者无法知道这个数字是从 73 条里算出来的，还是从全部 400 条里算出来的。

同一张卡片还把**分页长度当总数**：`frontend/src/views/SignalsView.vue:250` 的标题是
`信号结果追踪（{{ outcomes.length }} 条）`，而 `outcomes` 来自 `GET /signals/outcomes`
（`backend/app/api/routers/signals.py:79-117`）——返回裸数组、`limit` 默认 50（上限 500）、没有 total。
400 条结果时标题写「50 条」、同一张卡片下一行写「样本 73」，两个互相矛盾的口径。

第三个更安静的口径错误：summary **不接受** `symbol`，而列表接受
（`frontend/src/api.ts:769-776`、`SignalsView.vue:73-74` 只把 `symbolFilter` 传给列表），
于是按标的过滤后的行旁边配着全局统计。

顺带记下：`outcome_summary` 的 docstring 与 `backend/tests/test_outcome_summary.py` 的模块 docstring
都写「docs/09 §8」，但 docs/09 §8 是「Strategy evidence layers」，与信号结果无关 —— 这个端点在文档里
本来没有归属。

### 决策

1. `GET /signals/outcome-summary` 新增 `symbol` 查询参数，与 `/signals/outcomes` 同口径：先按
   `Asset.symbol` 精确解析，未知标的返回空范围，**不回落到全局平均**。
2. 响应固定携带范围与三个计数：
   `{"symbol": <范围或 null>, "signals": <范围内信号总数>, "decided": <有可用结果的信号数>,
   "undecided": signals - decided, "bars_after": DEFAULT_BARS_AFTER, "groups": {...}}`。
   `decided` 就是每个 `count`/`win_rate` 的分母；`undecided` 是还没有结果的信号（要等信号后
   `bars_after` 根 K 线，或该标的还没有 K 线序列）。
3. `evaluated` 改名为 `decided`：同一个数字只留一个名字。
4. `decided` 只数**有可用 pnl 的结果行**（`pnl_pct is not None`），因此
   `decided == groups["ALL"]["count"]` 恒成立。
5. 前端卡片不再用分页长度当总数：标题改为「信号结果追踪」，新增一行
   「范围：<symbol 或 全部标的> · 共 N 条信号，已评估 M 条（另有 K 条还没有结果…）」，
   表格上方注明「下表是最近 N 条已评估信号」，并把 `symbolFilter` 一起传给 summary；
   空表文案区分「一条信号都没有」与「有信号但还没有一条等到结果」。
6. 文档归属修正：两处「docs/09 §8」改为 docs/12，并在 docs/12 写下这三个计数的含义。

### 理由

- 胜率是比率，比率脱离分母就没有意义：`73/73` 与 `73/400` 读起来是同一句话，但一个是结论、
  一个是「73 条上的初步印象」。
- 未决信号既不能计入胜率、也不能算成亏损（那等于凭空造出失败），唯一诚实的位置是分母旁边。
  评估器自己的返回值里本来就有 `insufficient_data`/`skipped`，但那个数字活在 Celery 任务的返回值里，
  用户永远看不到；把「还没有结果」放进用户看得见的响应，是「记录必须说明自己覆盖了什么」
  这条线的延续（ADR-054/055/056/057/058/060/061/062/063/064）。
- 用 `signals - decided` 而不是逐条判断「为什么未决」：把 327 条未决拆成「还差几根 K 线」与
  「没有序列」需要为每条信号查未来 K 线（400 次查询），而这两类原因都写在 `bars_after` 与
  「没有 K 线序列」这句话里。宁可把规则说清，也不猜一个更细的分母。
- 分母只数有 pnl 的行，让分母、`count`、`win_rate` 三者永远一致，不引入「分母 5、样本 4」
  这种新的自相矛盾。
- 范围必须显式：同一个端点过滤与不过滤时都能用，所以它必须自己说明这次数字属于哪个范围，
  而不是让读者从旁边的表格去猜。

### 影响与兼容

- `evaluated` 是破坏性改名，但消费者只有前端一处（`frontend/src/api.ts` 的类型与
  `SignalsView.vue`），已同步。
- 未知 `symbol` 返回全 0 的空范围（与 `/signals/outcomes?symbol=...` 返回 `[]` 一致），不是 404。
- 数据库无变化、无迁移。

### 测试

- `test_the_summary_states_the_denominator_of_its_win_rate`：3 条已决 + 4 条未决 →
  `signals 7 / decided 3 / undecided 4`，胜率只按 3 条算，且 `decided + undecided == signals`。
- `test_the_summary_only_counts_the_symbol_it_names`、`test_an_unknown_symbol_is_an_empty_scope_not_a_global_average`、
  `test_an_outcome_without_a_pnl_is_not_a_decided_signal`；原 `test_outcome_summary_groups` 改用 `decided`
  并断言 `bars_after == 10`（`backend/tests/test_outcome_summary.py`，6 passed）。

## ADR-066：取钱不是亏钱：资金进出必须与盈亏分开（`net_deposits`，docs/08 §3、docs/12 Paper Accounts）

### 背景

- `POST /paper/accounts/{id}/fund` 的 `amount` 是带符号的：正数是入金，负数是提现。
  但基准只在**入金**时跟着走：

  ```python
  account.cash = new_cash
  if amount > 0:
      # Additional funding raises the baseline so the P&L percentage stays sane.
      account.initial_cash = Decimal(str(account.initial_cash)) + amount
  ```

  于是提现只减 `cash`，基准留在原地——**账户里少了的钱被记成了交易亏损**。
- 后果在一个从未交易过的 10,000 账户上最干净：提现 4,000 之后，账户列表的「盈亏」
  `(cash - initial_cash) / initial_cash` 变成 **-40%**；而同时
  `GET /paper/accounts/{id}/performance` 的 `final_equity` 仍是 10,000——它描述的钱
  已经不在账户里了（`cash` 只剩 6,000）。一个赚了 1,000 的账户提现 4,000，会被显示成
  **-30%**。全部提空则是 -100%。
- 这不是舍入问题，而是账目问题：做除法的那个数（基准）和账户实际持有的钱已经不是
  同一个东西，权益曲线从一个「已经离开的钱」起算。
- 探针 `backend/scripts/probe_paper_contributions.py` 把六种走法打在同一张表上，用真的
  `fund_account` / `account_equity` / `account_performance` 跑出来（修复前）：

  | 走法 | cash | 净入金 | 界面盈亏 | final_equity |
  | --- | --- | --- | --- | --- |
  | 不动 | 10,000 | 10,000 | 0% | 10,000 |
  | 入金 +5,000 | 15,000 | 15,000 | 0% | 15,000 |
  | 提现 -4,000 | 6,000 | **10,000** | **-40%** | **10,000** |
  | 赚 1,000 后提现 -4,000 | 7,000 | **10,000** | **-30%** | **11,000** |
  | 提空 10,000 | 0 | 10,000 | -100% | 10,000 |

### 决策

1. `fund_account` 对基准做**对称**更新：入金与提现都改 `initial_cash`，删掉
   `if amount > 0:` 这个不对称分支。基准从此的含义是**净入金**（入金 − 提现）。
2. 恢复一条可检验的不变量：`final_equity == net_deposits + 已实现盈亏`；空仓时它等于
   `cash`。提现是资金的移动而不是盈亏，所以它同时进基准与现金，两边一起走。
3. 对外发布的名字从 `initial_cash` 改为 **`net_deposits`**：`GET /paper/accounts`、
   `GET /paper/accounts/{id}`、`GET /paper/accounts/{id}/equity`、
   `GET /paper/accounts/{id}/performance` 的响应里不再有 `initial_cash`。
   数据库列名 `initial_cash` 保留（不值得为此迁移），创建请求体仍是 `initial_cash`——
   创建那一刻它确实等于净入金。`PaperAccountOut.net_deposits` 用
   `Field(validation_alias="initial_cash")` 读那一列。
4. `POST /paper/accounts/{id}/fund` 与 `/reset` 的响应都带 `net_deposits`，审计 payload 也
   记录它，让账本能重放资金流。
5. 基准不 > 0 时**不发布**收益率类指标：`compute_metrics` 原有的 `initial <= 0` 守卫保留，
   但说明从「equity curve too short for ratio metrics」拆出来，改成
   `initial capital is not positive, so ratio metrics have no denominator`。绩效响应新增
   `metric_notes`（即 `metrics.notes`）——指标为什么缺席也是答案的一部分。
   **分母检查排在曲线长度检查之前**：提空之后的账户只有单点权益曲线，若先判长度，
   卡片会把「没有分母」说成「账户太年轻」（v1.5.5 的真浏览器验收抓到的正是这一句），
   而长度检查仍保留给「基准正常但还没有交易」的账户。空曲线的判定（`len(equity) == 0`）
   单独放在最前面，避免把「没有曲线」也说成分母问题。
6. 前端：账户表的「初始资金」列改名「净入金」，绩效卡片副标题改「净入金 + 已实现」，
   盈亏百分比走新的 `formatPaperPnlPct()`：净入金 ≤ 0 时显示「—」而不是 `-100%`/`NaN%`
   （`frontend/src/format.ts`，`PaperView.vue` 与 `DashboardView.vue` 共用）。
7. 提现仍然不能超过可用现金（422 `withdrawal exceeds available cash`）——本 ADR 只改记账，
   不放宽风控。

### 理由

- **做除法的那个数必须等于账户真正持有的钱**。入金抬高基准是为了不把入金算成收益；
  提现不同步降低基准，就是把「钱离开账户」算成「交易亏掉了钱」——同一个错误的镜像。
  对称修正是唯一自洽的选择。
- **不变量比公式好检查**。`final_equity == net_deposits + 已实现盈亏` 一句话就能在 API 层
  断言（探针与测试都这么做）；原来的公式要解释「为什么 `final_equity` 比 `cash` 多出
  4,000」，没有诚实的解释。
- **字段名要说真话**。既然入金和提现都会改它，`initial_cash` 在任何一次资金流动之后都是
  错的。上一版（ADR-065）已经为同一个理由把 `evaluated` 改名为 `decided`：一个数只能有
  一个名字，而那个名字必须描述它现在装的是什么。
- **没有分母时不要生产百分比**。提空之后的 `-100%` 看起来像结论，其实是 `0/10,000` 这种
  没有意义的算式；`null` 加说明才是诚实的结果，界面显示「—」。
- 保留数据库列名是**有意的妥协**：改名要迁移、要回填、要在 Postgres 与 SQLite 上一致，
  而收益只是列名好看。把历史命名写进 docs/11 比假装它不存在更便宜。

### 影响与兼容

- **破坏性 API 变更**：读 `initial_cash` 的调用方改用 `net_deposits`（前端已同步）。创建与
  重置的**请求**参数不变。
- 已有账户：过去把入金折进基准、提现没有折，历史基准无法追溯修正（从当前的
  `initial_cash` 反推不出当年的资金流）。从本版起新的资金流动是对称的；docs/11 注明
  该列名的历史含义。
- 未平仓持仓与本 ADR 无关：`final_equity` 只算已实现盈亏，`cash + 持仓成本` 才是总权益，
  差额仍是持仓。探针刻意只用空仓账户，于是 `final_equity == cash` 是可直接断言的等式。
- 红线不变：这些字段只存在于 paper 表，与真实持仓严格隔离。

### 测试

- `test_a_withdrawal_is_not_a_trading_loss`：提现 4,000 后 `cash == net_deposits == 6,000`，
  `equity.realized_pnl == 0`，列表里该账户 `net_deposits == 6,000`，且响应里没有
  `initial_cash`。
- `test_a_profitable_account_still_reports_a_profit_after_a_withdrawal`：赚 1,000 的账户提现
  4,000 → `net_deposits 6,000`、`final_equity == cash == 7,000`、`total_return ≈ +16.67%`。
- `test_withdrawing_past_the_deposits_publishes_no_return`：提现 11,000 → `net_deposits -1,000`、
  `final_equity 0`、`total_return is None`、`max_drawdown is None`，`metric_notes` 含
  `no denominator`。
- `test_an_emptied_account_names_the_denominator_it_lost`：**没有交易**的账户提空到 0 →
  `metric_notes` 恰好是 `["initial capital is not positive, so ratio metrics have no denominator"]`
  （回归测试：这条曾经被「曲线太短」抢走，是浏览器验收发现的）。
- `test_api_fund_and_withdraw_limits`：入金 500 → `net_deposits 1,500`；超额提现仍 422。
- 探针 `backend/scripts/probe_paper_contributions.py`：六种走法全部通过，末行
  `RESULT: money in and money out move the baseline, and never the P&L`。
- 聚焦测试 `scripts/Invoke-Tests.ps1 -Keyword 'paper or metrics or api_paper'`：31 passed。

## ADR-067：未配置的 Ghostfolio 不是 500：构造失败也要走已经写好的 502 分支（`settings.py`）

### 背景

v1.5.5 的真浏览器验收在仪表盘上抓到一条服务端错误：

```
Failed to load resource: the server responded with a status of 500 (Internal Server Error)
  <http://127.0.0.1:4173/api/v1/settings/ghostfolio/holdings>
```

服务端日志（`app.api.main`）给出根因：

```
unhandled error on /api/v1/settings/ghostfolio/holdings
...
  File ".../app/api/routers/settings.py", line 296, in ghostfolio_holdings
    adapter = GhostfolioAdapter()
  File ".../app/data/ghostfolio.py", line 40, in __init__
    raise GhostfolioError("GHOSTFOLIO_BASE_URL is not configured")
app.data.ghostfolio.GhostfolioError: GHOSTFOLIO_BASE_URL is not configured
```

三件事实放在一起就是缺陷：

1. `GhostfolioAdapter.__init__` 在读取配置时就可能抛 `GhostfolioError`（`GHOSTFOLIO_BASE_URL`
   或 `GHOSTFOLIO_API_KEY` 为空），**不是**只有调用网络时才抛。
2. `ghostfolio_holdings` 写了 `except GhostfolioError → HTTPException(502)`，但
   `adapter = GhostfolioAdapter()` 写在这个 `try` **之外**，于是那道分支覆盖不到构造失败。
3. 同一个文件里的兄弟端点 `/settings/ghostfolio/test` 把构造放在 `try` **里面**——所以这
   不是设计选择，是漏改一处。

触发条件是最普通的安装方式：按 `.env.example` 复制 `.env`、不填 Ghostfolio（它是可选
依赖）。此时每次打开仪表盘都会产生一个 500。前端把这条请求 `.catch(() => null)` 掉了
（`frontend/src/views/DashboardView.vue`），所以页面仍然能渲染，问题只表现在两处：
服务端把一个「没配置」的事实报成 500，控制台留下一条红色错误。

把 500 改成 502 之后，控制台的那条红色错误**仍然在**：浏览器把任何非 2xx 的请求都记成
一条 failed request，前端 `.catch()` 拦得住异常，拦不住控制台。于是「未配置的可选集成
在页面上看起来像坏了」这半个问题只靠后端修不掉。

### 决策

1. 把 `adapter = GhostfolioAdapter()` 移进 `try`，让**已经存在**的
   `except GhostfolioError → 502` 分支真正生效。
2. 状态码不新增、不改：仍然是 502，`detail` 就是适配器给的那句话
   （`GHOSTFOLIO_BASE_URL is not configured`）。本 ADR 只修「错误分支够不到」，不重新
   定义「未配置」在语义上该是什么码。
3. 加一条 API 级测试：清空 `settings.ghostfolio_base_url` 后请求
   `/api/v1/settings/ghostfolio/holdings`，断言 502 与 detail 内容。
4. 仪表盘**不再明知故问**：`GET /settings` 早就返回
   `environment.ghostfolio_configured`（后端就是 `bool(settings.ghostfolio_base_url)`），
   所以 `DashboardView` 先取它，为假时**不请求**持仓；`api.ts` 的 `settings()` 由
   `Record<string, unknown>` 升级为真实的 `AppSettings` 类型，让这件事有类型可依，而不是
   靠运行时猜。未配置时的 502 因此只会在有人**直接调用**这个端点时出现——那正是它该出现
   的地方。

### 理由

- **写好的错误分支不能是死代码**。`except` 在那里，覆盖不到任何东西，比没有 `except`
  更危险：它让读者以为这条路已经被处理过了。这一版（v1.5.5）的另一半是 ADR-066，而
  v1.5.1 的 ADR-062、v1.5.3 的 ADR-064 处理的是同一类问题——异常跑出了本该接住它的
  范围。
- **502 是这个项目里「上游接不通」的既有答案**（ADR-039 已经这样定义 Ghostfolio
  连接失败）。配置缺失是上游不可用的一种，沿用它，界面与日志的词汇表不变。
- **500 的语义是「我们不知道发生了什么」**。这里我们完全知道：没配 `GHOSTFOLIO_BASE_URL`。
  把一个可以命名的事实降级成未命名异常，正是 docs/17 里反复拒绝的做法。
- 只改这一处、不顺手把 `ghostfolio_holdings` 的响应体改个形状：修缺陷的 diff 越小，
  越容易在事后证明它只修了缺陷。
- **端点是给调用方用的，页面要自己知道自己问得对不对**。已经有一个字段说明「配没配」，
  却不看它、每次都发一个注定失败的请求，这不是「健壮」，是把噪声当成了正常。真浏览器
  验收里这条失败请求是控制台上唯一一条错误——验收脚本卡在它上面，于是才被发现。

### 影响与兼容

- **无破坏性变更**：配置好 Ghostfolio 的部署完全不变（仍然请求持仓、仍然显示卡片）；
  未配置的部署从「每次加载都发一条注定 502 的请求」变成「根本不发」。直接调用
  `/settings/ghostfolio/holdings` 的调用方得到 502 + 明确 detail。
- `/settings/ghostfolio/test` 的行为不变（它本来就返回 `{ok: false, detail}`）。
- 红线不变：这两个端点都是只读，不写库、不触发交易。

### 测试

- `backend/tests/test_ghostfolio.py::test_holdings_endpoint_reports_an_unconfigured_ghostfolio`：
  `monkeypatch` 清空 `settings.ghostfolio_base_url` → `GET /api/v1/settings/ghostfolio/holdings`
  返回 **502**，`detail` 含 `GHOSTFOLIO_BASE_URL is not configured`。修复前该断言拿到 500。
- 浏览器验收（`%TEMP%\mql-ui-paper-contributions-v155.mjs`）：「零 console 错误」这一条
  在未配置 Ghostfolio 的验证栈上必须通过——后端不再产生 500，前端不再发这条请求。

## ADR-068：边缘必须分得清「这个文件不存在」和「这是应用」（静态资源 404、gzip、不可变缓存、favicon）

### 背景

v1.5.4 部署到 NAS 之后做体检（`http://192.168.2.2:8081`），对 web 容器逐项动手试出四条
事实：

1. **缺失的静态资源返回 200 + HTML**。`GET /assets/index-DOESNOTEXIST.js` →
   `200`、`Content-Type: text/html`、579 字节——就是 `index.html`。原因在
   `docker/web.nginx.conf`：只有一条 `location / { try_files $uri $uri/ /index.html; }`，
   没有单独的 `/assets/`。后果是浏览器读到一句最没用的错误：它缓存了旧的外壳、外壳引用
   了这次部署已经删掉的 bundle，于是把 HTML 当 JavaScript 解析，报「Unexpected token
   '<'」。外部探针也没法用状态码区分「有这个文件」和「没有」。
2. **整站没有压缩**。带 `Accept-Encoding: gzip, deflate` 请求
   `/assets/index-Be_RqsH4.js`，返回仍是 **1,289,010 字节**，响应里没有
   `Content-Encoding`。nginx 的 `gzip` 默认是 off。对照之下 API 的响应带着
   `Vary: Accept-Encoding`（FastAPI 的 `GZipMiddleware`）——同一个域里，API 压缩、
   静态资源不压缩。
3. **内容哈希文件名没有任何缓存指令**。响应只有 `ETag: "6ac05d04-13ab32"` 与
   `Last-Modified`。构建工具已经把内容摘要写进文件名，却让浏览器每次加载都回来校验。
4. **没有图标**。`frontend/index.html` 没有 `<link rel="icon">`，构建产物里也没有图标
   文件，于是 `/favicon.ico` 同样落到 SPA 回退、返回 `index.html`（200）。

四条事实形状相同：**边缘把「我不知道」答成了「这是应用」**。SPA 回退是给**路由**用的
（`/signals`、`/paper` 这类前端路径），不是给**文件**用的；`/assets/` 下面只可能有
带内容哈希的构建产物，那里的「没有」必须是一个明确的「没有」。

### 决策

1. `/assets/` 单独成 location：
   `try_files $uri =404;`，再 `add_header Cache-Control "public, max-age=31536000, immutable";`。
   哈希文件名按内容寻址 —— 命中就永久缓存（`immutable` 连条件请求都省掉），缺失就是 404。
   用 `add_header` 而不是 `expires`，避免同一个响应里出现两个 `Cache-Control`。
2. `location /` 保留 SPA 回退，并加 `Cache-Control "no-cache"`：外壳必须每次回源校验
   （响应带 `ETag`，没变就是 304）。不这么做，新部署之后浏览器还在用旧外壳，而旧外壳
   引用的资源已经不在。
3. 打开压缩：`gzip on; gzip_vary on; gzip_min_length 1024; gzip_proxied any;`
   `gzip_types application/javascript text/css application/json text/plain image/svg+xml;`。
   `gzip_vary` 不是可选项：没有它，中间缓存可能把压缩后的字节发给不支持压缩的客户端。
4. 交付图标：`frontend/index.html` 增加
   `<link rel="icon" type="image/svg+xml" href="/favicon.svg">`，新增
   `frontend/public/favicon.svg`；并让 `location = /favicon.ico` 明确回答 **204** ——
   我们确实没有 `.ico`，就不拿 HTML 冒充。
5. 配置模板的约束固化成测试：`docker/web-entrypoint.sh` 只做
   `envsubst '${AUTH_LINE}'`，所以该文件里除 `${AUTH_LINE}` 之外不允许出现任何
   `${...}`（nginx 自己的 `$uri`、`$host` 必须原样留下）。
6. 这些行为只在容器里成立，因此**在 CI 里验证**：`ci.yml` 的 docker compose smoke 作业
   增加一步「Web edge assertions」，在真实 web 镜像上用 curl 断言 404 / 压缩 /
   `immutable` / `no-cache` / 204 / 深链 200。

### 理由

- **一个状态码就是一句话**。`/assets/*.js` 返回 200 的 HTML 是假话，而假话比 404 更难
  查：404 指向「文件不在」，200+HTML 指向「语法错误」。这个项目在别处反复选择「让答案
  说出真正的原因」——ADR-062 把「草案不合格」答成 `review_required` 而不是
  `import_failed`，ADR-066 让提空的账户说「没有分母」而不是「曲线太短」——边缘不该例外。
- **SPA 回退的范围要写进配置，而不是靠约定**。`try_files ... /index.html` 的服务对象是
  前端路由；把 `/assets/` 单独拆出来，就是把这条边界写成 nginx 能执行的东西。
- **哈希即内容寻址，可永久缓存**。文件名里的内容摘要已经由构建付出成本，不利用它等于
  白付。
- **压缩是同一份内容的体积减半**：1,289,010 字节的 bundle，构建日志里 gzip 后是
  429.33 kB。NAS 在局域网里，但「加载一次要传多少字节」对任何部署都是同一个问题。
- **只在 CI 验证，并且把这个区别说清楚**。本地是 Windows、没有容器运行时，而 CI 的
  smoke 作业本来就构建并启动真实的 web 镜像、用真实的生产 compose 文件——那是唯一能
  看到 nginx 真实行为的地方。仓库里那 7 条读配置文本的测试是**守卫**：它们能在 `pytest`
  里五秒失败，但它们**不证明**行为，证明行为的是那一步 curl。

### 影响与兼容

- **无 API、无数据模型变更**：`/api/`、`/docs`、`/openapi.json`、`/healthz` 的行为与
  之前完全一致（它们各自有独立的 location，不继承 `/` 的缓存头）。
- 部署后有两处状态码变化，都是本次的目的：`/assets/` 下不存在的路径由
  200(HTML) 变 **404**；`/favicon.ico` 由 200(HTML) 变 **204**。
- `immutable` 依赖「`assets/` 里只有带内容哈希的文件」。目前构建产物是
  `assets/index-<hash>.js`、`assets/index-<hash>.css`，外壳 `index.html` 在
  `location /`。以后若有**不带哈希**的文件被放进 `assets/`，它会被永久缓存——这是这条
  决策的代价，写在这里以免以后被当成 bug。
- `no-cache` 不禁止缓存，只要求校验：已有的中间缓存仍会带 `ETag` 回源（304）。
- 红线不变：本次改动全在静态交付层，不写库、不触发交易、不影响回测可复现性。

### 测试

- `backend/tests/test_web_edge.py`（7 条，纯仓库、不需要容器）：`/assets/` 的
  `try_files $uri =404`；`immutable` 与 `max-age=31536000`；`location /` 的
  `no-cache`；`gzip on;` 与 `gzip_types` 含 `application/javascript`、`text/css`；
  `location = /favicon.ico` 的 `return 204`；`index.html` 链接的
  `frontend/public/favicon.svg` 确实存在；模板里只有 `${AUTH_LINE}` 一个占位符。
- `.github/workflows/ci.yml` 的「Web edge assertions (ADR-068)」（真实容器，唯一的
  行为验证）：缺失资源 404 且响应体不是 `<!doctype`；`/` 带 `Cache-Control: no-cache`
  且链接图标；`/favicon.svg` 200、`/favicon.ico` 204；bundle 带
  `Content-Encoding: gzip` 且小于 1,000,000 字节、带 `immutable`；`/signals` 深链仍
  200。
- 事实来源：NAS 上对 v1.5.4 web 容器的四条实测，见「背景」。

## ADR-069：健康检查必须在自己承诺的时间内回答（`PROBE_TIMEOUT_SECONDS`、有界 broker 连接、仪表盘不再等它）

- 背景：
  - 一个没有任何依赖可达的安装（本机验证栈：没有 Redis、没有 broker，`DATABASE_URL`/`REDIS_URL`/`CELERY_BROKER_URL` 都指向 compose 里的容器名）上，`GET /api/v1/health` 要 **10.4 秒**才回，而 `frontend/src/views/DashboardView.vue` 的 `load()` 用 `Promise.all` 把 `api.health()` 和模拟账户表放在一起 await —— 于是打开仪表盘先空白十秒，四张卡片和账户表全都在等一次健康检查。
  - 分段计时（`backend/scripts/probe_health_latency.py`，三个依赖都解析不了）：`database 1.26s`、`redis 2.08s`、`workers` **`11.76s`**，合计 **15.10s**。`/healthz` 不碰任何依赖，所以容器健康检查看不出这件事。
  - 元凶是 `_check_workers()` 里的 `celery_app.control.ping(timeout=1.0)`：那个 `timeout` 只约束「等回复」，不约束「连得上 broker」。broker 连不上时走的是 kombu 默认连接策略（重试 + 退避），把 1 秒的意图变成 11 秒的等待。热解析缓存后仍然要 **8.99–11.13s**（两次实测），所以慢的不是 DNS 查询本身，而是 ping 内部的连接路径。
  - 反证（`%TEMP%\mql_probe_health_warm_v157.py`，先热身 DNS 再计时）：同样抛 `OperationalError: Error 11001 connecting to quantlab-redis:6379. getaddrinfo failed.` 的 `celery_app.connection_for_read(transport_options={"socket_connect_timeout": 1, "socket_timeout": 1}).ensure_connection(max_retries=0, timeout=1)` 只用 **0.86s**、连续两次都是 0.86s。另两条路都不通：`connection_for_read(connect_timeout=1)` 抛 `TypeError: Connection._ensure_connection() got an unexpected keyword argument 'connect_timeout'`（celery 5.6.3 / kombu 5.6.2），`celery_app.conf.update(broker_connection_timeout=1, broker_connection_retry=False, broker_connection_max_retries=0)` 之后再 `control.ping` 仍是 **8.98s**。
  - Redis 侧同形但轻得多：`redis.Redis.from_url(settings.redis_url, socket_timeout=2)` 只给了读超时、没有连接超时，实测 2.08s。
  - 上限设好之后还剩一个**冷启动**代价：真栈里第一次 `GET /health` 仍要 **5.72s**（同一进程随后的重复调用 max 1.75s）。第一次探针在一个进程里要付冷解析器与 broker transport 的建立代价，那部分不在任何 socket 超时能约束的范围内。
- 决策：
  1. 本模块的每个依赖探针共用一个上限：`PROBE_TIMEOUT_SECONDS = 1.0`。
  2. `_check_workers()` 拆成两步：先问「broker 通不通」（`connection_for_read(transport_options={"socket_connect_timeout": …, "socket_timeout": …})` + `ensure_connection(max_retries=0, timeout=…)`），通不了就直接 `"unknown"`，通了才 `control.ping(timeout=PROBE_TIMEOUT_SECONDS)`。「有没有 worker 在答」这个问题只有在「broker 连得上」时才有意义。
  3. 词表不动：仍然是 `"N online"` / `"0 online"` / `"unknown"`，`/health` 的响应键一个不加一个不减，因此仪表盘、NAS 冒烟脚本与探针都不需要改解析。
  4. `_check_redis()` 补 `socket_connect_timeout`，读超时从 2 收到 1（ping 是亚毫秒操作，1 秒已经是三个数量级的余量）。
  5. 关闭连接失败用 `contextlib.suppress` 吞掉：清理出问题不是依赖状态，不能把已经拿到的 `"1 online"` 变成 `"unknown"`。
  6. 仪表盘不再 await `/health`：账户表、系统信息、信号等照旧并行等待，健康卡片**自己到达自己填**，失败时副标题写「健康检查没有响应」而不是永远停在「连接中…」。健康检查是补充信息，不是首屏数据。
  7. 时序断言写进离线探针 `backend/scripts/probe_health_latency.py`（先跑一轮热身、只给第二轮计分；单探针 ≤ 3.0s、合计 ≤ 5.0s，超了 `exit 1`），不写进单元测试；单元测试只断言「上限确实被传下去」与词表分支，避免慢 CI 上的抖动变成假红灯。
  8. 启动时预热一次：`warm_dependency_probes()` 在 `backend/app/api/main.py` 的 `lifespan()` 里起一个 daemon 线程跑一遍 `_check_redis()` + `_check_workers()`，**结果丢弃**。它把冷启动代价移出请求路径，但**不是缓存**——`/health` 仍然实时测量，所以预热不会变成一个过期的「connected」。
- 理由：
  - 「等回复」不等于「连得上」：一个只约束路径一半的超时不是超时。要给它上限，就得在真正可能卡住的那一步（建立连接）上给。
  - 热解析后仍然 9–11 秒，说明这不是 DNS 慢、也不是机器慢，而是代码在无界等待 —— 所以修在连接参数上，而不是把 ping 的超时调小或加缓存。
  - 词表不变才能让这次修复停留在实现内部：调用方看到的仍然是「在线几个 / 没人答 / 不知道」，只是「不知道」现在一秒就回来。
  - 清理失败不能污染结论：一句 `release()` 抛错不该把一次成功的探测改写成失败。
  - 页面不该被一个状态卡片拖住。健康检查是「补充信息」，它慢或失败时账户表仍然该立刻在屏幕上。
  - 只断言量级、并把计时放进探针：CI 机器负载不可控，「不许无界」要写成秒级预算，而不是把 0.86s 的成绩单钉死。
  - 冷启动的代价该付，但不该由第一个请求付：解析器与 transport 的第一次建立没法用超时约束（不是我们在等待，而是初始化本身要花时间），所以把它挪到启动时；**同时把结果丢掉**，否则「预热」很容易滑成「缓存」，而缓存一个健康状态就等于在撒谎。
- 影响与兼容：
  - 无 broker 的部署：`/health` 从 ~15s 降到 ~3s（本机实测 `database 1.24s / redis 0.86s / workers 0.86s`，合计 **2.96s**），`workers` 仍然是 `"unknown"`。
  - 有 broker 与 worker 的部署（含用户的 NAS）：先开一条连接再 ping，多一次本来就需要的连接建立（毫秒级）；词表与语义不变。
  - **未覆盖**：数据库那一段仍然由驱动的连接策略决定（本机解析不了主机时实测 1.25s，属于 libpq 的 `connect_timeout`，那是引擎创建时的事，不属于本模块）。如果哪台机器上数据库探测变慢，要在 `app/core/db.py` 的引擎上处理，而不是在这里加一层。
  - 仪表盘：`/health` 慢或失败不再挡住账户表；健康卡片在响应到达后自行填入。
  - 冷启动：预热之后第一次 `GET /health` 从 **5.72s 降到 2.19s**（真栈实测，同一进程随后的调用 1.75s 以内）；预热线程里的异常由 `contextlib.suppress` 吞掉、并且是 daemon 线程，失败不影响进程启动。
- 测试：
  - `backend/tests/test_health_probe.py`（**13 条**）：上限被传进 `connection_for_read`/`ensure_connection`/`ping`（这条是本次缺陷的回归守卫——旧代码是直接 `control.ping(timeout=1.0)`）；broker 不通 → `"unknown"` 且从不 ping；连上了没人答 → `"0 online"`；ping 抛错 → `"unknown"`；关闭失败不改变结论；redis 探针的 `socket_connect_timeout`/`socket_timeout` 被传下去；Redis 拒连 → `"unavailable"`；`/health` 的键集合与 `workers` 词表三种取值不变；预热按 `redis → workers` 顺序各跑一次；预热抛错被吞且线程结束；`create_app()` 进入 lifespan 时确实调用预热（monkeypatch `app.api.main.warm_dependency_probes`）。
  - `backend/scripts/probe_health_latency.py`：修复前打出 `workers -> unknown in 11.76s`、合计 `15.10s`、`RESULT: failures`；修复后 `RESULT: every dependency probe answers inside its budget`。

## ADR-070：一个事实只有一个出口（删除 `GET /settings/audit`、`total` 必须真的是总数）

- 背景：
  - 给 v1.5.4 的 NAS 部署做体检时对端点逐个探查，发现同一个账本有**两个出口**：`GET /settings/audit`（`backend/app/api/routers/settings.py:256`）与 `GET /audit/logs`（`backend/app/api/routers/audit.py:17`）返回同一批 **44 行**记录。前者**不带** `actor`，后者带；`docs/12_API_SPEC.md` 的 §Audit Logs 只记录了后者；前端读的却是前者（`frontend/src/api.ts` 的 `audit()`，`frontend/src/views/SettingsView.vue` 渲染）。
  - 两个出口各自手写一遍序列化字典，所以已经分叉：复制出来的那份丢了 `actor`——而「谁做的」是审计日志的第一个问题。改一处不会改另一处，两个答案只会越差越远。
  - 两个出口的 `total` 都是 `total: len(rows)`：`limit=2` 时 `total` 也是 2，于是「账本里一共有多少条」在 API 上**无法回答**——用一个页长冒充总数。`/settings/audit` 连参数校验都没有（`min(limit, 500)`，负数会直接传进 SQL），而 `/audit/logs` 有 `Query(ge=1, le=500)`。
- 决策：
  1. 审计只留一个出口：`GET /audit/logs` 与 `GET /audit/logs/entity/{entity_type}/{entity_id}`（也就是 docs/12 已经记录的那两个）。**删除** `GET /settings/audit`，不做别名、不做转发——转发只会让旧路径继续存在、继续被调用，重复就还在。
  2. `total` 由 `SELECT count(*)`（与 `events` 用同一个 `where`）回答，不再用 `len(rows)`。`limit`/`offset` 只影响 `events`。
  3. 前端改读 `/audit/logs`（`frontend/src/api.ts` 的 `audit()`）。
  4. 审计表新增「操作者」列渲染 `actor`，标题同时写「最近 N 条，共 M 条」，让 `total` 在界面上也有兑现，而不是只存在于 JSON 里。
  5. 把审计当断言工具的 4 个测试（`test_version_api.py`、`test_notifications.py`、`test_ai_providers.py`、`test_api.py`）改读 `/audit/logs`；其中 `test_api.py` 那条 `total >= 0`（恒真）改成「至少有一条 `strategy_version_created`，且每条事件都带 `actor`」。
- 理由：
  - 同一个事实有两个出口，就会有两个慢慢长歪的答案——其中一个已经丢了 `actor`。删掉一个出口，比让两个出口永远保持同步便宜，也更诚实。
  - `total: len(rows)` 是最容易骗人的字段名：调用方读到 3 会以为账本里有 3 条，其实那只是这一页。既然分页参数存在，「这一页多少条」已经由 `len(events)` 回答了，`total` 必须回答另一个问题，否则它没有存在的理由。
  - 这是一次**破坏性**变更（一个路由消失）。它从未出现在 docs/12 里，只有前端与 4 个测试在用，都在本次一并改掉；NAS 冒烟脚本 `scripts/Test-NasDeployment.ps1` 没有用它。
- 影响与兼容：
  - `GET /settings/audit` 现在返回 **404**（有测试守着，防止它悄悄回来）。
  - `GET /audit/logs` 的每条事件多了 `actor`（前端此前的类型是 `Record<string, unknown>`，不必改类型）。
  - `total` 的语义变了：以前恒等于 `len(events)`，现在等于匹配总数。任何一直拿它当分页长度用的调用方会看到更大的数字——那正是这个字段应该回答的问题。
  - 没有数据库迁移：只是查询与序列化的方式变了。
- 测试：
  - `backend/tests/test_audit_api.py`：`limit=2` 时 3 条记录仍然 `total == 3` 而 `events` 只有 2 条、`offset=2` 后 `total` 仍为 3；`/audit/logs/entity/signal/42` 在 `limit=1` 时 `total == 2`；`actor == "user"` 能被读回；`GET /settings/audit` 是 404。
  - `backend/tests/test_api.py::test_audit_log_records_events`：不再断言恒真的 `total >= 0`，而是断言至少一条 `strategy_version_created` 且每条事件都有 `actor`。

## ADR-071：健康检查必须说出它跑在哪一版库结构上（`migration`，`healthy` 要有第二个条件）

- 背景：给 NAS 部署做体检时，`scripts/Test-NasDeployment.ps1:92` 的步骤名叫「API /health (含依赖与迁移状态)」，而 `GET /health` 的响应里**根本没有库结构版本**——名字承诺了一个没人做过的检查，脚本本身也只断言了 `status == healthy`。同一次体检还暴露了更糟的一半：`status` 的判据只有 `database == "connected"`，所以一个「连上了库、但迁移从未运行」的 API 会报**和一次好部署完全一样**的 `healthy`。`docker/entrypoint.sh` 是在起服务前跑 `alembic upgrade head` 的（失败还会重试三次然后拒绝启动），可那保证的是**容器自己**做过迁移；一旦有人回滚、手工改过库、或把 API 指向另一个库，`/health` 仍旧说 healthy，而没有任何字段能让调用方发现这件事。
- 决策：
  1. `GET /health` 新增 `migration` 字段：**数据库自己报出的** alembic 版本号，取值来自 `SELECT version_num FROM alembic_version`，问库而不是问镜像——两者本就可能不一致，而部署方想知道的是库。
  2. 读不到版本表（表不存在、权限或语句失败）报 `"unknown"`；版本表存在但没有行报 `"none"`。两者都是明确的词，不是空格也不是 500。
  3. `status` 从「库能连上」升级为「库能连上**且结构版本说得出来**」：`database == "connected"` 与 `migration not in {"unknown", "none"}` 同时成立才是 `"healthy"`，否则 `"degraded"`。
  4. 探测顺序是先 database 再 migration，两者共用同一个 session：库都连不上时不必假装能读版本表。
  5. `/healthz` 依旧不碰任何依赖（容器 healthcheck 用的是它，见 `docker-compose.yml:153`），所以这次的语义收紧**不会**让容器被判死。
  6. `scripts/Test-NasDeployment.ps1` 的步骤改名为「API /health (依赖 + 库结构版本)」，并且真的检查：`status` 必须是 `healthy`，`migration` 不能为空、`unknown`、`none`，失败时把整份 JSON 打出来。
  7. CI 的 `Health assertions` 步骤在真实 compose 栈上取 `migration` 并断言它不是 `unknown`/`none`：单测只能证明代码路径，只有跑在「容器真的迁移过」的栈上，这个字段才算被验证。
- 理由：
  - 一个健康检查的价值在于**它敢说哪些情况不健康**。只说「连得上」的检查，对「连得上但结构不对」这种真实事故完全沉默。
  - 用两个词（`unknown` / `none`）区分「问不到」和「问到了、答案是没有」，是因为这两种情况要做的处置完全不同：前者查权限/连接，后者去跑迁移。
  - 从库里读而不是从镜像里读：镜像里的版本是意图，库里的版本是事实。部署检查要的是事实。
  - 收紧 `status` 的代价是明确且有限的：CIS 与 NAS 上的 API 都会在 `alembic upgrade head` 成功后才对外服务，所以正常部署仍是 `healthy`；会变成 `degraded` 的正是那些本该被发现的情况。
  - 不在 `/health` 里比对「镜像期望的 head」：那需要在每个请求里读迁移脚本目录或把 head 缓存进进程，而部署脚本与 CI 已经能在栈外做更可信的比对（CI 断言字段存在且不是空词，NAS 脚本在真实部署上做同样的事）。
- 影响与兼容：
  - 响应多一个键（8 → 9 个）。按整份 JSON 做等值断言的调用方要更新；前端仪表盘只读 `database` / `redis` / `status`，不受影响。
  - `status` 可能在**没有依赖故障**的情况下变成 `degraded`（库连得上但没有迁移信息），这是这次改动的本意。
  - 使用 `Base.metadata.create_all()` 建库的开发/测试环境没有 `alembic_version` 表，会看到 `migration: "unknown"` 与 `status: "degraded"`——这正是「这个库不是被迁移建起来的」的准确描述。
  - 没有数据库迁移：只增加一次只读 `SELECT`。
- 测试：
  - `backend/tests/test_health_probe.py`：`set(body)` 的九个键里含 `migration`；用临时 SQLite 分别造出「没有版本表」「版本表空」「版本表有 `0008_github_pending_review`」三种库，断言 `unknown` / `none` / 该版本号；参数化断言 `status` 只在结构版本说得出来时才是 `healthy`，`none`/`unknown` 时是 `degraded` 且 `database` 仍报 `connected`。
  - `.github/workflows/ci.yml` 的 `Health assertions`：在真实 compose 栈上 `sed` 出 `migration` 并拒绝 `''`/`unknown`/`none`/`null`。
  - `scripts/Test-NasDeployment.ps1`：把「依赖 + 库结构版本」这一步做成真正的断言（对 NAS 部署实跑时由用户执行）。

## ADR-072：部署自检的每一步都必须能失败（`Test-NasDeployment.ps1` 的断言语义）

- 背景：给 NAS 部署做体检时，把 `scripts/Test-NasDeployment.ps1` 的十二个步骤逐条读了一遍，发现 ADR-071 的同类缺陷在脚本里还有六处——步骤存在、名字可信、但**没有任何一条能让它失败**：
  - `Web 容器 /healthz` 只要求 HTTP 200。nginx 默认页、失效的 upstream、门户劫持都会 200，所以这个步骤在「web 容器根本没在代理 API」时也报 OK。
  - `API /healthz (存活探针)` 里写着 `if ($null -eq $r) { 'empty body' }` —— 打一行字就通过，从不看 `status`。
  - `API /system/info` 把 version/modules 格式化出来就算过：一个没有 version、没有 modules 的响应会打印 `version= env= modules=0` 并记 OK。
  - 行情同步那一步的断言是 `if ($r.inserted -lt 0)`：插入行数不可能为负，恒假。
  - `创建策略版本（校验不可变触发器）` 的名字承诺了一次不可变校验，实际只创建版本并打印哈希。
  - `运行回测` 只断言 `status == completed`，从不看 `result_hash`，而「回测可复现」是本项目的红线。
  - `扫描信号` 与 `模拟盘账户列表` 只把收到的值打印出来。
- 这不是理论问题：ADR-071 修掉的那个步骤，名字写着「API /health (含依赖与迁移状态)」，而 `/health` 的响应里压根没有迁移字段。它能连续几个版本报 OK，正是因为脚本不会失败——一个只打印的步骤把「我不知道」说成了「没问题」。
- 决策：
  1. 新增两个断言辅助函数。`Assert-Value -Label <string> -Value <obj> [-Pattern <regex>]`：缺失、空白或不匹配正则即 `throw`，匹配则返回去空白后的文本。`Assert-Rejected -What <string> -Action <scriptblock>`：只有调用**抛出的错误是 4xx 校验类拒绝**（消息里含 422/409/400）才算通过，成功的调用反而 `throw`。
  2. `Web 容器 /healthz` 读响应体并要求其中出现 `"status":"alive"`：200 本身不是证据，响应体要说明自己是谁。
  3. `API /healthz (存活探针)` 用 `Assert-Value` 读 `status` 并要求 `alive`。
  4. `API /system/info` 断言 `version` 非空、`modules` 数量大于 0；新增可选参数 `-ExpectVersion`，提供时版本必须与它相等——这一步从此回答「我要部署的那个版本在答」而不是「有某个版本在答」。
  5. 行情同步断言响应里带 `inserted` 与 `series_id`，并回读 `/market-data/series/{id}/bars` 确认这个序列真的有数据（同步是幂等的，第二次运行合法地插入 0 行，所以断言的对象是同步报告 + 回读，而不是 `inserted > 0`）。
  6. `创建策略版本（校验不可变触发器）` 兑现名字里的承诺，用两件事证明不可变：`immutable_hash` 是 64 位十六进制且 `GET /strategies/versions/{id}/verify` 的 `stored_hash` 与 `recomputed_hash` 都等于它、`intact` 为真；以及**同一个版本号写第二次必须被拒绝**。
  7. `运行回测（result_hash 可复现）` 对同一份请求跑两次，两次 `result_hash` 必须相同——红线只有在两次相同哈希里才算被看见。
  8. 任何步骤失败，汇总都必须以 `exit 1` 结束（原有行为，现在有测试守着）。
- 理由：
  - 一个自检脚本的价值等于**它敢判定失败的次数**。只打印的步骤会把一次真实缺陷变成一行 OK，而 ADR-071 的教训正是这种步骤让缺陷藏了几个版本。
  - 断言要看**这个名字承诺的东西**：叫「校验不可变触发器」就该证明不可变，叫「运行回测」就该证明可复现。名字与检查不对齐时，名字本身就是缺陷的一部分。
  - 幂等性决定了不能要求 `inserted > 0`：重复检查时第二次合法地插入 0 行，要求 >0 会让每一次重复体检都误报失败。断言的是「响应带同步报告」加「序列回读有数据」。
  - 版本比对交给调用方：脚本不可能知道线上应该是哪个版本，部署的人知道，所以 `-ExpectVersion` 是可选的，不传时行为与从前一致。
  - `Assert-Rejected` 的 scriptblock 参数**不能叫 `Body`**，这是首次真栈运行咬到的：步骤体在传给它的 scriptblock 里读自己的 `$body` 载荷，而 PowerShell 变量名大小写不敏感，`$body` 于是解析到了辅助函数的 `$Body` 参数（也就是 scriptblock 本身），探针把辅助函数当请求体发了出去，报 `An item with the same key has already been added. Key: Value`，而被观察的 422 拒绝根本没发生。参数改名 `$Action`，并加了一条守卫测试钉住这个形状。
  - 这些检查全部在**真实部署**上执行，因此验证的是行为而不是文本；仓库里的测试只能钉住形状（见测试段），这也是 CI 的 compose smoke 与 NAS 上的实跑不可省略的原因。
- 影响与兼容：
  - `Test-NasDeployment.ps1` 现在会在这些情况失败（以前记 OK）：web 边缘对 `/healthz` 回 HTML；存活探针的响应没有 `status`；`/system/info` 没有 version/modules，或版本与 `-ExpectVersion` 不符；同步报告缺 `inserted`/`series_id`，或序列回读为空；版本哈希不是 64 位十六进制、`/verify` 说哈希不符或版本已改动、同一个版本号能写第二次；两次回测的 `result_hash` 不同；`/signals/scan` 没有 `evaluated`；`/paper/accounts` 返回空响应。正常部署不受影响（本机真栈 12/12 通过，NAS 上由用户执行）。
  - 新增可选参数 `-ExpectVersion <x.y.z>`，不传时与从前一致（只是不比对版本）。
  - 不改变任何 API、数据库或前端；这是纯运维脚本的语义收紧。
- 测试：
  - `backend/tests/test_nas_deployment_script.py`：十四条守卫，读脚本原文，用正则 `^\s*Step\s+(?:'([^']*)'|"([^"]*)")\s*\{` 切出每个步骤体（到下一个 `Step` 为止）。核心一条是「每条步骤体里必须出现 `throw` 或 `Assert-`」——不允许存在只能打印的步骤；其余逐条钉住各步骤名字承诺的断言（web 存活读响应体、API 存活读 `status` 且不再有 `empty body` 文案、`/health` 拒绝 `unknown`/`none`、`/system/info` 断言 version/modules 且声明并使用 `$ExpectVersion`、版本步骤双重证明不可变、`Assert-Rejected` 不把任意错误当成功、回测要求两次同哈希、全文不得出现 `-lt 0`/`-ge 0` 恒真比较、同步步骤读 `inserted`/`series_id`、模拟盘步骤会 throw、汇总能 `exit 1`），最后一条钉住上面那个 `$Body` 遮蔽陷阱（`[scriptblock]$Action` 与 `& $Action | Out-Null`）。
  - 行为证据（本机 Windows 没有容器运行时，所以用 `%TEMP%` 下一个 stdlib 边缘替身扮演 web 容器：`/` 回带 `<div id="app">` 的外壳，`/healthz` 与 `/api/*`、`/openapi.json` 按 `docker/web.nginx.conf` 的映射反代到 8080）：同一支脚本三次运行 —— ① 替身对 `/healthz` 回 nginx 默认页 → **11/12，exit 1**，唯一失败的正是 `Web 容器 /healthz`（`HTTP 200 但响应体不是 API 存活探针`）；② 正常替身 + `-ExpectVersion 1.5.9`（API 实际是 1.5.10）→ **11/12，exit 1**，唯一失败是 `/system/info`（`version=1.5.10，期望 1.5.9`）；③ 正常替身 + `-ExpectVersion 1.5.10` → **12/12，exit 0**。
  - 第三次运行的逐步骤结果：`Web /healthz HTTP 200, status=alive`；`HTML 200 html=101B`；`API /healthz {"status":"alive"}`；`API /health status=healthy migration=0008_github_pending_review database=connected redis=unavailable workers=unknown`；`/system/info version=1.5.10 env=development provider=synthetic modules=8`；`行情同步 inserted=0 total=400 quality=valid readback=5 bars`；`创建策略版本 hash=50065a58… intact=True 重复创建被拒（422 Unprocessable Entity）`；`运行回测 status=completed trades=3 hash=c523d2ae… (两次一致)`；`扫描信号 evaluated=4`；`AI 任务详情端点 两个路由均已注册；unknown id -> 404`；`模拟盘账户列表 accounts=0`。
