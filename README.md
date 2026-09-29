# My Quant Lab — Personal Quantitative Research Laboratory

> 个人量化策略研究实验室 · NAS Docker 自托管 · **研究用途，不自动交易**
> 当前版本：**v0.0.1** · 技术栈：Python 3.12 + FastAPI + PostgreSQL + Redis + Celery + Vue 3

本项目为非程序员提供**可复现、可解释、AI 辅助**的量化策略研究环境，重点覆盖美股 / ETF /
加密货币的策略研究、回测、Walk-Forward 分析与 Paper Trading。

---

## 五条不可逾越的红线

| # | 红线 | 实现方式 |
|---|------|----------|
| 1 | **不自动交易** | 代码中不存在任何券商下单端点，测试 `test_no_broker_endpoint_exists` 强制校验 |
| 2 | **AI 不决定量化结果** | 所有指标由 `app/research/metrics.py` 计算；AI 只做解释（`app/ai/`） |
| 3 | **回测必须可复现** | 结果哈希 = 策略版本 + 数据集哈希 + 参数 + 引擎版本 + 特征版本 |
| 4 | **真实持仓与模拟盘隔离** | Ghostfolio 只读；模拟账户使用独立表与虚拟资金 |
| 5 | **GitHub 代码视为不可信输入** | 导入器只做文本/结构分析，不执行第三方代码 |

---

## 快速开始（NAS 部署）

```bash
git clone https://github.com/bobvane/My-Quant-Lab.git
cd My-Quant-Lab

# 1) 先跑体检脚本：一次性检查目录完整、.env 已配置、Docker 可用
./scripts/preflight.sh

# 2) 配置（preflight 会提示缺什么）
cp .env.example .env
sed -i 's/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=换成你的密码/' .env
sed -i 's/^SECRET_KEY=.*/SECRET_KEY=0123456789abcdef0123456789abcdef/' .env

# 3) 本地构建并启动（不需要任何镜像仓库账号）
docker compose build
docker compose up -d
```

访问：

- **Web 界面**：http://<NAS-IP>:8081
- **API 文档**：http://127.0.0.1:8080/docs（默认仅本机绑定，避免无认证暴露）
- **存活探针**：http://127.0.0.1:8080/api/v1/healthz

首次进入「行情与策略」页面：

1. 点击 **同步日线数据**（默认 `synthetic` 行情源，无需 API Key，即可跑通全流程）
2. 编辑左侧 DSL → **校验 DSL** → **创建策略与版本**
3. 回到「研究仪表盘」→ **立即扫描** 查看信号
4. 到「回测实验室」运行回测并查看权益曲线

排查问题：

```bash
docker compose ps -a              # 容器状态与健康状况
docker compose logs --tail 80 quantlab-api
```

> 想用真实行情：`.env` 中设置 `MARKET_DATA_PROVIDER=yahoo_finance`
> （免费行情，无需密钥，但受上游限流影响）。

### 常见报错对照

| 报错 | 原因 | 处理 |
|---|---|---|
| `lstat .../My-Quant-Lab/docker: no such file or directory` | 仓库拷贝不完整或过旧（`docker/` 目录缺失） | 重新 `git clone`，或在该目录执行 `git pull` |
| `Head "https://ghcr.io/...": unauthorized` | 试图从私有仓库拉镜像 | 默认已改为本地构建；见下方「使用预构建镜像」 |
| `POSTGRES_PASSWORD is required` | `.env` 未创建或未填写 | `cp .env.example .env` 后编辑 |
| `container quantlab-api is unhealthy` | 启动失败 | `docker compose logs --tail 100 quantlab-api`，日志会直接给出原因 |

---

## 使用预构建镜像（GitHub Packages）

仓库是私有的，拉取前需要登录：

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u <你的GitHub用户名> --password-stdin

