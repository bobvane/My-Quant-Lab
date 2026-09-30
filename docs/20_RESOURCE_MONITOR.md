# 20 — System Resource Monitor（系统资源监控）规格

> 状态：**已纳入计划，待确认方案后实施**（P1.5）
> 原则：**监控系统自身不能成为新的资源负担**

## 1. 需求定位

NAS 上长期运行多个服务（Hermes Agent、Ghostfolio、FreeLLMAPI、OmniRoute、
Tailscale、订阅聚合、Quant Lab、PostgreSQL/Redis 等）。真正要回答的问题不是
"Quant Lab 有几个容器"，而是：

* 整台 NAS 的整体资源使用情况；
* Quant Lab 在整台 NAS 资源消耗中所占的比例；
* 架构精简（6→4→2 容器）是否值得做——**用实测数据判断，而不是理论估算**。

明确**不做**的东西：Prometheus、Grafana、cAdvisor、InfluxDB、Elasticsearch、
独立监控平台容器、高频轮询服务。

## 2. 采集分层与权限边界（核心设计约束）

| 层 | 覆盖内容 | 需要的权限 | 风险 |
|---|---|---|---|
| **层 1：无 Docker 权限** | NAS 整体 CPU/RAM/Swap/磁盘；Quant Lab API 与 Worker 自身（各自 cgroup）；Redis（`INFO memory`，已有 redis-py） | 无。容器内 `/proc/stat`、`/proc/meminfo`、`/proc/loadavg` 默认反映宿主机；自身资源读 `/sys/fs/cgroup` | 无新增权限 |
| **层 2：受限 Docker API** | 全部容器一览（含 Hermes/Ghostfolio 等其他服务）的 CPU/RAM/状态 | 经**只读过滤代理**访问 Docker socket：仅放行 `GET /containers/json` 与 `GET /containers/{id}/stats?stream=false&one-shot=true`，拒绝其余一切方法与路径 | 代理容器本身拿到的是被过滤的 API；不挂完整 socket |
| **层 3：拒绝** | 容器管理、exec、build、日志流、镜像操作 | — | 一律拒绝并记录 |

**直接挂载 `/var/run/docker.sock`（等价宿主机 root）的做法被明确否决。**

层 2 的实现是本项目自写的**最小只读代理**：Python 标准库（`http.server` + Unix
socket），无常驻框架依赖，预计常驻内存 ~20MB。它是**一个**约 20MB 的微型容器，
不属于被禁止的"独立监控平台"。

> 层 1 单独即可回答验收问题 1/2/3/6/7/8/9 的大部分；
> 问题 4（NAS 上哪个服务最吃 RAM）与问题 5 的完整形态需要层 2。

## 3. 指标与口径（写入代码与文档，避免歧义）

* `NAS CPU%`：宿主机 CPU 忙碌占比（1 - idle / total，全核心合计）。
* `NAS RAM%` = used / total；`used` = total − available（与 `free` 口径一致）。
* `Disk%` = statvfs(f_frsize*blocks − f_bavail*f_frsize) / (f_frsize*blocks)，
  对可配置路径集合采样（默认容器内 `/app`，即宿主机系统盘）。
* `Quant Lab CPU 合计` = 各 Quant Lab 容器 CPU% 之和（同刻采样后相加）。
* `Quant Lab RAM 合计` = 各 Quant Lab 容器 mem used 之和。
* **Quant Lab RAM Share（占已用内存）** = Quant Lab RAM 合计 / NAS used RAM。
* **Quant Lab RAM Share（占总内存）** = Quant Lab RAM 合计 / NAS total RAM。
* **Quant Lab CPU Share（占当前 CPU 使用）** = Quant Lab CPU 合计 / max(NAS CPU%, 1)。
* 容器识别：按 Docker label `com.docker.compose.project=my-quant-lab` 自动发现，
  **不硬编码** api/worker/scheduler/web 等名字（ADR-024）。未来 6→4→3 拓扑变化不影响。

## 4. 数据模型（迁移 0004）

```sql
-- NAS 级采样
CREATE TABLE host_resource_samples (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    cpu_percent REAL, cpu_count SMALLINT,
    mem_used_mb REAL, mem_total_mb REAL,
    swap_used_mb REAL, swap_total_mb REAL,
    disk_used_gb REAL, disk_total_gb REAL
);
CREATE INDEX ix_host_res_samples_ts ON host_resource_samples (ts DESC);

-- 容器级采样（含 Quant Lab 与 NAS 上其他服务）
CREATE TABLE container_resource_samples (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    container_name VARCHAR(128) NOT NULL,
    compose_service VARCHAR(96),
    is_quantlab BOOLEAN NOT NULL,
    cpu_percent REAL,
    mem_used_mb REAL, mem_limit_mb REAL,
    state VARCHAR(32)
);
CREATE INDEX ix_cont_res_samples_ts ON container_resource_samples (ts DESC);
CREATE INDEX ix_cont_res_samples_name_ts ON container_resource_samples (container_name, ts DESC);

-- 聚合（5 分钟 / 小时 / 日）
CREATE TABLE resource_rollups (
    id BIGSERIAL PRIMARY KEY,
    granularity VARCHAR(8) NOT NULL,          -- '5m' | '1h' | '1d'
    bucket_start TIMESTAMPTZ NOT NULL,
    scope VARCHAR(24) NOT NULL,               -- 'host' | 'quantlab' | 容器名
    cpu_avg REAL, cpu_max REAL,
    mem_avg_mb REAL, mem_max_mb REAL
);
CREATE UNIQUE INDEX uq_resource_rollup ON resource_rollups
  (granularity, bucket_start, scope);

-- 任务资源事件（回测开始/结束等）
CREATE TABLE resource_events (
    id BIGSERIAL PRIMARY KEY,
    event_key VARCHAR(128) NOT NULL,          -- 如 backtest:123（幂等）
    event_type VARCHAR(48) NOT NULL,          -- backtest_started / backtest_completed ...
    started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    cpu_peak_percent REAL, mem_peak_mb REAL,
    payload_json JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX uq_resource_events_key ON resource_events (event_key);
```

