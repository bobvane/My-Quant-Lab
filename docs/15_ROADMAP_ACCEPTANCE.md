# 15 Roadmap & Acceptance Criteria

## Implementation status (as of v1.0)

All phases below are implemented and covered by automated tests; CI builds the
stack and runs an end-to-end smoke test on every push.

- Phase 0 Foundation - DONE (compose, migrations, health)
- Phase 1 Market Data + Quant Core - DONE (indicators, PA features, DSL, quality)
- Phase 2 Backtest Lab - DONE (engine, costs, metrics, walk-forward, OOS, comparison)
- Phase 3 Ghostfolio - DONE (read-only adapter, holdings, portfolio context, symbol aliasing)
- Phase 4 Paper Trading - DONE (execution engine, positions, funding, lifecycle, reset audit)
- Phase 5 AI Layer - DONE (provider abstraction, budget, cache, structured explanations, routing)
- Phase 6 GitHub Importer - DONE (AST analysis, provenance, watched sources, auto-reimport)
- Phase 7 Live Signal - DONE (scheduler, scanner, dedup, outcomes, notification, noise control)
- Phase 8 Strategy Lifecycle - DONE (evidence-gated promotion/degradation, dashboard)

Definition of Done: met (see the checklist below), including the no-auto-trading
boundary and the no-lookahead / immutability guarantees.

## Post-V1 progress

### v1.0.0 — released and verified on a real NAS

- 三个 GHCR 镜像（backend / web / docker-proxy）发布；release 工作流对**已发布镜像**跑冒烟。
- NAS 端到端检查 12/12 通过：Web `/healthz` 与首页、API `/health`（DB/Redis/worker 全绿）、
  `/system/info`、行情同步（含幂等重跑）、建策略与版本（不可变触发器）、回测、扫信号、
  AI 任务路由注册、模拟盘账户。
- `docs/17` ADR-039 收尾项全部落地。

### v1.1 — 完成（含 v1.1.x 补丁）

| 项 | 状态 | 说明 |
|---|---|---|
| 参数覆盖真正生效 + 结果哈希只覆盖有效参数 | DONE | ADR-040。修复「哈希说谎」：override 曾被折进哈希却从未应用 |
| 参数敏感性分析（后端 + API） | DONE | ADR-041 / `docs/21`。网格扫描、统计、稳健性判定、审计事件 |
| 参数敏感性分析（前端热力图） | DONE | 回测实验室接入，1 轴折线 / 2 轴热力图，并列交易数防「靠不交易变好看」；网格按所选版本**已声明参数自动填入**（v1.1.x 补） |
| 示例策略参数化（`period_ref`） | DONE | 两个示例原先的规则引用了**不存在的列**（`ema20`），实际被当作常量 20；已修正并加回归测试 |
| 行情 provider 语义写入文档 | DONE | README 说明换 provider 后演示代码必然 0 根 K 线 |
| Windows/UNC 开发脚本 | DONE | `scripts/Invoke-Tests.ps1`、`Invoke-FrontendChecks.ps1`、`Test-NasDeployment.ps1` |
| 真实浏览器验证脚本 | DONE | `scripts/Test-WebUi.ps1` 用无头 Chrome 断言 Vue 实际挂载与视图渲染（服务端返回 index.html 不能证明 SPA 能跑） |
| 走真实 HTTP 的集成探针 | DONE | `scripts/Test-EnsembleAttribution.ps1`（v1.3.9）：pytest 用 in-process TestClient，从不走网络；该脚本对活的栈建版本、跑回测、调 `POST /research/ensemble`，断言前端依赖的自洽不变量 |
| 清理 `StarletteDeprecationWarning`（httpx） | DONE | 加 `httpx2` 为**测试期**依赖（starlette TestClient 首选它）；应用自身 HTTP 调用仍用 httpx |
| CI action 版本对齐 | DONE | `upload-artifact` v4→v7（消除 Node 20 弃用告警）、`checkout` v5→v7；其余已是各自最新大版本 |
| v2：Monte Carlo 重采样 | DONE | ADR-043 / `docs/22`。交易级 IID bootstrap、分位数与概率、扇形图；纯描述、非预测 |
| 监控写入失败不再破坏主流程 | DONE | ADR-044。可选监控写入失败会污染会话，导致已完成的回测被报 500；已改为提交后独立事务并回滚 |
| v2：组合级仓位管理（Portfolio-aware sizing） | DONE | ADR-045 / `docs/23`。`fixed_fraction`（默认，行为不变）/ `risk_per_trade` / `atr_risk`；按止损距离反推数量，受现金上限约束 |
| v2：策略 Ensemble | DONE | ADR-047 / `docs/24`。加权投票合并决策为**一个**组合；严格多数（`>`）语义、权重归一化、`entry_bars` 与 `entries_taken` 分离、成员无共同 bar → 422 |
| 覆盖合并必须经过校验 | DONE | ADR-046。`model_copy(update=...)` 不校验，嵌套 `sizing` 覆盖被静默忽略并回退默认值；改为 `merge_spec_overrides` 重新校验 |

