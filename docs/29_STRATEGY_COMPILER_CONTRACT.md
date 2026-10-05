# Strategy Compiler Contract（v2.2.0 / Phase 5 / Step 2A 契约冻结）

> 状态：**契约冻结（Step 2A）**。本文档是规范性文件（normative），不是建议。
> 归属：v2.2.0 / Phase 5 / Step 2A。本阶段**只冻结契约**：文档 + ADR + 形如「契约守卫」的测试。
> 上游：`docs/28_STRATEGY_COMPILER_GAP_ANALYSIS.md`（只读审计，1340 行）。
> 权威代码：`backend/app/strategies/dsl.py`、`backend/app/ai/research_schemas.py`、`backend/app/capabilities.py`、`backend/app/strategies/validator.py`、`backend/app/data/strategy_service.py`。

---

## 1. 文档目的与边界

### 1.1 目的

把 `StrategyDraft`（`backend/app/domain/models.py:916-942`，DDL 列见 `backend/alembic/versions/0013_research_layer.py`）到 `StrategySpec` 1.0（`backend/app/strategies/dsl.py:255-279`）的转换，冻结成**确定性、可审计、可测试**的契约，使后续实现阶段（Step 2B/2C）没有解释空间。

### 1.2 边界（本文档管什么）

1. **输入契约**：编译器允许读什么、禁止读什么（§4）。
2. **输出契约**：三个结果状态、产物形状、落库影响（§5、§8）。
3. **映射契约**：草案字段 → DSL 槽位（§6、§10、§11）。
4. **决策契约**：每个槽位由谁决定（§7），哪些必须由用户决定（§12）。
5. **拒绝契约**：15 个拒绝码与三态语义（§8、§9）。
6. **能力契约**：`expressible` 与 `honoured` 的区分（§13）。
7. **不变式**：StrategySpec 1.0 一个字段都不改（§14）。
8. **身份契约**：`compile_hash` 与 `immutable_hash` 各回答一个问题（§15）。
9. **可解释契约**：编译报告结构（§16）。
10. **验收契约**：Martin 场景的结论（§17）与 12 项测试（§18）。

### 1.3 与 docs/28 的关系

`docs/28` 是**审计与建议**（它记录缺口、给推荐顺序）。本文档是**冻结后的契约**。两者冲突时以本文档为准，并在 §5.4 逐条列出被取代的建议。

### 1.4 上位决策（不得违反）

| ADR | 位置 | 对编译器的约束 |
|---|---|---|
| ADR-003 | `docs/17_DECISIONS.md:15` | AI 是解释层，不是计算层 |
| ADR-005 | `docs/17_DECISIONS.md:25` | 策略版本不可变 |
| ADR-009 | `docs/17_DECISIONS.md:46` | 不下单、不接券商 |
| ADR-045 / ADR-046 | `docs/17_DECISIONS.md:517`、`:533` | sizing 语义、覆盖合并必须经校验 |
| ADR-151 | `docs/17_DECISIONS.md:2902` | 能力注册表从代码派生，不支持的就说不知道 |
| ADR-154 | `docs/17_DECISIONS.md:2938` | 理解必须带来源，AI 补的必须自认是假设 |
| **ADR-155** | `docs/17_DECISIONS.md:2950` | StrategyDraft 是不可执行的候选形式化；**DSL 1.0 本版零改动** |
| ADR-156 | `docs/17_DECISIONS.md:2962` | 能力判定由服务端计算，模型只能声明不能裁决 |
| ADR-157 | `docs/17_DECISIONS.md:2974` | 结果数字是模型禁区 |
| ADR-159 | `docs/17_DECISIONS.md:2999` | 证据必须有原文（EXPLICIT 引文服务端逐字核对） |
| ADR-160 | `docs/17_DECISIONS.md:3010` | 一个未解问题只回答一条规则（`unknown.rule_id`） |
| ADR-162 | `docs/17_DECISIONS.md:3034` | 模型调用只有一条路（`run_task()`） |

---

## 2. 本阶段（Step 2A）明确不做的事情

1. 不实现编译器：不创建 `backend/app/compiler/`、不写映射函数、不加枚举常量到产品代码。
2. 不新增/修改任何 API 端点、请求或响应 schema。
3. 不修改 `StrategySpec` 及其任何子模型（字段、类型、默认值、校验器一律不动，ADR-155）。
4. 不新增、修改、删除任何 Alembic migration；下一个可用编号仍是 `0016`。
5. 不修改研究层（`backend/app/ai/**`）、能力注册表（`backend/app/capabilities.py`）、验证器（`backend/app/strategies/validator.py`）。
6. 不修改任何既有测试的断言。
7. 不修改 `docs/12`、`docs/13`（它们各自被 `backend/tests/test_api_spec_truth.py:37`、`test_frontend_contracts.py:49` 的守卫约束）。
8. 不新增 UI、不改前端。
9. 不执行 `git commit` / `git tag` / push / Release / NAS 部署。
10. 不改变 `version.txt`（仍为 `v2.1.0`）。

**本阶段允许新增的文件（白名单）**：

- `docs/29_STRATEGY_COMPILER_CONTRACT.md`（本文档）
- `backend/tests/test_compiler_contract.py`（契约守卫测试）
- `docs/17_DECISIONS.md` 中新增一条 ADR（编号 **ADR-167**，紧接 ADR-166 `docs/17_DECISIONS.md:3088`）
- 其余文档的**追加式**当前状态说明（§见 Step 2A 交付物清单）

---

## 3. 编译器版本与 StrategySpec 版本的关系

### 3.1 两个版本号，两个问题

| 常量 | 值 | 回答的问题 | 位置 |
|---|---|---|---|
| `SCHEMA_VERSION` | `"1.0"` | 「这份 DSL 文档写的是哪一版语法？」 | `backend/app/strategies/dsl.py:37` |
| `COMPILER_VERSION` | `"1.0"` | 「这份 spec 是哪一版编译器、按哪一版映射规则生成的？」 | **本契约新增**（Step 2B 落到 `backend/app/compiler/contract.py`） |

**冻结结论**：两者**独立**。

- StrategySpec 1.0 冻结 ⇒ `SCHEMA_VERSION` 保持 `"1.0"`，编译器**不得**写入其它值；`validator.py:293-301` 的 `unsupported_schema_version` 是唯一的解释者。
- 编译器的映射规则将来可以演进（新增指标别名、新增 canonical 参数键），那时**升 `COMPILER_VERSION`，不升 `SCHEMA_VERSION`**。
- `COMPILER_VERSION` 必须进入编译产物身份：`compile_hash`（§15）与 `evidence_json.compile_report.compiler_version`（§16）。
- 禁止把 `COMPILER_VERSION` 写进 `dsl_json`：DSL 里没有这个字段（`StrategySpec` 是 `extra="forbid"`，`dsl.py:258`，写进去会被 pydantic 拒绝）。

### 3.2 版本号的词法约束

`COMPILER_VERSION` 采用 `"<major>.<minor>"` 两段（与 `FEATURE_VERSION`/`ENGINE_VERSION` 的三段式不同，因为编译器没有补丁语义）；比较只允许**相等或不等**，不得解析成序数做「新版本优先」的判断。

---

## 4. 编译器输入契约

### 4.1 允许读

编译器（纯函数）的输入是**一个已通过研究层门禁的 `StrategyDraft` 业务行**（`backend/app/domain/models.py:916-942`）加上调用方给出的目标身份：

```python
@dataclass(frozen=True)
class CompilerInput:
    draft: StrategyDraft          # 业务行；不是 AITask
    strategy_id: int              # 已存在的 Strategy 行（§5.3）
    version: str                  # 由 strategy_service.next_version 决定（§5.3）
```

编译器可读的草案字段（全部来自 `draft.draft_json`，形状由 `StrategyDraft` 模型定义，`backend/app/ai/research_schemas.py:393-412`）：

| 字段 | 类型 | 用途 |
|---|---|---|
| `strategy_name` | str | `strategy.name`（DRAFT） |
| `status` | `SUPPORTED` / `PARTIALLY_SUPPORTED` / `NEEDS_CAPABILITY` | 能力预判（只读参考，最终以 `capability_report_json` 为准） |
| `market` | `{markets[], asset_classes[], timeframes[], universe}` | `market.asset_classes` / `market.timeframes`；`markets`/`universe` 不可表达（§6） |
| `rules[]` | `{id, field, statement, origin, confidence, derived_from, evidence[], parameters{}, required_capabilities[], note}` | 唯一可映射的载体：`field` + `parameters`（`statement` 仅供报告，§7.4） |
| `indicators[]` | `{name, origin, parameters{}, evidence[], note}` | `indicators[]` 声明 |
| `unknowns[]` | `{field, why, needed_to_formalize, rule_id}` | 拒绝依据（§9） |
| `required_capabilities[]` | `{capability, affected_rule, reason, suggested_alternative, alternative_is_experimental}` | 拒绝依据（§13） |
| `parameters` | dict | `spec.parameters` 透传（DRAFT） |
| `executable` | bool（恒 `false`，`backend/app/ai/research.py:900`） | 只读断言，不得被编译器改写 |

以及服务端已算好的两样东西（**只读**）：

- `draft.capability_report_json`：形状 = `CapabilityDecision.as_dict()`，键为 `verdict/requested/supported/partial/missing/model_capabilities/reasons/items`（生产方 `backend/app/ai/research_schemas.py:1448-1458`，唯一写入方 `backend/app/ai/research.py:897-904`；**不是**注册表内部的 `CapabilityReport`（`status/UNSUPPORTED`）形状——见 §13.3）。
- `draft.status` / `draft.capability_status` / `draft.model_status`：服务端判定、服务端判定、模型自述（三者并排保存，`models.py:927-931`）。

