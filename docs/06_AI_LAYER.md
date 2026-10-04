# 06 AI Layer / AI 智能层

> 阅读约定：§1–§10 是 v1.0 的原始设计，保留原样；其中与今天实现不一致的地方在正文里标注了
> 「已实现」/「计划」。§11 起是 v1.9.7 落地的 AI Quant Research Layer 骨架
> （设计见 `docs/25_AI_QUANT_RESEARCH_LAYER_PLAN.md`，现状对照见
> `docs/26_AI_QUANT_LAYER_GAP_ANALYSIS.md`，决策见 `docs/17_DECISIONS.md` 的 ADR-150…ADR-153）。

## 1. 总体目标

让专业工作被 AI 和量化引擎承担，用户得到通俗、可操作、可追溯的结果。

## 2. Provider abstraction

原设计（伪代码，未按此实现）：

```python
class AIProvider(Protocol):
    async def generate_structured(...): ...
    async def generate_text(...): ...
```

已实现的形状：`backend/app/ai/provider.py` 的 `OpenAICompatibleProvider(base_url, api_key, name, *, timeout)`
提供同步的 `chat()` / `structured_output()` / `list_models()` / `health_check()`；调用方通过
`OpenAICompatibleProvider` 适配任何 OpenAI-compatible 端点，因此可以接入不同供应商，核心业务里
不出现品牌分支（`docs/25` §三/§三十五）。

配置来自数据库行 `ai_providers`（`backend/app/domain/models.py` 的 `AIProvider`）：

- `provider_type` / `base_url` / `api_key_encrypted`（加密存储，永不回传）
- `default_model` / `is_active`
- `daily_budget_usd`（**供应商层**预算，USD，见 §14）
- `settings_json`

进程级配置（`backend/app/core/config.py`）：`ai_daily_budget_usd`（全局层）、
`ai_task_budget_usd`、`ai_daily_task_limit`、`ai_task_timeout_seconds`。

## 3. AI 角色

契约文件在 `backend/app/ai/contracts/`（每个角色一个 markdown，ADR-150）。
「已实现」指该角色的任务类型已有真实调用路径；「计划」指契约已就位但还没有 API/UI 接线。

| 原设计编号 | 角色 | 任务类型 | 状态 |
| --- | --- | --- | --- |
| R1 | Strategy Researcher | `strategy_research` | 计划（`RESEARCHER.md` 已就位） |
| R2 | Quant Tutor | — | 计划 |
| R3 | Signal Explainer | `signal_explanation` | 已实现（`EXPLAINER.md` + `POST /signals/{id}/explain`） |
| R4 | Research Assistant | `strategy_formalization` | 计划（`STRATEGY_ARCHITECT.md` 已就位） |
| R5 | Daily Analyst | — | 计划 |

回测分析（`backtest_analysis`）由 R3 的同一份契约承担，见
`backend/app/ai/contracts/EXPLAINER.md` 的 `## Task: backtest_analysis`。

## 4. AI 不可信边界

- 输出必须经过 schema validation：`validate_structured_output()`（剥 ``` 围栏）+
  `validate_structured_dict()`（缺 required 键即报错）。
- AI 不得直接产生以下事实：当前价格、历史收益率、胜率、最大回撤、持仓数量、账户余额。
  这些数字只能由 `build_signal_facts()` / `build_backtest_facts()` 从数据库与回测结果里取，
  以 structured facts 的形式交给模型（`docs/25` §二 §2.1）。
- 模型输出本身也当作不可信输入，必须过 Schema / Domain / Capability 校验（`docs/25` §三十）。
- **外部内容同样是不可信输入**（v1.9.7，ADR-153）：GitHub 源码、网页、PDF、文章里出现的
  「Ignore previous instructions…」是资料内容，不是系统指令。`UntrustedSource` /
  `wrap_untrusted()` / `assemble_messages()` 把来源渲染成**最后一条 user 消息**，
  system 消息里只有 SYSTEM 契约与角色契约，来源永远无法覆盖它们。守卫见
  `backend/tests/test_ai_runtime.py` 的注入用例。

## 5. Prompt 分层

每个 AI 任务的消息组成（`assemble_messages()`）：

```text
system : SYSTEM.md 正文 + 角色契约的该任务段落
user   : Task Prompt（这一次要做什么）
       + Structured Data（系统给的事实）
       + Provenance Metadata
       + Untrusted Sources（资料原文，永远排在最后）
