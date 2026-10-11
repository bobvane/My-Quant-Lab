# 35 全项目结构完成度审计（连续任务执行记录）

> 本文件是一次**连续审计任务**的工作记录，不是一次性快照。它按 m01711 的要求，以「最初规划的完整产品结构 vs 当前真实代码」为准绳逐模块核对，并在实施过程中持续更新。
>
> 审计方法：**不看目录名、不看文档描述**，每一项结论都要求指向具体代码、契约、测试或 CI 证据；无法从代码或实测取得证据的一律标「未确认」。
> 起点：`main` / `HEAD == origin/main == a07a6e0b4`（v2.6.0）。本次任务只读盘点 → 分阶段审计 → 按 P0/P1/P2 补齐「无需用户决策且安全」的缺口。
>
> 用户自有文档（`docs/02_ARCHITECTURE.md`、`docs/15_ROADMAP_ACCEPTANCE.md`、`docs/31*`、`docs/32*`、`docs/33*`）、`AGENTS.md`、`docs/agents/` 全程未修改、未暂存、未提交。

---

## 1. 原规划阶段与当前实现的对应关系

| 规划阶段（`docs/15_ROADMAP_ACCEPTANCE.md`） | 当前实现落点 | 状态 |
| --- | --- | --- |
| Phase 0 — Foundation | `backend/app/core/config.py`、`backend/app/api/main.py`、`backend/alembic/versions/0001..0021`、`docker-compose.yml`、`docker/entrypoint.sh`、`.github/workflows/ci.yml` | 已完成并有测试证据 |
| Phase 1 — Market Data + Quant Core | `backend/app/data/providers.py`、`backend/app/data/market_data_repo.py`、`backend/app/api/routers/{market_data,assets}.py`、`backend/app/workers/tasks.py`、`backend/app/research/metrics.py` | **部分实现或流程未闭环**（质量不参与下游判定） |
| Phase 2 — Backtest Lab | 无 `backend/app/backtest/` 目录；实现在 `backend/app/research/engine.py` + `backend/app/data/backtest_service.py` + `backend/app/api/routers/{backtests,backtest_metrics}.py` | 已实现但缺完整验证（`result_hash` 口径偏窄） |
| Phase 3 — Ghostfolio | `backend/app/ghostfolio/`、`backend/app/api/routers/ghostfolio.py` | 已完成并有测试证据（本轮未深入复审计） |
| Phase 4 — Paper Trading | 无 `backend/app/paper/`；实现在 `backend/app/simulation/paper_engine.py` + `backend/app/api/routers/paper.py` | 已实现但缺完整验证（**执行无幂等键**） |
| Phase 5 — AI Layer | `backend/app/ai/**` + `backend/app/api/routers/ai.py` | 已完成并有测试证据（G7 作答闭环 = ADR-211…213） |
| Phase 6 — GitHub Strategy Importer | `backend/app/importer/**` + `backend/app/sources/**` + `backend/app/api/routers/importer.py` | 已完成并有测试证据（四道防线），但自动激活语义待决 |
| Phase 7 — Live Signal | `backend/app/simulation/signal_engine.py`、`backend/app/simulation/outcome_evaluator.py`、`backend/app/api/routers/{signals,research}.py` | 已完成并有测试证据（信号→模拟账户为人工交接） |
| Phase 8 — Strategy Lifecycle | `backend/app/strategies/{dsl,validator,lifecycle,executor}.py`、`backend/app/data/strategy_service.py`、`backend/app/api/routers/strategy_versions.py` | 已完成并有测试证据（**标的不可表达** ⇒ 生命周期闭环有一段靠回测请求旁路） |
| 未来架构方向：Docker 7 → 4 容器 | 未实施（`docker-compose.yml` 仍是 v2.6.0 的三容器 + 既定编排） | 按指令未触碰 |

模块索引（`docs/03_MODULES.md`）M01 Dashboard / M02 Market Data / M03 Feature Engine / M04 Strategy Library / M05 GitHub Strategy Importer / M06 Backtest / M07 Paper Trading / M08 Signal Engine / M09 AI / M10 Ghostfolio Adapter / M11 Notification / M12 Audit & Observability / M13 Settings —— 13 个模块在 `backend/app/api/main.py:254-276` 都挂载了路由，在前端 `frontend/src/main.ts:19-37` 都有入口。

