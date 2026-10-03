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
> 修订（ADR-079 / ADR-085）：上面 `v0.0.10` 这个示例是旧规则的写法，已废止 —— 第三段**永远**是一位，`v1.6.9` 之后是 `v1.7.0`；`scripts/version.sh set` 会直接拒绝多位的版本号。
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

> 修订（ADR-097）：上面第 1 与第 5 条把「API 只绑 127.0.0.1、经 Web 容器代理」当成了边界，实际上 Web 容器发布 `${WEB_BIND:-0.0.0.0}:8081` 并把 `/api` 代理出去（且会注入同一个 token，见第 3 条），所以默认部署对局域网是开放的，token 拦住的是绕过容器的客户端。真正关上它要改 `WEB_BIND=127.0.0.1`，或在前置反向代理 / 防火墙上做。
> 修订（ADR-103）：上面第 2 条的末句「/docs 与 /openapi.json 不在 /api/v1 下，保持可访问」已被取代 —— 豁免名单现在是一个只有两个探针的清单（`/api/v1/healthz` 与 `/api/v1/health`），文档面与 schema 和 `/api/v1` 一样需要 Token；Web 容器为这三扇门都注入同一个 Token，所以经 8081 的浏览器访问不变，变的是绕过容器直连 API 端口的客户端。

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
  `docker compose smoke test` 的「Boot the stack」也是同一个原因；同一提交的 `frontend build` 与 release 流水线是绿的——那条绿色的 release 流水线自己其实打印了 `SMOKE_TEST_FAILED`，只是被 `continue-on-error` 挡在结论之外（见 ADR-075）。
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

## ADR-073：自检脚本的结论必须变成退出码（`Test-EnsembleAttribution.ps1` 的判定语义）

- 背景：ADR-072 修的是部署自检脚本；同一次体检把仓库里所有自检脚本的 `exit` 逐个数了一遍（`scripts/*.ps1`、`scripts/*.sh`、`backend/scripts/*.py`）：`Invoke-FrontendChecks.ps1` 两处、`Invoke-Tests.ps1` 两处、`Test-NasDeployment.ps1` 两处、`Test-WebUi.ps1` 两处、`preflight.sh` 一处、`Start-LocalStack.ps1` 一处、`version.sh` 三处、`resource_baseline.sh` **零处**（但它只测量并写基线、不断言结论，所以零处是对的）、而 `scripts/Test-EnsembleAttribution.ps1` **也是零处**——可它不是测量脚本。
  - 它是 `docs/15_ROADMAP_ACCEPTANCE.md:42` 标为 DONE 的集成探针（v1.3.9 引入），也是唯一在**真实 HTTP** 上验证 `POST /research/ensemble` 与 `/research/ensemble/sweep` 契约的地方：ensemble 版本、成员贡献与联盟、按权重出资与出资总和、sweep 的阈值网格、`effective_vote` 必须是 `possible_votes` 里的联盟总和、`entries_taken` 单调、超限阈值必须被拒，二十多条不变量。
  - 它把这些结论收集进一个 `$ok` 布尔，最后只打印一行 `INVARIANTS: OK` 或 `INVARIANTS: FAILED`，然后脚本就结束了。全文件没有任何 `exit`（唯一出现的 `exit` 是 `:28` 的 DSL 键 `exit = @{...}`）。于是「每一条不变量都不成立」的一次运行仍然以 0 结束：放在 `bash -e`、CI 步骤或 `pwsh -File … ; if ($LASTEXITCODE -ne 0)` 后面，这个探针永远说成功。结论只活在一段没人读的控制台文本里。
  - 同一支脚本还有第二处「说了不做」：它把 `POST /market-data/sync`（`:43-44`）与两个成员回测（`:57-59`）的响应 `| Out-Null` 丢掉，backtest summary identity 那张表（`:61-64`）只打印不校验，而脚本头部（`:4-7`）自称会断言 dataset identity —— 也就是说，它的两个成员回测可以分别建在不同序列上，探针照样说 OK；而如果同步根本没有建立序列（provider 什么都没给），它会拿空数据一路算下去，然后把「没有数据」报成「全部成立」。
  - `.github/workflows/nightly.yml` 只重建镜像，没有任何自检作业，所以这些脚本全靠人手跑；而人手跑的脚本如果只把结论打在屏幕上，就等于没有结论。
- 决策：
  1. `Test-EnsembleAttribution.ps1` 的每条不变量失败都记进 `$failures`（`function Fail([string]$Message)`：记录并打印 `FAIL: …`，消息里带实际数值，例如 `engine_version 'ensemble-9.9.9' != derived from ensemble_version '1.2.0'`）。
  2. 结论必须是脚本的最后动作，并且**变成退出码**：`$failures.Count -gt 0` 时打印 `INVARIANTS: FAILED (n)` 并逐条列出、`exit 1`；否则打印 `INVARIANTS: OK`、`exit 0`。用法注释写明「Exit codes: 0 = every invariant held, 1 = at least one did not」。
  3. 不再丢弃响应：`/market-data/sync` 的报告被读进来并断言有 `inserted` 属性、`series_id` 非空；缺 `series_id` 即 `Fail`，理由是「插入 0 行」是幂等同步的合法结果，而「没有序列」意味着接下来所有关于 bars 的检查都在验证一个并不存在的数据集。
  4. 两个成员回测的响应也被读进来：断言 `strategy_version_id` 相等、`status` 是 `completed`、`result_hash` 是 64 位十六进制、`symbol`/`timeframe` 与本探针一致；`bars_evaluated` 必须大于 0。
  5. backtest summary identity 那张表要真的校验：按 `id` 唯一匹配每个成员回测对应的那一行（必须恰好一行），并断言该行的 `dataset_hash` 与回测响应里的一致。
  6. 新增守卫测试 `backend/tests/test_self_check_verdicts.py`：每个 `Test-*.ps1` 必须同时含 `exit 0` 与 `exit 1`；`Test-EnsembleAttribution.ps1` 的退出块里必须出现 `$failures`，且最后一个非空行必须是 `exit 0`。
- 理由：
  - 一个自检脚本的结论有两个读者：人读屏幕，下一个程序只读退出码。只打印不退出，等于对第二个读者永远说成功。ADR-072 的 `Test-NasDeployment.ps1` 是同一个病的另一种形状（调用它的人也不看屏幕），这一版把同类审计推到所有自检脚本上，并让唯一一个「有判定能力却没有退出码」的脚本补上它。
  - 「先写测试、再改脚本」在这里是刻意的：守卫测试在旧脚本上是 **4 条失败 / 2 条通过**（红），改完才 6 条全绿。这样测试证明的是它抓到了真实缺陷，而不是它跟实现长得像。
  - 丢弃响应等于放弃断言：`| Out-Null` 之后脚本无法知道同步有没有建立序列、两个成员是不是同一份数据，identity 表的每一行都可能是空的，而「非空数组为真」会让所有判断通过。
  - 修完之后第一轮真栈运行又咬到**我自己新写的假通过**：`$summary = @(Invoke-RestMethod "$Base/backtests")` 中，PowerShell 把 cmdlet 的 JSON 数组包成了一个「单元素数组，而那个元素本身是 `Object[]`」，于是 `$row.dataset_version_id` 变成成员枚举、返回的是数组、每个判断都以「非空数组为真」通过，而屏幕上那张 identity 表是**空行** —— 探针在对它从没看过的行报 OK。修法是先赋值再包（`$summaryResponse = Invoke-RestMethod …`，再 `if ($null -ne $summaryResponse) { $summary = @($summaryResponse) }`），并加一条显式守卫：如果 `$summary[0]` 本身是数组就 `Fail "the backtest list came back nested, so the identity rows are not rows at all"`。教训与 ADR-072 的 `$Body` 遮蔽同类：**新写的断言本身也要被真栈验证**。
- 影响与兼容：
  - `scripts/Test-EnsembleAttribution.ps1` 现在会在这些情况以 1 结束（以前一律 0）：同步没有建立序列；成员回测不完整、`result_hash` 不是 64 位十六进制或两份数据不一致；`bars_evaluated` 为 0；任何一条 ensemble/sweep 不变量不成立。正常栈上仍然是 0。
  - 谁调用它就会开始看见失败：手工运行时多一行 `INVARIANTS: FAILED (n)` 与逐条原因（这些原因现在带实际数值）。目前没有 CI 作业调用它（`nightly.yml` 只重建镜像），这条改动只是让「调用它」这件事从这一版起有意义。
  - 不改变任何 API、数据库或前端；纯自检脚本的语义收紧。
- 测试：
  - `backend/tests/test_self_check_verdicts.py`：六条守卫，读 `scripts/` 下 `Test-*.ps1` 的原文并把 PowerShell 反引号续行折成一行：这些脚本存在；每个 `Test-*.ps1` 必须同时含 `exit 1` 与 `exit 0`；`Test-EnsembleAttribution.ps1` 的退出块里必须出现 `$failures` 且文件最后一个非空行必须是 `exit 0`；它必须真的检查数据（`market-data/sync`、`series_id`、`result_hash`、字面量 `'^[0-9a-f]{64}$'`、`dataset_version_id`、`bars_evaluated`）；折行后不得有把 `/market-data/sync` 或成员回测响应 `| Out-Null` 掉的语句；sweep 契约的 `max_thresholds`/`possible_votes`/`effective_vote`/`engine_version` 仍在（防止重写时把检查丢掉）。
  - 行为证据（本机真栈：SQLite + `MARKET_DATA_PROVIDER=synthetic` 的 API 跑在 8080；替身是 `%TEMP%` 下一个 stdlib 反代，可在反代时把 `/research/ensemble*` 的 `engine_version` 改写成 `ensemble-9.9.9`，或从同步报告里删掉 `series_id`）：① 直连 API → **exit 0**，identity 表四行真实数据、`market data … inserted=0 series_id=1`（第二次运行，幂等插入 0 行）、`INVARIANTS: OK`；② 纯反代替身（对照组，证明失败不是替身造成的）→ **exit 0**；③ 改写 `engine_version` → **exit 1**，恰好两条 FAIL（ensemble 与 sweep 的 `engine_version` 推导不一致），`INVARIANTS: FAILED (2)`；④ 同步报告缺 `series_id` → **exit 1**，一条 FAIL（`the sync established no series (no message) -- there are no bars to verify against`），`INVARIANTS: FAILED (1)`。

## ADR-074：客户端的每一次调用都是一句关于服务端的断言（`frontend/src/api.ts` ↔ OpenAPI 契约守卫）

- 背景：v0.9.9 的根因是 `frontend/src/views/SettingsView.vue` 调用了 `api.aiTask(id)`，而 `frontend/src/api.ts` 从来没有定义过这个方法；唯一抓住它的是 `vue-tsc` 的 TS2551，也就是说那一版的 Actions 失败是**类型检查**救回来的。
  - 这次体检要问的是它的反面：类型系统证明客户端能编译，不证明它调用的那扇门存在。`frontend/src/api.ts:8 const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'`，`:20 async function request<T>(path: string, init?: RequestInit): Promise<T>` —— `request<T>` 里的 `T` 是未校验的断言，`path` 只是一段字符串。客户端写 `/strategies` 而服务端只有 `/strategy-versions` 时，`vue-tsc`、`ruff`、`pytest` 全部绿灯，第一次发现是浏览器控制台里一条红色 failed request。
  - 门是唯一被写两遍的东西：`backend/app/api/main.py:208-229` 用 `app.include_router(router, prefix=settings.api_prefix)` 挂上 19 个 router（前缀 `/api/v1`）。真实门表只有 `app.openapi()["paths"]` 能给出——本机 FastAPI 版本下 `app.routes` 里是 19 个 `_IncludedRouter` 包装对象，**没有 `.path` 属性**。客户端一侧约 80 个 `request<...>(...)` 调用点，路径是字符串字面量或模板字面量（`/backtests/${id}`、`/backtests${flag ? '?status=…' : ''}`）。
  - 先审计、再决定。三个探针分别查门（路径 + 方法）、查旋钮（查询键双向）、查载荷（客户端接口字段 vs 响应 schema），结论是**今天没有任何漂移**：80 个调用点全部落在真实门上（另 2 个是条件查询串归一化造成的假象）；客户端发送的每个查询键都已声明；API 标为 required 的查询参数客户端都发送；27 份可比对的响应里客户端声明的顶层字段全部存在。
  - 同一次审计也证明了这类守卫**最容易死于假阳性**：第一版字段探针用扁平正则 `^\s*(\w+)\??\s*:` 读接口字段，把嵌套对象里的字段也当成顶层字段，于是 `SensitivityResult` 的 `summary: { mean, median, stdev, min, max, range, positive_ratio }`、`MonteCarloResult`、`EnsembleResult` 三处被报成「客户端字段不在 schema 里」——三条全是假的。人一旦学会忽略这种告警，守卫就等于不存在。
- 决策：
  1. 新增 `backend/tests/test_api_contract.py`，把「客户端调用 ↔ 服务端门」变成 CI 里会失败的断言，共四类检查：每个 `(path, method)` 必须由 API 服务；客户端发送的查询键必须已声明（条件查询串里的键按「可能发送」只做这一向检查）；API 声明为 required 的查询参数必须被无条件发送；客户端响应接口在**深度 0** 声明的字段必须出现在该响应的 schema 里。
  2. 扫描器本身要可信：用 `request` 标识符定位调用，泛型、括号、字符串、注释、模板字面量都做平衡扫描；模板表达式按「是否含 `?`」分成条件查询串（`${flag ? '?key=1' : ''}`，其中的键记为条件键）与路径段（归一成 `{}`）；数字路径段与 `${id}` 一样归一成 `{}`，因为 `'/backtests/12'` 与 `` `/backtests/${id}` `` 是同一个请求。
  3. 字段比较必须深度感知：`_declared_fields` 只在接口体深度 0 收集「标识符紧跟 `:` 或 `?`」的名字，嵌套 `{ … }` 里的名字不算字段——这条正是上面三条假阳性的解药。
  4. 守卫要有下限，否则解析器失灵时它会以「全绿」的方式失效：操作数 ≥ 90、调用点 ≥ 60、不同路径 ≥ 50、可比对的响应 ≥ 20。解析器读不懂客户端了，这些下限先失败。
  5. 守卫必须被证明会咬人：同一个文件里喂进一个故意坏掉的客户端（不存在的门、门不提供的方法、未声明的查询键、漏掉的必填参数），四个检查各咬一条；再喂一个守规矩的客户端，必须一条都不报——后者里特意留了 `summary: { mean: number | null; nonsense: number | null }`，把「嵌套字段不是字段」钉死。
- 理由：
  - 缺的是没有编译器的那个方向。v0.9.9 的方向（页面调 `api.ts` 里不存在的方法）由 `vue-tsc` 兜住；反方向（客户端调服务端没有的门、发服务端不认的键、声明服务端不发到顶层的字段）没有任何一层兜住，而它同样只在运行时暴露。
  - 为什么不以此版做真栈或浏览器取证：这一版没有改任何运行时行为，改的是「谁在什么时候检查」。真正的证据是负向注入——把真实 `api.ts` 的原文分别改一处（门改名、方法换成 `DELETE`、删掉必填的 `ids=`、在 `SensitivityResult` 里加一个不存在的字段），四个检查各自报出一条，而未改动的原文报 0 条。这比跑一次真栈更能证明守卫会咬。
  - 宁可少查，也不要假报。字段检查只覆盖 schema 是普通对象（或数组、联合）的 27 份响应，动态构造的 schema（`/health`、`/system/info`、`POST /strategies` 等）直接跳过。范围写进 ADR，而不是让守卫在将来某次重命名里突然变红。
  - 不把检查降级成「警告」：ADR-072 与 ADR-073 的教训是同一条——不能失败的检查等于没有检查。
- 影响与兼容：
  - 新增一个纯静态测试文件（读 `frontend/src/api.ts` 与 `create_app().openapi()`），不改 API、不改数据库、不改前端运行时，`api.ts` 一行未动。
  - 从此这一类改动会在 CI 里失败：调用不存在的门、调用门不提供的方法、发送未声明的查询键、漏掉必填查询参数、在响应接口深度 0 声明 API 不发送的字段。
  - 边界与代价：字段检查只对 27 份可比对响应生效（其余跳过）；条件查询串里的键只检查「是否已声明」，不算作满足必填参数（一个可能不发送的键不能满足一个总是要求的参数）；泛型参数里若出现箭头类型（`=>`）会让 `<`/`>` 配对失准，靠第 4 条下限兜住。今天的行为变化为零：守卫在现行代码上 8 条全绿，新增的失败面全部属于**将来**。
- 测试：
  - `backend/tests/test_api_contract.py`：八条。`test_the_client_source_is_where_this_guard_expects_it`（文件与默认基址 `/api/v1` 都还在，防止守卫指向错文件而「通过」）、`test_the_scan_finds_the_calls_and_the_doors`（四条下限）、`test_every_client_call_names_a_door_the_api_serves`、`test_the_client_sends_only_declared_query_keys`、`test_the_client_sends_every_required_query_key`、`test_the_client_declares_no_field_the_api_never_sends`（并要求 `compared >= 20`）、`test_the_contract_checker_bites_on_a_broken_client`、`test_the_contract_checker_accepts_a_client_that_keeps_the_contract`。
  - 审计与注入证据（`%TEMP%` 下的一次性脚本，不入库）：门/旋钮探针在真实 `api.ts` 上找到 80 个调用点、78 个落到真实门（另 2 个是条件查询串归一化假象，随后修正）；深度感知的字段探针比对 27 份响应、0 个问题；负向注入四处改动各产生恰好一条：`GET /backtests/compare_all`（门不存在）、`DELETE /strategies`（方法不对）、`GET /backtests/compare: omits required query key 'ids'`、`SensitivityResult for POST /research/sensitivity: declares field 'nonsense_field' the response schema does not describe`；未改动的原文 0 条。

## ADR-075：发布的验收必须决定运行的结论（`release.yml` 的 `SMOKE_TEST_FAILED` 与 `continue-on-error`）

### 背景

- `release.yml` 打完三镜像（backend/proxy/web）之后，用仓库自己的 `docker-compose.yml` 起一次真栈做「发布后验收」：按 `:${VERSION}` 拉取刚推上去的镜像、`docker compose up -d`、轮询健康，最后打印 `SMOKE_TEST_OK` 或 `SMOKE_TEST_FAILED`。
- v1.5.2 的那次发布（run **`37084889121`**，headSha `b6e2c6d3c`，镜像元数据 `version=1.5.2`）验收**失败**了。日志原文：`compose up exit: 1   healthy: 0` → `SMOKE_TEST_FAILED` → `##[error]Process completed with exit code 1.`；起因就是 ADR-064 那条迁移 id 放不进 `alembic_version.version_num VARCHAR(32)`：

  ```
  psycopg.errors.StringDataRightTruncation: value too long for type character varying(32)
  [entrypoint] ERROR: migrations failed; refusing to start
  dependency failed to start: container quantlab-api is unhealthy
  ```

  也就是说：**那一版发布出去的镜像起不来**。
- 而这次运行报告的结论是 **success**，`Smoke test the released images` 这一步在运行摘要里也显示成 `completed/success`；GitHub Release v1.5.2 照常创建，发布说明还写着 `docker compose pull && docker compose up -d`。
- 原因是一行 YAML：`release.yml:147 continue-on-error: true`（全仓库唯一一处 `continue-on-error`）。它把验收的失败从「运行的结论」里摘了出去，只留在没人会因为一枚绿色徽章去翻的日志里。ADR-064 记录了同一提交的 CI 是红的，所以缺陷 16 分钟后就被修掉（`97f9dab58`，v1.5.3）；但**发布流水线自己的判断**沉默了一整轮。
- 同一处验收还有第二个洞：它只问 API（`curl http://127.0.0.1:8080/api/v1/healthz`）。`docker compose up -d` 在 web 容器崩溃重启时同样返回 0，所以「web 镜像起不来」在旧验收下一样会得到 `SMOKE_TEST_OK`。

### 决策

1. 删掉 `continue-on-error: true`。`Generate release notes` 与 `Create GitHub Release` 两步本来就带 `if: always()`，所以**发布照旧发生、运行的结论改成失败**——`if: always()` 才是「失败了也要接着做」的表达方式，`continue-on-error` 是「失败了就当没发生」。工作流里留了注释说明为什么不能把它加回去。
2. 验收同时问两个容器：轮询里维护 `api_ok` 与 `web_ok`。`web_ok` 要求三件事同时成立——`http://127.0.0.1:8081/healthz` 通、`/` 返回的页面里有 `id="app"`（是应用而不是 nginx 欢迎页）、缺失的哈希资源返回 404 而不是应用外壳（把 ADR-068 的边界契约放到真部署上再验一次）。最终结论要求 `up_rc = 0` 且 `api_ok = 1` 且 `web_ok = 1`；失败时把三者一起打印，诊断摘要里补上 `quantlab-web` 的日志。
3. 新增离线守卫 `backend/tests/test_release_pipeline.py`（6 条，读工作流文本、不需要 Docker）：任何工作流都不得再出现 `continue-on-error`；验收步骤必须同时引用 8080 与 8081；`web_ok` 必须真的来自断言而不是只取一次响应（`healthz`、`id="app"`、`/assets/` 与 `404`）；失败分支必须看 `web_ok`；验收必须能 `exit 1`；`Create GitHub Release` 必须保留 `if: always()` 且排在验收之后；验收必须拉 `:${VERSION}` 而不是 `latest`。

