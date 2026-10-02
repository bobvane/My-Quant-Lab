# My Quant Lab — Personal Quantitative Research Laboratory

> 个人量化策略研究实验室 · NAS Docker 自托管 · **研究用途，不自动交易**
> 技术栈：Python 3.12 + FastAPI + PostgreSQL + Redis + Celery + Vue 3
> 许可：MIT，详见 [LICENSE](./LICENSE)

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

## 主要能力（V1）

- **行情**：synthetic（离线确定性演示）/ Yahoo Finance（美股·ETF·加密），去重、UTC、已收盘标记、数据质量检测（valid/partial/invalid）。
- **策略 DSL**：声明式指标（EMA/SMA/RSI/ATR/MACD/Bollinger，可用 `period_ref` 参数化）、Price Action 特征、条件算子；策略版本不可变、可校验、可验证哈希。
- **回测**：确定性引擎，next-bar-open 成交、手续费/滑点、止损止盈（同 bar 保守成交并标记）、MAE/MFE/R；**限价/停止入场（P1）**；Walk-Forward 与 **OOS 单次留出**；多回测对比、交易明细导出。
- **信号**：只用已收盘 K 线，去重、证据（FeatureSnapshot + 组合上下文）、WAIT/BUY/SELL/NO_SIGNAL、结果回填与统计（胜率/PnL 分组）。
- **模拟盘**：虚拟资金、多头执行、费用/滑点、账户开关/出入金、审计隔离。
- **策略生命周期**：确定性证据门控的晋级/降级（无 AI 介入），参考信号需人工。
- **通知（M11）**：Generic Webhook / 飞书 / Telegram / PushPlus / Email；去重、降噪（状态/免打扰/每日上限/冷却），密钥加密存储。
- **Ghostfolio**：只读接入，持仓市值/成本/盈亏/股息，符号联动（BTC-USD ↔ BITCOIN）。
- **AI（可选）**：仅解释引擎结果，多供应商/模型路由（能力/成本/预算）；未配置时量化功能完全正常。
- **安全**：可选 Bearer 鉴权 + 写请求限流、日志脱敏、审计；GitHub 导入只做静态分析、绝不执行代码。

---

## 快速开始（NAS 部署）

**只需要两个文件**：`docker-compose.yml` 和 `.env`（都放在同一个项目目录里）。
镜像来自 GitHub Packages 预构建，**不需要源码、不需要登录、不需要构建**。

### 图形化 NAS 界面（推荐）

1. 在 NAS 上建项目目录，例如 `/vol1/1000/Docker/My-Quant-Lab/`
2. 把仓库根目录的 `docker-compose.yml` 和 `.env.example` 拷进去，
   后者改名为 `.env`（**文件名就是 `.env`，开头有点、没有扩展名**）
3. 编辑 `.env`，至少改掉这两项：

   ```ini
   POSTGRES_PASSWORD=你的数据库密码
   SECRET_KEY=0123456789abcdef0123456789abcdef
   ```

4. 在容器管理界面里：Compose / 项目 → 选择该目录 → 拉取并启动
   （飞牛/群晖的 Compose 项目会自动 `pull`，不需要你手动构建）

### 终端命令

```bash
mkdir -p /vol1/1000/Docker/My-Quant-Lab && cd /vol1/1000/Docker/My-Quant-Lab
# 把 docker-compose.yml 与 .env.example 放进来，后者改名 .env 并改两处密码
cp .env.example .env
sed -i 's/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=你的密码/' .env
sed -i 's/^SECRET_KEY=.*/SECRET_KEY=0123456789abcdef0123456789abcdef/' .env

docker compose pull && docker compose up -d
```

### 锁定版本（可选）

默认 `MQL_VERSION=latest`（每次 pull 都是最新发布版）。想锁定版本防意外升级：

```ini
MQL_VERSION=v0.9.0
```

### 访问

- **Web 界面**：http://<NAS-IP>:8081
- **API 文档**：http://<NAS-IP>:8081/docs（经 Web 容器代理；API 直连端口默认只绑本机）
- **存活探针**：http://<NAS-IP>:8081/healthz

首次进入「行情与策略」页面：

1. 点击 **同步日线数据**（默认 `synthetic` 行情源，无需 API Key，即可跑通全流程）
2. 编辑左侧 DSL → **校验 DSL** → **创建策略与版本**
3. 回到「研究仪表盘」→ **立即扫描** 查看信号
4. 到「回测实验室」运行回测并查看权益曲线

排查问题：

```bash
docker compose ps -a                    # 容器状态与健康状况
docker compose logs --tail 80 quantlab-api
```

### 常见报错对照