### 4.2 禁止读

1. **`AITask.output_json`**：那是浅校验后的模型原始顶层对象（`backend/app/ai/runtime.py:357-358`；`validate_structured_dict` 只检查顶层必需键并 `return dict(data)`，`backend/app/ai/provider.py:276-284`），未经 pydantic 逐层 `extra="forbid"`。
2. 模型响应原文：仓库**没有**保存它的列（`grep raw_response|raw_output|raw_json|provider_response` over `backend/app` 零命中）。
3. 外部材料全文 / `AISourceSnapshot` / 快照正文：编译器不接触来源层。
4. 网络、系统时间（作为语义）、随机数、进程状态。
5. **请求体 override**：编译器不接受任何参数覆盖（否则同一草案可产出多份 spec，破坏 §15.4 的幂等）。要调参数请走回测端点的 `execution_overrides`（`backend/app/api/routers/backtests.py:71-74`）。

### 4.3 前置条件（调用方保证，编译器不重复计算）

1. `draft.draft_json` 非空（否则 500，属于数据损坏）。
2. 该草案已通过研究层三层门禁：`find_forbidden_keys` → `parse_draft`（`research_schemas.py:874-877`）→ `validate_draft`（`:1188-1394`）→ `assess_draft_capabilities`（`:1494-1571`）。调用点顺序见 `backend/app/ai/research.py:804-829`。
3. `strategy_id` 指向的 `Strategy` 行存在（否则 404 `strategy_not_found`）。
4. `version` 是调用方按 ADR-061 分配的（`strategy_service.next_version`，`backend/app/data/strategy_service.py:71-95`）。

**编译器不得自行调用研究层或 AI 层重新验证**；它只做纯函数映射（§13.4）。

---

## 5. 编译器输出契约

### 5.1 结果三态（互斥、完备）

| `result` | 含义 | 是否产生策略产物 |
|---|---|---|
| `COMPILED` | 全部槽位已由 DRAFT / DRAFT_PARAMETER / COMPILER_RULE 决定，且静态验证 `is_valid` | ✅ 产出 `dsl_json` + 编译报告 |
| `NEEDS_USER_DECISION` | 至少一个「用户可回答」的拒绝码 | ❌ 无 spec、无 `StrategyVersion`、无回测、不回填 `compiled_strategy_version_id` |
| `REJECTED` | 只含结构性拒绝码（含能力缺失、DSL 不可表达） | ❌ 同上 |

允许的返回类型（冻结签名，Step 2B 实现）：

```python
@dataclass(frozen=True)
class CompileResult:
    result: str                              # COMPILED | NEEDS_USER_DECISION | REJECTED
    compiler_version: str                    # "1.0"
    draft_hash: str                          # §15.2
    compile_hash: str | None                 # 仅 COMPILED 时非空
    spec: dict[str, Any] | None              # 仅 COMPILED 时非空
    report: dict[str, Any]                   # §16，三态都有
```

### 5.2 编译器不产生的东西

- 不产生版本号（由 `next_version` 产生，编译器只**接收**并回显）。
- 不产生 `Strategy` 行、`StrategyVersion` 行、`BacktestRun` 行。
- 不产生任何 AI 调用、`AITask` 行、预算消耗。
- 不产生新的 DSL 字段、新的验证器规则、新的指标类型。

### 5.3 是否允许编译器在编译时顺带创建 `Strategy` 行？

**不允许。这是冻结结论。**

理由（按重要性）：

1. **纯函数性**：编译器的全部价值在于「同一输入 → 同一输出」，可以脱离数据库单测（§18 的 T1–T12 大多不需要 DB）。一旦它 `db.add`，就必须在事务、并发、唯一约束下重新定义确定性。
2. **职责已有归属**：`Strategy` 行的创建与 slug 唯一性判定归 `strategy_service`（`slugify` `:41-43`、唯一约束 `models.py:200`、`strategy_version_plan` `:121+`）。仓库已存在三条创建路径（`backend/app/api/routers/strategies.py:189`、`backend/app/importer/importer.py:177`、`backend/app/workers/tasks.py:393`），编译器不得成为第四条。
3. **版本号分配是一条账本规则**（ADR-061）：`next_version` 会因历史版本非 `major.minor.patch` 而 `raise ValueError`（`strategy_service.py:84-93`）。让编译器「猜」版本号，等于把账本规则复制一份。
4. **不留半成品**：如果编译产物不合格，编译器不应已经写入任何行。先判定、后落库，是唯一不产生孤儿数据的方式。

因此 API 契约是「调用方给出目标，编译器给出产物，服务层落库」：见 §5.4 与 §16.6。

### 5.4 被本文档取代的 docs/28 建议

| docs/28 的建议 | 本文档的冻结结论 |
|---|---|
| `POST /ai/strategy/drafts/{id}/compile` **不接请求体参数**（docs/28 §16.2） | **必须接 `strategy_id`**（§16.6）；因为编译器不创建 `Strategy` 行（§5.3） |
| 拒绝码含 `draft_uncompilable`、`validator_rejected`、`missing_exit`、`missing_cost_decision`、`draft_not_found`、`compile_failed`（docs/28 §13.2） | 统一为 §9 的 **15 个码**；`draft_uncompilable` 由三态取代；`validator_rejected`→`validation_failed`；`missing_exit`/`missing_cost_decision`→`missing_required_slot`；`draft_not_found`/`compile_failed` 属于传输层，不进决策词表（§9.3） |
| 「不可编译」返回 422（docs/28 §13.2） | 三态都返回 **`result` 字段**；HTTP 层：`COMPILED`→201，`NEEDS_USER_DECISION`/`REJECTED`→422（§16.6），理由见 §16.6 |
| decided-by 未定枚举（docs/28 §18.3 只举例） | 冻结为 **4 个值**（§7），显式拒绝 `ENGINE_DERIVED` |

---

## 6. 草案规则 → StrategySpec 映射契约

### 6.1 映射总表

| 草案概念 | DSL 1.0 目标 | 决定方式 | 不可表达时 |
|---|---|---|---|
| `market.markets` | **无此字段** | — | `not_expressible` |
| `market.universe` | **无此字段** | — | `not_expressible` |
| `market.asset_classes` | `market.asset_classes` | 透传（COMPILER_RULE） | 报告标 `honoured: false`（§13.2） |
| `timeframe` / `market.timeframes` | `market.timeframes[0]` | DRAFT | 不在 `BARRS_PER_YEAR` 表（`backend/app/research/metrics.py:18-26`）→ `engine_incompatible` |
| `indicators` | `indicators[]` | DRAFT + COMPILER_RULE（生成 `id`） | 能力注册表没有该 type → `indicator_unmapped` |
| `features` | `spec.features` | COMPILER_RULE（恒 `[]`） | 该字段无消费者（`grep spec.features` 零命中），编译器不得写入 |
| `filters` | **无独立槽位**：由 `entry`/`exit` 条件组表达 | — | 排名/横向筛选 → `not_expressible`；别名清单见 `capabilities.py:222-236` |
| `entry` | `entry.long` / `entry.short` | DRAFT（canonical 条件） | 无规则 → `missing_required_slot` |
| `exit` | `exit.long` / `exit.short` | DRAFT（canonical 条件） | 无规则 → `missing_required_slot`（DSL 必填，`dsl.py:277-278`） |
| `sizing` | `execution.sizing` | 用户 + DRAFT（§12） | 缺失 → `missing_required_slot` |
| `fill` | `execution.fill_model` | COMPILER_RULE（`next_bar_open`） | 草案要别的值 → `engine_incompatible` |
| `cost`（手续费/滑点） | `execution.fee_bps` / `slippage_bps` | **用户** | 缺失 → `needs_user_decision` |
| `order type` + 偏移 | `execution.entry_order_type` / `limit_offset_atr` / `stop_offset_atr` / `order_valid_bars` | DRAFT（有则用）；缺省 COMPILER_RULE `market`/`1` | 需要偏移但缺失 → `missing_required_slot` |
| `risk` | `risk.*`（4 个键） | DRAFT + **用户** | 见 §11 |
| `outputs` | **无此字段**：指标由回测请求选择（`backend/app/research/metrics.py`） | — | `not_expressible` |
| `parameter` | `spec.parameters` | 透传（DRAFT） | — |
| 身份（`strategy.id/name/version`） | `strategy.*` | 见 §6.3 | — |

**三条结构性事实（docs/28 §5 的结论，此处冻结）**：

1. DSL 1.0 **没有** `cycle`、`filters`、`outputs` 字段。
2. `sizing` / `fill` / `cost` 都住在 `execution` 内（`dsl.py:215-234`），不是顶层。
3. **DSL 1.0 没有标的位置**：`grep "symbol|markets" backend/app/strategies/dsl.py` 零命中。标的属于回测请求与数据集参数，不属于 spec。

### 6.2 规则的读取方式（唯一的映射入口）

对每一条 `draft.rules[]`，编译器**只看两个字段**：

- `rule.field`（`RuleField`，10 个取值：`market/universe/timeframe/indicator/entry/exit/risk/sizing/execution/parameter`，`backend/app/ai/research_schemas.py:125-137`）
- `rule.parameters`（结构化参数，形状由 §10 冻结）

`rule.statement`、`rule.note`、`unknown.why`、`assumption.statement`、`ambiguity.phrase`、`evidence[].quote` 是**散文**，只能进入编译报告的 `statement` 字段与日志，**不得参与任何映射判断**（§18 T5）。

### 6.3 身份槽位