### 理由

- **判断的意义在于有人读到它**：ADR-072 说「部署自检的每一步都必须能失败」，ADR-073 说「自检的结论必须变成退出码」，这一条是同一句话的第三面——结论必须进入**流水线自己的记录**。我向用户汇报「已发布、CI 与 release 全绿」时读的就是这个结论；被 `continue-on-error` 摘掉的失败会让我把一个起不来的版本说成可以部署，这才是真正的危害。
- **发布与验收是两件事，顺序是刻意的**：镜像在验收之前就已经推进 GHCR，Release 页面是 NAS 用户唯一的入口；扣住它既不会撤回镜像，又会藏掉「怎么固定版本」的说明。要改的是结论，不是发布。
- **一个版本三个镜像，就该有一个覆盖三者的结论**：只问 API，等于用一半的证据宣布另一半没问题。
- **注释不是配置**：守卫读工作流文本时先丢掉 `#` 开头的行，否则它会栽在自己写下的解释上（这次实现时第一跑就被咬到：注释里出现了 `continue-on-error` 与 `SMOKE_TEST_FAILED` 两个词）。

### 影响与兼容

- 从此 release 运行的**红**意味着「已发布，但别部署」。诊断（起栈退出码、两个容器的状态与日志）在设计上已经写在步骤摘要里，因为它们在 `exit 1` 之前收集。
- 若验收因为环境原因偶发失败（例如 runner 上 8081 被占），运行会变红——它说的是「这次验收没通过」，不是「镜像坏了」，诊断里能分辨这两者；这是刻意接受的代价，比沉默更便宜。
- NAS 的部署方式不变（`docker compose pull && docker compose up -d`），用 `MQL_VERSION` 固定版本的做法不变。
- v1.5.13 自己的 release 运行是第一个「验收结论参与运行结论」的发布：它必须是绿的，且日志必须以 `SMOKE_TEST_OK` 结尾。

### 测试

- `backend/tests/test_release_pipeline.py` 6 passed（先写守卫再改工作流：改之前 2 failed / 4 passed，红点正是 `release.yml:147: continue-on-error: true` 与 `the smoke test never asks the web container`）。
- 语法与解析：`bash -n` 检查从工作流里抽出的步骤脚本 exit 0；PyYAML 解析 `ci.yml`/`release.yml`/`nightly.yml` 三个文件，`Smoke test the released images` 的键只剩 `env/id/name/run`。
- 行为复核（本机没有 Docker，用标准库替身在 127.0.0.1:8080/8081 扮演两个容器，把工作流里那段轮询原样抽出来跑（用 Actions 的方式 `bash -eo pipefail`，`seq 1 30` 改成 `seq 1 1`））：
  - `good`（API 存活 + 应用外壳 + 缺失资源 404）→ `api_ok=1 web_ok=1`；
  - `default-page`（web 只答 nginx 欢迎页）→ `api_ok=1 web_ok=0`；
  - `spa-fallback`（所有路径都回应用外壳，含缺失资源）→ `api_ok=1 web_ok=0`。
  - 同一替身上跑**旧的** API-Only 判据：三种模式全是 `healthy=1`，即旧代码在 web 一半坏掉时照样宣布 `SMOKE_TEST_OK`。
- v1.5.14 把这段判定抽成唯一一份 `scripts/verify-stack.sh`：release 的 smoke 步骤改为调用它，nightly 也开始用同一份（见 ADR-076）。

## ADR-076：夜间的镜像必须是一套能起来的、且证明过的镜像（`nightly.yml` 的镜像集合与验收）

- 背景：`.github/workflows/nightly.yml` 每夜把 main 构建成两个镜像推到 GHCR —— `ghcr.io/<owner>/my-quant-lab-backend:nightly` 与 `-web:nightly`（`:34`、`:43`），推完即结束。四件事同时成立，而且每一件都能从别处证明：
  - **集合不完整**：`release.yml` 发布**三个**镜像（`docker/Dockerfile.backend`、`docker/Dockerfile.proxy`、`docker/Dockerfile.web`），nightly 少了 proxy。而 `docker-compose.yml:106-131` 的 `quantlab-docker-proxy` 是默认服务、不在任何 `profiles:` 里，镜像名是 `ghcr.io/bobvane/my-quant-lab-docker-proxy:${MQL_VERSION:-latest}`（`:110`）。于是 `MQL_VERSION=nightly docker compose up -d` **必然**拉不到 `my-quant-lab-docker-proxy:nightly` —— `:nightly` 不是一套可以部署的镜像，它连仓库自己的 stock compose 文件都起不来。
  - **缓存是单向的**：两个 build 步骤都只有 `cache-from: type=gha`、没有 `cache-to`（`:35`、`:44`），而 `release.yml` 的三处是成对的（`:98-99`、`:131-132`、`:142-143`）。夜里那次构建只是读一份自己从不贡献的缓存。
  - **从来没有被启动过**：推完镜像就结束，没有任何一步问过「这套镜像能不能起来」。ADR-073 已经把这件事记在案（docs/17:1748：「`.github/workflows/nightly.yml` 只重建镜像，没有任何自检作业，所以这些脚本全靠人手跑」），但记在案不等于有人做。
  - **判定的位置**：v1.5.13 的验收（ADR-075）是内联在 `release.yml` 的 smoke 步骤里的（30 行轮询加判定），只在 release 那条路上存在。nightly 若照抄一份，两份判定必然漂移 —— ADR-070 已经为审计账本定过这条规矩：一个事实只有一个出口。
- 决策：
  1. 验收只有一份实现：新增 `scripts/verify-stack.sh`，问两半 —— API 的存活端点是否答，web 边缘是否在服务应用（`/healthz` 通、`/` 里有 `id="app"`、缺失的哈希资源是 404 而不是应用外壳，ADR-068）。参数为 `--api`/`--web-base`/`--attempts`/`--interval`；退出码 0 = 两半都答了，1 = 至少一半没有，**2 = 参数错**（问不出问题的调用不算通过）。
  2. `release.yml` 的 smoke 步骤不再自己轮询，改为 `bash scripts/verify-stack.sh`，并用 `set -o pipefail` 让脚本的退出码穿过 `tee` 写进 step summary；工作流保留拉镜像、诊断、`down -v`，以及 `SMOKE_TEST_FAILED`/`SMOKE_TEST_OK` 的结论。
  3. `nightly.yml` 补齐第三个镜像（与 release 同一集合），三处 build 都补上 `cache-to: type=gha,mode=max`；新增 `verify` 作业（`needs: images`、独立 runner），把 `:nightly` 从 registry 里拉回来、用 `MQL_VERSION=nightly` 起 stock compose、调用同一份判定，失败时 `NIGHTLY_VERIFY_FAILED` 加 `exit 1`，最后 `down -v` 收拾干净。
  4. 守卫：新增 `backend/tests/test_nightly_pipeline.py`，并改写 `backend/tests/test_release_pipeline.py` 里那条 web 断言 —— 夜里构建的镜像集合必须与 release 一致、每个 `cache-from: type=gha` 必须在同一文件里有配对的 `cache-to`、verify 作业必须按 nightly tag 拉起三个镜像并调用同一份判定、两条流水线都必须调用 `scripts/verify-stack.sh`、判定脚本必须是 LF 且必须能失败。
- 理由：
  - **一个 tag 就是一句承诺**：`:nightly` 承诺「main 上最新的一套镜像」；少了 proxy 就不是一套，「最新」也就没有意义。
  - **判定是代码，不是配置**：写成脚本才能被两条流水线调用、才能在本地被证明会失败；内联两份必然漂移，而漂移的判定比没有判定更糟 —— 它看起来像有。
  - **独立作业比同作业多一步更强**：镜像必须从 registry 里再取回来，而不是复用构建作业留在本地的层。
  - **`cache-from` 没有 `cache-to` 是单向声明**：它读一份自己从不写的缓存；守卫把它变成可见的等式，而不是读代码时靠印象。
  - 与 ADR-072/ADR-073/ADR-075 是同一条线：能失败、结论变成退出码、发布的验收必须决定运行的结论 —— 这里补上最后一环：**夜间的产物也要有一条结论**。
- 影响与兼容：
  - `:nightly` 现在多一个 proxy 镜像，且每次构建都要验收通过才算完成；夜里验收失败时运行会变红（以前永远是绿的）——那是本版的目的，代价是「镜像已推、但 tag 不可信」这个中间状态第一次变得可见。
  - nightly 现在也写 GHA 缓存（`mode=max`），会占缓存配额；换来的是它不再白读。
  - `release.yml` 从 305 行降到 279 行，判定搬进 129 行的脚本；两处行为在 good/default-page/spa-fallback/api-down 四态下逐字比对过（见「测试」）。
  - 本机没有 Docker，无法在本地真跑 compose：脚本级证明用替身完成；真机证明是 v1.5.14 自己的 release 运行（走同一份脚本）与手工触发的 nightly 运行。
- 测试：
  - `backend/tests/test_release_pipeline.py`（改后 7 条）与 `backend/tests/test_nightly_pipeline.py`（6 条）。**先红后绿**：把 HEAD 版（v1.5.13）的 `nightly.yml`/`release.yml` 放回原位跑这两个文件 → **6 failed, 7 passed**，失败信息逐条点名：`the nightly and release pipelines build different image sets, so :nightly is not a usable tag: nightly=['docker/Dockerfile.backend', 'docker/Dockerfile.web'] release=[... 'docker/Dockerfile.proxy' ...]`、`a workflow reads a build cache it never writes: ['nightly.yml: 2 cache-from, 0 cache-to']`、`no job named 'verify'`、`the smoke test no longer asks the shared verdict`；随后工作副本按 SHA256 逐字还原（两处 `RESTORED`）。
  - 行为证据（标准库替身扮演两个容器、直接跑真脚本）：`good → exit 0 / api_ok: 1 web_ok: 1 / VERIFY_STACK_OK`；`default-page`（web 只答 nginx 欢迎页）`→ exit 1 / web_ok: 0`；`spa-fallback`（所有路径都回应用外壳，含缺失资源）`→ exit 1 / web_ok: 0`；`api-down`（API 503）`→ exit 1 / api_ok: 0`；`--nonsense → exit 2`。
  - 语法与解析：PyYAML 解析 `ci.yml`/`release.yml`/`nightly.yml` 成功（`nightly.yml` 的作业为 `images` 与 `verify`）；三条工作流里每个 `run:` 体经 `bash -n` 全部 exit 0；`release.yml` 保持 CRLF 干净（279 行、0 bare LF），`nightly.yml` 与 `scripts/verify-stack.sh` 保持 LF。
  - 边界：这些守卫读文本，它们能证明集合一致、判定被接上、脚本会失败；它们不能证明某个 runner 上容器真的起来了 —— 那由 release 与 nightly 两条流水线的真机运行回答。

## ADR-077：生产环境不能带着示例密钥启动（`SECRET_KEY` 的生产校验、入口点顺序与文档里的密钥）

- 背景：`.env.example:13` 发出去的是 `SECRET_KEY=change-me-openssl-rand-hex-32`，而 `docker-compose.yml:32` 对它的要求只是「存在」（`SECRET_KEY: ${SECRET_KEY:?SECRET_KEY is required}`）——示例值满足这条要求。同一份 compose 文件 `:27` 写着 `APP_ENVIRONMENT: ${APP_ENVIRONMENT:-production}`、`.env.example:46` 写着 `APP_ENVIRONMENT=production`，所以照 README「拷 `.env.example` 改名 `.env`、至少改两处」的路径起来的部署**跑在生产模式**。问题在于 `secret_key` 不只是签名种子：`backend/app/infrastructure/secrets.py:27/40/50` 用 `sha256(settings.secret_key)` 派生存放密钥的 Fernet/XOR 密钥，于是「仓库里出现过的值」等于把库里加密保存的 provider 密钥（Ghostfolio token、AI API key）交给任何拿到数据库副本或读过本仓库的人。`backend/app/core/config.py:74` 当时是 `secret_key: str = Field(default="change-me-in-production")`，**没有任何生产校验**；README 当时还教所有人粘贴同一个 `0123456789abcdef0123456789abcdef`（32 位、不含任何占位标记、必然通过），即「公开的密钥」有第二个来源。第二半问题不在密钥而在顺序：`docker/entrypoint.sh` 的 `wait_for_db()`（`:35-62`）先跑，且把配置自检的 python 输出 `>/dev/null 2>&1`（`:52`），于是「密钥是示例值」表现为 30 轮 `waiting for database` + 60 秒后 `alembic upgrade head` 三连失败 → `ERROR: migrations failed; refusing to start`（`:79-81`），**用 75 秒指向错误的组件**。
- 决策：
  1. `Settings` 增加 `@model_validator(mode="after") def _refuse_the_example_secret_in_production()`：只在 `production` 下校验，其余环境原样放行（本地 `cp .env.example .env` 照旧能跑）。
  2. 生产下依次拒绝四种情况：空值、含公开占位标记（`_SECRET_PLACEHOLDERS`，含 `change-me`、`example`、`your-` 等）、**与仓库发布过的字面值完全相同**（`_PUBLISHED_SECRETS`：`change-me-openssl-rand-hex-32`、`change-me-in-production`、README 曾发布的 `0123456789abcdef0123456789abcdef`）、长度不足 `_SECRET_MIN_LENGTH = 32`；三条消息都点名补救命令 `openssl rand -hex 32` 与 `.env`。
  3. `docker/entrypoint.sh` 新增 `check_settings()`（`python -c 'from app.core.config import settings; print(settings.environment)'`，成功记 `configuration accepted (environment=…)`，失败把真实异常逐行加 `[entrypoint] ` 前缀并 `return 1`），并在 `api)`/`worker)`/`scheduler)`/`migrate)` 四个角色的 `wait_for_db` **之前** `check_settings || exit 1`。
  4. 起 stock compose 的两条流水线（`release.yml`、`nightly.yml`）不再把固定值 `ci-smoke-secret-key-0123456789abcdef` 写进 `.env`，改为 `export SECRET_KEY="$(openssl rand -hex 32)"` 与 `export POSTGRES_PASSWORD="$(openssl rand -hex 16)"`（export 的值优先于 `--env-file`），即「能起栈的人自己生成密钥」。
  5. README 的两处安装说明改成自己生成（`openssl rand -hex 32`），不再给出可直接粘贴的字面值。
  6. 新增 `backend/tests/test_production_secret.py` 12 条守卫：直接拿 **`.env.example` 当天的值**喂生产 `Settings` 必须被拒；短值、空值与补救命令文案；本地开发仍接受示例值；入口点四个角色都先自检后等库；两条流水线都生成密钥且不再含 `ci-smoke-secret-key`；compose 仍要求密钥存在；**文档里出现的每一个 `SECRET_KEY=` 值在生产下都必须不可用**（在 README 里粘一个自己生成的真密钥会被这条挡住）。
- 理由：「示例值存在」与「示例值可用」是两件事，`${VAR:?required}` 只能保证前者，所以校验必须落在读到值的那一层（应用），而不是落在编排层。校验只在生产生效，是因为这个默认值本来就是给本地开发用的，把它一并禁掉只会让本地跑不起来而不增加安全性。拒绝理由要包含补救命令，是因为失败信息是正在部署的人唯一会读的文档。`secret_key` 被用作 KDF 输入这一点让它的取值变成安全属性而非风格问题：公开的值等于公开的派生密钥，所以「仓库里曾出现过的值」必须按已泄露处理。两类发布值用两种匹配：占位标记用子串（包含 `change-me` 的不可能是生成出来的），发布过的字面值用精确比较——第一版把 `0123456789abcdef` 也当子串标记，全量测试当场指出它会连带拒掉测试库自己的 `test-secret-key-0123456789abcdef` 以及任何以这 16 位开头的真随机密钥，而这类拒绝换不来任何安全性，所以改成整值比较。入口点先自检再等库，是因为配置错误会在等待循环里伪装成数据库慢；把真实异常原样打出来，是把 75 秒的误导换成 3 秒的答案。CI 用的固定密钥与本版修的是同一个缺陷的另一种形态——流水线自己的密钥也不该是仓库里的字面值。
- 影响与兼容：**破坏性变更（有意）**：`APP_ENVIRONMENT=production` 且 `SECRET_KEY` 仍是示例值、README 旧值、或短于 32 字符的部署，现在会在启动时被拒并打印补救命令；按 README 新说明生成密钥即可。`test`/`development` 与 CI 不受影响（`ci.yml` 的 compose 作业显式设 `APP_ENVIRONMENT: ci`，`scripts/Invoke-Tests.ps1:37` 设 `test`，`scripts/Start-LocalStack.ps1:28` 设 `development`）。`scripts/preflight.sh:44-53` 对 `.env` 的占位检查仍在（面向 `docker compose` 之前的人），现在应用自身也拒绝，属两道门而非重复。发布过的字面值按整值比较，随机密钥只有整个值正好撞上才会被拒（16⁻³² 量级）；以那 16 位开头的密钥照常可用。NAS 部署者若之前照抄 README 的旧值，需要重新生成密钥；已经用旧值加密保存的 provider 密钥要用新密钥重新存一遍才能被读回。
- 测试：`backend/tests/test_production_secret.py` 13 条全绿（`Invoke-Tests.ps1 -Keyword production_secret` = `13 passed, 664 deselected`），`ruff check app tests` 与 `ruff format --check app tests` 干净。红证据：把 `backend/app/core/config.py`、`docker/entrypoint.sh`、`.github/workflows/release.yml`、`.github/workflows/nightly.yml`、`README.md` 五个文件换回 HEAD（v1.5.14）后跑同一批守卫 = **9 failed / 4 passed**，随后逐字节还原（五个文件 SHA256 全部 `match=True`）。行为证据：在 `backend` 目录下用真 `docker/entrypoint.sh` + `APP_ENVIRONMENT=production` + 示例密钥 = **exit 1、耗时 3 秒、`waiting for database` 0 行**，日志里同时出现 `configuration is not usable; refusing to start` 与 `SECRET_KEY still holds an example value (it contains 'change-me')`；换成 `openssl rand -hex 32` 后同一份 `check_settings` 打印 `configuration accepted (environment=production)`。


## ADR-078：部署自检必须读正在部署的那两个文件（`scripts/preflight.sh` 重写、`build:` 段的归属、CI 必须在起栈前跑它）

- 背景：把 NAS 部署的体检推到「起栈之前」这一步时，逐行读 `scripts/preflight.sh`（当时的 99 行）读出三个洞，每一个都能让它说「通过」而部署起不来：
  - **镜像清单是手写的，少一个**：`:60-61` 只对 backend 与 web 各做一次 `docker manifest inspect`，proxy 缺席。而 `docker-compose.yml:106-131` 的 `quantlab-docker-proxy` 是默认服务、不在任何 `profiles:` 里、镜像名是 `ghcr.io/bobvane/my-quant-lab-docker-proxy:${MQL_VERSION:-latest}`（`:110`）—— 这正是 ADR-076 在 nightly 里修掉的同一个集合不完整，只是在自检脚本里又出现一次：`docker compose up -d` 会去拉它，而自检从没问过它。
  - **它读的是检出的仓库，不是部署**：`:23-25` 把 `docker/Dockerfile.backend`、`docker/Dockerfile.web`、`docker/entrypoint.sh`、`docker/web.nginx.conf`、`backend/requirements.txt` 当硬要求，而 `README.md:41` 明说 NAS 部署**只需要两个文件**（`docker-compose.yml` + `.env`）。加上脚本开头 `REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$REPO_ROOT"`，它永远检查脚本所在的检出目录：两文件部署里根本没有这个脚本，带脚本的检出里又被要求交出源码。README 允许的两种部署形状，脚本对其中一种必然判 FAIL。
  - **没有人跑它**：`git grep preflight` 当时只命中 `docs/17_DECISIONS.md` 的 ADR 正文（`:1744`、`:1884`），没有任何工作流、脚本或文档调用它。一次没有入口的检查等于没有检查 —— 与 ADR-072（每一步都必须能失败）、ADR-073（结论必须变成退出码）是同一条线，这一版补的是入口。
  - 顺带一处让「源码是否必需」无法回答的结构问题：`docker-compose.yml:107-109` 是主文件里唯一的 `build:` 段（proxy，`context: .` + `dockerfile: docker/Dockerfile.proxy`），而 `docker-compose.build.yml:3-4` 的头注声称「生产 compose 只引用 GHCR 预构建镜像，不含 build 段」—— 这个不变量已经破了。
