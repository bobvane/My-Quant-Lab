# 26 AI Quant Layer Gap Analysis / AI 研究层差距分析

> 状态：**现状审计与差距分析**（不是"已实现规格"）。基线 `version.txt` = v1.9.6。
> 权威输入：`docs/25_AI_QUANT_RESEARCH_LAYER_PLAN.md`（用户提供的升级计划逐字副本，计划章节号引用即指该文件）。
> 已确认的架构决策：C1–C10 与新增产品原则（计划 §十三）、Martin 验收场景（计划 §十四）、指标分期（计划 §十五）、
> Prompt 单一来源（计划 §十六）、Cache key（计划 §十七）、Audit（计划 §十八）、安全（计划 §十九）、
> UI 分层（计划 §二十）、七条不变量（计划 §二十一）。
> 方法：只读审计。每条重要结论带 `path:line`，并注明对应计划章节。
> 阶段边界：本阶段只做 Read / Audit / Gap Analysis / Architecture clarification / Documentation（计划 §七十二、§七十三、§七十四）。
> **本文件完成后停止，不进入 v1.9.7。**

---

## 0. 结论摘要（先看这 12 条）

1. **`StrategySpec` 名字与契约已被占用**：`backend/app/strategies/dsl.py:255`，`extra="forbid"`（`backend/app/strategies/dsl.py:258`），全仓 104 处引用。→ 正式采用 A 方案：**DSL 1.0 不动**，新增研究层实体（计划 §八、§七、§四十二）。
2. **AI 层只有 2 条解释链、3 个源文件**，不是 5 个角色：`backend/app/ai/{__init__.py,provider.py,explain.py}`；`signal_explain@1.0.0`（`backend/app/ai/explain.py:291`）与 `backtest_explain@1.0.0`（`backend/app/ai/explain.py:323`）。计划 §四十九 的"保留现有 5 角色"应按**新建**估。
3. **没有 Capability Registry、没有 tool calling、没有 experiment 实体、没有 GitHub 以外的研究源抽象、没有 URL/PDF 摄取**（全仓零命中）。计划 §十九、§二十四、§四十三、§二十三 全部是**新建**。
4. **缓存 key 不含 provider / model**：`AIRequest.input_hash`（`backend/app/ai/provider.py:79-89`）只有 task + `name@version` + facts。计划 §三十八 要求扩展，且换模型必须 miss。
5. **Prompt 有三套潜在来源**：markdown（计划 §五 要新建）、DB `ai_prompts`（`backend/app/domain/models.py:635-648`，唯一写入方是 `backend/app/api/routers/ai.py:320-355`）、硬编码常量（`backend/app/ai/explain.py:75-90`）。必须收敛为"文件为唯一人工源"（计划 §十六）。
6. **预算有三套说法**：实际生效的 `ai_providers.daily_budget_usd`（`backend/app/domain/models.py:609`，默认 2.0 USD）、无读取方的 `Settings.ai_daily_budget_usd`（`backend/app/core/config.py:147`）、文档里的 ¥2/5/10（`docs/06_AI_LAYER.md:126-131`）。计划 §六十五 的 CRITICAL/IMPORTANT/OPTIONAL 需落成代码常量。
7. **能力边界比计划示例窄**：指标 EMA/SMA/RSI/ATR/MACD/Bollinger、8 个算子、2 种 `fill_model`、单标的回测；**没有** portfolio / universe / 横截面排名 / 再平衡。→ 计划 §十一 的动量排名示例、§四十五–§四十七 的多市场机制，今天只能输出 `UNSUPPORTED` / `PARTIALLY_SUPPORTED`，这正是计划 §十三 新增原则要求的诚实行为。
8. **Calmar / Recovery Factor / VaR / CVaR 完全不存在**（全仓 grep 零命中）；`sortino / max_drawdown_duration_bars / profit_factor / expectancy / exposure / turnover` 已存在（`backend/app/research/metrics.py:40-51`）。→ 按计划 §十五 分期，定义未定前不实现。
9. **命名冲突**：前端 `/research`（`frontend/src/main.ts:20`，v1.9.3 交付的普通用户向导）与后端 `/api/v1/research/*`（`backend/app/api/routers/research.py:45,85,129,188,266,323`，量化研究）。→ 决策 C9：AI 研究台走 `/lab` 与 `/api/v1/ai/research*`。
10. **审计字段不足**：`ai_tasks`（`backend/app/domain/models.py:651-672`）缺 role / output_hash / source_ids / strategy_version / tool_calls；`ai_usage`（`backend/app/domain/models.py:675-691`）缺"研究轮次"维度。计划 §三十七 要求 → 迁移 0012 起。
11. **现有 AI 面板大多不受高级模式门控**（Dashboard / Signals / StrategyDetail / Backtest），只有设置页是对的（`frontend/src/views/SettingsView.vue:723` vs `:727`）。计划 §二十/§六十九 的"普通模式只看到人话"需要补门控与守卫。
12. **docx 两份副本逐字节相同**（63,081 字节，sha256 `062c454aea795117…`，1,289 段 / 248 标题 / 0 表格），`python-docx 1.2.0` 可用。→ 决策 C8：V1.0 保持不动，新建 V1.1 + markdown 镜像 + 一致性测试。

---

## 1. Existing Capability（现有能力）

### 1.1 AI 层实现面（计划 §二、§四、§五、§四十九）

| 事实 | 位置 |
| --- | --- |
| AI 层只有 3 个源文件，无 `backend/app/services/` | `backend/app/ai/__init__.py`、`backend/app/ai/provider.py`（309 行）、`backend/app/ai/explain.py`（486 行） |
| Provider 的"服务层"（CRUD / 加密 / 连通性）在 data 目录 | `backend/app/data/ai_provider_service.py`（305 行） |
| 解释链只有 2 条 | `backend/app/ai/explain.py:253 explain_signal()`、`:303 explain_backtest()` |
| 事实组装（只读引擎结果） | `backend/app/ai/explain.py:203 build_signal_facts()`、`:227 build_backtest_facts()` |
| 预览解释（不落库） | `backend/app/ai/explain.py:273 explain_signal_facts()` |
| 两段 system prompt 是硬编码英文常量 | `backend/app/ai/explain.py:75-82 SIGNAL_SYSTEM_PROMPT`、`:84-90 BACKTEST_SYSTEM_PROMPT` |
| 输出 schema 只有 2 个，字段全是 string / array-of-string，**无任何数值字段** | `backend/app/ai/provider.py:37-48 SIGNAL_EXPLANATION_SCHEMA`、`backend/app/ai/explain.py:57-73 BACKTEST_EXPLANATION_SCHEMA` |

### 1.2 Provider / 路由 / 预算（计划 §三、§三十一、§三十二、§三十三、§六十五）

| 事实 | 位置 |
| --- | --- |
| Provider 契约是 Protocol，**不叫** `generate_structured()/generate_text()` | `backend/app/domain/protocols.py:40-65`：`chat(self, messages, *, model, temperature=0.1, max_tokens=None) -> str`、`structured_output(self, messages, *, model, schema) -> dict[str, Any]`、`list_models(self) -> list[str]`、`health_check(self) -> bool` |
| 唯一具体实现 | `backend/app/ai/provider.py:92 OpenAICompatibleProvider.__init__(self, base_url: str, api_key: str, name: str = "openai_compatible")` |
| 请求对象与哈希 | `backend/app/ai/provider.py:64-89 AIRequest`（task_type / prompt_name / prompt_version / system_prompt / user_prompt / structured_facts / schema / model / max_tokens=900 / temperature=0.1），`input_hash()` 在 `:79-89` |
| 三层路由与能力分档 | `backend/app/ai/provider.py:211 _tier_rank()`、`:215-223 _TASK_CAPABILITY`（`daily_summary/strategy_explanation/signal_explanation→cheap`，`backtest_analysis→standard`，`strategy_review/research_report/repository_analysis→high`）、`:226-227 task_capability()`、`:230-309 AIRouter`（`pick()` `:248-280`） |
| 模型成本字段 | `backend/app/domain/models.py:626-627`（`input_cost_per_mtok` / `output_cost_per_mtok`） |
| 核心业务里**没有**品牌分支 | 全仓无 `if provider == ...`；`provider_type` 仅出现在 `backend/app/domain/models.py:602`、`backend/app/domain/enums.py:102-105`（`openai_compatible/anthropic/google`） |
| 预算真正生效处 = 每 provider 一行的字段 | `backend/app/domain/models.py:609 daily_budget_usd`（Numeric(12,4)，默认 2.0）；`backend/app/ai/explain.py:158 spent_today_usd()`；`:358-359` 汇总各 provider；`:414-419` 抛 `BudgetExceeded`（文案含 `quantitative features keep working`）；`backend/app/ai/provider.py:60-61` 定义异常 |
| 预算是按 provider 而非按角色/任务类 | 同上；`backend/app/api/routers/ai.py:73-92` 把 `BudgetExceeded` 映射为 HTTP 429 |
| 全局配置键存在但**无读取方** | `backend/app/core/config.py:147 ai_daily_budget_usd`；只在 `backend/app/api/routers/settings.py:57` 回显与 `backend/tests/test_config.py:51-55` 断言 |
| token 是估算 | `backend/app/ai/explain.py:93-96 estimate_tokens()`（字符数 / 4）；`backend/app/ai/provider.py:186 estimate_cost()` |
| 用量记账 | `backend/app/ai/explain.py:171 record_usage()` → `ai_usage`（按 `usage_date, provider_id, model_id, task_type` 唯一，`backend/app/domain/models.py:689-691`） |

### 1.3 Prompt / 缓存 / 审计（计划 §三十六、§三十七、§三十八、§三十九）