---

## 2. 各模块完成度与证据

四层判据：**A 模块存在** / **B 模块可用** / **C 模块连通** / **D 用户流程闭环**。

| 模块 | A | B | C | D | 关键证据 |
| --- | --- | --- | --- | --- | --- |
| M01 Dashboard | ✓ | ✓ | ✓ | 部分 | `frontend/src/views/DashboardView.vue`；缺指标渲染「未知」而非 0 |
| M02 Market Data | ✓ | ✓ | **✗** | ✗ | `backend/app/data/market_data_repo.py:202-226`（`load_bars(only_closed=…)`）、`:269 upsert_bars(report=…)`；**`quality_status` 未被任何下游判定读取**（仅 `backend/app/api/routers/assets.py:66`、`market_data.py:99,128` 展示） |
| M03 Feature Engine | ✓ | ✓ | ✓ | ✓ | `backend/app/features/**`；`test_series_content_hash.py:61,108` |
| M04 Strategy Library | ✓ | ✓ | ✓ | ✓ | `backend/app/data/strategy_service.py:228 make_current`、`:257` 参数快照；`backend/app/domain/immutability.py:43` 触发器 |
| M05 GitHub Importer | ✓ | ✓ | ✓ | 部分 | `backend/app/importer/github_client.py:38 ALLOWED_HOSTS`、`:44 STRATEGY_EXTENSIONS`、`:46-50` 三个上限、`:424 _assert_allowed`；`github_client.py:189-201` 只在请求头用 client token |
| M06 Backtest | ✓ | ✓ | ✓ | 部分 | `backend/app/research/engine.py:519-532 result_hash`、`:48 ENGINE_VERSION="1.2.1"`；`test_backtest.py:38 test_backtest_is_deterministic` |
| M07 Paper Trading | ✓ | ✓ | 部分 | ✗ | `backend/app/simulation/paper_engine.py:97 execute_signal`；**无幂等键** |
| M08 Signal Engine | ✓ | ✓ | ✓ | 部分 | `backend/app/simulation/signal_engine.py`；`paper.py:753` 是唯一执行入口（人工） |
| M09 AI | ✓ | ✓ | ✓ | ✓ | `backend/app/ai/provider.py:72-79`（AI 不产数字）、`:99 UNTRUSTED_SOURCE_HEADER`、`:107-127 wrap_untrusted`；G7 闭环 ADR-211…213 |
| M10 Ghostfolio | ✓ | ✓ | ✓ | ✓ | `backend/app/ghostfolio/**` |
| M11 Notification | ✓ | ✓ | ✓ | 部分 | 按指令未深入 |
| M12 Audit & Observability | ✓ | ✓ | ✓ | ✓ | `backend/app/data/strategy_service.py:294 record_audit`；`backend/app/api/routers/health.py:159-179` |
| M13 Settings | ✓ | ✓ | ✓ | ✓ | `backend/app/api/routers/settings.py`；`backend/app/ai/secrets.py`（Fernet） |

---

## 3. 核心业务链的九段核对

「研究材料/市场数据 → 研究分析 → 假设与策略草案 → 用户回答编译问题 → 用户确认 → 策略编译与版本化 → 回测 → 风险与结果解释 → 实验比较 → 模拟账户记录与结果归因」逐段核对结果：

