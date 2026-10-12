# My Quant Lab 开发指南（技术与安全约束）

> 这份文件回答：**改这个项目时必须守住什么**。
> 项目是什么、有哪些模块，看 `docs/PROJECT_OVERVIEW.md`；还剩什么要做，看 `docs/DEVELOPMENT_PLAN.md`。
> 这里只写现在还生效的规则，不重复每次修补的过程。

---

## 1. 技术栈与目录

- 后端：Python 3.12+、FastAPI、SQLAlchemy 2、Alembic、Celery；数据库 PostgreSQL 16，缓存/队列 Redis 7。
- 前端：Vue 3 + TypeScript + Vite + ECharts。
- 运行形态：一个应用镜像（`quantlab-app`）+ `quantlab-postgres` + `quantlab-redis`；
  应用容器里跑四个进程（nginx、uvicorn、celery worker、celery beat），由 `docker/entrypoint.sh` 按 `APP_ROLE` 决定角色。

代码落点见 `docs/PROJECT_OVERVIEW.md` §7。几个容易找错位置的：

| 你以为在这里 | 实际在这里 |
|---|---|
| `backend/app/backtest/` | `backend/app/research/engine.py` + `backend/app/data/backtest_service.py` + `backend/app/api/routers/backtests.py` |
| `backend/app/paper/`、`backend/app/signals/` | `backend/app/simulation/paper_engine.py`、`signal_engine.py`、`outcome_evaluator.py` |
| `backend/app/market/` | `backend/app/data/providers.py`、`market_data_repo.py`、`backend/app/features/` |
| `backend/app/ghostfolio/` | `backend/app/data/ghostfolio.py` |
| `backend/app/main.py` | `backend/app/api/main.py`（uvicorn 入口是 `app.api.main:app`） |

---

## 2. 本地开发与测试

### 2.1 后端

```powershell
# 依赖与虚拟环境（已存在时跳过）
Q:\backend\.venv\Scripts\python.exe -m pip install -e "Q:\backend[dev]"

# 跑测试：工作目录必须是 Q:\backend，否则 Alembic 找不到 alembic.ini
Set-Location Q:\backend
$env:DATABASE_URL='sqlite+pysqlite:///:memory:'
$env:MARKET_DATA_PROVIDER='synthetic'
$env:SECRET_KEY='test-secret-key-0123456789abcdef'
$env:APP_ENVIRONMENT='test'
$env:TZ='UTC'; $env:PYTHONUTF8='1'
Q:\backend\.venv\Scripts\python.exe -m pytest tests/<聚焦文件> -o addopts= -p no:cacheprovider -q
```

本机经验（会踩的坑）：

- 工作目录不是 `Q:\backend` 时报 `No 'script_location' key found in configuration.`。
- 本机建议加 `--basetemp "%TEMP%\mql-bt"`，否则 `%TEMP%\pytest-of-...` 的坏链接会让收尾抛 `PermissionError`。
- 测试默认用内存 SQLite；需要真 PostgreSQL 的用例带 `pytest.mark.postgres`，只有设了 `TEST_POSTGRES_URL` 才真正连接（CI 里连 `localhost:5433/quantlab_test`）。
- 每次 pytest 都会打印一条无害噪声 `stuck-run recovery sweep failed ... no such table: backtest_runs`，可以忽略。

### 2.2 前端

不能在 UNC 路径上直接 `npm run build`，用脚本（它会镜像到本地目录再构建）：

```powershell
Set-Location Q:\
.\scripts\Invoke-FrontendChecks.ps1        # npm ci → vue-tsc --noEmit → vite build
```

跑之前先停掉本机镜像目录里可能还开着的 vite（`C:\Users\bobvane\AppData\Local\mql-fe-build`），
否则 `npm ci` 会和它抢 `node_modules`，随后报 `'vue-tsc' is not recognized`。

### 2.3 静态检查

```powershell
Set-Location Q:\backend
Q:\backend\.venv\Scripts\python.exe -m ruff check app tests
Q:\backend\.venv\Scripts\python.exe -m ruff format --check app tests
```

**范围就是 `app tests`**。`alembic/` 与 `scripts/` 里有一批历史格式问题（`ruff check .` 会报 75 个），
CI 不管它们；改到那些文件时不要顺手全量重排，避免无关 diff。

---

## 3. CI 与发布流程