| 事实 | 位置 |
| --- | --- |
| DB 提示词表存在 | `backend/app/domain/models.py:635-648 ai_prompts`（name / version / task_type / system_prompt / user_template / output_schema_json / capability_tier / is_active，唯一 `uq_ai_prompt(name, version)`） |
| 唯一的注册入口（幂等种子） | `backend/app/api/routers/ai.py:320-355 _seed_builtin_prompts()`，两条：`("signal_explain","1.0.0",…)`、`("backtest_explain","1.0.0",…)` |
| 读端点 | `backend/app/api/routers/ai.py:358-376 GET /ai/prompts` |
| 解释链**不读**该表，版本硬编码 | `backend/app/ai/explain.py:291`、`:323` |
| 缓存 = `ai_tasks.input_hash` 复用（无 Redis、无 TTL） | 写入 `backend/app/domain/models.py:660 input_hash`；命中查询 `backend/app/ai/explain.py:397-412`（`status == "completed"`）；返回体含 `cached: True`、`cost_usd_estimated: 0.0` |
| 审计表 | `backend/app/domain/models.py:651-672 ai_tasks`（task_type / provider_id / model_id / prompt_name / prompt_version / input_hash / input_json / output_json / token_usage_json / cost_usd / status / error_message / created_at / completed_at，索引 `ix_ai_tasks_type_status`） |
| 任务类型 / 状态枚举 | `backend/app/domain/enums.py:108-115 AITaskType`（含 `daily_summary / strategy_review / research_report / repository_analysis`，**均无实现**）、`:118-123 AITaskStatus`（pending / running / completed / failed / skipped） |
| provider 层已加密存储密钥 | `backend/app/domain/models.py:606 api_key_encrypted`；`backend/app/infrastructure/secrets.py` |
| 迁移 | `backend/alembic/versions/0001_initial_schema.py`（建 ai_* 表，`ai_tasks` 在 `:261-281`）、`backend/alembic/versions/0009_drop_dead_schema.py`（删除无人读的 `ai_models.context_length` / `supports_structured_output`） |

### 1.4 API 面（计划 §五十三–§五十六）

| 端点 | 位置 |
| --- | --- |
| `GET /api/v1/ai/status` | `backend/app/api/routers/ai.py:39-70` |
| `POST /api/v1/signals/{signal_id}/explain` | `backend/app/api/routers/ai.py:73-92`（错误映射：`LookupError→404`、`AI_UNCONFIGURED→503`、`BudgetExceeded→429`、`RuntimeError→502`） |
| `POST /api/v1/signals/preview-explain` | `backend/app/api/routers/ai.py:95-160` |
| `POST /api/v1/backtests/{run_id}/explain` | `backend/app/api/routers/ai.py:163-184` |
| `GET /api/v1/ai/tasks`、`/ai/tasks/{id}/status`、`/ai/tasks/{id}` | `backend/app/api/routers/ai.py:187-198`、`:201-215`、`:218-240`（`/status` 是 v1.1.0 为长任务加的轮询面，**AI Research 异步编排可直接复用这个模式**） |
| `GET /api/v1/ai/models`、`/ai/usage-today`、`/ai/usage`、`/ai/prompts` | `backend/app/api/routers/ai.py:262-281`、`:243-259`、`:284-317`、`:358-376` |
| provider CRUD 与连通性测试 | `backend/app/api/routers/settings.py:99,112,158,202,223,244`（审计事件 `ai_provider_created/updated/deleted`） |
| 量化研究（**不是** AI 研究） | `backend/app/api/routers/research.py:45 walk-forward`、`:85 oos`、`:129 sensitivity`、`:188 monte_carlo`、`:266 ensemble`、`:323 ensemble_sweep` |

### 1.5 前端面（计划 §四十、§四十一、§二十）

| 事实 | 位置 |
| --- | --- |
| 10 条路由，`/research` 是普通用户向导 | `frontend/src/main.ts:16-35`（`/research` 在 `:20`）；`/market` 重定向 `:31`；`/strategy/:strategyId` `:34` |
| 模式开关 | `frontend/src/mode.ts:13 UiMode`、`:15 STORAGE_KEY = 'mql-mode'`、`:28 mode`、`:31 isAdvanced` |
| AI 客户端方法 | `frontend/src/api.ts:1039-1083`（`aiStatus/explainSignalPreview/explainSignal/explainBacktest/aiModels/aiPrompts/aiTasksList/aiTask/aiUsage/aiProviders/testProvider/...`） |
| AI 面板所在视图 | `frontend/src/views/DashboardView.vue:631-646`、`frontend/src/views/SignalsView.vue:440-441,629`、`frontend/src/views/StrategyDetailView.vue:580-596`、`frontend/src/views/BacktestView.vue:2046-2094` |
| 只有设置页做了高级模式门控 | `frontend/src/views/SettingsView.vue:723`（`v-if="!isAdvanced"` 的人话说明）vs `:727`（`<template v-if="isAdvanced">` 包住模型目录 `:729`、提示词 `:753`、用量 `:778`、任务记录 `:805`、任务详情 `:840`） |

### 1.6 策略 DSL / 引擎 / 指标（计划 §七 Layer C、§八、§十一、§十四、§四十五）

| 事实 | 位置 |
| --- | --- |
| DSL 文档类（引擎契约） | `backend/app/strategies/dsl.py:255 StrategySpec`，`extra="forbid"` `:258`，`SCHEMA_VERSION = "1.0"` `:37` |
| 算子集合（8 个） | `backend/app/strategies/dsl.py:63 ComparisonOp`（gt/gte/lt/lte/eq/ne/crosses_above/crosses_below） |
| 成交模型（2 个） | `backend/app/strategies/dsl.py:64 FillModel`（next_bar_open / close_bar） |
| 订单类型 | `backend/app/strategies/dsl.py:186 OrderType`（market/limit/stop） |
| 仓位模式（3 个） | `backend/app/strategies/dsl.py:188 SizingMode`（fixed_fraction/risk_per_trade/atr_risk） |
| 风险模型 | `backend/app/strategies/dsl.py:139-141`（stop_loss_atr_multiple / take_profit_r_multiple / take_profit_atr_multiple）+ `:142 max_position_pct` |
| 市场与周期（自由字符串，非枚举） | `backend/app/strategies/dsl.py:237-242 MarketSpec`（默认 `["stock"]` / `["1d"]` / `allow_short=False`） |
| 文档化的能力边界（含"暂不支持"） | `docs/04_STRATEGY_DSL.md:130-137`：指标 EMA/SMA/RSI/ATR/MACD/Bollinger；8 算子；别名 `previous_high→prior_high`、`previous_low→prior_low`、`rolling_*_prev`、`volume_sma_20`；**`filters` / `outputs` 顶级块与未注册列（如 `stop_price`）会被拒绝** |
| 引擎入口 | `backend/app/research/engine.py:179 run_backtest(`、`backend/app/research/walk_forward.py:49 run_walk_forward(`、`backend/app/research/sensitivity.py:86 run_sensitivity(`、`backend/app/research/monte_carlo.py:99 run_monte_carlo(`、`backend/app/research/ensemble.py:720 run_ensemble(` / `:756 run_ensemble_sweep(` |
| 校验器入口 | `backend/app/strategies/validator.py:154 validate_strategy(` |
| DSL 解析/装载 | `backend/app/data/strategy_service.py:46 parse_spec(`、`:67 load_spec(` |
| 指标现状 | `backend/app/research/metrics.py:40-51`（sortino `:40`、max_drawdown_duration_bars `:42`、profit_factor `:47`、expectancy `:48`、exposure `:50`、turnover `:51`）；`backend/app/research/engine.py:521-522` 赋值；`backend/app/api/routers/backtests.py:249-261` 指标分组 |
| **不存在** | `calmar`、`recovery_factor`、`VaR`、`CVaR`（全仓 grep 零命中）；portfolio / universe / 横截面 ranking / rebalance（DSL 与引擎都没有） |
| 回测是单标的 | 请求模型为 `{strategy_version_id, symbol, timeframe, execution_overrides}`；`backend/app/research/ensemble.py:518` 取 frame 的 symbol，说明 ensemble 组合的是策略而非标的 |
| 版本不可变 | `backend/app/domain/immutability.py`；迁移 `backend/alembic/versions/0002_immutability.py`、`0010_immutability.py`；`backend/app/domain/models.py:214-245 StrategyVersion`（含 `:228 prompt_version`、`:229 evidence_json`） |
| 血统端点 | `backend/app/api/routers/strategies.py:274-301`（`GET /strategies/{strategy_id}/lineage`） |

### 1.7 研究源（计划 §二十一、§二十二、§二十三、§五十）

| 事实 | 位置 |
| --- | --- |
| 只有 GitHub 一条链 | `backend/app/data/github_source_service.py`、`backend/app/importer/github_client.py`、`backend/app/importer/extract.py`、`backend/app/api/routers/importer.py` |
| 表 | `backend/app/domain/models.py:731-753 github_sources`、`:754-776 github_snapshots`（commit 固化） |
| 静态分析与净化 | `backend/app/importer/sanitize.py`、`backend/app/importer/extract.py:80 UNSAFE_ATTRS`（`system/popen/exec/spawn/get/post/request/urlopen` 黑名单）、`backend/app/importer/extract.py:106-135 Evidence` dataclass |
| DSL 构建 | `backend/app/importer/dsl_builder.py`（`:5` 注释提 confidence、`:191` 提 provenance） |
| **不存在** | `ResearchSource` 通用抽象、URL 摄取、PDF 摄取、`experiment` 实体（`backend/app/domain/models.py` 全部 31 张表里没有） |

### 1.8 测试与护栏（计划 §六十二、§六十三）