### v1.2 / v1.3 — 完成

| 版本 | 内容 |
|---|---|
| v1.2.0 | Monte Carlo 重采样（ADR-043）+ 风险型仓位管理（ADR-045）+ 监控写入失败修复（ADR-044） |
| v1.3.0 | 策略集成引擎与 `POST /research/ensemble`（ADR-047）+ 覆盖合并校验修复（ADR-046） |
| v1.3.1 | 集成面板（成员选择/权重/阈值）与**与各成员对比表**；修掉成员指标标签对不上的缺陷 |
| v1.3.2 | 回测表单加入仓位管理选择器（策略默认 / 固定比例 / 按止损风险 / 按 ATR 风险） |
| v1.3.3 | 敏感性分析支持扫描 `risk_pct`（执行轴），可同时看入场参数与风险预算 |
| v1.3.4 | 集成组合权益曲线（复用 `EquityChart`） |
| v1.3.5 | 集成成员候选改为**跨策略**（原先只能选当前策略的版本，「让两个不同策略互相投票」在 UI 上不可达） |
| v1.3.6 | SQLite 上资源表主键不自增导致写入失败（ADR-048，新增迁移 0006 + 真实迁移链测试） |
| v1.3.7 | 修 `latest` 镜像标签被并发发布互相覆盖（只有最高 tag 才移动 `latest`） |
| v1.3.8 | 拒绝重复成员（会归一化成「一个参与者却显示为投票」）+ 权重归一/清空按钮 + 零交易文案 |
| v1.3.9 | 认同归因（ADR-049）：`signalled_bars` / `solo_signalled_bars` / 每成员支持率；对比表标注数据窗口不可比；修做空退出判定残留的 `>=` |
| v1.4.0 | 同口径成员对比（ADR-050）：`member_runs`（每成员在**同一批 bar、同一套成本模型、各自权重资金**下的独立跑分）+ 引擎版本提升为 `ensemble-1.1.0` + gzip 中间件；前端叠加组合与成员权益曲线 |
| v1.4.1 | 修访问日志整条丢失：脱敏过滤器清空 `record.args` 让 uvicorn `AccessFormatter` 解包 5 元组失败（release smoke test 里的 `ValueError: not enough values to unpack (expected 5, got 0)`），改为逐参数脱敏 |
| v1.4.2 | 投票阈值扫描（ADR-052）：`POST /research/ensemble/sweep` 一次给出集成**唯一旋钮**的全部台阶（票数只能落在联盟总数上，故曲面是阶梯），带 `effective_vote` 指明每个阈值实际在等哪个联盟；`ensemble.py` 拆出阈值无关的 `_prepare_ensemble` + 单阈值 `_run_vote`，使扫描点与直接运行「逐项一致」成为结构保证；引擎版本提升为 `ensemble-1.2.0`；前端新增 `ThresholdSweepChart.vue` 与阶梯语义说明 |
| v1.4.3 | 扫描网格必须精确且总能评估（ADR-053）：修 `_coalition_totals` 的逐级取整漂移（十二等权成员的最大票数曾是 `0.999996`，导致默认网格 13 点 > 上限 12，**最宽集成用不了默认路径**）；上限 12 → 64 并把放不下改成可操作的 422；票数比较统一到发布的六位精度（`_clears`），修掉「报告说需要两个成员、模拟却让一个进场」；响应新增 `max_thresholds` |
| v1.4.4 | 引擎的警告必须跟结果一起落库（ADR-054）：`backtest_results.warnings_json` + 迁移 `0007_backtest_result_warnings`，`GET /backtests/{id}` 不再硬编码返回空列表（此前「被忽略的参数覆盖」「warm-up 长于数据」只在创建响应里出现一次，刷新即消失，而后者对应的是一次 `number_of_trades = 0` 的「成功」回测）；前端在指标卡上方渲染回测提醒，扫描面板也显示自己的 `warnings` |
| v1.4.5 | 没跑起来的网格点不能赢排名（ADR-055）：引擎新增 `warmup_unmet` 标记（刻意不进 `as_dict()`），敏感性聚合把「整段落在预热期内」的点从 `best`/`worst`/`summary`/`stable` 中剔除（此前它们的扁平 0 会让 `best` 变成一个 0 笔交易的点、并把一致亏损报成「符号翻转」）；响应新增 `ranked_points`/`warmup_unmet_points`/`warnings`，`sensitivity_version` 升为 `1.1.0`；前端统计卡改显示「参与排名」、表格标出未测得点、图表不再画它们 |
| v1.4.6 | 分析必须说明它读了多少、跳过了什么（ADR-056）：`github_client.py` 新增 `FetchCoverage`（`fetch_repository` 返回三元组），`extract.py` 把 `files_scanned` 拆成 `files_parsed`/`files_inventoried`、`files_skipped` 带 `reason`、新增共用的 `build_coverage()`/`coverage_warnings()`；`/importer/github/analyze` 返回 `coverage` 并把覆盖率结论并入 `warnings`（此前 25 个从未尝试的候选、跳过的原因、"只登记未解析"全都不出现在报告里）；watcher 遇到读到不全（还有 Python 文件未读）时记 `incomplete` 并**拒绝自动导入**；前端显示「读取 X / Y」、非完整读取警告与被跳过文件的原因 |
| v1.4.7 | 抓取必须有墙钟预算，且必须说实话（ADR-057）：`fetch_repository` 新增 `max_seconds` 预算（默认 120s，端点字段 10–600），预算用完就停、把剩余候选记进 `not_attempted_files` 并置 `coverage.budget_exhausted` + `max_seconds`，警告改说「the fetch stopped after 120s…」而不是把责任推给从未碰到的上限（单请求超时约束不了 30 文件 × 2 次尝试 × 15s 的循环，实测一次默认分析要 640s）；`get_json` 补上 `timeout=self._timeout`（此前 repo/tree/commit 三类请求静默用 httpx 默认值，配置的超时只作用于文件下载）；watcher 只在**结构性**缺口（上限）时推进 `current_commit`，预算/网络造成的**瞬时**缺口记为 `transient` 并留待下轮重试（此前一次网络抖动等于永久放弃该次更新）；`analysis_version` 升为 `1.2.0`；前端新增「最多读取文件数 / 最长等待秒数」两个输入与超时中断结论 |
| v1.4.8 | 被监视的来源必须解释自己的状态（ADR-058）：`check_source` 的返回值与 `GitHubSource.last_import_status` 统一为同一套词表（`unchanged` 表示 head 未变、**什么都没抓取**；`no_change` 表示抓取并分析了一个新 commit 但 DSL 未变；`imported` / `incomplete` / `error`），此前 `unchanged`、`no_change`、`imported` 三条非事件路径**全部**写 `"checked"`，UI 里一行「已检查」无法区分「没有新东西」与「抓过了、策略没变」；旧记录里的 `checked` 不迁移也不猜测，前端标注为「已检查（旧记录）」；`GET /importer/github/sources/{id}/snapshots` 补返回 `extraction`（`imported`/`reason`/`transient`/`coverage`/`warnings`），把「为什么是这个状态」从数据库里放出来，人工导入的快照写入 `{"imported": true, "reason": "manual_import"}` 而不是空对象；`check_github_sources` 的汇总改为派生（`{"checked": len(sources), **outcomes}`），不再硬编码状态列表；前端状态列翻译为中文文案，每行新增「详情」按钮展示最近一条快照的原因、覆盖数字、警告与瞬时缺口的重试承诺（docs/05 §7.1、docs/12）。测试：新增 `test_a_new_commit_with_an_unchanged_dsl_is_stored_as_no_change`、`test_the_task_summary_names_the_statuses_the_run_produced`，并补断言存储值等于返回值、快照含 `extraction`。 |
| v1.4.9 | 读到了不等于看懂了（ADR-059）：`analyze_repository_files` 曾无条件把每个有内容的 `.py` 写进 `files_parsed`，而 `analyze_python_source` 在解析失败时只往 `unknowns` 塞一条 `category="unparseable"` —— 于是一个下载成功但根本无法解析的文件（Python 2 的 print 语句、NUL 字节、解析器拒绝的构造）贡献为零却被算作「已解析」：`coverage.complete` 仍为 `true`、`unread_python_files` 为 0、警告一句不说，watcher 照常无人值守导入，规则藏在该文件里的策略被静默降级。现在 `AnalysisResult` 新增 `files_unparsed`（`{path, reason}`）与 `parse_error`、`coverage.unparsed_python_files`，解析期异常捕获 `Exception`（不只 `SyntaxError`，仓库代码是不可信输入）并把结果记成「这个文件没被看懂」而不是「无法映射的构造」；watcher 的拒绝条件变为 `unread_python_files > 0 or unparsed_python_files > 0`，快照 `reason` 为 `incomplete_analysis` 或 `unparseable_python`，且解析失败与上限缺口同属结构性（`transient = false`、照常推进 `current_commit`，重读不会让它变得可解析）；`ANALYSIS_VERSION` 升为 `1.3.0`，端点返回 `files_unparsed`，前端头部行列出解析失败数、给出独立的解析失败表格，来源详情面板对 `unparseable_python` 给出解释 |
| v1.5.0 | 一个修订必须用 commit 命名（ADR-060）：分析路径曾把调用方给的分支名既当成抓取的 ref、又当成记录的修订 —— `get_tree` / `get_raw_file` 都用它请求、`RepoMeta` 只有 `ref`、导入时 `StrategyVersion.source_commit` 与 `GitHubSource.current_commit` 直接写分支名、快照也记在 `commit="main"` 上；于是「我读了 main」在 main 移动之后无法复读，抓取中途仓库被推新提交时 tree 与文件可能来自两个修订，而报告里没有任何东西能暴露这一点。现在 `GitHubClient.resolve_commit` 先把 ref 解析成 commit（`GET /repos/{o}/{r}/commits/{ref}`，解析不出 SHA 就报错，绝不把名字当修订），`fetch_repository` 的 tree 与每个文件都用该 SHA 读取，`RepoMeta` 新增必填字段 `commit`；`/importer/github/analyze` 响应新增 `commit`（`analysis_version` 升为 `1.4.0`），watcher 传进来的本来就是 head SHA，所以解析对它零成本；`POST /importer/github/import` 的 `commit` 为必填（7–64 位十六进制，否则 422），它被写进 `source_commit`、`evidence_json`（`{importer, repository, ref, commit}`）与审计记录，`_persist_github_source` 无条件记快照（删掉让不带 ref 的导入一条记录都没有的 `HEAD` 守卫）；前端把不像 SHA 的旧值渲染成 `main（ADR-060 之前记的是分支名）`，而不是让它看起来像一个 commit。顺带修正 docs/12 里把人工导入写成不存在的 `POST /strategies/import/github` 的一行。 |
| v1.5.1 | 版本号由拥有账本的 service 分配（ADR-061）：`GithubImportRequest.version` 曾默认 `"1.0.0"`、Web UI 的 `importReviewed` 更直接硬编码 `'1.0.0'`，而服务端按 slug 复用同名策略 —— 于是同一个仓库第二次导入必然撞上 `version ... already exists for this strategy` 的 422，而页面上没有任何能改版本号的地方，调用方只能猜。现在号码由账本所有者分配：`strategy_service.next_version()` 取下一个空闲补丁号（无版本 → `1.0.0`；`1.0.0` → `1.0.1`；`1.0.9` → `1.0.10`，按 `major/minor/patch` 三个**整数**比较，所以同时存在 `1.9.0` 与 `1.10.0` 时下一个是 `1.10.1` 而不是字符串比较得到的 `1.9.1`），账本里出现读不成三段数字的版本（如人工命名的 `v2-beta`）时**拒绝分配**并在理由里点名它；`POST /importer/github/import` 的 `version` 变为可选，省略即由服务端分配，响应新增 `version_assigned`（同时进 `evidence_json` 与审计记录）；新增 `GET /importer/github/versions?name=...` 在写任何东西之前回答这个 slug 有哪些版本、下一个会是什么（`can_assign` / `reason`）；前端新增版本号输入框，按 300ms 防抖查询账本并显示「将新建策略（slug …），版本 1.0.0」或「将在已有策略 #N（slug …）上创建新版本 1.0.1；已有版本：…」，手填的版本号已存在时直接禁用导入按钮（服务端仍最终裁决），导入失败后重新读账本。测试：`test_import_endpoint_assigns_the_next_version_when_none_is_named`、`test_import_endpoint_keeps_the_version_the_reviewer_names`、`test_version_ledger_reports_what_would_be_assigned`、`test_version_ledger_refuses_to_increment_a_version_it_cannot_read`，以及离线探针 `backend/scripts/probe_version_ledger.py`。 |
| v1.5.2 | 草案不合格时无人值守的导入必须停下来说明（ADR-062）：`dsl_builder` 从不生成离场规则（ADR-059 一族），所以机器草案的 `exit` 永远是 `{}`、`parse_spec` 必然拒绝它；`check_source` 却把这份草案直接交给 `create_strategy_version`（不在任何 try/except 里），于是 `ValueError: invalid strategy DSL -> : Value error, at least one exit rule is required` 逃出函数 —— Celery 任务崩掉、不写快照、不更新 `last_import_status`，而且 `check_github_sources` 的循环在第一个坏来源处中断，排在它后面的来源再也不会被检查（触发条件不是罕见输入：人工导入过的策略 `Strategy.source_url == GitHubSource.repository_url` 加上游新 commit）。现在无人值守导入先问人工导入路径同一个裁决者 `strategy_service.strategy_dsl_problem`（`parse_spec` + `validate_strategy`，因此"能解析但不合规"这条安静分支也被挡住），不通过就记为新的 `review_required`：快照 `reason="requires_review"` + `detail`（裁决原话）+ `transient=false`、`last_import_status="review_required"`、不创建任何版本，该 commit 写进新的可空列 `GitHubSource.pending_review_commit`（迁移 `0008_github_pending_review`，改名经过见 ADR-064）；`current_commit` 一并推进（同一 commit 重抓只会得到同一结论），但等待由独立列表达——否则下一轮 beat 会因 `head == current_commit` 报 `unchanged` 把它抹掉；等待中的 commit 在 `unchanged` 判定之前被拦下，只花一次 HEAD 请求返回 `review_required`、不重新抓取；人工导入**那个** commit 清空等待，导入别的 commit 保留（等待属于某个修订，不属于仓库）；更新的 commit 取代旧等待。顺带删掉 watcher 自己的 `_bump_version`（发布节奏 `1.0.9 → 1.1.0`）改用 `strategy_service.next_version`（整数补丁 `1.0.9 → 1.0.10`），同一列只剩一套编号规则；导入期任何异常都不再逃出 `check_source`（记 `error` + 快照 `reason="import_failed"` + `detail` 且不推进 `current_commit`，下轮重试），`check_github_sources` 逐来源再兜一层（`db.rollback()` + `logger.warning` + 记 `error`，一个来源崩掉不影响后面的来源）；sources 列表/详情/`check` 三个响应都新增 `pending_review_commit`，前端状态列显示「待人工审阅」（`wait` 色调而非错误色）加待审阅 commit 短 SHA，快照详情用 `detail` 解释「补上缺失的规则后人工导入这个 commit」。测试：`test_the_import_gate_names_what_blocks_an_unattended_import`、`test_an_exit_less_draft_is_refused_instead_of_crashing`、`test_a_pending_review_survives_the_next_run_without_refetching`、`test_a_new_commit_clears_a_review_that_was_never_done`、`test_an_import_that_raises_is_recorded_not_raised`、`test_a_source_with_nothing_linked_says_so`、`test_a_source_that_crashes_does_not_stop_the_run`、`test_the_watcher_numbers_versions_with_the_ledger`，`test_github_sources.py` 的 `test_importing_the_pending_commit_clears_the_review` 与 `test_importing_another_commit_keeps_the_pending_review`，以及离线探针 `backend/scripts/probe_watch_refusal.py`。 |
| v1.5.3 | 生命周期审计必须说出实际做的动作（ADR-063）：`apply_lifecycle` 用 `"promote" if _rank(target_stage) > _rank(previous) else "degrade"` 推方向，而 `_rank` 的序是 `(*PIPELINE, *MANUAL_ONLY)` —— `retired` 排在最高位（rank 7）、`degraded` 不在序里（返回 `-1`），于是把策略退休（生命周期里最重的动作）记成「晋级」`promote`，`degraded -> retired` 也一样，而 `paper_trading -> degraded` 才是 `degrade`；这个字段被审计表 UI 原样渲染（`frontend/src/views/SettingsView.vue:832` 「动作」列），所以页面上一次「退休」显示为 `promote`，并且没有任何测试看过它（全仓只有 `backend/app/strategies/lifecycle.py:325`/`:340` 提到 `_rank`）。现在方向由显式的 `_transition_action(previous, target)` 裁决、`_rank` 删除：落到 `degraded` 记 `degrade`、落到 `retired` 记 `retire`、从终态回到流水线记 `restore`、其余记 `promote`；词表固化为 `LIFECYCLE_ACTIONS = ("promote", "degrade", "retire", "restore")` 并从模块导出，因此 `retired -> degraded` 仍是 `degrade`、`retired -> reference_signal` 是 `restore`。测试：`test_retiring_a_strategy_is_recorded_as_a_retirement`、`test_retiring_a_degraded_strategy_is_not_a_promotion`、`test_a_step_forward_is_still_recorded_as_a_promotion`、`test_a_losing_paper_strategy_is_recorded_as_a_degradation`、`test_leaving_a_terminal_stage_is_recorded_as_a_restore`，原有审计测试补 `action in LIFECYCLE_ACTIONS`；另加离线探针 `backend/scripts/probe_lifecycle_direction.py`（打印五种走法的 `action`，不符即 exit 1）。同一版本还修掉一个会让 PostgreSQL 部署起不来的缺陷（ADR-064）：ADR-062 的迁移 id `0008_github_source_pending_review` 有 **33 个字符**，而 Alembic 在 PostgreSQL 上把版本表建成 `alembic_version.version_num VARCHAR(32)`，迁移最后一步 `UPDATE alembic_version SET version_num=...` 抛 `psycopg.errors.StringDataRightTruncation: value too long for type character varying(32)`，整个迁移事务回滚、compose entrypoint 重试三次后 `[entrypoint] ERROR: migrations failed; refusing to start`，于是 v1.5.2 的 CI 红灯（run `37084884774` 的 `backend tests + lint` 与 `docker compose smoke test` 双失败，同一提交的 release 流水线是绿的）；本地之所以看不见，是因为全量测试跑在 SQLite（不强制列长），唯一碰 PostgreSQL 的 `backend/tests/test_postgres_triggers.py` 四条因 `TEST_POSTGRES_URL` 未设置被 skip。现在 id 改名为 `0008_github_pending_review`（25 字符，文件名保留描述性的 `0008_github_source_pending_review.py`），并新增不需要数据库的守卫测试 `backend/tests/test_migration_revisions.py`（每个 id ≤ 32、id 唯一、`down_revision` 都能解析、整条链恰好一个 head，4 条）。 |
| v1.5.4 | 胜率必须跟它的分母一起发布（ADR-065）：`GET /signals/outcome-summary` 只聚合**已经有结果行**的信号，没有结果行的（评估器要等信号之后 `DEFAULT_BARS_AFTER = 10` 根 K 线，或该标的根本没有 K 线序列）在响应里完全不出现 —— 于是 400 条信号里 327 条未决时，页面只写「整体胜率 72%」，读者无从知道这个数字是从 73 条还是从 400 条里算出来的；同一张卡片还把**分页长度当总数**（`frontend/src/views/SignalsView.vue:250` 的标题用 `outcomes.length`，而 `GET /signals/outcomes` 默认 `limit=50`、上限 500、没有 total，400 条结果时标题写「50 条」而下一行写「样本 73」）；summary 还不接受 `symbol` 而列表接受，于是过滤后的行配着全局统计。现在响应固定携带范围与三个计数：`symbol`（`null` 为全部标的）、`signals`（范围内信号总数）、`decided`（有可用结果的数量，也就是每个 `count`/`win_rate` 的分母）、`undecided`（其余，`decided + undecided == signals`）、`bars_after`（评估器要等几根 K 线），`evaluated` 改名为 `decided`（同一个数字只留一个名字）；`decided` 只数有 pnl 的结果行，所以 `decided == groups.ALL.count` 恒成立；端点新增 `symbol` 参数（未知标的返回空范围，不回落全局平均）；前端标题不再拿分页长度当总数，改为「范围 + 共 N 条信号、已评估 M 条（另有 K 条还没有结果：要等信号后 N 根 K 线，或该标的还没有 K 线序列）」，表格上方注明「下表是最近 N 条已评估信号」，`symbolFilter` 一并传给 summary，空表文案区分「一条信号都没有」与「有信号但还没有一条等到结果」。顺带修掉两处指向不存在章节的 docstring（原文写 docs/09 §8，而 §8 是「Strategy evidence layers」，已改为 docs/12，并在 docs/12 写下三个计数的含义）。测试：`test_the_summary_states_the_denominator_of_its_win_rate`、`test_the_summary_only_counts_the_symbol_it_names`、`test_an_unknown_symbol_is_an_empty_scope_not_a_global_average`、`test_an_outcome_without_a_pnl_is_not_a_decided_signal`，原 `test_outcome_summary_groups` 改用 `decided` 并断言 `bars_after == 10`。 |
| v1.5.5 | 取钱不是亏钱（ADR-066）：`POST /paper/accounts/{id}/fund` 的 `amount` 是带符号的（正数入金、负数提现），但基准只在**入金**时跟着走（`if amount > 0:` 才抬高 `initial_cash`），提现只减 `cash` —— 于是账户里少了的那笔钱被记成交易亏损：一个从未交易过的 10,000 账户提现 4,000 后，账户列表的「盈亏」`(cash - initial_cash) / initial_cash` 变成 **-40%**，同时 `GET /paper/accounts/{id}/performance` 的 `final_equity` 仍是 10,000（那笔钱已经不在账户里，`cash` 只剩 6,000）；赚了 1,000 的账户提现 4,000 被显示成 -30%，提空是 -100%。现在基准**对称**更新（入金与提现都改它），它的含义是净入金（入金 − 提现），并恢复一条可断言的不变量 `final_equity == net_deposits + 已实现盈亏`（空仓时等于 `cash`）；对外发布的名字 `initial_cash` → `net_deposits`（账户列表/详情、`/equity`、`/performance` 四个响应都不再有 `initial_cash`，数据库列名与创建/重置请求体的 `initial_cash` 保留，`PaperAccountOut.net_deposits` 用 `Field(validation_alias="initial_cash")` 读列），`/fund` 与 `/reset` 的响应和审计 payload 一并带上 `net_deposits`；净入金 ≤ 0 时**不发布**收益率类指标（`compute_metrics` 原有的 `initial <= 0` 守卫说明由「equity curve too short for ratio metrics」拆出 `initial capital is not positive, so ratio metrics have no denominator`，并且**分母检查排在曲线长度检查之前**：提空后的账户只有单点权益曲线，先判长度会把「没有分母」说成「账户太年轻」，而长度检查仍保留给「基准正常但还没有交易」的账户，空曲线（`len(equity) == 0`）单独在最前面判定）；绩效响应新增顶层 `metric_notes` 把「指标为什么缺席」交给调用方，前端「初始资金」列改名「净入金」、绩效卡片副标题改「净入金 + 已实现」、盈亏百分比改走新的 `formatPaperPnlPct()`（净入金 ≤ 0 时显示「—」，不再打印 `-100%`/`NaN%`），`DashboardView` 的模拟账户表同步。提现仍然不能超过可用现金（422，只改记账不放宽风控）。测试：`test_a_withdrawal_is_not_a_trading_loss`、`test_a_profitable_account_still_reports_a_profit_after_a_withdrawal`（提现后收益率仍是 +16.67%）、`test_withdrawing_past_the_deposits_publishes_no_return`，`test_api_fund_and_withdraw_limits` 补断言 `net_deposits`，以及离线探针 `backend/scripts/probe_paper_contributions.py`（六种走法打在同一张表上，任一格不符即 exit 1），另有 `test_an_emptied_account_names_the_denominator_it_lost`（没有交易的账户提空到 0，`metric_notes` 必须恰好说出分母，而不是「曲线太短」——真浏览器验收发现的回归）。同一版本还修掉一个默认安装下每次打开仪表盘都会出现的 500（ADR-067）：`backend/app/api/routers/settings.py` 的 `ghostfolio_holdings` 把 `adapter = GhostfolioAdapter()` 写在 `try` **外面**，而 `GhostfolioAdapter.__init__` 在 `GHOSTFOLIO_BASE_URL`/`GHOSTFOLIO_API_KEY` 为空时就抛 `GhostfolioError`，于是同函数里那句 `except GhostfolioError → 502` 永远够不到构造失败，按 `.env.example`（Ghostfolio 是可选项、默认为空）装好之后前端控制台每次加载都留下 `500 (Internal Server Error) <.../api/v1/settings/ghostfolio/holdings>`（同一文件的兄弟端点 `/settings/ghostfolio/test` 把构造写在 `try` 里面，所以这是漏改而非设计）；现在构造移进 `try`，未配置的部署得到 **502 + `GHOSTFOLIO_BASE_URL is not configured`**（沿用 ADR-039 对「上游接不通」的既有状态码，不新增语义），测试 `test_holdings_endpoint_reports_an_unconfigured_ghostfolio`；仪表盘也不再明知故问：`GET /settings` 早已返回 `environment.ghostfolio_configured`（后端就是 `bool(settings.ghostfolio_base_url)`），为假时不请求持仓，`api.ts` 的 `settings()` 由 `Record<string, unknown>` 升级为真实的 `AppSettings` 类型——500 改成 502 之后控制台那行红字仍在（浏览器把任何非 2xx 都记成 failed request，`.catch()` 拦不住），真浏览器验收的「零 console 错误」正是卡在这条注定失败的请求上。 |


