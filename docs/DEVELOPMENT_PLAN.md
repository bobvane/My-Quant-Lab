# My Quant Lab 当前开发计划

> 这份文件只回答一个问题：**现在还剩什么要做**。
> 已完成的事只列结论和证据，不再列为待办——避免把做完的事重新做一遍。
> 每一条都指向代码、测试、提交或 ADR；没有证据的结论不写进来。
> 更新时间：2026-10-12（正式版本 `v2.7.0`）。

---

## 0. 当前状态

| 项目 | 现状 | 证据 |
|---|---|---|
| 正式版本 | `v2.7.0`（2026-10-12 发布） | tag `v2.7.0` → `e81c2ddcb`，GitHub Release = Latest |
| 版本文件 | `version.txt = v2.7.0`，五处版本引用一致 | `backend/tests/test_release_version_scheme.py`（18 passed） |
| 正式镜像 | `ghcr.io/bobvane/my-quant-lab-app:v2.7.0` | release 工作流 run `38208079789` 的 `Smoke test the released images` 真的拉起来并跑过 `scripts/verify-stack.sh` |
| 迁移链 | head = `0022_paper_order_signal_unique`（22 个迁移，单 head） | `backend/tests/test_migration_revisions.py`、`test_migrations_sqlite.py` |
| 全量测试 | **1786 passed, 6 skipped** | CI run `38199648656`（真 PostgreSQL 步骤另有 5 + 47 + 12 passed） |
| 契约守卫 | **110 passed** | `test_ui_promises.py` / `test_api_spec_truth.py` / `test_frontend_contracts.py` / `test_lab_journey_contracts.py` / `test_migration_revisions.py` / `test_examples.py` |

**怎么用这份文件**：要挑活时先看 §7 的推荐顺序；遇到「这件事到底做没做」先查 §1；
遇到「为什么不能那样做」查 §3；遇到需要拍板的查 §5。历史上每次修补的细节留在
`docs/17_DECISIONS.md`（ADR 日志）与 `docs/35_OVERALL_COMPLETION_AUDIT.md`（一次完整审计快照）里，不必重读。

---

## 1. 已完成并有证据（不要再当待办）

| 能力 | 落点 | 主要证据 |
|---|---|---|
| 部署与运行时：三容器四进程、健康检查、启动自动迁移 | `docker-compose.yml`、`docker/entrypoint.sh`、`backend/app/api/routers/health.py` | CI 四个 job 全绿；`docker compose` 冒烟 5 步 |
| 正式发布流程：版本文件同步、镜像标签、发布冒烟、GitHub Release | `.github/workflows/release.yml`、`scripts/version.sh` | run `38208079789` 12 步全 success |
| Nightly 镜像与验证 | `.github/workflows/nightly.yml` | run `38204500237` success |
| 行情接入与质量评估（多源、去重、时区、缺口检测、归档） | `backend/app/data/providers.py`、`market_data_repo.py`、`backend/app/api/routers/market_data.py` | 全量套件 + `test_market_data_quality.py` |
| 技术指标与价格行为特征（确定性计算） | `backend/app/features/` | 全量套件 |
| Strategy DSL 与版本管理（校验、版本不可变、激活、哈希校验） | `backend/app/strategies/dsl.py`、`validator.py`、`lifecycle.py`、`backend/app/data/strategy_service.py` | 全量套件 + `test_strategy_*` |
| 策略编译器：三种结论、15 个拒绝码、`USER_REQUIRED` 槽位、规范条件 | `backend/app/compiler/` | `test_compiler_contract.py` 对着冻结契约 `docs/29` 逐条验 |
| 确定性回测引擎：无未来函数、成本模型、可复现哈希 | `backend/app/research/engine.py`、`metrics.py` | CI 的 `Smoke 4/5` 同一请求跑两次 `result_hash` 相同（ADR-218） |
| AI 研究链条：材料 → 假设 → 草案 → 能力检查 → 人工确认 | `backend/app/ai/research.py`、`research_schemas.py`、`confirmation.py` | `test_draft_answers.py`、`test_ai_strategy_draft.py` |
| 编译器提问 → 用户作答 → 新草案（v2.7.0 补齐的缺口） | `backend/app/ai/draft_answers.py`、`GET/POST /ai/strategy/drafts/{id}/questions|answers` | 浏览器 UAT 50/50（ADR-211）、陈旧标签页 13/13（ADR-213）、PG CI 47 passed |
| 模拟交易幂等：一个信号只产生一笔订单 | `backend/app/simulation/paper_engine.py`、迁移 `0022` | `test_paper_idempotency.py` 12 passed（SQLite 与 CI 上的真 PostgreSQL）、浏览器 UAT 23/23（ADR-221） |
| 信号引擎、结果回填、通知（Webhook/飞书/Telegram/PushPlus/邮件） | `backend/app/simulation/signal_engine.py`、`outcome_evaluator.py`、`backend/app/notifications/` | 全量套件 |
| 实验与比较、模拟账户与归因 | `backend/app/data/experiment_service.py`、`backend/app/api/routers/{experiments,paper}.py` | 全量套件 + 浏览器 UAT |
| GitHub 导入四道防线（不执行仓库代码、域名与端口校验、DNS 重绑定防护、外部文本永不进 system） | `backend/app/importer/`、`backend/app/sources/` | 全量套件 + 来源/导入定向 214 passed |
| 审计与卡死运行回收 | `backend/app/data/strategy_service.py`、`run_recovery.py`、`backend/app/workers/tasks.py` | 全量套件 |
| 前端十个页面 + 未知道路兜底 + 普通/高级模式 + 指标解释 | `frontend/src/views/`、`main.ts`、`metrics.ts` | `Invoke-FrontendChecks.ps1`（typecheck + build）与四个文本守卫 |

