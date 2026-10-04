# My Quant Lab 开发规格 V1.1 —— AI Quant Research Architecture Update

> 本文件是 `docs/My_Quant_Lab_Development_Spec_V1.1.md`，是 `docs/My_Quant_Lab_Development_Spec_V1.1.docx`（以及仓库根同名副本）的**唯一内容源**。
> 文档身份：`V1.0 = Original Development Specification` / `V1.1 = AI Quant Research Architecture Update`。

## 0. 前言：V1.1 与 V1.0 的关系

### 0.1 两个版本的身份

- **V1.0 = Original Development Specification**。文件 `My_Quant_Lab_Development_Spec_V1.0.docx` 在仓库根与 `docs/` 各一份，63,081 字节、1,289 段、248 个 Title/Heading、0 个表格，sha256 前 16 位 `062c454aea795117`，两份逐字节相同（`docs/26_AI_QUANT_LAYER_GAP_ANALYSIS.md` §0 第 12 条）。**V1.0 永久保留：不删除、不改写、不覆盖。**本文件生成过程中未对 V1.0 做任何字节级修改。
- **V1.1 = AI Quant Research Architecture Update**。它是 V1.0 的**架构更新版，不是替代版**：V1.0 的章节号仍然可以被引用，V1.1 回答的是三个问题——V1.0 写了什么、今天实现到哪一步、AI Quant Research Layer 增加了什么。
- 产出依据：`docs/25_AI_QUANT_RESEARCH_LAYER_PLAN.md` §七十四（文档同步要求）、§七十五（修改原始开发计划）、§七十六（不要伪造 DOCX 修改结果），以及 `docs/26_AI_QUANT_LAYER_GAP_ANALYSIS.md` §20 的 Q9 约定——「V1.1 在 v1.9.7 完成时同步完成；V1.0 原件永久保留、不修改；V1.1 必须反映 Gap Analysis 的真实结论，不得复制 V1.0」。
- 事实基线：`version.txt` = `v1.9.8`。**本文件最初生成于 v1.9.7 提交之前，本次为 v1.9.8 刷新**：v1.9.7 生成时，那一版全部改动仍在 git 工作树中未提交（`docs/06_AI_LAYER.md`、`docs/12_API_SPEC.md`、`docs/15_ROADMAP_ACCEPTANCE.md`、`docs/17_DECISIONS.md` 等已在工作树中更新），本文件与那批改动一起在同一个 v1.9.7 提交中入库；本次 v1.9.8 刷新同理发生在 v1.9.8 提交之前，v1.9.8 的改动同样尚未提交。提交与打 tag 由主流程完成，本文件本身不做 commit / tag / push。v1.9.8 是补丁版本（ADR-086）：**不部署 NAS**，但仍按流程走 tests / CI / docs / release / tag。
- 「不要伪造 DOCX 修改结果」（§七十六）的落地方式：保留 V1.0 原件，**新建** V1.1 文档，并同时交付 Markdown 镜像作为唯一内容源，由 `scripts/build_dev_spec_docx.py` 确定性生成 DOCX。

### 0.2 怎么读这份文档

- 想了解**现在有什么**：读 §B 章节状态台账、§E 数据模型与 API 增量、§H 路线图进度。
- 想了解**要建成什么**：读 §A 定位、§C 架构、§D StrategySpec 边界、§F UI 增量。
- 想了解**为什么这样做**：读 §G 安全与测试要求、§I 验收标准，以及被引用的 `docs/25` 章节号（章节号是长期引用锚点，见 §J）。
- 状态词只有三种，与 `docs/13_UI_UX.md` 的约定一致：**已完成**（指得出代码或文件）、**进行中**（一部分已落地、一部分还没有）、**待开发**（契约或设计已就位，但没有真实调用路径）。
- 证据一律写成 `path`、`path:line` 或版本号。**凡是查不到确切证据的地方写「待补」，不编造版本号与测试数字。**

### 0.3 最高原则

1. **不要让用户理解量化系统，让系统帮助用户理解量化结果。**用户提供的是投资想法、GitHub 项目、策略代码、PDF、论文、网页、文章、访谈或自己的策略；系统负责把它变成可验证的研究结论，并且用人话讲清楚。
2. **AI 是研究员，不是 DBA。**（`docs/25` §七十九）AI 可以阅读、理解、提出假设、形式化、解释；不能直接改数据库、不能绕过 Validator、不能执行任意 Python 策略代码、不能编造市场数据与回测结果。
3. **AI 负责研究、推理、形式化、解释；程序负责计算、执行和验证。**（`docs/25` §二）任何数字只能来自确定性引擎。
4. **宁可告诉用户「无法形式化」，也不能制造一个看起来合理但未经证实的策略。**（`docs/25` §七十九、§十三）

### 0.4 V1.1 明确不做的事

- 不推翻任何已交付功能（Market Data、Ghostfolio、Research、GitHub Import、Strategy DSL、Strategy Version、Backtest、Paper、Signals、Risk、Sensitivity、Monte Carlo、Position Sizing、Ensemble、Resource Monitor、Audit、AI Provider、AI Explanation 全部保留，`docs/25` §七十三）。
- 不为 AI 引入巨大基础设施（不引入 Kafka / Kubernetes / 复杂 Agent Framework / 大型 Vector DB，`docs/25` §六十六）。
- 不在第一阶段引入 RAG 与自主 Agent Loop（`docs/25` §六十七、§六十八）。
- 不做自动交易、不做投资建议（`docs/25` §四十八）。
- 不重做 `Strategy Detail` 既有的九段（`docs/26` §5.8）。

## §A 定位与范围变更

### A.1 从 Quant Lab 升级为 AI-Powered Quantitative Research Lab

- V1.0 的定位：一个自托管的个人量化策略实验室——行情、特征、策略 DSL、回测、模拟盘、信号、AI 解释。
- V1.1 的定位（`docs/25` §一）：**AI-Powered Quantitative Research Lab**。用户在 V1.0 里「自己写策略」，在 V1.1 里可以「把资料交给系统研究」。
- 升级后的主流程：

```text
Research Source
  → AI Research
  → Strategy Understanding
  → Strategy Formalization
  → StrategySpec
  → Validation
  → Backtest
  → Risk Analysis
  → AI Interpretation
  → User
  → Strategy Iteration
  → New Strategy Version
```

- 目标形态（`docs/25` §八十一）：`My Quant Lab = Quant Engine + Research System + Strategy DSL + AI Research Intelligence`。**不是**「加一个 AI 聊天窗口」。

### A.2 五条红线（V1.0 已定，V1.1 不变）

| # | 红线 | V1.0 依据 | V1.1 的落地方式 |
| --- | --- | --- | --- |
| 1 | 不自动交易 | V1.0 `14 Security` §8、ADR-009 | 保持；AI 角色的输出永远不是交易指令 |
| 2 | AI 只解释不计算 | V1.0 `06 AI Layer` §4 | 强化为 AI System Contract（§C.4）；数字只能由 `build_signal_facts()` / `build_backtest_facts()` 注入 |
| 3 | 回测可复现 | V1.0 `07 Backtest Engine` §12 | 保持；AI 不得修改已产生的历史回测结果，`result_hash` 不可被 AI 改写 |
| 4 | 真实持仓与模拟盘严格隔离 | V1.0 `08 Paper Trading` §3、ADR-006 | 保持；AI 不得把两者混为一谈 |
| 5 | 外部内容一律视为不可信输入 | V1.0 `14 Security` §3、`05` 第 6 节 | 扩展为代码级信任边界 `SYSTEM / ROLE CONTRACT → TRUSTED TASK → UNTRUSTED SOURCE`（ADR-153） |

### A.3 V1.1 新增范围（`docs/25` §八十）

1. AI Role Contract 与 AI Runtime（研究角色有书面工作规范，不是散落的提示词字符串）。
2. Strategy Capability Registry（AI 必须知道系统支持什么、不支持什么）。
3. 研究来源统一（GitHub / URL / PDF / 文本 → Research Artifact）。
4. 研究与形式化（Research → Strategy Hypothesis → Strategy Draft → StrategySpec）。
5. 受控工具调用（Tool Gateway，读 / 重 / 写三档，写工具不存在）。
6. 统一解释入口与通俗表达（Explanation Framework）。
7. 策略实验与版本迭代（V1 → 实验 → V2，历史版本不可变）。
8. `/lab`「AI 研究实验室」界面与普通/高级可见性分层。
9. 安全、审计与完整测试。

### A.4 明确排除（非目标）

- 不做投资建议、不做收益承诺；AI 不得自行宣布「哪个策略最好」（`docs/25` §十四、§四十八）。
- 本期不做 Vision（用户决策 C5）；`source type` 预留 `image`，扫描 PDF 必须返回 `UNSUPPORTED / PARSE_FAILED`。
- 本期只实现 OpenAI-Compatible Provider，不新增 Anthropic Native / Google Native（用户决策 Q5）。
- Calmar / Recovery Factor / VaR / CVaR **未定义前不实现**（用户决策，`docs/25` §十五）。
- Portfolio / Universe / 横截面排名 / 再平衡 / T+1 / 涨跌停 / 杠杆 / 融资：当前引擎不支持，必须显式输出 `UNSUPPORTED` 或 `PARTIALLY_SUPPORTED`（`docs/26` §11.1）。

## §B V1.0 章节状态台账

### B.1 台账说明

- 状态三态：**已完成** / **进行中** / **待开发**。
- 证据列给出版本号或文件路径；查不到确切证据写「待补」。
- 本表只描述**V1.0 章节**的落地程度，不描述 AI Quant Research Layer（那在 §C 与 §H）。
- V1.0 的「前言」（一句话定义 / 核心原则 / 产品循环 / 核心真实数据源 / 推荐 V1 技术栈 / V1 范围边界）中的技术栈与范围边界已由 Phase 0–8 落地，此处不单列，实际实现见 `docs/02_ARCHITECTURE.md` 与 `docker-compose.yml`。

### B.2 逐章台账

