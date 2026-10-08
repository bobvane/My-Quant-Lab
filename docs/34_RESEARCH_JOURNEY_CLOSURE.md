# v2.7.0 研究旅程闭环：AI 研究 → 草案 → 编译 → 策略版本 → 回测 → 大白话解释

状态：已实施并通过验收（2026-10-09）。v2.6.0 已于 2026-10-08 发布：commit `0dfd0f72c`、tag `v2.6.0`、GHCR `sha256:5207555d…` 且 `latest` 已更新。

这一份文档只回答一个问题：**普通用户现在能不能在浏览器里，从「我有一个想法」走到「我看到了结论和人话解释」？**
如果中间还有一段要靠他自己把参数想出来、把页面猜出来，那一段就是本版要补的缺口。

---

## 1. 目标（用户当晚的要求）

一条链，中间无人工断点：

```
一句话/一段材料 → AI 理解 → 策略假设 → 策略草案 → 人工确认
  → 编译（现有编译器） → 可执行策略版本 → 回测（现有引擎） → 结论 + 大白话解释
```

硬约束：

- **不新建平行架构**。草案必须走现有编译器（`backend/app/compiler/`），版本必须进现有回测引擎（`backend/app/research/engine.py`），解释必须走现有 `run_task()` 与既有解释端点。
- **AI 永远不是回测数字正确性的依赖**。没有 Provider、超预算、解释被守卫拒绝时，页面上的数字一个都不能少。
- **不破坏已被验收的可靠性机制**（Celery late ACK、WorkerLost 重投、任务幂等、终态守卫、研究续跑、通知至多一次、savepoint、卡死恢复、visibility timeout、Beat 独立进程、单容器启动器）。

---

## 2. 现状（侦察结论，均有代码与测试为证）

**后端这条链已经存在，而且已经有 API 级测试。**

| 环节 | 实现 | 证据 |
| --- | --- | --- |
| 研究运行 | `POST /ai/research`（同步 200 / 异步 202） | `backend/app/api/routers/ai.py:594` |
| 假设 + 草案 | `strategy_hypotheses` / `strategy_drafts`，`executable=false` | `backend/app/ai/research.py:1017` |
| 人工确认 | `POST /ai/strategy/drafts/{id}/confirmations`（`strategy_version_created` 恒 false） | `ai.py:980` |
| 编译 | `POST /ai/strategy/drafts/{id}/compile` → 409/422/201 | `ai.py:840`；`docs/29` 冻结契约 |
| 策略版本 | `create_strategy_version(..., make_current=False)` | `backend/app/data/strategy_service.py:186` |
| 回测 | `POST /backtests` → 现有引擎 | `backend/app/api/routers/backtests.py:42` |
| 解释 | `POST /backtests/{id}/explain`、`/explain-performance` | `ai.py:242` / `ai.py:266` |

`backend/tests/test_ai_closure_end_to_end.py:277 test_the_whole_closure_runs_through_the_api` 已经从 API 层把整条链跑通（含两次 httpx 调用、两个 AITask、`cost_usd == Decimal("0.000252")`、审计顺序）。

**所以本版的缺口不在后端，而在浏览器里的那一半。** 下面每一条都能用文件行号复现。

### 2.1 缺口清单

- **G1（P0，断链）从草案到回测的交接不成立。**
  `frontend/src/views/LabView.vue:812-816` 的 `goToBacktest()` 只带 `strategy_version_id`，不带 `run=1`，也不带标的；接收端 `frontend/src/views/BacktestView.vue:1696` 只在 `run=1` 时自动跑。用户以为点一下就能看到结论，实际要在回测页再选一次版本、自己手输一个标的代码、再点一次「开始回测」。
  对照：`frontend/src/views/ResearchView.vue:194-210`（`/research` 那条链）是带 `run=1` 的，`docs/13_UI_UX.md:196-198` 把「不需要用户再点一次运行」写成了承诺（ADR-132）。**同一条产品承诺，在 AI 这条链上没有兑现。**
