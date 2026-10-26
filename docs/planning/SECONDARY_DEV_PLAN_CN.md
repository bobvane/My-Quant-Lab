# QuantDinger 二次开发计划 v4.2

> 状态：待你说"开始"
> 更新日期：2026-10-26
> 基线：QuantDinger 5.6.1（Apache 2.0）
> 环境：飞牛 fnOS，Docker 图形界面，**无 SSH**
> 定位：个人局域网使用，只做信号提示，不自动下单

---

## 0. 分工

你是**体验用户 + 部署者**，我是**技术负责人**。技术判断不需要你参与，你只需要说"好用 / 不好用 / 报什么错"。

| 事项 | 你 | 我 |
|---|---|---|
| 代码、配置、文档 | — | ✅ |
| GitHub 仓库、镜像构建 | 建仓库、点一次授权 | ✅ |
| 飞牛 GUI 上操作 | 按步骤点几下、复制粘贴 | 给出点哪里、复制什么 |
| 发现问题 | ✅ 主要工作 | 定位并修复 |

**我做不到的**：直接连你的 NAS。所以需要你在飞牛界面上点几下，把截图或报错发我。

---

## 1. 交付流水线

```
我改代码（这台电脑）
   → git push 到 GitHub（公开仓库）
   → GitHub Actions 自动校验 + 构建镜像（仅 x86_64，约 10–15 分钟）
   → 推到 GHCR（GitHub 镜像仓库，公开仓库的镜像免登录可拉）
   → 你在飞牛 GUI 里点"更新 / 重新部署"
```

**关键收益**：飞牛 NAS 不编译、不装环境、不需要命令行。

### 版本规则

```
v5.6.1-nas.1  →  .2  →  .3 ...
```

升级 = 我把 compose 里的版本号改掉 → 你在飞牛里点一次"重新部署"。
回滚 = 我把版本号改回上一个 → 你再点一次。

### CI 会顺带帮你把关

GitHub Actions 每次都会跑：

- `docker compose config -q`（compose 语法错误直接拦下）
- 后端契约测试（防止删 `mcp_server` 时改坏东西）

> 为什么重要：这台电脑没装 Docker，我无法本地验证 compose。所以把校验放在 GitHub 上，**你拉到的镜像一定是校验过的**。

---

## 2. 飞牛 GUI 部署步骤（你只需要照做）

### 2.1 首次部署

| 步 | 在飞牛上做什么 |
|---|---|
| 1 | 「文件管理」→ 在 docker 目录下新建文件夹 `quantdinger` |
| 2 | 我把 `docker-compose.yml` 和 `.env` 发你 → 上传到这个文件夹 |
| 3 | 「Docker」→「Compose」→「新增项目」 |
| 4 | 项目名 `quantdinger`，路径选刚建的文件夹 |
| 5 | 它会提示导入 `docker-compose.yml` → 选"是" |
| 6 | 点「构建 / 部署」，等 10 个容器变绿 |
| 7 | 浏览器打开 `http://NAS的IP:8888` |

> 说明：`.env` 放在 `docker-compose.yml` 同一目录，Compose 会自动读取。**不需要命令行。**

### 2.2 以后升级

| 步 | 做什么 |
|---|---|
| 1 | 我发你新的 `docker-compose.yml`（只改了一行版本号） |
| 2 | 你替换文件 |
| 3 | 「Compose」→ 项目右侧「...」→ 重新部署 |

---

## 3. 出问题怎么查（无 SSH 也够用）

飞牛的 Docker 界面自带**容器终端**和**日志**，足够排查：

| 现象 | 看哪里 |
|---|---|
| 某个容器不断重启 | 容器 → 右侧「...」→「运行日志」 |
| 数据拉不到 | 容器 `quantdinger-backend` →「终端」→ 粘贴我给的 curl 命令 |
| 页面打不开 | 容器 `quantdinger-frontend` 是否绿；端口是否被占用 |
| 代理是否生效 | 在容器终端里跑 `curl -I https://www.okx.com` |

你把**日志或终端输出截图**发我即可，不需要理解它。

---

## 4. 部署形态：18 → 10 个容器

### 保留（10）

`postgres`、`redis`、`redis-jobs`、`migration`、`backend`、`trading-worker`、`scheduler-worker`、`celery-worker`、`celery-beat`、`frontend`

### 删除（8）

