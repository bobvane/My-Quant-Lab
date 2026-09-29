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