1. **材料/市场数据 → 研究分析**：连通。`backend/app/ai/research.py:392 _ingest`、`:452-466` 把 `source_ref`/`snapshot_id`/`text_hash` 写进 `ResearchArtifact`；`backend/app/domain/models.py:888-902` 有 `UniqueConstraint("run_id","source_ref")`。
2. **研究分析 → 假设与草案**：连通且服务端把关。`research.py:773 evaluate_draft_payload` 是唯一门禁入口（另一个调用点是 `backend/app/ai/draft_answers.py`）；草案行只有 `research.py:951 store_draft_revision` 会写。
3. **草案 → 用户回答编译问题**：闭环（ADR-211）。`backend/app/api/routers/ai.py:1073 GET …/questions`、`:1123 POST …/answers`；`frontend/src/views/LabView.vue` 的作答表单。
4. **用户确认 → 编译**：连通且是硬门。`backend/app/ai/confirmation.py:104 record_confirmation`、`:152 latest_confirmation`（按 draft id）；`ai.py:939-947 require_confirmation` ⇒ 409 `draft_not_confirmed`。
5. **编译 → 策略版本**：连通且不可变。`ai.py:974-999 create_strategy_version(commit=False)`；`immutability.py:43` 触发器只比较 `version`/`dsl_json`/`immutable_hash`。
6. **策略版本 → 回测**：连通。`backend/app/data/backtest_service.py:245`、`:340 result_hash`。
7. **回测 → 风险与结果解释**：连通。`backend/app/api/routers/backtest_metrics.py` 直传 `metrics_json`；缺值一律走 `None`（ADR-214 修掉了两处例外）。
8. **回测 → 实验比较**：连通。`backend/app/domain/models.py:495-522 ExperimentResult`（`:513 backtest_run_id` 是「实验→回测」的真相）。
9. **信号 → 模拟账户 → 结果归因**：**链路最后一段无自动交接**。`backend/app/simulation/paper_engine.py:97 execute_signal` 的唯一调用者是 `backend/app/api/routers/paper.py:753`（`POST /paper/accounts/{id}/execute`），必须由人按一次；账户能追溯到实验/回测（`paper.py:292-302`），但执行本身**没有幂等键**。

### 3.1 已发现的断点（本轮已修）

| 断点 | 根因 | 处置 |
| --- | --- | --- |
| 作答旧草案会让版本血缘分叉 | `answer_draft_questions` 只看哈希，旧草案字节永不变 ⇒ 第二个标签页可用旧表单作答 | 已修：409 `draft_superseded`（ADR-213，提交 `76df625e0`） |
| 「未知」被写成 0 并标成可用 | `metrics.py:220-221` 空集均值写 `0.0`；`backtest_service.py:352-353 is_available=value is not None` | 已修：`None` + notes（ADR-214） |
| 干净周线永远被判 `partial` | 空洞阈值是固定 5 个日历日，而周线相邻两根隔 7 天 | 已修：按周期取阈值（ADR-215） |
| 失败的研究运行在列表里说不出原因 | `run_summary` 投影不含 `error_message` | 已修：列表带上它（ADR-216） |
| 未知地址是一片空白 | 没有 404 兜底路由 | 已修：`NotFoundView.vue` + `/:pathMatch(.*)*`（ADR-216） |
| 「最新已收盘 K 线」可能返回没走完那根 | `latest_closed_bar_time` 直接 `return series.series_end` | 已修：走 `load_bars(only_closed=True, limit=1)`（ADR-217） |
| 夜间同步丢「刷新了几根」 | `sync_market_data` 调 `upsert_bars` 没传 `report` | 已修：返回值带 `updated`（ADR-217） |

---

## 4. 本次实际修复的问题、修改文件与提交

| 提交 | ADR | 内容 |
| --- | --- | --- |
| `76df625e0` | ADR-213 | 作答只能发生在这次研究**当前**那一版草案上（409 `draft_superseded`，零写入） |
| `3f17d2550` | ADR-214/215/216 | 未知不入 0；空洞按周期量；失败原因进列表；未知地址有出口 |
| `69c7b1258` | ADR-217 | 「最新已收盘」真读已收盘 K 线；夜间同步报出刷新几根 |

每个提交只包含本任务明确产生的文件；用户自有文档与 `AGENTS.md`/`docs/agents/` 全程留在工作区未提交。每次提交前都比对了开始时的 sha256 指纹快照（`%TEMP%\mql-audit-workspace-state.txt`），六个用户文件指纹一致。

---

## 5. 实际执行的测试与结果

**本地（Windows，本机无 Docker/PostgreSQL/Redis；pytest 从 `Q:\backend` 跑，均带 `-o addopts= -p no:cacheprovider --basetemp`）：**

