# 19 AI 编程实施手册

## 1. 如何把本项目交给编程 AI

推荐把本目录放到 GitHub repository 根目录，并把 `16_AGENTS.md` 重命名或复制为 `AGENTS.md`，必要时再创建工具特定的 `CLAUDE.md` / `.cursor/rules/`。

第一轮不要让 AI"一次性做完全部项目"。应该按 Phase 0 → Phase 8 顺序执行。

## 2. 每阶段 AI Prompt 模板

```text
读取项目根目录的 AGENTS.md 和 docs/ 下的开发文档。
当前只实现 [PHASE/MODULE]。

要求：
1. 不实现超出当前阶段的功能。
2. 先检查现有代码，再修改。
3. 保持已有 API/domain contract 兼容。
4. 为新增逻辑编写测试。
5. 运行相关测试并报告结果。
6. 不以 TODO 代替核心实现。
7. 完成后输出：变更文件、设计决定、测试结果、已知限制。
```

## 3. 初始项目目录建议

```text
my-quant-lab/
├── AGENTS.md
├── README.md
├── docker-compose.yml
├── .env.example
├── docs/
├── backend/
├── frontend/
├── migrations/
├── examples/
└── tests/
```

## 4. 第一批开发任务

### Task 1
创建 backend skeleton + PostgreSQL + Redis + Alembic + health API。

### Task 2
创建 Asset/OHLCV/Feature/Strategy/StrategyVersion domain model。

### Task 3
实现 EMA/ATR/RSI/MACD/Bollinger + sample PA features。

### Task 4
实现 Strategy DSL parser + validator + deterministic executor。

### Task 5
实现 Backtest engine + golden fixtures。

### Task 6
实现 Paper Account + paper execution。

### Task 7
实现 MarketDataProvider interface 和第一个 provider。

### Task 8
实现 Ghostfolio adapter。

### Task 9
实现 AIProvider + OpenAI-compatible provider + budget/cache。

### Task 10
实现 GitHub importer first as read-only analysis/import flow。

## 5. 每次合并前检查

```text
[ ] Tests pass
[ ] No lookahead
[ ] Strategy version immutable
[ ] Backtest deterministic
[ ] Secrets not logged
[ ] No arbitrary GitHub code execution
[ ] Ghostfolio remains read-only
[ ] Paper account isolated
[ ] API docs updated
[ ] Docker healthchecks pass
```

AI 层改动另加（v1.9.7 起，ADR-150 至 ADR-153）：

```text
[ ] 新角色的提示词写进 backend/app/ai/contracts/*.md，而不是 Python 常量
[ ] 能力清单仍从代码派生（backend/app/capabilities.py），没有手抄的表
[ ] AI 调用走 backend/app/ai/runtime.py 的 run_task()，没有绕过预算与审计
[ ] 外部资料走 UntrustedSource / assemble_messages()，注入样本有测试
[ ] 新设置真的被消费（backend/tests/test_no_dead_settings.py），并同步 .env.example 与 docker-compose.yml
[ ] 补丁版按 ADR-086 不部署 NAS
```

写文件时用 LF：本仓库的 diff 与 CI 都以 LF 为准，PowerShell 的 `Set-Content` 会带进 CRLF，写完要数一遍 CR 字节（`[IO.File]::ReadAllBytes()` 里不等于 10 的 13 就是 CR）。

研究层改动另加（v1.9.8 起，ADR-154 至 ADR-157）：

```text
[ ] 模型输出先过 backend/app/ai/research_schemas.py 的四道门，任何失败都是 REJECT，不自动修正
[ ] 每条规则的 provenance 与证据都落在规则粒度上，AI 补的规则标 ASSUMED 并在界面上说出来
[ ] 能力裁决在服务端算（assess_draft_capabilities()），模型自报更强只记 capability_overclaim
[ ] 不产生任何结果指标（CAGR / Sharpe / 回撤…）：schema 里没有这些字段，散文里的数字标 UNVERIFIED
[ ] 草案 executable = false，不落 strategy_versions，不触发回测，不调用工具
[ ] 研究层调用全部经 run_task()，AITask.research_run_id 串起来，tool_calls 保持 []
```

迁移改动另加（v1.9.9 起，ADR-158）：

```text
[ ] upgrade() 里每个 op.create_table 都在它外键引用的同文件表之后（引用早期 revision 的表不算）
[ ] downgrade() 严格逆序：先删引用者、再删被引用者（PostgreSQL 会报 DependentObjectsStillExist）
[ ] 改过迁移文件就跑 backend/tests/test_migration_revisions.py（静态守卫，不连库、秒级）
[ ] 表名/约束名不超过 PostgreSQL 的 63 字节限制，revision id 不超过 32 字符（0008 的老教训）
```

