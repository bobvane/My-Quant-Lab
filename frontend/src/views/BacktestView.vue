<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  api,
  type Asset,
  type BacktestDetail,
  type BacktestSummary,
  type EnsembleMemberRun,
  type EnsembleResult,
  type EnsembleSweepResult,
  type ExplainResult,
  type MonteCarloResult,
  type SensitivityResult,
  type Strategy,
  type StrategyLifecycle,
  type StrategyVersion,
} from '@/api'
import EquityChart from '@/components/EquityChart.vue'
import MetricHint from '@/components/MetricHint.vue'
import MonteCarloChart from '@/components/MonteCarloChart.vue'
import MultiLineChart from '@/components/MultiLineChart.vue'
import SensitivityChart from '@/components/SensitivityChart.vue'
import StatCard from '@/components/StatCard.vue'
import ThresholdSweepChart from '@/components/ThresholdSweepChart.vue'
import { formatDateTime, formatNumber, formatPercent, toneOf } from '@/format'
import { isAdvanced } from '@/mode'
// 引擎的指标键名与术语在这一页出现过三次（明细、敏感性表头、对比表头），
// 三处都走同一个翻译表，否则同一个键会写出三种中文（ADR-127）。
import { metricKeyLabel, nextStepText, stagePage, timeframeLabel } from '@/wording'

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
 * Metrics the API returns as fractions (0.0512 is 5.12%), so they must be
 * rendered with `formatPercent`. Everything else is already in its own unit —
 * Sharpe/Sortino are ratios of returns, profit factor is a multiple, expectancy
 * and MAE/MFE are prices, trade counts are counts (ADR-087).
 */
const RATIO_METRICS = new Set([
  'total_return',
  'cagr',
  'max_drawdown',
  'win_rate',
  'annualized_volatility',
  'exposure',
])

function formatMetric(key: string, value: number | null | undefined, digits = 4): string {
  if (value == null) return '—'
  // A percentage needs two decimals, not four: the extra digits are noise.
  return RATIO_METRICS.has(key) ? formatPercent(value, 2) : formatNumber(value, digits)
}

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

// Monte Carlo resampling of the selected backtest (docs/22).
const mcResult = ref<MonteCarloResult | null>(null)
const mcRuns = ref(1000)
const mcSeed = ref(0)
const mcRunning = ref(false)

// ---------------------------------------------------------------- ensemble ----
// Weighted vote across several strategy versions, executed as ONE portfolio
// (docs/24). Members are picked from the selected strategy's versions plus any other
// version already loaded; each carries a weight that is normalised server-side.
const ensResult = ref<EnsembleResult | null>(null)
const ensSelected = ref<number[]>([])
const ensWeights = ref<Record<number, number>>({})
const ensThreshold = ref(0.5)
const ensRunning = ref(false)
/**
 * Vote-threshold sweep (docs/24 §7, ADR-052). Describes the same ensemble at several
 * thresholds; it never recommends one. `null` thresholds lets the server pick the
 * thresholds where the answer can change (the coalition totals).
 */
const ensSweepResult = ref<EnsembleSweepResult | null>(null)
const ensSweepRunning = ref(false)
/** Optional explicit thresholds; the server derives them from the weights when blank. */
const ensSweepThresholdsText = ref('')
/**
 * Per-member single-strategy metrics, so "is diversifying better?" is answerable.
 * The dataset fields travel with them because a member's own stored run may be on a
 * different data window than the vote — the comparison table has to say so.
 */
const ensMemberMetrics = ref<
  Array<{
    label: string
    versionId: number
    metrics: Record<string, number | null>
    datasetVersionId: number | null
    symbol: string | null
    timeframe: string | null
  }>
>([])
/**
 * All versions across every strategy. Candidates must not be limited to the strategy
 * currently selected for a single-strategy run — voting across strategies is the
 * whole point of an ensemble, and scoping it made that unreachable in the UI.
 */
const ensAllVersions = ref<StrategyVersion[]>([])

/** Candidates grouped by strategy, so the picker reads as "which strategies agree". */
const ensCandidateGroups = computed(() => {
  const byStrategy = new Map<number, StrategyVersion[]>()
  for (const v of ensAllVersions.value) {
    const list = byStrategy.get(v.strategy_id) ?? []
    list.push(v)
    byStrategy.set(v.strategy_id, list)
  }
  return [...byStrategy.entries()]
    .map(([id, list]) => ({
      strategyId: id,
      name: strategies.value.find((s) => s.id === id)?.name ?? `#${id}`,
      versions: [...list].sort((a, b) => b.id - a.id),
    }))
    .sort((a, b) => a.strategyId - b.strategyId)
})

const ensCandidateCount = computed(() => ensAllVersions.value.length)

async function loadEnsCandidates() {
  try {
    ensAllVersions.value = await api.allStrategyVersions()
  } catch (e) {
    error.value = (e as Error).message
  }
}

function toggleEnsMember(id: number) {
  // A sweep describes one exact member/weight set, so changing the set must drop it.
  // Leaving a stale staircase on screen would describe an ensemble that is no longer
  // selected — the numbers would look current but belong to someone else's vote.
  ensSweepResult.value = null
  const idx = ensSelected.value.indexOf(id)
  if (idx >= 0) {
    ensSelected.value = ensSelected.value.filter((x) => x !== id)
  } else {
    ensSelected.value = [...ensSelected.value, id]
    if (ensWeights.value[id] === undefined) ensWeights.value = { ...ensWeights.value, [id]: 1 }
    void loadMemberMetrics()
  }
}

/** Reset every selected member to weight 1 (equal say). */
function ensEqualWeights() {
  ensSweepResult.value = null
  const next: Record<number, number> = {}
  for (const id of ensSelected.value) next[id] = 1
  ensWeights.value = next
}

/** Clear the selection entirely. */
function ensClearSelection() {
  ensSweepResult.value = null
  ensSelected.value = []
  ensWeights.value = {}
}

/**
 * Versions present more than once. The API rejects duplicates because two entries of
 * one version normalise to 0.5 + 0.5, making the "majority" threshold satisfiable by
 * that single strategy — a vote with one participant. The picker prevents it up front.
 */
const ensDuplicateCount = computed(() => {
  const seen = new Set<number>()
  let dupes = 0
  for (const id of ensSelected.value) {
    if (seen.has(id)) dupes++
    seen.add(id)
  }
  return dupes
})

/** Latest run per version, used to compare the ensemble against each member. */
async function loadMemberMetrics() {
  const out: Array<{
    label: string
    versionId: number
    metrics: Record<string, number | null>
    datasetVersionId: number | null
    symbol: string | null
    timeframe: string | null
  }> = []
  for (const id of ensSelected.value) {
    // Prefer the global list: a member may belong to another strategy, which the
    // per-strategy `versions` list does not contain.
    const version =
      ensAllVersions.value.find((v) => v.id === id) ?? versions.value.find((v) => v.id === id)
    // The API labels members `strategyId@version` (research.py). Building the label
    // from `version` alone produced "1.0.5" and never matched the "1@1.0.5" emitted by
    // the backend, so every comparison cell fell back to "—".
    const label = version ? `${version.strategy_id}@${version.version}` : String(id)
    try {
      const runs = await api.backtests(id)
      const latest = runs.find((r) => r.status === 'completed')
      if (!latest) continue
      const detail = await api.backtest(latest.id)
      out.push({
        label,
        versionId: id,
        datasetVersionId: detail.dataset_version_id ?? null,
        symbol: detail.symbol ?? null,
        timeframe: detail.timeframe ?? null,
        metrics: {
          total_return: detail.total_return,
          max_drawdown: detail.max_drawdown,
          sharpe: detail.sharpe,
          win_rate: detail.win_rate,
          number_of_trades: detail.number_of_trades,
        },
      })
    } catch {
      // A member with no completed backtest simply has no comparison row.
    }
  }
  ensMemberMetrics.value = out
}