- 决策：
  1. `docker-compose.yml` 只留发布产物：proxy 的 `build:` 段移进 `docker-compose.build.yml`（补 `quantlab-docker-proxy`，`docker/Dockerfile.proxy`）。主文件零 `build:` 段，覆盖文件覆盖全部 5 个项目服务；这条不变量由守卫锁住。
  2. `scripts/preflight.sh` 重写为读**部署文件**：`--compose PATH`（默认 `docker-compose.yml`）、`--env-file PATH`（默认 `.env`）、`-h/--help`，未知参数 exit 2；不再 `cd`。
  3. 镜像集合**派生**自 compose（`compose_images()`：取 `image:` 行、按 compose 的规则展开 `${VAR}` 与 `${VAR:-default}`、只留含 `my-quant-lab` 的项目镜像 → backend/proxy/web 三个），脚本里不再出现任何镜像名。每个镜像先 `docker image inspect`（本地已有即通过，CI 路径）再退到 `docker manifest inspect`（NAS 路径），两者都失败才 FAIL，并点名镜像与 GHCR 包页。
  4. 源码文件只在 compose 里存在 `build:` 段时才是硬要求（Dockerfile 同样从 `dockerfile:` 行派生）；两文件部署给一条 WARN 说明「镜像来自注册表，不需要源码」，不再判死。
  5. 取值优先级与 `docker compose` 一致：`env_value()` 先 `printenv`（shell）再 grep env-file，去掉 CR 与首尾引号；密钥判定的三张表 `SECRET_PLACEHOLDERS` / `PUBLISHED_SECRETS` / `SECRET_MIN_LENGTH = 32` 与 `backend/app/core/config.py` 逐字相同（bash 数组 ↔ 模块常量，守卫断言两边相等）。
  6. CI 必须跑它，且必须在**起栈那个 step 内**、`up -d` 之前：`ci.yml` 的 `Boot the stack` 先 `export POSTGRES_PASSWORD="$(openssl rand -hex 16)"` 与 `export SECRET_KEY="$(openssl rand -hex 32)"`，再 `bash scripts/preflight.sh --env-file .env.example || { echo "=== pre-flight refused the deployment ==="; exit 1; }`，最后才是 `docker compose … up -d`。不能放成独立 step —— 那样上一步 export 的值不在环境里，`.env.example` 的示例密钥会让自检拒绝一次本来合法的 CI 起栈。
  7. 守卫 `backend/tests/test_deploy_preflight.py`（14 条）：主文件零 `build:` 段；覆盖文件为每个项目镜像给出 Dockerfile 且文件存在；proxy 在部署镜像集合里；preflight 不出现 `ghcr.io/bobvane/my-quant-lab` 字面量（清单必须派生）；两张密钥表与 `config.py` 相等；源码文件只在有 `build:` 段时被要求；`printenv` 出现在 env-file 的 grep 之前；`docker image inspect` 出现在 `manifest inspect` 之前；失败与用法两条出口都存在；CI 在起栈 step 内且排在 `up -d` 之前；两条真跑 bash 的行为守卫（派生出的集合与 compose 一致且不重复行、shell 的值在真运行里胜过 env-file）。
- 理由：
  - 「自检通过」必须等价于「这份部署能起来」，所以它要读的是**即将被执行的那两个文件**，而不是作者检出的仓库。否则 README 允许的两种形状里总有一种被自己的工具判错，而工具的名字（pre-flight）让它的结论看起来比实际更权威。
  - 清单必须派生而不是维护：手写清单漏掉 proxy 不是笔误，是结构（ADR-076 同一个洞）。派生之后「少问一个镜像」在结构上不可能发生，守卫只需要证明「这份清单是派生的」。
  - 本地已有镜像也算通过，是因为 NAS 上最常见的是「已经 pull 过、只是注册表暂时连不上」；此时判死比放过更糟（它挡住一次完全能起来的部署）。但注册表与本地都没有时必须失败。
  - `build:` 段的归属决定「源码是否必需」能否被回答，所以先修文件结构再修脚本：主文件是**部署形状**（只引用镜像），覆盖文件是**构建形状**（只补 build 段），各自只有一个职责。
- 影响与兼容：
  - 旧的调用方式（无参数、在仓库根目录跑）行为不变：默认 `docker-compose.yml` + `.env` 相对**当前目录**解析（在仓库根目录跑时路径与以前相同）。
  - `docker compose -f docker-compose.yml up -d` 仍会拉 `ghcr.io/...docker-proxy:${MQL_VERSION:-latest}`；只有叠加 `docker-compose.build.yml` 的本地构建路径会构建它（README 的本地构建用法不变）。
  - CI 多了一道门：自检失败时 `docker compose smoke test` 作业在起栈前就退出，`up -d` 不再有机会把一个起不来的部署送进验收（这是本版的目的）。
  - NAS 部署者若手上有完整检出，可以在起栈前自查：`bash scripts/preflight.sh --compose /vol1/1000/Docker/My-Quant-Lab/docker-compose.yml --env-file /vol1/1000/Docker/My-Quant-Lab/.env`。
- 测试：
  - `backend/tests/test_deploy_preflight.py` 14 条；连同 `backend/tests/test_release_version_scheme.py` 一起跑 = **22 passed / 1 failed**，唯一失败是 `version.txt` 当时还是 `v1.5.15`（ADR-079 的守卫，等 `set v1.6.0` 转绿）；`ruff check app tests` 与 `ruff format --check app tests` 干净。
  - **先红后绿**：用 `git worktree` 在 HEAD（v1.5.15）检出干净副本，把两个新测试文件放进去跑 = **14 failed / 9 passed**。逐条点名：`docker-compose.yml` 仍在 `:107` 有 `build:` 段、`quantlab-docker-proxy` 不在覆盖文件的 build 段里、preflight 硬编码镜像名、`v1.5.15 breaks the carry rule`，以及 `version.sh set v1.5.16` 在旧脚本上 **rc 0 并打印 `version set to v1.5.16`**（规则被破掉的那个入口）。
  - 行为证据（本机没有 Docker，用一个只实现 `info` / `image inspect` / `manifest inspect` / `compose version` / `compose config` 的替身放在 PATH 前面，直接跑真脚本；替身与部署目录都在 `%TEMP%` 下，不进仓库）：
    - 两文件部署（目录里只有 `docker-compose.yml` 与 `.env`，没有源码、不是 git 仓库）→ **exit 0**、`Pre-flight passed`，三个项目镜像逐个报 `image reachable in the registry`；
    - **同一个目录跑旧脚本** → **exit 1**，五条 `docker/Dockerfile.backend is missing — your copy is incomplete or out of date`（`Dockerfile.web`、`entrypoint.sh`、`web.nginx.conf`、`backend/requirements.txt`）—— 这就是 README 允许的部署形状被自己的自检判死的现场；
    - 注册表里缺 proxy（替身只让 `*docker-proxy*` 的 `manifest inspect` 失败）→ **exit 1** 并点名 `ghcr.io/bobvane/my-quant-lab-docker-proxy:1.6.0`（旧脚本从不问这个镜像）；
    - 注册表不可达但镜像已在本地 → **exit 0**（`image present locally`）；
    - `.env` 保留 `.env.example` 的 `change-me-openssl-rand-hex-32` → **exit 1** 并给出 `openssl rand -hex 32`（与 ADR-077 的应用层拒绝同一条规则）；
    - 同一个 `.env`、但 `SECRET_KEY` 由 shell 导出 → **exit 0**（shell 优先，与 `docker compose` 一致）。
  - 边界：守卫读文本与派生结果，它们能证明集合一致、入口被接上、脚本会失败；它们不能证明某个 NAS 上容器真的起来了 —— 那由部署者手上的 `Test-NasDeployment.ps1` 与 release/nightly 两条流水线的真机运行回答。

## ADR-079：版本号的第三段只占一位（进位规则与 `version.sh set` 的守卫）

- 背景：`scripts/version.sh` 的文件头（`:4-5`）本来就写着正确的进位规则（`v0.0.1 → … → v0.0.9 → v0.0.10 → … → v0.1.0`，`Each component counts 0-9 and carries over at 10`），`bump_version()`（`:41-50`）的进位也是对的（`patch < 9` 时 +1，否则 `minor + 1, patch = 0`，`minor > 9` 时 `major + 1`）。出错的只有入口：`cmd_set()`（`:100`）的校验正则 `^v?[0-9]+\.[0-9]+\.[0-9]+$` 允许第三段是**多位**，于是 v1.5.9 之后连续发布了 v1.5.10 … v1.5.15 六个版本（tag 已发布，是既成事实），而头注释里作为例子的 `v0.0.10` 正是那个入口的说明书。规则本身来自维护者：「以后版本v1.5.9之后就应该逢10进位，后续要记得改进。下一个版本进位到v1.6.0。」（m11491）
- 决策：
  1. `scripts/version.sh` 的文件头改成只说进位：第三段**永远是一位** —— `v1.5.8 → v1.5.9 → v1.6.0 → … → v1.6.9 → v1.7.0 → … → v1.9.9 → v2.0.0`，并删掉 `v0.0.10` 那个例子（它是入口的辩护词，不是规则的一部分）。
  2. `cmd_set()` 的校验正则收紧为 `^v?[0-9]+\.[0-9]+\.[0-9]$`，并显式拒绝 `minor > 9`；两处都在**写任何文件之前** exit 2，报错文案含 `carries over at 10` 与 `one digit`。
  3. 守卫 `backend/tests/test_release_version_scheme.py`（9 条）：`version.txt` 必须匹配 `^v\d+\.\d+\.\d$`；文件头必须含 `carries over at 10` 与 `v1.5.9 → v1.6.0`、且不含 `v0.0.10`；六处版本引用与 `version.txt` 一致（`.env.example` 的 `MQL_VERSION`、`backend/app/__init__.py`、`backend/pyproject.toml`、`frontend/package.json`、`frontend/package-lock.json` 两处）；`set v1.5.16` 必须被拒（rc 2、两条文案、`version.txt` 未被改动）；临时目录里 `set v1.6.0` 必须同步六处；`bump_version` 的进位表参数化（v1.5.8→v1.5.9、v1.5.9→v1.6.0、**v1.5.15→v1.6.0**、v1.6.8→v1.6.9、v1.6.9→v1.7.0、v1.9.9→v2.0.0）——做法是把脚本截断在 `case "${1:-show}" in` 之前再 `source`，因此不触发任何写入。
- 理由：规则早就在注释里、进位函数也早就正确，唯一出错的是「入口接不接受一个不合规的版本号」——所以修入口而不是写文档。守卫写成可执行的等式（六处同步 + 进位表 + 拒绝多位），是因为这类漂移发生在人手里而不是代码里：文档里的例子会被照抄，而失败的 `set` 不会被无视。历史 tag 不改写（v1.5.10–v1.5.15 保留原样）；编号从 v1.5.15 直接跳到 v1.6.0，回到收敛路径。
- 影响与兼容：`version.sh set` 现在拒绝多位第三段（包括本地临时用法），`bump` 不变（本来就正确）；已发布的 tag 与 Release 不受影响；下一个版本号是 v1.6.0。
- 测试：与 ADR-078 同一次运行（22 passed / 1 failed → `set v1.6.0` 之后全绿）；红证据里 `version.sh set v1.5.16` 在旧脚本上 rc 0 并打印 `version set to v1.5.16`，新脚本 rc 2 并说明进位规则。

## ADR-080：等一个永远不会答应的数据库，不能看起来像在等一个慢的（`docker/entrypoint.sh` 的等待循环必须保留并判定理由）

- 背景：`docker/entrypoint.sh` 的 `wait_for_db()`（v1.6.0 时 `:50-77`）用一个真查询探库，却把探针的输出丢掉了 —— `:53-67` 的探针调用写成 `if python -c '…' >/dev/null 2>&1; then`（`:67` 正是那个重定向）。于是三种**时钟无法改变**的答案与「服务还在启动」完全同形：密码错、库不存在、主机名解析不了，都表现为 30 × 2 s = 60 s 的 `waiting for database (N/30)`（`:72`），接着 `WARNING: database not ready after 60s; continuing so alembic reports the real error`（`:75`），然后是 `alembic upgrade head` 三连失败 → `ERROR: migrations failed; refusing to start`（`:94`）—— 一整分钟之后指向错误的组件。
- 放大这个洞的是编排层的健康判定：`docker-compose.yml:81` 的 db healthcheck 是 `pg_isready -U … -d …`，它**不做认证**（只问服务器收不收连接），所以密码错的部署里 db 依然 `service_healthy`、api/worker/scheduler 照常启动，那 60 秒的沉默发生在「编排说健康」之后。ADR-077（v1.5.15）修的是同族缺陷的前半段：`check_settings()`（`:27-35`）在 `wait_for_db` **之前**跑、四个角色 `check_settings || exit 1`，配置错误从 75 秒变成 3 秒；本版修的是落在数据库这一侧的同形缺陷 —— **答案被产生出来，又被丢掉**。
- 同一条线：ADR-072（每一步都必须能失败）、ADR-073（结论必须变成退出码）、ADR-076/ADR-078（集合与入口不能靠人手维护）、ADR-077（失败信息是正在部署的人唯一会读的文档）。
- 决策：
  1. 探针本体成为函数 `database_probe()`，理由不再被丢弃：`if reason=$(database_probe 2>&1); then log "database is ready"; return 0; fi`。脚本里不再出现 `>/dev/null`（守卫断言）。
  2. 新增 `database_error_is_permanent()`：`case` 匹配七条时钟无法改变的答案（`password authentication failed`、`no password supplied`、`does not exist`、`NoSuchModuleError`、`could not translate host name`、`Name or service not known`、`nodename nor servname provided`）→ 立即放弃，打印三行（`ERROR: the database answered with a problem that waiting cannot fix; giving up now instead of retrying ${attempts} times`、`reason: $reason`、`check POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB and DB_HOST where this deployment reads them`）后 `return 1`；四个角色的调用点从 `wait_for_db` 改成 `wait_for_db || exit 1`（与 `check_settings` 同形）。
  3. 会变好的答案照旧等，但**说出理由**：理由压成一行（`tr '\n' ' '` + `sed 's/  */ /g'`）后只在与上次不同时打印 `database not ready yet: $reason`（`last_reason` 去重），随后仍是 `waiting for database ($i/$attempts)`；预算变成可设的决定：`DB_WAIT_ATTEMPTS`（默认 30）与 `DB_WAIT_INTERVAL`（默认 2，注释保留 `defaults 30 x 2 s = 60 s`），并一路接到部署里（`docker-compose.yml` 的 `x-backend-env` 透传、`.env.example` 用注释记录默认值）。耗尽后仍保留 `WARNING … continuing so alembic reports the real error` 并 `return 0`：入口点不猜 alembic 的死因。
  4. 守卫 `backend/tests/test_database_wait.py`（23 条）：9 条读脚本的结构等式（不得有 `>/dev/null`、探针理由被接住、瞬时理由只打印一次、永久答案表与调用位置、两个默认值、`continue` 路径仍在、四个角色都 `wait_for_db || exit 1`、部署确实透传预算）+ 14 条行为证据（用替身跑真脚本：密码错与库不存在必须**第一次**回答就 rc 1、probes=1、alembic_calls=0、零行 `waiting for database`；连接被拒必须走满预算再让 alembic 报错；第二次就绪必须报 `database is ready`；14 个答案样本逐条判定永久/瞬时 —— 这一条是防「误判成永久 → 崩溃循环」的反向守卫）。
- 理由：失败信息是正在部署的人唯一会读的文档（ADR-077 的理由在这里同样成立）——「等 30 次然后说 alembic 失败」把一分钟和一次误导一起交给读者，而答案早在第一次探针的 stderr 里。「会变好的」与「不会变好的」必须分开：混在一起时预算不是保险而是浪费，且失败落在错误的组件上。`pg_isready` 不认证，所以「编排说健康」不能替代「我们真的连过」。默认预算仍是 30 × 2 s = 60 s，与 compose 的 healthcheck 同量级，慢启动的部署行为不变；把预算变成变量，是为了让它能被测试与运维调整 —— 行为守卫能在一秒内证明瞬时路径，靠的就是 `DB_WAIT_ATTEMPTS=2 DB_WAIT_INTERVAL=0`。
- 影响与兼容：密码错 / 库不存在 / 主机名解析不了的部署现在**第一次回答就退出**并打印 PostgreSQL 原话（以前是 60 秒 + 指向 alembic 的误导）；健康启动与慢启动路径行为不变（仍让 alembic 报真错、仍以退出码 1 收场）；新增两个环境变量 `DB_WAIT_ATTEMPTS`/`DB_WAIT_INTERVAL`（默认值不变，compose 已透传、`.env.example` 已记录）；`migrate` 与三个服务角色的等库判定现在是同一份实现。
- 测试：
  - **先红后绿**：把 v1.6.0 的 `docker/entrypoint.sh` 换回原位，用同一份替身、`PROBE_MODE=wrong-password`、外层 `timeout 20` 跑真脚本 → 20 秒内输出 `waiting for database (1/30)` … `(10/30)`，**全文没有 `password authentication failed`**，probes=10、alembic_calls=0（答案产生了、被丢掉了、预算照烧）。
  - 行为证据（本机没有 Docker；替身以 shell 函数注入子进程，`APP_ROLE=migrate` 直接跑真脚本，替身与日志都在 `%TEMP%` 下）：新脚本 `wrong-password` → **rc 1、probes 1、alembic_calls 0、零行 `waiting for database`**，日志含 `reason: not ready: (psycopg.OperationalError) connection failed: FATAL: password authentication failed for user "quantlab"`；`missing-database` → 同样 rc 1 / probes 1 / alembic_calls 0 并说出 `database "quantlab" does not exist`；`refused` + `DB_WAIT_ATTEMPTS=2 DB_WAIT_INTERVAL=0` → rc 0 / probes 2 / alembic_calls 1，理由只打印一次、`waiting for database (1/2)` 与 `(2/2)`、随后 `WARNING: database not ready after 0s (2 attempts); continuing…`；`ready-on-second` → rc 0，先 `database not ready yet: connection refused` 再 `database is ready`。
  - 守卫：`backend/tests/test_database_wait.py` 23 条全绿（`Invoke-Tests.ps1 -Keyword database_wait` = `23 passed, 700 deselected in 10.97s`）；连同 ADR-077 的 `test_production_secret.py` 与 ADR-078 的 `test_deploy_preflight.py` 一起跑 = `48 passed, 675 deselected`；`ruff check app tests` 与 `ruff format --check app tests` 干净。
  - harness 的三个坑（前两个已写进守卫注释）：① 替身函数必须 `return`、不能 `exit` —— `exit` 会结束 driver shell，`if python …` 永远进不了 else，现象是「探针只跑一次、没有 waiting 行」；② 交给 shell **重定向**的路径必须是 `/c/...` 形式（argv 会被 Git Bash 转换、重定向不会），否则被 `set -e` 直接判死；③ `export -f` 只带走函数文本，函数读到的变量必须自己 `export`，否则替身答出一个空理由、等待循环白等满预算 —— 守卫一开始正是这样「绿得可疑」的，靠 150 秒的耗时才暴露。
  - 边界：这些守卫读脚本文本并驱动真脚本的行为，它们能证明「理由被接住、永久答案不再等待、瞬时答案仍让 alembic 报错」；它们不能证明某个 NAS 上 PostgreSQL 真的是密码错的 —— 那由部署者手上的 `scripts/Test-NasDeployment.ps1` 与真机运行回答。

## ADR-081：删掉一条行情不能把可复现的回测变成不可复现的数字（行情系列的删除 = 硬删或归档）