| V1.0 章节 | 状态 | 主要证据 | 说明与欠账 |
| --- | --- | --- | --- |
| 01 产品需求（PRD） | 已完成 | `docs/15_ROADMAP_ACCEPTANCE.md`：Phase 0–8 全部 DONE；v1.0.0 在真实 NAS 验收 12/12 | 用户角色、目标、四条关键流程（GitHub 导入 / 策略验证 / 模拟盘 / 实时信号）均已交付；V1.1 新增的研究流程写在本文件 §C |
| 02 系统架构 | 已完成 | `docs/02_ARCHITECTURE.md`；6 层架构由 Phase 0–8 落地 | V1.1 在 6 层之外增加 Research Layer 与 AI Runtime（§C）；`docs/26` §16 把架构文档同步列为 v1.9.7–v1.9.8 工作 |
| 03 功能模块规范 | 已完成 | `docs/03_MODULES.md`；M01–M13 均有实现 | M05 GitHub Importer 见 ADR-060/061/062；M09 AI 在 v1.9.7 扩展（Role Contract / Runtime / Registry）；M09 的完整研究能力见 §H |
| 04 统一策略 DSL | 已完成（契约保持不变） | `docs/04_STRATEGY_DSL.md`；`backend/app/strategies/dsl.py:255` 的 `StrategySpec`，`schema_version` 1.0，全仓 104 处引用 | **DSL 1.0 不动**（用户决策 C1 = A 方案）；计划中的扩展字段落在研究层实体上，不进入 DSL（§D） |
| 05 GitHub 导入 | 已完成 | Phase 6 DONE；ADR-060（用 commit 命名修订）、ADR-061（版本账本由服务分配）、ADR-062（不合格草案转 `review_required`） | provenance / coverage / incomplete detection / parsing failure / license handling / watcher 全部保留（`docs/25` §五十）；V1.1 把它定位为 `ResearchSource` 的第一个实现 |
| 06 AI Layer | 进行中 | 已实现：`backend/app/ai/provider.py`（Provider 抽象与 `AIRouter`）、`backend/app/ai/runtime.py`、`backend/app/ai/budget.py`、`backend/app/ai/role_contracts.py`、`backend/app/capabilities.py`、`backend/app/ai/research_schemas.py`、`backend/app/ai/research.py`、`backend/app/ai/contracts/{SYSTEM,RESEARCHER,STRATEGY_ARCHITECT,EXPLAINER}.md` | 角色现状（`docs/06_AI_LAYER.md` §3）：R3 Signal Explainer = 已完成（`EXPLAINER.md` + `POST /signals/{id}/explain`）；回测分析 = 已完成（`EXPLAINER.md` 的 `## Task: backtest_analysis`）；**R1 Strategy Researcher = 已完成（v1.9.8：`RESEARCHER.md` 1.1.0 承担 `backend/app/ai/research.py` 五步链的理解步骤）**；**R2 Quant Tutor = 待开发（无契约文件）**；**R4 Research Assistant = 已完成（v1.9.8：`STRATEGY_ARCHITECT.md` 1.1.0 产出不可执行 `StrategyDraft` 并过能力裁决）**；**R5 Daily Analyst = 待开发（无契约文件；`daily_summary` 枚举在实现前不得对外承诺）** |
| 07 回测引擎 | 已完成 | Phase 2 DONE（engine / costs / metrics / walk-forward / OOS / comparison）；ADR-054（警告随结果落库）、ADR-055（`warmup_unmet` 点不参与排名） | 新增指标（Calmar / Recovery / VaR / CVaR）在定义与测试规范落地前不实现（§A.4） |
| 08 模拟盘 | 已完成 | Phase 4 DONE（执行引擎、持仓、入金、生命周期、重置审计）；ADR-066（提现不是亏损，基准改为净入金） | 与真实持仓（Ghostfolio）严格隔离的红线不变 |
| 09 信号引擎 | 已完成 | Phase 7 DONE（调度、扫描、去重、结果跟踪、通知、降噪）；ADR-065（胜率必须与分母一起发布） | 信号解释是 AI 已完成的两条链之一；信号的 BUY/SELL/WAIT 决定权属于 Signal Engine，不属于 AI（`docs/25` §三十） |
| 10 Ghostfolio | 已完成 | Phase 3 DONE（只读适配器、持仓、组合上下文、代码别名） | ADR-001 不变：走 REST，不直连数据库 |
| 11 数据模型 | 进行中 | 既有实体见 V1.0 `11 数据模型` 与 `docs/11_DATA_MODEL.md`；v1.9.7 新增 `ai_role_contracts` 表与 `ai_tasks` 五个可空列（迁移 `backend/alembic/versions/0012_ai_role_contracts.py`）；v1.9.8 新增迁移 `backend/alembic/versions/0013_research_layer.py`（`down_revision = "0012_ai_role_contracts"`）与六张研究层表（`research_artifacts`、`research_artifact_fragments`、`ai_research_runs`、`strategy_hypotheses`、`strategy_hypothesis_rules`、`strategy_drafts`） | 研究层六表已建；`ai_source_snapshots`（统一来源，Phase 4）、`strategy_experiments`（Phase 7）、`ai_tool_calls`（Tool Gateway，Phase 5）设计已定稿（`docs/26` §6）但**尚未建**，待 v1.9.9 |
| 12 API | 进行中 | `docs/12_API_SPEC.md` 逐端点真相，第 5 行的 `[已实现]` / `[计划]` / `[取消]` 标记由 `backend/tests/test_api_spec_truth.py` 与真实路由表双向绑定 | v1.9.7 新增三条只读端点；v1.9.8 新增四条研究层端点（`POST /ai/research`、`GET /ai/research`、`GET /ai/research/{run_id}`、`POST /ai/strategy/formalize`）并已在 `docs/12_API_SPEC.md` 的 AI Research 一节登记为 `[已实现]`（§E.4） |
| 13 UI/UX | 进行中 | `docs/13_UI_UX.md`（导航树已增至 10 行，含 `/lab`）；v1.9.3 `/research` 四步向导、v1.9.4 版式主次、v1.9.5/§v1.9.6 的真实渲染修复（ADR-126…ADR-149）；v1.9.8 `/lab` 只读第一版（`frontend/src/views/LabView.vue`，「AI 研究实验室」是 `frontend/src/App.vue` 的第 5 个导航项，普通与高级模式都可见） | 欠账：`docs/26` §0 第 11 条——Dashboard / Signals / StrategyDetail / Backtest 四个 AI 面板尚未做高级模式门控（只有 `frontend/src/views/SettingsView.vue:723,727` 是对的）；完整 `/lab`（状态机与溯源视图）待 v1.9.9 / v2.0.0 |
| 14 安全与许可 | 已完成（研究层部分进行中） | `docs/14_SECURITY_LICENSE.md`；ADR-153 的 `UntrustedSource` / `wrap_untrusted()` / `assemble_messages()` 把来源渲染成最后一条 user 消息 | 待开发：URL 摄取的 SSRF / 大小 / 超时约束、来源体积与清洗、工具滥用防护（`docs/26` §14） |
| 15 Roadmap | 进行中 | 已写入 v1.9.7 与 v1.9.8 版本行与读数段（v1.9.7 读数：工作树 27 改 + 15 新增、整仓 1014 passed + 4 skipped、`ruff` 180 files、前端 620 modules、五条红证据；v1.9.8 的版本行与真实读数见该文件） | 逐版继续加行与读数；按 `docs/26` §16 附真实渲染读数 |
| AGENTS.md（`docs/16`） | 已完成 | `docs/16_AGENTS.md`；Phase 0–8 按该契约执行 | V1.1 新增的契约约定（角色契约文件、审计、信任边界）需要补入该文件（`docs/26` §16 列为 v1.9.7） |
| 17 ADR | 进行中 | `docs/17_DECISIONS.md` 已写至 **ADR-157**：ADR-150 角色契约是 markdown 文件、ADR-151 能力注册表由代码派生并被测试绑定、ADR-152 预算决策链、ADR-153 AI runtime 与不可信来源边界、ADR-154 StrategyHypothesis 的理解必须携带 provenance、ADR-155 StrategyDraft 不可执行且 DSL 1.0 不变、ADR-156 能力裁决在服务端三态且无静默降级、ADR-157 结果是禁区（禁用指标键、UNVERIFIED 文字声明、七步校验链、一次受控重试） | 工具三档、`/lab` 路由、confidence 边界等 ADR 待后续版本补（`docs/26` §16） |
| 18 示例策略 | 已完成 | V1.0 `18 Sample Strategy — PA Breakout V1`；ADR-040 修复 `period_ref`（两个示例原先引用不存在的列 `ema20`，实际被当作常量 20） | 示例策略仍可作为回归基线；AI 生成的策略一律走新版本，不覆盖示例 |
| 19 AI 实施手册 | 已完成 | `docs/19_DEVELOPMENT_PLAYBOOK.md` | v1.9.7 起的施工依据是 `docs/26`，实施手册需要同步「契约文件 + 审计」约定（`docs/26` §16） |

## §C AI Quant Research Layer 架构（V1.1 新增）

### C.1 分层架构

`docs/25` §七十八给出的最终架构目标：

```text
External Sources (GitHub / PDF / Web / Paper / Idea / Code)
  → AI Research Layer
      (Researcher / Architect / Compiler / Analyst / Risk Analyst / Explainer)
  → StrategySpec Canonical IR
  → Capability Registry
  → Validator
  → Backtest Engine / Risk Engine
  → AI Interpretation
  → Plain Language
  → User
  → New Experiment
  → New Version
```

对应的运行时分层（`docs/25` §四）：`Model → Provider Adapter → AI Runtime → Role Contract → Tools → Structured Output`。其中：

- **Model** 可替换；**Provider Adapter** 是唯一的品牌相关代码（`backend/app/ai/provider.py` 的 `OpenAICompatibleProvider`）。
- **AI Runtime** 是全部 AI 调用的唯一入口（`backend/app/ai/runtime.py` 的 `run_task()`）。
- **Role Contract** 是磁盘上的 markdown（`backend/app/ai/contracts/*.md`）。
- **Tools** 只能经 Tool Gateway 调用（§C.8）。
- **Structured Output** 必须过 schema 校验（`docs/06_AI_LAYER.md` §4）。

### C.2 六个 AI 角色（`docs/25` §十五–§十七、§二十六–§二十八）

| 角色 | 职责 | 计划章节 | 契约文件 | 状态 |
| --- | --- | --- | --- | --- |
| Strategy Researcher | 阅读研究资料（GitHub / PDF / 网页 / 策略说明），识别交易逻辑、指标、市场、时间周期、入场、出场、风险管理、仓位、执行假设；提取证据、识别未知项 | §十五 | `RESEARCHER.md` | **已完成**（v1.9.8：契约升到 1.1.0，是 `backend/app/ai/research.py` 五步链的第一步） |
| Strategy Architect | 研究思想 → 策略假设 → Universe → Signals → Entry → Exit → Risk → Position Sizing → Execution，输出 `StrategyDraft` | §十六 | `STRATEGY_ARCHITECT.md` | **已完成**（v1.9.8：契约升到 1.1.0，产出**不可执行**的 `StrategyDraft` 并交服务端能力裁决；编译成 StrategySpec 待 v1.9.9） |
| Strategy Compiler | 把 Draft 编译为合法 StrategySpec；**禁止 AI → 任意 Python → 执行** | §十七 | `STRATEGY_COMPILER.md` | 待开发（契约未落盘） |
| Backtest Analyst | 分析收益、回撤、风险、稳定性、交易频率、盈亏结构、参数敏感性、异常、潜在过拟合与下一步研究方向；**不能修改原始结果** | §二十六 | `BACKTEST_ANALYST.md` | 部分：回测分析链已完成，由 `EXPLAINER.md` 的 `## Task: backtest_analysis` 承担；独立契约待落盘 |
| Risk Analyst | Max Drawdown / Drawdown Duration / Recovery / Sharpe / Sortino / Calmar / Volatility / VaR / CVaR / Monte Carlo / 持仓集中度 / Exposure / Turnover → 专业分析 + 通俗解释 + 风险提醒 + 要盯什么 | §二十七 | `RISK_ANALYST.md` | 待开发（契约未落盘） |
| Explainer | 面向普通用户的核心 AI：发生了什么？为什么？风险在哪里？下一步看什么？保留 Quant Tutor / Signal Explainer 的意图，统一到 Explanation Framework | §二十八、§三十 | `EXPLAINER.md` | **已完成**（信号解释 + 回测分析两条链） |

补充：`SYSTEM.md` 承载 18 条系统契约与最高原则，拼在每次请求的最前面；`REVIEWER.md`（契约合规自检，pass / needs_revision + findings）在 `docs/26` §10 中规划为 v1.9.9。

