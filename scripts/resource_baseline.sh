#!/usr/bin/env bash
# My Quant Lab — 实际资源基准测试
#
# 运行位置：NAS 主机（不是容器内），需要能执行 docker 与 curl。
#
#   cd /vol1/1000/Docker/My-Quant-Lab
#   sh scripts/resource_baseline.sh            # 完整测量（约 18 分钟）
#   sh scripts/resource_baseline.sh --quick    # 快速版（约 4 分钟，空闲采样 2 分钟）
#
# 产出：./resource-baseline-<日期>.md —— 把该文件内容贴回给我即可。
#
# 设计说明：
#   * 空闲期按你要求采样 10~15 分钟（默认 12 分钟，每分钟一次）
#   * 三类负载分别触发并采样：行情同步 / 信号扫描 / 回测
#   * 每类负载同时记录 CPU、RAM、网络、以及各容器内的 Python 进程数
#     （进程数比容器数更能解释内存占用）
set -eu

QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

IDLE_MINUTES=$([ "$QUICK" -eq 1 ] && echo 2 || echo 12)
LOAD_SECONDS=40
LOAD_INTERVAL=2

API="http://127.0.0.1:8080/api/v1"
OUT="resource-baseline-$(date +%Y%m%d-%H%M).md"

# quantlab-docker-proxy is a default service of docker-compose.yml (it is what lets the
# API read container stats), so the baseline has to include it: sampling grepped every
# `^quantlab-` container while the report only ever printed the list below (ADR-090).
CONTAINERS="quantlab-api quantlab-worker quantlab-scheduler quantlab-web quantlab-postgres quantlab-redis quantlab-docker-proxy"

command -v docker >/dev/null 2>&1 || { echo "docker 不可用，请在 NAS 主机上运行本脚本"; exit 1; }

say() { printf '%s\n' "$*"; }
emit() { printf '%s\n' "$*" >> "$OUT"; }

# ---------------------------------------------------------------- 工具函数
# 采样一次：容器名 / CPU% / 内存用量 / 内存% / 网络IO / 进程数
sample_once() {
    docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}|{{.NetIO}}' 2>/dev/null \
    | grep -E '^quantlab-' || true
}

# 容器内 python 进程数（最能解释内存）
proc_counts() {
    for c in $CONTAINERS; do
        n=$(docker exec "$c" sh -c 'ps -ef 2>/dev/null | grep -c "[p]ython"' 2>/dev/null || echo "n/a")
        printf '%s=%s\n' "$c" "$n"
    done | tr '\n' ' '
}

# 把多次采样聚合成 min/avg/max（针对 CPU% 与 内存 MiB）
# 输入文件：$1 = 采样文件（每行 name|cpu|mem|mempct|net）
aggregate() {
    file="$1"
    awk -F'|' '
    function to_mb(text,   v) {
        # 只把「已用量」那一段转成 MB；单位判断必须针对这一段，
        # 否则 "185MiB / 7.6GiB" 里的 GiB 会把数值放大 1024 倍。
        v = text
        if (v ~ /GiB/) { gsub(/[A-Za-z]/, "", v); return v * 1024 }
        if (v ~ /KiB/) { gsub(/[A-Za-z]/, "", v); return v / 1024 }
        if (v ~ /MiB/) { gsub(/[A-Za-z]/, "", v); return v + 0 }
        gsub(/[A-Za-z]/, "", v); return v + 0
    }
    {
        name = $1
        cpu = $2; gsub(/%/, "", cpu); cpu += 0
        used = $3; sub(/ \/ .*/, "", used); used = to_mb(used)
        n[name]++
        csum[name] += cpu; if (n[name] == 1 || cpu > cmax[name]) cmax[name] = cpu
        msum[name] += used; if (n[name] == 1 || used > mmax[name]) mmax[name] = used
    }
    END {
        printf "| 容器 | CPU 平均 | CPU 峰值 | 内存 平均 | 内存 峰值 | 采样 |\n"
        printf "|---|---|---|---|---|---|\n"
        for (name in n)
            printf "| %s | %.2f%% | %.2f%% | %.1f MB | %.1f MB | %d |\n", name, csum[name]/n[name], cmax[name], msum[name]/n[name], mmax[name], n[name]
    }' "$file"
}