```

Prompt 必须版本化：每个契约文件在 front-matter 里有 `version`，`AIPrompt` 表按
`(name, version)` 登记，历史名 `signal_explain` / `backtest_explain` 由契约的
`prompt_names` 保留。缓存键里的 `prompt_hash` 是
`system.content_hash:contract.content_hash`——契约文件改一个字节，缓存即失效。

## 6. AI 输出协议

Signal Explanation（`SIGNAL_EXPLANATION_SCHEMA`）：

```json
{
  "summary": "...",
  "why": ["..."],
  "what_could_invalidate": ["..."],
  "what_to_watch_next": ["..."],
  "risk_notes": ["..."],
  "plain_language": "..."
}
```

Backtest Analysis（`BACKTEST_EXPLANATION_SCHEMA`）：

```json
{
  "summary": "...",
  "key_drivers": ["..."],
  "risks": ["..."],
  "what_to_watch_next": ["..."],
  "plain_language": "..."
}
```

不得要求模型输出"保证盈利""确定上涨等结论"。

## 7. Model routing

已实现（`backend/app/ai/provider.py` 的 `AIRouter`）：按 `task_type` 查 `_TASK_CAPABILITY`
得到所需能力层级，再在 `ModelOption` 列表里按 `cheap < standard < high` 的 `_tier_rank`
挑选，并受预算与 `preferred_model` 约束。

原设计的分层意图（Low cost：日报/基础解释；Medium：策略理解/一般回测解读；
High capability：复杂 GitHub 项目/复杂组合/异常分析）仍然成立。

计划（`docs/25` §三十一~§三十四）：路由从「模型名称」升级为
`Role + Capability + Cost + User Preference`，并以 `Model Capability Profile`
（structured_output / tool_calling / long_context / vision / reasoning /
code_understanding / web_research）与角色的 `required_capabilities` 做匹配。

## 8. Cache

已实现（`backend/app/ai/runtime.py` 的 `cache_key()`，ADR-153）：缓存身份由八项组成——

- `role`
- `provider` / `model`
- `prompt_hash`（SYSTEM + 角色契约的哈希）
- `tool_result_hash`
- `source_snapshot_hash`（`UntrustedSource` 集合排序后的 sha256）
- `strategy_version`
- 原 `input_hash()`（任务类型 + facts + schema 等）

任何一项变化都不命中缓存；同一请求二次调用直接复用 `AITask` 行，不重复计费。

## 9. 用户体验

优先展示：

> "发生了什么？"
> "为什么？"
> "需要关注什么？"

再提供：

> "查看专业数据"

## 10. AI Daily Budget

原设计写的是「¥2/day」三档，实现用的是 **USD**（Q1 的约定）：

- 全局层：`AI_DAILY_BUDGET_USD`（默认 2.0）
- 供应商层：`ai_providers.daily_budget_usd`（默认 2.0）
- 单任务层：`AI_TASK_BUDGET_USD`（默认 1.0）
- 每日任务数：`AI_DAILY_TASK_LIMIT`（默认 20）
- 单任务超时：`AI_TASK_TIMEOUT_SECONDS`（默认 600）

预算达到阈值以后：

- 仍允许量化计算（回测、信号、模拟盘不读 AI 预算）
- 停止非关键 AI 任务，抛 `BudgetExceeded`，API 映射为 HTTP 429
- 判定顺序与实测见 §14

## 11. 角色契约层（v1.9.7，ADR-150）

- 目录：`backend/app/ai/contracts/`
  - `SYSTEM.md`——18 条系统契约（`docs/25` §六）与最高原则（§七十九）
  - `RESEARCHER.md`、`STRATEGY_ARCHITECT.md`、`EXPLAINER.md`
- 解析：`backend/app/ai/role_contracts.py`
  - `parse_contract(text, *, path)`：front-matter（`name` / `role` / `version` /
    `task_types` / `required_capabilities` / `output_language` / 可选 `prompt_names`）
    + 正文里按 `## Task: <task_type>` 分段
  - `load_contracts()`（`lru_cache`）、`role_contracts()`、`system_contract()`、
    `contract_for_role(role)`、`task_output_schemas()`
  - `RoleContract`：frozen dataclass，`content_hash` 是文件字节 sha256，`ref` 形如
    `EXPLAINER@1.0.0`
  - `sync_role_contracts(db)`：把契约 upsert 进 `ai_role_contracts` 表（可追溯「这次调用用的
    是哪份契约的哪个版本」）