### C.3 Role Contract（ADR-150）

- 位置：`backend/app/ai/contracts/`，每个角色一个 markdown 文件（类似编程 Agent 的 `AGENTS.md`）。
- 结构：
  - front-matter 字段：`name`、`role`、`version`、`task_types`、`required_capabilities`、`output_language`，可选 `prompt_names`。
  - 正文按 `## Task: <task_type>` 分段。
- 解析：`backend/app/ai/role_contracts.py` 的 `parse_contract(text, *, path)`；`RoleContract` 是 frozen dataclass，`content_hash` 是文件字节的 sha256，`ref` 形如 `EXPLAINER@1.0.0`。
- 索引：`sync_role_contracts(db)` 按 `(name, version)` upsert 进 `ai_role_contracts` 表，用于回答「这次调用用的是哪份契约的哪个版本」。
- 失败模式（全部在加载期抛 `ContractError`）：缺 `SYSTEM.md`、缺 front-matter、声明了 `task_types` 却没有 `## Task:` 段落、段落名与声明不匹配、同一 `name` 出现两份。
- 消息拼装（`assemble_messages()`）：system 消息 = `SYSTEM.md` 正文 + 该任务段落；来源永远排在最后一条 user 消息里（§G.1）。
- 语言策略（用户决策 Q1）：契约与系统提示用**英文**；面向用户的解释、提示、结论用**中文**；AI 不得因契约是英文就用英文回答。

### C.4 AI System Contract（18 条要点，`docs/25` §六）

`SYSTEM.md` 承载以下 18 条铁律：

1. 不编造市场数据。
2. 不编造回测结果。
3. 不编造策略原作者没有表达的规则。
4. 不把推断当事实。
5. 不把假设当原始策略。
6. 不修改已产生的历史回测结果。
7. 不直接修改数据库。
8. 不绕过 Strategy Validator。
9. 不直接执行任意 Python 策略代码。
10. 所有可执行策略必须进入 StrategySpec。
11. 必须过 Schema Validation。
12. 必须过 Strategy Validation。
13. 回测结果必须来自 Backtest Engine。
14. AI 对回测结果只能解释，不得重新计算后覆盖。
15. 无法形式化的策略必须明确标记，而不是猜测。
16. 所有外部策略必须保存 provenance。
17. 所有重要结论尽可能提供证据来源。
18. AI 可以提出研究假设，但不得把假设自动变成已验证事实。

### C.5 三层信息层（`docs/25` §七）

| 层 | 名称 | 内容 | 类比 |
| --- | --- | --- | --- |
| Layer A | Research Artifact | GitHub repo / PDF / URL / 文章 / 论文 / 用户输入 / 访谈 / 代码 | 原材料 |
| Layer B | Strategy Hypothesis | 策略思想、交易逻辑、假设、可能指标、可能入场、可能出场、风险管理、不确定项 | 研究结论 |
| Layer C | StrategySpec | 机器可执行的策略定义（DSL 1.0） | 可执行契约 |

- **禁止** `Research Artifact → 直接变成代码`。
- 正确链路：`Artifact → AI Research → Hypothesis → Formalization → StrategySpec → Validator → Backtest`。
- 研究层的中间实体 `StrategyDraft` 位于 Layer B 与 Layer C 之间：可编译、**不可执行**（`docs/26` §6）。

### C.6 证据与三态（`docs/25` §九、§十）

- 证据必须与策略规则绑定。不能只存 `close > ema20`，而要记录形如：

```json
{"rule": "close > ema20", "origin": "EXPLICIT", "confidence": 0.94,
 "evidence": [{"source": "github", "file": "strategy.py", "line_start": 82, "line_end": 85}]}
```

- `origin` 取值：
  - **EXPLICIT**：原文明确（例：Buy when price crosses EMA20）。
  - **INFERRED**：AI 按上下文推断（例：原文只说 buys strong momentum stocks → 推断 20-day return > threshold）。
  - **ASSUMED**：系统为实验主动假设（例：原文没说手续费 → 假设 fee = 10 bps），**不得伪装成原策略规则**。
  - **UNKNOWN**：材料没有说、AI 也不推断。
- AI 自报的 `confidence` 是**研究提示**，不是统计置信度：不得自动放行、不得改 `result_hash`、不得替代统计置信度、不得作为交易有效性证明（`docs/26` §19 第 20 条）。

### C.7 Strategy Capability Registry（ADR-151）

- `backend/app/capabilities.py` 是 AI 的「知识边界」，**全部从代码派生**，不手抄，由 drift test 绑定（`backend/tests/test_capabilities.py`）。
- 14 组能力与事实来源：

| 组 | 来源 |
| --- | --- |
| `operators` | `backend/app/dsl/schema.py` 的比较算子枚举 |
| `indicators` | `backend/app/features/engine.py` 的 `SUPPORTED_INDICATOR_TYPES` |
| `features` / `price_action_features` | `FEATURE_CATALOGUE` |
| `fill_models` / `entry_order_types` | `ExecutionSpec.model_fields` |
| `sizing_modes` | `RiskSpec.model_fields` |
| `risk_models` / `execution_fields` / `market_fields` | 各 spec 的字段 |
| `metrics` | `backend/app/research/metrics.py` 的 `Metrics`（去掉 `notes` / `initial_capital`） |
| `timeframe_annualisation` | `BARRS_PER_YEAR` |
| `data_providers` | `PROVIDER_NAMES` |
| `analysis_engines` | 回测 / 敏感性 / Monte Carlo / 集成 / 信号 / 模拟盘模块 |

- 明确不支持清单 `UNSUPPORTED_CAPABILITIES`（含原因）：`cross_sectional_universe`、`leverage`、`var_cvar`、`rag`、`live_execution` 等；`short_selling` 是目前**唯一**的 `partially_supported`。
- 三态评估 `assess(requested)` → `CapabilityReport`：
  - `SUPPORTED`：请求的能力都在清单里。
  - `PARTIALLY_SUPPORTED`：有缺失或部分支持，必须在 `reasons` 里列出「缺什么、为什么、哪些部分可以先形式化」。
  - `UNSUPPORTED`：全部缺失。
- 规则：**全部缺失 → `UNSUPPORTED`；任一缺失或部分支持 → `PARTIALLY_SUPPORTED`；否则 `SUPPORTED`**；未知 token 按缺失处理。
- AI 生成 StrategySpec 前必须先读它；遇到不支持的能力要报 `NEEDS_CAPABILITY`，**不得自己发明实现**（`docs/25` §二十、§五十二）。例：遇 VWAP → `unsupported capability: VWAP` + `Strategy status: NEEDS_CAPABILITY`。
- 同时 `backend/app/features/engine.py` 新增 `SUPPORTED_INDICATOR_TYPES`、`INDICATOR_ALIASES`、`normalise_indicator_type()`（`bb` / `BOLLINGER_BANDS` 折叠到规范名）；`VWAP` 仍被拒绝。
- 新增能力（VWAP / ADX / OBV / ROC 等）只扩展 Registry + Engine，不改 AI 逻辑。
- Registry **不建表**（用户决策）：必须由代码生成或校验。

### C.8 AI Tool Gateway（`docs/25` §二十四、§二十五、§五十七、§五十八）

- 结构：`AI → Tool Gateway → Domain Service`，**不是** `AI → Database`。
- 每次调用经 permission / validation / audit / timeout / resource limits；工具调用必须 bounded、audited、validated、有 timeout（`docs/25` §六十八）。
- 工具清单（`docs/26` §12）：

| 工具 | 档位 | 说明 |
| --- | --- | --- |
| `get_capabilities` | 读 | 能力注册表 |
| `get_strategy` / `get_strategy_version` | 读 | 读策略与版本（`backend/app/data/strategy_service.py` 的 `load_spec()`） |
| `get_backtest_result` | 读 | 只回已存结果 |
| `get_risk_analysis` | 读 | 风险指标读数 |
| `search_source` / `read_source` | 读 | 来源检索与阅读，源一律 UNTRUSTED |
| `validate_strategy` | 读 | 纯校验，不写库 |
| `run_backtest` | 重 | 需人工确认或配额 + Celery + 超时 |
| `run_sensitivity` | 重 | 同上 |
| `run_monte_carlo` | 重 | 同上 |
| `run_walk_forward` | 重 | 同上 |
| `compare_strategies` | 重 | 同上 |
| 任何写库 / 改策略 / 删数据 / 改配置 | 禁止 | 模型只能提 proposal，人工批准后走既有服务 |

- 工具结果必须结构化，AI 只负责阅读。示例：`{"backtest_id": ..., "strategy_version": ..., "metrics": {}, "warnings": [], "data_quality": {}}`。
- **写操作永不交给模型**（用户决策 C6）。AI 的写入路径只能是：`AI → create_strategy_draft() → Validator → Human approval → create_strategy_version()`。

### C.9 Explanation Framework（`docs/25` §二十八、§二十九、§五十六）

- 统一解释入口：signal / strategy / backtest / risk / paper / research 共用一套解释逻辑，不为每个页面重写 AI 调用。
- 解释层必须引用系统事实：系统给 `{"cagr": 0.187, "max_drawdown": -0.243, "sharpe": 1.21}`，AI 只能解释，**不得自行生成 cagr = 20%**。
- 输出四问：发生了什么？为什么？风险在哪里？下一步看什么？另加「查看专业分析」。
- Signal Explanation（`docs/25` §三十）：AI 可解释为什么触发、命中哪些规则、当前风险、哪些条件可能使信号失效、下一步关注什么；**不负责决定 BUY / SELL / WAIT**——决定权属于 Strategy Engine / Signal Engine。
- 已实现的输出协议（`docs/06_AI_LAYER.md` §6）：
  - `SIGNAL_EXPLANATION_SCHEMA`：`summary`、`why`、`what_could_invalidate`、`what_to_watch_next`、`risk_notes`、`plain_language`。
  - `BACKTEST_EXPLANATION_SCHEMA`：`summary`、`key_drivers`、`risks`、`what_to_watch_next`、`plain_language`。
- 不得要求模型输出「保证盈利」「确定上涨」等结论。

### C.10 Model Capability Profile 与 Role 能力匹配（`docs/25` §三十一–§三十四）

- `Model Capability Profile` 七项：`structured_output`、`tool_calling`、`long_context`、`vision`、`reasoning`、`code_understanding`、`web_research`。
- 项目**不判断哪个模型最聪明**，用户自己选；项目只判断是否满足 Role 的最低接口能力。
- Role Capability Requirements（`docs/25` §三十二）：

| 角色 | 最低能力 |
| --- | --- |
| Strategy Researcher | `long_context` + `structured_output` + `reasoning` |
| Strategy Compiler | `structured_output` + `reasoning` + `tool_calling` |
| Explainer | `text_generation` + `structured_output` |
| 复杂研究（Complex Research） | 上述四者（含 `tool_calling`） |

- 路由从「模型名称」升级为 **Role + Capability + Cost + User Preference**（`docs/25` §三十三）：复杂策略研究 → 高能力；每日摘要 → 低成本；风险解释 → 中/高；策略编译 → 高能力。
- 允许一个模型承担多个 Role（§三十四）。
- **不得制造模型绑定**（§三十五）：禁止 `if provider == "openai"` 式的核心业务分支（Provider API compatibility adapter 除外）。
- 现状：`ai_models.capability_tier` 目前只是单个字符串，`docs/26` §5.6 的裁决是用 `settings_json` 承载能力档位或加列，且**能力由用户手填**，不做自动探测（避免额外请求与成本）。已有分层意图 `cheap < standard < high`（`AIRouter`）仍然成立。