| 事实 | 位置 |
| --- | --- |
| AI 测试 3 个文件，共 33 条 | `backend/tests/test_ai_explain.py`（12）、`backend/tests/test_ai_providers.py`（15）、`backend/tests/test_ai_router.py`（6） |
| 全部用 FakeRouter 注入，不触网 | `backend/tests/test_ai_explain.py:57-68 FakeRouter`、`:71-77 make_factory` |
| "AI 不得计算"护栏原文 | `backend/tests/test_ai_explain.py:295 test_build_signal_facts_contains_no_computed_stats`，断言 `assert "win_rate" not in facts and "sharpe" not in facts`（`:304`）；`:278-281` 断言喂给模型的是库里原值 |
| 预算护栏 | `backend/tests/test_ai_explain.py:132 test_budget_exceeded_blocks_call`（`with pytest.raises(BudgetExceeded)`、`assert calls == []` `:137-139`） |
| 缓存护栏 | `backend/tests/test_ai_explain.py:115 test_explain_uses_cache_on_second_call` |
| 密钥永不外泄 | `backend/tests/test_ai_providers.py:38/:55/:156` |
| 前端契约护栏（先例） | `backend/tests/test_frontend_contracts.py:519-541`（`assert "AI 汇总（只解释已有数字，不重新计算）" in BACKTEST`）、`:960-970`；`backend/tests/test_ui_promises.py:408-473`（`DETAIL_SECTIONS` 含 `"AI 解释（AI Explanation）"` `:417`） |
| 死配置守卫（先例） | `backend/tests/test_no_dead_settings.py:117-133`（`ADR-084`：没有读取方的键不许复活） |
| 整仓规模与时长 | v1.9.6：955 passed, 4 skipped（`scripts/Invoke-Tests.ps1`，约 180s）；`ruff` 全过；前端 `FRONTEND OK` |

### 1.9 版本与发布规则（计划 §八十）

| 事实 | 位置 |
| --- | --- |
| 版本号唯一事实来源 | `version.txt`（当前 `v1.9.6`）；`scripts/version.sh` |
| **没有 v1.10.x** | `scripts/version.sh` 在小版本 > 9 时 `exit 2`；ADR-079：v1.9.9 → v2.0.0 |
| 只有 `X.Y.0` 部署 NAS | ADR-086；补丁版只 commit + tag + push |
| 标签即发布 | `.github/workflows/release.yml` 触发条件 `on: push: tags: ['v*']` → 任何 tag 都会推 GHCR 镜像 |

---

## 2. Reusable Capability（可直接复用，不建议重写）

| 复用项 | 位置 | 计划章节 | 复用方式 |
| --- | --- | --- | --- |
| Provider Protocol 与唯一适配器 | `backend/app/domain/protocols.py:40-65`、`backend/app/ai/provider.py:92` | §三、§三十五 | 就地扩展（加 `tools` 参数 / 结构化输出重试），不新建抽象层 |
| 分层路由与成本模型 | `backend/app/ai/provider.py:211-309` | §三十一–§三十四 | `task_type → tier` 升级为 `role → required_capabilities`，保留 `ModelOption` 成本排序 |
| 每日预算 + `BudgetExceeded → 429` | `backend/app/domain/models.py:609`、`backend/app/ai/explain.py:358-419`、`backend/app/api/routers/ai.py:73-92` | §六十五 | 在既有异常链上加"任务类别"与"研究轮次"配额 |
| `ai_tasks` + `/ai/tasks/{id}/status` 轮询模式 | `backend/app/domain/models.py:651-672`、`backend/app/api/routers/ai.py:201-215` | §五十三、§五十九、§六十 | AI Research 异步编排直接沿用；`ai_tasks` 加列而不换表 |
| 缓存查询（哈希复用） | `backend/app/ai/explain.py:397-412` | §三十八 | 保留机制，**修正 key**（见 §4.1） |
| 事实组装（只读、去数值黑名单） | `backend/app/ai/explain.py:203-250` | §二十九、§三十 | 作为所有 Analyst/Explainer 的输入构造范式 |
| 输出 schema 校验 | `backend/app/ai/provider.py:163-183` | §六十四 | 扩展到新角色 schema |
| 密钥加密存储 | `backend/app/domain/models.py:606`、`backend/app/infrastructure/secrets.py` | §十八 | 审计沿用"不落密钥" |
| GitHub 快照 / 净化 / 静态分析 | `backend/app/domain/models.py:754-776`、`backend/app/importer/{sanitize,extract,github_client}.py` | §二十一、§二十二、§五十 | 抽 `ResearchSource` 时把 GitHub 作为**第一个实现**，不改造其安全约束 |
| 现有引擎入口（回测/研究/指标） | `backend/app/research/{engine,metrics,walk_forward,sensitivity,monte_carlo,ensemble}.py` | §二十四、§五十五 | Tool Gateway 只做**包装与配额**，不改计算 |
| 校验器 | `backend/app/strategies/validator.py:154` | §十七、§七十七 H | 编译产物一律过它 |
| 版本不可变与审计 | `backend/app/domain/immutability.py`、`backend/app/domain/models.py:697-716 AuditLog` | §四十二 | 研究层所有写操作走"人工批准 + 既有服务" |
| 前端模式开关与契约测试 | `frontend/src/mode.ts`、`backend/tests/test_frontend_contracts.py` | §二十、§六十九 | 新专业词一律进高级模式，并用同样的读源码断言守卫 |
| 资源监控与限流 | `backend/app/infrastructure/resource_monitor.py`、`backend/app/infrastructure/rate_limit.py` | §六十六 | 重任务配额与资源上限联动 |

---

## 3. Extension Required（需扩展，按版本）

| 扩展项 | 现状 `path:line` | 扩展内容 | 计划章节 | 版本 |
| --- | --- | --- | --- | --- |
| Role Contract 运行时 | 无（只有 2 段硬编码常量 `backend/app/ai/explain.py:75-90`） | `backend/app/ai/contracts/*.md` + 加载器 + `required_capabilities` 解析 + 内容 hash | §四、§五、§三十二、§三十六 | v1.9.7 |
| 提示词单一来源 | DB 表 `backend/app/domain/models.py:635-648`、种子 `backend/app/api/routers/ai.py:320-355`、硬编码 `backend/app/ai/explain.py:291,323` | markdown 为唯一人工源 → 运行时 registry → 同步 `ai_prompts`（保留审计与缓存键） | §十六 | v1.9.7 |
| 缓存 key | `backend/app/ai/provider.py:79-89` | 加入 provider / model / role / prompt_hash / tool_result_hash / source_snapshot_hash / strategy_version | §十七、§三十八 | v1.9.7 |
| 审计字段 | `backend/app/domain/models.py:651-672` | 加 role / output_hash / source_ids / strategy_version_id / research_run_id；新增 `ai_tool_calls` | §十八、§三十七 | v1.9.7 / v1.9.9 |
| 预算分层 | `backend/app/domain/models.py:609`、`backend/app/core/config.py:147` | CRITICAL/IMPORTANT/OPTIONAL 常量 + 每次研究配额 + 单一来源；单位统一（见 §20 Q1） | §六十五 | v1.9.7 |
| 结构化输出泛化 | `backend/app/ai/provider.py:37-48`、`backend/app/ai/explain.py:57-73` | 每个 Role 的输出 schema + 数值字段禁令 | §六十四、§七十七 | v1.9.7 |
| Capability Registry | 无 | 由代码/DSL 生成 + drift test + `SUPPORTED/PARTIALLY_SUPPORTED/UNSUPPORTED` 判定 | §十九、§二十、§五十一、§五十二 | v1.9.7（骨架）/ v1.9.8（完整） |
| Research Source 抽象 | 仅 GitHub（见 §1.7） | `ResearchArtifact` + 文本 / URL / PDF / GitHub 四种实现 | §七、§二十三、§五十 | v1.9.8 |
| Strategy Hypothesis / Draft | 无 | 三态规则、证据绑定、能力缺口、待确认项 | §七、§九、§十、§十一、§十二 | v1.9.8 |
| Strategy Compiler | 无（只有 `backend/app/importer/dsl_builder.py` 的确定性构建） | `StrategyDraft → StrategySpec 1.0` + 过 validator；禁止 AI→Python | §十七、§六十一 | v1.9.9 |
| Tool Gateway | 无（全仓无 tool calling） | 读/重/写三档 + 配额 + 超时 + 审计 | §二十四、§二十五、§五十七、§五十八 | v1.9.9 |
| 异步研究工作流 | 有轮询模式可复用（`backend/app/api/routers/ai.py:201-215`） | Celery 阶段状态机 + 人工确认暂停 | §五十九、§六十、§十二（用户决策 C10） | v1.9.9 |
| 实验实体 | 无 | `strategy_experiments` + V1→V2 不可变迭代 | §十三、§四十三、§四十二 | v1.9.9 |
| 前端 `/lab` | 无（`frontend/src/main.ts:16-35`） | 研究台（分阶段状态、资料、假设、草案、确认、结论） | §四十、§四十一、§六十九、§七十 | v1.9.9–v2.0.0 |
| 新指标 | `backend/app/research/metrics.py:40-51` | Calmar / Recovery / VaR / CVaR（**先定义后实现**） | §十四、§二十七、§十五 | Future |

---

## 4. Refactor Required（需重构，含风险）

### 4.1 缓存 key 必须区分模型与角色（计划 §十七、§三十八）
现状：`backend/app/ai/provider.py:79-89` 的 `input_hash` 只由 task_type + `name@version` + facts 组成 → **换模型/换 provider 会命中旧结果**。
重构：key = `sha256(provider, model, role, prompt_hash, tool_result_hash, source_snapshot_hash, strategy_version, structured_input_hash)`。
风险：缓存全部失效一次（可接受）；`ai_tasks.input_hash` 是 `String(64)`（`backend/app/domain/models.py:660`），长度不变。
测试：同 facts 不同 model 必须 miss（先例风格 `backend/tests/test_ai_explain.py:115`）。

### 4.2 Prompt 三源合一（计划 §十六）
现状三处：markdown（待建）、`ai_prompts`（`backend/app/domain/models.py:635-648`）、硬编码（`backend/app/ai/explain.py:75-90`）。
重构：契约文件是唯一人工维护源；启动/迁移时导入 `ai_prompts` 作为索引；硬编码常量删除（或仅作 fallback 并由测试禁止分叉）。
风险：`_seed_builtin_prompts`（`backend/app/api/routers/ai.py:320-355`）与 `GET /ai/prompts` 的行为要一起改；`backend/tests/test_feature_metrics_api.py:8` 依赖该注册表。
测试：契约文件 hash 变化必须改变 prompt 版本/缓存键；测试断言"代码里不存在第二份 prompt 正文"。