# ---------------------------------------------------------------- 报告头
{
    say "My Quant Lab 资源基准测试"
    say "时间：$(date '+%Y-%m-%d %H:%M:%S %Z')"
    say "模式：$([ "$QUICK" -eq 1 ] && echo 快速 || echo 完整)（空闲采样 ${IDLE_MINUTES} 分钟）"
    echo
} | tee /dev/stderr >/dev/null

emit "# Quant Lab Resource Baseline"
emit ""
emit "- 采集时间：$(date '+%Y-%m-%d %H:%M:%S %Z')"
emit "- 模式：$([ "$QUICK" -eq 1 ] && echo "quick（空闲 ${IDLE_MINUTES} 分钟）" || echo "full（空闲 ${IDLE_MINUTES} 分钟）")"
emit "- 主机：$(uname -srm) / $(nproc) vCPU"
if [ -r /proc/meminfo ]; then
    total=$(awk '/MemTotal/{printf "%.1f", $2/1024}' /proc/meminfo)
    avail=$(awk '/MemAvailable/{printf "%.1f", $2/1024}' /proc/meminfo)
    emit "- 主机内存：总计 ${total} MB，当前可用 ${avail} MB"
fi
emit ""

say "== 1/4 拓扑与镜像体积 =="
emit "## 1. 拓扑与镜像体积"
emit ""
emit '```'
emit "$(docker ps --filter 'name=quantlab-' --format '{{.Names}}\t{{.Image}}\t{{.Status}}' 2>&1)"
emit ''
emit "镜像："
emit "$(docker images --format '{{.Repository}}:{{.Tag}}\t{{.Size}}' 2>&1 | grep -i quantlab || true)"
emit ''
emit "各容器 Python 进程数：$(proc_counts)"
emit '```'
emit ""

say "== 2/4 空闲采样（${IDLE_MINUTES} 分钟）=="
IDLE_FILE="$(mktemp)"
i=0
while [ "$i" -lt "$IDLE_MINUTES" ]; do
    i=$((i + 1))
    sample_once >> "$IDLE_FILE"
    say "  空闲采样 $i/${IDLE_MINUTES}"
    [ "$i" -lt "$IDLE_MINUTES" ] && sleep 60
done

emit "## 2. 空闲状态（${IDLE_MINUTES} 分钟，每分钟一次）"
emit ""
emit "$(aggregate "$IDLE_FILE")"
emit ""
emit "空闲期各容器 Python 进程数：\`$(proc_counts)\`"
emit ""

# ---------------------------------------------------------------- 负载
discover_ids() {
    STRAT_ID=$(curl -fsS "$API/strategies" 2>/dev/null | sed -n 's/.*"id":\([0-9]*\).*/\1/p' | head -n1)
    VERSION_ID=""
    if [ -n "${STRAT_ID:-}" ]; then
        VERSION_ID=$(curl -fsS "$API/strategies/$STRAT_ID/versions" 2>/dev/null | sed -n 's/.*"id":\([0-9]*\).*/\1/p' | head -n1)
    fi
    SYMBOL=$(curl -fsS "$API/assets" 2>/dev/null | sed -n 's/.*"symbol":"\([^"]*\)".*/\1/p' | head -n1)
    [ -n "${SYMBOL:-}" ] || SYMBOL="DEMO-AAPL"
}

run_load() {
    label="$1"; trigger="$2"; outfile="$3"
    say "  触发负载：$label"
    start=$(date +%s)
    eval "$trigger" >/tmp/mql-load-$$.out 2>&1 || true
    # 负载后立即开始采样
    j=0
    while [ "$j" -lt $((LOAD_SECONDS / LOAD_INTERVAL)) ]; do
        sample_once >> "$outfile"
        j=$((j + 1))
        sleep "$LOAD_INTERVAL"
    done
    elapsed=$(( $(date +%s) - start ))
    say "    $label 完成，耗时 ${elapsed}s"
    emit ""
    emit "耗时：${elapsed}s（触发）"
}