- 背景：v1.6.1 部署上线后，维护者在「行情与策略 → 行情同步」里删 `DEMO-AAPL` 删不掉。线上取证（NAS `192.168.2.2:8081`）：`DELETE /api/v1/market-data/series/1` → **HTTP 500**，body 是 `{"error":{"code":"internal_error","message":"internal server error","details":{"path":"/api/v1/market-data/series/1"}}}`，失败后 series 仍是 6 条（没有造成任何破坏，也没有任何解释）。前端把这个 `message` 原样显示，所以用户看到的就是「删除不掉」。
- 两个根因。① `backend/app/api/routers/market_data.py:268-277` 的 `delete_series()` 只做 `db.get` + `db.delete(series)` + `db.commit()`，**从不问这条 series 还有没有人用**；② `backend/app/domain/models.py:310` 的 `backtest_runs.dataset_version_id` 是 `ForeignKey("market_data.id")` —— **没有 `ondelete`**（对比 `:163` 的 `market_data_bars.series_id` 与 `:282` 的 `feature_snapshots.series_id`，两者都写了 `ondelete="CASCADE"`），所以 PostgreSQL 正确地拒绝删除，SQLAlchemy 抛 `IntegrityError`，端点把它变成通用 500。线上当时 9 个回测里有 **7 个** 的 `dataset_version_id=1`（就是 `DEMO-AAPL`），所以这条 series 恰好是最不可能删掉的那一条。
- 为什么这不该用「级联删掉那些回测」来解决：`backtest_runs.dataset_version_id` 指向的正是那个结果所依据的数据集，**这个指针就是可复现性的证据**（五条红线之一：回测必须可复现、`result_hash` 必须能重算）。删掉数据等于把一个可复现的结果变成谁也复现不了的数字；静默删掉 run 则是拿审计换方便。所以正确的结局不是「删掉」，而是**「退役但保留证据」**。
- 这个结局在仓库里已经有一半：`MarketDataSeries.is_archived`（`backend/app/domain/models.py:132`，默认 `False`）被 `backend/app/simulation/signal_engine.py:304` 与 `:349` 读取（信号生成已经排除归档的 series），但**全仓库没有任何地方写它**，列表与详情端点也不过问它。半成品概念 + 一个抛 500 的删除按钮 = 本次的缺陷形状。
- 这个 bug 能一路走到生产，还有第二个原因：**测试库从不打开外键**（全仓库 grep `PRAGMA|foreign_keys` 只命中 `backend/app/domain/models.py:335` 的 `foreign_keys=[dataset_version_id]`，那是 SQLAlchemy relationship 的关键字，与外键约束无关）。测试跑在内存 SQLite 上（`backend/tests/conftest.py:46-52`），而 SQLite 默认忽略外键 —— 于是旧代码在测试里「删得掉」：它把 run 变成孤儿，而不是报错。同一个洞还决定了第三件事：生产里的 500 不带任何可追查的线索（见 ADR-082）。
- 决策：
  1. 删除的结局由「有没有回测用它」决定，两种结局都**明说**：没有回测引用 → 真删（显式删 `market_data_bars` 与 `feature_snapshots` 再删 series，不依赖引擎的级联，这样在 SQLite 上同样正确），返回 `{"deleted", "symbol", "archived": false, "blocking_runs": 0, "message"}`；有回测引用 → 置 `is_archived=True`，**数据与 run 指针都保留**，返回 `{"deleted": null, "symbol", "archived": true, "blocking_runs": N, "message"}`，message 说明「被 N 次回测使用，已归档并在行情同步中隐藏；数据保留，回测结果仍可复现」。
  2. `?purge=true` 表示「我确实要彻底删」：只要还有回测引用就 **409**，detail 点名数量并给出路（「请先删除相关回测记录，或改为归档（不带 purge 参数）」），措辞与 `backend/app/api/routers/strategies.py:87-119` 的 `delete_strategy()` 同源；没有引用时照旧硬删。**任何情况下都不静默删掉回测。**
  3. 新增 `POST /api/v1/market-data/series/{id}/restore`：`is_archived=False`（未知 series 404），归档是可逆的。
  4. `GET /api/v1/market-data/series` 默认**隐藏**归档的 series，新参数 `?include_archived=true` 才列出；列表行、详情、以及 `GET /api/v1/assets/{id}/series` 都新增 `is_archived` 字段 —— 归档状态必须能从 API 读出来，不能只存在于数据库里。
  5. 归档、硬删、恢复都写审计（`record_audit`）：`market_data_series_archived`（action `archive`）/ `market_data_series_deleted`（action `delete`）/ `market_data_series_restored`（action `restore`），`entity_type` 统一 `market_data_series`。
  6. 测试库打开外键：`backend/tests/conftest.py` 的 `db_session` 在 `create_engine` 之后用 `@event.listens_for(engine, "connect")` 执行 `PRAGMA foreign_keys=ON`。测试库必须像生产那样失败，否则「仍被引用」与「已变孤儿」在测试里没有区别 —— 这正是本 bug 的藏身处。
  7. 守卫 `backend/tests/test_series_deletion.py`（11 条）：无引用必须真删（bars 与 feature snapshots 一并清零）；有引用必须归档且 `blocking_runs` 正确、bars 保留、run 仍指向一条存在的 series；重复归档幂等；归档的 series 默认不出现在列表里、`?include_archived=true` 时出现且带 `is_archived`；详情带 `is_archived`；`?purge=true` 必须 409 且数据不动；无引用的 purge 仍可硬删；未知 series 404；恢复后回到列表；三种审计事件都在；最后一条断言测试库真的打开了外键（`PRAGMA foreign_keys` 为 1，且绕过端点直接删被引用的 series 会 `pytest.raises(IntegrityError)`）。
  8. 前端（`frontend/src/views/StrategiesView.vue` 的「行情同步」卡片 + `frontend/src/api.ts`）：默认只看在用数据，加「显示已归档（有回测使用，数据为可复现而保留）」开关（切换即重新拉取，因为归档行只在请求时才在响应里）；行内 `已归档` 徽标与 `恢复` 按钮；提示区分「已删除」与「已归档……数据保留」两种结局。
- 理由：可复现性证据不能由便利性决定，所以「删掉一条被回测用过的行情」这个动作必须换一个结局，而不是换一个错误码。三个候选里，级联删 run 毁掉审计与可复现性（红线），裸 409 让用户永远清不掉这条数据（就是现在的死胡同），归档同时保住结果、证据与用户的意图 —— 而且 `is_archived` 这个概念在信号生成里已经被尊重，语义不是新造的。`?purge=true` 之所以存在，是因为「我不想要它了」和「我不在乎那些回测了」是两句话，脚本与人都应该能把第二句说出来，而系统仍然拒绝在没说出口时替他们决定。审计是一种「谁在什么时候让什么退役」的记录：归档不是删除，但它改变系统行为（信号生成会跳过它），所以它值得留痕。测试库打开外键不是为了多几条断言，而是为了让「引用完整性」这个生产事实第一次进入测试 —— 否则同类缺陷还会从这里出去。
- 影响与兼容：`DELETE /series/{id}` 仍然是 200（两种结局都可能），调用方必须读 `archived` 字段而不是假定已删；新增可选参数 `?purge` 与 `?include_archived`、新增端点 `POST /series/{id}/restore`；列表/详情/资产系列多一个 `is_archived` 字段（旧客户端忽略即可）；硬删路径现在显式删除子表（对 PostgreSQL 行为不变，对 SQLite 才开始正确）；被回测引用的 series 在 run 存在期间**再也不可能被删除**；归档的 series 从行情同步列表消失（信号生成本来就跳过它）；审计表会多出三类事件。
- 测试：
  - **先红后绿**：实现前跑 `scripts\Invoke-Tests.ps1 -Keyword "series_deletion" -SkipInstall` = **9 failed, 2 passed, 723 deselected in 23.48s**（红证据存 `%TEMP%\mql-162\red-series-delete.txt`），其中 `test_a_series_a_backtest_used_is_archived_not_orphaned` 触发的服务端日志就是线上 500 的本地复现：`sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError) FOREIGN KEY constraint failed` + `[SQL: DELETE FROM market_data WHERE market_data.id = ?]`。之所以本地终于看得见，正是因为同一版把 SQLite 外键打开了。
  - 实现后同一条命令 `11 passed, 723 deselected in 7.82s`；连同 ADR-082 的 `backend/tests/test_error_incident.py` 一起跑 = `16 passed, 723 deselected in 9.51s`；`ruff check app tests` 与 `ruff format --check app tests` 干净。
  - 前端 `scripts\Invoke-FrontendChecks.ps1 -SkipInstall` = `== FRONTEND OK: typecheck + build passed ==`（`vue-tsc --noEmit` + `vite build`，609 modules）。
  - 边界：守卫证明的是「取舍与结局」的契约（谁被保留、谁被隐藏、谁被拒绝、谁被记进审计）；它不能证明某个 NAS 上的 PostgreSQL 现在能删掉 `DEMO-AAPL` —— 那要在用户升级到带本版镜像的部署后用同一条 `DELETE` 复验（预期 200 + `archived: true` + 原数据仍在）。

## ADR-082：一个 500 必须留下能被追查的痕迹（每个未处理异常给一个短关联号）

- 背景：ADR-081 那个缺陷到达维护者面前时，全部信息就是 `500 internal server error` 加 `details: {"path": "/api/v1/market-data/series/1"}`。`backend/app/api/main.py:189-206` 的 `unhandled_exception_handler` 做了两件对的事 —— `logger.exception("unhandled error on %s", request.url.path)` 把栈留在容器日志里，生产下只把 `path` 交给调用方（不泄露原因）—— 但它们之间**没有共同的钥匙**：日志里有栈、没有请求身份，响应里有路径、没有身份。用户说「删除不掉」，维护者只能重新探测一遍才能把这句话和某一行日志对上。v1.6.1 时这段代码里 `incident` 出现 **0 次**，返回的原文就是 `"message": "internal server error",`。
- 决策：
  1. 新增 `new_incident_id()`（`backend/app/api/main.py`）：`secrets.token_hex(4)`，8 个十六进制字符 —— 短到能在电话里念完，又足够在一个人的日志窗口里不重复。
  2. 处理器为**每个**未处理异常生成一个关联号，并同时把它放进三处：日志行 `unhandled error on <path> (incident <id>)`、响应 `error.message`（`internal server error (incident <id>)`）、响应 `error.details.incident` 与响应头 `X-Incident-Id`。生产下仍然只给 `path` 与关联号，非生产下照旧额外给 `details.exception`。
  3. 去掉 `# pragma: no cover`：这条路径现在有守卫，不再假装无法测试。
  4. 守卫 `backend/tests/test_error_incident.py`（5 条）：真造一个抛异常的路由，断言 500 的 message、details、响应头与 `caplog` 里的日志行指向**同一个**关联号；两次错误的关联号**必须不同**（复用等于把别人的栈指给你）；非生产下带回原因；把 `settings.is_production` 打成 `True` 后原因消失而关联号仍在；`new_incident_id()` 50 次不重复且都能被 `[0-9a-f]{8}` 匹配。
- 理由：可观测性的最小单位不是「有没有日志」，而是「用户看到的那一句话能不能定位到那一行日志」。所以关联号必须进 `message` —— 前端显示的是 `body.error.message`（`frontend/src/api.ts:20-33`），只放进结构化的 `details` 等于只给读 JSON 的人用。每请求唯一是同一件事的另一半：如果所有人共享一个常量，这个号就是装饰。8 位十六进制是刻意的取舍 —— 关联号的作用域是一个人的排障窗口，不是全局账本。
- 影响与兼容：500 的 `message` 文案变了（仓库里没有断言旧文案的地方，前端原样显示，因此用户会看到可引用的号）；新增响应头 `X-Incident-Id`（代理与浏览器 Network 面板都会记录它）；生产下仍然不泄露异常类型与消息（ADR-077 的同一原则：公开发布的部署不该多说话），非生产行为不变。
- 测试：`backend/tests/test_error_incident.py` 5 条全绿（与 ADR-081 的守卫合跑 = `16 passed, 723 deselected in 9.51s`）；先红证据是 `git show HEAD:backend/app/api/main.py` 里 `incident` 出现 0 次、`"message": "internal server error",` 一字不改（本 ADR 是新增能力，不是修一个回归，所以红的一半由「改动前 handler 里根本没有这个概念」这个事实给出，而不是靠失败测试）。边界：守卫证明响应与日志共享同一个号；它不能证明某个部署的日志采集把这些行留住了 —— 那取决于运行环境。


## ADR-083：计数必须数全，而不是数记得住的那个（删除防护要数全部引用者）

- 背景：ADR-081 把「删除不能让可复现的证据变成不可复现的数字」立成规则，但那一条只落在**行情**上。同一形状的缺陷在策略与 AI provider 上还在。`backend/app/api/routers/strategies.py:96-106`（改动前）的 `delete_strategy()` 唯一的防护是查 `backtest_runs`（经 `strategy_versions` join），而 `signals.strategy_version_id`（`backend/app/domain/models.py:434`，`nullable=False`、**无 `ondelete`**）与 `paper_accounts.strategy_id`（`models.py:502`，可空但**无 `ondelete`**）都不在防护里。策略→版本是 ORM 级 `cascade="all, delete-orphan"`（`models.py:204-206`），所以 `db.delete(strategy)` 会真的发出 `DELETE FROM strategy_versions …`，PostgreSQL 的正确拒绝变成 commit 上的 `IntegrityError` → 通用 500。这不是理论路径：信号由 `backend/app/simulation/signal_engine.py:230-246` 在生产路径写入，模拟盘账户由 `backend/app/api/routers/paper.py:47-53` 创建。AI provider 同族：`backend/app/data/ai_provider_service.py:285`（改动前）只查 `ai_tasks`，而 `ai_usage.provider_id` 与 `ai_usage.model_id`（`models.py:688-689`，均无 `ondelete`）不问 —— 又因为 `AIProvider.models` 有级联（`models.py:618-620`），「只有该 provider 名下某个模型有用量」同样挡不住这次删除。
- 决策：
  1. 把原则写成一条：删除防护要**数全部引用者**，而不是写防护时第一个想到的那一类。引用者清单是数据模型的事实，不是记忆。
  2. `backend/app/api/routers/strategies.py` 新增 `_REFERENCE_LABELS`（`backtests`→回测记录、`signals`→信号记录、`paper_accounts`→模拟盘账户，元组顺序即消息里的报告顺序）、`_blocking_references(db, strategy_id) -> dict[str, int]`（版本子查询 + 三次 `db.scalar(select(func.count(...)))`，只返回非零项）与 `_blocked_delete_message(blocking)`（按类给条数，`、` 连接）；`delete_strategy()` 用它们取代 backtest-only 检查，仍然返回 **409**：`此策略被 2 条信号记录、1 条模拟盘账户 关联，不能直接删除。请先删除相关记录。`
  3. `backend/app/data/ai_provider_service.py` 的 `delete_provider()` 在既有 AI task 检查之后，再用一次 `or_` 查询统计 `ai_usage`：`AIUsage.provider_id == provider_id` **或** `AIUsage.model_id.in_(select(AIModel.id).where(AIModel.provider_id == provider_id))`；命中则 `ProviderConfigError("this provider has AI usage history; deactivate it instead of deleting")`（与既有 task 分支同族：停用，而不是删除）。
  4. 守卫 `backend/tests/test_delete_referrers.py`（6 条）：`test_a_signal_blocks_deleting_its_strategy`、`test_a_paper_account_blocks_deleting_its_strategy`、`test_the_refusal_counts_every_referrer`（两类引用同时存在时消息把**每一类的条数**都数出来）、`test_a_strategy_nobody_points_at_still_deletes`（**没有任何引用的策略仍然删得掉**，随后 GET 404）、`test_ai_usage_blocks_deleting_its_provider`、`test_ai_usage_of_a_model_alone_blocks_the_provider`（只有该 provider 名下某个模型的用量也拒绝）。
- 理由：这个缺陷的形状是「防护只写了记得住的那一个引用者」。它在当时的测试里不会露头，因为测试库也不打开外键（ADR-081 才打开），只在生产里以 500 出现。两条路可以避免下一次：把引用完整性交给数据库并让错误可见（ADR-081），或在应用层把引用者数全。这里选后者，因为用户该看到的是「谁在用它、先删什么」，而不是一个被拒绝的删除。消息按类给条数而不是给一个总数，因为下一步动作取决于**哪一类**在挡路：删回测、删信号，还是关掉模拟盘账户。「仍然删得掉」那条守卫是刻意的 —— 一个从不放行的防护和从不拦截的防护一样坏，而且更隐蔽。
- 影响与兼容：`DELETE /strategies/{id}` 仍然只在无引用时成功，但 409 的 detail 从单一「此策略有回测记录关联」变成按类计数的句子；有信号或有模拟盘账户的策略**再也不可能被删除**（此前是 500，现在是有解释的 409）；`delete_provider()` 的失败形态多了「有 AI 用量历史」一种。仓库里没有测试或前端断言旧的 409 文本（grep `此策略有回测记录关联` 在 `backend/tests` 与 `frontend/src` 零命中），前端 `frontend/src/api.ts` 原样显示 `error.detail`，所以用户看到的是新的可读句子。退役手段是停用/归档，不是删除 —— 与 ADR-081 的取值一致。
- 测试：先红后绿。`git worktree` 检出 v1.6.2（`17d7f191e`）并拷入本 ADR 的守卫 = **7 failed, 2 passed in 15.61s**（7 条包含本 ADR 的 5 条与 ADR-084 的 2 条），失败形态不是断言而是线上那个 500 家族：`sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError) FOREIGN KEY constraint failed`，策略路径的语句是 `[SQL: DELETE FROM strategy_versions WHERE strategy_versions.id = ?]`（抛在 `strategies.py` 的 `db.commit()`），provider 路径是 `[SQL: DELETE FROM ai_providers WHERE ai_providers.id = ?]`；同一次运行里 ADR-082 的日志行写着 `unhandled error on /api/v1/strategies/1 (incident bbfcc3b4)`（两个 ADR 在这里接上：一个让 500 可追查，一个让 500 不再发生）。实现后本 ADR 的 6 条全绿；批次读数见 ADR-085 的测试段。边界：守卫证明的是「拒绝的契约」（谁挡路、数出来的条数、无引用仍删得掉），它不能证明某个 NAS 上的 PostgreSQL 现在会给出 409 —— 那要等下一次大版本部署后用同一条 `DELETE` 复验。

## ADR-084：没人读的设置就是没人兑现的承诺（删掉零消费者的旋钮）

- 背景：`backend/app/core/config.py` 的 39 个 Settings 字段里，有 4 个在 config.py 之外**一次都没被读过**：`ai_provider_base_url`、`ai_provider_api_key`、`ai_default_model` 与 `scan_cron`。它们不是「留待将来」的空位，而是**已经有承诺的空位**：`.env.example` 教运维在这里填 provider 地址与密钥，`docker-compose.yml` 把它们透传进容器，`scan_cron` 的 `description` 写着「Celery beat crontab used by the signal scanner」。而 `backend/app/workers/celery_app.py:31` 的 `beat_schedule` 把扫描周期硬编码成 `crontab(minute="*/15")` —— 照着文档改 `SCAN_CRON` 的人会得到**零变化**，且没有任何提示。这与 ADR-077（示例密钥被当成合格配置）同族：文档承诺了系统不会兑现的行为。provider 的真正配置入口一直是数据库（Web 的「设置 → AI」，密钥加密存储，见 `backend/app/data/ai_provider_service.py`），环境变量这条路从来没有消费者。
- 决策：
  1. 删掉这 4 个字段（`config.py`，原 `:127-129` 与 `:135-138`）、`.env.example` 里对应的 3 行（`AI_PROVIDER_BASE_URL`/`AI_PROVIDER_API_KEY`/`AI_DEFAULT_MODEL`）与 `docker-compose.yml:38-40` 的透传；`.env.example` 的注释改为指明真实入口（Web 设置页，存数据库）。
  2. 保留 `ai_daily_budget_usd`（`backend/app/api/routers/settings.py:57` 通过 `GET /settings` 暴露、`frontend/src/api.ts:570` 读取，有真实消费者）以及 `default_currency`/`default_timezone`。
  3. 新增守卫 `backend/tests/test_no_dead_settings.py`（3 条）：`test_every_setting_is_read_somewhere`（`Settings` 的每个字段都必须在 `backend/app`、`scripts`、`docker`、`.github`、`frontend/src` 与 `docker-compose*.yml` 的合并文本里出现，形如 `.字段名`）、`test_every_documented_env_key_is_consumed`（`.env.example` 的每个键都必须被消费）、`test_the_knobs_that_did_nothing_are_gone`（被删掉的四个名字不许回来）。消费者扫描**刻意不含 `backend/tests`**：只有测试读的设置，对运维与用户而言仍然是没人读的。
- 理由：两个方向都诚实 —— 要么把旋钮接上，要么把旋钮撤掉。`SCAN_CRON` 可以接上，但 `beat_schedule` 是模块导入时构造的，要变成运行时可配就得重做调度层；三个 AI 环境变量接上则会与「密钥只进数据库、加密存储」的既有决定冲突（ADR-039），多出第二个事实来源与第二条泄密路径。所以撤掉是成本最低且不制造矛盾的做法。`SettingsConfigDict(..., extra="ignore", ...)`（`config.py:59-64`）保证老 `.env` 里残留这些键不会让启动失败，因此这是一次**无痛移除**。守卫的作用是让「加设置」这个动作必须同时回答「谁读它」—— 一份只有文档的旋钮，会让下一个人在排障时浪费一小时去改一个没有读者的变量。
- 影响与兼容：`AI_PROVIDER_BASE_URL`/`AI_PROVIDER_API_KEY`/`AI_DEFAULT_MODEL`/`SCAN_CRON` 不再是配置项（残留值被静默忽略，不报错）；provider 与模型的配置入口只有 Web 设置页；扫描周期仍固定 `*/15`（行为不变，只是文档不再暗示它可配）。守卫会让未来任何「只加字段不接读者」的改动在 CI 里失败。
- 测试：本 ADR 的 3 条在 v1.6.2 上是红的（红证据读数见 ADR-083，其中一条报 `ai_provider_base_url is back; it had no reader (ADR-084)`，另一条把四个没人读的名字一起列出来），实现后全绿（读数见 ADR-085 的测试段）。

## ADR-085：活文档不能教一条代码会拒绝的规则（README 的版本号示例）

- 背景：ADR-079 把「第三段永远一位、逢 10 进位」固化进 `scripts/version.sh`（`set` 现在拒绝 `v1.5.16`），并从脚本头注释里删掉了违规示例 `v0.0.10 → v0.1.0`。但同一个示例还活在**用户真正读的那份文档**里：`README.md:292` 当时写着「版本号从 `v0.0.1` 起，每段 0–9，到 10 进位（`v0.0.10 → v0.1.0`）」。于是仓库同时教两条互斥的规则，而只有一条能过 `version.sh`。守卫当时只读 `scripts/version.sh`（`backend/tests/test_release_version_scheme.py:75-81`），README 无人看守。
- 决策：
  1. `README.md:292` 改成新规则的例句 `v1.6.8 → v1.6.9 → v1.7.0`，并写明「第三段**永远**是一位」「`scripts/version.sh set` 会直接拒绝不合规的版本号」。
  2. `docs/17_DECISIONS.md:101-106` 的 ADR-017 保留原文（历史就是历史），但在 `**决策**` 之后加一行修订指针，指向 ADR-079/ADR-085。
  3. 守卫扩展到活文档：`test_the_live_documents_do_not_teach_an_uncarried_version` 断言 README 不含 `v0.0.10` 且含 `v1.6.9 → v1.7.0`；**刻意不检查 `docs/15`** —— 它的历史行里合法地引用了这个违规例子来叙述 ADR-079 修了什么。