### 4.3 预算单一来源 + 任务分层（计划 §六十五）
现状：`ai_providers.daily_budget_usd`（生效）、`Settings.ai_daily_budget_usd`（无读取方，`backend/app/core/config.py:147`）、文档 ¥2/5/10（`docs/06_AI_LAYER.md:126-131`）。
重构：确定唯一来源（建议：provider 行 + 全局默认值双读，全局键要么接线要么按 ADR-084 删除）；加任务类别上限与单次研究上限。
风险：`backend/tests/test_no_dead_settings.py:121-133` 的纪律——**不能留下第二个无读取方的键**。

### 4.4 `explain.py` 拆分（计划 §十五–§二十八、§四十九）
现状：`backend/app/ai/explain.py` 486 行承担 provider 选择、预算、事实组装、缓存、记账、两条解释链。
重构：拆为 `runtime/`（选择/预算/缓存/记账/审计）、`roles/`（各 Role）、`facts/`（事实组装）。对外函数名（`explain_signal` / `explain_backtest` / `explain_signal_facts`）保留，避免破坏既有调用方与测试。

### 4.5 前端 AI 面板门控（计划 §二十、§六十九）
现状：Dashboard / Signals / StrategyDetail / Backtest 的 AI 面板不受 `isAdvanced` 门控（见 §1.5）。
重构：**先分类**——现有"解释"是给普通用户的人话（应保持可见），新专业信息（StrategySpec / prompt_version / tool_trace / source_hash / Capability Registry）才进高级模式。见 §20 Q3。

### 4.6 领域校验与能力校验分层（计划 §六十四、§七十七 G/H）
现状：只有 `validate_strategy`（`backend/app/strategies/validator.py:154`）与 JSON schema 校验（`backend/app/ai/provider.py:163-183`）。
重构：三级校验链 = JSON schema → 领域校验 → 能力校验（Capability Registry），并禁止 AI 输出里出现引擎指标字段名（沿用 `backend/tests/test_ai_explain.py:304` 的断言风格）。

---

## 5. Architecture Conflict（架构冲突与裁决）

| # | 冲突 | 事实 `path:line` | 计划章节 | 裁决 |
| --- | --- | --- | --- | --- |
| 5.1 | `StrategySpec` 被计划当作"AI 研究信封"，实际是引擎 DSL 类且 `extra="forbid"` | `backend/app/strategies/dsl.py:255,258`；104 处引用 | §八、§七 Layer C | **C1 = A 方案**：DSL 保持 1.0 不动；研究层新增 `ResearchArtifact / StrategyHypothesis / StrategyDraft / StrategyCompiler` |
| 5.2 | `filters` / `outputs` 顶级块被 DSL 明确拒绝 | `docs/04_STRATEGY_DSL.md:130-137` | §八（列出 `filters`） | 研究层可表达，编译**不得**产出这些块；registry 标为 unsupported |
| 5.3 | 计划 §十一 的"动量 + 横截面排名 + 每月调仓"在当前引擎不可执行 | 无 universe/portfolio/ranking/rebalance（§1.6） | §十一、§四十五–§四十七 | 输出 `PARTIALLY_SUPPORTED` + 明确列 unsupported（计划 §十三 新原则）；如提替代实验必须标 `Experimental` |
| 5.4 | 计划 §四十九 说保留"5 个现有角色" | 实测 2 条链（`backend/app/ai/explain.py:291,323`）；R1/R4/R5 只有枚举与文档（`backend/app/domain/enums.py:109`、`docs/06_AI_LAYER.md:30-43`） | §四十九 | 按**新建**估工；`daily_summary` 等枚举值在没有实现前不得对外承诺 |
| 5.5 | 计划点名 `generate_structured()` / `generate_text()` | 全仓零命中；真实契约是 Protocol（`backend/app/domain/protocols.py:40-65`） | §三、§六十四 | 扩展 Protocol（加 `tools`），不新增同义 API |
| 5.6 | `ai_models.capability_tier` 只是单个字符串 | `backend/app/domain/models.py:625`、`backend/app/ai/provider.py:211` | §三十一（6 项能力） | 用 `settings_json`（`backend/app/domain/models.py:610`）或加列；能力由**用户手填**，不做自动探测（避免额外请求与成本） |
| 5.7 | 前端 `/research` 与后端 `/api/v1/research/*` 已被占用 | `frontend/src/main.ts:20`；`backend/app/api/routers/research.py:45,85,129,188,266,323` | §四十、§五十三 | **C9**：研究台 `/lab`，API `/api/v1/ai/research*`；既有两处不动 |
| 5.8 | 计划的 Strategy Detail 多分区 vs 既有九段与 ADR-114 | `frontend/src/views/StrategyDetailView.vue`、ADR-114、`docs/13_UI_UX.md` §3/§10 | §四十一 | 不重做九段；新增"AI 研究"分区，专业项进高级模式 |
| 5.9 | 全局 AI 预算键无读取方（与 ADR-084 纪律冲突） | `backend/app/core/config.py:147`；`backend/tests/test_no_dead_settings.py:121-133` | §六十五 | 接线或删除，二选一（见 §20 Q1） |
| 5.10 | 计划 §三十一 的 vision | 无任何图片链路 | §三十一 | **C5**：本期不做，`source type` 预留 `image` |
| 5.11 | 计划 §三十八 的 RAG 与向量库 | 无 | §六十七 | 第一阶段不做（计划自己也建议不做） |

---

## 6. New Database Models（建议新增，v1.9.8 设计定稿）

> 命名遵循既有风格（`snake_case` 表名、`TimestampMixin`、`_now` 默认值，见 `backend/app/domain/models.py:65-80`）。
> 所有新表只**新增**，不改动既有表的结构（除 §7 列出的可空加列）。

| 模型 | 用途 | 关键字段（草案） | 计划章节 | 版本 |
| --- | --- | --- | --- | --- |
| `ai_role_contracts` | 角色契约的运行时索引（源仍是 `backend/app/ai/contracts/*.md`） | `name`, `version`, `content_hash`, `required_capabilities_json`, `output_schema_json`, `is_active` | §五、§十六、§三十二 | v1.9.7 |
| `ai_research_runs` | 一次研究（多阶段、可暂停、可追溯） | `id`, `status`, `current_step`, `quota_json`, `created_at`, `completed_at` | §十二、§三十九、§五十九、§六十 | v1.9.9（表先建于 v1.9.8 亦可） |
| `research_artifacts` | 原始研究材料（Layer A） | `source_type`(text/url/github/pdf/image 预留), `uri`, `snapshot_hash`, `fetched_at`, `parse_status`(ok/UNSUPPORTED/PARSE_FAILED), `parse_error`, `size_bytes`, `license_note` | §七 Layer A、§二十三、§八十二 Scenario 2/4 | v1.9.8 |
| `research_artifact_fragments` | 引用片段（证据绑定的落点） | `artifact_id`, `locator_json`(file/line/page/char-range), `text_excerpt`(限长), `fragment_hash` | §九、§三十七、§十八 | v1.9.8 |
| `strategy_hypotheses` | AI 对材料的理解（Layer B） | `hypothesis_json`, `status`, `provider/model/role/prompt_version`, `confidence_self_reported`, `ai_task_id` | §七 Layer B、§十、§十四 | v1.9.8 |
| `strategy_hypothesis_rules` | 逐条规则的来源与能力状态 | `hypothesis_id`, `rule_key`, `statement`, `origin`(EXPLICIT/INFERRED/ASSUMED/UNKNOWN), `confidence`, `capability_status`, `evidence_fragment_ids` | §九、§十、§十八、§十三 | v1.9.8 |
| `strategy_drafts` | 可编译但不可执行的中间结构（Layer C 之前） | `draft_json`, `capability_report_json`, `status`(DRAFT/NEEDS_INPUT/NEEDS_REVIEW/READY/REJECTED), `is_experimental_of`(AI 替代实验标记), `compiled_strategy_version_id` | §八、§十一、§十二、§六十 | v1.9.8 |
| `strategy_experiments` | V1 → 实验 → V2 的迭代记录 | `base_strategy_version_id`, `hypothesis`, `change_json`, `produced_strategy_version_id`, `status` | §十三、§四十三、§四十二 | v1.9.9 |
| `ai_tool_calls` | 工具调用审计 | `ai_task_id`, `research_run_id`, `tool_name`, `args_hash`, `args_json`(脱敏), `result_hash`, `status`, `duration_ms`, `error_message` | §二十四、§五十七、§五十八、§十八 | v1.9.9 |
| `ai_source_snapshots` | URL/PDF 抓取快照（对标 `github_snapshots`） | `url`, `final_url`, `status_code`, `content_type`, `content_hash`, `bytes`, `fetched_at`, `robots_ok`, `stored_ref` | §二十三、§十九 | v1.9.8 |

**明确不新增**：Capability Registry **不建表**（用户决策：必须由代码/DSL 生成或校验，见计划 §五）。

---

## 7. Required Migrations

| 迁移（建议 revision） | 内容 | 版本 | 约束 |
| --- | --- | --- | --- |
| `0012_ai_role_contracts` | 新建 `ai_role_contracts`；`ai_tasks` 加可空列 `role` / `output_hash` / `research_run_id` / `strategy_version_id` / `source_ids_json` | v1.9.7 | 只加列，不删不改既有列（先例：`backend/alembic/versions/0006_resource_pk_sqlite.py` 的 SQLite 兼容处理） |
| `0013_research_layer` | 新建 `research_artifacts` / `research_artifact_fragments` / `strategy_hypotheses` / `strategy_hypothesis_rules` / `strategy_drafts` / `ai_source_snapshots` | v1.9.8 | 新增表，`down_revision = "0012_ai_role_contracts"` |
| `0014_ai_workflow_audit` | 新建 `ai_research_runs` / `ai_tool_calls` / `strategy_experiments`；`ai_usage` 加可空 `research_run_id` | v1.9.9 | 同上 |
| （条件）`0015_metrics_*` | Calmar / Recovery / VaR / CVaR 若需新列（例如 `backtest_metrics` 扩展） | Future | **必须先有指标定义与测试规范**（计划 §十五） |

