<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  api,
  type Asset,
  type BacktestDetail,
  type BacktestSummary,
  type ExplainResult,
  type SensitivityResult,
  type Strategy,
  type StrategyVersion,
} from '@/api'
import EquityChart from '@/components/EquityChart.vue'
import MultiLineChart from '@/components/MultiLineChart.vue'
import SensitivityChart from '@/components/SensitivityChart.vue'
import StatCard from '@/components/StatCard.vue'
import { formatDateTime, formatNumber, formatPercent, toneOf } from '@/format'

const runs = ref<BacktestSummary[]>([])
const onlyVersionFilter = ref(false)
const detail = ref<BacktestDetail | null>(null)
const error = ref('')
const busy = ref(false)

const strategies = ref<Strategy[]>([])
const versions = ref<StrategyVersion[]>([])
const assets = ref<Asset[]>([])
const strategyId = ref<number | null>(null)
const versionId = ref<number | null>(null)
const symbol = ref('DEMO-AAPL')
const timeframe = ref('1d')
const running = ref(false)
const oosResult = ref<Record<string, any> | null>(null)
const oosPct = ref(0.2)
const oosRunning = ref(false)
const wfResult = ref<Record<string, any> | null>(null)
const wfTrain = ref(200)
const wfTest = ref(60)
const wfRunning = ref(false)
// Parameter sensitivity sweep (docs/21). Descriptive only — the UI ranks points but
// never presents a "recommended" parameter set.
const sensResult = ref<SensitivityResult | null>(null)
const sensGridText = ref('')
const sensMetric = ref('sharpe')
const sensRunning = ref(false)
const SENS_METRICS = [
  'sharpe',
  'sortino',
  'total_return',
  'cagr',
  'max_drawdown',
  'win_rate',
  'profit_factor',
  'expectancy',
  'number_of_trades',
  'exposure',
]

/**
 * Parameters a strategy actually declares, with the values its indicators read via
 * `period_ref`. A parameter no `period_ref` points at is decorative: sweeping it
 * produces identical points, so it is deliberately excluded.
 */
const sweepableParams = computed<Array<{ name: string; current: number | null }>>(() => {
  const v = versions.value.find((x) => x.id === versionId.value)
  if (!v) return []
  const dsl = (v.dsl ?? {}) as Record<string, any>
  const declared = (dsl.parameters ?? {}) as Record<string, unknown>
  const refs = new Set<string>()
  for (const ind of (dsl.indicators ?? []) as Array<Record<string, any>>) {
    if (typeof ind?.period_ref === 'string') refs.add(ind.period_ref)
  }
  return [...refs]
    .filter((name) => name in declared)
    .map((name) => {
      const raw = declared[name]
      return { name, current: typeof raw === 'number' ? raw : null }
    })
    .sort((a, b) => a.name.localeCompare(b.name))
})

/** A small sweep range that brackets the declared value; never goes below 2. */
function sensitivityAxis(name: string, current: number | null, label: string): string {
  if (current !== null && current >= 1) {
    const base = Math.max(2, Math.round(current))
    const values = [Math.max(2, base - 5), base, base + 5, base + 10]
    return `${name}:${[...new Set(values)].sort((a, b) => a - b).join(',')}`
  }
  return `${name}:${label}`
}

function seedGridFromVersion() {
  const params = sweepableParams.value
  if (!params.length) {
    sensGridText.value = ''
    return
  }
  sensGridText.value = params
    .map((p) => sensitivityAxis(p.name, p.current, '5,10,20,40'))
    .join('; ')
}