/**
 * Portfolio and member equity curves on one axis.
 *
 * These come from `member_runs`, which the ensemble simulated on its own bars with its
 * own cost model, so every line starts from its own account's capital and they are
 * directly comparable. They are *independent* runs, not a concurrent portfolio: the
 * engine holds one position at a time, so a member line answers "what if only this
 * member had traded", not "what did this member contribute while the vote ran".
 */
const ENS_CHART_MEMBERS = 4

const ensEquitySeries = computed(() => {
  const result = ensResult.value
  if (!result) return []
  const lines = [
    {
      name: '集成组合',
      emphasis: true,
      points: (result.equity_curve ?? []).map((p) => ({
        ts: String((p as Record<string, unknown>).timestamp ?? ''),
        value: Number((p as Record<string, unknown>).equity ?? 0),
      })),
    },
    ...result.member_runs.slice(0, ENS_CHART_MEMBERS).map((run) => ({
      name: run.label,
      emphasis: false,
      points: (run.equity_curve ?? []).map((p) => ({
        ts: String((p as Record<string, unknown>).timestamp ?? ''),
        value: Number((p as Record<string, unknown>).equity ?? 0),
      })),
    })),
  ]
  return lines.filter((line) => line.points.length > 0)
})

/** Members whose same-bar run the ensemble reported, keyed by label. */
const ensRunsByLabel = computed(() => {
  const map = new Map<string, EnsembleMemberRun>()
  for (const run of ensResult.value?.member_runs ?? []) map.set(run.label, run)
  return map
})

/** How many members were left out of the chart because it holds four lines. */
const ensChartOmittedMembers = computed(() =>
  Math.max(0, (ensResult.value?.member_runs.length ?? 0) - ENS_CHART_MEMBERS),
)

/** One member's same-bar run, or null when the ensemble did not report one. */
function memberRunFor(label: string): EnsembleMemberRun | null {
  return ensRunsByLabel.value.get(label) ?? null
}

/** Metrics of one member's own latest completed backtest, for the comparison table. */
function memberMetricsFor(label: string): Record<string, number | null> | null {
  return ensMemberMetrics.value.find((x) => x.label === label)?.metrics ?? null
}

/** One member's numbers for the table: its own same-bar run, else its stored backtest. */
function memberCompareRow(label: string): {
  total_return: number | null
  max_drawdown: number | null
  sharpe: number | null
  entries: number | null
} {
  const run = memberRunFor(label)
  if (run) {
    return {
      total_return: run.metrics.total_return ?? null,
      max_drawdown: run.metrics.max_drawdown ?? null,
      sharpe: run.metrics.sharpe ?? null,
      entries: run.entries_taken,
    }
  }
  const stored = memberMetricsFor(label)
  return {
    total_return: stored?.total_return ?? null,
    max_drawdown: stored?.max_drawdown ?? null,
    sharpe: stored?.sharpe ?? null,
    entries: stored?.number_of_trades ?? null,
  }
}

async function runEnsemble() {
  error.value = ''
  ensResult.value = null
  ensMemberMetrics.value = []
  if (ensSelected.value.length < 2) {
    error.value = '请至少选择两个策略版本参与投票（单个成员没有「认同」可言）'
    return
  }
  if (!symbol.value.trim()) {
    error.value = '请填写标的代码'
    return
  }
  ensRunning.value = true
  try {
    const members = ensSelected.value.map((id) => ({
      strategy_version_id: id,
      weight: Number(ensWeights.value[id] ?? 1),
    }))
    ensResult.value = await api.ensemble(members, symbol.value.trim(), ensThreshold.value)
    await loadMemberMetrics()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    ensRunning.value = false
  }
}

/**
 * Longest explicit threshold list this endpoint will evaluate.
 *
 * Prefer the budget the server reported over any number written here: it is the server's
 * constant, and it has already changed once (12 was not enough for the widest ensemble's
 * own default grid). `null` until a sweep has answered.
 */
const ensSweepBudget = computed(() => ensSweepResult.value?.max_thresholds ?? null)

/**
 * Parse the optional explicit threshold list. Returns `null` when blank, which asks the
 * server to derive the thresholds where the answer can change (the coalition totals)
 * instead of us guessing a grid.
 */