| 命令 | 结果 |
| --- | --- |
| `pytest tests/test_draft_answers.py tests/test_lab_journey_contracts.py` | 70 passed |
| `pytest tests/test_metrics_finite.py tests/test_backtest.py tests/test_paper_engine.py` | 48 passed |
| `pytest tests/test_market_data_quality.py tests/test_market_data_providers.py tests/test_series_resolution.py tests/test_series_content_hash.py tests/test_series_deletion.py` | 68 passed |
| `pytest tests/test_bar_lifecycle.py tests/test_task_idempotency.py tests/test_market_data_quality.py tests/test_market_data_providers.py` | 61 passed |
| 契约守卫组（`test_ui_promises.py test_api_spec_truth.py test_frontend_contracts.py test_lab_journey_contracts.py test_migration_revisions.py test_examples.py`） | 108 passed |
| `ruff check app tests` / `ruff format --check app tests` | All checks passed! / 246 files already formatted |
| `scripts\Invoke-FrontendChecks.ps1`（typecheck + build） | FRONTEND OK |

**浏览器（本机栈：uvicorn `app.api.main:app` + 镜像 vite + AI 桩 + CDP Chrome）：**

| 脚本 | 结果 |
| --- | --- |
| `%TEMP%\mql-accept\g7-stale-tab-uat.mjs`（ADR-213） | **13/13 通过** |
| `%TEMP%\mql-accept\dead-ends-uat.mjs`（ADR-216） | **14/14 通过** |

**CI（GitHub Actions，`ci.yml` 四个 job）：**`3f17d2550` 的 run `38166968679` 四个 job 全绿；`76df625e0` 的 run `38166635700` 全绿。

**未验证（如实声明）**：PostgreSQL 上只有 `tests/test_postgres_triggers.py` 与 `tests/test_draft_answers.py` 两个步骤在 CI 里跑；本轮新增/修改的行情、指标、任务相关测试**只在 SQLite 上验证过**，没有在本机 PostgreSQL 上跑过（本机无 Docker/PG，且禁止连生产 NAS 代替测试）。

---

## 6. 仍然存在的结构缺口（按 P0/P1/P2）

### P0

1. **模拟执行没有幂等键**（未修，需用户决策）。`backend/app/simulation/paper_engine.py:97 execute_signal` 全文没有 `signal_id` 去重；`backend/alembic/versions/0001_initial_schema.py:439-461` 建 `paper_orders` 时没有 `UniqueConstraint(signal_id)`；部分平仓可重复扣（`paper_engine.py:394-420` 每次调用新建一条 `PaperTrade`）；只有全仓口径被应用层挡住（`:239-240` 重复 BUY、`:335-336` 空仓 SELL）。既有测试 `backend/tests/test_paper_engine.py:250-267` 的第二次 422 是因为中间把**账户**关了（`:261-262`），掩盖了这个问题。

### P1

2. **`quality_status` 不参与任何下游判定**。只有两处写（`backend/app/workers/tasks.py:111`、`backend/app/api/routers/market_data.py:232`），三处读且全是展示；`backend/app/data/backtest_service.py:169-174` 只做 `only_closed=True` + `MIN_BACKTEST_BARS` ⇒ `quality_status="invalid"` 的序列照样进回测/信号/模拟账户。**是否该拦住回测是产品决策**（拦了会让既有回测突然不可用）。
3. **质量只覆盖本次抓取窗口**（`market_data.py:231`、`tasks.py:111` 传的都是 provider 刚返回的 frame）⇒ 字段真实语义是「最后一次抓取的质量」，更早的空洞发现不了。改名或改为全量评估都是安全的，但语义变更需先定。
4. **`result_hash` 口径偏窄**。`backend/app/research/engine.py:519-532` 的 payload 只含 `strategy_version` 标签、`dataset_hash`、`engine_version`、`feature_version`、`parameters`、`metrics`、`trade_count`，**不含 DSL 正文/成交模型/git 代码版本** ⇒ 哈希不能自证「跑的是哪份 DSL」。按 `docs/07_BACKTEST_ENGINE.md:64-69`，改口径须同版提升 `ENGINE_VERSION`（当前 `engine.py:48 "1.2.1"`）⇒ 会改变历史可比性，需用户确认。
5. **失败/中断的重试路径不完整**：列表已能说原因（ADR-216），但没有「重跑研究员阶段」的接口——`backend/app/api/routers/ai.py:642 start_research_run` 会重新付费，`:817 formalize_hypothesis_endpoint` 只补 architect 阶段。
6. **GitHub 监视器自动创建并激活**策略版本（`backend/app/workers/tasks.py:364-402`，`:402 make_current=True`），而 AI 路径是 `make_current=False`（`ai.py:983`）⇒ 是否需要人工确认才激活，是安全边界决策。
7. **前端没有测试框架**（`frontend/package.json` 只有 dev/build/preview/typecheck）。当前靠 Python 文本扫描守卫（`backend/tests/test_frontend_contracts.py` 等四个文件），它们**钉不住**真实渲染、异步竞态、未知路由兜底之外的交互。是否引入 e2e/组件测试需要决策。
8. **`is_experimental_of` 恒为 NULL 却对外发布**（`backend/app/domain/models.py:1070`；`backend/alembic/versions/0013_research_layer.py:153`；`research.py:1391`）⇒ 「AI 替代实验」机制要么实现要么删字段。
9. **AI provider 无重试/无故障转移**（`backend/app/ai/provider.py:403` 单次 `httpx.post`）⇒ 一次网络抖动等于一次研究失败。
10. **默认没有身份认证**（`backend/app/api/main.py:141-154`：`API_AUTH_TOKEN` 未设即放行；`backend/app/ai/confirmation.py:76-79 DEFAULT_ACTOR`）。

