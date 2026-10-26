# 在飞牛 fnOS 上部署

目标：在一台 x86_64、16 GB 内存的 NAS 上，用图形界面把 10 个容器跑起来，
全程不需要 SSH。

---

## 0. 准备

| 项目 | 要求 |
| --- | --- |
| NAS 架构 | x86_64（不是 ARM 的低功耗型号） |
| 内存 | ≥ 8 GB，16 GB 更稳 |
| Docker | fnOS「应用中心」里安装 Docker 应用 |
| 磁盘 | 预留 30 GB（镜像 + 数据库 + 回测缓存） |

后续命令默认数据盘挂在 `/vol1`。如果你的数据盘是 `/vol2`，把下面的路径整体替换。

---

## 1. 在电脑上生成 `.env`

`.env` 里三个密钥必须是随机值，随机值在 NAS 上不方便生成，所以在电脑上先做好。

1. 下载本仓库（页面右上角 `Code` → `Download ZIP`），解压。
2. 在解压出来的目录里打开 PowerShell，执行：

   ```powershell
   Copy-Item .env.example .env
   python scripts/bootstrap_env.py
   ```

   屏幕会打印一行 `Administrator password: xxxxxxxxxxxxxxxx`，**先记下来**，
   这是登录用的初始密码。

   如果电脑上没有 Python，用任意方式生成三段 64 位十六进制随机串，分别填进
   `POSTGRES_PASSWORD`、`SECRET_KEY`、`CREDENTIAL_ENCRYPTION_KEY`，再自己填
   `ADMIN_PASSWORD`。三段的长度都要 ≥ 10 个字符。

3. `.env` 里再确认这几项：

   ```
   ADMIN_USER=admin
   ADMIN_PASSWORD=刚才记下的密码或你自己设的
   TZ=Asia/Shanghai
   FRONTEND_PORT=8888
   ```

---

## 2. 上传到 NAS

只上传**两个文件**，不需要整个目录：

| 文件 | 说明 |
| --- | --- |
| `docker-compose.yml` | 服务定义 |
| `.env` | 你的配置和密钥 |

步骤：

1. 打开 fnOS「文件管理」，进入数据盘，新建目录 `docker/my-quant-lab`。
2. 把两个文件拖进去。
3. `.env` 以点开头，文件管理器默认可能不显示隐藏文件，上传后在「显示隐藏文件」
   里确认一下：目录里必须同时有 `docker-compose.yml` 和 `.env`。

> ⚠️ 这两个文件必须在**同一个目录**里。`.env` 缺失时 Docker 会在挂载点自动建一个
> 同名目录，后端就读不到配置，症状是容器反复重启并报 `SECRET_KEY` 相关错误。

---

## 3. 在 Docker 界面里创建项目

1. 打开 fnOS「Docker」应用 → 左侧「Compose」→「新增项目」。
2. 项目名称：`my-quant-lab`
3. 路径：选择第 2 步的 `/vol1/docker/my-quant-lab`
4. 来源选择「使用现有 docker-compose.yml」（不要粘贴内容，也不要选模板）。
5. 点「确定」。fnOS 会自动执行 `docker compose up -d` 并拉取镜像。

镜像合计约 1.5 GB，首次拉取视网络情况需要 5～20 分钟。

### 如果镜像拉不下来

fnOS 的 Compose 界面拉取第三方仓库（GHCR）有已知问题，按顺序试：

1. **确认代理**：你的路由器已经做了全局透明代理，先直接重试一次；容器通常已经被覆盖。
2. **给 Docker 配代理**：Docker 应用 →「设置」→「代理」，填入路由器的 HTTP 代理端口
   （`192.168.1.1:7890` 之类），保存后重新部署。
3. **只用 7890 / 7891 / 7893 这三个端口**，不要用 7892（redir）和 7895（tproxy），
   这两个只有路由器自己能用。
4. 还是不行就在电脑上 `docker pull` 后导出镜像，再从 fnOS 的「镜像」页导入。

---

## 4. 验证启动

Compose 页面里应该能看到 10 个容器：

```
mql-db  mql-redis  mql-redis-jobs  mql-migration  mql-api
mql-trading  mql-scheduler  mql-celery  mql-beat  mql-web
```

- `mql-migration` 跑完就退出，状态显示 `exited (0)` 是**正常**的。
- 其余 9 个应该是「运行中」。
- 首次启动大约 1 分钟，`mql-api` 转「运行中」后再打开网页。

浏览器访问 `http://<NAS 的 IP>:8888`，用 `.env` 里的 `ADMIN_USER` /
`ADMIN_PASSWORD` 登录，登录后立刻在设置里改密码。

---

## 5. 首次配置

登录后按这个顺序做，不要跳步：

1. **改密码**：右上角头像 → 账号设置。
2. **Telegram 通知**：设置 → 通知 → Telegram，填 Bot Token 和 Chat ID，
   点「发送测试消息」确认能收到。
3. **建模拟盘**：交易 → 模拟账户 → 新建，初始资金填 `10000`，币种 `USD`。
   这一步决定了后面所有信号记到哪个账本上。
4. **确认不接实盘**：不要把交易所密钥配成「实盘」权限。只读行情不需要密钥；
   需要余额时才加，权限选「读取」。
5. **加策略**：策略 → 新建 → 选模板或写代码 → 回测 → 启动。
   启动后信号会按策略周期产生，并推送到 Telegram。

---

## 6. 日常维护

| 想做的事 | 做法 |
| --- | --- |
| 升级 | 改 `.env` 里的 `BACKEND_TAG` / `FRONTEND_TAG`，Compose 页面点「重新部署」 |
| 看日志 | Compose 项目 → 容器 →「日志」 |
| 备份数据库 | 见下方命令，或用 fnOS 的容器终端 |
| 改配置 | 改 `.env` 后必须「重新部署」，只重启容器不生效 |

备份数据库（在电脑上执行，需要能和 NAS 通网）：

```bash
docker run --rm -e PGPASSWORD='你的POSTGRES_PASSWORD' postgres:18.3-alpine \
  pg_dump -h <NAS的IP> -U quantdinger quantdinger > backup-$(date +%F).sql
```

前提是把 `DB_PORT` 临时映射出来；更省事的办法是直接用 fnOS 容器终端：

```bash
pg_dump -U quantdinger quantdinger > /tmp/backup.sql
```

---

## 7. 常见故障

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| `mql-api` 反复重启，日志提 `SECRET_KEY` | `.env` 里 `SECRET_KEY` 是空值或太短 | 重新生成，≥ 10 字符 |
| `mql-api` 日志里 `password authentication failed` | `POSTGRES_PASSWORD` 在数据库建好之后被改过 | 改回原值；或删卷重建（会丢数据） |
| `mql-migration` 一直 `exited (1)` | 数据库还没起来，或 `.env` 不是文件 | 先看 `mql-db` 是否 healthy |
| 网页 502 | `mql-api` 没起来 | 先修后端，再刷网页 |
| 网页能开但登录报错 | `ADMIN_*` 只在首次建库时生效 | 用旧密码登录，或直接在库里改 |
| 8888 打不开但从 NAS 本机能开 | 端口绑定成了 `127.0.0.1` | `.env` 里 `FRONTEND_HOST=0.0.0.0` |
| 策略不产生信号 | 策略没启动 / 周期没到 / 模拟账户没建 | 策略页看状态，账户页看账本 |

排查时优先看这三个容器的日志顺序：`mql-db` → `mql-migration` → `mql-api`。