研究层证据与材料改动另加（v2.0.0 起，ADR-159 至 ADR-162）：

```text
[ ] EXPLICIT 规则的 evidence 必须带 quote，且 quote 规范化空白后能在读入材料里逐字找到
    （找不到是 evidence_mismatch → REJECT，不是警告；缺引文 evidence_missing_quote）
[ ] 引文核对结果由服务端写回 verified / char_start / char_end / verified_against，不进给模型的 schema
[ ] hypothesis 与 draft 的 unknowns 要指名到 rule_id；field 级说法只在「该字段只有一条 EXPLICIT」时免罪
[ ] 材料保留按种类：user_input 整份留，其余默认 ≤500 字符摘录；要整份留必须带 license_note
[ ] source_hash（用户交上来的原文）与 text_hash（模型读到的文本）分开记；少留时发 excerpt_limited
[ ] 任何新的 AI provider 调用都只能经 run_task()：加完模块跑 backend/tests/test_ai_provider_boundary.py
```

来源摄取改动另加（v2.1.0 起，ADR-163 至 ADR-166）：

```text
[ ] 任何新的出站读取都先过 app/sources/guard.py 的 check_url()：判解析出来的地址，不判 hostname 字符串
[ ] 实际 TCP 连接必须用已验证的 IP（fetch.py 的 PinnedBackend.connect_tcp），Host 头与 TLS SNI 仍用原 hostname
[ ] redirect 每一跳重跑完整 guard；robots.txt 与正文同等受管（同一 guard / 超时 / 大小 / 逐跳重新校验）
[ ] app/sources/ 里不许 import httpx、不许读 HTTP_PROXY / HTTPS_PROXY / ALL_PROXY（trust_env=False 的等价物是根本没有那层）
[ ] 新增一种危险地址就往 backend/tests/test_source_ssrf.py 补一例；改了抓取逻辑就跑 test_source_fetch.py
[ ] 解析只做减法：不执行 JS、不做 OCR、不加关键词/正则 injection detector（test_source_injection.py 会拦）
[ ] 快照只追加、不覆盖；不新增任何第三方全文列（full_text / content / body / raw_text / raw / blob / payload）
[ ] 摄取端点不调用模型、不建 AITask、不花 AI 预算；policy=full 且 truncated=true 必须如实回报
[ ] 材料进入研究者时仍然是 UntrustedSource：text 与 uri 同给时 text 优先并记 text_preferred，绝不偷偷联网
```

编译器契约改动另加（v2.2.0 起，ADR-167）：

```text
[ ] 先改 docs/29 的契约，再改代码：契约是规范、代码是它的实现；不一致时改代码或改契约并补 ADR，不许「实现说了算」
[ ] 编译器必须是纯函数：只读 draft.draft_json + capability_report_json，不读 AITask.output_json、不读模型响应原文、不联网、不调模型
[ ] 不许创建 Strategy 行、不许分配版本号、不许落库：落库只经 POST /ai/strategy/drafts/{draft_id}/compile 走 strategy_service.create_strategy_version()
[ ] 拒绝不许做成 warning：三态与 15 个拒绝码由 docs/29 冻结；缺槽位 / 歧义 / 表达不了 / 能力缺失都必须拒绝，不许替用户决定
[ ] 不许把 pydantic 默认值当取值来源：max_position_pct → 1.0、fee_bps → 0.0、sizing.mode → fixed_fraction 都是「没说」，不是「已定」
[ ] COMPILER_VERSION 与 dsl.SCHEMA_VERSION 独立，且不得写进 dsl_json；不改 StrategySpec 1.0（ADR-155 继续有效）
[ ] 动到契约就同步 docs/29 + backend/tests/test_compiler_contract.py，并跑 ruff format --check app tests
```

## 5.1 推送与网络（Windows 上的两个坑，v1.9.7 实测）

- **`git push` 报 schannel `CRYPT_E_REVOCATION_OFFLINE`**：Windows 的 schannel 在离线或代理环境下拿不到吊销列表，握手直接失败；同一个远端用 OpenSSL 后端就通。给这一条命令加参数即可，不要改全局配置：
  `git -c http.sslBackend=openssl -c http.proxy=http://192.168.2.5:7893 push <url> main refs/tags/vX.Y.Z`。