### C.11 预算与成本分级（`docs/25` §六十五，ADR-152）

- 成本分级：
  - **CRITICAL** = Strategy Compilation。
  - **IMPORTANT** = Backtest Analysis、Risk Analysis。
  - **OPTIONAL** = Daily Summary。
- 预算耗尽时：**量化计算继续工作**（回测、信号、模拟盘不读 AI 预算），AI 任务降级或停止，抛 `BudgetExceeded`，API 映射为 HTTP 429。
- 已实现的单一判定链（`backend/app/ai/budget.py` 的 `decide()`，顺序固定）：

```text
global(已耗尽) → provider(已耗尽) → task(本次估算 > 单任务上限)
→ global(本次成本) → provider(本次成本) → calls(今日调用数)
```

- 返回 `BudgetDecision(allowed, scope, reason, limit_usd, spent_usd, estimated_usd, remaining_usd)`；`0` 表示该层整体禁用（不是「无限」）。
- 配置（全部为**真设置**，同时进 `.env.example`、`docker-compose.yml` 与 `backend/tests/test_deploy_defaults.py` 的 `KNOBS` 守卫）：

| 设置 | 默认值 | 层 |
| --- | --- | --- |
| `AI_DAILY_BUDGET_USD` | 2.0 | 全局（原 V1.0 无读取方，v1.9.7 正式接线） |
| `ai_providers.daily_budget_usd` | 2.0 | 供应商 |
| `AI_TASK_BUDGET_USD` | 1.0 | 单任务 |
| `AI_DAILY_TASK_LIMIT` | 20 | 每日任务数 |
| `AI_TASK_TIMEOUT_SECONDS` | 600 | 单任务超时 |

- 研究配额（用户决策 Q4，做成配置项 + 默认值，不得硬编码）：单次 Research 重任务 ≤ 3、每日 Research 重任务 ≤ 20、单个重任务硬超时 600s、单次 Research AI 成本 ≤ 1.00 USD；`run_backtest` / `run_sensitivity` / `run_monte_carlo` 各计 1 次重任务；**必须防止**模型用参数扫描把 1 次调用偷偷扩成几百次计算却仍只算 1 次配额——扫描规模必须折算，第一版不求精细计费，但必须有硬上限。
- 预算统一 **USD**（用户决策 Q1）。V1.0 `06 AI Layer` 原文写的「¥2/day」是实现与文档的偏差，以本文件为准。

### C.12 Prompt 版本化与 AI 输出可审计（ADR-153）

- Prompt 单一来源（用户决策）：`backend/app/ai/contracts/` 的 markdown 是唯一人工维护源；DB 的 `ai_prompts` 只做运行时索引；硬编码提示词常量不再作为源。
- 命名风格：V1.0 已有 `signal_explain@1.0.0` / `backtest_explain@1.0.0`；V1.1 新增 `strategy_research@1.x`、`strategy_formalization@1.x`、`strategy_compile@1.x`、`backtest_analysis@1.x`、`risk_analysis@1.x`、`plain_explanation@1.x`。
- 历史名不丢：`EXPLAINER.md` 的 `prompt_names` 保留 `signal_explain` / `backtest_explain`。
- 审计要求：记录 provider / model / role / prompt_version / input_hash / output_hash / source_ids / source_snapshot_hash / strategy_version / tool_calls / token_usage / cost / status / created_at。
- 不存密钥；默认不存完整外部原文，只存 `source_id` / hash / snapshot / 引用片段。
- `GET /ai/audit/{task_id}`（v1.9.7）已暴露 provider / model / role / 契约哈希 / 输入输出哈希 / token / 成本 / 来源 / 策略版本；`tool_calls` 待 v1.9.9 的 `ai_tool_calls`。
- 缓存身份（`cache_key()`）由八项组成，任何一项变化都不命中缓存：`role`、`provider` / `model`、`prompt_hash`（SYSTEM + 角色契约的哈希）、`tool_result_hash`、`source_snapshot_hash`、`strategy_version`、原 `input_hash`。
  - V1.0 的 `AIRequest.input_hash` **不含 provider / model**，换模型不会 miss 缓存——这是 `docs/26` §4.1 记录的缺陷，已由 ADR-153 修正。

### C.13 研究过程可追踪（`docs/25` §三十九）

- 必须能回答：AI 做了什么 → 读取了什么 → 得出了什么 → 哪些是原文 → 哪些是推断 → 哪些是假设 → 最终形成什么策略。
- **不要只显示「AI 已生成策略」**。中间层（Artifact → Hypothesis → Draft → StrategySpec）都要有落点与审计。
- 已落点（v1.9.8，`docs/26` §6）：`ai_research_runs`（一次研究、多阶段）、`research_artifacts`、`research_artifact_fragments`（证据绑定）、`strategy_hypotheses`、`strategy_hypothesis_rules`（逐条规则的三态与能力状态）、`strategy_drafts`；`ai_research_runs` 的「可暂停」异步工作流与人工确认、以及 `strategy_experiments` 仍待 v1.9.9。
- 研究状态机（`docs/25` §六十）：`DRAFT` / `NEEDS_INPUT` / `NEEDS_REVIEW` / `READY` / `VALIDATED` / `BACKTESTED` / `EXPERIMENTAL` / `REJECTED`。
- 失败不是错误，而是研究结果（`docs/25` §六十一）：`Strategy cannot be formalized` 必须显示无法形式化的原因，并给出需要用户确认的候选选项（例：1. EMA20 2. 固定止损 3. ATR Stop 4. 自定义规则）。
- 唯一「模型不能自己跨过」的点是人工确认（用户决策 C10、`docs/25` §十二、§六十）。

## §D StrategySpec 扩展与向后兼容

### D.1 V1 DSL 仍是稳定契约

- `backend/app/strategies/dsl.py:255` 的 `StrategySpec` 是引擎契约，`extra="forbid"`（`backend/app/strategies/dsl.py:258`），全仓 104 处引用，`schema_version` 1.0。
- 用户决策 **C1 = A 方案**（`docs/26` §19）：**`StrategySpec` 与 `schema_version` 不动**；研究层新增 `ResearchArtifact → StrategyHypothesis → StrategyDraft → StrategyCompiler → StrategySpec 1.0`。
- `docs/04_STRATEGY_DSL.md` 已明确 DSL 是核心稳定契约，本次继续坚持；**不要为了扩展而破坏现有 V1 DSL**（`docs/25` §八）。
- `docs/04_STRATEGY_DSL.md:130-137` 明确拒绝 `filters` / `outputs` 顶级块——研究层可以**表达**这类思想，但**编译不得产出这些块**，Registry 把它们标为 unsupported（`docs/26` §5.2）。

### D.2 研究层实体承载新语义（不进入 DSL）

| 计划字段 | 承载位置 | 版本 | 说明 |
| --- | --- | --- | --- |
| `source` / `provenance` | `research_artifacts` / `strategy_hypotheses` / 既有 `StrategyVersion.source_commit` | v1.9.8 | 外部策略必须保存 provenance（系统契约第 16 条） |
| `hypothesis` | `strategy_hypotheses.hypothesis_json` | v1.9.8 | Layer B 的研究结论 |
| `assumptions` / `unknowns` | `strategy_hypotheses` + `strategy_hypothesis_rules.origin`（`ASSUMED` / `UNKNOWN`） | v1.9.8 | 假设不得伪装成原策略规则 |
| `evidence` / `confidence` | `research_artifact_fragments` + `strategy_hypothesis_rules.evidence_fragment_ids` / `confidence` | v1.9.8 | 证据与规则绑定；AI confidence ≠ 统计置信度 |
| `limitations` | `strategy_drafts.capability_report_json`（不支持项与原因） | v1.9.8 | 能力边界必须显式输出 |
| `market` / `universe` / `portfolio` / `position_sizing` / `execution` | DSL 1.0 已有 `market` / `execution` / `risk`（含 sizing）；`universe` / `portfolio` 无 | 部分 | `universe` / 横截面排名 / 再平衡 / 杠杆 / T+1 / 涨跌停 **当前不支持**，必须 `UNSUPPORTED` 或 `PARTIALLY_SUPPORTED` |

### D.3 计划中的扩展字段清单（`docs/25` §八）

`metadata`、`source`、`provenance`、`hypothesis`、`market`、`universe`、`timeframe`、`indicators`、`features`、`filters`、`entry`、`exit`、`risk`、`position_sizing`、`execution`、`portfolio`、`parameters`、`assumptions`、`unknowns`、`evidence`、`confidence`、`limitations`。

其中 `market` / `timeframe` / `indicators` / `features` / `entry` / `exit` / `risk` / `position_sizing` / `execution` / `parameters` 在 DSL 1.0 中**已有对等表达**；其余字段按 D.2 落在研究层实体上，或明确标记为不支持。

### D.4 向后兼容规则

1. **只增不改**：既有字段名、语义、`extra="forbid"` 行为不变。
2. **新增必须可选**：任何新字段进入 DSL 前必须能对旧数据与旧策略保持可解析、可回测。
3. **迁移必须可 `downgrade`**，且不得触碰不可变守卫（`backend/app/domain/immutability.py`、`0002_immutability`、`0010_immutability`）。
4. **策略版本不可变**（`docs/25` §四十二）：V1 不被 AI 修改，演进路径只能是 `V1 → AI Research → V2`。
5. **兼容性证据**：`backend/app/strategies/validator.py` 的 `validate_strategy` 与 `backend/tests/test_dsl_indicators.py` / `backend/tests/test_strategies.py` 是回归基线。

## §E 数据模型与 API 增量

### E.1 v1.9.7 / v1.9.8 已落地（事实）

- 新表 `ai_role_contracts`：角色契约的运行时索引（源仍是 `backend/app/ai/contracts/*.md`）。
- `ai_tasks` 新增五个可空列：`role`、`output_hash`、`source_ids_json`、`research_run_id`、`strategy_version_id`。
- 迁移：`backend/alembic/versions/0012_ai_role_contracts.py`。约束：只加列、不删不改既有列；同时支持 SQLite 与 PostgreSQL 16。
- v1.9.8 迁移 `0013_research_layer` 已落地：`backend/alembic/versions/0013_research_layer.py`（`down_revision = "0012_ai_role_contracts"`）。
- v1.9.8 新表（6）：`research_artifacts`、`research_artifact_fragments`、`ai_research_runs`、`strategy_hypotheses`、`strategy_hypothesis_rules`、`strategy_drafts`；`ai_source_snapshots` **未建**。
- 迁移链现状：`0001_initial → 0002_immutability → 0003_fix_triggers_json → 0004_resource_monitor → 0005_signal_outcome_times → 0006_resource_pk_sqlite → 0007_backtest_result_warnings → 0008_github_pending_review → 0009_drop_dead_schema → 0010_immutability → 0011_signal_closes_direction → 0012_ai_role_contracts → 0013_research_layer`。
- 计划中的后续迁移：`0014_ai_workflow_audit`（v1.9.9）、（条件）`0015_metrics_*`（Future，必须先有指标定义与测试规范）。

