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