- **G2（P0，找不到入口）普通用户不知道这条链存在。**
  首次使用引导卡 `frontend/src/views/DashboardView.vue:516-527` 的四步只提到「数据 → 研究策略 → 回本页 → 模拟验证」，没有 `/lab`；「我在研究什么」卡 `:530-535` 在无策略时只指向「我的策略」。`/lab` 在左栏只是四个字的文字链接。
- **G3（P0，位置）AI 汇总埋在页面最底部。**
  `frontend/src/views/BacktestView.vue:2897-2945` 的「AI 汇总」卡排在权益/回撤曲线（`:2884-2893`）之后。**实施时修正了这条缺口的依据**：`docs/30_PHASE_C_PERFORMANCE_RISK_BENCHMARK.md:336-344` §9.2 要求落在「可信程度怎么样？」之后、「高级提示」之前的是**绩效/风险/对照卡组**，而那个卡组本来就在那个位置（`:2078`），所以它不是「AI 汇总卡放错了」的证据。真正的依据是 ADR-128「结论先行」与 `docs/13_UI_UX.md` §13「一级指标之后才轮到研究工具」：AI 汇总是结论的人话版本，属于结论区；权益/回撤曲线与高级分析属于研究工具。`docs/13_UI_UX.md:85` 只规定这个区块的名字与固定五段，不规定位置。
- **G4（P1，不诚实）空态写了一句实现里没有的话。**
  `frontend/src/views/BacktestView.vue:2942-2944`：「未配置 AI 时这个按钮不可用」。全文件没有任何 `api.aiStatus()` 调用，按钮永远可点；真正发生的是点下去之后报 503。这违反 `docs/13_UI_UX.md:181`「点不动的按钮必须说明为什么」的反面——**说得到做不到**。
- **G5（P1，材料）只能贴一段纯文本。**
  `frontend/src/views/LabView.vue:1651-1655` 自己承认「后端本身支持按 URL、PDF、GitHub 文件抓取材料，但还没有接到这个页面上」；`submit()` `:610-633` 只送一个 `user_input`。后端能力（`POST /ai/sources/url`，带 SSRF 守卫）从 v2.1.0 就在，UI 侧零接线。
- **G6（P2，文档自相矛盾）** `docs/13_UI_UX.md:15` 写「共 11 条」导航，实际 10 条（`frontend/src/App.vue:70-81`、`frontend/src/main.ts:20-29`）；§11「研究策略（从想法到一次回测）」把主流程定义成 `/research` 四步向导，**完全不含 AI**，与 `docs/25_AI_QUANT_RESEARCH_LAYER_PLAN.md:2375-2454`/`:2966-3051` 的场景 1「一条链，中间无人工断点」不一致。
- **G7（本版不做，见 §5）编译器「需用户回答」没有回答入口。**
- **G8（本版不做）「继续研究」V1→V2 迭代在 UI 上无入口。**
- **G9（本版不做）Lab 页无前端行为测试框架**（`frontend/package.json:6-11` 只有 `dev`/`build`/`preview`/`typecheck`），守卫只能靠后端文本扫描测试。

---

## 3. 本版切片

四个切片，全部只动前端 + 文本守卫 + 文档。**后端零改动**（既有的四个端点已经够用），因此对 §1 的可靠性红线是零风险。

### M1（P0）Lab → 回测 一次性交接

- `frontend/src/views/LabView.vue` 的 ⑦ 版本卡加一个**标的（数据序列）下拉**，数据来自现有 `GET /assets` + `GET /series`（与 `/research` 页同一个来源，`frontend/src/views/ResearchView.vue:31-43` 的映射逻辑照抄，不新造 API）。
- 选中后按钮跳 `/backtest?strategy_version_id=<id>&symbol=<symbol>&timeframe=<tf>&run=1`，由接收端自动开跑（`frontend/src/views/BacktestView.vue:1696`/`:1729` 已经实现，不用改）。
- 为什么必须由用户选标的：DSL 表达不了「标的」，编译器把它留在 `not_expressible`（`backend/app/compiler/compiler.py:617`），标的属于**运行参数**而不是策略内容。所以这里不是「再问一个问题」，而是把本来就该由人定的一件事在正确的位置问一次，并且给足理由与人话选项。
- 没有可用的序列时，按钮说明原因并指向「数据」页（`/data`），不留灰按钮。