### E.2 研究层表（六张已建，其余设计已定稿、未实现）

| 模型 | 用途 | 版本 |
| --- | --- | --- |
| `ai_research_runs` | 一次研究（多阶段、可追溯） | **v1.9.8 已建**（异步工作流与人工确认待 v1.9.9） |
| `research_artifacts` | 原始研究材料（Layer A） | **v1.9.8 已建** |
| `research_artifact_fragments` | 引用片段（证据绑定的落点） | **v1.9.8 已建** |
| `strategy_hypotheses` | AI 对材料的理解（Layer B） | **v1.9.8 已建** |
| `strategy_hypothesis_rules` | 逐条规则的来源与能力状态 | **v1.9.8 已建** |
| `strategy_drafts` | 可编译但不可执行的中间结构 | **v1.9.8 已建** |
| `strategy_experiments` | V1 → 实验 → V2 的迭代记录 | v1.9.9（未建） |
| `ai_tool_calls` | 工具调用审计 | v1.9.9（未建） |
| `ai_source_snapshots` | URL / PDF 抓取快照（对标 `github_snapshots`） | **未建**（原计划 v1.9.8，随统一来源推迟到 v1.9.9） |

**明确不新增**：Capability Registry 不建表。

### E.3 数据保留策略（用户决策 Q6）

- 默认**不保存完整外部原文**：GitHub / 网页 / 论文默认只存 `source_type` / `uri` / `title` / `author` / `provider` / `source_hash` / `snapshot_hash` / `retrieved_at` / `content_length` / `content_excerpt`（≤ 500 字符）/ `metadata`。
- 500 字符**不是绝对硬限制**：用户主动输入（例：粘贴 5000 字笔记）可完整保存；用户上传且明确拥有/授权的资料可完整保存，但需要 retention policy 控制。
- 可追溯链必须成立：`ResearchArtifact → source_hash → snapshot → StrategyHypothesis → StrategyDraft → DSL`。

### E.4 API 增量

**v1.9.7 已实现（`docs/12_API_SPEC.md` 已登记为 `[已实现]`）**：

| 端点 | 用途 |
| --- | --- |
| `GET /ai/capabilities` | 能力注册表：指标 / 特征 / 算子 / 成交模型 / 风控模型 / 仓位模式 / 指标口径 / 数据源 / 分析引擎 + 明确不支持的能力与原因（ADR-151）。AI 生成前必须先读它 |
| `GET /ai/roles` | 解析 `backend/app/ai/contracts/*.md`，返回 `name` / `role` / `version` / `task_types` / `prompt_names` / `required_capabilities` / `output_language` / `content_hash` / `source_path` + `indexed` 标记（ADR-150） |
| `GET /ai/audit/{task_id}` | provider / model / role / prompt 版本与哈希、输入输出哈希、token 与成本、`source_ids`、关联策略版本（ADR-153） |

**v1.9.8 已实现（`docs/12_API_SPEC.md` 的 AI Research 一节已登记为 `[已实现]`）**：

| 端点 | 用途 |
| --- | --- |
| `POST /ai/research` | 研究输入 → RESEARCHER → StrategyHypothesis → STRATEGY_ARCHITECT → StrategyDraft → 能力裁决（不生成可执行策略、不跑回测）；body `{question, sources:[{label?,kind?,source_ref,text}], model?}`，`question` 3–4000 字符、`sources` 1–8；答案不合规仍返回 200 且 `status="rejected"`；未配置 provider 返回 503 |
| `GET /ai/research` | 研究运行摘要列表（`limit` 默认 20、上限 100） |
| `GET /ai/research/{run_id}` | 单次研究详情；未知 `run_id` 返回 404 |
| `POST /ai/strategy/formalize` | body `{run_id}` 或 `{hypothesis_id}` → `{"draft": …}`；两者都缺返回 400、id 未知返回 404、答案不是草案返回 422 |

**规划中（尚未在 `docs/12_API_SPEC.md` 登记，均为 v1.9.9）**，按 `docs/26` §8：

| 端点 | 版本 |
| --- | --- |
| `POST /ai/sources/text`、`POST /ai/sources/url`、`POST /ai/sources/pdf` | v1.9.9 |
| `GET /ai/research/{run_id}/artifacts`、`GET /ai/research/{run_id}/hypothesis`、`GET /ai/research/{run_id}/draft`、`POST /ai/research/{run_id}/confirm` | v1.9.9 |
| `POST /ai/strategy/drafts/{id}/compile`（**需人工批准**） | v1.9.9 |
| `POST /ai/backtests/{run_id}/analyze` | v1.9.9 |
| `POST /ai/explain`（统一解释入口） | v1.9.9 |
| `POST /ai/experiments`（写库需人工批准） | v1.9.9 |

**登记纪律（必须遵守）**：`docs/12_API_SPEC.md` 第 5 行规定每行端点声明带且只带一个标记（`[已实现]` / `[计划]` / `[取消]`），并由 `backend/tests/test_api_spec_truth.py` 与 `create_app().openapi()` 的真实路由表**双向绑定**——写了却没服务的 `[已实现]` 会失败，服务了却没写的路由也会失败。因此：

- `docs/12` 的 `[计划]` 在 v1.9.7 生成时有两条：`GET /backtests/comparisons/{comparison_id}`（第 84 行）与 `POST /ai/tasks/{task_id}/cancel`（第 625 行）；v1.9.8 的四条研究层端点已在实现后登记为 `[已实现]`。
- 上表「规划中」的端点**必须先在 `docs/12` 加 `[计划]` 行**，实现后再改为 `[已实现]`；在此之前仓库里不应存在这些路由。

**明确不改动**：`/api/v1/research/*`（量化研究）、前端 `/research`、既有 AI 解释端点（向后兼容，用户决策 C9）。命名空间统一为 `/api/v1/ai/*`。

## §F UI 增量

### F.1 `/lab`「AI 研究实验室」

- 新路由 `/lab`，面向**普通用户开放**，**不进高级模式**（用户决策 Q2）：AI 研究台是核心产品能力，不是开发者功能。
- 用户看到的名字是「**AI 研究实验室**」；内部技术名仍是 **AI Quant Research Layer**。
- **已交付（v1.9.8，只读第一版）**：`frontend/src/views/LabView.vue`，在 `frontend/src/main.ts` 注册为 `{ path: '/lab', name: 'lab', component: LabView }`，并在 `frontend/src/App.vue` 作为第 5 个导航项（普通与高级模式都可见）。第一版覆盖 `研究输入 → StrategyHypothesis → StrategyDraft → 能力裁决`：普通模式只显示人话结论与下一步；`origin` 徽标、能力 token、run id、attempts、当前步骤与 JSON 只在高级模式显示。例外：`origin === 'ASSUMED'` 的规则在**两种模式下都**标注「⚠️ AI 提出的假设，不是你的原话」；草案卡片始终说明它不可运行、从未回测、没有收益 / 回撤 / Sharpe 数字。
- **第一版刻意不做**：不编译成 StrategySpec、不触发回测、不产出任何绩效数字、没有人工确认与异步状态机、没有来源摄取（text / url / pdf / github）、没有完整溯源视图。
- 计划中的页面结构（`docs/25` §四十、`docs/26` §13）：研究资料 → AI 分析 → 策略假设 → 形式化 → 能力检查 → 确认 → 编译 → 回测 → 分析 → 迭代。
- 计划中的阶段可视化：`Researching → Extracting → Checking capabilities → Building draft → Validating → Waiting for approval → Backtesting → Analyzing → Completed`；可复用既有 `/ai/tasks/{id}/status` 轮询模式。
- 溯源视图（Martin 场景）：原始资料 → AI 理解 → EXPLICIT / INFERRED / ASSUMED / UNKNOWN → Draft → DSL → 回测结果。
- 版本：`/lab` 的只读第一版 v1.9.8 **已完成**，完整状态机 v1.9.9，完整研究 UI 与溯源视图 v2.0.0。

### F.2 普通模式 vs 高级模式的可见性边界

- 现有机制：`frontend/src/mode.ts`（localStorage 键 `mql-mode`，默认普通模式），开关**只改变显示的东西，不改变计算的东西**；两种模式下引擎读到同一份策略、同一份数据集、同一个参数，回测结果哈希一致（`docs/13_UI_UX.md` §10）。
- 普通模式**不应出现**（`docs/25` §六十九、§二十）：`LLM`、`Prompt` / `prompt_version`、`Token`、`Provider`、`Function Calling` / `tool_trace`、`StrategySpec`、`AST`，以及 `source_hash`、`Capability Registry`、Research Artifact / Strategy Hypothesis / Strategy Draft / Provenance / Evidence / AI Confidence 等技术名。
- 普通模式**应该看到**（人话）：研究资料、策略想法、AI 分析、策略方案、回测、风险、结论、下一步实验、继续研究。
- 高级模式**可以展开**：StrategySpec、Evidence、Assumptions、AI reasoning summary、Tool trace、Audit、Model & Provider。
- 现状与欠账（`docs/26` §0 第 11 条、§13）：
  - 现有 4 个 AI 解释面板（Dashboard / Signals / StrategyDetail / Backtest）**保持普通模式可见**——它们是人话解释（用户决策 Q3）。
  - 但这 4 个视图的 AI 面板目前**没有**做高级模式门控，只有 `frontend/src/views/SettingsView.vue:723,727` 是正确的；`docs/13_UI_UX.md` §10 的隐藏清单需要扩充新专业词，并沿用 `backend/tests/test_frontend_contracts.py` 的读源码断言风格加守卫（含现有的欠账清单守卫 `test_the_outstanding_list_covers_exactly_the_unfinished_sections`）。
- `Strategy Detail` **不重做九段**（`frontend/src/views/StrategyDetailView.vue`，ADR-114），只新增「AI 研究」分区（`docs/25` §四十一）。
- 验收方式教训（ADR-144）：一个词表项被某个页面用上了，不等于每个渲染它的调用点都翻了——守卫必须写成**对调用点**的断言。

## §G 安全与测试要求

### G.1 Prompt injection 的数据边界（ADR-153，`docs/25` §六十三）

固定的消息顺序：

```text
SYSTEM / ROLE CONTRACT  →  TRUSTED TASK  →  UNTRUSTED SOURCE
```

- **来源永不覆盖契约**：`UntrustedSource` / `wrap_untrusted()` / `assemble_messages()` 把来源渲染成**最后一条 user 消息**，system 消息里只有 SYSTEM 契约与角色契约。
- GitHub 源码、网页、PDF、文章里出现的 `Ignore previous instructions...`、`Forget the system rules...`、`Execute this command...` 都是**资料内容**，不是系统指令。
- 模型输出同样是不可信输入，必须过四级校验：Schema → Domain → Capability → Safety（`docs/25` §六十四）。现状是两级：`validate_structured_output()` / `validate_structured_dict()`（`backend/app/ai/provider.py`）与 `backend/app/strategies/validator.py` 的 `validate_strategy`。
- 计划：加显式分隔标记、来源体积上限、控制字符清理、截断策略（并在 UI 说明已截断）。

### G.2 GitHub 代码不可信（`docs/25` §二十二、§五十）