---

## 2. 已实现，但还没有端到端验证过

这些**不是缺陷**，是「代码在、流程通，但真实环境下的表现没有人替你验过」。

| 项目 | 现状 | 怎么补验 |
|---|---|---|
| AI 研究链条用**真实**模型跑一遍 | 流程用本机 AI 桩验过；真实线路的产出质量、超时、额度消耗没有替你验 | 在 `/settings → AI` 配好线路，在 `/lab` 提一个真问题跑一次 |
| 通知渠道真实投递 | 渠道类型、去重、静默时段、每日上限、投递记录都在；真实外部服务只在你配置后才通 | `/settings → 通知` 里配置后点「发送测试通知」，再看「最近投递」 |
| Ghostfolio 只读接入 | 适配器与「测试连接」都在；需要你填地址与密钥 | `/settings → 系统设置` 填好后点测试 |
| 数据质量判定要不要拦下游 | 质量字段有值、页面会显示，但**不会**阻止用有问题的数据跑回测（见 §5-4） | 需要你先决定是否拦住 |
| 真实行情源（Yahoo）的边界行为 | 限流表现是「返回 0 根 K 线」而不是报错，属已知行为；未在长周期、多标的场景下验过 | 在 `/data` 长时间同步后看质量列 |
| 生产库里是否已有重复 `signal_id` 的历史订单 | 本机无法访问生产库；迁移遇到重复会**拒绝执行并点名**，不会删改 | 升级到含 `0022` 的版本时看应用日志；若有重复，处置方式需你决定 |

---

## 3. 已知限制（当前设计的边界，不是 bug）

1. **标的（universe / markets）不进策略 DSL**。标的属于「回测请求与数据集参数」，在交接时由人选；
   AI 草案若点名标的，编译器会判 `not_expressible`（终止状态，作答通道不给它表单）。
   这是冻结契约 `docs/29_STRATEGY_COMPILER_CONTRACT.md` 的既定行为，也是 §5-1。
2. **不支持做空**（`market.allow_short` 无法由草案表达）。编译器会问，但 v1 无法安全作答。
3. **V1 是长仓单标的**：同一账户同一标的不允许同时持有多头以外的方向。
4. **数据质量只描述「最后一次抓取」**，不覆盖序列的全生命周期。
5. **回测的样本外与滚动验证只算不落库**，因此不能事后复现比对。
6. **信号 → 模拟成交由人按一次**，系统不会自动下单（红线之一）。
7. **`dataset_version` 永远是 `v1`**：`BacktestRun.dataset_version_id` 存的其实是行情序列 id，
   列名与内容不一致（`backend/app/domain/models.py:132`、`:302`）；只影响可读性。
8. **`PaperAccount.is_experimental_of` 从未被写入**（`models.py:1080`，全仓只有一处读），
   对外却发布该字段。