- **代理与 token 都只在命令行上给**：`http.proxy` 按需写在 `-c` 里；远端用临时 token URL 推送时可以改用 https 而不是 ssh，但**推完必须用 `git ls-remote` 核实**远端真的有了那个 commit 与 tag（本地 `origin/main` 在临时 URL 方案下不会更新，`git status` 说明不了任何事）。
- **`Z:` 映射盘不保证存在**：会话重启后 `Set-Location Z:\...` 可能报 `Cannot find drive`。文件工具走 UNC 路径没问题，但 `npm` / `cmd` 这类必须在真实盘符下运行的工具要改用 `scripts\Invoke-FrontendChecks.ps1`（它会镜像到 `%LOCALAPPDATA%\mql-fe-build` 再构建）。

## 5.2 已经推上去的 tag 红了怎么办（v1.9.8 事故，v1.9.9 修好）

v1.9.8 的 tag 推上去之后，GitHub 上一次红了三处：CI 的 `Run PostgreSQL regression tests`（4 条 PostgreSQL 回归全部 setup ERROR）、CI docker compose 冒烟的 `Boot the stack`、release 的 `Smoke test the released images`。release 是**照发**的，正文里被自动写上「**Smoke test: failure.** … consider the previous tag.」——别人看到的就是一个红着的版本。

- **先分清「谁红、为什么红」**：三处红如果指向同一件事（栈起不来），根因通常只有一个。这次是 API 容器 entrypoint 的迁移链跑不过：`[entrypoint] migration attempt 3 failed` → `[entrypoint] ERROR: migrations failed; refusing to start`，所以「迁移 → 栈 → 镜像冒烟」连锁红。
- **本地为什么全绿**：`backend/alembic/versions/0013_research_layer.py` 先建 `research_artifacts`，而它外键指向的 `ai_research_runs` 更晚才建。**SQLite 接受指向尚不存在表的外键，PostgreSQL 不接受**（`psycopg.errors.UndefinedTable: relation "ai_research_runs" does not exist`）。本机没有 docker，所以 PostgreSQL 侧只能靠 CI——凡是「SQLite 能过、PostgreSQL 才炸」的东西（外键前向引用、表名/约束名超长、revision id 超长），都要按 ADR-158 的静态守卫在本地兜住。
- **不要移动已发布的 tag**：commit、tag、GHCR 镜像都留着，**只向前发一版补丁**（v1.9.8 → v1.9.9）；已发布的东西被改写比留一个红 tag 更糟。修好之后顺手把上一版的 release 说明补一句「这一版的迁移在 PostgreSQL 上失败，请用 vX.Y.Z」。
- **补丁版的版本递增**：按 ADR-079 的规则递增；v1.9.9 之后就是 v2.0.0，所以被这一版挤掉的功能顺延到下一个版本号，别塞进补丁版。
- **红版处理纪律（v1.9.9 独立验收后立的准则）**：已经 commit / 发布的版本，如果因为 CI、Release、迁移、启动这类**发布门禁**失败而不成立，可以发下一个**最小修复版**把发布契约恢复回来——但修复版只准解决阻断问题，不得借机进入下一 Phase；一旦修复涉及新功能、架构扩展、范围扩大或下一 Phase 的内容，必须先停下来问，不能自己往前推。本仓库的两次红版（v1.9.8 → v1.9.9 只改迁移顺序、v1.9.9 → v2.0.0 只做验收报告点名的 P1/P2）都是这条纪律的例子：向前修，不回滚、不移动 tag。
- **验收报告先复核再动手**：收到外部验收/评审报告时，逐条回到代码里确认（行号、常量、测试函数），把「确认 / 部分成立 / 不成立」分开写，再决定改什么。v1.9.9 报告里的 P1-01（引文不校验）、P2-01（`unknowns` 按 field 掩盖多条规则）、P2-03（`text_hash` 与 `size_bytes` 指向不同对象）与 P2-04（只有研究层守卫）都复核成立；P2-02（单项能力支持 ≠ 组合可执行）确认为事实但属于 Compiler 阶段，只写进文档、不改代码。

## 5.3 来源抓取的 SSRF 回归要求（v2.1.0 起，ADR-164）

只要动到 `backend/app/sources/`、`backend/app/api/routers/sources.py`、`backend/app/data/source_snapshot_service.py`，或 `/ai/research` 的来源解析，提交前必须跑：

```text
[ ] backend/tests/test_source_ssrf.py       （76 例：scheme / credentials / host / port / 地址分类 / 多地址 / redirect / rebinding）
[ ] backend/tests/test_source_fetch.py      （37 例：redirect 逐跳复核、大小、超时、代理环境变量无效、robots 受管）
[ ] backend/tests/test_source_parse.py      （33 例：HTML / PDF 的诚实失败与「不做检测器」）
[ ] backend/tests/test_source_snapshot.py + test_source_snapshot_migration.py（19 + 11 例：append-only、列名黑名单、downgrade 逆序）
[ ] backend/tests/test_source_research.py + test_source_injection.py（12 + 8 例：接入研究层、隔离证明）
```