### 保留策略（可配置）

| 数据 | 范围 | 默认 |
|---|---|---|
| 原始采样（1 分钟） | 最近 | `RESOURCE_RETENTION_RAW_DAYS=7` |
| 5 分钟聚合 | 8–30 天 | `RESOURCE_RETENTION_ROLLUP_DAYS=30` |
| 超过 30 天 | 删除 | 每日 Celery 任务清理 |

量级估算（10 个容器、60 秒粒度）：host 1,440 行/天 + 容器 14,400 行/天 ≈
**11 万行/周**（数 MB），聚合后 30 天约数万行。PostgreSQL 完全无压力，
不引入分区/时序扩展。

## 5. 采集与任务

* 采集任务 `quantlab.collect_resources`：Celery beat 每 60 秒一次
  （`crontab(minute="*")`），跑在现有 worker 内，**无新常驻进程**。
* 清理任务 `quantlab.purge_resources`：每日 03:17 UTC。
* 单次采集开销目标：<100ms CPU；worker 本就常驻，增量≈0。
* 前端**不做高频轮询**：读取最近一次采样；页面刷新间隔 30–60 秒。

## 6. API（`/api/v1/resources`）

| 端点 | 作用 |
|---|---|
| `GET /resources/current` | 最近一次采样：NAS 概览、Quant Lab 合计、两种占比、逐容器明细、最吃 RAM 的 NAS 服务与 Quant Lab 服务 |
| `GET /resources/history?metric=cpu\|ram&range=1h\|24h\|7d\|30d` | 时间序列（1h/24h 用原始采样，7d/30d 用聚合） |
| `GET /resources/events?limit=` | 资源事件（回测等的峰值与耗时） |
| `GET /resources/summary` | 直接回答验收问题 1–9 的结构化结果 |

## 7. 前端

新增导航项「系统资源」：

* NAS 卡片：CPU%、核心数、RAM used/total 与 %、Swap、磁盘 used/total/%
* Quant Lab 卡片：CPU 合计、RAM 合计、容器数与状态、两种占比
* Docker Overview 表：全部容器（Quant Lab 分组置顶，其他服务随后）
* 历史图表（ECharts，已有依赖）：CPU 与 RAM × 1h/24h/7d/30d；
  Quant Lab 各容器 RAM 单独一组
* 资源事件列表：任务、耗时、CPU/RAM 峰值
* 轮询间隔 60 秒，页面隐藏时暂停

## 8. 回测资源事件（最小侵入）

在 `create_backtest` 完成处插入两个动作（不改任务系统）：

1. 开始时写 `resource_events`（event_key=`backtest:{id}`，status=started）；
2. 完成时更新：从 `resource_samples` 取窗口内 CPU/RAM 峰值 + duration。

`payload_json` 记录 strategy_version / dataset_hash / trade_count，便于关联。

## 9. 测试计划

* 单元：占比计算（含除零/空样本）、聚合边界、retention 清理、
  容器 label 自动发现（fake Docker 响应）、代理白名单（非 GET / 越权路径拒绝）
* 集成：采集→查询回路；回测事件峰值正确落在采样窗口内；幂等（重复 event_key 不重复插入）
* 契约：`/resources/*` 响应形状；密钥/敏感信息不出现在任何响应
* 性能：采集任务单次耗时断言 <500ms；保留策略执行后行数符合预期

## 10. 验收标准对照

| # | 问题 | 覆盖 |
|---|---|---|
| 1 | NAS 整体 CPU/RAM | 层 1 |
| 2 | Quant Lab 用了多少 | 层 1 |
| 3 | Quant Lab 占 NAS 多少 | 层 1（两种口径） |
| 4 | NAS 上哪个服务最吃 RAM | 层 2 |
| 5 | Quant Lab 内哪个 service 最吃 RAM | 层 1（api/worker 自报）+ 层 2（pg/redis/web） |
| 6 | 空闲 24h 平均 | 层 1 |
| 7 | 行情同步峰值 | 层 1（worker 自报） |
| 8 | 回测峰值 | 层 1 |
| 9 | 7 天内存增长趋势 | 层 1 |

## 11. 实施顺序

* **Phase 1a（先做，无 Docker 权限）**：迁移 0004、psutil 采集、Celery 任务、
  保留策略、API、回测事件、前端页面、测试、文档（ADR-024）
* **Phase 1b（需你确认层 2）**：只读过滤代理微容器 + 全容器 Docker Overview