- 不在 API 进程 `import` 用户代码；不直接执行 shell；不给 GitHub 代码 NAS 权限；不给宿主机写权限；默认不提供网络。
- 若未来必须执行，条件全部满足才允许：isolated worker、non-root、network disabled、resource quota、read-only source、destroy after execution。
- 优先 static analysis + AI extraction。
- 现有约束（已实现）：`backend/app/importer/{sanitize,github_client}.py`、快照固定到 commit（ADR-060）、coverage / incomplete detection / parsing failure / license handling（ADR-056…ADR-059）全部保留，不得删除。

### G.3 AI 不直连数据库、不执行任意 Python 策略代码

- `AI → Tool Gateway → Domain Service`；AI 不能 `UPDATE database`。
- 禁止链路 `AI → arbitrary Python → execute`；正确链路 `AI → declarative StrategySpec → Validator → Engine`。
- 不存在「AI 直接 SQL」与「AI 执行任意代码」的实现路径，测试必须能证明这一点。

### G.4 超大来源与工具滥用

- 来源：大小上限、超时、内容截断、`file://` 与私网地址拒绝。
- URL 摄取必须处理 **SSRF**：禁 `localhost` / loopback / 私网 IP / `file://`；禁重定向到私网；超时；下载上限；robots / ToS；快照留存。
- PDF：纯文本优先；扫描件返回 `UNSUPPORTED` / `PARSE_FAILED`，**不得假装看懂**。
- 工具滥用：读工具默认开放；重任务需要用户确认或配额；**写工具不存在**；参数扫描必须按实际计算规模折算配额（防止 1 次调用扩成几百次计算）。
- 资源上限与 `backend/app/infrastructure/resource_monitor.py`、`docker-compose.yml` 的服务限制对齐；限流复用 `backend/app/infrastructure/rate_limit.py`。
- 安全声明必须与真实部署一致（既有先例：`backend/tests/test_boundary_claims.py`、`docs/14_SECURITY_LICENSE.md`）。

### G.5 测试清单

| 测试族 | 断言方向 | 先例 / 落点 |
| --- | --- | --- |
| Provider | OpenAI-compatible 接入、结构化输出、无效 JSON、超时、重试、预算；**换模型必须 miss 缓存** | `backend/tests/test_ai_providers.py`、`backend/tests/test_ai_router.py:18-62` |
| Role Contract | 角色加载、prompt 版本、内容 hash、`required_capabilities` 匹配、缺失能力拒绝、六类 `ContractError` | `backend/tests/test_ai_role_contracts.py`（v1.9.7 新增） |
| Capability Registry | drift test（声称支持但代码没有 / 代码新增但 Registry 未收录 / 资产类别与周期集合与行情源不一致）、`UNSUPPORTED` 不静默降级 | `backend/tests/test_capabilities.py`（v1.9.7 新增） |
| Research | source → hypothesis、证据绑定、unknowns、confidence、三态区分；五族共 55 例 | `backend/tests/test_ai_research.py`（16 例）、`backend/tests/test_ai_strategy_draft.py`（17 例）、`backend/tests/test_ai_research_security.py`（22 例），共享脚手架与演示答案在 `backend/tests/research_payloads.py`（v1.9.8 新增）；族名：Researcher（`test_researcher_*`）、Architect（`test_strategy_architect` / `test_strategy_draft_schema` / `test_strategy_draft_provenance` / `test_strategy_draft_does_not_execute`）、Capability（`test_supported_capability` / `test_partially_supported_capability` / `test_needs_capability` / `test_no_silent_downgrade`） |
| Compiler | 合法/非法 StrategySpec、unsupported capability、缺 exit、歧义规则；**不存在 AI → Python 执行路径** | `backend/tests/test_strategies.py:39-90`、`backend/tests/test_dsl_indicators.py` |
| 回测不可篡改 | AI 不能修改结果、AI 只能读取 structured facts、分析只 echo 库中数字、facts 函数不含统计量 | `backend/tests/test_ai_explain.py:295-304`、`:278-281` |
| 安全 | v1.9.8 已建：五类注入样本（"Ignore previous instructions" / "Reveal system prompt" / "Execute this command" / "Change strategy rules" / "Pretend this capability exists"）作为 ResearchArtifact 内容，断言不能改变 Role Contract；AI 输出完整性（伪造 CAGR / 伪造 Sharpe / 伪造回测结果 / 不支持的指标一律拒绝或标为非事实）；工具守卫：`backend/app/ai/research.py` 不含 `run_backtest` / `BacktestEngine` / `walk_forward` / `run_monte_carlo` / `run_sensitivity` / `StrategyVersion(` / `BacktestRun(` / `subprocess` / `os.system` / `eval(` / `exec(` / `import httpx` 或直接 `structured_output(` 调用，且必须含 `run_task(`。仍待 v1.9.9：GitHub malicious repository、超大来源、工具滥用、SSRF 黑名单 | `backend/tests/test_ai_research_security.py`（22 例，v1.9.8 新增）；注入先例 `backend/tests/test_ai_runtime.py` |
| AI Runtime / 审计 | provider / model / role / prompt_version / hash / source_ids / tool_calls 完整；无密钥、无全文 | `backend/tests/test_ai_runtime.py`、`backend/tests/test_ai_providers.py:156` |
| 前端契约 | 普通模式不出现新专业词；阶段状态文案；中文提示 | `backend/tests/test_frontend_contracts.py:519-541,960-970`、`backend/tests/test_ui_promises.py:408-473` |
| 规模纪律 | 整仓时长可控（v1.9.6 读数：955 passed / 4 skipped / 约 180s） | `scripts/Invoke-Tests.ps1` |

## §H Phase 0–12 路线图与当前进度

### H.1 Phase 表（`docs/25` §八十）

| Phase | 内容 | 对应版本 | 状态 | 证据 / 说明 |
| --- | --- | --- | --- | --- |
| Phase 0 | 现状审计 + Gap Analysis | — | **已完成** | `docs/26_AI_QUANT_LAYER_GAP_ANALYSIS.md`（570 行；只读审计、未改动任何产品代码，仅新增 `docs/25`、`docs/26` 与 `.gitignore` 的 `.scratch/`） |
| Phase 1 | AI Role Contract + AI Runtime 基础 | v1.9.7 | **已完成** | `backend/app/ai/contracts/*.md`、`backend/app/ai/role_contracts.py`、`backend/app/ai/runtime.py`、`backend/app/ai/budget.py`、`backend/app/capabilities.py`、迁移 `0012_ai_role_contracts`、ADR-150…153 |
| Phase 2 | Capability Registry + StrategySpec 扩展 | v1.9.7 起 | **部分完成**：Registry 骨架已完成（14 组 + 不支持清单 + 三态评估 + drift test），并在 v1.9.8 用于 StrategyDraft 的服务端能力裁决；**StrategySpec 扩展不实现**（按 C1 = A 方案，DSL 1.0 不动，扩展落在研究层实体） | `backend/app/capabilities.py`、`backend/tests/test_capabilities.py`；`docs/26` §5.1/§5.2 的裁决 |
| Phase 3 | AI Strategy Research + Strategy Formalization | v1.9.8 | **已完成（v1.9.8，停在草案 + 能力裁决）** | `backend/app/ai/research_schemas.py`、`backend/app/ai/research.py`、迁移 `0013_research_layer` + 六表、四条端点（§E.4）、ADR-154…157；`RESEARCHER.md` / `STRATEGY_ARCHITECT.md` 已升到 1.1.0；**不做** Strategy Compiler 与任何回测入口 |
| Phase 4 | GitHub / Web / PDF Research Source 统一 | v1.9.9 | **待开发** | `ResearchSource` 抽象与 `POST /ai/sources/text\|url\|pdf`、`ai_source_snapshots`；GitHub 侧复用既有 importer（ADR-060/061/062） |
| Phase 5 | Tool Gateway + Backtest / Risk / Research 工具 | v1.9.9 | **待开发** | `ai_tool_calls`、读/重/写三档、重任务需确认或配额（用户决策 C6） |
| Phase 6 | AI Explanation + Plain Language | v1.0 起 / v1.9.9 | **部分完成**：信号解释与回测分析已完成（中文输出、schema 校验、429/503 错误映射）；统一 `/ai/explain` 入口待开发 | `docs/06_AI_LAYER.md` §16「已实现」 |
| Phase 7 | Strategy Experiment + Version Iteration | v1.9.9–v2.0.0 | **待开发** | `strategy_experiments`、`POST /ai/experiments`；策略版本不可变（§D.4） |
| Phase 8 | UI/UX 统一（`/lab`、普通/高级分层） | v1.9.9–v2.0.0 | **待开发** | `/lab` **只读第一版 v1.9.8 已完成**（§F.1）；完整状态机与溯源视图、四视图 AI 面板门控欠账见 §F.2 |
| Phase 9 | Security + Prompt Injection + Audit | v1.9.7 起 | **部分完成**：信任边界（ADR-153）+ 审计列 + `GET /ai/audit/{task_id}` 已完成；SSRF / 来源体积 / 工具滥用待开发 | `backend/tests/test_ai_runtime.py` 注入用例 |
| Phase 10 | 完整测试 | 每版 | **进行中**：v1.9.7 新增 Role Contract / Runtime / Capability 三个测试族；v1.9.8 新增研究层五族 55 例（§G.5）；端到端验收场景 1–7 + Martin 场景属 v2.0.0 | `docs/26` §15 |
| Phase 11 | 文档同步 | 每版 | **进行中**：v1.9.7 已重写 / 更新 `docs/06`、`docs/12`、`docs/15`、`docs/17` 与 `docs/02` / `docs/03` / `docs/11` / `docs/16` / `docs/19` / `docs/00`；v1.9.8 已同步 `docs/06`（§17/§18）、`docs/12`（AI Research）、`docs/13`、`docs/15`、`docs/17`（ADR-154…157）、`docs/19`、`docs/25`、`docs/26`（§22）与本文件 | §J |
| Phase 12 | 更新原始 Development Specification | v1.9.7 起（v1.9.8 刷新） | **已完成（本文件）** | `docs/My_Quant_Lab_Development_Spec_V1.1.md` + `.docx` + 根目录副本 + `scripts/build_dev_spec_docx.py`；V1.0 原件未动；本文件已按 v1.9.8 事实刷新（§0.1） |

### H.2 版本里程碑（`docs/26` §17）