### P2

11. `dataset_version` 永远是 `"v1"`（`models.py:132`；`market_data_repo.py:152,161,171` 无人传参），而 `BacktestRun.dataset_version_id` 实际存的是 `series.id`（`backtest_service.py:245`、`experiment_service.py:304`）⇒ 改名/补注释即可。
12. `fill_model` 是死配置却在前端当「执行模型」展示（`backend/app/strategies/dsl.py:218` 定义、`compiler.py:842-854` 回显，`engine.py:273` 与 `backend/app/research/ensemble.py:209` 只读 `entry_order_type`；`frontend/src/views/BacktestView.vue:3269` 展示）。
13. CI 的 `Smoke 4/5: run backtest`（`.github/workflows/ci.yml:422`）只断言 `status=='completed'`（`:438`），不校验 `result_hash`。
14. CI 没有前端测试步骤；迁移链只被 `upgrade` 覆盖（`backend/tests/test_migrations_sqlite.py:45` 只跑 `command.upgrade(...,"head")`），21 个 `downgrade()` 从未执行。
15. `/health` 的 `status` 不含 redis/workers（`backend/app/api/routers/health.py:167-168`）。
16. 证据校验的兜底：`backend/app/ai/research_schemas.py:1140` 在 `text_hash` 为 NULL 时用本次读到的正文自算哈希（未知 ref 已先判 `evidence_unknown_source`，不可伪造，但缺 `text_hash` 时应改判 violation）。
17. 「只有两条草案写入路径」靠约定维持（`research.py:951`），没有静态断言钉住。
18. `frontend/src/views/LabView.vue` 的 `openExperimentById`：只有 404 分支清 `openExperiment`，其它错误会同时显示上一次的实验详情与「读不到这条实验」。
19. `docs/13_UI_UX.md` 的欠账节写着「（无）」，与实际不符。
20. `backend/app/data/market_data_repo.py` 的 `latest_closed_bar_time` 全仓零调用者（ADR-217 已把它改对，但没消费者）。

### 文档陈旧快照（已核实，暂不改）

- `docs/26_AI_QUANT_LAYER_GAP_ANALYSIS.md:263` 把 `is_experimental_of` 说成「AI 替代实验标记」；
- `docs/32_7_TO_3_CONTAINER_FEASIBILITY_AUDIT.md:418`、`docs/33_V2_6_P0_RELIABILITY_AND_3_CONTAINER_IMPLEMENTATION_PLAN.md:59,1771` 三处「仓库里没有任何 reaper」全部陈旧（reaper 已存在：`backend/app/data/run_recovery.py:147 recover_stuck_runs`、`backend/app/workers/tasks.py:188-198`、`backend/app/workers/celery_app.py:83-84`）；
- `docs/30_PHASE_C_PERFORMANCE_RISK_BENCHMARK.md:79` 写着「无样本时 `0.0`」，已被 ADR-214 改变（该文件是历史基准快照）；
- `docs/34_RESEARCH_JOURNEY_CLOSURE.md` 的 G1–G6 已修、G7 已由 ADR-211 完成，仅 G8/G9 仍欠。