通用要求：
- 迁移链现为 `0001_initial → 0002_immutability → 0003_fix_triggers_json → 0004_resource_monitor → 0005_signal_outcome_times → 0006_resource_pk_sqlite → 0007_backtest_result_warnings → 0008_github_pending_review → 0009_drop_dead_schema → 0010_immutability → 0011_signal_closes_direction`（`backend/alembic/versions/`）。
- 必须同时支持 SQLite（本地验证库 `backend/.local-verify.sqlite3`）与 PostgreSQL 16；必须可 `downgrade`。
- 不得触碰不可变守卫：`backend/app/domain/immutability.py`、`0002_immutability`、`0010_immutability`。

---

## 8. Required API

命名空间：`/api/v1/ai/*`（前缀见 `backend/app/core/config.py:81`，挂载见 `backend/app/api/main.py:270`）。

| 方法 / 路径 | 用途 | 计划章节 | 版本 | 备注 |
| --- | --- | --- | --- | --- |
| `GET /ai/capabilities` | Capability Registry（含 `SUPPORTED / PARTIALLY_SUPPORTED / UNSUPPORTED` 与缺失项） | §十九、§五十一、§五十二 | v1.9.7 | 只读；由代码生成 |
| `GET /ai/roles` | 角色契约列表 + `required_capabilities` + 当前是否满足 | §五、§三十二 | v1.9.7 | 只读 |
| `GET /ai/audit/{task_id}` | provider/model/role/prompt_version/input_hash/output_hash/source_ids/tool_calls | §十八、§三十七 | v1.9.7 | 不返回密钥；不返回 source 全文 |
| `POST /ai/research` | 创建研究（异步，返回 run id） | §五十三（用户决策 C10、§十二） | v1.9.9 | 复用 `ai_tasks` + `/ai/tasks/{id}/status` 模式 |
| `GET /ai/research/{run_id}` | 阶段状态（Researching → … → Completed） | §十二、§三十九 | v1.9.9 | 轮询 |
| `GET /ai/research/{run_id}/artifacts`、`/hypothesis`、`/draft` | 分层结果 | §七、§三十九 | v1.9.9 | 普通模式给摘要，高级模式给原始 |
| `POST /ai/research/{run_id}/confirm` | 人工确认假设 / 歧义选项 | §十二、§六十 | v1.9.9 | 唯一的"模型不能自己跨过"的点 |
| `POST /ai/sources/text`、`/ai/sources/url`、`/ai/sources/pdf` | 资料摄取（GitHub 走既有 importer） | §二十三、§十九 | v1.9.8 | URL/PDF 受 SSRF/大小/超时约束 |
| `POST /ai/strategy/formalize` | 思想/资料 → `StrategyDraft` | §五十四 | v1.9.8 | 不产出可直接执行的 DSL |
| `POST /ai/strategy/drafts/{id}/compile` | Draft → DSL 1.0（过 validator）→ 创建新版本 | §十七、§四十二 | v1.9.9 | **需人工批准**；写库走既有 `strategy_service` |
| `POST /ai/backtests/{run_id}/analyze` | Backtest Analyst（在既有 explain 之上扩展） | §二十六、§五十五 | v1.9.9 | 数字只能来自已存结果 |
| `POST /ai/explain` | 统一解释入口（signal / strategy / backtest / risk / paper / research） | §五十六、§二十八 | v1.9.9 | 既有 3 个端点保留兼容（`backend/app/api/routers/ai.py:73,95,163`） |
| `POST /ai/experiments` | 创建实验（V1 → V2） | §十三、§四十三 | v1.9.9 | 写库需人工批准 |

**明确不改动**：`/api/v1/research/*`（量化研究）、前端 `/research`、既有 AI 解释端点（向后兼容）。

---

## 9. Required AI Runtime

| 组件 | 职责 | 现状落点 | 计划章节 |
| --- | --- | --- | --- |
| Contract Loader | 读 `backend/app/ai/contracts/*.md`，解析 `required_capabilities`、输出 schema、版本；算 `content_hash` | 新建（现无 `contracts/`） | §五、§十六 |
| Contract Registry | 契约 ↔ `ai_role_contracts` ↔ 缓存键三方一致 | 新建（现只有 `_seed_builtin_prompts` `backend/app/api/routers/ai.py:320-355`） | §十六、§三十六 |
| Request Assembler | 组装顺序 **System Contract → Role Contract → Task → UNTRUSTED SOURCE**，源单独消息并加显式标记 | 新建（现为 `AIRequest` 单段 `system_prompt` + `user_prompt`，`backend/app/ai/provider.py:64-89`） | §六、§六十三、§十九 |
| Capability Guard | 调用模型前先判定能力，不支持即返回 `UNSUPPORTED`，禁止静默降级 | 新建 | §十八、§二十、§五十一、§五十二 |
| Output Validator | schema → 领域 → 能力 → 安全（数字护栏）四级 | 现有两级：`backend/app/ai/provider.py:163-183`、`backend/app/strategies/validator.py:154` | §六十四、§七十七 G/H |
| Tool Gateway Client | 读工具直通；重任务生成 proposal → 人工确认 → Celery；**无写工具** | 新建 | §二十四、§二十五、§五十七、§五十八 |
| Audit Writer | `ai_tasks` 扩展列 + `ai_tool_calls`；只存 hash/引用，不存密钥与全文 | 现有 `backend/app/ai/explain.py:171 record_usage()`、`ai_tasks` | §十八、§三十七 |
| Cache | key 修正；错误复用必须杜绝；TTL 本期可评估不做 | 现有 `backend/app/ai/explain.py:397-412` | §十七、§三十八 |
| Budget / Quota | 任务类别 + 单次研究 + 每日三层；耗尽抛既有 `BudgetExceeded` → 429 | 现有 provider 级（`backend/app/domain/models.py:609`、`backend/app/ai/explain.py:414-419`） | §六十五 |
| Orchestrator | Celery 阶段状态机 + 可暂停（NEEDS_INPUT / NEEDS_REVIEW） | 现有轮询模式可复用（`backend/app/api/routers/ai.py:201-215`） | §五十九、§六十 |
| Language Policy | 输出语言与契约语言分开定义（中文输出为现状，`backend/tests/test_frontend_contracts.py:960`） | 开放问题见 §20 Q1 | §二十、§六十九 |

---

## 10. Required Role Contracts

| 契约文件（`backend/app/ai/contracts/`） | 角色 | required_capabilities（草案） | 输出（草案） | 计划章节 | 版本 |
| --- | --- | --- | --- | --- | --- |
| `SYSTEM.md` | System Contract（18 条铁律） | — | —（拼进每次请求的最前） | §六、§七十九 | v1.9.7 |
| `RESEARCHER.md` | Strategy Researcher | `source_reading`, `market_data_read`, `strategy_read`, `long_context` | hypothesis + rules(三态) + evidence + unknowns | §十五、§三十二 | v1.9.7 |
| `STRATEGY_ARCHITECT.md` | Strategy Architect | `dsl_capabilities`, `capability_registry_read`, `structured_output` | `StrategyDraft` + capability report | §十六 | v1.9.7 |
| `STRATEGY_COMPILER.md` | Strategy Compiler | `dsl_capabilities`, `indicators`, `operators`, `fill_models`, `sizing_modes`, `structured_output` | DSL 1.0 文档（过 validator） | §十七、§三十二 | v1.9.9 |
| `BACKTEST_ANALYST.md` | Backtest Analyst | `backtest_result_read`, `risk_metrics`, `sensitivity`, `monte_carlo` | 专业分析 + 通俗解释 + 风险 + 下一步 | §二十六、§二十九 | v1.9.9 |
| `RISK_ANALYST.md` | Risk Analyst | `risk_metrics`, `monte_carlo`, `position_read` | 风险结论 + 要盯什么 | §二十七 | v1.9.9 |
| `EXPLAINER.md` | Explainer（统一解释；覆盖现有两条链） | `structured_output` | 发生了什么 / 为什么 / 风险 / 下一步 + 免责 | §二十八、§三十 | v1.9.7（先承接现有 2 条链） |
| `REVIEWER.md` | Reviewer（契约合规自检） | `structured_output`, `capability_registry_read` | pass / needs_revision + findings | §四、§五 | v1.9.9 |

现状映射：现有 `SIGNAL_SYSTEM_PROMPT`（`backend/app/ai/explain.py:75-82`）与 `BACKTEST_SYSTEM_PROMPT`（`:84-90`）分别迁入 `EXPLAINER.md` 的信号/回测小节；`prompt_name` 保持 `signal_explain` / `backtest_explain` 以免破坏历史审计（`backend/app/domain/models.py:658-659`）。

---

## 11. Required Capability Registry

### 11.1 覆盖维度与**代码事实来源**（这是防漂移的关键）

| 维度 | 事实来源 `path:line` | 状态 |
| --- | --- | --- |
| Operators | `backend/app/strategies/dsl.py:63 ComparisonOp` | 已存在 |
| Fill Models | `backend/app/strategies/dsl.py:64 FillModel` | 已存在 |
| Order Types | `backend/app/strategies/dsl.py:186 OrderType` | 已存在 |
| Sizing Modes | `backend/app/strategies/dsl.py:188 SizingMode` | 已存在 |
| Risk Models | `backend/app/strategies/dsl.py:139-142` | 已存在 |
| Indicators | `docs/04_STRATEGY_DSL.md:132` 与特征引擎实现（v1.9.7 需定位精确注册点：`backend/app/features/engine.py`） | 已存在（**注册点待钉死**） |
| Asset Classes / Timeframes | `backend/app/strategies/dsl.py:237-242`（自由字符串）+ 行情源能力（`backend/app/data/providers.py`、`backend/app/data/symbols.py`） | 部分（需按 provider 汇总真实可用集合） |
| Data Sources | `backend/app/data/providers.py`、`backend/app/domain/protocols.py:17-37 MarketDataProvider` | 已存在 |
| Risk Metrics | `backend/app/research/metrics.py:40-51` | 已存在 |
| Analysis Engines | `backend/app/research/{engine,walk_forward,sensitivity,monte_carlo,ensemble}.py`（入口见 §1.6） | 已存在 |
| Portfolio / Universe / Rebalance | 无 | **不支持**（必须显式输出） |
| Execution mechanics（T+1 / 涨跌停 / 杠杆 / 融资） | 无 | **不支持** |