**至此 docs/15 的 P0/P1/P2 与 v2 结构性能力清单全部完成。** 后续为打磨与体验改进。


## Phase 0 — Foundation

交付：Docker Compose、DB、Redis、API、Web、基础认证、迁移、日志。

验收：

- `docker compose up -d` 可启动
- healthcheck 全绿
- 数据库迁移可重复执行
- 配置错误可读

## Phase 1 — Market Data + Quant Core

交付：OHLCV、EMA/ATR/RSI/MACD/Bollinger、PA features、Strategy DSL。

验收：

- 相同输入得到相同特征
- 时间排序正确
- 缺失数据有明确状态
- closed bar 规则通过测试

## Phase 2 — Backtest Lab

交付：backtest、fees/slippage、trade log、metrics、OOS。

验收：

- golden fixtures 全通过
- no-lookahead tests 全通过
- backtest run 可复现
- 旧结果不被新版本覆盖

## Phase 3 — Ghostfolio

交付：REST adapter、sync、portfolio context。

验收：

- 能测试连接
- 能同步活动和资产
- Ghostfolio 原数据不会被修改
- 同步失败显示明确错误

## Phase 4 — Paper Trading

交付：virtual cash、positions、equity curve、signal lifecycle。

验收：

- 初始资金可配置
- 买卖后现金/持仓正确
- fee/slippage 正确进入结果
- account reset 有审计事件