`.github/workflows/ci.yml` 四个 job（push 到 `main` 与 PR 都跑）：

| job | 内容 |
|---|---|
| `backend tests + lint` | `ruff check app tests` → `ruff format --check app tests` → 全量 `pytest --cov-fail-under=85.5` → PostgreSQL 触发器回归 → G7 草案问答（PG）→ 模拟幂等（PG）→ 上传覆盖率 |
| `frontend build` | `npm ci` → `npm run typecheck` → `npm run build` |
| `docker compose smoke test` | 起真实栈，5 步冒烟：健康、迁移、web 首页、跑回测（同一请求跑两次比 `result_hash`）、暴露面 |
| `celery redelivery` | 任务重投递可靠性（P0 机制，不可削弱） |

注意 `concurrency: cancel-in-progress: true`：**推了新提交就只等当前 HEAD 的 CI**，前一个 run 会被取消。

发布（`.github/workflows/release.yml`，`on: push: tags: ['v*']`）：

1. 打 tag 前先用 `bash scripts/version.sh set vX.Y.Z` 改版本并提交（它同步五处：
   `version.txt`、`backend/app/__init__.py`、`backend/pyproject.toml`、`frontend/package.json`、
   `frontend/package-lock.json`；**`.env.example` 故意不同步**，模板永远 `MQL_VERSION=latest`，见 ADR-172）。
2. 推 `main`，再推 tag。release 会校验 tag 与 `version.txt` 一致，构建并推送镜像
   `ghcr.io/bobvane/my-quant-lab-app`（标签 `vX.Y.Z` / `X.Y.Z` / `X.Y`，最高稳定版额外拿 `latest`），
   **用该镜像真起一次栈跑冒烟**，然后创建 GitHub Release。
3. 版本号规则（ADR-079）：每段 0–9、到 10 进位，`v1.5.9 → v1.6.0`；`scripts/version.sh set` 会拒绝不合规的号。
4. 部署节奏（ADR-086）：**只有 `X.Y.0` 需要部署到 NAS**，补丁版本只推送。
5. `nightly` 是可变标签，只用于试跑；正式发布不依赖它。

---

## 4. 数据库与迁移

- 迁移链在 `backend/alembic/versions/`，当前 head = `0022_paper_order_signal_unique`，**必须保持单 head**。
- revision id 必须 ≤32 字符、且文件名与 id 一致（`backend/tests/test_migration_revisions.py` 守卫）。
- 容器启动时会自动 `alembic upgrade head`，迁移失败会直接启动失败并打印原因（不会留半改状态）。
- SQLite 不能 `ALTER TABLE ADD CONSTRAINT`（会报 `NotImplementedError`）：**加约束要用 batch 模式**
  （`with op.batch_alter_table(..., recreate="auto") as batch:`）或改用 `CREATE UNIQUE INDEX`
  （ADR-219、ADR-221 的取舍记录在 ADR 里）。加唯一索引优先于加唯一约束：两个方言都原生支持，
  且不会重建被外键引用的父表，也天然允许多个 NULL。
- 新迁移要**从空库一路升到 head**、要**从 head 一路退回 base 再升回来**
  （`backend/tests/test_migrations_sqlite.py::test_every_revision_can_be_walked_back_down_to_base`），
  要能在**已有数据的旧库**上升级。
- 涉及数据安全的迁移不许自动删改历史数据：`0022` 的做法是升级前预检重复，遇到冲突**拒绝执行并点名**，
  把处置权留给人。
- 大表主键用 BIGINT（ADR-048 的教训）。

---

## 5. 安全边界（不可逾越）

1. **不自动交易**：系统只产出研究信号与模拟成交，任何路径都不接券商下单。
2. **AI 只解释、不产生数字**：所有数字来自确定性引擎；AI 输出必须是定性说明。
3. **回测可复现**：同样的输入必须得到同样的 `result_hash`；改成交语义要同版提升 `ENGINE_VERSION`
   （`docs/07_BACKTEST_ENGINE.md`）。