function parseEnsSweepThresholds(): number[] | null {
  const raw = ensSweepThresholdsText.value.trim()
  if (!raw) return null
  const parts = raw
    .split(/[,，\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
  const numbers = parts.map((s) => Number(s))
  if (numbers.some((n) => !Number.isFinite(n) || n < 0 || n >= 1)) {
    throw new Error('阈值需为 [0, 1) 之间的数字，例如 0, 0.25, 0.5, 0.75')
  }
  const budget = ensSweepBudget.value
  if (budget !== null && numbers.length > budget) {
    throw new Error(`一次扫描最多评估 ${budget} 个阈值，当前填了 ${numbers.length} 个`)
  }
  return numbers
}

async function runEnsembleSweep() {
  error.value = ''
  ensSweepResult.value = null
  if (ensSelected.value.length < 2) {
    error.value = '请至少选择两个策略版本参与投票（单个成员没有「认同」可言）'
    return
  }
  if (!symbol.value.trim()) {
    error.value = '请填写标的代码'
    return
  }
  let thresholds: number[] | null
  try {
    thresholds = parseEnsSweepThresholds()
  } catch (e) {
    error.value = (e as Error).message
    return
  }
  ensSweepRunning.value = true
  try {
    const members = ensSelected.value.map((id) => ({
      strategy_version_id: id,
      weight: Number(ensWeights.value[id] ?? 1),
    }))
    ensSweepResult.value = await api.ensembleSweep(members, symbol.value.trim(), thresholds)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    ensSweepRunning.value = false
  }
}

/** Sweep points in chart shape. The endpoint returns no curves, only per-threshold numbers. */
const ensSweepPoints = computed(() => ensSweepResult.value?.points ?? [])

/** A vote share with no trailing zeros: 0.5 stays "0.5", not "0.500". */
function fmtVote(value: number): string {
  return String(Number(value.toFixed(6)))
}

/** Attainable vote totals, as a readable list ("0 · 0.5 · 1"). */
const ensSweepPossibleVotes = computed(() =>
  (ensSweepResult.value?.possible_votes ?? []).map(fmtVote).join(' · '),
)

/**
 * How many *distinct* trading behaviours the sweep actually found.
 *
 * When this is 1 the knob is inert for these weights: the surface is flat, and any
 * claim that one threshold beat another would be noise.
 */
const ensSweepDistinctEntries = computed(
  () => new Set(ensSweepPoints.value.map((p) => p.entries_taken)).size,
)

const ensSweepNote = computed(() => {
  const points = ensSweepPoints.value
  if (points.length === 0) return ''
  const counts = points.map((p) => p.entries_taken)
  const lowest = points[0]
  const highest = points[points.length - 1]
  if (ensSweepDistinctEntries.value === 1) {
    return `所有 ${points.length} 个阈值下开仓次数都是 ${counts[0]} 次 —— 在当前成员权重下这个旋钮不改变结果，别在这里调参。`
  }
  return `阈值从 ${fmtVote(lowest.vote_threshold)} 升到 ${fmtVote(highest.vote_threshold)}，开仓次数从 ${counts[0]} 次降到 ${counts[counts.length - 1]} 次。`
})

async function runMonteCarlo() {
  error.value = ''
  mcResult.value = null
  const runId = detail.value?.id
  if (runId === undefined || runId === null) {
    error.value = '请先在「回测记录」里选择一次已完成回测'
    return
  }
  if (mcRuns.value < 1 || mcRuns.value > 5000) {
    error.value = '重采样次数需在 1–5000 之间'
    return
  }
  mcRunning.value = true
  try {
    mcResult.value = await api.monteCarlo(runId, mcRuns.value, mcSeed.value)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    mcRunning.value = false
  }
}
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

// Points that never got an evaluable bar report a flat 0, which would draw as a real
// measurement sitting at the top of every losing grid. Plot only what was measured.
const sensChartPoints = computed(() =>
  (sensResult.value?.points ?? []).filter((p) => !p.warmup_unmet),
)

async function load() {
  error.value = ''
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
  const ok = window.confirm(`确定删除回测 #${id}？结果、指标与成交明细会一并删除，且不可恢复。`)
  if (!ok) return
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

// Position sizing for the next run (docs/23). Defaults to the strategy's own
// setting, so leaving this on "策略默认" changes nothing about existing behaviour.
const sizeMode = ref<'strategy' | 'fixed_fraction' | 'risk_per_trade' | 'atr_risk'>('strategy')
const sizeRiskPct = ref(0.01)
const sizeFraction = ref(0.5)

/** Execution overrides for the run button; empty means "use the strategy as stored". */
function sizingOverrides(): Record<string, unknown> {
  if (sizeMode.value === 'strategy') return {}
  if (sizeMode.value === 'fixed_fraction') {
    return { sizing: { mode: 'fixed_fraction', fraction: sizeFraction.value } }
  }
  return { sizing: { mode: sizeMode.value, risk_pct: sizeRiskPct.value } }
}

/**
 * Inclusive bar window for the next run. The API has always accepted ISO
 * `start`/`end` and filters bars with them; an empty box means "use every bar in
 * the series" (ADR-089). Days are read as UTC, like the bars themselves.
 */
const startDate = ref('')
const endDate = ref('')

function clearDates() {
  startDate.value = ''
  endDate.value = ''
}

function dayStart(value: string): string {
  return new Date(`${value}T00:00:00Z`).toISOString()
}

function dayEnd(value: string): string {
  return new Date(`${value}T23:59:59Z`).toISOString()
}

async function runNew() {
  error.value = ''
  if (versionId.value === null || !symbol.value.trim()) {
    error.value = '请先选择策略版本并填写标的代码'
    return
  }
  if (startDate.value && endDate.value && startDate.value > endDate.value) {
    error.value = '起始日期不能晚于结束日期'
    return
  }
  if (sizeMode.value !== 'strategy') {
    if (sizeRiskPct.value <= 0 || sizeRiskPct.value > 1) {
      error.value = '单笔风险比例需在 0 与 1 之间'
      return
    }
    if (sizeMode.value === 'fixed_fraction' && (sizeFraction.value <= 0 || sizeFraction.value > 1)) {
      error.value = '投入比例需在 0 与 1 之间'
      return
    }
  }
  running.value = true
  try {
    const result = await api.runBacktest(
      versionId.value,
      symbol.value.trim(),
      timeframe.value,
      sizingOverrides(),
      startDate.value ? dayStart(startDate.value) : undefined,
      endDate.value ? dayEnd(endDate.value) : undefined,
    )
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

// 回测结果的第一屏先说结论，再说细节（评审 §9、§10、§13；ADR-128、ADR-129）。
//
// 这里每个数字都取自引擎已经存下来的读数（`detail.metrics`）或生命周期证据
// （`/lifecycle/strategies/{id}`）；这一页不重新计算任何量化事实，缺读数就写清楚缺什么。
// `MIN_MEANINGFUL_TRADES` 必须与后端 `lifecycle.py` 的 `min_backtest_trades` 相同，
// 否则同一个策略在首页「够样本」、在回测页「样本太少」。
const MIN_MEANINGFUL_TRADES = 10

const lifecycle = ref<StrategyLifecycle | null>(null)

/** 展示中的这条回测属于哪个策略：证据卡说的是它，不是下拉框里碰巧选中的那个。 */
const evidenceStrategyId = computed(() => {
  const runVersion = detail.value?.strategy_version_id
  if (runVersion !== undefined && versions.value.some((v) => v.id === runVersion)) {
    return strategyId.value
  }
  const version = ensAllVersions.value.find((v) => v.id === runVersion)
  return version?.strategy_id ?? strategyId.value
})

async function loadLifecycle() {
  const id = evidenceStrategyId.value
  if (id === null) {
    lifecycle.value = null
    return
  }
  try {
    lifecycle.value = await api.lifecycle(id)
  } catch (e) {
    lifecycle.value = null
    error.value = (e as Error).message
  }
}

/** 引擎读数按需取用；`null` 就是「这次没有这个数」，不拿 0 顶上。 */
function metricValue(key: string): number | null {
  const raw = detail.value?.metrics?.[key]
  return typeof raw === 'number' ? raw : null
}

/** 一句结论：这套策略历史上表现怎么样（评审 §9）。 */
const conclusionHeadline = computed(() => {
  const run = detail.value
  if (!run) return ''
  const ret = run.total_return
  const performance =
    ret === null || ret === undefined
      ? '没有算出总收益'
      : ret >= 0
        ? `整体是赚钱的：累计收益 ${formatPercent(ret)}`
        : `整体是亏钱的：累计收益 ${formatPercent(ret)}`
  const period = `${run.symbol ?? '该标的'} · ${timeframeLabel(run.timeframe)}`
  return `${period} 的历史数据里，这套策略${performance}，期间最大回撤 ${formatPercent(run.max_drawdown)}。`
})

/** 结论徽章：只有历史回测一层证据也敢说「值得继续验证」，但样本不够时先说样本。 */
const verdict = computed(() => {
  const run = detail.value
  if (!run) return { tone: 'warn', label: '' }
  const trades = run.number_of_trades ?? 0
  if (trades < MIN_MEANINGFUL_TRADES) {
    return { tone: 'warn', label: `样本太少（${trades} 笔），先别下结论` }
  }
  if (run.total_return === null || run.total_return === undefined) {
    return { tone: 'warn', label: '这次回测没有算出收益，先看提醒' }
  }
  if (run.total_return <= 0) return { tone: 'bad', label: '历史回测没有赚钱，不建议继续' }
  return { tone: 'ok', label: '值得继续验证' }
})

/** 最大风险（评审 §9）：回撤与最长连续亏损，两句话都来自已存读数。 */
const worstPart = computed(() => {
  const run = detail.value
  if (!run) return ''
  const bits: string[] = []
  if (run.max_drawdown !== null && run.max_drawdown !== undefined) {
    bits.push(`从最高点跌到随后最低点最深 ${formatPercent(Math.abs(run.max_drawdown))}`)
  }
  const streak = metricValue('max_consecutive_losses')
  if (streak !== null) bits.push(`最长连续亏 ${streak} 笔`)
  if (!bits.length) return '这次回测没有留下回撤或连续亏损的读数。'
  return `${bits.join('，')} —— 这是这套策略最难熬的部分。`
})

/** 可信程度（评审 §9）：每一层证据是做过还是没做过，门槛来自生命周期。 */
const evidenceRows = computed(() => {
  const life = lifecycle.value
  const gates = life?.gates ?? {}
  const evidence = (life?.evidence ?? {}) as Record<string, unknown>
  const count = (key: string): number => Number(evidence[key] ?? 0)
  return [
    {
      name: '历史回测',
      done: Boolean(gates.backtested),
      detail: `${count('backtest_runs')} 次回测，其中交易最多的一次 ${count('backtest_best_trades')} 笔`,
    },
    {
      name: '样本外验证（OOS）',
      done: Boolean(gates.oos_tested),
      detail: `${count('oos_windows')} 个样本外窗口`,
    },
    {
      name: '滚动验证（Walk-Forward）',
      done: wfResult.value !== null,
      detail: wfResult.value
        ? '本次页面已经跑过一次（只算本页状态，不构成策略证据）'
        : '还没有跑：它检验样本外表现是否稳定',
    },
    {
      name: '模拟验证',
      done: Boolean(gates.paper_trading),
      detail: `${count('paper_trades')} 笔模拟成交`,
    },
  ]
})

/** 下一步：生命周期有证据支撑的那一步；没有就退回主流程，话术与首页一致。 */
const nextStep = computed(() => {
  const life = lifecycle.value
  const stage = life?.suggested_next
  if (stage) {
    return {
      text: nextStepText(stage),
      reason: life?.blocked_reason ? `现在还不能推进：${life.blocked_reason}` : '',
      to: stagePage(stage)?.to ?? '',
      linkText: stagePage(stage)?.label ?? '',
    }
  }
  if (detail.value) {
    return { text: nextStepText('backtested'), reason: '', to: '/backtest', linkText: '' }
  }
  return { text: nextStepText(null), reason: '', to: '', linkText: '' }
})

/** §10 二级：详细分析默认收起，一次点击就能全部打开。 */
const showDetailAnalytics = ref(false)

/** §13 ②原因：优先用 `key_drivers`，旧结果回退到 `why`；两者都没有就不编。 */
const aiDrivers = computed<string[]>(() => {
  const explanation = btExplanation.value?.explanation
  if (!explanation) return []
  if (explanation.key_drivers?.length) return explanation.key_drivers
  return explanation.why ?? []
})

/** §13 ③风险：`risks` 与 `risk_notes` 都是对已存事实的说法，合并显示。 */
const aiRisks = computed<string[]>(() => {
  const explanation = btExplanation.value?.explanation
  if (!explanation) return []
  return [...(explanation.risks ?? []), ...(explanation.risk_notes ?? [])]
})

/** §13 ④可信程度：这一句来自已存证据，不是 AI 写的，所以 AI 没配置时它照样在。 */
const evidenceSentence = computed(() => {
  const done = evidenceRows.value.filter((row) => row.done).map((row) => row.name)
  const missing = evidenceRows.value.filter((row) => !row.done).map((row) => row.name)
  if (!missing.length) return `这几层证据都已经做过：${done.join('、')}。`
  const doneText = done.length ? done.join('、') : '（还没有）'
  return `已经做完的是${doneText}；还没有做的是${missing.join('、')} —— 上面的结论只在做过的这几层里成立。`
})

watch(strategyId, loadVersions)
watch(evidenceStrategyId, loadLifecycle)
onMounted(async () => {
  await load()
  await loadVersions()
  await loadLifecycle()
  // Ensemble candidates span every strategy, so they load independently of the
  // single-strategy selection above.
  await loadEnsCandidates()
})
</script>

<template>
  <div>
    <h1 class="page-title">回测</h1>
    <p class="page-sub">
      运行一次历史回测，然后看结论：这套策略过去表现如何、为什么、风险有多大、下一步该做什么。
      同一「策略版本 + 数据集 + 参数 + 引擎版本 + 特征版本」必然得到相同结果；样本不足的指标显示 N/A，不会编造。
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

      <div class="row" style="margin-top: 8px">
        <label class="muted" style="display: flex; align-items: center; gap: 6px">
          仓位管理
          <select v-model="sizeMode" style="max-width: 190px">
            <option value="strategy">策略默认（不改动）</option>
            <option value="fixed_fraction">固定比例</option>
            <option value="risk_per_trade">按止损风险</option>
            <option value="atr_risk">按 ATR 风险</option>
          </select>
        </label>
        <label
          v-if="sizeMode === 'fixed_fraction'"
          class="muted"
          style="display: flex; align-items: center; gap: 6px"
        >
          投入现金比例
          <input
            v-model.number="sizeFraction"
            type="number"
            min="0.01"
            max="1"
            step="0.05"
            style="max-width: 90px"
          />
        </label>
        <label
          v-if="sizeMode === 'risk_per_trade' || sizeMode === 'atr_risk'"
          class="muted"
          style="display: flex; align-items: center; gap: 6px"
        >
          单笔风险占权益
          <input
            v-model.number="sizeRiskPct"
            type="number"
            min="0.001"
            max="1"
            step="0.005"
            style="max-width: 90px"
          />
        </label>
        <span class="muted">
          <template v-if="sizeMode === 'strategy'">按策略版本里已保存的仓位设置执行。</template>
          <template v-else-if="sizeMode === 'fixed_fraction'">投入当前现金的固定比例。</template>
          <template v-else>
            按「入场价到止损价的距离」反推数量，使止损距离与单笔风险解耦；无止损时回退为固定比例。
          </template>
        </span>
      </div>
      <div class="row" style="margin-top: 8px">
        <label class="muted" style="display: flex; align-items: center; gap: 6px">
          起始日期
          <input v-model="startDate" type="date" style="max-width: 165px" />
        </label>
        <label class="muted" style="display: flex; align-items: center; gap: 6px">
          结束日期
          <input v-model="endDate" type="date" style="max-width: 165px" />
        </label>
        <button class="ghost" :disabled="!startDate && !endDate" @click="clearDates">
          清除区间
        </button>
        <span class="muted">
          <template v-if="startDate || endDate">
            只回测该区间内的 K 线（含首尾两天，按 UTC 计算）。
          </template>
          <template v-else>留空则使用该序列已同步的全部 K 线。</template>
        </span>
      </div>
      <p class="muted" style="margin-bottom: 0">
        先到「我的策略」同步该标的的数据；同一版本重复运行会得到相同结果（结果哈希可验证）。
        仓位管理会写入本次回测的执行模型，因此改变它会让结果哈希随之变化。
      </p>
    </div>

    <!-- 第一屏：这套策略历史上表现怎么样、最坏能坏到哪里、这份结论有多可信、下一步做什么
         （评审 §9；ADR-128）。全部读数来自引擎与生命周期证据。 -->
    <div v-if="detail" class="card conclusion-card" style="margin-top: 14px">
      <h3>历史回测结论</h3>
      <p class="verdict-line">
        <span class="verdict" :class="verdict.tone">{{ verdict.label }}</span>
      </p>
      <p class="conclusion-sentence">{{ conclusionHeadline }}</p>
      <div class="grid cols-4" style="margin-top: 10px">
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
          label="胜率"
          :value="formatPercent(detail.win_rate)"
          sub="方向猜对的频率"
        />
        <StatCard
          label="交易次数"
          :value="detail.number_of_trades ?? '—'"
          sub="上面的结论有多少样本支撑"
        />
      </div>
      <div class="grid cols-4" style="margin-top: 10px">
        <StatCard
          label="盈亏效率"
          :value="formatNumber(metricValue('profit_factor'), 2)"
          :tone="toneOf(metricValue('profit_factor'))"
          sub="赚的钱 ÷ 亏的钱"
        />
        <StatCard
          v-if="metricValue('cagr') !== null"
          label="年化复合收益率"
          :value="formatPercent(metricValue('cagr'))"
          :tone="toneOf(metricValue('cagr'))"
          sub="按年折算的复合增长"
        />
        <StatCard
          v-if="metricValue('average_holding_bars') !== null"
          label="平均持仓（根）"
          :value="formatNumber(metricValue('average_holding_bars'), 1)"
          :sub="`1 根 = 1 个${timeframeLabel(detail.timeframe)}数据点`"
        />
      </div>
      <p class="risk-line"><b>最大风险：</b>{{ worstPart }}</p>
      <p class="next-line">
        <b>下一步：</b>{{ nextStep.text }}
        <RouterLink v-if="nextStep.to && nextStep.linkText" :to="nextStep.to">
          {{ nextStep.linkText }}
        </RouterLink>
        <span v-if="nextStep.reason" class="muted">（{{ nextStep.reason }}）</span>
      </p>
    </div>

    <div v-if="detail" class="card" style="margin-top: 14px">
      <h3>可信程度怎么样？</h3>
      <p class="muted">
        每一层证据是「做过」还是「没做过」，由系统按已存证据判断（生命周期门槛，不涉及模型）。
        没做过的那一层，就是上面结论的边界。
      </p>
      <table>
        <thead>
          <tr>
            <th>证据</th>
            <th>状态</th>
            <th>已经存下来的东西</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in evidenceRows" :key="row.name">
            <td>{{ row.name }}</td>
            <td>{{ row.done ? '已完成' : '未完成' }}</td>
            <td class="muted">{{ row.detail }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 评审 §10 的三级分析：OOS 分割、滚动 Walk-Forward、参数敏感性、Monte Carlo、
         策略集成与投票阈值扫描默认收起，能力一点没少（ADR-126、ADR-128）。 -->
    <div v-if="!isAdvanced" class="card" style="margin-top: 14px">
      <h3>高级分析（普通模式下不占第一屏）</h3>
      <p class="muted">
        下面这些是研究工具，不是「先看结论」需要的东西：OOS 分割、滚动 Walk-Forward、参数敏感性、
        Monte Carlo 重采样、策略集成与投票阈值扫描。它们的能力一点没少，只是在普通模式里默认收起 ——
        要看就在顶部切到「● 高级模式」。
      </p>
    </div>

    <template v-if="isAdvanced">
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
              <td>{{ metricKeyLabel(k) }}<MetricHint :label="k" /></td>
              <td :class="oosResult.in_sample[k] != null ? toneOf(oosResult.in_sample[k]) : ''">
                {{ formatMetric(k, oosResult.in_sample[k]) }}
              </td>
              <td :class="oosResult.out_of_sample[k] != null ? toneOf(oosResult.out_of_sample[k]) : ''">
                {{ formatMetric(k, oosResult.out_of_sample[k]) }}
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
        <p v-for="(w, i) in sensResult.warnings" :key="i" class="notice">{{ w }}</p>

        <div class="grid cols-4">
          <StatCard
            label="参与排名 / 网格点"
            :value="`${sensResult.ranked_points} / ${sensResult.grid_points}`"
            :sub="
              sensResult.warmup_unmet_points > 0
                ? `${sensResult.warmup_unmet_points} 个点整段落在预热期内，未被测到，已排除`
                : '未测得或目标指标未定义的点不计入统计'
            "
          />
          <StatCard
            label="目标指标均值"
            :value="formatMetric(sensMetric, sensResult.summary.mean)"
            :sub="`中位数 ${formatMetric(sensMetric, sensResult.summary.median)}`"
          />
          <StatCard
            label="极差 (max − min)"
            :value="formatMetric(sensMetric, sensResult.summary.range)"
            :sub="`标准差 ${formatMetric(sensMetric, sensResult.summary.stdev)}`"
          />
          <StatCard
            label="邻域稳健"
            :value="sensResult.stable === null ? 'N/A' : sensResult.stable ? '同号' : '符号翻转'"
            :tone="sensResult.stable === true ? 'pos' : sensResult.stable === false ? 'neg' : undefined"
            sub="仅表示符号一致，不代表策略好"
          />
        </div>

        <SensitivityChart
          v-if="sensChartPoints.length > 0"
          :points="sensChartPoints"
          :axes="sensResult.axes"
          :metric="sensResult.metric"
          height="320px"
        />

        <table style="margin-top: 10px">
          <thead>
            <tr>
              <th>参数</th>
              <th>{{ metricKeyLabel(sensResult.metric) }}</th>
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
                <template v-if="p.warmup_unmet">（整段在预热期内）</template>
              </td>
              <td :class="p.warmup_unmet ? 'muted' : toneOf(p.objective)">
                {{
                  p.warmup_unmet
                    ? '未测得'
                    : p.objective === null
                      ? 'N/A'
                      : formatMetric(sensMetric, p.objective)
                }}
              </td>
              <td :class="p.warmup_unmet ? 'muted' : toneOf(p.metrics.total_return)">
                {{ p.warmup_unmet ? '—' : formatPercent(p.metrics.total_return) }}
              </td>
              <td>{{ p.warmup_unmet ? '—' : formatPercent(p.metrics.max_drawdown) }}</td>
              <td>{{ p.warmup_unmet ? '—' : (p.metrics.number_of_trades ?? '—') }}</td>
            </tr>
          </tbody>
        </table>
        <p class="muted" style="margin-top: 6px">
          标绿 = 目标指标最高的点，标红 = 最低的点，均为排序结果而非推荐。
          <code>N/A</code> 表示该点样本不足以计算该指标。
        </p>
        <p v-if="sensResult.warmup_unmet_points > 0" class="muted" style="margin-top: 6px">
          标「整段在预热期内」的点，整段数据都在策略预热期里，一根可评估的 bar
          都没有——它们报的 0 不是「不赚不亏」，而是「没跑起来」。这些点不参与上面的均值、极差与稳健性判断，也不进图。
        </p>
      </div>
    </div>

    <div class="card" style="margin-top: 14px">
      <h3>Monte Carlo 重采样</h3>
      <p class="muted">
        把当前所选回测的<b>已成交交易</b>有放回地重采样许多次，看结果的分布而不是单条历史路径。
        同一批交易换个顺序就可能回撤更深——这正是单次回测看不到的东西。
        <b>这是对历史的再抽样，不是预测</b>：它假定交易互相独立、同分布（真实交易存在自相关，
        因此会低估连续亏损的概率），且只用该策略自己已实现的交易。
      </p>
      <div class="row">
        <input
          v-model.number="mcRuns"
          type="number"
          min="1"
          max="5000"
          step="100"
          style="max-width: 150px"
          placeholder="重采样次数"
        />
        <input
          v-model.number="mcSeed"
          type="number"
          step="1"
          style="max-width: 150px"
          placeholder="随机种子"
        />
        <button :disabled="mcRunning || !detail" @click="runMonteCarlo">
          {{ mcRunning ? '计算中…' : '运行 Monte Carlo' }}
        </button>
        <span class="muted">
          {{ detail ? `基于回测 #${detail.id}` : '先选择一次已完成回测' }} ·
          相同种子必然得到相同分布
        </span>
      </div>

      <div v-if="mcResult" style="margin-top: 12px">
        <p v-for="w in mcResult.warnings" :key="w" class="notice">{{ w }}</p>

        <div class="grid cols-4">
          <StatCard
            label="盈利概率"
            :value="formatPercent(mcResult.summary.probability_of_profit)"
            :tone="mcResult.summary.probability_of_profit >= 0.5 ? 'pos' : 'neg'"
            :sub="`亏损失概率 ${formatPercent(mcResult.summary.probability_of_loss)}`"
          />
          <StatCard
            label="收益中位数"
            :value="formatPercent(mcResult.summary.total_return.p50)"
            :tone="toneOf(mcResult.summary.total_return.p50)"
            :sub="`p5 ${formatPercent(mcResult.summary.total_return.p5)} · p95 ${formatPercent(mcResult.summary.total_return.p95)}`"
          />
          <StatCard
            label="回撤中位数"
            :value="formatPercent(mcResult.summary.max_drawdown.p50)"
            :tone="toneOf(mcResult.summary.max_drawdown.p50)"
            :sub="`最差路径 ${formatPercent(mcResult.summary.worst_max_drawdown)}`"
          />
          <StatCard
            label="清零概率"
            :value="formatPercent(mcResult.summary.probability_of_ruin)"
            :tone="mcResult.summary.probability_of_ruin > 0 ? 'neg' : 'plain'"
            sub="权益归零的路径占比"
          />
        </div>

        <MonteCarloChart
          :paths="mcResult.sample_equity_paths"
          :initial-capital="mcResult.summary.initial_capital"
          height="320px"
        />

        <table style="margin-top: 10px">
          <thead>
            <tr>
              <th>指标</th>
              <th>p5</th>
              <th>p25</th>
              <th>p50</th>
              <th>p75</th>
              <th>p95</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="muted">总收益</td>
              <td>{{ formatPercent(mcResult.summary.total_return.p5) }}</td>
              <td>{{ formatPercent(mcResult.summary.total_return.p25) }}</td>
              <td>{{ formatPercent(mcResult.summary.total_return.p50) }}</td>
              <td>{{ formatPercent(mcResult.summary.total_return.p75) }}</td>
              <td>{{ formatPercent(mcResult.summary.total_return.p95) }}</td>
            </tr>
            <tr>
              <td class="muted">最大回撤</td>
              <td>{{ formatPercent(mcResult.summary.max_drawdown.p5) }}</td>
              <td>{{ formatPercent(mcResult.summary.max_drawdown.p25) }}</td>
              <td>{{ formatPercent(mcResult.summary.max_drawdown.p50) }}</td>
              <td>{{ formatPercent(mcResult.summary.max_drawdown.p75) }}</td>
              <td>{{ formatPercent(mcResult.summary.max_drawdown.p95) }}</td>
            </tr>
            <tr>
              <td class="muted">夏普</td>
              <td>{{ formatNumber(mcResult.summary.sharpe.p5) }}</td>
              <td>{{ formatNumber(mcResult.summary.sharpe.p25) }}</td>
              <td>{{ formatNumber(mcResult.summary.sharpe.p50) }}</td>
              <td>{{ formatNumber(mcResult.summary.sharpe.p75) }}</td>
              <td>{{ formatNumber(mcResult.summary.sharpe.p95) }}</td>
            </tr>
          </tbody>
        </table>
        <p class="muted" style="margin-top: 6px">
          方法 <code>{{ mcResult.method }}</code> · 观测交易 {{ mcResult.summary.observed_trades }} 笔 ·
          每次 {{ mcResult.summary.trades_per_run }} 笔 · {{ mcResult.summary.runs }} 条路径 ·
          seed {{ mcResult.seed }} · 时间周期 {{ mcResult.timeframe }}
        </p>
      </div>
    </div>

    <div class="card" style="margin-top: 14px">
      <h3>策略集成（加权投票）</h3>
      <p class="muted">
        勾选多个策略版本，逐根 K 线按权重投票，只有票数<b>严格超过</b>阈值的才开仓，
        合并后的决策驱动<b>一个</b>组合执行。
        <b>这不是「哪个策略最好」</b>，而是「互相认同是否比单打独斗更稳」。
        等权两成员时各占 0.5 票，所以 0.5 的阈值意味着<b>两个都同意</b>才算数。
      </p>

      <div v-if="!ensCandidateCount" class="muted">还没有任何策略版本可参与投票。</div>
      <div v-else>
        <p class="muted" style="margin: 0 0 6px">
          共 {{ ensCandidateCount }} 个版本可选，<b>可跨策略</b>投票（下面按策略分组）。
          勾选后可在同一行填权重，权重会在服务端归一化。
        </p>
        <div
          v-for="g in ensCandidateGroups"
          :key="g.strategyId"
          style="margin-bottom: 6px"
        >
          <span class="muted" style="margin-right: 8px">#{{ g.strategyId }} {{ g.name }}</span>
          <span style="display: inline-flex; flex-wrap: wrap; gap: 8px">
            <label
              v-for="v in g.versions"
              :key="v.id"
              class="muted"
              style="display: flex; align-items: center; gap: 6px; border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px"
            >
              <input
                type="checkbox"
                style="width: auto"
                :checked="ensSelected.includes(v.id)"
                @change="toggleEnsMember(v.id)"
              />
              v{{ v.version }}
              <input
                v-if="ensSelected.includes(v.id)"
                v-model.number="ensWeights[v.id]"
                type="number"
                min="0"
                step="0.5"
                style="max-width: 70px"
                title="权重（会被归一化）"
              />
            </label>
          </span>
        </div>

        <div class="row" style="margin-top: 8px">
          <label class="muted" style="display: flex; align-items: center; gap: 6px">
            投票阈值
            <input
              v-model.number="ensThreshold"
              type="number"
              min="0"
              max="0.99"
              step="0.1"
              style="max-width: 90px"
            />
          </label>
          <button :disabled="ensRunning || ensSelected.length < 2" @click="runEnsemble">
            {{ ensRunning ? '计算中…' : '运行集成' }}
          </button>
          <button class="ghost" :disabled="!ensSelected.length" @click="ensEqualWeights">
            权重归一（全设为 1）
          </button>
          <button class="ghost" :disabled="!ensSelected.length" @click="ensClearSelection">
            清空选择
          </button>
          <span class="muted">
            已选 {{ ensSelected.length }} 个成员 ·
            阈值越高越保守（需要更多权重认同）
          </span>
        </div>

        <div class="row" style="margin-top: 8px">
          <label class="muted" style="display: flex; align-items: center; gap: 6px">
            扫描阈值（留空 = 自动）
            <input
              v-model="ensSweepThresholdsText"
              type="text"
              placeholder="例如 0, 0.25, 0.5, 0.75"
              style="max-width: 220px"
            />
          </label>
          <button
            class="ghost"
            :disabled="ensSweepRunning || ensSelected.length < 2"
            @click="runEnsembleSweep"
          >
            {{ ensSweepRunning ? '扫描中…' : '扫描阈值' }}
          </button>
          <span class="muted">
            留空时服务端只在<b>答案会发生变化</b>的阈值上跑（票数只能落在成员权重的联盟总数上）
          </span>
        </div>
        <p v-if="ensSelected.length === 1" class="muted" style="margin-top: 4px">
          至少需要两个成员：只有一个成员时不存在「认同」这回事。若想让某个策略话语权更大，
          请保留其他成员并调高它的权重，而不是把它重复添加。
        </p>
      </div>

      <div v-if="ensSweepResult" style="margin-top: 12px">
        <h4>投票阈值扫描（同一批成员、同一批 bar）</h4>
        <p class="muted">
          阈值是集成<b>唯一的旋钮</b>；加权票是成员权重之和，所以它只能落在
          <b>{{ ensSweepPossibleVotes }}</b> 这些值上。两个相邻票数之间的任何阈值行为完全相同 ——
          这张图是<b>阶梯</b>而不是曲线，别在两个台阶之间找最优点。本表只描述形状，
          <b>不推荐阈值</b>。
        </p>
        <p v-if="ensSweepNote" class="muted">{{ ensSweepNote }}</p>
        <p v-for="(w, i) in ensSweepResult.warnings" :key="i" class="notice">{{ w }}</p>
        <p class="muted">
          本次评估了 {{ ensSweepPoints.length }} 个阈值，一次扫描最多
          {{ ensSweepResult.max_thresholds }} 个。要更多台阶就自己填阈值列表；超过上限会被
          <b>拒绝</b>（报错会说明这份成员权重一共有多少个联盟边界），而不是悄悄截断。
        </p>
        <ThresholdSweepChart :points="ensSweepPoints" height="280px" />
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>阈值</th>
                <th>实际生效票数</th>
                <th>开仓</th>
                <th>投票通过</th>
                <th>有成员想入场</th>
                <th>总收益</th>
                <th>最大回撤</th>
                <th>夏普</th>
                <th>交易数</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in ensSweepPoints" :key="p.vote_threshold">
                <td>{{ fmtVote(p.vote_threshold) }}</td>
                <td>
                  {{ fmtVote(p.effective_vote) }}
                  <span class="muted">（{{ Math.round(p.effective_vote * 100) }}% 权重）</span>
                </td>
                <td>{{ p.entries_taken }} 次</td>
                <td>{{ p.entry_bars }} 根</td>
                <td>{{ p.signalled_bars }} 根</td>
                <td :class="toneOf(p.total_return)">{{ formatPercent(p.total_return) }}</td>
                <td>{{ formatPercent(p.max_drawdown) }}</td>
                <td>{{ formatNumber(p.sharpe) }}</td>
                <td>{{ p.number_of_trades }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="ensResult" style="margin-top: 12px">
        <p v-for="w in ensResult.warnings" :key="w" class="notice">{{ w }}</p>

        <p v-if="ensResult.trades.length === 0" class="notice">
          成员之间在本次数据上<b>没有产生任何认同</b>（票数从未严格超过阈值），所以组合没有开过仓 ——
          收益/回撤为 0 并非「稳健」，而是「没交易」。可以降低阈值、换用信号重叠更多的成员，
          或放宽成员的入场条件。
          <template v-if="ensResult.agreement.signalled_bars > 0">
            实际有 {{ ensResult.agreement.signalled_bars }} 根 K 线至少有一个成员想入场，
            其中 {{ ensResult.agreement.solo_signalled_bars }} 根是<b>只有单个成员</b>想入场，
            这些全被投票否决。
          </template>
        </p>

        <div class="grid cols-4">
          <StatCard
            label="组合总收益"
            :value="formatPercent(ensResult.metrics.total_return)"
            :tone="toneOf(ensResult.metrics.total_return)"
            :sub="`期末权益 ${formatNumber(ensResult.final_equity)}`"
          />
          <StatCard
            label="组合最大回撤"
            :value="formatPercent(ensResult.metrics.max_drawdown)"
            :tone="toneOf(ensResult.metrics.max_drawdown)"
            sub="越小越好"
          />
          <StatCard
            label="组合夏普"
            :value="formatNumber(ensResult.metrics.sharpe)"
            :tone="toneOf(ensResult.metrics.sharpe)"
            :sub="`评估 ${ensResult.bars_evaluated} 根`"
          />
          <StatCard
            label="认同并开仓"
            :value="String(ensResult.agreement.entries_taken)"
            :sub="`票数过阈值 ${ensResult.agreement.entry_bars} 根 · 交易 ${ensResult.trades.length} 笔`"
          />
        </div>

        <p class="muted" style="margin-top: 6px">
          有成员想入场的有 {{ ensResult.agreement.signalled_bars }} 根，其中票数严格过阈值的
          {{ ensResult.agreement.entry_bars }} 根（支持率
          {{ formatPercent(ensResult.agreement.entry_support_rate) }}）；只有单个成员想入场的
          {{ ensResult.agreement.solo_signalled_bars }} 根，这些是<b>被投票否决</b>的信号 ——
          如果大部分信号都属于这一类，说明成员之间几乎没有共识，而不是「信号很干净」。
        </p>

        <h4 style="margin: 12px 0 4px">集成组合与各成员（同口径）权益曲线</h4>
        <MultiLineChart :series="ensEquitySeries" height="280px" />
        <p class="muted" style="margin-top: 6px">
          每条线都由服务端用<b>同一个模拟器</b>在同一批 {{ ensResult.bars_evaluated }} 根 bar
          上算出来，成本模型、成交规则与组合完全一致，所以可以直接比较：粗线是集成组合，细线是
          各成员<span v-if="ensChartOmittedMembers > 0"
            >（图中只画了前 {{ ENS_CHART_MEMBERS }} 个，另有
            {{ ensChartOmittedMembers }} 个见下表）</span
          >。每个成员用的是<b>它自己那份资金</b>（{{ ensResult.initial_capital }} × 权重）的独立账户，
          即「如果只让这一个成员交易会怎样」，<b>不是</b>组合同时持有了这些仓位 —— 引擎一次只持有
          一个仓位。它回答「投票有没有比单干更好」，不回答「某个成员在投票运行时贡献了多少」。
        </p>

        <table style="margin-top: 10px">
          <thead>
            <tr>
              <th>对象</th>
              <th>权重</th>
              <th>自身触发</th>
              <th>认同/否决</th>
              <th>总收益</th>
              <th>最大回撤</th>
              <th>夏普</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><b>集成组合</b></td>
              <td class="muted">—</td>
              <td>{{ ensResult.agreement.entries_taken }} 次开仓</td>
              <td class="muted">
                {{ ensResult.agreement.signalled_bars }} → {{ ensResult.agreement.entry_bars }}
              </td>
              <td :class="toneOf(ensResult.metrics.total_return)">
                {{ formatPercent(ensResult.metrics.total_return) }}
              </td>
              <td>{{ formatPercent(ensResult.metrics.max_drawdown) }}</td>
              <td>{{ formatNumber(ensResult.metrics.sharpe) }}</td>
            </tr>
            <tr v-for="m in ensResult.members" :key="m.label">
              <td class="muted">
                {{ m.label }}
                <span v-if="!memberRunFor(m.label)" class="muted" style="margin-left: 4px"
                  >（无同口径跑分，回退到历史回测）</span
                >
              </td>
              <td>{{ m.weight.toFixed(2) }}</td>
              <td>{{ memberCompareRow(m.label).entries ?? '—' }} 根</td>
              <td>
                {{ m.entry_agreed }} 认同 / {{ m.solo_entries }} 单独
                <span class="muted" v-if="m.entry_support_rate !== null">
                  （{{ formatPercent(m.entry_support_rate) }}）</span
                >
              </td>
              <td :class="toneOf(memberCompareRow(m.label).total_return)">
                {{ formatPercent(memberCompareRow(m.label).total_return) }}
              </td>
              <td>{{ formatPercent(memberCompareRow(m.label).max_drawdown) }}</td>
              <td>{{ formatNumber(memberCompareRow(m.label).sharpe) }}</td>
            </tr>
          </tbody>
        </table>
        <p class="muted" style="margin-top: 6px">
          「自身触发」是各成员自己在同一批 bar 上开出的仓位数；集成那行是组合真正开出的仓位数
          （持仓期间的重复触发不算新仓），因此它必然不超过成员中最少的那个。「认同/否决」里，
          <b>认同</b>是该成员的信号中票数过阈值的根数，<b>单独</b>是只有它一个人想入场、被投票
          否决的根数，括号内为认同占自身触发的比例。成员的收益/回撤/夏普取自<b>本次集成自己算出的
          同口径跑分</b>，与组合完全可比；若某成员没有该跑分（旧版服务端），则回退到它最近一次
          已完成回测并标注，缺失时显示 <code>—</code>。
        </p>
      </div>
    </div>
    </template>

    <div v-if="detail && (detail.warnings?.length ?? 0) > 0" class="card" style="margin-top: 14px">
      <h4>这次回测的提醒</h4>
      <p class="muted">
        引擎在算之前就想说的事。它们跟着结果一起存下来了，所以刷新页面也还在 ——
        别把这些数字当成一次干净的回测。
      </p>
      <p v-for="(w, i) in detail.warnings" :key="i" class="notice">{{ w }}</p>
    </div>

    <div v-if="detail" class="card" style="margin-top: 14px">
      <h3>权益曲线</h3>
      <EquityChart :points="detail.equity_curve" />
    </div>

    <div v-if="detail" class="card" style="margin-top: 14px">
      <h3>回撤曲线</h3>
      <MultiLineChart :series="drawdownSeries" height="220px" />
    </div>

    <!-- AI 汇总整个研究结果（评审 §13；ADR-129）：结论、原因、风险、可信程度、下一步。
         AI 只解释引擎算出来的事实，所以缺 AI 时这一页的结论照样成立。 -->
    <div v-if="detail" class="card" style="margin-top: 14px">
      <h3>AI 汇总（只解释已有数字，不重新计算）</h3>
      <div class="row" style="margin-bottom: 10px">
        <button :disabled="explainingBt" @click="explainCurrent">
          {{ explainingBt ? '解读中…' : '生成解读' }}
        </button>
        <span v-if="btExplanation?.cached" class="muted">缓存命中，未产生费用</span>
      </div>
      <div v-if="btExplanation">
        <h4>① 结论</h4>
        <p>{{ btExplanation.explanation.summary }}</p>
        <p v-if="btExplanation.explanation.plain_language" class="muted">
          {{ btExplanation.explanation.plain_language }}
        </p>

        <h4>② 原因</h4>
        <ul v-if="aiDrivers.length" class="answer-list">
          <li v-for="(driver, idx) in aiDrivers" :key="idx">{{ driver }}</li>
        </ul>
        <p v-else class="muted">这次解读没有给出原因。没有原因就不编一个。</p>

        <h4>③ 风险</h4>
        <ul v-if="aiRisks.length" class="answer-list">
          <li v-for="(risk, idx) in aiRisks" :key="idx">{{ risk }}</li>
        </ul>
        <p v-else class="muted">
          这次解读没有给出风险条目；上面「最大风险」那段是引擎读数，它一直有效。
        </p>

        <h4>④ 可信程度</h4>
        <p>{{ evidenceSentence }}</p>
        <ul v-if="btExplanation.explanation.what_could_invalidate?.length" class="answer-list">
          <li v-for="(item, idx) in btExplanation.explanation.what_could_invalidate" :key="idx">
            什么会让它失效：{{ item }}
          </li>
        </ul>

        <h4>⑤ 下一步</h4>
        <ul v-if="btExplanation.explanation.what_to_watch_next.length" class="answer-list">
          <li v-for="(item, idx) in btExplanation.explanation.what_to_watch_next" :key="idx">
            {{ item }}
          </li>
        </ul>
        <p v-else>{{ nextStep.text }}</p>
      </div>
      <p v-else class="muted">
        尚未生成解读。未配置 AI 时这个按钮不可用，上面的结论、风险与可信程度都不受影响。
      </p>
    </div>

    <div v-if="compareResult" class="card" style="margin-top: 14px">
      <h3>回测对比</h3>
      <table>
        <thead>
          <tr>
            <th>回测 #</th>
            <th v-for="m in compareResult.metrics" :key="m">{{ metricKeyLabel(m) }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in compareResult.runs" :key="r.run_id">
            <td>{{ r.run_id }}</td>
            <td v-for="m in compareResult.metrics" :key="m" :class="toneOf(r[m])">
              {{ formatMetric(m, r[m]) }}
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
        <p v-else class="muted">还没有回测记录。到「我的策略」创建策略后即可运行。</p>
        <div class="row" style="margin-top: 8px">
          <button :disabled="comparing || compareIds.length < 2" @click="runCompare">
            {{ comparing ? '对比中…' : `对比选中（${compareIds.length}）` }}
          </button>
        </div>
      </div>

      <div v-if="isAdvanced" class="card">
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
          </tbody>
        </table>
        <p v-else class="muted">选择一条回测记录查看详情。</p>
      </div>

      <div class="card" style="margin-top: 14px">
        <h3>查看假设</h3>
        <table v-if="detail">
          <tbody>
            <tr>
              <td>成交模型</td>
              <td>{{ detail.execution_model.fill_model }}</td>
            </tr>
            <tr>
              <td>订单类型</td>
              <td>
                {{ detail.execution_model.entry_order_type || 'market' }}（有效期
                {{ detail.execution_model.order_valid_bars ?? 1 }} bar）
              </td>
            </tr>
            <tr>
              <td>手续费</td>
              <td>{{ detail.execution_model.fee_bps }} bps</td>
            </tr>
            <tr>
              <td>滑点</td>
              <td>{{ detail.execution_model.slippage_bps }} bps</td>
            </tr>
          </tbody>
        </table>
        <p class="muted">
          这四项就是这次回测实际使用的成交假设：换个手续费或滑点，同一批信号会给出不同的成交与收益，
          所以它们和上面的结果哈希一起构成这次回测的复现记录。
        </p>
      </div>
    </div>

    <!-- 评审 §10 的二级指标：不再默认铺满页面，但一次点击就全部打开（ADR-128）。 -->
    <div v-if="detail && metricRows.length" class="card" style="margin-top: 14px">
      <div class="row" style="justify-content: space-between">
        <h3>查看详细分析（二级指标）</h3>
        <button class="ghost" @click="showDetailAnalytics = !showDetailAnalytics">
          {{ showDetailAnalytics ? '收起详细分析' : '查看详细分析' }}
        </button>
      </div>
      <p class="muted">
        夏普比率、索提诺比率、年化复合收益率、每笔交易期望收益、持仓时间占比都在这里，默认收起是为了
        让第一屏只说结论。逐笔的 R 倍数、MAE、MFE 在下面的「交易明细」里（
        <a href="#trades">跳到交易明细</a>）。
      </p>
      <table v-if="showDetailAnalytics">
        <thead>
          <tr>
            <th>指标</th>
            <th>值</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in metricRows" :key="m.key">
            <td>{{ metricKeyLabel(m.key) }}</td>
            <td :class="toneOf(m.value)">{{ m.value != null ? formatMetric(m.key, m.value) : 'N/A' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="detail?.trades.length" id="trades" class="card" style="margin-top: 14px">
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