- 理由：文档里错误示例的危害比脚本里的更大 —— 脚本的错误只在被读到时误导，README 的错误会在每个人打标签时被照着做。守卫要盯「活文档」（对**当前**行为的承诺），不要盯「历史文档」（ADR 与路线图记录当时发生了什么），否则守卫会逼着人篡改历史。区分标准是时态：现在时的规则必须与代码一致；过去时的记录不必。这条规则在本版由守卫自己证明了一次：第一版把 `docs/15` 也断言了，于是守卫在合法的历史引文上变红（`the roadmap still shows the uncarried example`），所以断言的边界被收回到 README。
- 影响与兼容：README 的版本号段改写（无行为影响）；新增守卫会在未来任何人把违规示例写回 README 时失败；`docs/15` 的历史行不受影响。
- 测试：本版批次 `pwsh -NoProfile -File scripts\Invoke-Tests.ps1 -Keyword "delete_referrers or no_dead_settings or release_version_scheme or ai_providers or delete_strategy" -SkipInstall` = **1 failed, 38 passed, 711 deselected**（唯一红点就是上面那条过严的守卫），修后 `test_release_version_scheme.py` = **13 passed**（含新增的 README 断言与 ADR-086 的部署节奏断言）、`ruff check app tests` 与 `ruff format --check app tests` 干净（144 files already formatted）。「先红」由守卫在 v1.6.2 的 README 上必然失败给出：`README.md:292` 当时确实写着 `v0.0.10`，报 `the README still shows the uncarried example`。 全量 `pwsh -NoProfile -File scripts\Invoke-Tests.ps1 -SkipInstall` = **746 passed, 4 skipped in 125.27s**（v1.6.2 基线 735 passed / 4 skipped，本版新增 11 条：`test_delete_referrers.py` 6 + `test_no_dead_settings.py` 3 + `test_release_version_scheme.py` 2；4 条 skip 仍是 `backend/tests/test_postgres_triggers.py` 的 `TEST_POSTGRES_URL not set`），前端 `pwsh -NoProfile -File scripts\Invoke-FrontendChecks.ps1 -SkipInstall` = `== FRONTEND OK: typecheck + build passed ==`（`vue-tsc --noEmit` + `vite build`，609 modules）。

## ADR-086：只有大版本才部署到 NAS（第三段为 0）

- 背景：用户在本轮开发中定下部署节奏：只有大版本（`X.Y.0`，即版本号第三段为 `0` 的版本，如 `v1.7.0`/`v1.8.0`/`v1.9.0`）才部署到 NAS；补丁版本（`v1.6.3`、`v1.6.4`…）不部署，同时要求加快开发节奏。此前每个版本都做一次 NAS 巡检（`scripts/Test-NasDeployment.ps1`、`scripts/verify-stack.sh`、手工 `docker compose up -d`），把大量时间花在重复的部署仪式上，而补丁之间的差异远小于一次部署的成本。
- 决策：
  1. 部署节奏写成规则：**只有 `X.Y.0` 部署到 NAS**；补丁版本只 commit + tag + push，由 CI 验证并积累。
  2. 规则写进两处活文档：`README.md` 的「版本与发布」段新增 `### 部署节奏（ADR-086）`（含升级命令 `docker compose pull && docker compose up -d` 与用 `scripts/Test-NasDeployment.ps1` 复验），`docs/15_ROADMAP_ACCEPTANCE.md` 的 v1.6.3 行同样记录。
  3. 守卫：`test_the_readme_states_which_versions_get_deployed` 断言 README 同时提到 `ADR-086` 与 `X.Y.0`，防止这条说明被后来的编辑顺手删掉。
- 理由：部署是有成本的验证动作，它的价值与「两个版本之间发生了什么」成正比。补丁版本只含少量修复，让它们先在仓库里积累（每个版本仍有测试、文档与 CI 三作业），到大版本再一次性上 NAS，既减少人工步骤，也让每次部署对应一个语义上有意义的版本。速度不是靠少做验证换来的：测试、文档、CI 全绿与打 tag 仍是每个版本的硬要求，被砍掉的只是**重复的部署仪式**。
- 影响与兼容：NAS 上的版本会滞后于 `main`（这是决策本身，不是遗漏）；用户升级时用 README 里的两条命令；`docs/15` 仍逐版记录（不部署不等于不记录）；补丁版本的验证边界从此是「CI 绿 + 守卫绿」，NAS 真机证据只在大版本提供。交付上仍要如实说明某个补丁版本未上 NAS，不能让读者以为它已在生产运行。
- 测试：`test_the_readme_states_which_versions_get_deployed` 通过（读数同 ADR-085 的 13 passed）。本条是流程决策，没有行为代码改动，因此不提供红证据 —— 它的守卫只保证规则在文档里不被静默删除。

## ADR-087：单位是数据的一部分，不是显示的装饰（前端按 API 给的单位渲染）

- 背景：前端↔API 审计的第一条。`frontend/src/views/SignalsView.vue:263` 用 `formatNumber(outcomeSummary.groups.ALL.avg_pnl_pct, 3)` 之后再手工拼一个 `%`，`:280-281`（分组表）与 `:311-313`（逐条明细）是同一个形状；而同一个面板里 `:262` 的胜率用的是 `formatPercent`（`frontend/src/format.ts:9`，它自己乘 100）。后端给的是**比率**：`backend/app/simulation/outcome_evaluator.py:96` `pnl_pct = (close_end - signal_close) / signal_close * direction_sign`、`:101-105` 的 mae/mfe 同为单位、`:115-117` `round(..., 8)` 落库，`backend/app/api/routers/signals.py:170` 原样透传，`:183-192` 的汇总（`win_rate`/`avg_pnl_pct`/`total_pnl_pct`）也全是比率。于是真值 `0.0512` 被显示成 `0.051%`（正确是 5.12%）—— 差 100 倍，而且 `toneOf` 的涨跌配色也一起吃到了错误的量级。同一个形状在回测页有第二份：`frontend/src/views/BacktestView.vue:970-977`（OOS 表）、`:1176`（敏感性目标）、`:1667`（compare 表）、`:1773`（指标明细）用 `formatNumber(..., 4)`，而 `:1600-1620`（headline）、`:1706-1708`（runs）、`:1032-1038`（walk-forward）用 `formatPercent`，数据源 `backend/app/research/walk_forward.py:174-189` 返回的是比率 —— 同一页上 `0.0512` 与 `5.12%` 指同一个量级。
- 决策：
  1. 规则：**API 给什么单位，前端就按什么单位渲染；乘 100 只允许发生在 `formatPercent` 里**。
  2. `frontend/src/views/SignalsView.vue` 的 6 处改为 `formatPercent(..., 3)`：`:263`（平均/累计）、`:280-281`（分组表两列）、`:311-313`（逐条 `pnl_pct`/`mae_pct`/`mfe_pct`）；`toneOf` 与样式不动。
  3. `frontend/src/views/BacktestView.vue` 引入 `RATIO_METRICS = new Set(['total_return','cagr','max_drawdown','win_rate','annualized_volatility','exposure'])` 与 `formatMetric(key, value, digits = 4)`（比率 → `formatPercent(value, 2)`，其余 → `formatNumber(value, digits)`，null/undefined → `'—'`），五个渲染点（OOS 表两列、compare 表、指标明细、敏感性目标、敏感性摘要）全部走它。判据是**指标名**，不是 `%` 字面量。
  4. 守卫 `backend/tests/test_frontend_contracts.py` 三条：`test_outcome_ratios_are_rendered_as_percentages`（对 `pnl_pct`/`mae_pct`/`mfe_pct`/`avg_pnl_pct`/`total_pnl_pct` 断言不存在 `formatNumber(...) +`，并要求 `formatPercent` 至少出现 6 次、含胜率那一处）、`test_the_backtest_page_keys_its_units_on_the_metric_name`、`test_the_views_do_not_keep_a_second_copy_of_the_rule`（只查 SignalsView，见理由）。
- 理由：`0.0512` 与 `5.12%` 的差别不是显示风格，是数据被读错了一百倍，而错的量级还会喂给涨跌配色。守则必须绑在**指标名/来源**上，不能一刀切禁止 `+ '%'`：Ghostfolio 侧后端已经给 0–100（`backend/app/data/ghostfolio.py:241-246` 的 `_as_pct(...)`，测试 `backend/tests/test_ghostfolio.py:38/89/91` 断言 25.0 / 25.0 / 0.6），所以 Dashboard 与资源页的 `formatNumber(...) + '%'` 是**正确**的写法；同理交易明细的 `mae`/`mfe` 是价格（`backend/app/research/engine.py:628-632`，`BacktestView.vue:1821-1822` 用 `formatNumber` 正确）。第三条守卫最初也断言了 `"* 100" not in BACKTEST`，被回撤**图表**的坐标换算 `((p.equity - peak) / peak) * 100` 打红，随后把边界收回到「指标渲染」：图表把序列缩放到百分比是坐标轴单位，不是指标单位。
- 影响与兼容：信号 outcome 面板的 5 个数字从「小 100 倍」变成真值（`0.0512` → `5.12%`），胜率不变；回测页 OOS/compare/指标明细/敏感性的比率列从裸小数变成百分比，`sharpe`/`number_of_trades`/`average_holding_bars` 等非比率不变；API 与数据库均无变更，纯渲染修正（用户此前照抄过这些百分比的话需要按新显示重算）。
- 测试：`backend/tests/test_frontend_contracts.py` 的上述三条；红证据（v1.6.3 上跑同一份守卫）报 `pnl_pct is a fraction, so it needs formatPercent: ['formatNumber(g.avg_pnl_pct, 3) +', 'formatNumber(g.total_pnl_pct, 3) +', 'formatNumber(o.pnl_pct, 3) +']` 与 `assert 'const RATIO_METRICS = new Set([' in …`。

## ADR-088：一个模块失败不能让整页空白（每个请求自己回答）

- 背景：`frontend/src/views/DashboardView.vue:51-59` 的 `Promise.all([...])` 里，`api.aiStatus()`、`api.assets()`、`api.series()`、`api.settings()` 各自带了 `.catch(() => …)`，而 `api.systemInfo()` 与 `api.paperAccounts()` **没有**；`:60-64` 的六个赋值全部在 resolve 之后执行，`:76-78` 的 catch 只写 `error.value`。`Promise.all` 的语义是「全成功才继续」，所以任意一个 5xx 会让系统信息、模拟账户、资产、行情系列、AI 状态与设置一起空白 —— 而同一页 `:36-50` 的 `api.health()` 早就单独 catch 并写 `healthError`（ADR-069 的先例）。`frontend/src/views/SettingsView.vue:178-207` 是同一个形状：九个请求里 `api.audit()`、`api.settings()`、`api.aiProviders()`、`api.notificationConfig()` 没有 catch，赋值 `:193-203` 也在 resolve 之后，因此 `/audit/logs` 或 `/settings` 一失败，审计、系统设置、AI 服务商、通知配置四块一起空白。
- 决策：
  1. 每个请求自己回答：`const failures: string[] = []` 与 `const note = (label: string) => { failures.push(label); return null }`；`api.systemInfo().catch(() => note('系统信息'))`、`api.paperAccounts().catch(() => note('模拟账户') ?? [])`，SettingsView 侧四个同样处理（`api.audit()` 的 catch 返回 `{ total: 0, events: [] }` 以保住类型）。
  2. 赋值处对缺失容错：`settings?.environment ?? {}`、`settings?.settings ?? []`、`providers.value = ai?.providers ?? []`、`if (notification) applyNotification(notification)`（Dashboard 的模板本来就用 `info?.` 与 `accounts.length`）。
  3. 失败按模块名报告：`error.value = \`${failures.join('、')} 加载失败，页面其余内容仍然可用\``。
- 理由：页面上的模块之间没有依赖关系，请求也不该有 —— 用 `Promise.all` 的默认语义等于把「一个接口挂了」升级成「整页不可用」，而用户最需要看到的往往正是还活着的那部分（例如 AI 状态挂了不该让模拟账户的现金也消失）。点名失败的模块，比一句「加载失败」更接近用户接下来要做的事。
- 影响与兼容：失败时页面显示可用部分，并在错误区点名失败的模块；成功路径完全不变；无 API 变更。
- 测试：`backend/tests/test_frontend_contracts.py::test_every_dashboard_request_answers_for_itself` 与 `::test_every_settings_request_answers_for_itself`（把 `await Promise.all([...])` 块里每个 `api.` 开头的行都要求带 `.catch(`）；红证据报 `AssertionError: ['api.systemInfo(),', 'api.paperAccounts(),']` 与 `['api.audit(),', 'api.settings(),', 'api.aiProviders(),', 'api.notificationConfig(),']`。

## ADR-089：UI 承诺的能力必须送达后端（扫描要写库、重置要有按钮、区间要发出去、删除要先问）

- 背景：四件事，都是「后端已经能做、文档已经承诺，UI 没有送达」：
  1. **「立即扫描」永远扫不出信号**：`backend/app/api/routers/signals.py:279` 的 `scan(persist: bool = False)` 默认是干跑（`:280` 的 docstring 写着 `With persist=true signals are stored`），而 `frontend/src/api.ts` 的 `scanSignals` 是 `() => request<...>('/signals/scan', { method: 'POST' })`，从不带参数，所以返回的 `created` 恒为 0；`frontend/src/views/SignalsView.vue:46-59` 还丢弃整个返回值，只写一句「扫描完成，已重新加载信号列表」。用户点了按钮，系统什么都没做，界面却报了成功。
  2. **模拟盘重置没有入口**：`backend/app/api/routers/paper.py:416` 有 `POST /accounts/{account_id}/reset`，`docs/12_API_SPEC.md:280` 有记录，`frontend/src/views/PaperView.vue:215-217` 甚至写着重置警告文案、`:188` 显示 `reset_count`，但操作列 `:190-211` 只有持仓/绩效/关闭|重开，`frontend/src/api.ts` 里也没有对应方法。
  3. **回测日期区间发不出去**：`backend/app/api/schemas.py` 的 `BacktestCreate` 有 `start`/`end`，`backend/app/api/routers/backtests.py:74` 把它们交给 `load_bars(..., start=…, end=…, only_closed=True)`，但 `frontend/src/api.ts` 的 `runBacktest` body 里只有 `{strategy_version_id, symbol, timeframe, execution_overrides}`，配置区里也没有输入框 —— 而 `docs/13_UI_UX.md:52-60` 要求它。
  4. **破坏性操作没有二次确认**：全前端 `confirm(` 命中 0 次（`StrategiesView.vue:705`/`:677`、`BacktestView.vue:1712`、`SettingsView.vue:482`），而后端在无引用时是不可逆硬删（`backend/app/api/routers/market_data.py` 的 `_hard_delete` 显式删 bars 与 FeatureSnapshot、`backend/app/api/routers/backtests.py:383-404`）。
- 决策：
  1. `scanSignals: (persist = false) => request<...>(\`/signals/scan?persist=${persist}\`, { method: 'POST' })`；`SignalsView.vue` 改调 `api.scanSignals(true)`，并用返回值写提示：`扫描完成：评估 N 条，写入 M 条新信号` / `…没有新的可执行信号`。（`persist` 是标量参数，FastAPI 按 query 解析，放 body 不生效。）Dashboard 的 `runScan()` 保持干跑 —— 它渲染的是响应里的 `result.signals`。
  2. 新增 `api.resetPaperAccount(accountId, initialCash?)` 与 `PaperView.vue` 的「重置」按钮（`button.danger` 样式新增在 `frontend/src/style.css`，仓库原本没有 `.danger`）；确认文案用 `account.net_deposits`，因为 `PaperAccount` 接口没有 `initial_cash` 字段。
  3. 回测页新增起始/结束日期输入与「清除区间」，按 UTC 取边界（`T00:00:00Z` / `T23:59:59Z`，含首尾两天），并在发送前拒绝「起始晚于结束」。
  4. 六处破坏性操作先问再动手：`deleteStrategy`、`deleteSeries`、`removeRun`、`remove`（provider）、`resetAccount`，确认文案各自说明后果（不可恢复；行情被回测引用时会改为归档而不是删除；provider 有 AI 调用记录时后端会拒绝，请改为停用），且一律在调用 `api.` 之前 `if (!ok) return`。
- 理由：按钮存在的意义是它会改变系统状态。第 2、3、4 件是「后端已经能做的能力没有送达」；第 1 件更糟 —— 界面给了成功提示而系统什么都没做，假成功比缺功能更坏（用户会以为自动化在跑）。UTC 边界与 `only_closed=True` 一致，避免本地时区把一天切在错误的位置。确认文案刻意提前说明**可能被拒绝**与**可能只是归档**这两种结局，让用户在点之前就知道会发生什么。
- 影响与兼容：点「立即扫描」现在会真的写入信号（信号列表与审计随之增长；干跑仍可从 Dashboard 使用）；模拟盘重置会清空虚拟持仓与交易记录并递增 `reset_count`（不可逆，所以有确认）；回测可指定区间（留空 = 使用该序列全部已同步 K 线，行为不变）；删除按钮多一步确认。API 无变更（都是既有端点），无数据迁移。
- 测试：`backend/tests/test_frontend_contracts.py` 的 `::test_a_scan_the_ui_promises_can_actually_store_signals`、`::test_the_documented_paper_reset_has_a_control_and_a_method`、`::test_a_backtest_can_name_its_date_window`、`::test_every_destructive_button_asks_first`。红证据（`git worktree add --detach %TEMP%\mql-red-164 HEAD`，HEAD = v1.6.3 `054d3d430`，拷入同一份守卫）= **8 failed, 1 passed in 3.12s**：`deleteStrategy deletes without asking`（旧正文 `async function deleteStrategy(id: number) {`）、`assert 'scanSignals: (persist = false) =>' in …`、`assert 'resetPaperAccount: (accountId: number, initialCash?: number) =>' in …`、`assert ('start?: string' in …)`，唯一通过的是 `test_the_views_do_not_keep_a_second_copy_of_the_rule`（旧 SignalsView 本来也没有 `* 100`，这条守卫防的是**将来**有人再手写第二份换算）。全量：v1.6.3 基线 746 passed / 4 skipped → 本版 **755 passed, 4 skipped in 146.49s**（`== BACKEND OK ==`，4 条 skip 仍是 `backend/tests/test_postgres_triggers.py` 的 `TEST_POSTGRES_URL not set`）；`ruff check app tests` All checks passed、`ruff format --check app tests` 145 files already formatted；前端 `scripts\Invoke-FrontendChecks.ps1 -SkipInstall` = `== FRONTEND OK: typecheck + build passed ==`（`vue-tsc --noEmit` 无输出 + `vite build` 609 modules、`✓ built in 12.19s`）。守卫是文本级断言（先例：`backend/tests/test_nas_deployment_script.py`、`backend/tests/test_nightly_pipeline.py`），它证明的是「视图与 API 客户端里的这些承诺仍然在场」，不能证明浏览器里的渲染结果 —— 那需要一个组件级/无头浏览器测试，`scripts/Test-WebUi.ps1` 是现成的起点。
## ADR-090：一个不会失败的检查只是装饰（脚本与 CI 里六处「不可能红」）