- 失败模式：缺 `SYSTEM.md`、缺 front-matter、声明了 `task_types` 却没有 `## Task:` 段落、
  段落名与声明不匹配、同一 `name` 出现两份——全部在加载期抛 `ContractError`。

## 12. AI Runtime（v1.9.7，ADR-153）

`backend/app/ai/runtime.py` 是所有 AI 调用的唯一入口：

- `estimate_tokens()` / `estimate_request_cost()` / `output_hash()`
- `cache_key()` / `source_snapshot_hash()`
- `run_task(db, request, *, providers, router_factory=None, prompt_hash="")`：
  缓存命中 → `guard()` 预算 → 建 `AITask`（status=running，写 `role` / `source_ids_json`）
  → 调用供应商 → 写 `output_hash` / `completed_at` → `record_usage()`
- `audit_payload(db, task)`：provider / model / role / 契约哈希 / 输入输出哈希 /
  token / 成本 / 来源 / 策略版本；`GET /ai/audit/{task_id}` 暴露

`AITask` 在 v1.9.7 增列：`role`、`output_hash`、`source_ids_json`、`research_run_id`、
`strategy_version_id`（迁移 `backend/alembic/versions/0012_ai_role_contracts.py`）。

## 13. 能力注册表（v1.9.7，ADR-151）

`backend/app/capabilities.py` 是 AI 的「知识边界」，全部**从代码派生**，不手抄：

| 组 | 来源 |
| --- | --- |
| `operators` | `backend/app/dsl/schema.py` 的 `ComparisonOp` 等枚举 |
| `indicators` | `backend/app/features/engine.py` 的 `SUPPORTED_INDICATOR_TYPES` |
| `features` / `price_action_features` | `FEATURE_CATALOGUE` |
| `fill_models` / `entry_order_types` | `ExecutionSpec.model_fields` |
| `sizing_modes` | `RiskSpec.model_fields` |
| `risk_models` / `execution_fields` / `market_fields` | 各 spec 的字段 |
| `metrics` | `backend/app/research/metrics.py` 的 `Metrics`（去掉 `notes` / `initial_capital`） |
| `timeframe_annualisation` | `BARRS_PER_YEAR` |
| `data_providers` | `PROVIDER_NAMES` |
| `analysis_engines` | 回测 / 敏感性 / Monte Carlo / 集成 / 信号 / 模拟盘模块文件 |

另外显式登记**不支持**的能力与原因：`UNSUPPORTED_CAPABILITIES`（如
`cross_sectional_universe`、`leverage`、`var_cvar`、`rag`、`live_execution`；
`short_selling` 是目前唯一的 `partially_supported`）。

`assess(requested)` 返回三态：

- `SUPPORTED`——请求的能力都在清单里
- `PARTIALLY_SUPPORTED`——有缺失或部分支持（缺什么、为什么在 `reasons` 里）
- `UNSUPPORTED`——全部缺失

AI 生成 StrategySpec 前必须先读它；遇到不支持的能力要报 `NEEDS_CAPABILITY`，不得自己发明实现
（`docs/25` §二十、§五十二）。`GET /ai/capabilities` 暴露同一份数据。