| 槽位 | 来源 | 说明 |
|---|---|---|
| `strategy.id` | `CompilerInput.strategy_id` | 已存在的 slug（`models.py:200`） |
| `strategy.name` | `draft.strategy_name` | DRAFT |
| `strategy.version` | `CompilerInput.version` | `next_version` 分配；冲突 → `version_conflict` |
| `strategy.description` | `None` | 不得把 `understanding_of_original` 塞进去（它是散文） |
| `strategy.source` | COMPILER_RULE | 固定为 `{"type": "compiler", "compiler_version": "1.0", "draft_id": <int>, "hypothesis_id": <int>, "run_id": <int>}`；该字段全仓无消费者（`grep .strategy.source` 零命中），因此只作为溯源注解，**不得**承载语义 |
| `schema_version` | COMPILER_RULE | 恒 `"1.0"` |

---

## 7. decided-by 枚举（冻结）

### 7.1 四个值

```python
DECIDED_BY = ("DRAFT", "DRAFT_PARAMETER", "COMPILER_RULE", "USER_REQUIRED")
```

| 值 | 含义 | 是否允许出现在成功的 `COMPILED` 产物里 |
|---|---|---|
| `DRAFT` | 值直接来自草案的结构化字段（`rule.field` / `draft.market` / `draft.strategy_name` / `draft.parameters`） | ✅ |
| `DRAFT_PARAMETER` | 值来自 `rule.parameters` 的某个键（canonical 条件、sizing、risk、order type） | ✅ |
| `COMPILER_RULE` | 纯机械、不改变经济含义的规则：`schema_version`、指标 `id` 生成、`strategy.source`、多规则合并时的 `all`/`any` 包装、`fill_model`、`features=[]` | ✅ |
| `USER_REQUIRED` | **必须由人给出**的值（§12）；编译器只能报告缺失，不能补默认 | ❌（出现即 `NEEDS_USER_DECISION`） |

**每个被写入的槽位都必须在报告的 `slots[]` 里出现一次并带 `decided_by`**（§16.4）。没有任何槽位允许「没人决定」。

### 7.2 为什么拒绝 `ENGINE_DERIVED`

候选值 `ENGINE_DERIVED`（「值是引擎在运行时的既定行为决定的」）**被显式拒绝**，理由：

1. **它就是静默默认值的化名。** 引擎里大量取值来自 DSL 的 pydantic 默认值或引擎内联默认：`max_position_pct=1.0`（`backend/app/research/engine.py:203-205`）、`deploy = fraction if fraction is not None else max_position_pct`（`backend/app/research/engine.py:112-114`）、`paper_engine.py:59-61` 的 `fee_bps=10.0/slippage_bps=5.0`。把这些记为「引擎派生」等于给它们发一张合法通行证，正好是 §12 要禁止的行为。
2. **引擎行为不是规范。** `fill_model` 从未被引擎读取（`grep` 命中仅 `dsl.py:24,218`、`importer/dsl_builder.py:162`、`capabilities.py:128`），`market.timeframes` 也无人读取。若以「引擎行为」为准，编译器会把死字段当成能力（docs/28 §13.2 的假 SUPPORTED）。
3. **两态会互相污染。** 一旦存在 `ENGINE_DERIVED`，「为什么这个值是这样」的答案就从「草案说了」/「编译器按规则填」变成「引擎恰好这么干」，审计链断掉。

**替代规则（冻结）**：一个值要么来自草案（`DRAFT`/`DRAFT_PARAMETER`），要么是机械且不改变经济含义的（`COMPILER_RULE`），要么必须由人给出（`USER_REQUIRED`）。**不存在第四种来源。**

---

## 8. 拒绝语义与状态

### 8.1 三态判定的唯一规则

```text
codes = 全部被触发的拒绝码（一次编译必须全部报出，不得在第一个码处停止）
if codes 包含任一 USER_DECIDABLE 码:
    result = "NEEDS_USER_DECISION"
elif codes 非空:
    result = "REJECTED"
else:
    result = "COMPILED"
```

- **优先级**：`NEEDS_USER_DECISION` 优先于 `REJECTED`。当两类码同时存在（Martin 场景正是如此），主状态取「有一个问题可以请人来回答」，但 `rejections[]` **必须同时列出**结构性的码，一个都不许隐藏。
- 理由：主状态是**给用户看的下一个动作**（「请回答这几个问题」比「这份草案不能编译」更可执行）；而结构性码仍然出现在报告里，审计不丢信息。

### 8.2 用户可回答 / 结构性码的分类（冻结）

| 分类 | 码 |
|---|---|
| `USER_DECIDABLE`（→ `NEEDS_USER_DECISION`） | `needs_user_decision`、`unknown_blocks_slot`、`ambiguous_phrase`、`missing_required_slot` |
| 结构性（→ `REJECTED`） | `capability_missing`、`not_expressible`、`indicator_unmapped`、`parameter_invalid`、`rule_unmapped`、`rule_conflict`、`indicator_collision`、`validation_failed`、`engine_incompatible`、`provenance_invalid`、`version_conflict` |

判据：**「人回答一个问题就能让这份草案可编译」⇒ 用户可回答；「草案本身必须改（或 DSL/引擎/注册表必须扩）」⇒ 结构性。**

### 8.3 三态的硬约束

1. `NEEDS_USER_DECISION` 与 `REJECTED`：**不产生 spec、不产生 `StrategyVersion`、不跑回测、不回填 `compiled_strategy_version_id`**。
2. `COMPILED`：`dsl_json` 必须能被 `StrategySpec.model_validate` 接受（`strategy_service.parse_spec` `:46-55`），且 `validate_strategy(spec).is_valid` 为真（`backend/app/strategies/validator.py:154-302`）。**「成功 + invalid」这种组合不存在。**
3. 任何状态下，编译器**不得回写草案行**：`StrategyDraft` 没有不可变触发器，因此这条靠代码纪律与测试（§18 T12）保证。
4. 任何状态下，`draft.executable` 都必须保持 `false`（`StrategyDraft` 的语义由 ADR-155 定义）。

---

## 9. 拒绝原因码词表（冻结）

### 9.1 15 个码

| # | 码 | 定义 | 触发条件（在哪里判定） | 分类 |
|---|---|---|---|---|
| 1 | `needs_user_decision` | 存在一个明确的待决问题，且没有更具体的码能描述它 | 兜底：草案的 `unknowns`/`ambiguities` 指向的槽位无法用其它码表达 | USER_DECIDABLE |
| 2 | `unknown_blocks_slot` | 某个 `unknown` 卡住了一个 DSL 必填槽位 | `unknown.needed_to_formalize == True` 且 `unknown.field` ∈ {`timeframe`,`entry`,`exit`,`risk`,`sizing`}，或 `unknown.rule_id` 指向的规则承载必填槽位（`research_schemas.py:278-291`、ADR-160） | USER_DECIDABLE |
| 3 | `ambiguous_phrase` | 草案自己承认这句话有多种读法 | `ambiguity.needs_decision == True`（`research_schemas.py:268-275`）且该 `phrase` 落在某个必填槽位的规则上 | USER_DECIDABLE |
| 4 | `capability_missing` | 需要的系统能力不在注册表里（或只部分支持） | `capability_report_json.missing` 非空、或 `.partial` 非空、或 `.verdict ∈ {"NEEDS_CAPABILITY", "UNSUPPORTED"}`（§13.3；形状与写入方见 §4.1） | 结构性 |
| 5 | `not_expressible` | DSL 1.0 没有能承载它的字段 | 草案要求 `universe`/`markets`/标的、组合/排名/杠杆/基本面/新闻/微观结构/VaR/Calmar、`outputs` 等（§6.1） | 结构性 |
| 6 | `indicator_unmapped` | 指标名字在能力注册表里找不到对应类型 | `indicators[].name` ∉ 注册表 indicators 组（`capabilities.py:98-199`，来源 `features/engine.py` 的 `SUPPORTED_INDICATOR_TYPES`）/ 别名表 | 结构性 |
| 7 | `parameter_invalid` | `rule.parameters` 不是 canonical 形状 | §10 的键/类型校验失败（含使用了非 canonical 格式、`threshold` 键、rule 级 `period` 与 `indicators[]` 冲突之外的未知键） | 结构性 |
| 8 | `rule_unmapped` | 一条草案规则无法确定性地落到任何槽位 | `rule.field` 是 `entry`/`exit`/`indicator`/… 但 `parameters` 里没有 canonical 条件/取值（例：Martin 的 `d-intent`） | 结构性 |
| 9 | `rule_conflict` | 两条规则对同一槽位给出互相矛盾的值 | 多个 `risk` 规则给出不同的 `stop_loss_atr_multiple`；同一 `(field, side)` 的规则未给 `combine`；`period` 与 `indicators[]` 不一致 | 结构性 |
| 10 | `indicator_collision` | 两个不同的声明被映射成同一个 DSL 指标 `id` | 生成的 `id`（如 `rsi14`）重复（`backend/app/features/engine.py:162` 的后写覆盖） | 结构性 |
| 11 | `missing_required_slot` | DSL 要求（或本契约要求）的槽位在草案里根本没有对应规则 | `entry.long`/`exit.long` 无规则；`risk` 缺少止损/止盈；`sizing` 缺失；order type 需要偏移但没给 | USER_DECIDABLE |
| 12 | `validation_failed` | 生成的 spec 没有通过静态验证 | `validate_strategy(spec)`（`validator.py:154-302`）返回的 `errors` 非空（`warnings` 不算） | 结构性 |
| 13 | `engine_incompatible` | DSL 能表达，但引擎不会按它执行（会造成「写下的 ≠ 跑的」） | 草案要求 `fill_model != "next_bar_open"`；`timeframe` 不在年化表；需要 `spec.features`/`Condition.threshold` 才成立的语义 | 结构性 |
| 14 | `provenance_invalid` | 溯源被升级或缺失 | 编译产物把 `ASSUMED` 升级为 `EXPLICIT`/`INFERRED`（`ORIGIN_STRENGTH`，`research_schemas.py:108`）；`derived_from` 指向的规则找不到 | 结构性 |
| 15 | `version_conflict` | 目标 `(strategy_id, version)` 已被占用 | `UniqueConstraint` `uq_strategy_version`（`backend/app/domain/models.py:200` 所在表约束）冲突 | 结构性 |