| 版本 | 范围 | 关键交付物 | 迁移 | 部署 |
| --- | --- | --- | --- | --- |
| **v1.9.7** 基础 Role Contract + Runtime | §四、§五、§六、§三十二、§三十六、§三十七、§三十八、§十九（Registry 骨架）、§三十三、§三十五 | 契约文件、Contract Loader/Registry、Capability Registry 骨架 + drift test、缓存 key 修正、审计列、预算分层、`ai_role_contracts` | `0012` | 补丁版：不部署 NAS（ADR-086） |
| **v1.9.8** Research Layer + Hypothesis + Draft + Capability Verdict（停在草案 + 能力裁决） | §七、§九、§十、§十一、§十二、§十八、§二十、§二十三、§五十、§五十一、§五十二、§六十一 | **已交付**：`backend/app/ai/research_schemas.py`（领域模型 + 四个门 + `assess_draft_capabilities()` + `find_unverified_result_claims()`）、`backend/app/ai/research.py`（五步链，复用 v1.9.7 的 `run_task()`）、迁移 `0013_research_layer` + 六表、四条端点（`POST /ai/research`、`GET /ai/research`、`GET /ai/research/{run_id}`、`POST /ai/strategy/formalize`）、`origin` 三态 provenance（EXPLICIT / INFERRED / ASSUMED / UNKNOWN）、非可执行 `StrategyDraft`、服务端能力三态裁决、`RESEARCHER.md` / `STRATEGY_ARCHITECT.md` 升到 1.1.0、`/lab` 只读第一版、ADR-154…157、研究层五族 55 例测试。**未交付**：Strategy Compiler（Draft → StrategySpec 1.0）、任何 AI 触发的回测 / 风险 / 敏感性 / Monte Carlo 入口、Tool Gateway（`ai_tool_calls`）、统一研究来源（text / url / pdf / github、`ai_source_snapshots`）、`ai_research_runs` 的异步工作流与人工确认、`strategy_experiments`、四个 UI AI 面板门控、完整 `/lab`、`REVIEWER.md` | `0013` | 补丁版：不部署 NAS（ADR-086） |
| **v1.9.9** Compiler + Tool Gateway + Audit + Security + Workflow | §十七、§二十四、§二十五、§五十七、§五十八、§五十九、§六十、§六十二、§六十三、§六十四、§六十五、§五十三–§五十六、§四十三 | `StrategyCompiler`（Draft → DSL 1.0）、Tool Gateway（三档 + 配额 + 审计）、`ai_research_runs` / `ai_tool_calls` / `strategy_experiments`、异步研究工作流 + 人工确认、统一解释入口 | `0014` | 补丁版 |
| **v2.0.0** AI Quant Research Layer 里程碑 | §四十、§四十一、§四十二、§四十四、§四十五–§四十八、§四十九、§六十六、§六十九、§七十、§七十七、§七十八、§八十二 | 完整研究 UI + 溯源视图、实验与版本迭代闭环、验收场景全部可演示、文档同步、V1.1 docx + markdown 镜像 | — | **部署 NAS 并验收**（ADR-086） |
| **Future** | Calmar / Recovery / VaR / CVaR；Portfolio / Universe / Rebalance；Vision；RAG | 先定义后实现 | 视需要 | — |

说明：不创建 `v1.10.x`（用户决策 C2）；v1.9.7 / v1.9.8 / v1.9.9 都正常发 GHCR 镜像但**不自动部署 NAS**；`v2.0.0` = GHCR + 正式 NAS 部署候选（用户决策 Q8）。每个版本仍必须有 tests / CI / docs / release / tag。

### H.3 交付节奏（用户决策，`docs/26` §20.1）

- 开发顺序：Role Contract → AI Runtime 基础 → 统一 Budget → Audit 基础 → Capability Registry 基础 → 测试 → 文档 → CI → Tag。
- **不得提前实现** v1.9.8 / v1.9.9 / v2.0.0 的功能。
- **每个版本完成后必须停止**，并给出：① 实际修改文件 ② 实际代码变更 ③ 测试结果 ④ CI 结果 ⑤ 文档变更 ⑥ Git diff ⑦ 版本号 ⑧ 下一版本计划；**等待用户确认后才进入下一版本**。
- v2.0.0 的核心验收链：`Martin 的研究资料 → AI 理解 → EXPLICIT / INFERRED / ASSUMED / UNKNOWN → Strategy Draft → Capability Validation → DSL 1.0 → Deterministic Backtest → Risk Analysis → AI Plain-language Explanation`，必须完整可追溯。

## §I 验收标准 A–N（`docs/25` §七十七）

| # | 标准 | 要求 | 当前状态 |
| --- | --- | --- | --- |
| A | Model Independence | 统一 Provider 接入 OpenAI-compatible，核心代码不依赖具体模型品牌 | **已完成**：`OpenAICompatibleProvider`（`backend/app/ai/provider.py`）；契约只声明能力不绑模型（ADR-150）；本期只实现 OpenAI-Compatible（用户决策 Q5） |
| B | Role Contract | 能按 Role 加载对应 AI 工作规范 | **部分完成**：4 份契约已就位并可解析（`SYSTEM.md` / `RESEARCHER.md` / `STRATEGY_ARCHITECT.md` / `EXPLAINER.md`）；`STRATEGY_COMPILER.md` / `BACKTEST_ANALYST.md` / `RISK_ANALYST.md` / `REVIEWER.md` 待开发 |
| C | Research | 用户可提交研究资料让 AI 分析策略 | **部分完成**（v1.9.8）：`POST /ai/research`、`GET /ai/research`、`GET /ai/research/{run_id}` 已实现并登记为 `[已实现]`；来源摄取 `POST /ai/sources/text\|url\|pdf` 与 GitHub / URL / PDF 统一仍是 v1.9.9 |
| D | Formalization | AI 能把策略思想转换为 StrategySpec | **部分完成**（v1.9.8）：`POST /ai/strategy/formalize` 已实现（`{run_id}` 或 `{hypothesis_id}` → `{"draft": …}`），但只产出**不可执行**的 `StrategyDraft`；编译成 StrategySpec 的 `POST /ai/strategy/drafts/{id}/compile` 仍是 v1.9.9 |
| E | Evidence | 每条重要规则尽可能有 provenance | **部分完成**（v1.9.8）：GitHub 导入已有 `source_commit` / `evidence_json` / provenance；研究层的逐规则证据绑定已落地（`research_artifact_fragments` + `strategy_hypothesis_rules.evidence_fragment_ids`），EXPLICIT / INFERRED 必须来自本次运行输入的证据 |
| F | Uncertainty | 能区分 EXPLICIT / INFERRED / ASSUMED / UNKNOWN | **已完成**（v1.9.8）：`strategy_hypothesis_rules.origin` ∈ EXPLICIT / INFERRED / ASSUMED / UNKNOWN；EXPLICIT / INFERRED 必须带本次输入证据，ASSUMED 必须被 `assumptions[].applies_to` 覆盖，UNKNOWN 必须被 `unknowns[].field` 覆盖，任何缺口即 REJECT（ADR-154） |
| G | Capability | AI 知道当前系统支持什么；不支持时明确报告而不是静默生成 | **已完成**（v1.9.7 Registry + v1.9.8 服务端裁决）：14 组能力 + `UNSUPPORTED_CAPABILITIES` + 三态 `assess()` + `GET /ai/capabilities`（ADR-151）；v1.9.8 起 StrategyDraft 的能力裁决由服务端按草案实际用到的能力计算（不采信模型自报），`SUPPORTED` / `PARTIALLY_SUPPORTED` / `NEEDS_CAPABILITY` 三态且无静默降级（ADR-156） |
| H | Validation | 任何 AI StrategySpec 必须过 schema + domain + capability validation | **部分完成**（v1.9.8）：研究层已有七步校验链（Model → Raw → JSON/Schema → Domain → Capability → Provenance → StrategyDraft，任一失败即 REJECT，最多一次受控重试，ADR-157）；可执行策略侧的 schema（`validate_structured_output()`）与 domain（`validate_strategy`）已有，capability / safety 两级待 v1.9.9 |
| I | Backtest | AI 不能伪造回测数字 | **已完成**：数字只由 `build_signal_facts()` / `build_backtest_facts()` 注入；`backend/tests/test_ai_explain.py:295-304` 断言 facts 不含统计量、分析只 echo 库中数字 |
| J | Explanation | 用户可获得专业分析 + 通俗解释 | **已完成**：信号解释 + 回测分析两条链（中文输出、`plain_language` 字段、429/503 映射）；统一入口待 v1.9.9 |
| K | Iteration | V1 → AI 分析 → 实验 → V2 | **待开发**：`strategy_experiments` 与 `POST /ai/experiments` 计划 v1.9.9 |
| L | Security | 外部 GitHub / Web / PDF 内容不能覆盖 AI System Contract | **部分完成**（v1.9.8）：信任边界（ADR-153）与注入守卫已完成；v1.9.8 新增 `backend/tests/test_ai_research_security.py`（22 例，五类注入样本作为 ResearchArtifact 内容，断言不能改变 Role Contract）；URL / PDF 摄取、SSRF、来源体积待 v1.9.9 |
| M | Audit | AI 调用能追踪 model / provider / role / prompt / source / tool / result | **部分完成**（v1.9.8）：`ai_tasks` 五个新列 + `GET /ai/audit/{task_id}`（v1.9.7）；v1.9.8 的 `research_run_id` 参数与 `audit_payload()` 新增 `source_snapshot_hash` / `strategy_draft_version` / `tool_calls: []`（本版不发起工具调用，空列表本身是记录的一部分）；真实 `tool_calls` 待 v1.9.9 的 `ai_tool_calls` |
| N | Documentation | 开发计划、架构、DSL、AI、API、UI、Roadmap 与实际实现一致 | **进行中**：`docs/06` 已重写，`docs/12` / `docs/15`（v1.9.7 版本行与读数已写入）/ `docs/17` 已更新；v1.9.8 已完成一轮同步（`docs/06` / `docs/12` / `docs/13` / `docs/15` / `docs/17` / `docs/19` / `docs/25` / `docs/26`，见 §J）；本 V1.1 即 Phase 12 的交付物 |

## §J 文档同步与维护规则

### J.1 `docs/00…26` 的职责