discover_ids

echo
say "== 3/4 负载测试 =="
say "  策略版本 id=${VERSION_ID:-无} 标的=${SYMBOL}"

SYNC_FILE="$(mktemp)"; SCAN_FILE="$(mktemp)"; BT_FILE="$(mktemp)"

emit "## 3. 负载测试"
emit ""
emit "使用：策略版本 \`${VERSION_ID:-n/a}\`、标的 \`${SYMBOL}\`"
emit ""
emit "### 3.1 行情同步"
emit ""
run_load "行情同步" \
    "curl -fsS -X POST '$API/market-data/sync' -H 'Content-Type: application/json' -d '{\"symbol\":\"$SYMBOL\",\"timeframe\":\"1d\",\"lookback_days\":400}'" \
    "$SYNC_FILE"
emit "$(aggregate "$SYNC_FILE")"
emit ""

emit "### 3.2 信号扫描"
emit ""
run_load "信号扫描" "curl -fsS -X POST '$API/signals/scan'" "$SCAN_FILE"
emit "$(aggregate "$SCAN_FILE")"
emit ""

if [ -n "${VERSION_ID:-}" ]; then
    emit "### 3.3 回测"
    emit ""
    say "  注意：观察 API 在回测期间是否仍能响应"
    run_load "回测" \
        "curl -fsS -X POST '$API/backtests' -H 'Content-Type: application/json' -d '{\"strategy_version_id\":$VERSION_ID,\"symbol\":\"$SYMBOL\",\"timeframe\":\"1d\"}'" \
        "$BT_FILE"
    emit "$(aggregate "$BT_FILE")"
    # 回测期间 API 响应性抽查（这是"Web 是否失去响应"的直接证据）
    emit ""
    emit "回测期间 API 响应性抽查："
    emit '```'
    for k in 1 2 3; do
        t0=$(date +%s%N 2>/dev/null || date +%s)
        if curl -fsS -m 5 "$API/healthz" >/dev/null 2>&1; then
            t1=$(date +%s%N 2>/dev/null || date +%s)
            emit "  第 $k 次 /healthz 成功，耗时 $(( (t1 - t0) / 1000000 )) ms"
        else
            emit "  第 $k 次 /healthz 失败或超时（>5s）"
        fi
    done
    emit '```'
    emit ""
else
    emit "### 3.3 回测"
    emit ""
    emit "跳过：没有可用的策略版本。请先在 Web 界面创建策略后再运行本脚本。"
    emit ""
fi

# ---------------------------------------------------------------- 日志健康
say "== 4/4 日志与稳定性 =="
emit "## 4. 日志与稳定性"
emit ""
emit "### 各容器最近日志中的 ERROR/WARNING 计数（最近 200 行）"
emit '```'
for c in $CONTAINERS; do
    n=$(docker logs --tail 200 "$c" 2>&1 | grep -cE 'ERROR|CRITICAL' || true)
    w=$(docker logs --tail 200 "$c" 2>&1 | grep -cE 'WARN' || true)
    emit "$c: ERROR=$n WARN=$w"
done
emit '```'
emit ""
emit "### 重启次数（长期稳定性）"
emit '```'
for c in $CONTAINERS; do
    emit "$c: $(docker inspect -f '{{.RestartCount}}' "$c" 2>/dev/null || echo n/a)"
done
emit '```'
emit ""

rm -f "$IDLE_FILE" "$SYNC_FILE" "$SCAN_FILE" "$BT_FILE" /tmp/mql-load-$$.out 2>/dev/null || true

say ""
say "完成。报告已写入：$OUT"
say "请把 $OUT 的完整内容贴回给我。"