**一个都不加、一个都不减。** 若未来确需新码，必须先改本文档（并升 `COMPILER_VERSION`），不得在实现里悄悄新增。

**第 1 号码（`needs_user_decision`）的当前发射状态（Step 2B Final Corrective Pass）。** 它是全局 USER_DECIDABLE 兜底码，但 Step 2B 的规则对每一个已知的 USER_REQUIRED 情况都使用了更具体的码（`missing_required_slot` / `unknown_blocks_slot` / `ambiguous_phrase`），因此 Step 2B 当前**没有直接发射点**：

```text
Step 2B:
needs_user_decision = valid global code
                    = currently unreachable by direct emission
```

- **不得**为它人工构造触发条件，**不得**把它从词表里删除或降级：它仍是 §9.1 的第 1 号码、仍是 USER_DECIDABLE，三态判定仍按 §8.1 从码的分类推导。
- 它**不属于** ADR-168 的 `unreachable_in_step_2b`。那张表记录的是**输入边界导致的不可达**（`ambiguous_phrase`、`provenance_invalid`、`version_conflict`：冻结的 `CompilerInput` 里根本没有对应输入），而本码是「当前规则没有直接发射点」。**两者语义不同，不得合并，也不得把本码加进那张表。**

### 9.2 码与结果状态的绑定

- §9.1 的「分类」列是唯一的分类依据；实现必须从常量推导，不得在判定处硬编码分支。
- 同一码可以在多个槽位触发；`rejections[]` 里每个触发点各占一条（带 `slot`/`rule_id`），**不做去重**（去重会隐藏「两个不同槽位都缺」这一事实）。同一 `(code, slot, rule_id)` 三元组不重复。

### 9.3 被排除在决策词表之外的三个词

| 词 | 为什么不在词表里 | 它的正确位置 |
|---|---|---|
| `draft_not_found` | 它不是对草案的判断，是路由层「这一行不存在」 | HTTP 404（§16.6） |
| `strategy_not_found` | 同上 | HTTP 404 |
| `compile_failed` | 它不是「草案不可编译」，是「编译器自己崩了」 | HTTP 500 + 日志（不能用它掩盖 `NEEDS_USER_DECISION`） |

---

## 10. 结构化参数规范（冻结：canonical 条件格式）

### 10.1 唯一合法格式

**选择 (b)（操作数式，operand-centric）作为唯一 canonical 格式：**

```json
{
  "left": "close",
  "operator": "crosses_above",
  "right": "EMA",
  "period": 20
}
```

理由：

1. **与 DSL 一一对应**：DSL 的 `Condition` 就是 `{op, left, right}`（`backend/app/strategies/dsl.py:67-75`，字段顺序 `op, left, right, threshold`）。(b) 只差一个「操作数名字」，映射是恒等变换；而 (a) 的 `{indicator, period, operator}` 无法表达仓库里真实存在的价格对价格规则（`examples/strategies/pa-breakout.json` 的 `close > prior_high`、`backend/tests/test_strategies.py:15-35` 的 `VALID_DSL`）。
2. **不把 id 生成泄漏进草案**：(a) 把「指标 + 周期」绑在条件里，编译器必须同时决定 `indicators[].id`；在 (b) 里 `indicators[]` 是唯一的指标声明处，条件只**引用**名字，`id` 由编译器机械生成（`COMPILER_RULE`）。
3. **拒绝语义干净**：(a) 的 `threshold` 键在 DSL 里存在但**没有任何读者**（`Condition.threshold`，`dsl.py:75`；`grep` 零消费），允许它等于允许「写下一个永远不生效的字段」。

### 10.2 另一个格式必须被拒绝

(a) 形式（含 `indicator` / `threshold` 键）**必须被拒绝**，拒绝码 **`parameter_invalid`**，且 `rejections[].detail` 必须给出转换提示：

```text
{"indicator": "RSI", "period": 14, "operator": "lt", "threshold": 30}
  → 拒绝（parameter_invalid）
  → 正确写法：{"left": "RSI", "operator": "lt", "right": 30, "period": 14}
```

**不得同时接受两种格式，也不得「自动兼容」。** 静默兼容 = 两套语义，两套测试，两份 bug。

### 10.3 canonical 条件的完整键表

| 键 | 必需 | 类型 | 规则 |
|---|---|---|---|
| `left` | ✅ | string | 序列操作数：已知列名，或 `indicators[]` 里声明的指标名。**不得是数字**（DSL 的 `left` 是序列） |
| `operator` | ✅ | string | 必须是 `ComparisonOp` 成员（`dsl.py:63`：`gt/gte/lt/lte/eq/ne/crosses_above/crosses_below`）；否则 `parameter_invalid` |
| `right` | ✅ | string \| number | 数字 = 阈值；字符串 = 已知列名或声明的指标名 |
| `period` | 可选 | int | 本条条件引用的指标的周期；若 `indicators[]` 也声明了周期，两者必须相等，否则 `rule_conflict` |
| `side` | `field ∈ {entry, exit}` 时必需 | `"long"` \| `"short"` | 决定进 `entry.long` 还是 `entry.short`；`short` 还要求 `market.allow_short`（`dsl.py:275-276`）与能力检查 |
| `combine` | 同一 `(field, side)` 有多条规则时必需 | `"all"` \| `"any"` | 决定 `ConditionGroup` 的合并方式；**不得默认** |
| 其它键 | ❌ | — | 未知键 → `parameter_invalid` |

**禁止的键（显式列出）**：`indicator`、`threshold`、`condition`、`conditions`、`expr`、`expression`、`dsl`、`strategy_spec`、`id`、`type`、`input`。

### 10.4 数字的规范化（决定性序列化）

DSL 的 `Condition.left/right` 是 `str`（`dsl.py:73-74`），所以数字阈值必须**决定性地**序列化成字符串：

- 规则：`"%.10g"` 格式（先例：`backend/app/features/engine.py:208-214` 用 `float_format="%.10g"` 做 `feature_input_hash`）。
- `30` → `"30"`；`30.0` → `"30"`；`0.1` → `"0.1"`；`0.1234567890123` → `"0.123456789"`（`.10g` 取 10 位有效数字且**不保留尾零**；旧示例里的 `"0.1234567890"` 是笔误，已按上面的规范句修正）。
- **不得**使用 `str()`（会给出 `"30.0"`）、`repr()`、`json.dumps()` 的默认浮点输出或本地化格式。

### 10.5 指标 `id` 的生成规则（COMPILER_RULE）

```text
id = <type 小写去空格> + <period 或 period_ref 的十进制字面量>
例：RSI(14) → "rsi14"；EMA(20) → "ema20"；ATR(14) → "atr14"
无 period 的指标：id = <type 小写>（例："obv"）
```

- 指标 `type` 必须用能力注册表的指标组做**别名归一**（`capabilities.py:98-199` 的 indicators 组；已含 `BB`/`BOLLINGER`/`BOLLINGER_BANDS` 别名，见 `docs/28` §6.2）。
- 归一后仍不认识 → `indicator_unmapped`。
- 生成的两个 `id` 相同但语义不同 → `indicator_collision`（例：草案里同时声明 `EMA(20)` 与 `ema period=20`）。

### 10.6 `field == "indicator"` 规则的参数形状（冻结）

`field == "indicator"` 的草案规则有两件事可以说，**必须用两种互斥形状之一表达，不得混用**：

| 形状 | 键 | 用途 | 例 |
|---|---|---|---|
| **条件形** | `left` + `operator` + `right`（+ `period?` / `combine?`） | 该规则在断言一个可测试的判据 | `{"left": "RSI", "operator": "lt", "right": 30, "period": 14}` |
| **声明形** | `name`（+ `period?` / `input?`） | 该规则只声明使用某个指标，不断言判据 | `{"name": "RSI", "period": 14}` |

- `name` 与 `left` **不得同时出现**（同时出现 = 形状不唯一）→ `parameter_invalid`。
- 两种形状都不得使用 §10.3 的禁止键（`indicator`、`threshold`、`expr`、…）→ `parameter_invalid`。
- 声明形只产出 `indicators[]` 条目，**不产出任何条件**；若某条 `field == "entry"`/`"exit"` 规则的全部内容都来自声明形，则该槽位不会被填上 → 由 §8.1 判定（通常 `missing_required_slot` 或 `rule_unmapped`）。
- 条件形引用的指标名必须能被 `indicators[]` 或本条 `period` 解析；否则 `indicator_unmapped`。
- **Martin 的实证**：`d-entry.parameters = {"indicator": "RSI", "period": 14, "threshold": 30}`（`backend/tests/research_payloads.py:291`）同时含禁止键 `indicator`/`threshold`、缺 `left`/`operator`/`right`、且无法判定是条件形还是声明形 → **`parameter_invalid`**（见 §17.2）。

---

## 11. 风险字段规范（冻结）

### 11.1 四个键（与 `RiskSpec` 字段名逐字一致）