// Re-seed whenever the selection changes so the default grid always matches the
// strategy in play; a stale default would just produce a 422 ("not declared").
watch(versionId, seedGridFromVersion)
function exportTradesCsv() {
  const trades = detail.value?.trades ?? []
  if (!trades.length) return
  const columns = [
    'direction',
    'entry_time',
    'entry_price',
    'exit_time',
    'exit_price',
    'quantity',
    'pnl',
    'pnl_pct',
    'r_multiple',
    'mae',
    'mfe',
    'fees',
    'slippage',
    'holding_bars',
    'exit_reason',
    'ambiguous_fill',
  ]
  const escape = (v: unknown) => {
    const s = v == null ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const lines = [columns.join(',')]
  for (const t of trades) {
    lines.push(columns.map((c) => escape((t as Record<string, unknown>)[c])).join(','))
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `backtest-${detail.value?.id ?? 'run'}-trades.csv`
  a.click()
  URL.revokeObjectURL(url)
}

const drawdownSeries = computed(() => {
  const points = detail.value?.equity_curve ?? []
  let peak = -Infinity
  const series = points.map((p) => {
    peak = Math.max(peak, p.equity)
    const dd = peak > 0 ? ((p.equity - peak) / peak) * 100 : 0
    return { ts: p.timestamp, value: dd }
  })
  return [{ name: '回撤 %', points: series }]
})

const metricRows = computed(() => {
  const metrics = detail.value?.metrics ?? {}
  return Object.entries(metrics as Record<string, number | null>).map(([key, value]) => ({
    key,
    value,
  }))
})

const compareIds = ref<number[]>([])
const compareResult = ref<{ metrics: string[]; runs: Array<Record<string, any>> } | null>(null)
const comparing = ref(false)

function toggleCompare(id: number) {
  const idx = compareIds.value.indexOf(id)
  if (idx >= 0) compareIds.value.splice(idx, 1)
  else compareIds.value.push(id)
}

async function runCompare() {
  error.value = ''
  compareResult.value = null
  if (compareIds.value.length < 2) {
    error.value = '请至少勾选两条回测记录'
    return
  }
  comparing.value = true
  try {
    compareResult.value = await api.compareBacktests(compareIds.value)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    comparing.value = false
  }
}

async function runOos() {
  error.value = ''
  oosResult.value = null
  if (versionId.value === null) {
    error.value = '请先选择策略版本'
    return
  }
  if (!symbol.value.trim()) {
    error.value = '请填写标的代码'
    return
  }
  oosRunning.value = true
  try {
    oosResult.value = await api.runOos(
      versionId.value,
      symbol.value.trim(),
      oosPct.value,
      timeframe.value,
    )
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    oosRunning.value = false
  }
}

async function runWf() {
  error.value = ''
  wfResult.value = null
  if (versionId.value === null) {
    error.value = '请先选择策略版本'
    return
  }
  if (!symbol.value.trim()) {
    error.value = '请填写标的代码'
    return
  }
  if (wfTrain.value + wfTest.value < 80) {
    error.value = '训练 + 测试窗口合计至少 80 根'
    return
  }
  wfRunning.value = true
  try {
    wfResult.value = await api.walkForward(
      versionId.value,
      symbol.value.trim(),
      wfTrain.value,
      wfTest.value,
    )
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    wfRunning.value = false
  }
}

async function runSensitivity() {
  error.value = ''
  sensResult.value = null
  if (versionId.value === null) {
    error.value = '请先选择策略版本'
    return
  }
  if (!symbol.value.trim()) {
    error.value = '请填写标的代码'
    return
  }
  const grid = parseGrid(sensGridText.value)
  if (grid === null) {
    error.value = '参数网格格式应为 参数名:值1,值2（多轴用分号分隔，例如 trend_period:5,10,20）'
    return
  }
  sensRunning.value = true
  try {
    sensResult.value = await api.sensitivity(
      versionId.value,
      symbol.value.trim(),
      grid,
      sensMetric.value,
      timeframe.value,
    )
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    sensRunning.value = false
  }
}

/** Parse `name:v1,v2; other:1,2` into the API's grid shape; null when malformed. */
function parseGrid(text: string): Record<string, Array<number | string>> | null {
  const out: Record<string, Array<number | string>> = {}
  const axes = text.split(';').map((s) => s.trim()).filter(Boolean)
  if (!axes.length) return null
  for (const axis of axes) {
    const idx = axis.indexOf(':')
    if (idx <= 0) return null
    const name = axis.slice(0, idx).trim()
    const values = axis
      .slice(idx + 1)
      .split(',')
      .map((v) => v.trim())
      .filter(Boolean)
      .map((v) => {
        const num = Number(v)
        return Number.isFinite(num) && v !== '' ? num : v
      })
    if (!name || !values.length) return null
    out[name] = values
  }
  return out
}

const sensPointCount = computed(() => {
  const grid = parseGrid(sensGridText.value)
  if (!grid) return 0
  return Object.values(grid).reduce((acc, vs) => acc * vs.length, 1)
})

async function load() {  error.value = ''
  try {
    const [r, s, a] = await Promise.all([
      api.backtests(onlyVersionFilter.value ? (versionId.value ?? undefined) : undefined),
      api.strategies(),
      api.assets(),
    ])
    runs.value = r
    strategies.value = s
    assets.value = a
    if (runs.value.length) {
      detail.value = await api.backtest(runs.value[0].id)
    }
    if (strategies.value.length && strategyId.value === null) {
      strategyId.value = strategies.value[0].id
    }
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function loadVersions() {
  versions.value = []
  versionId.value = null
  if (strategyId.value === null) return
  try {
    versions.value = await api.strategyVersions(strategyId.value)
    const current = versions.value.find((v) => v.is_current) ?? versions.value[0]
    if (current) versionId.value = current.id
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function removeRun(id: number) {
  error.value = ''
  busy.value = true
  try {
    await api.deleteBacktest(id)
    runs.value = runs.value.filter((r) => r.id !== id)
    if (detail.value?.id === id) {
      detail.value = runs.value.length ? await api.backtest(runs.value[0].id) : null
    }
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function open(id: number) {
  error.value = ''
  busy.value = true
  try {
    detail.value = await api.backtest(id)
    btExplanation.value = null
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function runNew() {
  error.value = ''
  if (versionId.value === null || !symbol.value.trim()) {
    error.value = '请先选择策略版本并填写标的代码'
    return
  }
  running.value = true
  try {
    const result = await api.runBacktest(versionId.value, symbol.value.trim(), timeframe.value)
    runs.value = [result, ...runs.value]
    detail.value = result
    btExplanation.value = null
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    running.value = false
  }
}

const btExplanation = ref<ExplainResult | null>(null)
const explainingBt = ref(false)

async function explainCurrent() {
  if (!detail.value) return
  explainingBt.value = true
  error.value = ''
  try {
    btExplanation.value = await api.explainBacktest(detail.value.id)
  } catch (e) {
    error.value = (e as Error).message
    btExplanation.value = null
  } finally {
    explainingBt.value = false
  }
}

watch(strategyId, loadVersions)
onMounted(async () => {
  await load()
  await loadVersions()
})
</script>

<template>
  <div>
    <h1 class="page-title">回测实验室</h1>
    <p class="page-sub">
      同一「策略版本 + 数据集 + 参数 + 引擎版本 + 特征版本」必然得到相同结果。样本不足的指标显示 N/A，不会编造。
    </p>

    <p v-if="error" class="error">{{ error }}</p>

    <div class="card">
      <h3>运行新回测</h3>
      <div class="row">
        <select v-model="strategyId" style="max-width: 220px">
          <option v-for="s in strategies" :key="s.id" :value="s.id">
            #{{ s.id }} {{ s.name }}
          </option>
        </select>
        <select v-model="versionId" style="max-width: 200px">
          <option v-for="v in versions" :key="v.id" :value="v.id">
            {{ v.version }}{{ v.is_current ? '（当前）' : '' }} · {{ v.validation_status }}
          </option>
        </select>
        <input v-model="symbol" list="asset-list" style="max-width: 160px" placeholder="标的代码" />
        <datalist id="asset-list">
          <option v-for="a in assets" :key="a.id" :value="a.symbol" />
        </datalist>
        <select v-model="timeframe" style="max-width: 110px">
          <option value="1d">日线</option>
        </select>
        <button :disabled="running || versionId === null" @click="runNew">
          {{ running ? '计算中…' : '开始回测' }}
        </button>
      </div>
      <p class="muted" style="margin-bottom: 0">
        先到「行情与策略」同步该标的的数据；同一版本重复运行会得到相同结果（结果哈希可验证）。
      </p>
    </div>

    <div class="card" style="margin-top: 14px">
      <h3>样本外验证（OOS）</h3>
      <div class="row">
        <input
          v-model.number="oosPct"
          type="number"
          min="0.05"
          max="0.95"
          step="0.05"
          style="max-width: 130px"
          placeholder="样本外比例"
        />
        <button :disabled="oosRunning || versionId === null" @click="runOos">
          {{ oosRunning ? '计算中…' : '运行 OOS' }}
        </button>
        <span class="muted">
          前 {{ formatPercent(1 - oosPct) }} 训练、后 {{ formatPercent(oosPct) }} 检验；同一策略不做参数拟合。
        </span>
      </div>
      <div v-if="oosResult" style="margin-top: 10px">
        <table>
          <thead>
            <tr>
              <th>指标</th>
              <th>样本内</th>
              <th>样本外</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="k in ['total_return', 'max_drawdown', 'sharpe', 'win_rate', 'number_of_trades']" :key="k">
              <td>{{ k }}</td>
              <td :class="oosResult.in_sample[k] != null ? toneOf(oosResult.in_sample[k]) : ''">
                {{ oosResult.in_sample[k] != null ? formatNumber(oosResult.in_sample[k], 4) : '—' }}
              </td>
              <td :class="oosResult.out_of_sample[k] != null ? toneOf(oosResult.out_of_sample[k]) : ''">
                {{ oosResult.out_of_sample[k] != null ? formatNumber(oosResult.out_of_sample[k], 4) : '—' }}
              </td>
            </tr>
            <tr>
              <td>样本数</td>
              <td>{{ oosResult.in_sample_bars }}</td>
              <td>{{ oosResult.out_of_sample_bars }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="card" style="margin-top: 14px">
      <h3>滚动 Walk-Forward</h3>
      <p class="muted">
        滑动窗口重复「训练段 + 测试段」，看样本外表现是否稳定（不同于只切一次的 OOS）。
        训练段窗口仅用于观察，不做参数拟合。
      </p>
      <div class="row">
        <input
          v-model.number="wfTrain"
          type="number"
          min="60"
          step="10"
          style="max-width: 130px"
          placeholder="训练窗口"
        />
        <input
          v-model.number="wfTest"
          type="number"
          min="20"
          step="10"
          style="max-width: 130px"
          placeholder="测试窗口"
        />
        <button :disabled="wfRunning || versionId === null" @click="runWf">
          {{ wfRunning ? '计算中…' : '运行 Walk-Forward' }}
        </button>
        <span class="muted">训练 {{ wfTrain }} 根 / 测试 {{ wfTest }} 根</span>
      </div>

      <div v-if="wfResult" style="margin-top: 10px">
        <table>
          <thead>
            <tr>
              <th>窗口数</th>
              <th>平均样本内收益</th>
              <th>平均样本外收益</th>
              <th>样本外为正的窗口</th>
              <th>一致性</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{{ wfResult.windows }}</td>
              <td>{{ formatPercent(wfResult.summary.mean_is_return) }}</td>
              <td>{{ formatPercent(wfResult.summary.mean_oos_return) }}</td>
              <td>
                {{ wfResult.summary.positive_oos_windows }} /
                {{ wfResult.windows }}
              </td>
              <td>{{ formatPercent(wfResult.summary.consistency) }}</td>
            </tr>
          </tbody>
        </table>

        <table v-if="wfResult.segments?.length" style="margin-top: 10px">
          <thead>
            <tr>
              <th>#</th>
              <th>训练段</th>
              <th>测试段</th>
              <th>样本内收益</th>
              <th>样本外收益</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="seg in wfResult.segments" :key="seg.window">
              <td>{{ seg.window }}</td>
              <td class="muted">{{ String(seg.train_start).slice(0, 10) }} → {{ String(seg.train_end).slice(0, 10) }}</td>
              <td class="muted">{{ String(seg.test_start).slice(0, 10) }} → {{ String(seg.test_end).slice(0, 10) }}</td>
              <td>{{ formatPercent(seg.in_sample.total_return) }}</td>
              <td :class="toneOf(seg.out_of_sample.total_return)">
                {{ formatPercent(seg.out_of_sample.total_return) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="card" style="margin-top: 14px">
      <h3>参数敏感性分析</h3>
      <p class="muted">
        把策略<b>已声明</b>的参数扫成网格、逐点独立回测，看结论在参数邻域内是平移还是翻转。
        <b>这不是参数优化</b>：下方「最优/最差」只是排序结果，不构成推荐；数值全部由确定性引擎计算。
        参数必须用 <code>period_ref</code> 声明（例如 <code>parameters.trend_period</code> 对应
        <code>indicators[].period_ref = "trend_period"</code>）。
      </p>
      <div class="row">
        <input
          v-model="sensGridText"
          style="min-width: 280px; flex: 1 1 320px"
          :placeholder="sweepableParams.length ? '' : 'fast_period:5,10,20,40'"
        />
        <select v-model="sensMetric" style="max-width: 170px">
          <option v-for="m in SENS_METRICS" :key="m" :value="m">{{ m }}</option>
        </select>
        <button
          :disabled="sensRunning || versionId === null || !sensGridText.trim()"
          @click="runSensitivity"
        >
          {{ sensRunning ? '计算中…' : '运行敏感性分析' }}
        </button>
        <span class="muted">
          {{ sensPointCount }} 个网格点<template v-if="sensPointCount > 144">（超过上限 144）</template>
        </span>
      </div>
      <p v-if="versionId !== null && !sweepableParams.length" class="muted" style="margin-top: 6px">
        当前策略版本没有可扫描的参数：需要用 <code>period_ref</code> 引用
        <code>parameters</code> 里的键（例如 <code>indicators[].period_ref = "fast_period"</code>
        对应 <code>parameters.fast_period</code>）。上面的输入框可手动填写
        <code>参数名:值1,值2</code>。
      </p>
      <p v-else-if="sweepableParams.length" class="muted" style="margin-top: 6px">
        已按该版本声明的参数自动填入：
        <code v-for="p in sweepableParams" :key="p.name" style="margin-right: 6px">
          {{ p.name }}={{ p.current ?? '—' }}
        </code>
      </p>

      <div v-if="sensResult" style="margin-top: 12px">
        <div class="grid cols-4">
          <StatCard
            label="已评估 / 网格点"
            :value="`${sensResult.evaluated_points} / ${sensResult.grid_points}`"
            sub="目标指标未定义的点不计入统计"
          />
          <StatCard
            label="目标指标均值"
            :value="formatNumber(sensResult.summary.mean)"
            :sub="`中位数 ${formatNumber(sensResult.summary.median)}`"
          />
          <StatCard
            label="极差 (max − min)"
            :value="formatNumber(sensResult.summary.range)"
            :sub="`标准差 ${formatNumber(sensResult.summary.stdev)}`"
          />
          <StatCard
            label="邻域稳健"
            :value="sensResult.stable === null ? 'N/A' : sensResult.stable ? '同号' : '符号翻转'"
            :tone="sensResult.stable === true ? 'pos' : sensResult.stable === false ? 'neg' : undefined"
            sub="仅表示符号一致，不代表策略好"
          />
        </div>

        <SensitivityChart
          :points="sensResult.points"
          :axes="sensResult.axes"
          :metric="sensResult.metric"
          height="320px"
        />

        <table style="margin-top: 10px">
          <thead>
            <tr>
              <th>参数</th>
              <th>{{ sensResult.metric }}</th>
              <th>总收益</th>
              <th>最大回撤</th>
              <th>交易数</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(p, i) in sensResult.points"
              :key="i"
              :class="{
                best: sensResult.best && p.result_hash === sensResult.best.result_hash,
                worst: sensResult.worst && p.result_hash === sensResult.worst.result_hash,
              }"
            >
              <td class="muted">
                {{ Object.entries(p.parameters).map(([k, v]) => `${k}=${v}`).join(', ') }}
              </td>
              <td :class="toneOf(p.objective)">
                {{ p.objective === null ? 'N/A' : formatNumber(p.objective) }}
              </td>
              <td :class="toneOf(p.metrics.total_return)">
                {{ formatPercent(p.metrics.total_return) }}
              </td>
              <td>{{ formatPercent(p.metrics.max_drawdown) }}</td>
              <td>{{ p.metrics.number_of_trades ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
        <p class="muted" style="margin-top: 6px">
          标绿 = 目标指标最高的点，标红 = 最低的点，均为排序结果而非推荐。
          <code>N/A</code> 表示该点样本不足以计算该指标。
        </p>
      </div>
    </div>

    <div v-if="detail" class="grid cols-4" style="margin-top: 14px">
      <StatCard
        label="总收益率"
        :value="formatPercent(detail.total_return)"
        :tone="toneOf(detail.total_return)"
        :sub="`期末权益 ${formatNumber(detail.final_equity)}`"
      />
      <StatCard
        label="最大回撤"
        :value="formatPercent(detail.max_drawdown)"
        :tone="toneOf(detail.max_drawdown)"
        sub="越小越好"
      />
      <StatCard
        label="夏普比率"
        :value="formatNumber(detail.sharpe)"
        :tone="toneOf(detail.sharpe)"
        sub="风险调整后收益"
      />
      <StatCard
        label="胜率"
        :value="formatPercent(detail.win_rate)"
        :sub="`交易 ${detail.number_of_trades ?? 0} 次`"
      />
    </div>

    <div v-if="detail" class="card" style="margin-top: 14px">
      <h3>权益曲线</h3>
      <EquityChart :points="detail.equity_curve" />
    </div>

    <div v-if="detail" class="card" style="margin-top: 14px">
      <h3>回撤曲线</h3>
      <MultiLineChart :series="drawdownSeries" height="220px" />
    </div>

    <div v-if="detail" class="card" style="margin-top: 14px">
      <h3>AI 解读（只解释已有数字，不重新计算）</h3>
      <div class="row" style="margin-bottom: 10px">
        <button :disabled="explainingBt" @click="explainCurrent">
          {{ explainingBt ? '解读中…' : '生成解读' }}
        </button>
        <span v-if="btExplanation?.cached" class="muted">缓存命中，未产生费用</span>
      </div>
      <div v-if="btExplanation">
        <p>{{ btExplanation.explanation.summary }}</p>
        <p class="muted">{{ btExplanation.explanation.plain_language }}</p>
        <ul v-if="btExplanation.explanation.key_drivers?.length" class="muted">
          <li v-for="(d, idx) in btExplanation.explanation.key_drivers" :key="idx">{{ d }}</li>
        </ul>
        <p v-if="btExplanation.explanation.risks?.length" class="muted">
          风险提示：{{ btExplanation.explanation.risks.join('；') }}
        </p>
      </div>
      <p v-else class="muted">尚未生成解读。未配置 AI 时此按钮不可用，量化功能不受影响。</p>
    </div>

    <div v-if="compareResult" class="card" style="margin-top: 14px">
      <h3>回测对比</h3>
      <table>
        <thead>
          <tr>
            <th>回测 #</th>
            <th v-for="m in compareResult.metrics" :key="m">{{ m }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in compareResult.runs" :key="r.run_id">
            <td>{{ r.run_id }}</td>
            <td v-for="m in compareResult.metrics" :key="m" :class="toneOf(r[m])">
              {{ r[m] != null ? formatNumber(r[m], 4) : '—' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="grid cols-2" style="margin-top: 14px">
      <div class="card">
        <div class="row" style="justify-content: space-between">
          <h3>回测记录</h3>
          <label class="muted" style="display: flex; align-items: center; gap: 6px">
            <input
              v-model="onlyVersionFilter"
              type="checkbox"
              style="width: auto"
              @change="load"
            />
            只看当前所选版本
          </label>
        </div>
        <table v-if="runs.length">
          <thead>
            <tr>
              <th>选</th>
              <th>#</th>
              <th>时间</th>
              <th>收益</th>
              <th>回撤</th>
              <th>夏普</th>
              <th>交易</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in runs" :key="r.id">
              <td><input type="checkbox" style="width: auto" :checked="compareIds.includes(r.id)" @change="toggleCompare(r.id)" /></td>
              <td>{{ r.id }}</td>
              <td>{{ formatDateTime(r.created_at) }}</td>
              <td :class="toneOf(r.total_return)">{{ formatPercent(r.total_return) }}</td>
              <td>{{ formatPercent(r.max_drawdown) }}</td>
              <td>{{ formatNumber(r.sharpe) }}</td>
              <td>{{ r.number_of_trades ?? 'N/A' }}</td>
              <td>
                <button class="ghost" :disabled="busy" @click="open(r.id)">查看</button>
                <button class="ghost" :disabled="busy" @click="removeRun(r.id)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">还没有回测记录。到「行情与策略」创建策略后即可运行。</p>
        <div class="row" style="margin-top: 8px">
          <button :disabled="comparing || compareIds.length < 2" @click="runCompare">
            {{ comparing ? '对比中…' : `对比选中（${compareIds.length}）` }}
          </button>
        </div>
      </div>

      <div class="card">
        <h3>结果可复现性</h3>
        <table v-if="detail">
          <tbody>
            <tr>
              <td>结果哈希</td>
              <td><code>{{ detail.result_hash }}</code></td>
            </tr>
            <tr>
              <td>数据集哈希</td>
              <td><code>{{ detail.dataset_hash }}</code></td>
            </tr>
            <tr>
              <td>引擎版本</td>
              <td>{{ detail.engine_version }}</td>
            </tr>
            <tr>
              <td>特征版本</td>
              <td>{{ detail.feature_version }}</td>
            </tr>
            <tr>
              <td>成交模型</td>
              <td>
                {{ detail.execution_model.fill_model }} · 订单
                {{ detail.execution_model.entry_order_type || 'market' }}（有效期
                {{ detail.execution_model.order_valid_bars ?? 1 }} bar）· 手续费
                {{ detail.execution_model.fee_bps }}bps · 滑点
                {{ detail.execution_model.slippage_bps }}bps
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">选择一条回测记录查看详情。</p>
      </div>
    </div>

    <div v-if="detail && metricRows.length" class="card" style="margin-top: 14px">
      <h3>指标明细</h3>
      <table>
        <thead>
          <tr>
            <th>指标</th>
            <th>值</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in metricRows" :key="m.key">
            <td>{{ m.key }}</td>
            <td :class="toneOf(m.value)">{{ m.value != null ? formatNumber(m.value, 4) : 'N/A' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="detail?.trades.length" class="card" style="margin-top: 14px">
      <div class="row" style="justify-content: space-between">
        <h3>交易明细</h3>
        <button class="ghost" @click="exportTradesCsv">导出 CSV</button>
      </div>
      <table>
        <thead>
          <tr>
            <th>方向</th>
            <th>开仓时间</th>
            <th>开仓价</th>
            <th>平仓时间</th>
            <th>平仓价</th>
            <th>数量</th>
            <th>盈亏</th>
            <th>盈亏%</th>
            <th>R</th>
            <th>MAE</th>
            <th>MFE</th>
            <th>手续费</th>
            <th>持仓</th>
            <th>原因</th>
            <th>歧义成交</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(t, i) in detail.trades" :key="i">
            <td>{{ t.direction }}</td>
            <td>{{ formatDateTime(String(t.entry_time)) }}</td>
            <td>{{ formatNumber(Number(t.entry_price)) }}</td>
            <td>{{ formatDateTime(t.exit_time ? String(t.exit_time) : null) }}</td>
            <td>{{ formatNumber(t.exit_price ? Number(t.exit_price) : null) }}</td>
            <td>{{ formatNumber(Number(t.quantity), 4) }}</td>
            <td :class="toneOf(t.pnl ? Number(t.pnl) : null)">
              {{ formatNumber(t.pnl ? Number(t.pnl) : null) }}
            </td>
            <td :class="toneOf(t.pnl_pct ? Number(t.pnl_pct) : null)">
              {{ t.pnl_pct != null ? formatPercent(Number(t.pnl_pct)) : '—' }}
            </td>
            <td>{{ t.r_multiple != null ? formatNumber(Number(t.r_multiple), 2) : '—' }}</td>
            <td class="muted">{{ t.mae != null ? formatNumber(Number(t.mae)) : '—' }}</td>
            <td class="muted">{{ t.mfe != null ? formatNumber(Number(t.mfe)) : '—' }}</td>
            <td class="muted">{{ t.fees != null ? formatNumber(Number(t.fees)) : '—' }}</td>
            <td class="muted">{{ t.holding_bars ?? '—' }} bar</td>
            <td>{{ t.exit_reason }}</td>
            <td>
              <span v-if="t.ambiguous_fill" class="badge SELL">保守处理</span>
              <span v-else class="muted">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