### M2（P0）入口与下一步

- `frontend/src/views/DashboardView.vue` 的首次引导卡从四条改成五条，第 2、3 条并列为两条入口：② 已经知道想试什么规则 → `/research`；③ 只有一句想法、说不上规则 → `/lab`（写明「它走的还是同一个引擎」）。**实施时推翻了本计划原先「四步不动、只加一句话」的写法**：只加一句话的话，第 2 条仍然是对他说的、第 3 条仍然被跳过，而 ①②③④ 的编号会与卡片里的四条冲突；把入口数如实写成两条，读者才能按自己的处境选一条（ADR-193）。
- `frontend/src/views/LabView.vue` 页头（「这一页在做什么」卡）已经自述链路，本版不再改。
- 「下一步」提示：`/lab` 的 ⑦ 版本卡与 `/research` 的 ④ 都通向同一条 query（M1），所以回测页不需要为两个入口写两套提示。

### M3（P0/P1）解释落在对的位置，并且说实话

- 把 `frontend/src/views/BacktestView.vue` 的「AI 汇总」卡移到「可信程度怎么样？」之后、绩效/风险/对照卡组之前（依据是 ADR-128「结论先行」与 `docs/13_UI_UX.md` §13「一级指标之后才轮到研究工具」；`docs/30:336-344` 那段要的是绩效/风险/对照卡组，它本来就在那之后，所以 AI 汇总卡排在它前面而不是插在它中间）。
- 页面挂载时真读一次 `GET /ai/status`（`frontend/src/api.ts:1729` 已有 `aiStatus`）：`configured === false` 时按钮 disabled 并写明原因 + 指向「系统管理」；`configured === null`（没问到）时**保持「不知道」**——按钮可用、文案不声称任何一方，因为读不到状态不等于没有 Provider。空态文案改掉「未配置 AI 时这个按钮不可用」这句没问过服务端的话。
- 从实验室交接过来（`run=1`）的回测跑完后，**自动请求一次**「AI 汇总」（`POST /backtests/{id}/explain`——**修正**：本计划原先写的是 `/explain-performance`，那是 Phase C 那张「AI 用大白话解释这次分析」卡背后的端点；空态假话所在的那张卡是「AI 汇总」，它对应的是 `/explain`），仅在 Provider 已配置时发生，按 run id 只发生一次，失败**完全静默**。理由：`docs/25` 场景 1 要求这条链中间没有人工断点，而 `docs/13:252`「AI 解释是加法，不是链路」说的是**数字不依赖 AI**——两者可以同时成立：解释是自动去要的，要到要不到都不影响结论。**这是对既有承诺的修改，写进 ADR-192。**

### M4（P1）材料能贴网址

- `frontend/src/views/LabView.vue` 的材料区加「贴文字 / 贴网址」二选一；贴网址走现有 `POST /ai/sources/url`（`backend/app/api/routers/sources.py:89`，SSRF 守卫在 `backend/app/sources/guard.py`），拿到 `snapshot_id` 后按 `{kind:'url', uri, snapshot_id}` 送进 `POST /ai/research`（`backend/app/api/schemas.py:1194-1219` 已支持）。
- `frontend/src/api.ts` 增加 `aiSourceUrl` 客户端方法，并把 `aiResearchStart` 的 payload 类型补齐 `uri`/`snapshot_id`/`retention`。
- 抓取失败必须说清是「这个地址不允许抓」（422 `snapshot_status="blocked"`）还是「这个地址读不到」（502），并且**绝不在失败后继续调用研究接口**。
- 同时必须改掉 `backend/tests/test_lab_journey_contracts.py:193-197` 里那条把「还没有接到这个页面上」钉成事实的断言——它的名字就是 `test_the_page_no_longer_claims_it_cannot_read_the_web_or_pdf`，本版正是它期待的那个「接上了」的时刻。

