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
| v1.5.6 | 边缘必须分得清「这个文件不存在」和「这是应用」（ADR-068）：给 v1.5.4 的 NAS 部署做体检时对 web 容器逐项实测，发现四条同形缺陷 —— `GET /assets/index-DOESNOTEXIST.js` 返回 **200 + `Content-Type: text/html` + 579 字节**（就是 `index.html`），因为 `docker/web.nginx.conf` 只有一条 `location / { try_files $uri $uri/ /index.html; }`、没有单独的 `/assets/`，于是浏览器缓存了旧外壳、外壳引用了已删除的 bundle 时把 HTML 当 JavaScript 解析、报一句离真相最远的「Unexpected token '<'」；带 `Accept-Encoding: gzip, deflate` 请求 `/assets/index-Be_RqsH4.js` 仍是 **1,289,010 字节**、响应里没有 `Content-Encoding`（nginx 的 `gzip` 默认 off，而同一个域里 API 的响应带着 FastAPI `GZipMiddleware` 留下的 `Vary: Accept-Encoding`）；内容哈希文件只有 `ETag: "6ac05d04-13ab32"` 与 `Last-Modified`、没有任何缓存指令（构建已经把内容摘要写进文件名，却让浏览器每次加载都回源校验）；没有任何图标（`frontend/index.html` 没有 `<link rel="icon">`、产物里没有图标文件，`/favicon.ico` 也落回 SPA 回退、返回 `index.html`）。现在 `/assets/` 单独成 location 并 `try_files $uri =404`、命中则 `Cache-Control "public, max-age=31536000, immutable"`（用 `add_header` 而非 `expires`，避免同一响应出现两个 `Cache-Control`），`location /` 保留 SPA 回退并加 `Cache-Control "no-cache"`（外壳必须回源校验，带 `ETag` 的 304 仍然便宜），整站打开压缩（`gzip on; gzip_vary on; gzip_min_length 1024; gzip_proxied any; gzip_types application/javascript text/css application/json text/plain image/svg+xml;`，`gzip_vary` 不是可选——没有它中间缓存可能把压缩字节发给不支持的客户端），交付 `frontend/public/favicon.svg` 与 `<link rel="icon" type="image/svg+xml" href="/favicon.svg">`，并让 `location = /favicon.ico` 明确回答 **204**（确实没有 `.ico`，不拿 HTML 冒充）。测试：`backend/tests/test_web_edge.py` 七条**守卫**（`/assets/` 的 `try_files $uri =404`；`immutable` 与 `max-age=31536000`；`location /` 的 `no-cache`；`gzip on;` 与 `gzip_types` 含 `application/javascript`/`text/css`；`location = /favicon.ico` 的 `return 204`；`index.html` 链接的图标文件存在；模板里除 `${AUTH_LINE}` 外没有任何 `${...}` 占位符——`web-entrypoint.sh` 只 `envsubst '${AUTH_LINE}'`，其他占位符会把 nginx 自己的 `$uri`/`$host` 抹掉），以及 `.github/workflows/ci.yml` 里新增的「Web edge assertions (ADR-068)」在**真实容器**上断言缺失资源 404 且响应体不是 `<!doctype`、`/` 带 `no-cache`、`/favicon.svg` 200、`/favicon.ico` 204、bundle 带 `Content-Encoding: gzip` 且小于 1,000,000 字节、bundle 带 `immutable`、`/signals` 深链仍 200 —— 本地是 Windows、没有容器运行时，nginx 的真实行为只在 CI 的 compose smoke 作业里可见，仓库里那七条读配置文本的测试是守卫、不证明行为。无 API/数据模型变更，`/api/`、`/docs`、`/openapi.json`、`/healthz` 各有独立 location，不继承 `/` 的缓存头。 |
| v1.5.7 | 健康检查必须在自己承诺的时间内回答（ADR-069）：给 v1.5.4 的 NAS 部署做体检时顺带发现，在一个**没有任何依赖可达**的安装上 `GET /api/v1/health` 要 **10.4 秒**才回，而 `frontend/src/views/DashboardView.vue` 的 `load()` 用 `Promise.all` 把 `api.health()` 和模拟账户表放在一起 await —— 打开仪表盘先空白十秒，四张卡片与账户表都在等一次健康检查（`/healthz` 不碰依赖，所以容器健康检查看不出这件事）。分段计时（`backend/scripts/probe_health_latency.py`，`quantlab-postgres`/`quantlab-redis` 都解析不了）：`database 1.26s`、`redis 2.08s`、`workers` **`11.76s`**，合计 **15.10s**。元凶是 `_check_workers()` 的 `celery_app.control.ping(timeout=1.0)`：那个 `timeout` 只约束「等回复」，不约束「连得上 broker」，broker 连不上时走 kombu 的默认连接策略（重试 + 退避），把 1 秒的意图变成 11 秒的等待；先热身 DNS 再测仍然是 **8.99–11.13s**，说明慢的不是解析器而是 ping 内部的连接路径。反证：同样抛 `OperationalError: Error 11001 connecting to quantlab-redis:6379. getaddrinfo failed.` 的 `connection_for_read(transport_options={"socket_connect_timeout": 1, "socket_timeout": 1}).ensure_connection(max_retries=0, timeout=1)` 只用 **0.86s**（连续两次）；另两条路不通 —— `connection_for_read(connect_timeout=1)` 抛 `TypeError: Connection._ensure_connection() got an unexpected keyword argument 'connect_timeout'`（celery 5.6.3 / kombu 5.6.2），`conf.update(broker_connection_timeout=1, broker_connection_retry=False, broker_connection_max_retries=0)` 后再 ping 仍是 8.98s。现在每个探针共用 `PROBE_TIMEOUT_SECONDS = 1.0`：`_check_workers()` 先问「broker 通不通」（连接上限 + `max_retries=0`，`finally` 里 `release()`），通不了直接 `"unknown"`、通了才 ping（「有没有 worker 在答」只在 broker 连得上时才有意义）；`_check_redis()` 补 `socket_connect_timeout` 并把读超时收到 1 秒；关闭连接失败用 `contextlib.suppress` 吞掉（清理出问题不是依赖状态，不能把 `"1 online"` 变成 `"unknown"`）；词表与响应键一个不加一个不减，所以仪表盘、NAS 冒烟与探针都不用改解析。仪表盘同时不再 await `/health`：账户表、系统信息、信号照旧并行等待，健康卡片自己到达自己填，失败时副标题写「健康检查没有响应」而不是永远停在「连接中…」。修复后本机合计 **2.96s**（`database 1.24s / redis 0.86s / workers 0.86s`）。上限之外还剩一个**冷启动**代价：真栈里第一次 `GET /health` 仍要 **5.72s**（同进程随后重复调用 max 1.75s），因为第一次探针要付冷解析器与 broker transport 的建立代价、那不在任何 socket 超时里；于是 `warm_dependency_probes()` 在 `backend/app/api/main.py` 的 `lifespan()` 里起一个 daemon 线程预热 `_check_redis()` + `_check_workers()` 各一次并**丢弃结果**（是预热不是缓存——`/health` 仍实时测量，异常被 `contextlib.suppress` 吞掉），真栈第一次请求随之从 5.72s 降到 **2.19s**。测试：`backend/tests/test_health_probe.py` **十三条**（上限被传进 `connection_for_read`/`ensure_connection`/`ping` 作为本次缺陷的回归守卫、broker 不通从不 ping、`[]` → `"0 online"`、ping 抛错 → `"unknown"`、关闭失败不改结论、redis 的 `socket_connect_timeout`/`socket_timeout`、Redis 拒连 → `"unavailable"`、`/health` 键集合与词表三种取值不变、预热按 `redis → workers` 各跑一次、预热抛错被吞且线程结束、`create_app()` 进 lifespan 时确实调用预热），时序断言放进离线探针 `backend/scripts/probe_health_latency.py`（先热身一轮只给第二轮计分，单探针 ≤ 3.0s、合计 ≤ 5.0s，超了 exit 1；修复前 `RESULT: failures`）。**未覆盖**：数据库那一段仍由驱动的连接策略决定（本机 1.25s，属 libpq 的 `connect_timeout`，要在引擎创建处处理）。 |
| v1.5.8 | 一个事实只有一个出口（ADR-070）：给 v1.5.4 的 NAS 部署做体检、逐端点探查时发现同一个审计账本有**两个出口** —— `GET /settings/audit`（`backend/app/api/routers/settings.py:256`）与 `GET /audit/logs`（`backend/app/api/routers/audit.py:17`）返回同一批 **44 行**记录；前者**不带** `actor`、后者带，`docs/12_API_SPEC.md` 的 §Audit Logs 只记录了后者，而前端读的是前者（`frontend/src/api.ts` 的 `audit()`，`SettingsView.vue` 渲染），两边各自手写一遍序列化字典所以已经分叉：复制出来的那份丢了「谁做的」。两个出口的 `total` 都是 `total: len(rows)` —— `limit=2` 时 `total` 也是 2，「账本里一共有多少条」在 API 上**无法回答**，用页长冒充总数；`/settings/audit` 连参数校验都没有（`min(limit, 500)`，负数会直接进 SQL），而 `/audit/logs` 有 `Query(ge=1, le=500)`。现在审计只留一个出口：**删除** `GET /settings/audit`（不做别名、不做转发——转发只会让旧路径继续被调用、重复就还在），`total` 由 `SELECT count(*)` 与 `events` 用同一个 `where` 回答、`limit`/`offset` 只影响 `events`，前端改读 `/audit/logs`，审计表新增「操作者」列渲染 `actor`、标题写「最近 N 条，共 M 条」让 `total` 在界面上也有兑现，4 个把审计当断言工具的测试改读 `/audit/logs`（`test_version_api.py` / `test_notifications.py` / `test_ai_providers.py` / `test_api.py`），其中 `test_api.py` 那条恒真的 `total >= 0` 改成「至少一条 `strategy_version_created`，且每条事件都带 `actor`」。**破坏性变更**：`/settings/audit` 现在返回 404（有测试守着，防止它悄悄回来）；`total` 语义变了（以前恒等于 `len(events)`），一直拿它当分页长度用的调用方会看到更大的数字——那正是这个字段应该回答的问题。它从未出现在 docs/12 里、NAS 冒烟脚本没用它。无数据库迁移。 |
| v1.5.9 | 健康检查必须说出它跑在哪一版库结构上（ADR-071）：`scripts/Test-NasDeployment.ps1:92` 的步骤名叫「API /health (含依赖与迁移状态)」，但 `GET /health` 的响应里**没有库结构版本**这个字段、脚本也只断言了 `status == healthy` —— 名字承诺了一个没人做过的检查。更糟的是 `status` 的判据只有 `database == "connected"`：`docker/entrypoint.sh` 会先跑 `alembic upgrade head` 再起服务（失败重试三次后拒绝启动），可那保证的是**容器自己**迁移过；一旦有人回滚、手工改库、或把 API 指向另一个库，一个「连得上但迁移没跑」的 API 会报出和一次好部署完全一样的 `healthy`，而没有任何字段能让调用方发现。本版让 `/health` 如实报出**数据库自己说的** alembic 版本号 `migration`（读不到版本表报 `unknown`、版本表为空报 `none`），并把 `status` 升级为「库连得上**且**结构版本说得出来」才是 `healthy`；部署脚本改了名并真的断言这个字段，CI 也在真实 compose 栈上拒绝空词——单测只能证明代码路径，只有跑在容器真的迁移过的栈上，这个字段才算被验证。 |
| v1.5.10 | 部署自检的每一步都必须能失败（ADR-072）：给 NAS 部署做体检时把 `scripts/Test-NasDeployment.ps1` 的十二个步骤逐条读了一遍，发现 ADR-071 的同类缺陷在脚本里还有六处——步骤存在、名字可信、但没有任何一条能让它失败：`Web 容器 /healthz` 只要求 HTTP 200（nginx 默认页、失效的 upstream、门户劫持都会 200）；`API /healthz (存活探针)` 里 `if ($null -eq $r) { 'empty body' }` 只打一行字就通过、从不看 `status`；`API /system/info` 把 version/modules 格式化出来就算过（没有 version/modules 的响应会打印 `version= env= modules=0` 并记 OK）；行情同步那一步的断言是 `if ($r.inserted -lt 0)`——插入行数不可能为负、恒假；`创建策略版本（校验不可变触发器）` 的名字承诺一次不可变校验却只创建并打印哈希；`运行回测` 只断言 `status == completed`、从不看 `result_hash`，而「回测可复现」是本项目的红线；`扫描信号` 与 `模拟盘账户列表` 只打印。后果并非理论：ADR-071 修掉的那个步骤名字写着「含依赖与迁移状态」，而 `/health` 压根没有迁移字段，它能连续几个版本报 OK 正是因为脚本不会失败。现在新增两个断言辅助函数 `Assert-Value`（缺失/空白/不匹配正则即 throw）与 `Assert-Rejected`（只有抛出的错误是 4xx 校验类拒绝才算通过、成功的调用反而 throw），并让每个名字兑现：web 存活要求响应体里有 `"status":"alive"`；API 存活用 `Assert-Value` 读 `status` 并要求 `alive`；`/system/info` 断言 version 非空与 `modules` 数大于 0，并新增可选参数 `-ExpectVersion`（从「有某个版本在答」升级为「我要部署的那个版本在答」）；行情同步断言响应里有 `inserted`/`series_id` 并回读 `/market-data/series/{id}/bars`（同步幂等、第二次合法地插入 0 行，所以不能要求 `inserted > 0`）；不可变由两件事证明——`immutable_hash` 是 64 位十六进制、`GET /strategies/versions/{id}/verify` 的 `stored_hash`/`recomputed_hash` 都等于它且 `intact` 为真，以及同一个版本号写第二次必须被拒；回测对同一份请求跑两次、两次 `result_hash` 必须相同。首次真栈运行还抓到辅助函数里的一个 PowerShell 陷阱：`Assert-Rejected` 的 scriptblock 参数原本叫 `-Body`，而步骤体在它里面读自己的 `$body` 载荷，变量名大小写不敏感，于是探针把辅助函数当请求体发了出去、报 `An item with the same key has already been added. Key: Value`，被观察的 422 拒绝根本没发生——参数改名 `$Action`。测试：`backend/tests/test_nas_deployment_script.py` 十四条守卫（读脚本文本、用正则切出每个步骤体，核心一条是「每条步骤体里必须出现 `throw` 或 `Assert-`」——不允许存在只能打印的步骤，其余逐条钉住各步骤名字承诺的断言，最后一条钉住那个 `$Body` 遮蔽陷阱）；行为证据是本机一个 stdlib 边缘替身（`/` 回带 `<div id="app">` 的外壳、`/healthz` 与 `/api/*`、`/openapi.json` 按 `docker/web.nginx.conf` 的映射反代到 8080）上的三次运行——替身对 `/healthz` 回 nginx 默认页时 **11/12，exit 1**（唯一失败正是 `Web 容器 /healthz`）、正常替身配错版本 `-ExpectVersion 1.5.9` 时 **11/12，exit 1**（唯一失败是 `/system/info`）、正常替身配 `-ExpectVersion 1.5.10` 时 **12/12，exit 0**。无 API、数据库或前端改动，纯运维脚本的语义收紧。 |
| v1.5.11 | 自检脚本的结论必须变成退出码（ADR-073）：ADR-072 把「部署自检的每一步都必须能失败」钉进了 `scripts/Test-NasDeployment.ps1`，这一版把同一次体检推到所有自检脚本上——逐个数 `exit` 时发现 `scripts/Test-EnsembleAttribution.ps1`（`docs/15_ROADMAP_ACCEPTANCE.md:42` 标为 DONE 的集成探针、v1.3.9 引入，也是唯一在**真实 HTTP** 上验证 `POST /research/ensemble` 与 `/research/ensemble/sweep` 契约的地方）有二十多条不变量，却**一个 `exit` 都没有**：结论只收集进一个 `$ok` 布尔，最后打印一行 `INVARIANTS: OK/FAILED` 就结束，于是「每一条都不成立」的一次运行同样以 0 退出——放在 `bash -e`、CI 步骤或 `if ($LASTEXITCODE)` 后面，这个探针永远说成功，结论只活在一段没人读的控制台文本里。同一支脚本还有第二处只说不做：`POST /market-data/sync`（`:43-44`）与两个成员回测（`:57-59`）的响应被 `| Out-Null` 丢掉，backtest summary identity 那张表（`:61-64`）只打印不校验，而脚本头部（`:4-7`）自称断言 dataset identity——两个成员回测可以分别建在不同序列上，探针照样说 OK；同步没有建立序列（provider 什么都没给）时它会拿空数据一路算下去，把「没有数据」报成「全部成立」。现在每条失败都记进 `$failures`（`Fail` 打印带实际数值的原因），结论是脚本的最后动作并**变成退出码**（`INVARIANTS: FAILED (n)` + 逐条原因 + `exit 1`；`INVARIANTS: OK` + `exit 0`），同步报告与两个成员回测的响应都被读进来并断言（`inserted` 属性存在、`series_id` 非空；`strategy_version_id` 相等、`status` 是 `completed`、`result_hash` 是 64 位十六进制、`symbol`/`timeframe` 与本探针一致、`bars_evaluated > 0`），identity 表按 `id` 唯一匹配每个成员回测那一行并比对 `dataset_hash`。改完第一轮真栈运行还咬到**我自己新写的假通过**：`@(Invoke-RestMethod …)` 会把 JSON 数组包成「单元素数组、而那个元素本身是 `Object[]`」，于是每个判断都以「非空数组为真」通过、屏幕上那张 identity 表印成空行——改成先赋值再包、并加一条「列表是嵌套的」显式守卫。测试：`backend/tests/test_self_check_verdicts.py` 六条守卫（每个 `Test-*.ps1` 必须同时含 `exit 0` 与 `exit 1`；结论是最后动作且退出块里用到 `$failures`；真的检查 `market-data/sync`/`series_id`/`result_hash`/`dataset_version_id`/`bars_evaluated`；折行后不得把同步或成员回测的响应 `| Out-Null` 掉；sweep 契约仍在），**先写测试再改脚本：旧脚本 4 条失败 2 条通过（红）、改完 6 条全绿**；行为证据是本机真栈四轮运行——直连 API **exit 0**（identity 表四行真实数据、`inserted=0 series_id=1`）、纯反代替身 **exit 0**（对照组，证明失败不是替身造成的）、反代把 `engine_version` 改写成 `ensemble-9.9.9` 时 **exit 1**（恰好两条 FAIL）、反代删掉同步报告的 `series_id` 时 **exit 1**（一条 FAIL）。无 API、数据库或前端改动，纯自检脚本的语义收紧。 |
| v1.5.12 | 客户端的每一次调用都是一句关于服务端的断言（ADR-074）：v0.9.9 的根因是 `frontend/src/views/SettingsView.vue` 调了 `frontend/src/api.ts` 从未定义的 `api.aiTask`，唯一抓住它的是 `vue-tsc` 的 TS2551——那次失败是**类型检查**救回来的。这一版问的是它的反面：类型系统证明客户端能编译，不证明它调用的那扇门存在。`frontend/src/api.ts`（31904 B）里 `:8 const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'`、`:20 async function request<T>(path: string, init?: RequestInit): Promise<T>`，`T` 是未校验的断言、`path` 只是一段字符串；约 80 个调用点写下的路径与 `backend/app/api/main.py:208-229` 用 `app.include_router(router, prefix=settings.api_prefix)` 挂上的 19 个 router 之间的关系，编译期与测试期都没有人检查过（真实门表只有 `app.openapi()["paths"]` 能给出——本机 FastAPI 版本下 `app.routes` 里是 19 个 `_IncludedRouter` 包装对象、没有 `.path`）。先审计再决定：三个探针分别查门（`(path, method)`）、查旋钮（查询键双向）、查载荷（客户端接口字段 vs 响应 schema），结论是**今天没有任何漂移**——80 个调用点全部落在真实门上（另 2 个是条件查询串归一化的假象）、发送的每个查询键都已声明、API 标为 required 的查询参数客户端都发送、27 份可比对响应里客户端声明的顶层字段全部存在；但审计同时证明这类守卫最容易死于假阳性：第一版字段探针用扁平正则 `^\s*(\w+)\??\s*:` 读接口字段，把嵌套结构里的名字也当顶层字段，于是 `SensitivityResult` 的 `summary: { mean, median, stdev, min, max, range, positive_ratio }`、`MonteCarloResult`、`EnsembleResult` 三处被报成「客户端字段不在 schema 里」，三条全假——人一旦学会忽略这种告警，守卫就等于不存在。现在新增 `backend/tests/test_api_contract.py`，把这层关系变成 CI 里会失败的断言：调用必须是真实存在的门与方法；发送的查询键必须已声明（`` `${flag ? '?key=1' : ''}` `` 这种条件查询串里的键按「可能发送」只做这一向检查）；API 标为 required 的查询参数必须被无条件发送；客户端响应接口在**深度 0** 声明的字段必须出现在该响应的 schema 里（深度感知是刻意的——`_declared_fields` 只在深度 0 收「标识符紧跟 `:` 或 `?`」的名字，嵌套 `{ … }` 里的名字不算字段，正是上面三条假阳性的解药）；扫描器本身要可信（泛型、括号、字符串、注释、模板字面量全程平衡扫描；数字路径段与 `${id}` 一样归一成 `{}`，因为 `'/backtests/12'` 与 `` `/backtests/${id}` `` 是同一个请求）；四条下限（操作 ≥ 90、调用点 ≥ 60、不同路径 ≥ 50、可比对响应 ≥ 20）保证解析器失灵时以失败告终，而不是以「全绿」的方式失效；守卫还必须被证明会咬人——同一个文件里有故意坏掉的客户端（不存在的门、门不提供的方法、未声明的查询键、漏掉的必填参数）让四个检查各咬一条，也有守规矩的客户端必须一条都不报，后者特意留了 `summary: { mean: number \| null; nonsense: number \| null }` 把「嵌套字段不是字段」钉死。测试：`backend/tests/test_api_contract.py` 八条全绿；负向注入的证据是在**真实 `api.ts` 原文**上分别改一处（门改名成 `/backtests/compare_all`、方法换成 `DELETE`、删掉必填的 `?ids=`、在 `SensitivityResult` 里加 `nonsense_field`），四个检查各报恰好一条，而未改动的原文报 0 条。边界写进 ADR：字段检查只覆盖 schema 是普通对象/数组/联合的 27 份响应，动态构造的 schema（`/health`、`/system/info`、`POST /strategies` 等）跳过；条件键只检查是否已声明、不算作满足必填参数；泛型里若出现箭头类型（`=>`）会干扰 `<`/`>` 配对，靠下限兜住。不改 API、数据库或前端运行时，`api.ts` 一行未动，今天的行为变化为零，新增的失败面全部属于将来。 |


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
