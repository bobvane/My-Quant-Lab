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

## 5.1 推送与网络（Windows 上的两个坑，v1.9.7 实测）

- **`git push` 报 schannel `CRYPT_E_REVOCATION_OFFLINE`**：Windows 的 schannel 在离线或代理环境下拿不到吊销列表，握手直接失败；同一个远端用 OpenSSL 后端就通。给这一条命令加参数即可，不要改全局配置：
  `git -c http.sslBackend=openssl -c http.proxy=http://192.168.2.5:7893 push <url> main refs/tags/vX.Y.Z`。
- **代理与 token 都只在命令行上给**：`http.proxy` 按需写在 `-c` 里；远端用临时 token URL 推送时可以改用 https 而不是 ssh，但**推完必须用 `git ls-remote` 核实**远端真的有了那个 commit 与 tag（本地 `origin/main` 在临时 URL 方案下不会更新，`git status` 说明不了任何事）。
- **`Z:` 映射盘不保证存在**：会话重启后 `Set-Location Z:\...` 可能报 `Cannot find drive`。文件工具走 UNC 路径没问题，但 `npm` / `cmd` 这类必须在真实盘符下运行的工具要改用 `scripts\Invoke-FrontendChecks.ps1`（它会镜像到 `%LOCALAPPDATA%\mql-fe-build` 再构建）。

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
