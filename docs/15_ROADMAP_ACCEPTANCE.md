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

### v1.1 — 进行中

| 项 | 状态 | 说明 |
|---|---|---|
| 参数覆盖真正生效 + 结果哈希只覆盖有效参数 | DONE | ADR-040。修复「哈希说谎」：override 曾被折进哈希却从未应用 |
| 参数敏感性分析（后端 + API） | DONE | ADR-041 / `docs/21`。网格扫描、统计、稳健性判定、审计事件 |
| 参数敏感性分析（前端热力图） | DONE | 回测实验室接入，1 轴折线 / 2 轴热力图，并列交易数防「靠不交易变好看」 |
| 示例策略参数化（`period_ref`） | DONE | 两个示例原先的规则引用了**不存在的列**（`ema20`），实际被当作常量 20；已修正并加回归测试 |
| 行情 provider 语义写入文档 | DONE | README 说明换 provider 后演示代码必然 0 根 K 线 |
| Windows/UNC 开发脚本 | DONE | `scripts/Invoke-Tests.ps1`、`Invoke-FrontendChecks.ps1`、`Test-NasDeployment.ps1` |
| 清理 `StarletteDeprecationWarning`（httpx） | TODO | CI 输出仍有 1 条告警 |
| v2 结构性能力（Monte Carlo / 组合级仓位 / Ensemble） | TODO | 需新增 ADR 与 fixtures（ADR-037 §4） |


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