| 报错 | 原因 | 处理 |
|---|---|---|
| `POSTGRES_PASSWORD is required` | `.env` 未创建或未填写 | `cp .env.example .env` 后修改 |
| `container quantlab-api is unhealthy` | 启动失败 | `docker compose logs --tail 100 quantlab-api`，日志会直接给出原因 |
| 拉取镜像缓慢或超时 | 和 GitHub 之间的网络问题 | 多试几次，或换个时间段；也可以在有源码的机器上用 `docker-compose.build.yml` 本地构建 |

> **想用真实行情**：`.env` 中设置 `MARKET_DATA_PROVIDER=yahoo_finance`
> （美股 / ETF / 加密货币，如 AAPL、SPY、BTC-USD；无需密钥，镜像已内置 yfinance）。
>
> 也可以在单次同步时用 `provider` 参数临时指定，例如
> `POST /api/v1/market-data/sync` 请求体带 `{"symbol": "AAPL", "provider": "yahoo_finance"}`。
>
> 两个注意点：
> 1. `synthetic` 只服务 `DEMO-AAPL` / `DEMO-BTC` 两个演示代码。用随机数据冒充实盘代码
>    会被明确拒绝（HTTP 4xx），避免研究结论建立在编造的价格之上。
> 2. Yahoo 可能对同一出口 IP 限流，而它的表现是**返回 0 根 K 线而非报错**；
>    若代码本身有效，稍后重试通常即可。

---

## 安全与暴露面（可选）

默认情况下 API 只绑定 `127.0.0.1`，由 Web 容器代理 `/api`，局域网无法直连。
如果你要把 API 暴露出去，建议在 `.env` 里开启鉴权：

```ini
# 至少 8 位，仅允许 A-Z a-z 0-9 . _ ~ + / = -；留空 = 不鉴权
# 生成方式：openssl rand -hex 24
API_AUTH_TOKEN=
# 每个 IP 对写请求（POST/PUT/DELETE）的每分钟上限，0 = 关闭
RATE_LIMIT_PER_MINUTE=60
```

- 开启后，除 `/api/v1/healthz` 与 `/api/v1/health` 两个探针外，所有 API 请求
  都需要 `Authorization: Bearer <token>`；**内置 Web 容器会自动带上它**，
  浏览器端无需任何配置。
- 通知渠道（Webhook / 飞书 / Telegram / PushPlus / Email）的密钥、AI API Key
  均**加密存储、只写入不回显**；审计日志也绝不包含密钥。
- 更稳妥的做法仍是：不直接暴露 API 端口，在反向代理层再加一层认证。

---

## 可选：从源码构建（开发者）

生产 compose 不含 `build:` 段。需要本地构建时叠加
[`docker-compose.build.yml`](./docker-compose.build.yml)：

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml build
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d
```

---

## 许可

本项目采用 MIT 许可，详见 [LICENSE](./LICENSE)。

---

## 架构（已确定的 Q1–Q10 决策）

### 七个 Docker 服务

| 服务 | 作用 | 网络 |
|------|------|------|
| `quantlab-web` | Vue 3 + TypeScript + Vite（nginx 分发，代理 /api） | frontend |
| `quantlab-api` | FastAPI（REST + OpenAPI），启动时自动执行 Alembic 迁移 | frontend + backend |
| `quantlab-worker` | Celery worker（行情同步、回测、信号扫描） | backend |
| `quantlab-scheduler` | Celery Beat（每 15 分钟扫描一次信号） | backend |
| `quantlab-postgres` | PostgreSQL 16 | backend（不对外暴露） |
| `quantlab-redis` | Redis 7（缓存 + Broker + Result） | backend（不对外暴露） |
| `quantlab-docker-proxy` | 只读 Docker stats 代理，唯一接触 docker.sock 的容器（ADR-024） | backend（不对外暴露） |

> 设计原则：**模块化单体 + Docker 服务化基础设施**。业务域不拆微服务。
>
> 「系统资源监控」需要读取 NAS 上每个容器的状态。为了不把 `docker.sock` 挂进
> `quantlab-api` / `quantlab-worker`，只有 `quantlab-docker-proxy` 挂它，且该代理
> 仅放行两个只读 GET 端点、端口从不发布。不需要容器级监控时可在 `.env` 里设
> `RESOURCE_COLLECTION_ENABLED=false`（代理容器仍在，但不再被调用）。

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
├── docker-compose.yml  # 七服务编排
└── .env.example        # 环境变量模板
```

---

> **免责声明**：本项目仅用于策略研究、回测与模拟，不构成投资建议，
> 不连接任何券商，也不会自动执行真实交易。所有投资决策由你自己做出。