| 文档 | 职责 | V1.1 相关状态 |
| --- | --- | --- |
| `docs/00_README.md` | 文档包入口与项目定位 | 已补「AI 量化研究智能层（进行中）」一节与定位升级（v1.9.7） |
| `docs/01_PRODUCT_SPEC.md` | 01 产品规格（PRD） | 已完成 |
| `docs/02_ARCHITECTURE.md` | 02 系统架构（6 层） | 已补四层 AI 架构图与三条不许越过的线（v1.9.7）；Research Layer 的实体层同步待 v1.9.9 |
| `docs/03_MODULES.md` | 03 功能模块规范（M01–M13） | 已补 M09 AI 的四层职责与 `app/capabilities.py`（v1.9.7） |
| `docs/04_STRATEGY_DSL.md` | 04 统一策略规范；DSL 是核心稳定契约 | **只加**「研究层产出 DSL 1.0」的分层说明；契约本身不改（v1.9.8） |
| `docs/05_GITHUB_STRATEGY_IMPORT.md` | 05 GitHub 导入流水线、安全与许可证 | 待补「GitHub 是 `ResearchSource` 的第一个实现」，安全约束不变（v1.9.9） |
| `docs/06_AI_LAYER.md` | 06 AI 智能层：Provider、角色、契约、Runtime、Registry、预算、审计 | **已更新**（v1.9.7 重写；v1.9.8 新增 §17 研究层、§18 已实现 / 未实现清单） |
| `docs/07_BACKTEST_ENGINE.md` | 07 回测引擎口径与红线 | 新指标（Calmar / Recovery / VaR / CVaR）的定义与口径属 Future |
| `docs/08_PAPER_TRADING.md` | 08 模拟盘与账户隔离 | 已完成 |
| `docs/09_SIGNAL_ENGINE.md` | 09 信号生成、证据与生命周期 | 已完成 |
| `docs/10_GHOSTFOLIO_INTEGRATION.md` | 10 Ghostfolio 只读集成 | 已完成 |
| `docs/11_DATA_MODEL.md` | 11 数据模型与完整性规则 | 待补研究层新表（六表 v1.9.8 已建，文档同步待 v1.9.9） |
| `docs/12_API_SPEC.md` | 12 逐端点真相 + `[已实现]`/`[计划]`/`[取消]` 标记（由测试双向绑定） | **每版必更**；v1.9.7 已加三条端点，v1.9.8 已加 AI Research 一节与四条研究层 `[已实现]` 端点 |
| `docs/13_UI_UX.md` | 13 界面按现状记录 + §10 普通/高级门控 + §14 欠账表 | 已补 `/lab`：导航树 10 行含 `/lab`（v1.9.8）；§10 隐藏清单扩充新专业词仍待 v1.9.9 |
| `docs/14_SECURITY_LICENSE.md` | 14 密钥、沙箱、注入、审计、许可、不自动交易边界 | 待补 URL/PDF 摄取与注入边界（v1.9.9） |
| `docs/15_ROADMAP_ACCEPTANCE.md` | 15 版本表 + 每版真实读数 | 每版加行与读数；v1.9.7 与 v1.9.8 版本行与读数均已写入（工作树、门禁与红证据数字见该文件） |
| `docs/16_AGENTS.md` | 16 AI 编程契约（不可协商规则、开发顺序、测试底线） | 已补五条 AI 层代码位置约束与 AI 测试底线（v1.9.7） |
| `docs/17_DECISIONS.md` | 架构决策记录（ADR），当前至 ADR-157 | v1.9.8 新增 ADR-154…157（provenance、不可执行草案、服务端能力裁决、结果禁区）；工具三档、confidence 边界等 ADR 待后续版本（v1.9.7 起） |
| `docs/18_SAMPLE_STRATEGY.md` | 示例策略 PA Breakout V1 | 已完成 |
| `docs/19_DEVELOPMENT_PLAYBOOK.md` | AI 编程实施手册 | 已补 AI 层施工勾选项与「写文件必须 LF」教训（v1.9.7）、研究层勾选项与 §5.1 push / network 教训（v1.9.8） |
| `docs/20_RESOURCE_MONITOR.md` | 系统资源监控规格 | 已完成；与 Tool Gateway 的资源上限对齐（§G.4） |
| `docs/21_PARAMETER_SENSITIVITY.md` | 参数敏感性分析规格 | 已完成；是 AI 防过拟合提醒的证据来源 |
| `docs/22_MONTE_CARLO.md` | Monte Carlo 重采样规格 | 已完成；Risk Analyst 的输入之一 |
| `docs/23_POSITION_SIZING.md` | 仓位管理规格 | 已完成；`sizing_modes` 进入 Capability Registry |
| `docs/24_ENSEMBLE.md` | 策略集成规格 | 已完成；`compare_strategies` 工具的基础 |
| `docs/25_AI_QUANT_RESEARCH_LAYER_PLAN.md` | 用户提供的升级计划逐字副本，**权威输入** | 逐字内容保持原样，另附滚动实现状态（v1.9.8 已更新），作为长期引用锚点（用户决策 Q10） |
| `docs/26_AI_QUANT_LAYER_GAP_ANALYSIS.md` | 现状审计与差距分析，v1.9.7 起的施工依据 | 已完成；v1.9.8 新增 §22 落地状态 |
| `docs/README.md` | 开发文档包索引 | 新文档落盘后同步索引 |
| `docs/My_Quant_Lab_Development_Spec_V1.0.docx` | 原始开发规格 | **永久保留、不修改** |
| `docs/My_Quant_Lab_Development_Spec_V1.1.md` / `.docx` | 本文件与其 DOCX 镜像 | 唯一内容源是 `.md`，`.docx` 由脚本生成 |

### J.2 `docs/25` 章节号是长期引用锚点（用户决策 Q10）

- `docs/25_AI_QUANT_RESEARCH_LAYER_PLAN.md` 的章节号（§一…§八十二）是**长期引用锚点**：Gap Analysis、本 V1.1、后续开发文档、commit / PR 说明、验收测试都可以直接引用。
- 未来计划结构若发生重大变化，**不静默修改旧章节**，必须采用「**旧章节保留 + 新增修订章节 + 版本说明**」的方式。
- 本文件引用的所有 `§xx` 均指 `docs/25` 的章节号；引用 V1.0 章节时写「V1.0 的 xx 章」。

### J.3 文档同步的硬要求

1. **每个版本**同步 `docs/12`（端点真相）、`docs/15`（版本行与真实读数）、`docs/17`（新 ADR），并按 `docs/26` §16 的清单更新受影响文档。
2. **不要为「完整」而臃肿**：文档要写现状与可核查的证据，而不是把将来时写成既有事实。
3. **DOCX 纪律**（`docs/25` §七十六）：永远不删除、不覆盖历史版本的 DOCX；新版本新建文件并标注 `V1.0 = Original Development Specification` / `V1.1 = AI Quant Research Architecture Update`。
4. **生成器纪律**：`docs/My_Quant_Lab_Development_Spec_V1.1.md` 是唯一内容源；`.docx` 一律由 `scripts/build_dev_spec_docx.py` 生成，脚本必须可重复运行且确定性（无时间戳、无随机），只依赖 python-docx 与标准库。

## §K v1.9.8 落地状态（滚动更新）

`version.txt` = `v1.9.8`（补丁版本；按 ADR-086 补丁版**不部署 NAS**，但仍按流程走 tests / CI / docs / release / tag）。本版把 Phase 3（AI Strategy Research + Strategy Formalization）推进到**「草案 + 能力裁决」为止，并刻意停在这里**：系统能读懂材料、形成带 provenance 的 StrategyHypothesis、产出**不可执行**的 StrategyDraft 并给出服务端能力裁决，但**不会**生成可执行策略、**不会**触发回测、**不产出任何绩效数字**。v1.9.9 尚未开始。

**本版已交付**

- 后端模块：`backend/app/ai/research_schemas.py`（领域模型 + 四个门 + `assess_draft_capabilities()` + `find_unverified_result_claims()`）、`backend/app/ai/research.py`（五步链：研究输入 → RESEARCHER → StrategyHypothesis → STRATEGY_ARCHITECT → StrategyDraft → 能力裁决）。
- 迁移 `backend/alembic/versions/0013_research_layer.py`（`down_revision = "0012_ai_role_contracts"`），建六张表：`research_artifacts`、`research_artifact_fragments`、`ai_research_runs`、`strategy_hypotheses`、`strategy_hypothesis_rules`、`strategy_drafts`；`ai_source_snapshots` **未建**。
- 四条端点（`docs/12_API_SPEC.md` 已登记 `[已实现]`）：`POST /ai/research`、`GET /ai/research`、`GET /ai/research/{run_id}`、`POST /ai/strategy/formalize`。
- provenance：每条规则带 `origin` ∈ EXPLICIT / INFERRED / ASSUMED / UNKNOWN；EXPLICIT / INFERRED 必须来自**本次运行**的输入证据，ASSUMED 必须被 `assumptions[].applies_to` 覆盖，UNKNOWN 必须被 `unknowns[].field` 覆盖，任何缺口即 REJECT。Martin 场景「BTC 超跌后反弹时买入」必须给出 BTC / oversold / rebound / buy 为 EXPLICIT，定义 / 时间周期 / 出场 / 止损 / 仓位为 UNKNOWN；AI 提出的 `RSI(14) < 30` 再上穿 30 只允许标为 `ASSUMED`，并注明是 AI 提出的定义、不是用户原话。
- `StrategyDraft` 不可执行：`executable` 是服务端计算字段且恒为 false，`compiled_strategy_version_id` 保持 NULL，DSL 1.0（`backend/app/strategies/dsl.py`）**未改**；弱化或隐藏 EXPLICIT 规则会记录 `dropped_explicit_rule`。
- 能力裁决在服务端按草案实际用到的能力计算（不采信模型自报，强于实际的自报记录 `capability_overclaim` 并降级）；`NEEDS_CAPABILITY` 只在完全无可建项时给出，否则缺项为 `PARTIALLY_SUPPORTED`，全支持为 `SUPPORTED`，无静默降级。"20-day momentum + cross-sectional ranking + monthly rebalance top 10%" 必须读作 `PARTIALLY_SUPPORTED`（支持：20 日动量；缺能力：横截面排名 / 组合构建 / 月度再平衡），AI 的「只做单资产 20 日动量」建议必须显式标为 `Experimental Alternative` 并说明它与用户意图是**不同的策略**。
- 结果是模型禁区：研究 schemas 里没有 CAGR / Sharpe / Max Drawdown / 胜率字段，像「预计 CAGR 25%」这样的文字声明一律标 UNVERIFIED 且不落库；校验链为 Model → Raw → JSON/Schema → Domain → Capability → Provenance → StrategyDraft，任一失败即 REJECT，最多一次同门重试，被拒的运行不落 hypothesis / draft 行。
- Runtime 复用（不重写）：全部经 `run_task()`；`backend/app/ai/runtime.py` 新增 `research_run_id` 参数（记录在 `AITask` 上，不影响路由与缓存），`audit_payload()` 另返回 `source_snapshot_hash` / `strategy_draft_version` / `tool_calls: []`（本版不发起工具调用，空列表本身是记录的一部分）；`RESEARCHER.md` / `STRATEGY_ARCHITECT.md` 升到 1.1.0 并提供两份新的输出 JSON schema。
- ADR-154…ADR-157（`docs/17_DECISIONS.md`）：ADR-154 理解必须携带 provenance、ADR-155 StrategyDraft 不可执行且 DSL 1.0 不变、ADR-156 能力裁决在服务端三态且无静默降级、ADR-157 结果是禁区。
- UI：`/lab` 只读第一版（`frontend/src/views/LabView.vue`，在 `frontend/src/main.ts` 注册，并在 `frontend/src/App.vue` 作为第 5 个导航项「AI 研究实验室」，普通与高级模式都可见）；普通模式只显示人话结论与下一步，`origin` 徽标 / 能力 token / run id / attempts / 当前步骤 / JSON 仅高级模式；`ASSUMED` 规则在**两种模式下都**标注「⚠️ AI 提出的假设，不是你的原话」，草案卡片始终声明不可运行、从未回测、没有收益 / 回撤 / Sharpe 数字。
- 测试：五族 55 例——`backend/tests/test_ai_research.py`（16）、`backend/tests/test_ai_strategy_draft.py`（17）、`backend/tests/test_ai_research_security.py`（22），共享脚手架与演示答案在 `backend/tests/research_payloads.py`。

**本版未交付（下一步）**

- Strategy Compiler（Draft → StrategySpec 1.0）。
- 任何 AI 触发的回测 / 风险 / 敏感性 / Monte Carlo 入口。
- Tool Gateway（Phase 5，`ai_tool_calls`）。
- 统一研究来源（Phase 4：text / url / pdf / github 摄取、`ai_source_snapshots`）。
- `ai_research_runs` 的异步工作流与人工确认。
- `strategy_experiments`（Phase 7）。
- 四个 UI AI 面板（Dashboard / Signals / StrategyDetail / Backtest）的高级模式门控。
- 完整 `/lab`（Phase 8，含状态机与溯源视图）与 `REVIEWER.md`。
- v1.9.9 尚未开始。

权威的逐版读数在 `docs/15_ROADMAP_ACCEPTANCE.md`；细节见 `docs/06_AI_LAYER.md`、`docs/17_DECISIONS.md` 与 `docs/26_AI_QUANT_LAYER_GAP_ANALYSIS.md` §22。