---

## 4. 验收

- 后端：`backend/tests/test_lab_journey_contracts.py`（按 M1/M3/M4 改）全绿；`test_frontend_contracts.py`、`test_ui_promises.py` 全绿；全量 pytest 与 v2.6.0 基线一致。
- 前端：`npm run typecheck`（`vue-tsc -b`）、`npm run build` 通过。
- 浏览器 UAT（本地栈，`scripts\Start-LocalStack.ps1`）：Path A 研究→看到结果；Path B 研究→草案；Path C 草案→编译→版本；Path D 版本→回测→结论；Path E 结论→AI 解释（无 Provider 时验证「按钮不可用且说清原因」这条确定性分支）。
- 可靠性回归：本版不动 Celery / 迁移 / 容器 / compose / 引擎 / 编译器 / 数据库，相关测试只做全量复跑，不新增。

---

## 5. 本版明确不做（附原因）

- **编译器 `NEEDS_USER_DECISION` 的回答入口**（G7）。这是夜跑里最像「断链」的一处：`docs/29_STRATEGY_COMPILER_CONTRACT.md` 是**冻结契约**（`:135` 不得覆盖参数、`:534` 只吃持久化的能力报告、`:544` 不得创建 AITask/预算、`:823-835` 十三条禁令），把「用户的回答」变成编译器输入需要改契约 + `CompilerInput` + 新列/迁移（下一个迁移号 `0021`），并且 `docs/29` 的修订必须由人复审。**属于「外部契约变更」，按当晚规则记录而不是擅自改。**
- **V1→V2 AI 实验迭代端点**（G8，`POST /ai/experiments`，`docs/26:306`）：需要新的实验语义与研究层状态机，属于新架构面，不在「复用现有」的范围。
- **结构化 findings/risks/citations/steps、run 级预算、PDF/GitHub 文件材料、多段纯文本**：研究层 14 条 GAP 的一部分，需要新列与新迁移，本版不碰。
- 站点级视觉改版、框架/组件库替换、微服务、Redis/PG 重构、新队列/新库、重写回测引擎、大重构、`README` 与 `docs/27` 的措辞、旧 GHCR 镜像清理。

---

## 6. 实施记录（2026-10-09）

### 6.1 落点