| 键 | 类型 | 约束 | 决定方式 |
|---|---|---|---|
| `stop_loss_atr_multiple` | number | `> 0` | DRAFT_PARAMETER（草案 `risk` 规则给出） |
| `take_profit_r_multiple` | number | `> 0` | DRAFT_PARAMETER |
| `take_profit_atr_multiple` | number | `> 0` | DRAFT_PARAMETER |
| `max_position_pct` | number | `0 < x ≤ 1` | DRAFT_PARAMETER（给出时）/ **USER_REQUIRED**（缺失时） |

来源：`backend/app/strategies/dsl.py:139-142`。

### 11.2 三条硬规则

1. **`risk` 块必须能被 DSL 接受**：`RiskSpec` 要求至少一个止损或止盈（`dsl.py:173-183` 的 `_at_least_one`，错误消息「risk block must define a stop loss or a take profit」）。若草案的 `risk` 规则只给了 `max_position_pct`，编译器必须报 `missing_required_slot`（**不得**为了通过验证而补一个止损倍数）。
2. **只接受扁平形式**：`{"stop_loss_atr_multiple": 2.0}` 合法；嵌套形式 `{"stop_loss": {"type": "atr_multiple", "multiple": 2.0}}`（`dsl.py:144-171` 允许）**不是 canonical**，属于草案输入形状错误 → `parameter_invalid`，理由是嵌套形式把两个键名压成一层，映射表必须为它再写一份分支（两套真相）。
3. **单位必须在键名里说清**：ATR 倍数与 R 倍数不可互换。草案写 `{"take_profit": 2.0}`（无单位）→ `parameter_invalid`；数字必须挂在带单位语义的键上。

### 11.3 为什么 `max_position_pct` 缺失是 `USER_REQUIRED` 而不是默认值

引擎在缺 `risk` 块时用 `max_position_pct = 1.0`（`backend/app/research/engine.py:203-205`），并在 `fraction is None` 时把 `deploy` 设为该值（`engine.py:112-114`）——**满仓**。这正是用户 §十二 点名的「仓位大小」不得自动决定。所以：缺失 = `needs_user_decision`，绝不是「用 DSL 默认值」。

---

## 12. 哪些字段不允许编译器自动决定

以下槽位一旦在草案里没有明确取值，编译器**只能拒绝或标记 `USER_REQUIRED`**，不得使用：DSL 的 pydantic 默认值、引擎内联默认值、`capabilities.py` 的注册表推断、任何「常见做法」。

| 槽位 | 禁止的默认来源 | 正确结果 |
|---|---|---|
| `risk` 存在性 | 引擎的「无 risk 块」路径（`engine.py:203-205`） | `missing_required_slot` |
| 止损/止盈取值 | 无 | `missing_required_slot` |
| `max_position_pct` | `1.0`（`engine.py:203-205`） | `needs_user_decision` |
| `execution.sizing.mode/fraction/risk_pct/atr_multiple` | `fixed_fraction`/`None`/`0.01`/`2.0`（`dsl.py:200-206`） | `missing_required_slot`/`needs_user_decision` |
| `fee_bps` / `slippage_bps` | `0.0` / `0.0`（`dsl.py:223-224`） | `needs_user_decision` |
| `entry_order_type` + `limit_offset_atr` / `stop_offset_atr` | `market` / `None` / `None`（`dsl.py:219-221`） | 有规则用规则；缺偏移而需要 → `missing_required_slot`；否则 COMPILER_RULE `market` |
| `allow_short` / `side` | `False`（`dsl.py:242`） | 有 short 规则但 `allow_short` 未声明 → `needs_user_decision` |
| 标的（symbol/universe） | 无字段可填 | `not_expressible` |
| `timeframe` | `["1d"]`（`dsl.py:241`） | `unknown_blocks_slot` |
| `exit` 条件 | 无 | `missing_required_slot` |
| `entry`/`exit` 多条规则的 `all`/`any` | 无 | 缺 `combine` → `rule_conflict` |
| 指标 `input` 列 | `"close"`（`dsl.py:110`） | 草案未声明 → `needs_user_decision`（RSI 算在 close 还是 high 上会改变结果） |
| 指标 `period` | 无 | 草案未声明 → `needs_user_decision` |
| `initial_capital` | `10_000.0`（`dsl.py:226`） | COMPILER_RULE（记账基准，非策略决策：sizing 用比例） |
| `fill_model` | `next_bar_open`（`dsl.py:218`，且引擎只实现这一种） | COMPILER_RULE = `next_bar_open`；草案要别的 → `engine_incompatible` |
| `spec.features` | `[]` | COMPILER_RULE = `[]`（无人读，不得写入） |
| `Condition.threshold` | `None`（`dsl.py:75`） | COMPILER_RULE = 永不写入；草案试图设置 → `parameter_invalid` |

**总原则（写进 ADR-167）**：`StrategySpec` 的 pydantic 默认值**不是**编译器可以使用的取值来源。它们是「引擎在没人管的时候会怎么跑」的记录，不是「编译器可以替用户决定」的授权。

---

## 13. 能力判定规则

### 13.1 两个不同的问题

| 问题 | 名字 | 回答者 |
|---|---|---|
| 「DSL 1.0 能不能把这件事写下来？」 | `expressible` | `StrategySpec` 模型（`dsl.py:255-279`） |
| 「引擎会不会按写下的跑？」 | `honoured` | 引擎与注册表（`backend/app/research/**`、`backend/app/simulation/**`、`capabilities.py`） |

`capabilities.assess()` **只回答第一个的近亲**——它是**逐 token 的词汇表查询**（`capabilities.py:331-378`），不是「这条规则树能不能跑」。所以：

> **「每个能力都 SUPPORTED」≠「这份草案可编译」。** 编译器必须自己做 §6 的映射，能力判定只是门禁之一。

### 13.2 注册表里「声明了但引擎不兑现」的条目（编译器必须按 `honoured` 处理）

| 条目 | `expressible` | `honoured` | 编译器动作 |
|---|---|---|---|
| `fill_model`（注册表 `capabilities.py:128` 声明 `dsl.py:64`） | ✅ | ❌ 引擎恒按下一根开盘成交（`research/engine.py:369-408`） | 恒写 `next_bar_open`；草案要别的 → `engine_incompatible` |
| `market.timeframes`（注册表 `capabilities.py:163`） | ✅ | ⚠️ 仅当值在 `BARRS_PER_YEAR`（`research/metrics.py:18-26`） | 表外 → `engine_incompatible` |
| `market.asset_classes`（同上） | ✅ | ❌ 无消费者 | 透传 + 报告 `honoured: false` |
| `short_selling`（`capabilities.py:219`，`partial=True`） | ✅（回测可做空） | ⚠️ 纸面交易只做多（`simulation/paper_engine.py`） | 草案要求做空 → `capability_missing`（除非确认不进入模拟盘路径） |
| `cross_sectional_universe` / `portfolio_rules` / `leverage` / `fundamentals` / `news` / `vision` / `rag` / `live_execution`（`capabilities.py:229-286`） | ❌ | ❌ | `capability_missing`（`live_execution` 另有一条红线：本项目永不实际下单） |
| 派生列名（`highest_high_20`、`lowest_low_20`、`previous_high`、`previous_low`、`rolling_high_prev`、`rolling_low_prev`、`rsi`） | ✅（validator 接受） | ❌ `build_features` 不产出该列 | 条件引用它 → `engine_incompatible` |

**「validator 认识」≠「引擎能读」。** `validate_strategy` 的列词表（`KNOWN_DERIVED`）比 `build_features` 真正物化的列更宽，v2.1.0 有 7 个名字落在差集里（上表最后一行）。编译器**不得**自己抄一份清单，必须读引擎自己的可读来源 `FEATURE_CATALOGUE`（`backend/app/features/catalogue.py`；由 `backend/tests/test_feature_catalogue.py` 把该清单与 `build_features` 的真实输出**双向**比对），差集因此随引擎生长自动收缩（引擎补上该列 → 差集为空 → 守卫自动放行）。条件的任一操作数命中差集 → 拒绝码 `engine_incompatible`（§9.1 第 13 号，结构性），`spec = null`、`compile_hash = null`。本段不新增码、不改 §8.1 的判定规则。

### 13.3 编译器与注册表的分工

1. **编译器不得实现 `assess()`**，也不得扩展注册表；它只**消费**已经落库的 `capability_report_json`——不重算、不刷新、不联网。
2. **形状以落库方为准，不以注册表内部对象为准**：`capability_report_json` 存的是 `CapabilityDecision.as_dict()`（`backend/app/ai/research_schemas.py:1448-1458`），唯一写入者是 `backend/app/ai/research.py:897-904`（草案行创建时写入，此后永不更新）；键为 `verdict` / `requested` / `supported` / `partial` / `missing` / `model_capabilities` / `reasons` / `items`。它**不是** `CapabilityReport.as_dict()`（`backend/app/capabilities.py:300-308`：键为 `status`、取值含 `UNSUPPORTED`）——后者只活在注册表进程内，从不落库。因此编译器**不得**把 `status` / `UNSUPPORTED` 当输入；锁定这一形状的守卫是 `backend/tests/test_compiler_core.py` 里直接用 `CapabilityDecision(...).as_dict()` 造报告的用例。
3. 判定：以下三条互相独立，任一条命中 ⇒ 至少一个 `capability_missing`——`.missing` 非空；`.partial` 非空；或 `verdict ∈ {"NEEDS_CAPABILITY", "UNSUPPORTED"}` 而两者皆空（后一个值只为容错，当前写入方永不产生）。
4. `CapabilityVerdict`（`research_schemas.py:142`，`SUPPORTED/PARTIALLY_SUPPORTED/NEEDS_CAPABILITY`）是**服务端复算**的结论；`model_status`（模型自述，`models.py:929-931`）与它无关，编译器读 `verdict` 读的就是复算值。注册表侧另有一套 `CAPABILITY_STATUSES`（`capabilities.py:48,51`，`SUPPORTED/PARTIALLY_SUPPORTED/UNSUPPORTED`）——两套词表名字不同、用途不同，编译器只认落库的那一套。
5. `PARTIALLY_SUPPORTED` 一律按 `capability_missing` 处理（保守）。放宽必须先改本文档。