---

## 7. 必须由用户决定的事项

1. **草案点名标的（`universe`/`markets`）会进入无法通过作答修复的终止状态**。这是**冻结契约的既定行为**，不是实现缺陷：`backend/app/compiler/compiler.py:958-990` 的 `_read_market_block` 与 `:601-627` 的 `_map_market_rule` 对它记 `not_expressible`（结构码，永远不出现为可回答问题）；`docs/29_STRATEGY_COMPILER_CONTRACT.md:208-210`、`:230`、**`:791`（明令禁止把 `universe="BTC"` 偷换成 symbol）**、`:809`（`test_martin_scenario_stays_uncompilable`）把它钉死。矛盾在于 **AI 层自己的契约要求填这两个字段**：`backend/app/ai/contracts/STRATEGY_ARCHITECT.md:58`、`backend/app/ai/contracts/RESEARCHER.md:30,44`。⇒ 两条出路都越界（改 AI 契约语义，或改冻结 `docs/29`），故不自行实施。需要用户选：**(a)** 明确「标的不是策略内容，草案不许点名标的」（改 AI 契约与提示词）；**(b)** 给 DSL 加标的字段（改冻结契约，等于扩展策略表达能力）；**(c)** 保持现状，只把 UI 文案说得更准（当前只能笼统说「无法用这套策略语言表达」，且不能承诺「重新生成一版就好」）。
2. **`market.allow_short` 不是可回答问题**：`backend/app/ai/draft_answers.py` 的 `UNSUPPORTED_SLOTS` 明确拒绝并给出中文原因（草案格式没有承载它的字段、编译器不读它）。做空能力本身需要产品决策。
3. **模拟执行幂等**（P0-1）：重复请求应当 409 还是幂等返回原单？历史重复数据如何处置？
4. **`quality_status` 是否应拦住回测/信号**（P1-2、P1-3）：拦住会让既有回测突然不可用。
5. **`ENGINE_VERSION` 与 `result_hash` 口径**（P1-4）：是否接受一次口径变更与随之而来的历史可比性断裂。
6. **GitHub 监视器是否自动激活**（P1-6）：自动激活等于外部代码直接成为当前策略。
7. **前端测试框架**（P1-7）与**默认身份认证**（P1-10）是否纳入近期范围。
8. **`is_experimental_of`**（P1-8）：「AI 替代实验」要实现还是删字段。
9. **AI provider 重试/故障转移**（P1-9）与**部分重跑研究员阶段**（P1-5）的计费与状态语义。
10. **`/setup-matt-pocock-skills` 的收尾**（与本审计无关，仍待用户操作）：9 个 GitHub 标签因细粒度 PAT 缺 `pull_requests: write` 无法创建（可补权限、换 classic PAT 或网页手工建）；探针 issue #1 `setup probe`、#2 `zz-probe-keyring`、#3 `zz-hdr-allowed`、#4 `zz-probe-env`、#5 `zz-curl-probe` 需手工删除。

---

## 8. 后续推荐顺序

1. （P0）模拟执行幂等键 —— 需先定语义（等第 3 项回答）。
2. （P1）前端 404 之外的两处小修：`LabView.vue openExperimentById`、`docs/13_UI_UX.md` 欠账节；这两项无需决策。
3. （P1）`result_hash` 口径 + `ENGINE_VERSION`（等第 5 项回答）。
4. （P1）`quality_status` 的下游语义（等第 4 项回答）。
5. （P1）标的表达能力（等第 1 项回答）—— 这是唯一一处「用户流程无法自行走完」的结构缺口。
6. （P2）`dataset_version` 命名、`fill_model` 展示口径、CI 冒烟校验 `result_hash`、迁移 `downgrade` 覆盖、`/health` readiness 字段、`text_hash` 兜底改 violation。
7. （P2）前端测试框架与默认认证（等第 7 项回答）。

---

## 9. 本报告的维护说明

- 本报告随实施更新：每个合并的修复都会在 §3.1/§4/§5 追加，并在 §6 划掉已修项。
- §5 严格区分「本地通过 / CI 通过 / 未验证」，不把未执行的测试写成通过。
- 本报告只记录事实与证据；所有需要业务语义的选项集中在 §7，不在代码里替用户决定。