| 切片 | 改动 | 文件 |
| --- | --- | --- |
| M1 | ⑦ 版本卡新增「用哪份数据验证这一版？」标的（数据序列）下拉，数据来自现有 `GET /assets` + `GET /series`；交接按钮跳 `/backtest?strategy_version_id=<id>&symbol=<symbol>&timeframe=<tf>&run=1`；无可用序列时写明原因并指向「数据」页 | `frontend/src/views/LabView.vue`（`loadBacktestTargets()`、`chosenBacktestTarget`、`backtestBlockedReason`、`goToBacktest()`、`#lab-backtest-target`） |
| M2 | 首次引导卡四条改五条，第 2、3 条并列为两条入口（`/research` 与 `/lab`），标题「第一次用？按这五步走」 | `frontend/src/views/DashboardView.vue:515-535` |
| M3 | 「AI 汇总」卡从权益/回撤曲线之后移到「可信程度怎么样？」之后、绩效/风险/对照卡组之前；挂载时真读 `GET /ai/status`（`aiConfigured: boolean \| null`）；从实验室交接（`run=1`）的这一次跑完自动请求一次 `POST /backtests/{id}/explain`，只一次、失败静默；空态文案改成实话（未配置 → 按钮 disabled + 指向「系统管理」+「上面的结论、风险与可信程度都不受影响」；读不到状态 → 保持「不知道」，按钮可用） | `frontend/src/views/BacktestView.vue`（`aiConfigured`、`loadAiStatus()`、`labHandoff`、`autoExplainHandedOverRun()`、`explainCurrent({silent})`、AI 汇总卡 `:2121-2180`）、`frontend/src/api.ts`（`aiStatus` 已在，新增 `aiSourceUrl`） |
| M4 | 材料区「贴一段文字 / 给一个网址」二选一；网址走现有 `POST /ai/sources/url`（SSRF 守卫不变），拿到 `snapshot_id` 后按 `{kind:'url', uri, snapshot_id}` 送研究接口；抓取失败按 422/502 说清是「不允许抓」还是「读不到」，且**绝不继续提交研究** | `frontend/src/views/LabView.vue`（`materialKind`、`sourceUri`、`buildSource()`、`ingestProblem()`、`SOURCE_REFUSAL_TEXT`）、`frontend/src/api.ts`（`aiSourceUrl`、`AIResearchSourceInput`） |
| 文档 | ADR-191/192/193；`docs/13_UI_UX.md`（导航条数 11→10、§11 新增「从一句想法开始」、§15 新增「AI 汇总卡在结论区」）；本文件 | `docs/17_DECISIONS.md:3443/3455/3467`、`docs/13_UI_UX.md` |
| 守卫 | `test_lab_journey_contracts.py` 钉新交接 query、网址材料接线、并**删除**「还没有接到这个页面上」那条旧断言；`test_frontend_contracts.py` 新增位置守卫与自动解释守卫、引导卡改五步 + 两个 `RouterLink` | `backend/tests/test_lab_journey_contracts.py`、`backend/tests/test_frontend_contracts.py` |

后端一行未改：既有的 `POST /ai/research`、`/confirmations`、`/compile`、`POST /backtests`、`POST /backtests/{id}/explain`、`GET /ai/status`、`POST /ai/sources/url` 已经够用，所以 §1 的三条硬约束（不新建平行架构、AI 不是数字依赖、不动已验收的可靠性机制）是结构性成立，而不是「小心避开」。

### 6.2 实施中修正的两处计划

1. **G3 的依据写错了**（已在 §2.1 中改正）：`docs/30:336-344` §9.2 要求落在「可信程度」之后的是绩效/风险/对照卡组，而它本来就在那里（`BacktestView.vue:2078`）。「AI 汇总」卡上移真正的依据是 ADR-128「结论先行」与 `docs/13_UI_UX.md` §13「一级指标之后才轮到研究工具」。
2. **编译要求草案把该定的都定下来**：第一版浏览器 UAT 卡在 Path C，服务端如实回了 `422 {"result":"NEEDS_USER_DECISION"}`——草案没写 exit/risk/sizing/手续费时，编译器拒绝替用户编默认值（`docs/28` §11「默认值炸弹」）。这**不是**缺陷，是产品行为；实验里换了「已决草案」后 `201 {"result":"COMPILED"}`、六条规则全 `MAPPED`、`rejections=[]`。含义：Path A 的成败取决于模型是否给出可编译草案，前端到编译为止的接线本身是通的。**尚未解决的是 G7（让用户回答「需要人定的事」），本版不做。**

### 6.3 验收结果