### 13.4 编译器不得调用 AI

1. 结构性禁止已存在：`FORBIDDEN_CONTENT_KEYS` 把 `dsl`/`strategy_spec`/`compiled*`/`python`/`sql`/`execute`/`run_backtest`/`broker`/`order` 列为模型输出禁区（`research_schemas.py:188-213`），`find_forbidden_keys` 递归任意深度（`:738-763`）。
2. 写版本行的既有路径里没有 AI：`create_strategy_version` 的调用者只有 `strategies.py:202`、`importer.py:221`、`workers/tasks.py:393`。
3. 能力判定已由服务端复算（`research_schemas.py:1494-1571`），不需要模型再判一次。
4. 编译器不得创建 `AITask`、不得调用 `record_usage`、不得触碰预算（`runtime.py:313`、`:367`；`ai/budget.py:74,192,244`）。

---

## 14. StrategySpec 1.0 不变式

### 14.1 一个字都不改

`SCHEMA_VERSION` 保持 `"1.0"`（`dsl.py:37`）。下列字段集合**冻结**（Step 2B 的守卫测试按此断言，§18 T4）：

| 模型 | 行 | 字段（顺序不变） |
|---|---|---|
| `StrategySpec` | `dsl.py:255-279` | `schema_version, strategy, market, indicators, features, parameters, entry, exit, risk, execution` |
| `StrategyBlock` | `dsl.py:245-252` | `id, name, version, description, source` |
| `MarketSpec` | `dsl.py:237-242` | `asset_classes, timeframes, allow_short` |
| `IndicatorSpec` | `dsl.py:96-111` | `id, type, period, period_ref, input, params` |
| `Condition` | `dsl.py:67-75` | `op, left, right, threshold` |
| `ConditionGroup` | `dsl.py:78-90` | `all, any`（恰好一个） |
| `SideRules` | `dsl.py:114-120` | `long, short` |
| `RiskSpec` | `dsl.py:123-183` | `stop_loss_atr_multiple, take_profit_r_multiple, take_profit_atr_multiple, max_position_pct` |
| `SizingSpec` | `dsl.py:191-212` | `mode, fraction, risk_pct, atr_multiple` |
| `ExecutionSpec` | `dsl.py:215-234` | `fill_model, entry_order_type, limit_offset_atr, stop_offset_atr, order_valid_bars, fee_bps, slippage_bps, allow_fractional, initial_capital, sizing` |

同样冻结的枚举：`ComparisonOp`（`dsl.py:63`）、`FillModel`（`:64`）、`OrderType`（`:186`）、`SizingMode`（`:188`）。

### 14.2 编译器必须复用的既有判定，不得重写

| 判定 | 复用 | 禁止 |
|---|---|---|
| DSL 形状 | `strategy_service.parse_spec`（`:46-55`） | 自己写 pydantic 校验 |
| 静态验证 | `validator.validate_strategy`（`:154-302`） | 自己维护「已知列」词表（两份词表已经漂移过一次，见 docs/28 §24 G15） |
| 版本号 | `strategy_service.next_version`（`:71-95`） | 自己数版本 |
| 内容哈希 | 只**新增** `compile_hash`（§15） | 改 `immutable_hash` 的算法 |

### 14.3 编译器必须显式保持的三处「不写」

1. **不写 `spec.features`**（`[]`，无消费者）。
2. **不写 `Condition.threshold`**（无读者）。
3. **不写 `strategy.description`**（散文不得进入产品字段）。

---

## 15. hash 契约

### 15.1 四个既有 hash 各回答一个问题（不得混用）

| hash | 位置 | 覆盖 | 回答的问题 |
|---|---|---|---|
| `immutable_hash` | `backend/app/data/strategy_service.py:58-64` | `{"version", "dsl"}`，`sort_keys=True, separators=(",", ":"), default=str` + SHA-256 | 「这一行版本有没有被改过？」 |
| `result_hash` | `backend/app/research/engine.py:524-537` | 回测结果摘要（**不含 spec 正文/费用/sizing/fill_model**） | 「这次回测的结果是不是同一份？」 |
| `feature_input_hash` | `backend/app/features/engine.py:208-214` | OHLCV，`float_format="%.10g"` | 「特征是在哪份输入上算的？」 |
| `output_hash` / `source_snapshot_hash` / `cache_key` | `backend/app/ai/runtime.py:62-116` | AI 层输入输出与缓存身份 | 「AI 调用用了哪份输入？」 |

**仓库此前不存在 spec 级编译哈希**（无 `spec_hash` / `compiled_hash`）。

### 15.2 `draft_hash`（新）

```python
draft_hash = sha256(json.dumps(projection, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
```

`projection` = `draft.draft_json` 的**去散文化投影**：删除下列散文键后再序列化（其余全部保留，含 `origin`/`confidence`/`derived_from`/`parameters`/`evidence`）：

**被排除的散文键（冻结列表）**：`rules[].statement`、`rules[].note`、`indicators[].note`、`unknowns[].why`、`assumptions[].statement`、`assumptions[].reason`、`ambiguities[].phrase`、`notes[]`、`understanding_of_original`、`limitations[]`、`evidence[].quote`。

**被保留的结构化键**：`strategy_name`、`status`、`market.*`、`rules[].{id,field,origin,confidence,derived_from,parameters,required_capabilities,evidence[].{source_ref,locator,verified,char_start,char_end,verified_against}}`、`indicators[].{name,origin,parameters}`、`unknowns[].{field,needed_to_formalize,rule_id}`、`assumptions[].applies_to`、`ambiguities[].{readings,needs_decision}`、`required_capabilities[]`、`parameters`、`executable`。

> `ambiguities[].readings` 与 `needs_decision` **保留**：它们决定「这份草案是否在等人决策」，属于结构事实；`phrase` 被排除，因为它是散文锚点。

### 15.3 `compile_hash`（新）

```python
compile_hash = sha256(json.dumps(
    {
        "compiler_version": COMPILER_VERSION,     # "1.0"
        "draft_hash": draft_hash,
        "strategy": {"id": strategy_id, "version": version},
        "spec": spec,                             # spec = StrategySpec.model_dump(mode="json")
    },
    sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str,
).encode("utf-8")).hexdigest()
```

- `spec` 必须是 `model_dump(mode="json")` 的结果（与 `strategy_service.create_strategy_version` 落库的形状一致），**不是**手写的 dict。
- `compile_hash` **只在 `COMPILED` 时存在**；`NEEDS_USER_DECISION`/`REJECTED` 时为 `null`（没有产物就没有产物身份）。

### 15.4 两条纪律

1. **不得用 `immutable_hash` 当 `compile_hash`，也不得反过来。** 前者回答「有没有被改」，覆盖 `(version, dsl)`；后者回答「哪一版编译器用哪份草案生成了它」，还覆盖 `compiler_version` 与 `draft_hash`。二者**输入不同、生命周期不同、读者不同**。
2. **幂等**：同一 `draft_hash` + 同一 `COMPILER_VERSION` + 同一 `(strategy_id, version)` ⇒ 同一 `spec` 与同一 `compile_hash`；`create_strategy_version` 不做内容去重（唯一去重是 watcher 比对最新一版，`backend/app/workers/tasks.py:379`），因此幂等由调用方按 `evidence_json.compile_report.compile_hash` 自查。

**禁止**的哈希输入：`hash()`（进程随机盐）、`uuid4()`、当前时间（时间只能作 `created_at`）、`set` 迭代序。

### 15.5 落库位置

`compile_hash` / `draft_hash` / `compiler_version` 写进 `StrategyVersion.evidence_json.compile_report`（列已存在，`models.py:229`）。**不新增列、不新增 migration**（§17.4）。

---

## 16. Compile Report 结构

### 16.1 顶层（冻结）

```json
{
  "compiler_version": "1.0",
  "result": "COMPILED",
  "draft": {"draft_id": 12, "hypothesis_id": 9, "run_id": 4,
            "strategy_name": "BTC oversold rebound (RSI formalization)",
            "draft_hash": "…", "compile_hash": "…"},
  "target": {"strategy_id": 7, "version": "1.1.0"},
  "rules": [ …§16.2… ],
  "slots": [ …§16.3… ],
  "unmapped": [ …§16.4… ],
  "warnings": [ …§16.5… ],
  "dsl_validation": [ …ValidationIssue.as_dict()… ],
  "capability": { …CapabilityReport.as_dict()… },
  "rejections": [ …§16.6… ]
}
```

### 16.2 `rules[]`（每条草案规则一条，**包括被拒绝的**）

```json
{"draft_rule_id": "d-entry", "field": "indicator", "origin": "ASSUMED",
 "derived_from": "r-rebound",
 "decision": "MAPPED",                      // MAPPED | UNMAPPED | REJECTED | INFORMATION_ONLY
 "targets": ["indicators.rsi14"],
 "statement": "RSI(14) 上穿 30 时买入。"}     // 散文，仅供报告
```

