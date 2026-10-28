# My Quant Lab —— 仓库约定

本文件是本仓库的规范说明，改动本仓库前先读这一遍。

## 语言约定

- **配置文件注释一律用中文**：`docker-compose.yml`、`.env.example`、
  `.github/bundle.Dockerfile`、`.github/workflows/*.yml`。
- **所有面向人的运行提示一律用中文**：脚本的 `print` / `stderr` 输出、
  流水线步骤名（`name:`）、`::error::` 报错文案、`docker compose` 的必填变量提示。
- **面向机器的标识符一律保持英文**：环境变量名、服务名、容器名、镜像地址、
  文件路径、Git 标签、`VERSION` 内容、镜像标签。
- 文档同时提供英文与中文时，两份都要同步改。

## 仓库边界

- 本仓库**只放部署层**：Compose 文件、配置模板、脚本、运维文档。
- 不放应用源码。应用镜像从上游仓库拉取，版本由 `.env` 里的
  `BACKEND_TAG` / `FRONTEND_TAG` 固定。
- 上游源码基线保存在仓库之外的本地目录，不是本仓库的一部分。

## 版本与发布

- `VERSION` 是版本号的唯一来源，发版时标签必须与它一致（`v<版本>`）。
- 每个标签同时产出 GitHub Release 和 GHCR 包，两者的版本号必须相同。
- 发布流水线：`checks` → `package` → `release`，前一步失败后面不会执行。

## 安全底线

- `.env` 永远不进仓库，只提交 `.env.example`。
- 不降低实盘保护：本栈只做模拟盘跟踪，不下真单。
- `CREDENTIAL_ENCRYPTION_KEY` 一经使用就不得更改，否则已存的交易所密钥会解不开。

## 校验

- Compose 改动必须能通过 `docker compose config --quiet`（`validate.yml` 会自动跑）。
- 脚本改动在本地跑一次 `python scripts/bootstrap_env.py`，确认重复执行是幂等的。