- **后端聚焦**：`test_lab_journey_contracts.py`、`test_frontend_contracts.py`、`test_ui_promises.py`、`test_ai_closure_end_to_end.py`、`test_draft_confirmation.py` → `84 passed in 72.51s`。
- **后端全量**：`python -m pytest tests -o addopts= -p no:cacheprovider -q`（workdir `backend/`）→ **1633 passed, 6 skipped in 297.89s**；v2.6.0 基线为 1630 passed / 6 skipped，多出的 3 条即本版新增/改写的守卫。可靠性机制（Celery late ACK、WorkerLost 重投、幂等、终态守卫、续跑、通知至多一次、savepoint、卡死恢复、visibility timeout、Beat 独立进程、单容器启动器）只做全量复跑，未新增测试、未改实现。
- **前端**：`npm run typecheck`（`vue-tsc --noEmit`）无输出；`npm run build` → vite v6.4.3、626 modules、`dist/assets/index-CKTOcqNH.js` 1,583.81 kB（gzip 523.62 kB）、17.03s，只有大 chunk 警告。
- **Lint**：本次改动范围 `ruff check` + `ruff format --check` 均干净（只允许改本次范围，不动历史基线）。
- **浏览器 UAT（本地栈 = `uvicorn 127.0.0.1:8080` + `vite localhost:5173` + `%TEMP%` 里一个 OpenAI 兼容桩 Provider，NAS 与生产库全程未碰，未申请任何真实 key）**：
  - **A 研究 → 看到结果**：提交「马丁说，下跌之后出现反转就买入…」→ 运行完成（`/lab?run=7`），草案含 6 条规则（市场/入场/出场/风控/仓位/成交），每条标注「AI 提出的假设，不是你的原话」，能力检查「完整支持」。
  - **B 研究 → 草案**：草案自带「当前人工结论：尚未人工确认」与确认/驳回/需修改三个按钮。
  - **C 草案 → 编译 → 策略版本**：点「确认」后编译 → 「草案已经编译成策略版本 **1.0.2**（策略 #1）」，`校验状态：已通过校验`，`是否当前版本：不是，还没激活`。
  - **D 版本 → 回测 → 结论**：交接口选 `DEMO-AAPL · 日线` → 跳 `/backtest?strategy_version_id=3&symbol=DEMO-AAPL&timeframe=1d&run=1` → **自动开跑**（用户没再点运行）→ 结论卡「样本太少（2 笔），先别下结论 / 累计收益 2.84%，期间最大回撤 -4.45%」。
  - **E 结论 → AI 解释**：交接来的这一次**自动**取回解释，五段齐全（① 结论 ② 原因 ③ 风险 ④ 可信程度 ⑤ 下一步），卡片位置在「可信程度」之后、绩效/风险/对照卡组之前。
  - **E2 未配置分支**：把 Provider 置为 `is_active:false` 后刷新，卡片写「这个实例未配置 AI，所以「生成解读」暂时点不了…到「系统管理」的 AI 那一栏填一个 Provider 就能用。上面的结论、风险与可信程度都不受影响——那些数字是引擎算出来的，不经过 AI。」——**不再有「未配置 AI 时这个按钮不可用」那种没问过服务端的话**（G4 关闭）。
  - 全程 `consoleErrors=[]`、`httpErrors=[]`。

### 6.4 过程中修掉的两个环境坑（与产品代码无关）

- **`database is locked`**：本地验证库原本放在映射的 SMB 盘上（`backend/.local-verify.sqlite3`），第一次浏览器 UAT 提交研究时 `POST /ai/research` 500，抛点是 `backend/app/api/routers/ai.py:640` 的 `db.commit()`，且**一个模型调用都没发生**。SQLite 的文件锁跨 SMB 不可靠；把库复制到本地盘（NTFS）后同一操作稳定通过。这只影响本地验证姿势，不影响部署形态（生产是 PostgreSQL，且 NAS 全程未碰）。
- **`alembic ... No 'script_location' key found`**：从仓库根跑全量 pytest 时，`test_audit_action_length.py` 与 `test_migrations_sqlite.py` 共 5 条报此错——alembic 需要工作目录是 `backend/`（`alembic.ini` 在那里）。以 `backend/` 为工作目录复跑即全绿（§6.3 的 1633 passed）。**不是代码失败。**
- 顺手修 `scripts/Invoke-FrontendChecks.ps1`：镜像目录的 robocopy 由 `/E` 改成 `/MIR`。镜像目录是持久化的，仓库删掉的文件（如 v2.6.0 删掉的 `src/views/ResourcesView.vue`）原本会留在镜像里被 `vue-tsc` 扫到，报出 4 条指向已不存在代码的 `TS2339`；`/MIR` 让镜像与仓库一致（`/XD node_modules dist` 保证这两者既不进镜像也不被清掉）。
