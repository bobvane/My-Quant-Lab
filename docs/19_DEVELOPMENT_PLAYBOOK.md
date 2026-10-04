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
