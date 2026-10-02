<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '@/api'
import MultiLineChart from '@/components/MultiLineChart.vue'
import { formatDateTime, formatNumber } from '@/format'

type Point = { ts: string; value: number | null }

const loading = ref(true)
const error = ref('')
const current = ref<Record<string, any> | null>(null)
const summary = ref<Record<string, any> | null>(null)
const events = ref<Array<Record<string, unknown>>>([])
const range = ref<'24h' | '7d' | '30d'>('24h')
const history = ref<{ host: Point[]; quantlab: Point[]; containers: Record<string, Point[]> }>({
  host: [],
  quantlab: [],
  containers: {},
})
let timer: number | undefined

const RANGE_LABELS: Record<string, string> = { '1h': '1 小时', '24h': '24 小时', '7d': '7 天', '30d': '30 天' }
const metric = ref<'cpu' | 'ram'>('cpu')

async function loadCurrent() {
  try {
    const [c, s] = await Promise.all([api.resourcesCurrent(), api.resourcesSummary()])
    current.value = c
    summary.value = s
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function loadHistory() {
  try {
    const result = await api.resourcesHistory(metric.value, range.value)
    history.value = {
      host: result.host ?? [],
      quantlab: result.quantlab ?? [],
      containers: result.containers ?? {},
    }
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function loadEvents() {
  try {
    events.value = (await api.resourcesEvents(30)).events
  } catch {
    events.value = []
  }
}

async function loadAll() {
  loading.value = true
  error.value = ''
  await Promise.all([loadCurrent(), loadHistory(), loadEvents()])
  loading.value = false
}

const nas = computed(() => (current.value?.available ? current.value.host : null))
const ql = computed(() => (current.value?.available ? current.value.quantlab : null))
const containers = computed(() =>
  ((current.value?.containers ?? []) as Array<Record<string, any>>)
    .slice()
    .sort((a, b) => (b.mem_used_mb ?? 0) - (a.mem_used_mb ?? 0)),
)

function chartSeries(): Array<{ name: string; points: Point[] }> {
  const series: Array<{ name: string; points: Point[] }> = [
    { name: metric.value === 'cpu' ? 'NAS CPU %' : 'NAS 内存 MB', points: history.value.host },
    { name: 'Quant Lab', points: history.value.quantlab },
  ]
  for (const [name, points] of Object.entries(history.value.containers)) {
    if (name !== 'quantlab') series.push({ name, points })
  }
  return series
}

function renderChart(): Array<{ name: string; points: Point[] }> {
  return chartSeries()
}

watch([range, metric], loadHistory)
onMounted(async () => {
  await loadAll()
  timer = window.setInterval(loadCurrent, 60_000)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <div>
    <h1 class="page-title">系统资源</h1>
    <p class="page-sub">
      整台 NAS 与 Quant Lab 的资源占用。采集在后台每 60 秒进行一次，页面每 60 秒自动刷新，
      监控本身几乎不占用资源。
    </p>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="!current?.available" class="notice">
      暂无采样数据。采集任务随 worker 每分钟运行一次，启动约一分钟后此处即有数据。
    </p>

    <div v-if="nas" class="grid cols-4">
      <div class="card">
        <h3>NAS CPU</h3>
        <div class="stat">{{ formatNumber(nas.cpu_percent, 1) }}%</div>
        <div class="muted">{{ nas.cpu_count }} 核心</div>
      </div>
      <div class="card">
        <h3>NAS 内存</h3>
        <div class="stat">
          {{ formatNumber((nas.mem_used_mb ?? 0) / 1024, 1) }} /
          {{ formatNumber((nas.mem_total_mb ?? 0) / 1024, 1) }} GB
        </div>
        <div class="muted">
          已用 {{ formatNumber(nas.mem_used_mb) }} MB
          <template v-if="nas.swap_total_mb > 0">
            · Swap {{ formatNumber(nas.swap_used_mb) }}/{{ formatNumber(nas.swap_total_mb) }} MB
          </template>
        </div>
      </div>
      <div class="card">
        <h3>Quant Lab</h3>
        <div class="stat">{{ formatNumber(ql?.cpu, 2) }}% · {{ formatNumber(ql?.mem_mb) }} MB</div>
        <div class="muted">
          {{ ql?.container_count }} 个容器 ·
          占当前 CPU {{ formatNumber(ql?.cpu_share_of_current_usage, 1) }}% ·
          占已用内存 {{ formatNumber(ql?.ram_share_of_used, 1) }}%
        </div>
      </div>
      <div class="card">
        <h3>磁盘（系统盘）</h3>
        <div class="stat">
          {{ formatNumber(nas.disk_used_gb, 0) }} /
          {{ formatNumber(nas.disk_total_gb, 0) }} GB
        </div>
        <div class="muted">
          已用 {{ formatNumber((nas.disk_used_gb / (nas.disk_total_gb || 1)) * 100, 1) }}%
        </div>
      </div>
    </div>

    <div class="card" style="margin-top: 14px">
      <h3>历史趋势</h3>
      <div class="row" style="margin-bottom: 10px">
        <select v-model="metric" style="max-width: 160px">
          <option value="cpu">CPU %</option>
          <option value="ram">内存 MB</option>
        </select>
        <select v-model="range" style="max-width: 160px">
          <option value="1h">过去 1 小时</option>
          <option value="24h">过去 24 小时</option>
          <option value="7d">过去 7 天</option>
          <option value="30d">过去 30 天</option>
        </select>
        <span class="muted">刷新 {{ RANGE_LABELS[range] ?? range }} 数据</span>
      </div>
      <div v-if="history.host.length || history.quantlab.length" style="margin-top: 6px">
        <MultiLineChart :series="chartSeries()" />
      </div>
      <table v-if="history.host.length || history.quantlab.length">
        <thead>
          <tr>
            <th>序列</th>
            <th>采样点</th>
            <th>最新值</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in renderChart()" :key="s.name">
            <td>{{ s.name }}</td>
            <td>{{ s.points.length }}</td>
            <td>
              {{ s.points.length ? formatNumber(s.points[s.points.length - 1]!.value, 2) : '—' }}
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">所选范围内还没有历史数据（7/30 天视图需要积累几分钟到几天）。</p>
      <p class="muted" style="margin-bottom: 0">
        图表数据来自 60 秒原始采样（1h/24h）与 5 分钟聚合（7d/30d）。原始数据保留
        7 天，聚合保留 30 天，到期自动清理。
      </p>
    </div>

    <div class="card" style="margin-top: 14px">
      <h3>容器明细（按内存排序，含 NAS 上全部 Docker 服务）</h3>
      <table v-if="containers.length">
        <thead>
          <tr>
            <th>容器</th>
            <th>服务</th>
            <th>归属</th>
            <th>CPU</th>
            <th>内存</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in containers" :key="c.container_name">
            <td>{{ c.container_name }}</td>
            <td>{{ c.compose_service ?? '—' }}</td>
            <td>
              <span class="badge" :class="c.is_quantlab ? 'BUY' : 'NO_SIGNAL'">
                {{ c.is_quantlab ? 'Quant Lab' : '其他服务' }}
              </span>
            </td>
            <td>{{ formatNumber(c.cpu_percent, 2) }}%</td>
            <td>{{ formatNumber(c.mem_used_mb) }} MB</td>
            <td>{{ c.state ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">
        暂无容器明细。全 NAS 视图需要只读 Docker 代理容器（quantlab-docker-proxy）在运行。
      </p>
    </div>

    <div class="card" style="margin-top: 14px">
      <h3>资源事件（任务峰值）</h3>
      <table v-if="events.length">
        <thead>
          <tr>
            <th>事件</th>
            <th>开始</th>
            <th>耗时</th>
            <th>CPU 峰值</th>
            <th>内存峰值</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(e, i) in events" :key="String(e.event_key)">
            <td>{{ e.event_type }} · {{ e.event_key }}</td>
            <td>{{ formatDateTime(String(e.started_at ?? '')) }}</td>
            <td>{{ e.duration_seconds != null ? e.duration_seconds + 's' : '—' }}</td>
            <td>{{ formatNumber(e.cpu_peak_percent == null ? null : Number(e.cpu_peak_percent), 1) }}%</td>
            <td>{{ formatNumber(e.mem_peak_mb == null ? null : Number(e.mem_peak_mb)) }} MB</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">
        暂无事件。每次回测完成后会记录该窗口内的 CPU/内存峰值与耗时。
      </p>
    </div>
  </div>
</template>