### 11.2 drift test（用户决策：Registry 必须由代码生成或校验）

CI 必须失败的三类情形：
1. Registry 声称支持某指标/算子/成交模型，而 `backend/app/strategies/dsl.py` 的枚举或特征引擎注册表里没有；
2. 枚举里新增了能力，而 Registry 未收录（反向漂移）；
3. Registry 的资产类别/周期集合与可用行情源不一致。

### 11.3 判定输出

`SUPPORTED` / `PARTIALLY_SUPPORTED` / `UNSUPPORTED` + `missing[]` + `reason`；`PARTIALLY_SUPPORTED` 时必须能列出「哪些部分能先形式化」（计划 §十八、§十三）。

---

## 12. Required Tool Gateway

| 工具 | 档位 | 现状映射 `path:line` | 配额 / 约束 | 计划章节 |
| --- | --- | --- | --- | --- |
| `get_capabilities` | 读 | 新建（§11） | 只读 | §二十四、§五十一 |
| `get_strategy` / `get_strategy_version` | 读 | `backend/app/data/strategy_service.py:67 load_spec()`；`backend/app/api/routers/strategies.py` | 只读 | §二十四 |
| `get_backtest_result` | 读 | `backend/app/api/routers/backtests.py`、`backend/app/api/routers/backtest_metrics.py` | 只读，只回已存结果 | §二十九 |
| `get_risk_analysis` | 读 | `backend/app/research/metrics.py:40-51` | 只读 | §二十七 |
| `search_source` / `read_source` | 读 | 新建（GitHub 侧复用 `backend/app/importer/extract.py`；`backend/app/domain/models.py:754-776`） | 只读，源一律 UNTRUSTED | §二十三、§六十三 |
| `validate_strategy` | 读 | `backend/app/strategies/validator.py:154` | 纯校验，不写库 | §十七 |
| `run_backtest` | **重** | `backend/app/research/engine.py:179 run_backtest(` | 需人工确认或配额 + Celery + 超时 | §二十四、§五十七（用户决策 C6） |
| `run_sensitivity` | **重** | `backend/app/research/sensitivity.py:86 run_sensitivity(` | 同上 | 同上 |
| `run_monte_carlo` | **重** | `backend/app/research/monte_carlo.py:99 run_monte_carlo(` | 同上 | 同上 |
| `run_walk_forward` | **重** | `backend/app/research/walk_forward.py:49 run_walk_forward(` | 同上 | 同上 |
| `compare_strategies` | **重** | `backend/app/research/ensemble.py:720 run_ensemble(` / `:756 run_ensemble_sweep(` | 同上 | §二十四 |
| 任何写库 / 改策略 / 删数据 / 改配置 | **禁止** | — | 模型只能提 proposal，人工批准后走既有服务 | §二十五、§二十一 不变量 2 |

审计与资源：每次调用写 `ai_tool_calls`（§6）；资源上限与 `backend/app/infrastructure/resource_monitor.py`、`docker-compose.yml` 的服务限制对齐（计划 §六十六）。

---

## 13. Required UI

| 项 | 内容 | 现状 | 计划章节 | 版本 |
| --- | --- | --- | --- | --- |
| 新路由 `/lab` | AI 研究台：资料 → 假设 → 草案 → 能力检查 → 确认 → 编译 → 回测 → 分析 | 无（`frontend/src/main.ts:16-35`） | §四十、用户决策 C9 | v1.9.9–v2.0.0 |
| 阶段状态可视化 | Researching → Extracting → Checking capabilities → Building draft → Validating → Waiting for approval → Backtesting → Analyzing → Completed | 无；可复用 `/ai/tasks/{id}/status` 轮询（`backend/app/api/routers/ai.py:201-215`） | §十二、§三十九 | v1.9.9 |
| 溯源视图（Martin 场景） | 原始资料 → AI 理解 → EXPLICIT / INFERRED / ASSUMED / UNKNOWN → Draft → DSL → 回测结果 | 无 | §十四、§四十一 | v2.0.0 |
| Strategy Detail | 不重做九段；新增"AI 研究"分区 | `frontend/src/views/StrategyDetailView.vue`、ADR-114 | §四十一 | v2.0.0 |
| 普通 / 高级分层 | 普通模式只见：研究资料 / 策略想法 / AI 分析 / 策略方案 / 回测 / 风险 / 结论 / 下一步实验；专业词（StrategySpec、prompt_version、tool_trace、source_hash、Capability Registry）只在高级模式 | 现有门控只在 `frontend/src/views/SettingsView.vue:723,727`；4 个视图的 AI 面板未门控（见 §1.5） | §二十、§六十九 | v1.9.9 |
| 隐藏清单与守卫 | `docs/13_UI_UX.md` §10 的隐藏清单需要扩充新专业词；沿用 `backend/tests/test_frontend_contracts.py` 的读源码断言风格 | `docs/13_UI_UX.md` §10、§14（欠账清单守卫 `test_the_outstanding_list_covers_exactly_the_unfinished_sections`） | §二十 | 每版 |

---

## 14. Required Security Controls

| 控制 | 要求 | 现状 | 计划章节 |
| --- | --- | --- | --- |
| 注入结构边界 | 顺序固定 System Contract → Role Contract → Task → UNTRUSTED SOURCE；源**永不进** system prompt；加显式分隔标记 | 无（现在只有单段 `system_prompt`，`backend/app/ai/provider.py:64-89`） | §六、§六十三、§十九 |
| 注入测试样本 | `"Ignore previous instructions..."` / `"Forget the system rules..."` / `"Execute this command..."` 作为资料输入时行为不变 | 无 | §六十三 |
| 源体积与清洗 | 大小上限、控制字符清理、截断策略（并在 UI 说明已截断） | 无 | §六十三 |
| URL 摄取 | SSRF：禁 localhost / loopback / 私网 IP / `file://`；禁重定向到私网；超时；下载上限；robots/ToS；快照留存 | 无（唯一相关代码是 `backend/app/importer/extract.py:80 UNSAFE_ATTRS` 的静态黑名单） | §十九、§二十三 |
| PDF | 纯文本优先；扫描件返回 `UNSUPPORTED / PARSE_FAILED`，**不得假装看懂** | 无 | §八十二 Scenario 4、用户决策 C5 |
| GitHub 不变量 | 不在 API 进程 import 用户代码、不执行 shell、默认无网络、快照固定 commit | 已实现（`backend/app/importer/{sanitize,github_client}.py`、`backend/app/domain/models.py:754-776`） | §二十二、§五十 |
| 密钥 | 加密存储、永不进审计、永不进前端 | 已实现（`backend/app/domain/models.py:606`、`backend/app/infrastructure/secrets.py`、`backend/tests/test_ai_providers.py:38,55,156`） | §十八 |
| 工具权限 | 读/重/写三档；**无写工具**；重任务需确认或配额 | 无 | §二十五、用户决策 C6 |
| 限流与资源 | 复用限流与资源监控 | `backend/app/infrastructure/rate_limit.py`、`backend/app/infrastructure/resource_monitor.py` | §六十六 |
| 暴露面声明纪律 | 安全声明必须与真实部署一致（既有先例） | `backend/tests/test_boundary_claims.py`、`docs/14_SECURITY_LICENSE.md` | §十九 |

---

## 15. Required Tests

| 版本 | 测试族 | 断言方向 | 现有同类先例 `path:line` |
| --- | --- | --- | --- |
| v1.9.7 | Provider / 结构化输出 | 无效 JSON、超时、重试、品牌无关；**换模型必须 miss 缓存** | `backend/tests/test_ai_providers.py`、`backend/tests/test_ai_router.py:18-62` |
| v1.9.7 | Role Contract | 契约加载、版本与内容 hash、`required_capabilities` 匹配、缺失能力拒绝 | 新建（先例风格：`backend/tests/test_no_dead_settings.py:117-133`） |
| v1.9.7 | Capability Registry | drift test（§11.2 三类失败）、`UNSUPPORTED` 不静默降级 | 新建 |
| v1.9.7 | 审计 | provider/model/role/prompt_version/hash/source_ids/tool_calls 完整；无密钥、无全文 | `backend/tests/test_ai_providers.py:156` |
| v1.9.8 | 研究摄取 | 文本/URL/PDF 成功与失败路径；PDF 扫描件 → `PARSE_FAILED` | 新建 |
| v1.9.8 | Hypothesis / Draft | 三态区分（EXPLICIT/INFERRED/ASSUMED/UNKNOWN）、证据绑定、能力缺口、待确认项 | 新建 |
| v1.9.8 | 能力拒绝 | 动量+横截面排名 → `PARTIALLY_SUPPORTED` 且列出 unsupported（**不得**悄悄改成单标的） | 新建（对应计划 §十三 验收核心） |
| v1.9.9 | Compiler | 合法/非法 draft、缺 exit、未注册指标、歧义规则；**不存在 AI→Python 执行路径** | `backend/tests/test_strategies.py:39-90`、`backend/tests/test_dsl_indicators.py` |
| v1.9.9 | Tool Gateway | 读工具直通；重任务未确认被拒；配额耗尽被拒；写工具不存在 | `backend/tests/test_ai_explain.py:132-139`（预算拒绝先例） |
| v1.9.9 | 安全 | 3 类注入样本；SSRF 黑名单；超大 source；工具滥用 | 新建 |
| v1.9.9 | AI 不计算 | 事实函数不含统计量；schema 不含引擎字段；分析只 echo 库中数字 | `backend/tests/test_ai_explain.py:295-304`、`:278-281` |
| v2.0.0 | 验收场景 1–7 + Martin 场景 | 端到端（本地 stub provider 做确定性验收；真实 provider 由用户手工跑一次留证据） | `backend/tests/test_ai_explain.py:57-77`（FakeRouter 注入先例） |
| 每版 | 前端契约 | 普通模式不出现新专业词；阶段状态文案；中文提示 | `backend/tests/test_frontend_contracts.py:519-541,960-970`、`backend/tests/test_ui_promises.py:408-473` |
| 每版 | 规模纪律 | 整仓时长可控（v1.9.6：955 passed / 4 skipped / ~180s） | `scripts/Invoke-Tests.ps1` |