`kafka`、`kafka-init`、`kafka-audit-worker`、`strategy-dispatcher-worker`、`strategy-evaluator-worker`、`mcp`、`mobile`、`env-sync`

### 关键开关

```bash
KAFKA_EVENT_PUBLISH_ENABLED=false
STRATEGY_DISTRIBUTED_BAR_ENABLED=false
GRID_ACTOR_ROUTING_ENABLED=false
```

### 为什么不再往下合并

| 合并 | 省内存 | 代价 |
|---|---|---|
| beat 并入 scheduler-worker | 80–120MB | 要写进程守护，一个进程挂了看不出来 |
| migration 并入 backend | 0 | 失去"迁移完成才启动"保护 |
| 两个 Redis 合并 | 60–80MB | 队列和缓存淘汰策略冲突，可能丢任务 |
| backend + celery-worker | 150–200MB | 出问题时更难定位 |

16GB 内存下 10 个容器不是瓶颈，少这 1–2 个换来的麻烦不值得。

---

## 5. 网络与代理

路由器全局透明代理，端口：Mixed 7893 / HTTP 7890 / SOCKS 7891 / Redir 7892 / TProxy 7895。

### 5.1 先验证

```bash
# 飞牛容器终端里执行（docker → 容器 backend → 终端）
curl -sS -o /dev/null -w '%{http_code}\n' https://www.okx.com/api/v5/public/time
curl -sS -o /dev/null -w '%{http_code}\n' https://query1.finance.yahoo.com/v8/finance/chart/AAPL
```

| 结果 | 结论 | 动作 |
|---|---|---|
| 都是 200 | 透明代理已覆盖容器 | **不用配代理** |
| 超时 / 000 | 容器没被覆盖 | 配 `PROXY_URL` |

### 5.2 需要配时

只有 **7890 / 7891 / 7893** 能当客户端代理。**7892、7895 是路由器内核专用端口，填进去必然失败。**

```bash
PROXY_URL=http://192.168.x.1:7890
LLM_PROXY_URL=http://192.168.x.1:7890
```

填路由器内网 IP，不要填 `127.0.0.1`。

---

## 6. 物理删除清单

⚠️ 仓库目前没有 `.git`。我会先 `git init` 并提交一次基线，**之后再删都可恢复**。

### 6.1 零连带

`docker-compose.ghcr.yml`、`install.sh`、`install.ps1`、`docker-compose.production.yml`、`docker-compose.observability.yml`、`docker-compose.build.yml`、`ops/`、`backend_api_python/railway.json`
连带：`.github/workflows/basic-ci.yml` 里 4 个校验步骤。

### 6.2 `mcp_server/`（已确认删除）

删除目录 + `mcp-ci.yml` + `dependabot` 两个块 + 契约测试中的 MCP 用例 + `AGENTS.md` 4 处描述 + 过时文档。
`/api/agent/v1`（Agent Gateway）保留。

---

## 7. 实施步骤

| 步骤 | 谁 | 内容 |
|---|---|---|
| S1 | 我 | 备份 + `git init` + 提交 5.6.1 基线 |
| S2 | 我 | 改 CI：镜像名改成你的、只构建 amd64、加 compose 校验、构建前复制 `LICENSE`、加 OCI 标签 |
| S2b | 我 | 新增 `MODIFICATIONS.md`（列出全部改动）+ 改 README 首行"非官方分支"声明 |
| S3 | **你** | 建 GitHub 空仓库，地址发我（**仓库名不要用 QuantDinger**，例如 `qd-nas`） |
| S4 | 我 | 推送，触发首次构建，确认 GHCR 上有镜像 |
| S5 | 我 | 改造 `docker-compose.yml`（删 8 个服务、去掉 build 段、指向 GHCR 镜像） |
| S6 | 我 | 写 `.env` 模板与飞牛部署文档 |
| S7 | **你** | 飞牛 GUI 里上传两个文件、点部署 |
| S8 | **你** | 把容器状态截图 + 三条 curl 结果发我 |
| S9 | 我 | 按结果修配置，直到数据能拉到 |
| S10 | 我 | 删 §6 文件 + 修连带项 + CI 通过 |
| S11 | **你** | 用一周，把"不好用"的地方告诉我 |

---

## 8. 验收（用你的语言）