几条不写进代码也必须遵守的纪律：

- **测试不联网**：`check_url(url, resolver=…)` 注入解析器，`retrieve_document(..., retrieve=…)` 注入取回函数，PDF 用手工构造的夹具；任何需要真实 DNS 或真实站点才能通过的断言都不许进仓库。
- **新发现一种危险地址/协议，先补一例再改代码**：SSRF 矩阵是清单式的，删断言或放宽 `_address_problem()` 的判定等于把边界往后挪——`docs/14` §4.1 里的表格是这份矩阵的对外说明，两边必须同时改。
- **不许为了让测试变绿而声称"完全防止 DNS rebinding"**：本版采用 `docs/27` §6.2 的方案 A（连已验证 IP + 原 hostname 的 SNI/Host），因此可以声称没有 TOCTOU 窗口；如果将来退回方案 B（先解析检查、再交给普通客户端），必须同时改 `docs/14` 的安全声明、在这里加回残余 TOCTOU 风险说明，并让测试只证明实际达到的边界。
- **抓取失败不许伪装成 AI 拒绝**：策略拒绝是 422（`detail.error == "source_blocked"`，被拒的源仍落库），抓取/解析失败是 502（`detail.error == "source_unavailable"`）；研究入口里任一源被拒绝 ⇒ 整跑 `rejected`，不静默降级。
- **摄取与 AI 预算分开**：`/ai/sources/*` 不建 `AITask`；如果哪天它开始调模型，那必须是一次新的架构决定，而不是顺手加一行。

## 5.4 编译器契约先于编译器（v2.2.0 起，ADR-167）

`StrategyDraft → StrategySpec 1.0` 这件事在本仓库里是**先冻结契约、再写实现**，不是反过来：

- **契约是规范，代码是它的实现**：`docs/29_STRATEGY_COMPILER_CONTRACT.md` 冻结输入 / 输出、`decided_by` 四个值（含显式拒绝 `ENGINE_DERIVED`）、`COMPILED` / `NEEDS_USER_DECISION` / `REJECTED` 三态、15 个拒绝码、canonical 参数形状、两个 hash 的口径与落库位置。实现与文档不一致时先问「哪一个错了」——改代码，或者改契约并补一条 ADR，**不许让「已经写成的代码」默认成为规范**。这条纪律的来源就是 `docs/28` 的只读审计：契约缺失时写实现，等于让实现顺便定规范（能力注册表按 schema 派生、`fill_model` 被回显成 SUPPORTED、`max_position_pct=1.0` 这类默认值把「没说」变成「已决定」）。
- **编译器是纯函数，且不创造策略行**：它只读草案，输出 `spec` 或拒绝；`Strategy` 行、版本号与账本由既有的 `strategy_service` 拥有。想「顺手把策略建出来」时停下来问——那会多出第四条创建路径，并留下半成品。
- **拒绝是主产物之一**：缺槽位、歧义、表达不了、能力缺失都必须变成明确的拒绝码，不许降级成 warning、不许替用户补默认值、不许把模型的措辞当成规则来源。Martin 场景（`backend/tests/research_payloads.py`）就是这条纪律的验收样本：它必须被拒绝（`NEEDS_USER_DECISION`、`spec = null`），**不是**被编译。
- **守卫怎么写**：契约的文字与常量由 `backend/tests/test_compiler_contract.py` 与 `docs/29` 双向钉住；前向守卫（`importlib.util.find_spec` / 目录判断）在实现出现后自动升级为真断言，不许用「包还不存在」当断言（`docs/29` §18）。