# 切换到 GHCR 覆盖文件
docker compose -f docker-compose.yml -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.yml -f docker-compose.ghcr.yml up -d --no-build
```

或在 `.env` 里指定版本号（需先 `docker login ghcr.io`）：

```bash
echo "MQL_VERSION=v0.0.4" >> .env
```

**默认路径不需要登录**：直接 `docker compose build` 即可在本机构建。

---

## 架构（已确定的 Q1–Q10 决策）

### 六个 Docker 服务

| 服务 | 作用 | 网络 |
|------|------|------|
| `quantlab-web` | Vue 3 + TypeScript + Vite（nginx 分发，代理 /api） | frontend |
| `quantlab-api` | FastAPI（REST + OpenAPI），启动时自动执行 Alembic 迁移 | frontend + backend |
| `quantlab-worker` | Celery worker（行情同步、回测、信号扫描） | backend |
| `quantlab-scheduler` | Celery Beat（每 15 分钟扫描一次信号） | backend |
| `quantlab-postgres` | PostgreSQL 16 | backend（不对外暴露） |
| `quantlab-redis` | Redis 7（缓存 + Broker + Result） | backend（不对外暴露） |

> 设计原则：**模块化单体 + Docker 服务化基础设施**。业务域不拆微服务。

### 应用内部领域层

```text
backend/app/
├── domain/         # 枚举、ORM 模型、Provider 协议
├── data/           # 仓储、行情 Provider（Graft 抽象）
├── features/       # 指标 + Price Action（纯确定性函数）
├── strategies/     # DSL Schema、静态校验器、确定性执行器
├── research/       # 回测引擎、绩效指标、Walk-Forward
├── simulation/     # 模拟盘、信号引擎
├── ai/             # Provider Adapter（仅解释，不计算）
├── infrastructure/ # 密钥加密、日志脱敏
├── api/            # FastAPI 路由与 Schema
└── workers/        # Celery 任务
```

---

## 量化引擎契约

### 时间推进（无未来函数）

```text
bar t 收盘
  → 用截至 t 的信息评估信号
  → 订单在 t+1 开盘成交（fill_model = next_bar_open）
```

- 指标预热期输出 `NaN`，不参与交易；
- 突破位使用**当前 K 线之前**的 N 根最高/最低价；
- 截断历史数据后重算，历史区间的特征值必须完全一致
  （`test_build_features_lookahead_regression` 强制校验）。

### 成交与成本

- 默认 `next_bar_open` 成交，滑点与手续费按 bps 计入；
- 止损与止盈在同一根 K 线同时触发时，**按保守的止损价成交**并标记
  `ambiguous_fill`，绝不选择对结果更有利的一侧。

### 可复现性

每次回测记录并可校验：

```text
Strategy Version (immutable_hash) + Dataset Hash + Parameters
+ Engine Version + Feature Version  →  Result Hash
```

策略版本在**数据库层面**由触发器保护（`alembic/versions/0002_immutability.py`），
已完成的回测结果同样不可篡改。

---

## 本地开发

### 后端

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install pytest ruff

export DATABASE_URL=sqlite+pysqlite:///:memory:      # 测试无需外部服务
export MARKET_DATA_PROVIDER=synthetic

pytest -o addopts=            # 运行全部测试
ruff check app tests          # 静态检查
python scripts/check_examples.py   # 校验示例策略可跑通
```

启动开发服务器：

```bash
uvicorn app.api.main:app --reload --port 8080
```

### 前端

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173，/api 代理到 8080
npm run build
```

---

## 版本与发布

版本号从 `v0.0.1` 起，每段 0–9，到 10 进位（`v0.0.10 → v0.1.0`）。

```bash
./scripts/version.sh show          # 查看当前版本
./scripts/version.sh bump --tag    # 升版本、提交、打标签
git push origin main && git push origin <新版本号>
```

推送标签后 `release.yml` 会自动：

1. 构建并推送 `backend` / `web` 镜像到 GHCR；
2. 用该版本镜像跑一次冒烟测试；
3. 创建 GitHub Release，附带 NAS 部署说明。

---

## 常见问题

**页面能打开但提示后端不可用**
后端默认只绑定 `127.0.0.1:8080`，由 `quantlab-web` 代理访问。若要从局域网直接调 API，
把 `.env` 的 `API_BIND` 改为 `0.0.0.0`（**请自行加反向代理与认证**）。

**回测提示「需要至少 60 根已收盘 K 线」**
先在「行情与策略」页面同步数据。

**指标显示 N/A**
样本不足时系统**故意不显示**数值，而不是编造。样本量满足后自动出现。

**AI 解释不可用**
AI 是可选能力。未配置 provider 时量化功能全部正常，只是没有自然语言解释。

---

## 目录结构

```text
My-Quant-Lab/
├── backend/            # FastAPI 应用、领域层、迁移、测试
├── frontend/           # Vue 3 + TypeScript 界面
├── docker/             # Dockerfile、entrypoint、nginx 配置
├── examples/strategies # 示例策略 DSL
├── scripts/version.sh  # 版本与发布脚本
├── docs/               # 开发文档（设计规格）
├── docker-compose.yml  # 六服务编排
└── .env.example        # 环境变量模板
```

---

> **免责声明**：本项目仅用于策略研究、回测与模拟，不构成投资建议，
> 不连接任何券商，也不会自动执行真实交易。所有投资决策由你自己做出。