- 背景：v1.6.3 期间脚本/CI 只读审计的 8 条里，有六条是同一个形状 —— 一段代码看着在判定，实际无论输入如何都报 OK。① `scripts/Test-NasDeployment.ps1:281-286`（改动前）的未知 id 检查：`Invoke-Api -Path '/ai/tasks/999999' | Out-Null` 之后 `throw '期望 404，却返回了 200'`，catch 里 `if ($_.Exception.Message -match '404')` 打印「符合预期」—— 而它自己抛出的那句话里就带着 `404`，于是**接口返回 200**（v0.9.9 那个回归）被报成通过；`backend/tests/test_nas_deployment_script.py:54-58` 只要求每一步有 `throw`/`Assert-`，所以放行。② `scripts/verify-stack.sh:22`（改动前）问的是 `/api/v1/healthz`，而 `backend/app/api/routers/health.py:147-150` 的 `liveness()` 故意「touches nothing」，只回 `{"status":"alive"}`；真正的依赖契约在同文件 `:168`（`status = "healthy" if database == "connected" and migrated else "degraded"`）与 `:175`（`"workers": _check_workers()`）—— 于是 release（`.github/workflows/release.yml:182-183` → `:218-221` 的 `SMOKE_TEST_FAILED`）与 nightly（`.github/workflows/nightly.yml:112-113` → `:144-147`）共用的这份判定，在 worker 崩溃重启时照样通过（`docker compose up -d` 对 crash-loop 容器返回 0；compose 给 worker（`docker-compose.yml:181-186`）与 scheduler（`:204-209`）配了 healthcheck，但没有任何一条流水线读它）。③ `.github/workflows/ci.yml:362` 的 Smoke 5/5 只有 `print('evaluated =', d['evaluated'])`，而兄弟步骤 `:280`（`assert d['inserted']>0, d`）与 `:346`（`assert d['status']=='completed', d`）都断言。④ `scripts/Start-LocalStack.ps1:101-116` 算出 `$webOk`、失败时打印「vite did not come up」与 stderr 尾巴，然后 `:118-120` 打印 pid 就结束 —— 退出码 0（API 那一半在 `:63-67` 有 `exit 1`），而它不在 `backend/tests/test_self_check_verdicts.py:18` 的 `Test-*.ps1` glob 内，所以没人管。⑤ `scripts/Invoke-FrontendChecks.ps1:101` 的清理条件写的是 `$isUnc` —— 这个变量全脚本从未赋值（真正的判定变量是 `:65` 的 `$needsMirror`），分支永不执行、`-KeepMirror` 是空开关。⑥ `scripts/resource_baseline.sh:29` 的 `CONTAINERS` 六个名字漏了默认服务 `quantlab-docker-proxy`（`docker-compose.yml:109-134`，挂 `/var/run/docker.sock:ro`），而采样 `:38-41` 用 `grep -E '^quantlab-'` 其实采到了它，只有聚合循环 `:45`/`:222`/`:231` 报六个 —— 报告少一个容器，读报告的人不会知道。
- 决策：
  1. `scripts/Test-NasDeployment.ps1` 新增 `Get-ApiStatus`（读**响应状态码**：成功返回 `200`，失败从 `$_.Exception.Response.StatusCode` 取值，取不到才 rethrow），未知 id 步骤改为 `$unknown = Get-ApiStatus -Path '/ai/tasks/999999'` + `if ($unknown -ne 404) { throw "unknown id 期望 404，实际 $unknown" }`，正文里不再有 `-match '404'`。
  2. `scripts/verify-stack.sh` 的默认 `--api` 改成 `http://127.0.0.1:8080/api/v1/health`，并从响应体解析 `status` 与 `workers`：只有 `status=healthy` 且 `workers` 形如 `<n> online`（`n≥1`）才算 `api_ok=1`；每轮与最终结论都打印 `api_ok=… (status=… workers=…)`，失败行带上原因（web 半照旧：`/healthz` 通、`/` 含 `id="app"`、缺失的哈希资源必须 404，ADR-068）。
  3. `.github/workflows/ci.yml` 的 Smoke 5/5 改成断言契约：`assert isinstance(d['evaluated'], int) and d['evaluated'] >= 0, d` 与 `assert d.get('disclaimer'), d`。
  4. `scripts/Start-LocalStack.ps1` 的 web 失败分支加 `exit 1` 并打印 `START_LOCAL_STACK_FAILED (web did not answer on :$WebPort)`，与 API 那一半同形（ADR-073：自检脚本必须能失败）。
  5. `scripts/Invoke-FrontendChecks.ps1:101` 的 `$isUnc` 改成 `$needsMirror`。
  6. `scripts/resource_baseline.sh` 的 `CONTAINERS` 补上 `quantlab-docker-proxy`。
  7. 守卫 `backend/tests/test_script_guard_integrity.py`（文本级，先例 `backend/tests/test_nas_deployment_script.py`、`test_release_pipeline.py`）把每一条各钉一次：未知 id 步骤必须有 `Get-ApiStatus` 与 `-ne 404`、且**去掉整行注释后**不得再出现 `-match '404'`；`verify-stack.sh` 必须问 `/api/v1/health`、不得回到 `/api/v1/healthz`、必须出现 `"workers"` 与 `healthy`；每个**独立**的 `python3 -c`（不是 `$( … )` 取值那种）都必须含 `assert`，且这样的探针不少于 3 个；`Start-LocalStack.ps1` 的「vite did not come up」之后必须有 `exit 1` 与 `START_LOCAL_STACK_FAILED`；`Invoke-FrontendChecks.ps1` 不得出现 `$isUnc`、清理条件必须用 `$needsMirror`；`resource_baseline.sh` 的清单必须覆盖 `docker-compose.yml` 里每个**没有 `profiles:`** 的 `quantlab-*` 服务（用 PyYAML 读 compose 自己算，而不是再抄一份名单）。
- 理由：这些检查存在的意义是「坏掉的时候变红」，而它们当时的效果是「让流水线看起来是绿的」。第 ① 条尤其贵：它的成功文案里带着 `404` 这个字面量，于是**判定读的是自己的措辞，而不是被检查的系统** —— 一条检查必须读它声称在检查的东西（状态码、响应字段、退出码），这也是「匹配错误消息」这个抽象本身是错的。第 ② 条的教训是共享判定要问**契约**而不是**存活**：两条流水线都以为自己在验证部署，实际只验证了进程还占着端口。第 ⑤ 条说明死掉的守卫比没有守卫更糟：`-KeepMirror` 看起来可调，实际没有任何效果。第 ⑥ 条是「同一份清单被枚举两次」的代价 —— 采样与报告用两份名单，报告就少一个容器而没人知道。
- 影响与兼容：`verify-stack.sh` 的退出语义不变（0/1/2），但**判定变严**：worker 不在线或数据库迁移未就绪的栈现在会红 —— 这正是它本来要回答的问题（历史调用若把 `--api` 指向 `/healthz`，需要改成 `/health`；两条流水线都用默认值，无需改动）。`Start-LocalStack.ps1` 现在会在 vite 起不来时以退出码 1 结束（此前是 0）。`Test-NasDeployment.ps1` 的未知 id 步骤在非 404 时会报出实际状态码。其余为纯修正，无 API 变更、无数据变更。
- 测试：`backend/tests/test_script_guard_integrity.py` 中对应的 6 条；红证据（`git worktree add --detach %TEMP%\mql-red-165 HEAD`，HEAD = v1.6.4 `5e664232c`，拷入同一份守卫）= **8 failed in 0.85s**，其中属于本条的六条为 `the step no longer reads the response status`、`the verdict is back on the liveness probe`（`'/api/v1/healthz' is contained here: 0.0.1:8080/api/v1/healthz"`）、`ci.yml:362 prints a result instead of asserting one`、`the vite failure path cannot fail the script`、`the dead variable is back`、`these default services are never reported: ['quantlab-docker-proxy']`。实现后 8 passed；`bash -n` 三个 shell 脚本通过，`[System.Management.Automation.Language.Parser]::ParseFile` 解析三个 `.ps1` 无错，三条 workflow 经 PyYAML 解析通过。

## ADR-091：发布事件必须自己校验版本，同一事实不能有两份答案

- 背景：另外两条发现不是「判定太松」，而是「唯一的事实被放在两个地方，或者放对了地方却没有人在那个时刻读它」。① 版本：`scripts/version.sh` 声明的版本方案由 `backend/tests/test_release_version_scheme.py` 守卫，而 `.github/workflows/ci.yml:3-7` 的触发条件是 `push: branches: [main]` —— **打 tag 不会跑 ci.yml**，于是真正发布的那个事件（`.github/workflows/release.yml:37-47` 解析 `workflow_dispatch` 输入或 `${GITHUB_REF#refs/tags/}`）从不读 `version.txt`、也从不校验 `vX.Y.Z` 形状：`git tag -a v1.7` 或者 tag 与 `version.txt` 不一致，都能一路发到 GHCR 与 GitHub Release（release notes 在 `:256`/`:259` 宣传 `MQL_VERSION` 与文档地址）。② 文档地址：`scripts/version.sh:157`（改动前）的部署说明打印 `- API docs: http://<nas-ip>:8080/docs`，而 `README.md:98`、`.github/workflows/release.yml:259` 与 `docker/web.nginx.conf:51-54` 都走 web 容器的 `:8081`（`docker-compose.yml:146` 只把 8080 绑到 `127.0.0.1`）—— 同一个事实两个答案，其中一个在 NAS 上根本连不通。
- 决策：
  1. `.github/workflows/release.yml` 在 `Resolve version` 之后、latest 判定之前新增步骤「The tag must agree with version.txt」：先用 `[[ "$tag" =~ ^[0-9]+\.[0-9]+\.[0-9]$ ]]` 校验方案形状，再把 `declared="$(tr -d '[:space:]' < version.txt)"` 与 `${version#v}` 逐字比较，不一致就 `exit 1` 并提示先跑 `bash scripts/version.sh set <version>`。
  2. `scripts/version.sh` 的部署说明把 API 文档地址改成 `:8081/docs`，与 README 一致。
  3. 守卫：`backend/tests/test_script_guard_integrity.py::test_the_release_reads_the_version_file_it_ships` 要求 `release.yml` 出现 `version.txt` 以及 `tr -d`/`declared`，且这一比较必须发生在 `Create GitHub Release` **之前**；`::test_the_release_notes_do_not_point_at_a_port_that_serves_nobody` 用正则从 `version.sh` 与 `README.md` 各取 `:(\d+)/docs` 并断言两者**相等** —— 它钉的是「一致」，不是某一个数字，所以将来换端口只需同改两处。
- 理由：把版本方案交给「推 main 时顺手跑一下」的守卫，等于在没有推 main 的时刻没有守卫；**唯一事实的来源（`version.txt`）必须在发布那一刻被读一次**，ADR-017/ADR-079 定的方案才算真的生效。第二条是「同一事实两处」的老账（ADR-070、ADR-085 是同一主题的不同侧面）：两边各写一次时，正确性取决于两次都记得改，而这次没有 —— 守卫改成「两处必须一致」比「断言等于 8081」更接近事实本身。
- 影响与兼容：打 tag 时若 `version.txt` 不一致，release 会在构建镜像**之前**失败（这是好事：错误版本的镜像不会被发布出去）。历史 tag 不受影响，无需重打。`scripts/version.sh notes` 输出的部署说明现在指向能真正打开的地址。无 API 变更、无数据变更。
- 测试：上述两条守卫；红证据里对应 `the release never reads the file it publishes` 与 `the release notes do not point at a port that serves nobody`（v1.6.4 上 8 failed 中的两条）；实现后 8 passed。发布事件自身的行为证据是下一次 `git tag -a v1.6.5` 推送后的 release run（它会执行该步骤）。

## ADR-092：写 bars 的人必须同时写它的哈希

- 背景：v1.6.3 期间 DB/ORM 只读审计的第一条是「有读无写」：`market_data.content_hash`（`backend/app/domain/models.py:131`）被序列详情端点 `backend/app/api/routers/market_data.py:133` 读取并原样返回，而全仓库没有任何一行代码写它 —— 唯一计算同型哈希的 `series_content_hash()`（`backend/app/data/market_data_repo.py:249-252`，sha256 覆盖 `frame.to_csv(float_format="%.10g")`）只有一个调用点，`backend/app/api/routers/backtests.py:87` 把结果存进 `backtest_runs.dataset_hash`，从不回写序列。于是这个唯一暴露该字段的接口从项目开始就恒返回 `null`（列表端点 `backend/app/api/routers/market_data.py:91-105` 甚至不返回该列），`docs/11_DATA_MODEL.md` 也从未描述过它。写入 bars 的路径有两条：`backend/app/api/routers/market_data.py:226-230`（手动/接口同步）与 `backend/app/workers/tasks.py:97-98`（夜间与计划同步），两条都走同一个 `upsert_bars()`（`backend/app/data/market_data_repo.py:196-242`）。
- 决策：
  1. 刷新放进两条路径共用的入口：`backend/app/data/market_data_repo.py` 新增 `refresh_series_content_hash(db: Session, series: MarketDataSeries) -> str | None`（`load_bars(db, series, only_closed=True)` → 空则 `None`，否则 `series_content_hash(frame)`），并在 `upsert_bars()` 收尾（`return inserted` 之前）无条件调用它；函数加入 `__all__`。
  2. 语义定死：哈希覆盖该序列**全部 closed bars**（时间升序、`float_format="%.10g"`），没有 closed bars 时是 `NULL`（不等于「空 frame 的哈希」）。
  3. 因此该值等于同名序列上「不带区间的回测」记录进 `backtest_runs.dataset_hash` 的值（`backend/app/api/routers/backtests.py:87` 用的是同一个函数，且 `:74` 的 `load_bars` 不传 `start`/`end`/`limit`），调用方可以据此确认「我看到的 bars 就是那份结果算的 bars」。
  4. 守卫 `backend/tests/test_series_content_hash.py`（6 条）把这条链路钉死：写入即得哈希、重同步不变、多一根 bar 就变、空序列为 `None`、端点返回它、一次真实回测的 `dataset_hash` 与之相等。
- 理由：把回写放在「两条调用方各自记得」的位置，第三个写入者出现时就会漏（`workers/tasks.py` 就是第二个写入者）；放在唯一的写入口里，忘记这件事在结构上不可能发生。哈希的用途也要求它描述**库里现在存着什么**，而不是「某次调用传进来了什么」——因此实现方式是 upsert 之后重新读一遍已落库的 closed bars，而不是对入参 frame 求哈希。
- 影响与兼容：列早已存在且可空，无需迁移；代价是每次 upsert 后多一次 `load_bars` + 一次 `to_csv`（同步路径为 400 根 bar 量级，可接受）。历史库里的既有行仍为 `NULL`，直到该序列下一次同步。
- 测试：`backend/tests/test_series_content_hash.py`（6 passed）。红证据：把该文件拷进 v1.6.5 的 worktree（`git worktree add --detach %TEMP%\mql-red-166 90a358e81`）运行 = 5 failed，`test_a_sync_stores_the_hash_of_the_bars_it_inserted` 的断言消息正是 `assert None is not None + where None = <MarketDataSeries>.content_hash`（第 6 条 `test_a_series_without_bars_has_no_hash_at_all` 在旧代码上「通过」——因为旧代码什么都不写）。

## ADR-093：目录必须来自代码，因为它就是代码

- 背景：审计第二条：`features` 表（`FeatureDefinition`，`backend/app/domain/models.py:261-272`）在全仓库**零写入**，却有两个读端点 —— `GET /features`（`backend/app/api/routers/features.py:37`）与 `GET /features/versions` 的 `definition_versions`（同文件 `:22-27`）。也就是说这两个端点承诺的能力一生都不存在：引擎实际产出三十个特征列（`build_features()`），而接口回答「没有特征」。同型的还有 `jobs` / `job_logs`（`backend/app/domain/models.py:705-738`）：既无写入也无读取，没有任何路由暴露它们。`docs/12_API_SPEC.md:44-46` 还宣称 `GET /features/{id}` 与 `POST /features` 存在。
- 决策：
  1. 目录搬到代码里：新增 `backend/app/features/catalogue.py` —— `FEATURE_CATALOGUE`（32 项：indicator 12 + price_action 20）与 `catalogue_payload()`（按 `name` 排序、**无 `id`**，字段 `name`/`feature_type`/`feature_version`/`description`/`inputs`/`params`/`is_deterministic`/`lookahead_safe`）；`GET /features` 返回它，且不再依赖数据库会话。
  2. `GET /features/versions` 返回真实存在的东西：`engine_feature_version`、`indicator_version`、`price_action_version` 与 `snapshot_versions`（来自 `feature_snapshots`，它**有**写入 —— `backend/app/simulation/signal_engine.py:278`），删掉 `definition_versions`。
  3. 删除 `FeatureDefinition` / `Job` / `JobLog` 三张表（迁移 `0009_drop_dead_schema`），`docs/12_API_SPEC.md` 把 `/features/{id}`、`POST /features`、`/features/{feature_id}/versions` 标记为随死表取消。
  4. 守卫 `backend/tests/test_feature_catalogue.py` 把目录与引擎的**真实产出**双向比对（`set(build_features(sample_bars).columns) - set(OHLCV_COLUMNS)` 与目录名字集合互相相等）：算出来却没登记、登记了却没算出来，都会红。
- 理由：一张永远为空的表不是「还没填」，而是「没有任何代码路径会填它」——它让一个端点看起来在提供能力（v1.6.4 ADR-089 的同型问题）。特征目录的事实来源就是计算特征的代码，所以它必须与代码放在一起、并被代码验证；只把名字抄进另一处注释或数据表，就会像这张表一样在某一天变成谎言。
- 影响与兼容：`GET /features` 的返回形状变化（不再有 `id`、不再为空），`GET /features/versions` 少一个字段、多两个版本字段；前端从未调用过这两个端点（`frontend/src/api.ts` 与 `frontend/src/views/*.vue` grep 命中 0），因此对外兼容性影响只体现在文档与 OpenAPI。表删除有迁移 `0009`，其 `downgrade()` 会真实重建三张表与两个被删列（PG 回归套件的 teardown 会跑到 `downgrade("base")`，半恢复的 schema 会污染下一次 `upgrade`）。
- 测试：`backend/tests/test_feature_catalogue.py`（6 passed）、`backend/tests/test_dead_schema.py`（4 passed）、改造后的 `backend/tests/test_feature_metrics_api.py`（目录非空且含 `ema20`/`rsi14`/`breakout`、`ai/models` 仍为空、`"definition_versions" not in body`）。红证据：同一 worktree 里 `test_feature_catalogue.py` 收集期即失败 —— `ModuleNotFoundError: No module named 'app.features.catalogue'`。

## ADR-094：不变式必须在建库时就存在，而不是只在某个迁移里

- 背景：审计第三条：策略版本与已完成回测的不可变触发器只存在于迁移 `backend/alembic/versions/0002_immutability.py`（且 `:29-30` 判断「非 PostgreSQL 直接 return」）与 `0003_fix_triggers_json.py`。而实际会建库的路径里，单元测试（`backend/tests/conftest.py:64` 的 `Base.metadata.create_all(engine)`）、四个探针脚本（`probe_watch_refusal.py:122`、`probe_version_ledger.py:72`、`probe_paper_contributions.py:115`、`probe_lifecycle_direction.py:144`）与开发者临时库都不跑迁移 —— 它们建出来的库**一个守卫都没有**，而 `backend/app/domain/models.py:14-17` 却声称该不变式由触发器保证。这正是 `operator does not exist: json = json` 那次事故能进生产的原因：本地唯一会建的库跑不了这个守卫，0002 里 `NEW.dsl_json = OLD.dsl_json` 的写法只有在 PG 上才暴露（0003 改成 `::text` 比较）。
- 决策：
  1. 守卫的唯一定义放进 `backend/app/domain/immutability.py`：`statements_for(dialect_name)` / `drop_statements_for(dialect_name)` / `install_immutability_triggers(connection)` / `drop_immutability_triggers(connection)`，触发器名与拒绝消息都是模块常量（`trg_strategy_versions_immutable`、`trg_backtest_results_immutable`、`"... are immutable: create a new version instead"`、`"completed backtest results are immutable"`）。
  2. 通过 `@event.listens_for(Base.metadata, "after_create")` 安装：任何用 `create_all()` 建出来的库都带上同一份守卫；`backend/app/domain/models.py` 导入该模块以注册事件。
  3. 迁移链调用同一个函数：新增 `backend/alembic/versions/0010_immutability.py`，`upgrade()` = `install_immutability_triggers(op.get_bind())`，`downgrade()` 刻意空实现并注明「不变式不能被回滚削弱」（0002/0003 作为历史记录保留）。
  4. 两种方言给出一致的判定：PostgreSQL 用 `NEW.dsl_json::text IS DISTINCT FROM OLD.dsl_json::text`（沿用 0003 的修正），SQLite 用 `BEFORE UPDATE ... FOR EACH ROW WHEN OLD.x IS NOT NEW.x ... RAISE(ABORT, '...')`。
  5. 守卫 `backend/tests/test_immutability_guard.py`（10 条）在**单元测试自己的 SQLite 库**上真跑这些触发器：直接改 `dsl_json`/`version`/`immutable_hash` 必须失败、`is_current` 翻转必须仍然成功（v0.9.9 那次 500 的回归）、已完成结果的 `summary_json` 不可改、`backtest_runs` 仍可归档；另有文本断言「`backend/app` 下含 `CREATE TRIGGER` 的文件恰好只有 `app/domain/immutability.py`」「`0010` 引用共享函数」「`models.py` 导入该模块」。
- 理由：只写在某条迁移里的守卫等价于「只有走过那条迁移的库才有」——而测试库、探针库、开发库都不过那条路，于是**没有任何测试证明它有效**（PG 套件默认 skip，本地永远跳过 4 条）。把定义绑在 metadata 上，「从模型建库」与「从迁移建库」就得到同一个结果，缺一即无法建出库。
- 影响与兼容：`create_all()` 会多执行两条 DDL；`test_immutability_guard.py` 让这套守卫在本地 SQLite 上也被真跑（此前只有 PG 上 4 条、默认 skip）。两种方言的异常类型不同（SQLite 是 `IntegrityError`、PG 是 `ProgrammingError`），测试按消息中的 `immutable` 匹配，两种都断言消息文本而不是类型。0002/0003 保留为历史记录，新库的最终状态由 0010 安装同一份定义。
- 测试：`backend/tests/test_immutability_guard.py`（10 passed）。红证据：同一 worktree 里 8 failed，其中 `test_the_test_database_really_carries_the_guards` 的断言消息为 `assert {'trg_backtest_results_immutable', 'trg_strategy_versions_immutable'} <= set()`（旧代码建出来的测试库 `sqlite_master` 里没有任何触发器），`test_the_migration_chain_uses_the_shared_definition` 报 `FileNotFoundError: ...\backend\alembic\versions\0010_immutability.py`；另两条在旧代码上通过（`is_current` 翻转、`backtest_runs` 归档），因为「没有守卫」不会拦住任何写入。

## ADR-095：没人读的列不是能力，是负债（以及第二份 schema 快照）