**这套顺序的落地状态（v2.2.0 编译器切片，工作区）**：Step 2A（契约冻结）、Step 2B（`backend/app/compiler/` 的 Compiler Core）、Step 2C（`POST /api/v1/ai/strategy/drafts/{draft_id}/compile` 与草案 → 版本绑定）、Step 2D（409 走项目 `error` 信封 = ADR-169；capability 报告按落库的 `CapabilityDecision.verdict` 读 = ADR-170）**已全部落地**，三份守卫共 **80 例**（`test_compiler_contract.py` 16 + `test_compiler_core.py` 49 + `test_compiler_api.py` 15），全仓 1361 passed / 4 skipped。工作区六处版本镜像已置为 **v2.2.0**，**当时尚未 commit / tag / push / release / deploy**（此后已发布，见本段末）——所以上面每一条纪律仍是**进行中的约束**，不是「做完就作废」的检查表。**`is_current` 激活路径不检查 `validation_status` 这件事没有在 Step 2D 里修**：它已登记为独立的 P0 级契约缺口（`docs/28` §7.3、G8/G9/G11；`docs/26` §26），要动它必须另立切片，并把 `backend/app/data/strategy_service.py`、`backend/app/api/routers/strategy_versions.py` 与信号扫描路径（`backend/app/simulation/signal_engine.py`）一起纳入范围。**该缺口已由 v2.3.0 收口**（ADR-171；切片范围正是这里点名的这三处），落地状态见下面的 §5.5；`v2.2.0` 编译器切片与补丁版 `v2.2.1` 随后也已发布（commit `53a166748` / `ac486b3d1`）。

## 5.5 激活有效性门（v2.3.0 起，ADR-171）

`is_current` 是**分发**角色：信号路径只看它选版本（`backend/app/simulation/signal_engine.py:352`、`:383`）。因此「谁能成为当前版本」必须由契约回答，而不是由默认值回答——在 v2.3.0 之前它恰恰是由默认值回答的（`make_current` 默认 true，编译端点又继承了这个默认）。

- **唯一可成为当前版本的状态是 `valid`**：`pending` 与 `invalid` 都不行，`pending` 不享有任何宽容。判定只有一处来源——`backend/app/data/strategy_service.py` 的 `ACTIVATABLE_VALIDATION_STATUSES = ("valid",)`，激活、`make_current=true` 的创建、扫描选择三条路径共用，不许各写各的 `== "valid"` 字面量。
- **编译器产物不自动 current**：编译的语义是产出，不是上线；编译出的版本要驱动信号，必须再由人显式激活（而激活现在要求 valid）。这与 `docs/15` Phase 8 的验收（不存在「AI 一句话升级策略」的路径）一致，也是 `docs/29` §5.3「编译器是纯函数、落库归服务层」的自然推论。
- **拒绝的形状**：两种拒绝都是 422 + 项目既有的 `{"detail": …}`，句子以 `strategy version is '<status>', not 'valid'` 开头（与 `backend/app/api/routers/backtests.py:56-60` 逐字相同）；**不新造信封**（ADR-169 的教训：同一个状态码在同一个端点不许有两种读法）。`make_current=false` 永远允许——把无效版本记进账本是账本需求，被禁止的只是「无效 + 成为当前」，且被拒绝的创建**一行都不写**。
- **拒绝也要留痕**：被拒绝的激活写 `strategy_version_activation_rejected`；被拒绝的创建不写（它没有产生任何实体，`entity_id` 无值可指），由 422 响应本身充当记录——这条不对称是刻意的。
- **守卫怎么写**：契约文字与常量由 `backend/tests/test_activation_validity.py` 与 ADR-171 双向钉住；扫描与编译两条路径都必须带**正向对照**，不许用「什么都没发生」当断言——扫描器的防御性 `except`（`signal_engine.py:362-364`、`:393-395`）会让坏数据看起来像「没有信号」。

**落地状态（v2.3.0 Step 1）**：契约已冻结（ADR-171），门已在三条写入路径上落地——`backend/app/data/strategy_service.py` 导出判定常量、拒绝异常与冻结句，`backend/app/api/routers/strategy_versions.py` 拒绝并审计，`backend/app/api/routers/ai.py` 落库时显式 `make_current=False`，`backend/app/simulation/signal_engine.py` 的两条扫描查询按 `is_current AND validation_status IN ('valid',)` 选择；Step 0 的守卫测试全部转为通过。本切片不加迁移、不改 DB 结构、不改引擎计算、不改前端与版本号。**发布与验收**：commit `216ddd5cd`、annotated tag `v2.3.0`，CI run 37423731812 与 release run 37423742355 皆 success；NAS 只读实机验收 **Overall READY / 验收通过、Critical Findings = 0**（Activation Gate 与 Compiler no-auto-current 记为 PARTIAL，唯一原因是线上不存在可安全测试的 pending / invalid 版本与 draft——按验收纪律不创建测试数据），完整读数见 `docs/15_ROADMAP_ACCEPTANCE.md` 的「v2.3.0 的读数」段。

## 6. 未来扩展策略

当新增策略时，优先：

```text
Strategy DSL
→ fixture tests
→ backtest
→ OOS
→ paper
```

不要为了一个策略去修改 Backtest Engine 的核心语义；如确有必要，先更新 ADR 和回测 golden tests。
