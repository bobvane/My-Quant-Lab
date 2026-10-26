# My Quant Lab

一套**只跑在一台 NAS 上**的个人量化研究环境的部署层。
盯美股和加密货币、跑策略回测、用**模拟盘**记录每一笔信号（不下真单），
并把信号和每日汇总推到 Telegram。

本仓库**只有部署层**：一个 Compose 文件、一份配置模板、几页运维说明。
应用程序本身是拉取预构建镜像运行的，所以这里没有任何应用源码。

- English: [README.md](README.md)
- 在飞牛 fnOS 上部署：[docs/NAS_DEPLOYMENT_CN.md](docs/NAS_DEPLOYMENT_CN.md)
- 依赖与许可说明：[NOTICE.md](NOTICE.md)

## 仓库内容

| 路径 | 作用 |
| --- | --- |
| `docker-compose.yml` | 整套服务，10 个容器 |
| `.env.example` | 配置模板，复制成 `.env` 再填 |
| `scripts/bootstrap_env.py` | 自动生成三个必填的随机密钥 |
| `docs/NAS_DEPLOYMENT_CN.md` | 图形界面部署步骤 |
| `.github/workflows/validate.yml` | 每次推送校验 Compose 文件 |

## 容器

| 容器 | 作用 |
| --- | --- |
| `mql-web` | 网页，并把 `/api` 反代给后端 |
| `mql-api` | HTTP 接口 |
| `mql-trading` | 策略运行时：算 K 线、记模拟单 |
| `mql-scheduler` | 持仓监控、订单对账、品类同步 |
| `mql-celery` | 回测、AI 任务、维护任务 |
| `mql-beat` | 定时任务调度 |
| `mql-migration` | 一次性建表 |
| `mql-db` | PostgreSQL 18，唯一可信数据源 |
| `mql-redis` | 可丢的缓存 |
| `mql-redis-jobs` | 持久化的任务队列 |

没有消息队列、没有手机端、没有监控套件。策略在 `mql-trading` 进程内直接算，
所以 10 个容器就够了。

## 快速开始

前提：x86_64 机器，装好 Docker 与 Docker Compose v2，至少 4 核 CPU、8 GB 内存。

```bash
cp .env.example .env
python scripts/bootstrap_env.py     # 自动填 POSTGRES_PASSWORD / SECRET_KEY / CREDENTIAL_ENCRYPTION_KEY
docker compose pull
docker compose up -d
```

浏览器打开 `http://<NAS 的 IP>:8888`，用 `.env` 里的 `ADMIN_USER` /
`ADMIN_PASSWORD` 登录。

第一次启动要先等建表容器跑完，大约一分钟。`docker compose ps` 里
`mql-migration` 显示 `exited (0)` 就表示建表成功。

## 端口

| 端口 | 绑定范围 | 用途 |
| --- | --- | --- |
| 8888 | 所有网卡 | 网页，局域网要能访问 |
| 5000 | 仅本机 | 后端接口，经网页容器反代 |
| 5432 | 仅本机 | PostgreSQL，仅管理员直连 |
| 6379 | 仅本机 | Redis 缓存，仅管理员直连 |

如果 NAS 会被公网访问，请在 8888 前面加一层带 TLS 的反向代理，其余端口不要
映射出去。

## 日常运维

```bash
docker compose ps                      # 看状态
docker compose logs -f mql-api         # 跟某个服务的日志
docker compose pull && docker compose up -d    # 升级
docker compose down                    # 停止，数据保留
docker compose down -v                 # 停止并删除数据库
```

备份数据库：

```bash
docker compose exec mql-db pg_dump -U quantdinger quantdinger > backup-$(date +%F).sql
```

镜像版本固定在 `.env` 的 `BACKEND_TAG` / `FRONTEND_TAG`。升级是一个明确的动作：
改 `.env`，再 `docker compose pull && docker compose up -d`。
`CREDENTIAL_ENCRYPTION_KEY` 必须保持不变，否则已保存的交易所密钥会解不开。

## 版本

本部署层独立编号：`0.1.0` 是第一个在目标 NAS 硬件上确认能跑起来的版本。
标签用 `v0.1.0`、`v0.2.0`……；底层应用版本在 `.env` 里单独固定。

## 说明

- 这套环境不下真单。即使填了交易所密钥，也只用于读余额和行情，成交是本地算出来的。
- 模拟盘结果是假设性的：用的是历史与延迟数据，没有完全计入手续费、滑点与流动性差异。
- 许可与署名见 [NOTICE.md](NOTICE.md)。