9. **账户的 `strategy_version_id` 与它执行过的信号版本不做一致性校验**。
10. **默认无登录鉴权**：局域网内可读写 API，只有 `API_AUTH_TOKEN` 与限流两道可选闸门（红线与安全说明见 `docs/PROJECT_OVERVIEW.md` §10）。

---

## 4. 未来计划（尚未实施，也还没有排期）

- **G8「继续研究」V1→V2 迭代**：在研究页把一次研究的结论变成下一轮的输入。需要新的实验语义与
  研究层状态机，属于产品设计，不是补接口。
- **研究员阶段单独重跑**：目前只能整条链重跑。
- **前端自动化测试框架**：现在靠四个 Python 文本守卫盯着 UI 承诺，没有真实的组件测试。
- **`result_hash` 口径扩容**：把 DSL 正文、成交模型与代码版本纳入哈希（改口径须同版提升
  `ENGINE_VERSION`，见 `docs/07_BACKTEST_ENGINE.md`）。
- **通知渠道重试与故障转移**、**AI 线路重试**：都涉及重复计费/重复投递的取舍，先等决定。
- **Docker 容器减少（7→3）**：`docs/31`、`docs/32`、`docs/33` 已做可行性分析，属高风险架构改造，未获授权。

---

## 5. 需要你决定的事项

| # | 事项 | 为什么不能由我决定 | 现在的影响 |
|---|---|---|---|
| 1 | 草案点名标的怎么办（改 AI 层「草案不再点名标的」，还是改冻结编译器容忍标的） | 两条路都要动契约或业务语义 | 这类草案会停在「无法编译」 |
| 2 | 要不要支持做空 | 新的业务语义与风控 | 做空策略无法编译 |
| 3 | 生产库里若已有重复 `signal_id` 的订单怎么处置（保留 / 人工挑选 / 其他） | 涉及真实账户账本，且你未授权自动清理 | 迁移会拒绝执行并点名，不会删改 |
| 4 | 数据质量为 `invalid` 时要不要拦住回测与信号 | 会让「本来能跑」的实验跑不了，是产品决策 | 现在照样能跑 |
| 5 | 是否启用身份鉴权（`API_AUTH_TOKEN`） | 影响你的日常访问方式 | 局域网内可读写 API |
| 6 | GitHub 观察者要不要在无人确认时自动创建并激活策略版本 | 涉及自动改写「当前版本」 | 现状是会自动激活 |
| 7 | `/importer/github/analyze` 是否继续接受客户端提交的 token，以及确认记录要不要绑定草案内容哈希 | 前者涉及凭据来源，后者超出本轮范围 | 风险有限，但不符合最小信任 |
| 8 | AI 数字准入（禁止 AI 编造数字）要不要扩展到信号解释与回测解释 | 现在只有绩效解释过机检 | 另两处解释靠提示词约束 |
| 9 | 通知/AI 线路要不要自动重试 | 可能重复计费、重复投递 | 现在失败即如实报错 |

---

## 6. 暂不投入（现在做没有收益或风险过高）

- 界面美化、文案微调、非历史文档补齐。
- 依赖大版本升级、Docker 架构简化、任务队列替换、数据存储方案更换。
- 为「看起来更完整」而补写历史审计报告、搬动历史文档目录。
- 在没有上述决定之前实现的标的/做空支持。

---

## 7. 推荐顺序（要挑一件做时）

1. **数据质量要不要拦下游**（§5-4）：投入小、直接影响「会不会用错数据得出漂亮结论」，是正确性问题。
2. **`result_hash` 口径扩容**（§4）：让「可复现」这句话覆盖得更多；改动集中在引擎一处，但要同版提升 `ENGINE_VERSION`。
3. **确认记录绑定草案内容哈希 + importer 服务端 token**（§5-7）：金额不大但属于安全边界收口。
4. **账户版本一致性与 `is_experimental_of`**（§3-8、§3-9）：把发布出去的字段与真实语义对齐。
5. 其余按 §5 的决定再排。

> 每一项开工前照 `docs/DEVELOPMENT_GUIDE.md` 的检查单走：先定位根因、写清验收标准、
> 只改必要文件、跑专项测试 + 回归 + `ruff` + 前端 typecheck/build，涉及数据库要验完整迁移链。