## Phase 5 — AI Layer

交付：provider abstraction、OpenAI-compatible API、budget、cache、explanation。

验收：

- API key 不泄漏
- provider 可切换
- AI 输出 schema 可验证
- AI 关闭时核心量化功能仍可运行
- AI 不得修改回测结果

## Phase 6 — GitHub Strategy Importer

交付：repo import、strategy extraction、provenance、license、version diff。

验收：

- GitHub repo 输入后产生 import report
- 能标记 unknown/unsafe rules
- 不执行不受信任代码
- commit 变化产生策略新版本
- 原版本结果仍可查看

## Phase 7 — Live Signal

交付：scheduler、scanner、notification、signal outcomes。

验收：

- 只用 closed bars
- 同一事件不会重复通知
- BUY/SELL/WAIT 状态可追溯
- 信号结果可以回填

## Phase 8 — Strategy Lifecycle

交付：automated promotion/degradation rules、strategy dashboard。

验收：

- Experimental → OOS → Paper 的流程可见
- 每次晋级/降级有证据
- 没有"AI 一句话升级策略"的路径

## Definition of Done

一个功能只有同时满足以下条件才算完成：

1. 有 domain test
2. 有 API test（若涉及 API）
3. 有 UI state handling（若涉及 UI）
4. 有错误处理
5. 有日志
6. 有文档
7. Docker 可启动
8. 不违反 no-auto-trading boundary
9. 不引入未来函数
10. 不破坏已有 strategy version / backtest history