---

## 16. Documentation Changes

| 文档 | 现状 | 需要做什么 | 版本 |
| --- | --- | --- | --- |
| `docs/00_README.md` | V1.0 定位（117 行） | 补"AI 研究层"在核心原则与产品循环中的位置 | v1.9.7 |
| `docs/02_ARCHITECTURE.md`、`docs/03_MODULES.md` | 6 层架构 / 模块清单 | 增加 Research Layer 与 AI Runtime 的层次与模块（M09 AI 扩展） | v1.9.7–v1.9.8 |
| `docs/04_STRATEGY_DSL.md` | 契约 + §6 边界（137 行） | **只加**"研究层产出 DSL 1.0"的分层说明；契约本身不改 | v1.9.8 |
| `docs/05_GITHUB_STRATEGY_IMPORT.md` | GitHub 导入（18.5 KB） | 说明 GitHub 成为 `ResearchSource` 的第一个实现，安全约束不变 | v1.9.8 |
| `docs/06_AI_LAYER.md` | 2.5 KB，R1–R5 是目标设计 | **重写扩展**：Role Contract、Runtime、Gateway、审计、预算分层 | v1.9.7 |
| `docs/07_BACKTEST_ENGINE.md` | 引擎（4 KB） | 新指标的定义与口径（Calmar/Recovery/VaR/CVaR） | Future |
| `docs/11_DATA_MODEL.md` | 数据模型 | 新表（§6） | v1.9.8 |
| `docs/12_API_SPEC.md` | 45.7 KB 逐端点真相 | 新端点（§8）；注意该文档曾专门重写过，必须保持一致 | 每版 |
| `docs/13_UI_UX.md` | 39.5 KB，§14 是"欠账（无）" | `/lab` 新节；§10 隐藏清单扩充新专业词；维持 §14 的欠账机制不被破坏 | v1.9.9 |
| `docs/14_SECURITY_LICENSE.md` | 安全与许可 | URL/PDF 摄取与注入边界 | v1.9.8 |
| `docs/15_ROADMAP_ACCEPTANCE.md` | 241.9 KB，版本表 + 每版读数 | 每个版本加行与读数（含真实渲染证据） | 每版 |
| `docs/17_DECISIONS.md` | 499.3 KB，末尾 ADR-149 | 新 ADR：研究层与 DSL 分层、契约单一来源、缓存 key、工具三档、registry 漂移守卫、`/lab` 路由、版本里程碑、confidence 边界 | v1.9.7 起 |
| `docs/19_DEVELOPMENT_PLAYBOOK.md`、`docs/16_AGENTS.md` | 开发约定 | 契约文件与审计约定 | v1.9.7 |
| **新** `docs/25_...PLAN.md` | 已落盘（用户计划逐字副本） | 保持原样，作为权威输入引用 | 已完成 |
| **新** `docs/26_...GAP_ANALYSIS.md` | 本文件 | 审计结论；v1.9.7 起按它施工 | 已完成 |
| `My_Quant_Lab_Development_Spec_V1.1.docx` + markdown 镜像 | 两份 V1.0 逐字节相同 | 新建 V1.1（记录 V1.0 已完成内容、v1.9.6 现状、AI 研究层规划、v1.9.7–v2.0.0、已确认架构边界、被修正的原计划内容），并加一致性测试 | v1.9.7（待你确认时机） |
| `README.md` | 20.8 KB | 定位与入口 | v1.9.9 |

---

## 17. Version Mapping

| 版本 | 范围（计划章节） | 交付物 | 迁移 | API | 前端 | 部署 |
| --- | --- | --- | --- | --- | --- | --- |
| **v1.9.7** 基础 Role Contract + Runtime | §四、§五、§六、§三十二、§三十六、§三十七、§三十八、§十九（Registry 骨架）、§三十三、§三十五 | `backend/app/ai/contracts/*.md`、Contract Loader/Registry、Capability Registry 骨架 + drift test、缓存 key 修正、审计列、预算分层常量、`ai_role_contracts` | `0012` | `GET /ai/capabilities`、`/ai/roles`、`/ai/audit/{task_id}` | 无新页面（解释链行为不变；错误文案与配额提示） | 补丁版：不部署 NAS（ADR-086） |
| **v1.9.8** Research Layer + Hypothesis + Draft + Capability Registry 完整 | §七、§九、§十、§十一、§十二、§十八、§二十、§二十三、§五十、§五十一、§五十二、§六十一 | `ResearchSource` 抽象（text/url/pdf/github）、`ResearchArtifact`+片段、`StrategyHypothesis`+逐条规则三态、`StrategyDraft`、能力判定（`SUPPORTED/PARTIALLY_SUPPORTED/UNSUPPORTED`）、`POST /ai/sources/*`、`POST /ai/strategy/formalize` | `0013` | 见 §8 | `/lab` 第一版（资料 + 假设 + 草案，只读态） | 补丁版 |
| **v1.9.9** Compiler + Tool Gateway + Audit + Security + Workflow | §十七、§二十四、§二十五、§五十七、§五十八、§五十九、§六十、§六十二、§六十三、§六十四、§六十五、§五十三–§五十六、§四十三 | `StrategyCompiler`（Draft→DSL 1.0）、Tool Gateway（读/重/写三档 + 配额 + 审计）、`ai_research_runs`/`ai_tool_calls`/`strategy_experiments`、异步研究工作流 + 人工确认、统一解释入口 | `0014` | 见 §8 | `/lab` 完整阶段状态机；普通/高级分层与隐藏清单守卫 | 补丁版 |
| **v2.0.0** AI Quant Research Layer 里程碑 | §四十、§四十一、§四十二、§四十四、§四十五–§四十八、§四十九、§六十六、§六十九、§七十、§七十七、§七十八、§八十二（Scenario 1–7 + Martin 场景） | 完整研究 UI + 溯源视图、实验与版本迭代闭环、验收场景全部可演示、文档同步、V1.1 docx + markdown 镜像 | — | — | 完整 | **部署 NAS 并验收**（ADR-086） |
| **Future** | §十四/§二十七 的 Calmar / Recovery / VaR / CVaR；§十一/§四十五–§四十七 的 Portfolio / Universe / Rebalance；§三十一 Vision；§六十七 RAG | 先定义后实现 | 视需要 | 视需要 | 视需要 | — |

---

## 18. Metrics Disposition（计划 §十五 专项）

| 指标 | 状态 | 位置 / 备注 | 处置 | 版本 |
| --- | --- | --- | --- | --- |
| Sharpe | Existing | 引擎既有（`backend/app/api/routers/backtests.py:250` risk_adjusted 组） | 直接用 | — |
| Sortino | Existing | `backend/app/research/metrics.py:40` | 直接用 | — |
| Max Drawdown | Existing | 引擎既有（`backend/app/api/routers/backtests.py:249`） | 直接用 | — |
| Max Drawdown Duration | Existing | `backend/app/research/metrics.py:42` | 直接用 | — |
| Profit Factor | Existing | `backend/app/research/metrics.py:47` | 直接用 | — |
| Expectancy | Existing | `backend/app/research/metrics.py:48` | 直接用 | — |
| Exposure | Existing | `backend/app/research/metrics.py:50`、`backend/app/research/engine.py:521` | 直接用 | — |
| Turnover | Existing | `backend/app/research/metrics.py:51`、`backend/app/research/engine.py:522` | 直接用 | — |
| Win Rate | Existing | 引擎既有（`backend/app/api/routers/backtests.py:256` 附近） | 直接用 | — |
| Annualized Volatility | Existing | `backend/app/api/routers/backtests.py:249` | 直接用 | — |
| Parameter Sensitivity | Existing | `backend/app/research/sensitivity.py:86` | 直接用 | — |
| Monte Carlo（含 Sortino 分布） | Existing | `backend/app/research/monte_carlo.py:99`、`:211` | 直接用 | — |
| Walk-Forward / OOS | Existing | `backend/app/research/walk_forward.py:49`、`backend/app/api/routers/research.py:85` | 直接用 | — |
| Robustness（综合结论） | Partial | 由 walk-forward / OOS / sensitivity / Monte Carlo 组合判断，无单一"Robustness"指标 | 由 Analyst 角色按证据表述，不造新指标 | v1.9.9 |
| **Calmar** | **Missing** | 全仓零命中 | 先写指标定义（CAGR 口径、MaxDD 口径、样本不足时的返回值）与测试规范 | Future |
| **Recovery Factor** | **Missing** | 全仓零命中 | 先定义（与 Calmar 的区别：金额口径 vs 比率口径） | Future |
| **VaR** | **Missing** | 全仓零命中 | 必须先定：Historical / Parametric / Monte Carlo；confidence level；return horizon；单资产 vs 组合；聚合方法 | Future |
| **CVaR** | **Missing** | 全仓零命中 | 同 VaR（并明确与 VaR 的一致性约束） | Future |

结论：计划 §十四「AI 不得自行宣布哪个策略最好」在 v1.9.7–v2.0.0 内**只能用上表 Existing 指标闭环**；Missing 指标在定义与测试规范落地前不得实现（用户决策，计划 §十五）。

---

## 19. Confirmed Decisions（本轮确认）