- `decision = "INFORMATION_ONLY"`：规则没有 DSL 归宿但也不构成拒绝（例：`field == "market"` 的说明性规则，其信息已在 `market.asset_classes` 里体现）。
- **`statement` 只允许出现在这里**（报告/日志），不得参与映射。

### 16.3 `slots[]`（每个被写入或应当被写入的 DSL 槽位一条）

```json
{"slot": "entry.long.all[0]", "decided_by": "DRAFT_PARAMETER",
 "value": {"op": "crosses_above", "left": "rsi14", "right": "30"},
 "source_rule_ids": ["d-entry"]}
```

**不变量**：`COMPILED` 状态下，`slots[]` 的并集必须覆盖 §14.1 里所有「产物中非默认值」的路径，且**没有任何槽位的 `decided_by == "USER_REQUIRED"`**。`NEEDS_USER_DECISION` 时，缺失槽位以 `{"slot": …, "decided_by": "USER_REQUIRED", "value": null, "source_rule_ids": []}` 出现。

### 16.4 `unmapped[]`

```json
{"draft_rule_id": "d-intent", "field": "entry",
 "reason_code": "rule_unmapped",
 "detail": "no canonical condition in parameters"}
```

### 16.5 `warnings[]`

**直接复用** `ValidationIssue.as_dict()`（`validator.py:102-108`，形状 `{severity, code, message, path}`），只装 `severity == "warning"` 的条目（`cross_column_comparison`、`missing_risk`、`large_position`、`zero_costs`）。不得另造形状。

### 16.6 `rejections[]`（相对 docs/28 的**唯一新增顶层键**）

```json
{"code": "unknown_blocks_slot", "slot": "exit.long", "rule_ids": ["d-exit"],
 "detail": "unknown 'exit' (needed_to_formalize=true) has no deterministic mapping",
 "user_decidable": true}
```

**为什么必须新增这个键**：§9 的很多码（`missing_required_slot`、`version_conflict`、`validation_failed`、`capability_missing`）**没有对应的草案规则**，无法挂在 `rules[].decision` 或 `unmapped[]` 上。若不给它们一个顶层数组，那些拒绝就只剩一条人类可读的消息，`result` 就成了不可机器读取的状态。`user_decidable` 直接由 §8.2 的分类表推导。

### 16.7 HTTP 契约（冻结）

```text
POST /ai/strategy/drafts/{draft_id}/compile
body: {"strategy_id": 7}

201  {"result": "COMPILED", "strategy_id": 7, "strategy_version_id": 31,
      "version": "1.1.0", "compile_hash": "…", "report": { … }}
404  {"error": {"code": "draft_not_found",    "message": "…"}}
404  {"error": {"code": "strategy_not_found", "message": "…"}}
409  {"error": {"code": "draft_already_compiled", "message": "…", "details": { … }}}
409  {"error": {"code": "version_unassignable",    "message": "…", "details": { … }}}
409  {"error": {"code": "version_conflict",        "message": "…", "details": { … }}}
422  {"result": "NEEDS_USER_DECISION" | "REJECTED", "report": { … }}
```

- **状态码的理由**：`201` 对应「创建了一行」（与 `POST /strategies/{id}/versions` 的 `strategies.py:202-215` 一致）；`422` 对应「判定为不可编译、什么都没创建」（与 `importer.py:190-197` 的 `422 + issues` 一致）；`409` 对应「目标身份冲突，换个版本号可重试」。
- **`409` 一律走项目的 `error` 信封，且绝不带 `report`**：`draft_already_compiled` / `version_unassignable` / `version_conflict` 都是**输入边界错误**——编译器没有产出任何 `CompileResult`，所以响应体里**不允许**出现 `result` 或 `report`（伪造一份 `{"result": "REJECTED", "report": …}` 会让客户端以为编译器真的跑过一轮）。`error.details` 只放机器可读的定位信息（如 `{"strategy_id": 7, "versions": ["1.0.0"]}`）。
- **`version_conflict` 不可由编译器产生**：§9 收录它是为了让「目标 `(strategy_id, version)` 已被占用」（`uq_strategy_version`，`backend/app/domain/models.py:200` 所在表约束）在词表里有个名字；它只可能出现在 API 层已分配版本号之后的写入失败/竞态路径上（`backend/app/api/routers/ai.py` 的 `IntegrityError` 处理）。编译器自身对已占用版本无感知（`compile_strategy_draft()` 不查库，见 §4.1）。
- **三态都要带 `result`**：客户端必须读 `result` 而不是只看状态码（422 有两种含义）。
- 新的编译端点**自动继承** `backend/app/api/main.py:138-169` 的 Bearer 鉴权与 `:171-176` 的变更型请求限流；不得绕开中间件（例如自建 app）。
- `rejections[].detail` **不得回显模型原文**；只允许回显结构化字段名、槽位名与注册表给出的 `reason`。

---

## 17. Martin 场景的契约结论

### 17.1 输入（逐字，`backend/tests/research_payloads.py:18-20`）

```python
QUESTION = "Martin 说这个策略在 BTC 超跌之后反弹的时候买入。"
NOTE = "Martin：BTC 超跌之后反弹的时候买入。就这一句，没有别的了。"
MARTIN = "note-martin"
```

### 17.2 结论（冻结）

```json
{"result": "NEEDS_USER_DECISION",
 "rejections": ["unknown_blocks_slot", "missing_required_slot",
                "rule_unmapped", "parameter_invalid", "not_expressible"],
 "unreachable_in_step_2b": ["ambiguous_phrase"],
 "spec": null, "compile_hash": null, "strategy_version_id": null}
```

**Martin 的实际拒绝码是五个。** `rejections[]` 里没有 `ambiguous_phrase`——它仍然是 §9.1 的合法全局码，但在 Step 2B 冻结的输入契约下**不可达**（见本节末尾「为什么这里没有 `ambiguous_phrase`」与 ADR-168）。

逐条依据（每条都可在 fixture 与代码里复查）：

| 码 | 依据 |
|---|---|
| `unknown_blocks_slot` | 草案 `unknowns` 四条（`timeframe`/`exit`/`risk`/`sizing`，各 `needed_to_formalize: true`，`research_payloads.py:302-307`）；其中 `exit` 是 DSL 必填（`dsl.py:277-278`） |
| `missing_required_slot` | `exit.long` 在草案里**没有任何** `field ∈ {exit}` 的规则；`max_position_pct`/`fee_bps`/`slippage_bps` 无取值（USER_REQUIRED） |
| `rule_unmapped` | 唯一 `field == "entry"` 的规则 `d-intent` 只有散文 `statement`、**无 `parameters`**（`:280-283`） |
| `parameter_invalid` | `d-entry.parameters` 用了禁止键 `indicator`/`threshold` 且不成形状（`:291`，见 §10.6） |
| `not_expressible` | 草案 `market.universe = "BTC"`、`market.markets = ["crypto"]`（`:259-264`）；DSL 1.0 没有这两个字段（`grep "symbol|markets" backend/app/strategies/dsl.py` 零命中） |

**五个码、三种类别齐备**：两条需要人回答（`unknown_blocks_slot`、`missing_required_slot`）、三条结构性（`rule_unmapped`、`parameter_invalid`、`not_expressible`）。按 §8.1，含 USER_DECIDABLE 码 → 结果为 `NEEDS_USER_DECISION`。

### 17.2.1 为什么这里没有 `ambiguous_phrase`

> `ambiguous_phrase` remains a valid global rejection code, but it is unreachable in Step 2B because ambiguity data is not part of the frozen `CompilerInput` contract.

- **词表没变**：`ambiguous_phrase` 仍是 §9.1 的合法全局拒绝码，触发条件仍是「`ambiguity.needs_decision == True` 且该 `phrase` 落在必填槽位的规则上」，分类仍是 USER_DECIDABLE（§8.2）。**不得删除、不得改定义、不得改触发条件。**
- **但输入里没有它**：Step 2B 冻结的输入是 `CompilerInput(draft, strategy_id, version)`（§4.1），可读的草案字段是 `strategy_name`/`status`/`market`/`rules[]`/`indicators[]`/`unknowns[]`/`required_capabilities[]`/`parameters`/`executable` —— **`ambiguities` 不在其中**。`StrategyDraft`（`research_schemas.py:393-412`）根本没有 `ambiguities` 字段；`Ambiguity`（`:268-275`）只挂在 `StrategyHypothesis` 上，而 §4.2 禁止编译器读 hypothesis（也不允许访问数据库）。Martin 的 `draft_payload`（`research_payloads.py:253-319`）里也确实没有这个键——那两条歧义在 hypothesis 层（`:214-225`）。
- **所以编译器报什么**：Martin 的槽位阻断只能由 `unknowns` 承载（`unknown_blocks_slot`），而不能由歧义承载。编译器**不得**自己造一个歧义触发点。
- **禁止的四种「让它可达」做法**：给 `StrategyDraft` 加 `ambiguities` 字段；改 `StrategyHypothesis`；让编译器去读 hypothesis（含任何数据库查询）；把 `origin == "ASSUMED"` 当成歧义。以上任一做法都是 **Contract boundary violation**（§18 T8 有前向守卫；见 ADR-168）。
- **代价与结论**：Martin 的拒绝理由从六条变成五条，**结果不变**——仍是 `NEEDS_USER_DECISION`，仍不可编译，`spec` 仍为 `null`。若将来 `CompilerInput` 扩展（例如把假设层的歧义作为显式入参传入），必须**重新审查** `ambiguous_phrase` 的可达性，并同步本节、§18 T8 与 §20（ADR-168）。