4. **真实与模拟隔离**：模拟账户、模拟订单与真实持仓不混用；Ghostfolio 只读。
5. **外部代码与文本不可信**：导入的 GitHub 代码只做静态分析、绝不执行；外部来源正文永不进 system 提示。
6. **出站请求要防 SSRF**：抓取与通知/Webhook 都要校验协议、端口、重定向与解析后的地址（`backend/app/sources/`、`backend/app/notifications/provider.py`）。
7. **密钥不进仓库、不进日志**：只走环境变量或数据库里的加密字段，对外只暴露掩码。
8. **不碰生产**：不 SSH 登录 NAS、不在 NAS 上执行 Docker 命令、不直接读写生产数据库、
   不自动部署或重启 NAS 容器、不为测试修改生产数据、不覆盖已发布镜像。验证只在 CI 或本机隔离环境做。
9. **不动用户文件**：`docs/02_ARCHITECTURE.md`、`docs/15_ROADMAP_ACCEPTANCE.md`、`docs/31*`、`docs/32*`、
   `docs/33*`、`AGENTS.md`、`docs/agents/` 属于用户自己维护的内容，不得覆盖、删除、擅自暂存或提交。
10. **冻结契约**：`docs/29_STRATEGY_COMPILER_CONTRACT.md` 未经明确批准不得修改。它规定了编译器的
    输入、三种结论、15 个拒绝码与「标的不可表达」等语义，`backend/tests/test_compiler_contract.py` 逐条盯着它。

> 需要用户明确批准才能做的事：改冻结契约与既有业务语义、增删数据库表/迁移（除明确授权的缺口补齐）、
> 调整鉴权与网络暴露、发布正式版本或 tag、任何触碰 NAS 生产环境的操作、清理历史业务数据。

---

## 6. 这些守卫在盯什么（改前端/接口时最容易踩）

前端**没有组件测试框架**，UI 承诺靠 Python 文本守卫：

| 守卫 | 盯住的东西 |
|---|---|
| `backend/tests/test_ui_promises.py` | `docs/13_UI_UX.md` 的导航树必须与 `frontend/src/main.ts` 的真实路由一致；含 `:` 的路由必须在这份文档里被点名；「欠账」节只能列出文档里真的没实现的小节 |
| `backend/tests/test_frontend_contracts.py` | 每个页面的关键按钮/文案/状态存在且用真实 API；不许出现假数据、写死的第二份规则 |
| `backend/tests/test_api_spec_truth.py` | `docs/12_API_SPEC.md` 里每一行端点声明恰好一个 `[已实现]/[计划]/[取消]` 标记；API 真在服务的路由必须被声明过 |
| `backend/tests/test_lab_journey_contracts.py` | 研究链条每一步的前后端契约（含 G7 问答、实验打开失败、幂等回执） |
| `backend/tests/test_migration_revisions.py` | 单 head、revision id 长度与命名 |
| `backend/tests/test_examples.py` | `examples/strategies/*.json` 必须真的通过 DSL 校验 |
| `backend/tests/test_compiler_contract.py` | 编译器与冻结契约 `docs/29` 一致 |
| `backend/tests/test_ai_provider_boundary.py` | AI 层模块清单是固定的，新增 AI 模块要显式登记 |

改动前先想一遍：**我改的这句文案/这个按钮，会不会踩到上面某一条？** 会的话，改代码而不是改守卫。

---

## 7. 什么算「做完了」

一项工作只有同时满足下面几条才算完成：

1. 行为按预期实现，失败路径也有明确、人能看懂的说明（不写死按钮、不静默吞错）。
2. 既有行为没有被破坏（跑相关回归，而不是只看新用例）。
3. 有对应的测试：新行为要有能证明它的用例；修 bug 要先能复现。
4. 静态检查通过：`ruff check app tests`、`ruff format --check app tests`。
5. 改了前端就跑了 `scripts/Invoke-FrontendChecks.ps1`（typecheck + build）。
6. 改了数据库就验了完整迁移链（空库 → head、head → base → head、旧库升级）。
7. 涉及用户可见流程，做了一次真实浏览器验证，并留下可复读的证据（DOM 文本/接口返回，不是「点过了」）。
8. 提交信息说得清做了什么、为什么；`HEAD == origin/main` 且 CI 四个 job 全绿。
9. 没有在验证里绕过测试、放宽断言、跳过安全检查或把没跑过的测试写成通过。
10. 没有顺手改无关代码、没有改用户自有文件、没有动版本号/tag（除非本次任务就是发布）。

**如实区分**：实现了 / 本地验证了 / 构建过了 / 容器起来了 / 冒烟过了 / 用户部署了 —— 这六件事不能互相顶替。