- 背景：审计第四条的三个字段声明后从未被读写：`backtest_runs.parameters_id`（`backend/app/domain/models.py:311`，`ForeignKey("strategy_parameters.id")` —— 真正的参数记录在同表 `parameters_json` `:314`，审计原文把它误标成 `strategy_versions` 的字段）、`ai_models.context_length`（`:632`）、`ai_models.supports_structured_output`（`:633`）：在 `backend/`、`frontend/`、`docs/` 里 grep 不到任何消费者。同一次审计还留下了仓库里唯一一份 `.sql`：`backend/pg.sql`（589 行 / 21,754 字节，`alembic upgrade --sql` 导出的静态 DDL 快照，`git log` 只有 `4f65fc755` 一次触碰），它零引用（`git grep -n -I -- 'pg\.sql'` 无命中），并且已经漂移：里面同时躺着 `content_hash`、`parameters_id`、`context_length`、`supports_structured_output` —— 也就是说读者若把它当真相，会看到一份并不存在的 schema。`docs/11_DATA_MODEL.md` 开头还复述了「模型包含 17 个核心表」这个数字。
- 决策：
  1. 删三个死列（迁移 `0009_drop_dead_schema` 用 `op.batch_alter_table(..., recreate="auto")` 分别在 `backtest_runs` 与 `ai_models` 上 `drop_column`）。
  2. 删 `backend/pg.sql`（`git rm`）：schema 的唯一事实来源是 `backend/app/domain/models.py` 与 `backend/alembic/versions/`，需要静态 DDL 时现场生成。
  3. `docs/11_DATA_MODEL.md` 不再复述表的数量，改为指向 `Base.metadata`（表的清单以代码为准）。
  4. 守卫 `backend/tests/test_dead_schema.py`（4 条）：三张死表不在 `Base.metadata.tables`；`models.py` 不再出现 `FeatureDefinition`/`JobLog`/`class Job(`/`parameters_id`/`context_length`/`supports_structured_output`；`backend/pg.sql` 不存在；`backend/` 下没有含 `CREATE TABLE` 的 `*.sql`（防止第二份 schema 快照回来）。
- 理由：一个永远是 `NULL` 或永远取默认值的列看起来像能力，读代码的人会以为有人在写它 —— `parameters_id` 尤其误导，因为真正的参数就躺在同一张表的 `parameters_json` 里。没人读的列不是「将来可能有用」，而是必须被维护、被迁移、被解释的负债。`backend/pg.sql` 是 ADR-090/091 那条主题在 DB 层的同型问题（同一事实不能有两份答案）：它没有消费者，所以只会在有人打开它的时候骗人。
- 影响与兼容：`parameters_id` 的外键随列一起消失；三列都没有写入路径，因此不涉及数据迁移。`pg.sql` 可从 git 历史（`4f65fc755`）取回。`Base.metadata` 的表数从 34 降到 31（`feature_snapshots` 保留）。表结构删除全部由 `0009` 承担，其 `downgrade()` 负责恢复。
- 测试：`backend/tests/test_dead_schema.py`（4 passed）。红证据：同一 worktree 里 4 条全红，消息分别为 `test_the_removed_tables_are_not_in_the_metadata`（`features`/`jobs`/`job_logs` 仍在 metadata）、`test_the_models_no_longer_declare_the_dead_names`、`test_the_static_schema_dump_is_gone`（`backend/pg.sql` 仍存在）、`test_no_second_copy_of_the_schema_lives_in_a_sql_file`。

## ADR-096：文档里的门必须是门（API 规范与真实路由双向绑定）

- 背景：v1.6.6 之后的一次只读审计把 `docs/12_API_SPEC.md` 的约 95 条端点声明与 `create_app().openapi()` 的真实路由表逐条对照，得到 11 条 high、1 条 medium、1 条 low 的漂移，另有 20 个真实存在的门文档一字未提。形态两种：① **整族凭空存在** —— `GET/POST /market-data-series`（真名 `/market-data/series`，且没有 POST）、`/market-data-snapshots/{series_id}`、`/market-data/{symbol}`（真名 `/market-data/latest/{symbol}`）、`/strategy-parameters*`（真名 `/strategy-versions/{version_id}/parameters`）、`/backtest-results*`（结果只有 `GET /backtests/{run_id}`）、四个 AI 家族（真身挂在 `/settings/ai/providers*`），以及 `GET /ai/models/provider/{id}`、`GET /ai/usage/provider/{id}`（provider 过滤是 `provider_id` 查询参数）、`GET /ai/prompts/{id}`、`POST /ai/prompts`、`POST /ai/tasks`；② **契约错位** —— `POST /market-data/sync` 的 `symbol/timeframe/start/end/provider` 是 `MarketDataSyncRequest` 请求体却被写成查询参数，`GET /feature-snapshots/{series_id}/bar/{timestamp}` 出现两次且两处互相矛盾，importer 家族有一处写成顶层路径。`backend/tests/test_api_contract.py`（8 passed）看不见这些：它绑的是**客户端**与 API，而客户端本身健康 —— 漂移全部住在 Markdown 里。
- 决策：
  1. `docs/12_API_SPEC.md` 的每条端点行带且只带一个状态标记：`[已实现]`（今天真的被服务）、`[计划]`（尚未实现）、`[取消]`（从未实现或已撤回），一行一个端点；撤回的门集中到文末的 `## 撤回的端点`，一条一行、不写长篇道歉。
  2. 新增守卫 `backend/tests/test_api_spec_truth.py`（6 条）把三组声明与真实路由表双向绑定：扫描下限（≥90 条被服务的路由、≥90 条 `[已实现]` 声明）、每条端点行必须有唯一标记、`[已实现]` 必须在服务（否则列出 `docs/12:行号` 与路径）、**被服务的路由必须被声明**（否则列出 `METHOD /path`）、`[计划]`/`[取消]` 不许落在被服务的路由上（标记过期就是把真门藏起来）、每个已挂载的 router 前缀必须在规范里出现。
  3. 审计点名的 20 个门按代码补写，改写过程中又发现并修正 9 处同类漂移（`POST /backtests/{id}/compare` 真名 `GET /backtests/compare`、`GET /strategies/{id}/versions/{version}`、`POST /strategies/{id}/backtest`、`POST /strategies/{id}/validate` 真名 `POST /strategies/validate`、`GET /research/runs/{id}`、`GET /github/snapshots*` 真名在 `/importer/github/*`、`POST /paper/orders`、`GET /paper/trades/{id}`、`GET /settings/audit`），并把 `?query` 从反引号里挪出去（`GET /backtests/compare?ids=1,2` 这种写法会让扫描器看不见那条声明）。
- 理由：规范是部署者与集成方**唯一会读**的东西，却没有任何机制保证它正确 —— 客户端契约有 `test_api_contract.py` 双向看门，规范只靠人维护，于是它随代码漂移了很久。这是 ADR-090/091 与 ADR-095 那条主题（同一事实不能有两份答案）在文档层的实例：真实路由表与 Markdown 是同一事实的两份，就必须有守卫让它们互相校验。三分类标记让「未实现」不能躲在正文里：`[计划]` 成真时、`[取消]` 的门真被实现时，都必须翻牌，否则守卫红。
- 影响与兼容：没有任何运行时代码变化。`GET /healthz` 由 `include_in_schema=False` 提供，因此守卫把它按名字加进「被服务」集合（`SERVED_OUTSIDE_THE_SCHEMA = {("GET", "/healthz")}`），规范也必须声明它（写成「只声明存活」）。文档从 566 行长到 575 行：134 条端点声明 = 112 条 `[已实现]` + 2 条 `[计划]` + 20 条 `[取消]`，与 112 个被服务的 `(method, path)` 一一对应（无多余、无遗漏）。
- 测试：`backend/tests/test_api_spec_truth.py` **6 passed**、`backend/tests/test_api_contract.py` **8 passed**。红证据（`git worktree` 在 v1.6.6 `f7fcd93d8` 上跑同一份守卫）4 条红：扫描下限 `only 8 implemented claims found; the spec scan broke`；**129 个端点行没有任何标记**（`docs/12_API_SPEC.md:7: GET /health`、`:31: GET /market-data-series` …）；**104 个被服务的门文档从未声明**（`DELETE /backtests/{}`、`GET /assets`、`GET /resources/current` …）；以及 `docs/12_API_SPEC.md:545: [取消] GET /features is served`（旧标记正压在一个真门上）。

## ADR-097：声明的边界必须有人交付（默认部署对局域网是开放的）

- 背景：`.env.example` 的端口段曾写「API 只绑定本机，由 Web 容器代理 /api，避免无认证暴露」，鉴权段写「留空 = 无鉴权（默认；API 只绑本机、由 Web 容器代理，局域网无法直连）」，`README.md:143` 同一句话写在「安全与暴露面」节，`docs/17` 的 ADR-028 第 5 条把「只绑本机 + 网关」当成主要边界，`backend/app/core/config.py:111-114` 的注释也复述「safe only because it binds to 127.0.0.1」。真实组合是：`docker-compose.yml:216` 用 `${WEB_BIND:-0.0.0.0}:${WEB_PORT:-8081}:80` 把 web 发布在所有网卡；`docker/web.nginx.conf:38-49` 把 `/api/` 反代到 `quantlab-api:8080`；`docker/web-entrypoint.sh:10-15` 在配置了 Token 时把 `Authorization: Bearer` 注入这个代理。于是**两种配置都不构成边界**：Token 为空时局域网任意主机都能读写 API（只有 `RATE_LIMIT_PER_MINUTE=60` 的写限流）；Token 非空时 Web 容器替所有客户端带上它，经 8081 的访问照样能用 —— Token 拦住的是绕过容器的客户端，而 8080 本来就只绑 `127.0.0.1`。
- 决策：
  1. 四处文案改成事实：`.env.example` 的端口段说明「这是默认部署唯一对局域网开放的端口，而它同时把 /api 代理出去」，鉴权段说明 Token 到底拦住谁、并把 `WEB_BIND=127.0.0.1` 写成真正关上的办法；`README.md` 的「安全与暴露面」按同义重写；`backend/app/core/config.py` 的 `api_auth_token` 注释改写并点名 `WEB_BIND`；`docs/17` 的 ADR-028 加 `> 修订（ADR-097）` 指针。
  2. 守卫 `backend/tests/test_boundary_claims.py`（6 条）：三处旧句必须消失（`局域网无法直连`、`局域网无法无认证访问`、`避免无认证暴露`）；端口段必须说出「局域网开放」与关闭办法（`127.0.0.1`）；鉴权段必须说明 Token 拦的是「绕过 Web 容器」的客户端、且经 8081 的路仍然可用；README 同一节不得复述旧句并须点名 `WEB_BIND`；`config.py` 注释不得再出现 `safe only because`；最后一条守着「让旧句为假的那个组合」（nginx 的 `proxy_pass http://quantlab-api:8080/api/;` 与 `${AUTH_LINE}`、compose 的 `${WEB_BIND:-0.0.0.0}`）—— 组合一变，前面那些句子就要重读。
- 理由：边界不是承诺，是配置的后果。默认部署对局域网开放是 NAS 的使用方式决定的（用户从自己的 PC 浏览器访问），不能靠改默认值来「修」；能修的是文档不再说反话，并把唯一真正能关上的开关指出来。这是 ADR-090/091 的主题在文档层的第二个实例。
- 影响与兼容：没有任何行为变化；默认部署仍然局域网可达，Token 的语义也没有变化（只是被写清楚了）。`API_BIND=127.0.0.1` 依旧关住 8080 直连。
- 测试：`backend/tests/test_boundary_claims.py` **6 passed**。红证据（`git worktree` 在 v1.6.6 `f7fcd93d8` 上跑同一份守卫）4 条红：`.env.example` 同时含 `局域网无法直连` 与 `避免无认证暴露`；端口段没有说「局域网开放」；鉴权段没有说「绕过 Web 容器」；`README.md` 仍写 `局域网无法直连`。

## ADR-098：密码不是 URL 的一部分（一个地方组装它）

- 背景：密码里只要出现 `%`、`@`、`:`、`/` 之一，四个角色的 alembic 都会在建立任何连接之前失败，而且**没有任何写法能同时满足两个读取方**。`backend/alembic/env.py` 曾把 `settings.database_url` 交给 alembic 的 `Config`（`config.set_main_option("sqlalchemy.url", …)`，旧 `:24`），而那个 `ConfigParser` 使用 BasicInterpolation —— 裸 `%` 直接抛 `ValueError: invalid interpolation syntax`；同时 `docker-compose.yml`（旧 `:28`）把 `${POSTGRES_PASSWORD}` 原样拼进 `DATABASE_URL`，同一个变量又原样交给 `quantlab-postgres`（旧 `:72`），所以按 ConfigParser 的要求写 `%%` 会让数据库容器真的收到 `pa%%ss`；至于 `@`、`:`、`/`，它们会让 SQLAlchemy 把密码的后半截解析成 host/port/database。`.env.example:7` 却写着「随便设一个强密码」，`scripts/preflight.sh:260` 只对长度 warn。
- 决策：
  1. 设置层持有**分量**而不是 URL：`postgres_user`/`postgres_password`/`postgres_db`/`postgres_host`/`postgres_port`（`backend/app/core/config.py:91-95`，均带默认值），由 `_assemble_the_database_url`（`:177-195`）用 `URL.create(…).render_as_string(hide_password=False)` 组装 `database_url`。显式设置的 `DATABASE_URL` 仍然优先（`:96-98`），`backend/tests/conftest.py:18` 的 SQLite 内存库与探针脚本不受影响。
  2. alembic 不再持有 URL：`backend/alembic/env.py` 改为 `create_engine(settings.database_url, poolclass=pool.NullPool)`，`backend/alembic.ini` 里没有 `sqlalchemy.url`（注释说明原因）；该文件也必须保持纯 ASCII —— alembic 按本机 locale 读取它，一个 em dash 就足以让测试套件在 Windows 上抛 `UnicodeDecodeError`。
  3. compose 只传分量：应用拿到的 `POSTGRES_USER/PASSWORD/DB`（`docker-compose.yml:32-34`）与数据库容器拿到的是同一批变量（`:79-81`），地址由 `POSTGRES_HOST`/`POSTGRES_PORT`（`:35-36`）给出。
- 理由：百分号编码只能发生一次，而且必须发生在「知道哪个分量是哪个」的那一层。同一个密码既要原样进 postgres 容器、又要进应用，任何在中间层转义的写法都会让两边看到不同的字符串 —— 所以转义点必须在最末端（SQLAlchemy 的 URL 构造器），而不是在配置文本里。URL 是派生事实，不是来源；这是 ADR-091「同一事实不能有两份答案」在配置层的实例。
- 影响与兼容：没有行为变化（默认值相同）。密码现在可以包含 `@ : / %`（包括 `%25` 这类看起来已编码的形态）并原样送达驱动；`DATABASE_URL` 显式设置仍然取胜，四个探针脚本与单测路径不变。
- 测试：`backend/tests/test_deploy_defaults.py` 的 `test_the_deployment_no_longer_hands_a_url_to_a_configparser`（alembic 不再 `set_main_option`、`alembic.ini` 无 `sqlalchemy.url` 且为纯 ASCII）、`test_the_configparser_would_have_refused_a_typical_password`（钉住机制本身，免得禁令看起来是凭空的）、`test_every_awkward_password_survives_the_round_trip`（参数化 `pa@ss`/`pa:ss`/`pa/ss`/`pa%ss`/`p%25ss@x/y:z`/`plain`，`make_url` 往返后 password、host、port、database、username 全部一致）、`test_the_database_parts_compose_passes_are_the_parts_the_app_expects`（`x-backend-env` 里不得再出现 `DATABASE_URL`）。红证据（`git worktree` 在 v1.6.7 `b9ad69921` 上跑同一份守卫）见 `docs/15` 的 v1.6.8 行。

## ADR-099：会失败的检查必须先能跑起来（探针的二进制、依赖的顺序、没有迁移的角色要硬失败）

- 背景：三条同源的编排缺陷。
  1. `docker-compose.yml:216` 给 `quantlab-scheduler` 的探针是 `pgrep -f 'celery.*beat' || exit 1`，而镜像（`docker/Dockerfile.backend:12-17`）基于 `python:3.12-slim`，从不安装 procps —— 容器里没有 `pgrep`，探针恒以 127 退出，scheduler 自上线起一直 `unhealthy`，而 `docker compose up -d` 对 unhealthy 的容器仍返回 0，所以本地和流水线都没人发现；同一缺依赖让 `.github/workflows/release.yml:218` 的诊断 `ps aux` 只打印 not found。
  2. worker 与 scheduler 曾以 `depends_on: quantlab-api: condition: service_started` 启动（旧 `docker-compose.yml:172-173`、`:197-198`），而迁移只在 api 角色执行（`docker/entrypoint.sh`）—— 全新建库时 beat 的 `collect-resources` 可以在建表之前触发并逐个失败。同文件的 `quantlab-web` 早就用 `service_healthy`，说明这是漏改而不是决策。
  3. `wait_for_db` 预算耗尽时 `return 0`（旧 `docker/entrypoint.sh:115-116`）：api 角色后面有 alembic 把真正的错误抛出来，worker 与 scheduler 后面没有这一层，于是「连不上库、每个任务都失败」的 worker 在探针（只 ping celery）与流水线里都算健康。
- 决策：
  1. 镜像安装 `procps`（`docker/Dockerfile.backend:17`），让已经写在 compose 里的 `pgrep` 探针真的能跑；注释点名这次事故。
  2. worker 与 scheduler 的 api 依赖改成 `condition: service_healthy`（`docker-compose.yml:185-186`、`:211-212`），队列与 beat 都在 schema 就绪之后才启动。
  3. `wait_for_db` 接受 `--required`（`docker/entrypoint.sh:96-133`）：对不跑迁移的角色，预算耗尽要打印 `ERROR: database not ready after …s (… attempts); this role runs no migrations, so nothing else would report it` 与最后一次失败原因并 `return 1`；worker/scheduler 的调用改成 `wait_for_db --required || exit 1`（`:167`、`:174`），api 与 migrate 保持原样（`:157`、`:180`），继续让 alembic 报真错。
- 理由：不会失败的检查有两种 —— 二进制不存在的检查，和后面没人接住失败的检查。前者是 ADR-090 的同型问题（断言不存在，看着却像通过）；后者把「暂时连不上」和「永远连不上」混为一谈，只有确实存在下一层会报错的角色才有资格放行。
- 影响与兼容：`docker compose up -d` 仍返回 0，但数据库不可达时 worker/scheduler 现在以非零码退出、探针显示 unhealthy（api 行为不变，仍由 alembic 报错）。`backend/tests/test_database_wait.py` 新增 2 条，其中一条用 bash stub driver 真跑 `entrypoint.sh`（`APP_ROLE=worker`、`DB_WAIT_ATTEMPTS=2`、`DB_WAIT_INTERVAL=0`），断言 `rc == 1`、恰好探测 2 次、从未调用 alembic、且输出中不出现 `starting celery worker`。
- 测试：`backend/tests/test_deploy_defaults.py` 的 `test_the_scheduler_probe_can_actually_run_where_it_is_declared`（探针里有 `pgrep` 就必须在镜像里装上 `procps`）、`test_migrations_finish_before_the_queues_start`（worker/scheduler 的 api 依赖必须是 `service_healthy`），以及 `backend/tests/test_database_wait.py` 的两条新用例。红证据见 `docs/15` 的 v1.6.8 行。

## ADR-100：同一事实只能有一份默认值（默认值与探针各只有一处声明）

- 背景：两类「同一件事写了两遍」。
  1. 部署默认值：`docker-compose.yml:37` 的 `MARKET_DATA_PROVIDER` 默认 `synthetic`、`backend/app/core/config.py:122` 默认 `yahoo_finance`；compose `:47` 默认启用 docker proxy，`config.py:150` 的 `DOCKER_PROXY_URL` 默认 `None`；compose `:60` 的 NO_PROXY 结尾有 `,.internal`，`.env.example` 没有；compose `:55` 的 CORS 默认含 `5173`，`.env.example` 只给 `8081`。
  2. 探针与用户：镜像自带 `HEALTHCHECK`（`docker/Dockerfile.backend:36-37` 30s/5、`docker/Dockerfile.web:24-25` 30s/3、`docker/Dockerfile.proxy`）而 compose 为每个服务另写一份（`:160-165` 45s/6、`:236` 20s/5 …），compose 的值总是取胜，镜像里那份从不执行却读起来像契约；compose 的 api 探针写死 `8080` 而同服务端口是变量；`docker/Dockerfile.proxy:15` 声明 `USER proxy`，又被 `docker-compose.yml:117` 的 `user: root` 覆盖。
- 决策：
  1. 三个镜像的 `HEALTHCHECK` 全部删除，探针只在 compose 里声明一次 —— compose 按角色写，用的就是该角色真实的命令（api 的 `curl …:8080/api/v1/healthz` 与同服务的 `PORT: "8080"` 一致）；镜像注释写明「单跑镜像请用 `--health-cmd`」。
  2. `docker/Dockerfile.proxy` 不再切换用户（`USER root`，`:19`），因为 Docker socket 归 root，各 NAS 的 GID 又各不相同 —— compose 不必再用 `user: root` 去覆盖镜像，两处说法一致。
  3. 应用默认值向部署对齐：`market_data_provider` 默认改为 `synthetic`（`backend/app/core/config.py:140`，仓内 `backend/tests/conftest.py:19` 本来就覆盖它），`.env.example` 补上 `,.internal` 与 `http://localhost:5173`，并补一行注释说明 `POSTGRES_HOST`/`POSTGRES_PORT` 的用途。
  4. 守卫 `backend/tests/test_deploy_defaults.py`：扫描 compose 中所有 `${VAR:-default}`，要求每一项都能在 `.env.example` 找到同一个值（例外只有 `MQL_VERSION`，因为 `.env.example` 钉住的是随包发布的版本号）；另比对 12 个部署旋钮的代码默认值与文档值（`CODE_EXCEPTIONS` 三项各自注明理由）；并要求每个服务都声明探针、且没有任何镜像再自带 `HEALTHCHECK`。