### 17.3 必须同时记录的字段异常（fixture 现状，Step 2B 不得「顺手修好」）

草案里承载买入语义的规则 `d-entry` 的 `field` 是 **`"indicator"`**，不是 `"entry"`（`research_payloads.py:284-292`）；唯一 `field == "entry"` 的规则 `d-intent` 是纯散文。**这不是 fixture 的 bug，而是「草案的语义与 DSL 的槽位不是同一套坐标系」的真实证据**，正是编译器必须靠 canonical 参数而不是靠散文来映射的原因。

### 17.4 Martin 不允许出现的东西

- 不允许产出 `spec`、`StrategyVersion`、`compile_hash`。
- 不允许回填 `StrategyDraft.compiled_strategy_version_id`。
- 不允许跑回测。
- 不允许把 `universe="BTC"` 偷换成某个资产的 symbol 后「编译成功」——那会改变策略语义。
- 既有测试的断言方向必须保持：`backend/tests/test_ai_strategy_draft.py:108-110`（`executable is False`）、`:245-249`（`StrategyVersion` 计数 0）、`:253`（模型自报 `executable=True` → `not_executable`）。

---

## 18. 测试契约（12 项）

落点：**`backend/tests/test_compiler_contract.py`**（本阶段唯一新增测试文件）。风格遵循仓库既有做法：**「一个事实，两个文件」**——文档与代码/常量对不上就红（先例 `backend/tests/test_exposure_surface.py:127`、`test_api_spec_truth.py:37`）。

| # | 测试 | 断言什么 | 为什么可能红（非空洞） |
|---|---|---|---|
| T1 | `test_compiler_version_is_independent_of_schema_version` | `COMPILER_VERSION == "1.0"` 且 `dsl.SCHEMA_VERSION == "1.0"`；docs/29 §3 同时出现两个常量名 | 有人把编译器版本塞进 DSL，或改 `SCHEMA_VERSION` |
| T2 | `test_decided_by_enum_is_frozen` | 四个值逐字；`ENGINE_DERIVED` **不在**枚举里；docs/29 §7.2 明确拒绝它 | 有人加第五个值来给静默默认开后门 |
| T3 | `test_rejection_code_vocabulary_is_frozen` | 15 个码逐字、集合相等；docs/29 §9.1 的表格行数 = 15 | 有人在实现里悄悄加码/改名 |
| T4 | `test_strategy_spec_1_0_surface_is_unchanged` | §14.1 十个模型的**字段名集合**与四个枚举逐字相等 | 有人「顺手」给 DSL 加字段（ADR-155 禁止） |
| T5 | `test_statement_text_is_not_part_of_compiler_semantics` | docs/29 §15.2 的散文排除列表存在；改 `statement` 不影响 `draft_hash` 的定义（列表里含 `rules[].statement`）；且该列表不包含 `parameters` | 有人把散文重新写进哈希/映射 |
| T6 | `test_provenance_can_only_be_weakened` | `ORIGIN_STRENGTH` 的强度序为 `EXPLICIT > INFERRED > ASSUMED > UNKNOWN`，且 `research_schemas` 里存在 `provenance_stronger_than_hypothesis` 判定；docs/29 §9.1 的 `provenance_invalid` 指向它 | 有人让编译器把 `ASSUMED` 变 `EXPLICIT` |
| T7 | `test_user_required_slots_are_never_filled_by_defaults` | docs/29 §12 表格里的每个槽位都出现；且该节点名了被禁止的默认来源（`engine.py:203-205`、`dsl.py:223-224` 等） | 有人把 `max_position_pct=1.0` 写成「默认值可接受」 |
| T8 | `test_martin_scenario_stays_uncompilable` | 读真实 fixture：假设层两条 `needs_decision`、四条 `needed_to_formalize`、`d-entry.field == "indicator"`、无 `field == "exit"` 的规则、`market.universe == "BTC"`；docs/29 §17.2 列出**五个实际码**（`unknown_blocks_slot`/`missing_required_slot`/`rule_unmapped`/`parameter_invalid`/`not_expressible`）且把它们标为 `unreachable_in_step_2b: ["ambiguous_phrase"]`；**契约边界前向守卫**：`StrategyDraft` 的字段里没有 `ambiguities`、fixture 的 `draft_payload` 里没有 `ambiguities` 键、且 `backend/app/compiler/**.py` 源码里不得出现 `StrategyHypothesis`/`parse_hypothesis`/`needs_decision`/`"ambiguities"`（§9.1 词汇表常量里的码名 `ambiguous_phrase` 不算） | 有人改 fixture 让 Martin 变得「可编译」；或给 `StrategyDraft` 加 `ambiguities`；或让编译器读 hypothesis —— 从而让 `ambiguous_phrase` 在 Step 2B 变可达（Contract boundary violation，见 §17.2.1 与 ADR-168） |
| T9 | `test_compile_hash_is_not_the_immutable_hash` | docs/29 §15 同时定义两者且输入不同；`strategy_service.immutable_hash` 仍是 `{"version","dsl"}` 的 SHA-256 | 有人把 `compile_hash` 实现成 `immutable_hash` 的别名 |
| T10 | `test_compiler_never_imports_the_ai_layer` | 若 `backend/app/compiler/` 存在：其源码不得出现 `app.ai` / `AITask` / `run_task`（前向守卫） | Step 2B 让编译器调用模型 |
| T11 | `test_compiler_never_touches_the_network` | 同上：不得出现 `httpcore`/`httpx`/`requests`/`urllib`/`socket`/`aiohttp` | 编译器去抓网页 |
| T12 | `test_compiler_never_runs_a_backtest_or_writes_rows` | 同上：不得出现 `run_backtest`/`BacktestRun`/`db.add`/`session.commit`；且 docs/29 §5.3 明确「不创建 Strategy 行」 | 编译器自己落库/跑回测 |

**前向守卫的写法（T10–T12）**：用 `importlib.util.find_spec("app.compiler")`（或直接在磁盘上检查目录）判断；目录不存在时，测试**只断言文档侧的契约**（避免用「包不存在」当断言——那会在 Step 2B 一开工就变红，属于把过程当契约）。目录存在时，扫描其 `*.py` 全文并断言禁用词为零。

**测试不得**：连数据库、调网络、依赖当前时间、依赖执行顺序。

---

## 19. 后续实现阶段的禁止项（Step 2B / Step 2C）

1. **编译器进程内不得有 AI**：不 import `app.ai.*`、不建 `AITask`、不花预算（§13.4）。
2. **编译器不得有 I/O**：不联网、不读来源快照、不读材料全文、不读 `AITask.output_json`。
3. **编译器不得落库**：不 `db.add`/`commit`；行创建归服务层（§5.3）。
4. **编译器不得跑回测**：`run_backtest`（`research/engine.py:179`）及其任何分析模块（`walk_forward`/`sensitivity`/`monte_carlo`/`ensemble`）都不得被 import。
5. **不得改 DSL**：不加字段、不改枚举、不改默认值、不改校验器（ADR-155）。
6. **不得加 migration**：产物落 `evidence_json` 与 `compiled_strategy_version_id`（§15.5、§17.4）。
7. **不得新增第四条版本创建路径**：编译端点必须复用 `strategy_service.create_strategy_version`。
8. **不得自动补答案**：不得为 `USER_REQUIRED` 槽位填值，不得替用户在歧义里选读法，不得把 `unknown` 当作「用默认值也未尝不可」。
9. **不得把拒绝做成 200 + warning**：三态必须如实进入 `result`（§16.7）。
10. **不得回显模型原文**：`rejections[].detail` 只允许结构化字段名与注册表理由（§16.7）。
11. **不得改既有测试的断言方向**：`test_ai_strategy_draft.py` 的三条断言（`:108-110`、`:245-249`、`:253`）是 Martin 契约的一部分。
12. **不得在 UI 里把 `NEEDS_USER_DECISION` 画成错误页**：它是一个需要用户回答问题的正常状态（Step 2C 的范围）。
13. **`COMPILER_VERSION` 变更必须同时改本文档并补一条 ADR**。

---

## 20. 本阶段结论

1. **契约已冻结**：输入、输出、映射、决策、拒绝、能力、不变式、哈希、报告、验收十个面都有可执行的定义，不存在「到时候看情况」的空白。
2. **编译器是纯函数**：`StrategyDraft + (strategy_id, version) → CompileResult`，无 AI、无网络、无 DB、无回测（§19 的 13 条禁止项）。
3. **一个字段都不改 DSL**：StrategySpec 1.0 与 `SCHEMA_VERSION = "1.0"` 保持原样；表达不了的语义一律 `not_expressible`，不偷换。
4. **一个 migration 都不加**：落库靠 `evidence_json` 与既有的 `compiled_strategy_version_id`（下一个可用编号仍是 `0016`）。
5. **Martin 场景在契约下仍然不可编译**，且理由被逐条钉死为**五个码**（`unknown_blocks_slot`、`missing_required_slot`、`rule_unmapped`、`parameter_invalid`、`not_expressible`——与 §17.2、§18 T8 同一口径）。`ambiguous_phrase` 仍是 §9.1 的合法全局码，但在 Step 2B 冻结的输入契约下不可达（§17.2.1、ADR-168）。这是本契约最重要的负向验收（§17）。
6. **最容易走错的三个地方已经提前封死**：`ENGINE_DERIVED`（§7.2）、canonical 条件二选一（§10.2）、`USER_REQUIRED` 不得用默认值填充（§12）。
7. **下一步**：ADR-167 + 本文档 + `backend/tests/test_compiler_contract.py` 一起提交给用户独立审查；**Step 2B（实现内核）必须等这次审查通过后另行开始**。