## 14. 预算与超时（v1.9.7，ADR-152）

`backend/app/ai/budget.py` 的 `decide()` 是唯一判定点，顺序固定：

```text
global(已耗尽) → provider(已耗尽) → task(本次估算 > 单任务上限)
→ global(本次成本) → provider(本次成本) → calls(今日调用数)
```

返回 `BudgetDecision(allowed, scope, reason, limit_usd, spent_usd, estimated_usd, remaining_usd)`；
`0` 表示该层整体禁用（不是「无限」）。`guard(db, ...)` 从 settings 读全局上限、单任务上限与
每日任务数。供应商层仍是 `ai_providers.daily_budget_usd`。用量行写在 `ai_usage`
（按 `usage_date` / `provider_id` / `model_id` / `task_type` 聚合）。

## 15. 新增 API 与配置（v1.9.7）

只读 API（`docs/12_API_SPEC.md` 有登记）：

- `GET /ai/capabilities`——能力注册表
- `GET /ai/roles`——角色契约（含 `indexed` 标记，读取时同步 `ai_role_contracts`）
- `GET /ai/audit/{task_id}`——单次调用的审计记录

配置（`.env.example` 与 `docker-compose.yml` 同步）：

- `AI_DAILY_BUDGET_USD`（已有）
- `AI_TASK_BUDGET_USD=1.0`
- `AI_DAILY_TASK_LIMIT=20`
- `AI_TASK_TIMEOUT_SECONDS=600`

## 16. 已实现 / 未实现一览（截至 v1.9.7）

已实现：

- Provider 抽象与路由、预算三层、缓存身份、审计
- 角色契约层（加载 / 校验 / 登记 / 两个只读端点）
- 能力注册表（14 组 + 不支持清单 + 三态评估）
- 解释链：信号解释、回测分析（中文输出、schema 校验、429/503 错误映射）
- 提示词分层、版本化与哈希

未实现（后续版本按 `docs/25` §八十的顺序推进）：

- `POST /ai/research` 与 `POST /ai/strategy/formalize`（Phase 3）
- 研究来源统一（GitHub / URL / PDF / 文本 → Research Artifact，Phase 4）
- Tool Gateway 与受控工具调用（Phase 5）
- 统一 Explanation API（Phase 6）
- 策略实验与版本迭代、迁移分析（Phase 7）
- `/lab` 研究界面（Phase 8）

## 17. AI 研究层：Research → Hypothesis → Draft → Capability（v1.9.8）

本节的规格来源是 v1.9.8 的二十节需求（Phase 3：策略研究与形式化）。**本版停在草案**：不做 Strategy Compiler，不跑回测 / 风险 / 敏感性 / Monte Carlo，不做 AI 回测分析师，不做完整 `/lab`，不做自动实验循环。

链路（ADR-154…ADR-157）：

```text
研究输入（question + 1–8 条材料）
  → RESEARCHER（契约 1.1.0）         → StrategyHypothesis（每条规则带 provenance）
  → STRATEGY_ARCHITECT（契约 1.1.0） → StrategyDraft（executable = false）
  → 服务端能力校验                   → SUPPORTED / PARTIALLY_SUPPORTED / NEEDS_CAPABILITY
```

四道门（全部集中在 `backend/app/ai/research_schemas.py`，测试直接对着这四道门写）：