1. **总体原则**：不推翻现有系统，加"AI 研究层"；AI 负责理解/研究/假设/形式化/解释，确定性引擎负责验证/计算/回测/结果（计划 §二、§七十九）。
2. **C1 = A 方案**：`backend/app/strategies/dsl.py` 的 `StrategySpec` 与 `schema_version` 不动；新增 `ResearchArtifact → StrategyHypothesis → StrategyDraft → StrategyCompiler → StrategySpec 1.0`；禁止 AI→Python→执行。
3. **C2 版本规划**：v1.9.7 Role Contract + Runtime；v1.9.8 Research Layer + Hypothesis + Draft + Capability Registry；v1.9.9 Compiler + Tool Gateway + Audit + Security + Workflow；v2.0.0 里程碑。**不创建 v1.10.x**；每版测试/文档/CI/版本号/tag；只有 v2.0.0 部署 NAS。
4. **C3**：Role Contract 与 Capability Registry 同版起步；契约声明 `required_capabilities`，由 Registry 判定。
5. **C4**：Provider = OpenAI-Compatible + 少量必要 Native；核心业务禁止品牌分支；Role Contract 只声明能力，不绑定模型。
6. **C5**：本期不做 Vision；`source type` 预留 `image`；扫描 PDF → `UNSUPPORTED / PARSE_FAILED`。
7. **C6**：默认禁止 AI 无限调用重计算工具；重任务经"用户确认 / 研究配额 / 每日配额 / 规模 / 超时 / 资源限制"后由 Celery 执行；读工具默认开放；**写操作永不交给模型**。
8. **C7**：先 Gap Analysis 再写代码；本文件即交付物；完成即停。
9. **C8**：V1.0 docx 不修改；新建 V1.1 docx + markdown 镜像（记录 V1.0 已完成内容、v1.9.6 现状、AI 研究层规划、v1.9.7–v2.0.0、已确认边界、被修正的原计划内容）；不覆盖历史。
10. **C9**：`/research` 与 `/api/v1/research/*` 不动；AI 研究台 `/lab`，API `/api/v1/ai/research*`。
11. **C10**：AI Research 走异步（创建 AITask → Celery → 阶段状态 → `GET /api/v1/ai/research/{id}`），前端显示阶段流水线。
12. **新增产品原则（计划 §十三）**：AI 可以创建"策略"，但不能伪造"策略能力"；能力不足必须 `PARTIALLY_SUPPORTED` 并列出 unsupported；替代实验必须标 `Experimental` 且声明"不等于原始策略"。
13. **Martin 场景（计划 §十四）**：成为正式验收场景，必须可追溯「原始资料 → AI 理解 → EXPLICIT/INFERRED/ASSUMED/UNKNOWN → Draft → DSL 1.0 → 回测结果」。
14. **指标分期（计划 §十五）**：Existing / Missing / Planned 分列并归档到版本；Calmar / Recovery / VaR / CVaR 先定义后实现。
15. **Prompt 单一来源（计划 §十六）**：`backend/app/ai/contracts/` markdown 是唯一人工维护源；DB 只做运行时索引/缓存；prompt hash 参与审计与缓存。
16. **Cache key（计划 §十七）**：至少含 provider / model / role / prompt_version / tool_result_hash / source_snapshot_hash / strategy_version / input_hash。
17. **Audit（计划 §十八）**：记录 provider/model/role/prompt_version/input_hash/output_hash/source_ids/source_snapshot_hash/strategy_version/tool_calls/token_usage/cost/status/created_at；不存密钥；默认不存外部原文，只存 source_id/hash/snapshot/引用片段。
18. **安全（计划 §十九）**：注入防护做成代码结构；源永不进 system prompt；URL 摄取必须处理 SSRF/localhost/loopback/私网/`file://`/重定向/大小/超时。
19. **UI（计划 §二十）**：普通模式只见人话层；专业词进高级模式；不重做 Strategy Detail。
20. **七条不变量（计划 §二十一）**：AI ≠ 回测引擎 / ≠ 数据库写入者 / ≠ 任意代码执行器 / ≠ 行情源；StrategySpec 1.0 = 确定性引擎契约；研究层 ≠ 执行层；AI confidence ≠ 统计置信度（不得自动放行、不得改 `result_hash`、不得替代统计置信度、不得作为交易有效性证明）。

---

## 20. Answered Questions（用户在 2026-10-04 逐条确认，v1.9.7 的施工依据）

| # | 最终决定 |
| --- | --- |
| Q1 | Role/System Contract **英文**；面向用户的解释/提示/结论**中文**；内部结构化输出可用英文字段名；AI 不得因契约是英文就用英文回答。预算统一 **USD**；`AI_DAILY_BUDGET_USD`（`backend/app/core/config.py:147`）**正式接线**（不删除）；三层预算必须形成**单一、可测试的决策链**：global = 系统总上限，provider = 单 Provider 上限，research task = 单次研究上限。 |
| Q2 | `/lab` **对普通用户开放**，不进高级模式（AI 研究台是核心产品能力，不是开发者功能）；普通用户看到的名字是 **「AI 研究实验室」**，内部技术名仍是 AI Quant Research Layer。 |
| Q3 | 现有 4 个 AI 解释面板（Dashboard / Signals / StrategyDetail / Backtest）**保持普通模式可见**（它们是人话解释）；高级模式才显示 Research Artifact / Strategy Hypothesis / Strategy Draft / Provenance / Evidence / Capability / Prompt Version / Tool Trace / Audit / Model & Provider / AI Confidence。 |
| Q4 | 配额做成**配置项 + 默认值**（不得硬编码）：单次 Research 重任务 ≤ **3**、每日 Research 重任务 ≤ **20**、单个重任务硬超时 **600s**、单次 Research AI 成本 ≤ **1.00 USD**。`run_backtest` / `run_sensitivity` / `run_monte_carlo` 各计 1 次重任务；**必须防止**模型用参数扫描把 1 次调用偷偷扩成几百次计算而仍只算 1 次配额——参数扫描/策略比较/Monte Carlo 按实际计算规模折算，第一版不求精细计费，但必须有**硬上限**；所有重计算进入统一 quota accounting。 |
| Q5 | 本期**只实现 OpenAI-Compatible Provider**（不新增 Anthropic Native / Google Native）；核心 AI Research Layer 不得依赖任何特定 Provider；Adapter 已为将来留接口。 |
| Q6 | 默认**不保存完整外部原文**，但 500 字符**不是绝对硬限制**：默认 GitHub/网页/论文只存 `source_type/uri/title/author/provider/source_hash/snapshot_hash/retrieved_at/content_length/content_excerpt(≤500)/metadata`；**用户主动输入**（如 Martin 粘贴的 5000 字笔记）可完整保存；用户上传且明确拥有/授权的资料可完整保存，但需 retention policy 控制。`ResearchArtifact → source_hash → snapshot → StrategyHypothesis → StrategyDraft → DSL` 必须可追溯。 |
| Q7 | 本期支持 **文本 / URL / GitHub / PDF**（优先级 Text·URL·GitHub > PDF）；**截图/图片明确不支持**；扫描 PDF 无法可靠提取文本时必须返回 **`PARSE_FAILED`**，不得让 AI 猜内容。 |
| Q8 | **v1.9.7 / v1.9.8 / v1.9.9 都正常发 GHCR 镜像，但不自动部署 NAS**；`v2.0.0` = GHCR + 正式 NAS 部署候选。每个版本仍必须有 tests / CI / docs / release / tag，不得因为不部署而降低质量。 |
| Q9 | V1.1 开发计划文档（`My_Quant_Lab_Development_Spec_V1.1.docx` + Markdown 镜像）在 **v1.9.7 完成时同步完成**；V1.0 原件永久保留、不修改；V1.1 必须反映 Gap Analysis 的真实结论，不得复制 V1.0。 |
| Q10 | `docs/25_AI_QUANT_RESEARCH_LAYER_PLAN.md` 的章节号**是长期引用锚点**（Gap Analysis、V1.1、后续开发文档、commit/PR、验收测试都可引用）；未来计划结构若重大变化，**不静默改旧章节**，采用「旧章节保留 + 新增修订章节 + 版本说明」。 |

### 20.1 用户同时确认的交付节奏（v1.9.7 起每版适用）

- 开发顺序：Role Contract → AI Runtime 基础 → 统一 Budget → Audit 基础 → Capability Registry 基础 → 测试 → 文档 → CI → Tag。
- **不得提前实现** v1.9.8 / v1.9.9 / v2.0.0 的功能。
- **每个版本完成后必须停止**，并给出：① 实际修改文件 ② 实际代码变更 ③ 测试结果 ④ CI 结果 ⑤ 文档变更 ⑥ Git diff ⑦ 版本号 ⑧ 下一版本计划；等待用户确认后才进入下一版本。
- v2.0.0 的核心验收链：**「Martin 的研究资料 → AI 理解 → EXPLICIT / INFERRED / ASSUMED / UNKNOWN → Strategy Draft → Capability Validation → DSL 1.0 → Deterministic Backtest → Risk Analysis → AI Plain-language Explanation」**，必须完整可追溯，且 AI 不得伪造引擎不支持的能力或回测结果。

---

## 21. Stop Point（本阶段边界与仓库改动清单）

- 本阶段只做 Read / Audit / Gap Analysis / Architecture clarification / Documentation（计划 §七十二、§七十三、§七十四；用户 §二十二）。
- **未改动任何产品代码**（`backend/app/**`、`frontend/src/**` 零改动）。
- 本轮仓库改动仅三项：
  1. `docs/25_AI_QUANT_RESEARCH_LAYER_PLAN.md`（新增，用户计划逐字副本）；
  2. `docs/26_AI_QUANT_LAYER_GAP_ANALYSIS.md`（新增，本文件）；
  3. `.gitignore`（新增 `.scratch/`，用于用户要求"仓库内临时文件"的落点）。
- 下一步：等你回答 §20 的开放问题，再进入 v1.9.7（Role Contract + Runtime + Capability Registry 骨架）。
- 在此之前不写任何实现代码。