- 理由：默认值写两份就会产生两个答案，而答案是哪一个取决于谁最后说话；探针写在镜像里、compose 又覆盖它，等于让「哪个探针在跑」变成需要人工核对的事实。这是 ADR-091「同一事实不能有两份答案」在部署配置上的实例：可以派生的就不要重复声明，必须声明的就让它只有一处。
- 影响与兼容：`docker run` 不带 `--health-cmd` 的镜像不再自带探针（Dockerfile 注释里写明）；compose 的行为除探针归属外没有变化；`MARKET_DATA_PROVIDER` 的默认值从 `yahoo_finance` 改为 `synthetic`，与 `.env.example`/compose/测试夹具一致 —— 本地与 CI 都不会因此去请求外网。
- 测试：`backend/tests/test_deploy_defaults.py` 的 `test_every_default_compose_writes_is_the_one_the_example_documents`、`test_every_required_variable_is_documented`、`test_the_code_defaults_are_the_ones_the_example_documents`、`test_every_service_declares_the_probe_that_runs`、`test_the_proxy_runs_as_the_user_both_files_name`。红证据见 `docs/15` 的 v1.6.8 行。
## ADR-101：引用别人的代码要钉住那一次提交（可变标签不是版本）

- 背景：`.github/workflows/` 里 24 处 `uses:` 全部指向可变主标签（`ci.yml:37` 的 `actions/checkout@v7`、`nightly.yml` 的 8 处、`release.yml` 的 10 处，共 9 个不同的 action），其中 `docker/login-action`、`docker/setup-buildx-action`、`docker/build-push-action`、`docker/metadata-action`、`softprops/action-gh-release` 都是第三方代码；而 `release.yml` 与 `nightly.yml` 的作业持有 `packages: write`（能推 ghcr 镜像），release 作业还用一个能创建 Release 的 token。标签是别人可以随时重新指向的指针：某个 action 仓库被接管、或维护者改一次 tag，下一次推到 `main` 就跑上了不同的代码，而这次提交里没有任何一行发生变化。
- 决策：
  1. 24 处 `uses:` 全部钉到 40 位 commit SHA，行尾保留解析来的版本注释：`actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7`（完整映射写在 `docs/15` 的 v1.6.9 行）。SHA 用 `gh api repos/<slug>/git/ref/tags/<tag>` 取得（annotated tag 再解引用一层）。
  2. 新增守卫 `backend/tests/test_workflow_integrity.py`：`.github/workflows/*.yml` 里的每处 `uses:` 要么是本地 action（`./`）或容器（`docker://`），要么必须匹配 `@<40 位十六进制>`；被钉住的那一行还必须带 `#` 版本注释 —— SHA 自己不可读，人与 Dependabot 都需要知道它原来是哪个版本。
- 理由：SHA 不可移动，标签可以。安全边界不在「信任哪个 action」，而在「这次运行的是不是我提交里写到的那份代码」；把版本号留在注释里既保住可读性，也让升级变成一次可见的 diff（改 SHA 与注释），而不是一次静默的替换。
- 影响与兼容：升级 action 需要改 SHA（Dependabot 的 `github-actions` 生态会据此开 PR）；工作流的运行行为与之前完全一致，本机与 CI 都不需要改动。
- 测试：`backend/tests/test_workflow_integrity.py::test_every_published_action_is_pinned_to_a_commit`（带扫描下限 `seen >= 20`，防止正则失效后空跑）与 `::test_the_pinned_actions_say_which_version_they_were`。红证据见 `docs/15` 的 v1.6.9 行。

## ADR-102：守卫必须问对问题（一个判定看着在跑，其实问错了对象）

- 背景：四条同源缺陷，共同点都是「检查存在、显示绿、但它问的不是那个问题」。
  1. 死设置：`backend/tests/test_no_dead_settings.py` 当时只要求 `backend/app` 里出现任意 `.name`，于是 `Settings.host` 与 `Settings.port`（旧 `backend/app/core/config.py:82-83`）靠 `request.client.host`、`parsed.port`、`self.host = validate_smtp_host(host)` 这些**别的对象**通过了守卫；全仓库没有一处 `settings.host` / `settings.port` 读者 —— 容器绑 `0.0.0.0`，端口由入口点从壳层 `PORT` 传给 uvicorn（`docker/entrypoint.sh`），对外发布在哪个地址由 compose 的 `API_BIND` 决定。
  2. 覆盖率工件：`ci.yml:75-81` 的 `Upload coverage` 是 `if: always()` 加 `if-no-files-found: ignore`，产出它的运行步没有 `--cov-fail-under`，全仓库也没有任何下限 —— 测试崩溃、报告根本没写出来时这一步仍然是绿的，而且那个百分数从来没有人和它比较过。
  3. 发布说明：`release.yml` 的 `Generate release notes` 与 `Create GitHub Release` 都在 `if: always()` 下运行（这是有意的：镜像已经推上去了），但说明文本从不提 smoke 结果，于是任何「镜像起不来」的版本，其 Release 页面看上去都可以部署。
  4. 诊断文案：`docker/entrypoint.sh:114` 在永久失败时让运维 check `DB_HOST`，而全仓库没有任何代码读 `DB_HOST`（真名是 `POSTGRES_HOST`）；同一个错字符串还被 `backend/tests/test_database_wait.py:234` 钉住，所以修文案必须同改守卫。
- 决策：
  1. 删掉 `Settings.host` 与 `Settings.port`（`backend/app/core/config.py:81-85` 留注释说明部署持有它们），守卫判据收窄为「`backend/app` 侧必须出现 `settings.<字段>`，或 `config.py` 自己的校验器出现 `self.<字段>`」；`test_the_knobs_that_did_nothing_are_gone` 按 `^\s+host:\s` / `^\s+port:\s` 钉住它们不许回来。
  2. `ci.yml` 删掉 `if-no-files-found: ignore`（报告缺失就是这一步失败），并按实测钉下限：本机全量 `pytest -o addopts= --cov=app --cov-report=xml` = 8180 statements / 1148 missed = **86%**，因此运行步用 `--cov-fail-under=86`（提高覆盖率时上调；下调必须出现在 diff 里，不能悄悄发生）。
  3. `release.yml` 的说明读 `steps.smoke.outcome`，在标题下第一行写 `> **Smoke test: passed.** …` 或 `> **Smoke test: <outcome>.** … the stack was not seen to boot …`。
  4. `docker/entrypoint.sh` 的诊断文案改成 `POSTGRES_HOST`，并在同一次提交里改 `backend/tests/test_database_wait.py` 的断言、新增「`DB_HOST` 不许出现」。
- 理由：ADR-084/090/091 都建立在「守卫会红」之上。守卫问错对象时它比没有守卫更糟 —— 它把「我已经检查过了」写进了绿色。因此收窄判据（`settings.` 前缀、必须有下限、必须写判定、必须点名真实变量）比增加例外名单更接近事实：例外名单会让真正死掉的字段躲在手写理由后面。
- 影响与兼容：`HOST=` 与 `PORT=` 不再被应用读取（它们本来也没有被任何代码读；入口点读的是壳层 `PORT`，由 compose 提供）；CI 在覆盖率低于 86% 时会失败；Release 说明多一行判定；入口点的诊断文案变化要求同改测试（已同改）。
- 测试：`backend/tests/test_no_dead_settings.py`（收紧判据，`test_the_knobs_that_did_nothing_are_gone` 增补）、`backend/tests/test_workflow_integrity.py::test_no_workflow_can_accept_a_missing_coverage_report` 与 `::test_the_coverage_number_is_enforced_not_merely_printed`、`backend/tests/test_release_pipeline.py::test_the_release_notes_carry_the_smoke_verdict`、`backend/tests/test_database_wait.py::test_a_permanent_answer_is_not_waited_for`。红证据见 `docs/15` 的 v1.6.9 行。
> 修订（ADR-105）：上面「影响与兼容」写的「CI 在覆盖率低于 86% 时会失败」当时并不成立 —— `--cov-fail-under=86` 配上 pytest-cov 默认的 `--cov-precision=0` 后，判定用的是 `round(total, 0)`（85.96% 被进位成 86%，于是放行），而同一支运行打印的 `FAIL Required test coverage of 86% not reached` 用的是未取整的 85.96%。v1.7.0 改成 `--cov-fail-under=85.5 --cov-precision=2`：有效阈值与原来的 85.5% 完全相同，但判定与打印从此读同一个数。

## ADR-103：文档面也是 API 的门（豁免名单必须是一个清单，而不是一个前缀）

- 背景：`backend/app/api/main.py` 的鉴权中间件当时按前缀决定放行：`if not path.startswith(settings.api_prefix) or path in open_paths: return await call_next(request)`，即「不在 `/api/v1` 下的一律不管」。而 OpenAPI schema 在应用根 `/openapi.json`、Swagger UI 在 `/docs`（FastAPI 的 `openapi_url` / `docs_url` 默认值），于是这两扇门**按构造**是开的：`.env.example` 声称「除两个 health 探针外所有 API 请求都要 Token」，实际任何能到达 API 端口的客户端都能读到完整 API 形状；而经 8081 时 nginx 会为 `/api/` 注入 Token，所以一个匿名访客只要打开 `/docs`，就能用 Swagger 的「Try it out」以代持身份调用所有端点。ADR-028 第 2 条（`docs/17_DECISIONS.md:277`）把这件事写成了决策（「`/docs` 与 `/openapi.json` 不在 `/api/v1` 下，保持可访问」），`backend/tests/test_api_auth.py:56-58` 的 `test_openapi_is_not_gated` 又把它钉成了断言；`docker/web.nginx.conf` 的 `/api/` location 带 `${AUTH_LINE}`，`/docs` 与 `/openapi.json` 两个 location 不带 —— 同一份配置里两个答案。
- 决策：
  1. 中间件只豁免一个清单：`open_paths = {f"{settings.api_prefix}/healthz", f"{settings.api_prefix}/health"}`，其余**所有**路径（含 `/docs`、`/openapi.json`、`/redoc` 与未知路径）在设置了 `API_AUTH_TOKEN` 时都要求 `Authorization: Bearer <token>`。
  2. `docker/web.nginx.conf` 的 `/docs` 与 `/openapi.json` 两个 location 也注入 `${AUTH_LINE}`，因此浏览器经 8081 打开文档仍然可用（页面自己去取 `/openapi.json` 时同样经这个代理）。
  3. `scripts/Test-NasDeployment.ps1` 读 schema 时带上与 `Invoke-Api` 相同的 `Authorization` 头，否则设了 Token 的部署会让自检报一个其实是自己 401 的失败。
  4. ADR-028 第 2 条的末句被本 ADR 取代，已在该条后追加 `> 修订（ADR-103）` 指针。
- 理由：一扇门要么需要凭证要么不需要，判据应该是「它是不是这个应用提供的入口」，而不是「它的路径长什么样」。旧判据让任何不在 `/api/v1` 下的新路由默认对外，这正是「默认安全」的反面；它与 ADR-090/091（守卫不能按名称放行）和 ADR-095（同一事实不能有两份）是同一类问题在 HTTP 层的实例。豁免名单之所以必须写成清单，是因为清单能被守卫逐字对照文档，而前缀永远无法被对照。
- 影响与兼容：默认部署（`API_AUTH_TOKEN` 为空）行为完全不变（`${AUTH_LINE}` 展开为空行，中间件直接放行）。设置了 Token 的部署：经 8081 的浏览器访问不受影响（容器代持 Token）；直连 API 端口且不带头的客户端会拿到 401，包括打开 `/docs` —— 这是有意的，`README.md:98`、`.github/workflows/release.yml:292` 与 `scripts/version.sh:157` 宣传的一直是 `http://<nas-ip>:8081/docs`。`/redoc` 同样被门住（未在文档里宣传，故不计入兼容性影响）。
- 测试：`backend/tests/test_api_auth.py::test_the_documentation_surface_is_gated_too`（设 Token 后 `/docs`、`/openapi.json` 无头 == 401、带头 == 200、错 Token == 401）与 `::test_an_unknown_path_is_not_a_way_around_the_token`（`/redoc` ∈ {401, 404}、`/api/v1/strategies` == 401），原 `test_openapi_is_not_gated` 删除。新守卫 `backend/tests/test_exposure_surface.py`：凡 `proxy_pass http://quantlab-api:8080` 的 location 必须含 `${AUTH_LINE}`、中间件不得再出现 `startswith(settings.api_prefix)`、`.env.example` 写明的豁免与中间件的 `open_paths` 必须一致并点名 `/docs`、NAS 自检的 schema 步骤必须带头。行为侧还有 CI 的《Exposure assertions》步骤（ADR-104）。红证据见 `docs/15` 的 v1.7.0 行。

## ADR-104：已文档化的开关必须被真的按下去过（一次 CI 里按下去，而不是每次靠人记得）

- 背景：`.env.example` 从 v1.6.7 起如实写明「8081 是默认部署唯一对局域网开放的端口，只想自己用就改 `WEB_BIND=127.0.0.1`」，而 `git grep WEB_BIND` 在 `.github/`、`scripts/*.sh`、`scripts/*.ps1` 里**零命中** —— 这条关闭办法从来没有被任何自动化执行过一次。同一份文件里关于 `API_AUTH_TOKEN` 的那句话（「除两个探针外都要 Token」）在 v1.7.0 之前也是错的，而 CI 里同样没有任何一处设置过 Token。两句话都写在文档里，都没有一次运行证明它们存在。
- 决策：CI 的 compose smoke 作业在默认栈（无 Token、默认绑定）跑完 `Smoke 1/5`–`5/5` 之后新增一步《Exposure assertions (ADR-103/104)》：把 `.env.example` 复制成 `/tmp/exposure.env` 并追加 `WEB_BIND=127.0.0.1` 与 `API_AUTH_TOKEN=ci-exposure-token-1` 两行，再用这个 env file 重建 `api` 与 `web`（`up -d --no-deps`，其余容器不动；不靠壳层环境变量是因为 shell 与 `--env-file` 谁优先由 compose 决定，覆盖值一旦悄悄丢失，这一步就会在什么都没验证的情况下报成功），然后断言三件事 ——(1) `127.0.0.1:8081/healthz` 通，而 `http://<本机非回环地址>:8081/healthz` 必须连不上；(2) 直连 `127.0.0.1:8080` 不带 Token 时 `/docs`、`/openapi.json`、`/api/v1/strategies` 全 401，而 `/api/v1/healthz`、`/api/v1/health` 为 200；(3) 经 8081 不带任何头时 `/docs`、`/openapi.json`、`/api/v1/strategies`、`/healthz` 全 200（Web 容器为每扇门代持了 Token）。默认栈那一步保持不变。
- 理由：把「文档说的」与「跑过的」绑在一起的代价只有一次容器重建，收益是这两句话从此不可能悄悄变错。审计发现 M3 之后只剩三个选项：改默认绑定（选项 A）、在 preflight 里强制 opt-in（选项 B）、不改行为只验证（选项 C）。选项 A 会改变所有现有用户从别的机器访问 `http://<nas-ip>:8081` 的方式 —— 那是产品决策，不该由补丁版本替用户做；选项 B 只在 CI 与 `Test-NasDeployment.ps1` 里跑，`docker compose up -d` 不经过它，所以它约束的是维护者而不是部署者。本版因此选 C，并把「默认仍是开放的」继续如实写在 `.env.example` 与 `docs/15` 的欠账里。
- 影响与兼容：CI 的 compose smoke 作业多约一分钟（一次 `up -d --no-deps` 加三组 curl）；本地与 NAS 部署零变化（这一步只在 CI 里改环境变量，末尾仍是 `docker compose … down -v`）。没有 Token 的默认部署不受鉴权与绑定影响。
- 测试：`backend/tests/test_exposure_surface.py::test_ci_presses_the_documented_off_switch_and_the_token` 断言 `.github/workflows/ci.yml` 里同时存在 `WEB_BIND=127.0.0.1`、`API_AUTH_TOKEN=$TOKEN`、这两个覆盖值确实被写进 `/tmp/exposure.env` 且该 env file 被交给 compose（该路径至少出现两次）、非回环探针 `http://$lan:8081/healthz`、直连三扇门的清单 `/docs /openapi.json /api/v1/strategies` 以及 401/200 的判定（防止这条证据被静默删掉）。红证据见 `docs/15` 的 v1.7.0 行。

## ADR-105：覆盖率下限必须和它打印的判定用同一个数（一个检查的两个半边互相矛盾）

- 背景：ADR-102 给 CI 加了 `--cov-fail-under=86`，守卫 `backend/tests/test_workflow_integrity.py::test_the_coverage_number_is_enforced_not_merely_printed` 断言这个选项存在、数值落在 50–100 —— 它问的是「有没有写」，而不是「写了之后真的会失败吗」。v1.7.0 提交前本地按 CI 的原命令跑了一遍：`pytest -o addopts= --cov=app --cov-report=term-missing --cov-report=xml --cov-fail-under=86` 输出 `TOTAL 8178 1148 86%` 与 `FAIL Required test coverage of 86% not reached. Total coverage: 85.96%`，而进程退出码是 **0**；v1.6.9 的 ci run `37115089006` 里 `Run tests` 步骤打印了同一行 `FAIL`，而该步骤与整个作业的结论都是 **success**（`gh api /repos/bobvane/My-Quant-Lab/actions/jobs/111180241785` 逐步骤确认；ci.yml 三个作业都没有 `continue-on-error`，也没有自定义 shell）。根因在 pytest-cov 7.1.0 的两条路径：真正的判定在 `pytest_cov/plugin.py:373` 调 `coverage.results.should_fail_under(total, fail_under, precision)`，实现是 `round(total, precision) < fail_under`；而 `pytest_terminal_summary`（`plugin.py:411-421`）只用未取整的 `self.cov_total < self.options.cov_fail_under` 决定打印 `FAIL …` 还是 `reached`。`--cov-precision` 默认 0，于是 85.96 先被进位成 86、判定为「够」（真实阈值是 85.5），打印的那句话却按 85.96 说「没够」—— 同一个检查的两个半边互相矛盾，而且矛盾的方向恰好让失败看起来像成功。
- 决策：
  1. `ci.yml` 的 `Run tests` 改成 `--cov-fail-under=85.5 --cov-precision=2`。显式指定精度后，判定比较的是 `round(total, 2) < 85.5`，与打印判定读的是同一个数；85.5 正是原先「精度 0 + 下限 86」真正在执行的阈值（`round(total, 0) >= 86` 等价于 `total >= 85.5`），所以门槛没有被放松，只是被写成它真正的样子。YAML 里附了上面这段理由与本日实测值（8178 statements / 1148 missed / 85.96%）。
  2. `--cov-precision=2` 同时让覆盖率表格显示 `85.96%` 而不是取整后的 `86%`，不再出现「表里写 86、判定说没到」的观感。
  3. 守卫改名并改问法：`backend/tests/test_workflow_integrity.py::test_the_coverage_floor_means_what_the_run_prints` 允许下限是小数、且**必须**同时出现 `--cov-precision` 并 ≥ 2 —— 没有精度就不能保证判定与打印读的是同一个数。
- 理由：一个检查的价值全在「它会不会失败」，而「会不会失败」取决于它拿哪个数去比。`--cov-fail-under` 这个名字与它打印的 `FAIL` 文案都强烈暗示它在失败，实际却为一次 85.96% 的运行放行，v1.6.9 因此把一条从未生效的门禁写进了 ADR-102 的「影响与兼容」。这与 ADR-090（永不失败的检查只是装饰）、ADR-102（守卫必须问对问题）是同一类缺陷的第三种实例：不是没写检查，也不是问错了对象，而是**判定与它自己的输出用了两个不同的数**。显式写出精度是唯一能让「读到的数」与「比对的数」重合的办法。
- 影响与兼容：CI 的通过门槛从「实际执行 85.5%」变成「声明 85.5%」，数值不变；一条 85.96% 的运行现在打印 `Required test coverage of 85.5% reached. Total coverage: 85.96%` 并以 0 退出；任何让覆盖率跌破 85.5% 的提交从此真的会让 `Run tests` 失败（此前只要小数部分把它抬过线就会被静默放行）。覆盖率表格的显示精度随之提高，`coverage.xml` 一直是全精度、未受影响。
- 测试：`backend/tests/test_workflow_integrity.py::test_the_coverage_floor_means_what_the_run_prints`（下限可含小数、必须配 `--cov-precision` ≥ 2、仍必须有 `--cov-report=xml`）。行为证据：同一条 CI 命令在改动前打印 `FAIL … Total coverage: 85.96%` 却退出 0；改成 `--cov-fail-under=85.5 --cov-precision=2` 后打印 `Required test coverage of 85.5% reached. Total coverage: 85.96%` 且退出 0；把下限抬到 86（精度 2）后打印 `FAIL … not reached` 且退出 1 —— 两个半边从此一致。红证据见 `docs/15` 的 v1.7.0 行。