1. **Schema 门**——pydantic 模型全部 `extra="forbid"`；缺键/多键被分类成 `schema_invalid` / `fabricated_metric` / `forbidden_content`。
2. **Domain 门**——`validate_hypothesis()` / `validate_draft()`：`EXPLICIT`/`INFERRED` 规则的 evidence 必须指向本次材料（`evidence_missing` / `evidence_unknown_source`）；`ASSUMED` 规则必须被假设覆盖（`assumed_not_disclosed`）；`UNKNOWN` 规则必须被 unknowns 覆盖（`unknown_not_disclosed`）；派生规则的 provenance 只准减弱（`provenance_stronger_than_hypothesis` / `new_rule_must_be_assumed`）；hypothesis 的 `EXPLICIT` 规则不得凭空消失（`dropped_explicit_rule`）。
3. **Capability 门**——`assess_draft_capabilities()` 在服务端裁决三态；模型自报更强记 `capability_overclaim`；替代方案必须标 `alternative_is_experimental`，且 `Experimental Alternative ≠ 用户原策略`。
4. **结果门**——递归扫描禁字段（`FORBIDDEN_METRIC_KEYS` / `FORBIDDEN_CONTENT_KEYS`），散文里的「预计 CAGR 25%」被标成 `UNVERIFIED` 而不是事实。

失败语义：任一失败＝`ResearchRejected` → run 状态 `rejected` 且 `violations_json` 逐条记录（`POST /ai/research` 仍返回 200：一份答得不合格的模型回答是资源，不是服务器错误）；`POST /ai/strategy/formalize` 用 422 让人看到 `step` 与违规码。允许**一次**受控重试，走同一套门；传输/供应商失败让 run `failed`。拒绝时不写 hypothesis / draft，只留 run 与失败的 `AITask`。

数据面（迁移 `0013_research_layer`）：`research_artifacts`、`research_artifact_fragments`、`ai_research_runs`、`strategy_hypotheses`、`strategy_hypothesis_rules`、`strategy_drafts`。本版材料由用户手输（1–8 条），还没有 GitHub / URL / PDF 的统一抓取（Phase 4）。

运行与审计：研究层的每一次模型调用都经 `run_task()`，v1.9.7 的预算、缓存身份与审计**一行都没有重写**（ADR-152/153）；`AITask.research_run_id` 把 run 与调用串起来；`audit_payload()` 的 `strategy_draft_version` 指向本次 run 产出的草案版本，`tool_calls` 恒为 `[]`——本版没有任何工具调用，空列表是记录而不是遗漏（规格 §15）。

API：四个端点（`POST /ai/research`、`GET /ai/research`、`GET /ai/research/{run_id}`、`POST /ai/strategy/formalize`），登记在 `docs/12_API_SPEC.md` 的 AI Research 一节。

UI：`/lab`「AI 研究实验室」（`frontend/src/views/LabView.vue`）——普通模式只给人话（AI 怎么理解、规则是什么、还缺什么、系统能不能做），高级模式才显示 provenance 徽标、能力 token、违规码与运行元数据；`origin = ASSUMED` 的规则在**两种模式**下都标注「AI 提出的假设，不是你的原话」，草案卡片常驻说明「不能直接运行、这一版没有跑过任何回测」。

## 18. 已实现 / 未实现一览（截至 v1.9.8）

已实现（在 §16 那一版之上新增）：

- 研究层四道门、五步数据流与两个新角色契约（`RESEARCHER` / `STRATEGY_ARCHITECT` 1.1.0）
- 四个端点：`POST /ai/research`、`GET /ai/research`、`GET /ai/research/{run_id}`、`POST /ai/strategy/formalize`
- 能力三态裁决、`capability_overclaim` 与「不静默降级」
- `/lab` 最小界面（研究输入 → AI 理解 → 策略假设 → 策略草案 → 能力检查）

仍未实现（按 `docs/25` §八十的顺序推进）：

- Strategy Compiler（Draft → StrategySpec 1.0），以及任何让 AI 触发回测 / 风险 / 敏感性 / Monte Carlo 的入口（规格 §2 明令禁止，测试与源码守卫一起钉住）
- 研究来源统一（GitHub / URL / PDF / 文本 → Research Artifact，Phase 4）
- Tool Gateway 与受控工具调用（Phase 5）——因此 `audit_payload()` 的 `tool_calls` 恒为 `[]`
- 统一 Explanation API（Phase 6）、策略实验与版本迭代（Phase 7）、完整 `/lab`（Phase 8）