| 项 | 标准 |
|---|---|
| 打开网页 | `http://NAS的IP:8888` 能登录 |
| 容器 | 不超过 10 个在跑，没有反复重启的 |
| 数据 | 美股、加密都能看到行情和回测结果 |
| 通知 | 手机 Telegram 收到买卖提示 |
| 重启 | 飞牛重启后自动恢复 |
| 升级 | 我给你一个新 compose，你点一次"重新部署" |

---

## 9. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 飞牛 Docker GUI 拉取第三方镜像有已知 Bug | 拉不到 GHCR 镜像 | 先试 GHCR；失败则我把镜像改推到 Docker Hub（公开免登录） |
| 本机没装 Docker，我无法本地验证 compose | 可能推错配置 | CI 里加 `docker compose config -q` 强制校验 |
| 删除不可恢复 | 删错文件 | 先 `git init` 提交基线，全部可回溯 |
| 密钥泄露到公开仓库 | 严重 | `.env` 已在 `.gitignore`，推送前我会再检查一遍 |
| Actions 分钟数 | 构建排队 | 仅构建 amd64；公开仓库的 Actions 免费 |
| 无 SSH，排障靠截图 | 来回慢 | 用飞牛容器终端；必要时我写"一键自检"页面 |

### 9.1 法律与许可（已核对上游原文）

| 对象 | 许可 | 我们能不能动 |
|---|---|---|
| **后端代码**（本仓库） | Apache 2.0 | ✅ 可改、可打包、可公开，但必须遵守下面 4 条 |
| **Web 前端源码**（QuantDinger-Vue） | 独立的"源码可见"许可，**仅限非商业** | ⚠️ 不 fork、不转发、不改水印；只拉官方镜像做个人非商业使用 |
| **移动端源码** | 同上 | ⚠️ 我们本来就不用 |
| **名称 / Logo / 品牌** | Apache 2.0 **不授予商标权**，另受 TRADEMARKS.md 约束 | ❌ 不能用它当我们的产品名，不能暗示官方 |

#### 公开仓库必须做到的 4 条（Apache 2.0 第 4 条）

1. **保留 `LICENSE`**，且分发镜像里也要带一份。
   - 已核实：后端镜像实际**不含** LICENSE（`COPY . .` 只复制 `backend_api_python/`）。
   - 解决：CI 构建前把根目录 `LICENSE` 复制进构建上下文（`LICENSE` 无扩展名，不会被 `*.md` 忽略规则挡掉）。
2. **声明改过哪些地方**：新增 `MODIFICATIONS.md`，逐条列出"删了 mcp_server、删了 8 个容器、改了 compose、改了 CI"。
3. **不能叫 QuantDinger**：公开仓库改成中性名（如 `qd-nas`），README 首行写明
   > 非官方个人分支，与 Open Byte Inc. 无关联、未获其背书。
4. **镜像加 OCI 标签**：`org.opencontainers.image.licenses=Apache-2.0`、`org.opencontainers.image.source=<仓库地址>`，让别人一眼能追到来源。

#### 三条禁止事项

- ❌ 不要 fork 前端、不要去掉或修改界面里的 QuantDinger 品牌与水印（那是另一套许可 + 商标条款）。
- ❌ 不要用它的 Logo 当自己项目的图标。
- ❌ 不要在公开仓库里暗示"官方认证 / 合作 / 授权"。

> 本仓库没有 `NOTICE` 文件（已核实），所以不需要额外携带；`LICENSES/Apache-2.0.txt` 与 `REUSE.toml` 保留即可。
> 我们是**修改版公开分发**，所以上面 4 条都属于"必须"，不是"建议"。

---

## 10. 待确认（只剩仓库地址）

1. 你把 GitHub 仓库地址发我（**公开**、**空仓库**、不要勾初始化文件、**名字不要用 QuantDinger**，例如 `qd-nas`）。
2. 说一声"开始"，我就从 S1 做起。

---

## 11. 讨论历程

- v0.1–v0.3：目标确认、能力核实
- v1.0–v1.1：文章→策略流水线（**作废**）
- v2.0–v3.0：精简 + 单一 NAS 部署
- v3.1–v3.3：容器合并、代理端口、删除清单
- v4.0：确认"我不会开发，项目以你为主"；引入 GitHub 构建
- v4.1：确认公开仓库、飞牛 GUI 无 SSH；补 GUI 部署步骤与排障方式
- **v4.2：核对上游许可原文（Apache 2.0 + 前端源码可见许可 + 商标政策）；补公开分发的 4 条必须动作与 3 条禁止事项**
