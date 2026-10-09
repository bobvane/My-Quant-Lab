<script setup lang="ts">
/**
 * 实验中心（/experiments）：列表 / 新建 / 详情 / 比较 四块都在这一页。
 *
 * 这一页的三条底线（ADR-182 / ADR-183）：
 * ① 只显示服务端存下来的数字。引擎没给的读数写「未知」，不拿 0 或占位值顶上，
 *    也不在这里根据别的字段重算——比较的结论由服务端比较已存的配置得出。
 * ② 每个 API 调用都自己 try/catch，失败就把 `(e as Error).message` 显示在事发那块；
 *    按钮在忙的时候禁用，并写明在忙什么（ADR-138：点不动的按钮必须说明为什么）。
 * ③ 状态词一律中文；原始编号 / `result_hash` / 参数 JSON 原样读数只在高级模式出现。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import {
  ApiError,
  api,
  type Asset,
  type BacktestAnalysis,
  type ExperimentCompareOut,
  type ExperimentCreatePayload,
  type ExperimentDetailOut,
  type ExperimentResultOut,
  type ExperimentSummaryOut,
  type Strategy,
  type StrategyVersion,
} from '../api'
import { formatDateTime, formatMetric, formatNumber, formatPercent, toneOf } from '../format'
import { isAdvanced } from '../mode'
import { metricKeyLabel, timeframeLabel } from '../wording'

const router = useRouter()

// ---- 词表与取值守卫 ---------------------------------------------------------

/** 服务端 `ExperimentStatus` 的五个取值。wording.ts 里没有实验状态词，先在本页收着。 */
const STATUS_LABELS: Record<string, string> = {
  draft: '草稿（还没跑）',
  running: '运行中',
  completed: '已完成',
  failed: '没有跑完',
  archived: '已归档',
}

const STATUS_FILTERS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '', label: '全部（含已归档）' },
  { value: 'draft', label: '草稿' },
  { value: 'running', label: '运行中' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '没有跑完' },
  { value: 'archived', label: '已归档' },
]

/** 服务端 `ExperimentCreate.kind`；这一页只发起回测类实验，其余四种留给「研究实验室」。 */
const KIND_LABELS: Record<string, string> = {
  backtest: '跑一次回测',
  sensitivity: '参数敏感性',
  monte_carlo: '成交重采样',
  walk_forward: '滚动前进',
  oos: '样本外检验',
}

/**
 * 逐条结果表读的键：先是服务端 `COMPARE_METRICS` 那五个平铺指标，
 * 再是实验层投影保证会有的三个（`final_equity` / `cagr` / `total_fees`）。
 * 只按这个固定顺序读；行里多出来的键不加列、也不重算。
 */
const RESULT_METRIC_KEYS: readonly string[] = [
  'total_return',
  'max_drawdown',
  'sharpe',
  'win_rate',
  'number_of_trades',
  'final_equity',
  'cagr',
  'total_fees',
]

const TIMEFRAME_CHOICES: ReadonlyArray<string> = ['1d', '1h', '4h', '1w']

function statusLabel(status: string | null | undefined): string {
  if (!status) return '未知'
  const label = STATUS_LABELS[status]
  if (label) return label
  return isAdvanced.value ? `未知（${status}）` : '未知'
}

function kindLabel(kind: string | null | undefined): string {
  if (!kind) return '未知'
  const label = KIND_LABELS[kind]
  if (label) return label
  return isAdvanced.value ? `未知（${kind}）` : '未知'
}

/** 界面上的「未知」是唯一允许的缺失写法：null / 缺字段都不许退化成 0 或占位符。 */
function textOr(value: unknown): string {
  if (value === null || value === undefined) return '未知'
  const text = String(value).trim()
  return text ? text : '未知'
}

function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

/** 只认非数组对象：能安全当字典读的东西。 */
function recordAt(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function listAt(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

/** 指标读数：服务端给的键照原样取；取不到返回 null，由调用处写成「未知」，绝不本地补算。 */
function numberAt(source: Record<string, unknown> | null | undefined, key: string): number | null {
  if (!source) return null
  const value = source[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function metricText(source: Record<string, unknown> | null | undefined, key: string): string {
  const value = numberAt(source, key)
  return value === null ? '未知' : formatMetric(key, value)
}

function dateTimeOr(value: string | null | undefined): string {
  if (!value) return '未知'
  const text = formatDateTime(value)
  return text === '—' ? '未知' : text
}

function strategyText(item: ExperimentSummaryOut): string {
  const name = item.strategy_name ? item.strategy_name : '未知'
  const version = item.version ? item.version : '未知'
  return `${name} / ${version}`
}

function symbolsText(item: ExperimentSummaryOut): string {
  const symbols = listAt(item.symbols)
    .map((value) => String(value))
    .filter((value) => value)
  if (symbols.length) return symbols.join('、')
  return item.symbol ? item.symbol : '未知'
}

function rangeText(item: ExperimentSummaryOut): string {
  const start = item.start_date
  const end = item.end_date
  if (!start && !end) return '未知'
  return `${start ?? '未知'} ~ ${end ?? '未知'}`
}

function resultCountText(item: ExperimentSummaryOut): string {
  return typeof item.result_count === 'number' ? String(item.result_count) : '未知'
}

// ---- ① 列表 -----------------------------------------------------------------

const experiments = ref<ExperimentSummaryOut[]>([])
const listLoading = ref(false)
const listError = ref('')
const listNotice = ref('')
const statusFilter = ref('')
const listLimit = ref(20)

const currentFilterLabel = computed(
  () => STATUS_FILTERS.find((item) => item.value === statusFilter.value)?.label ?? '全部（含已归档）',
)

async function loadExperiments() {
  listLoading.value = true
  listError.value = ''
  try {
    const response = await api.experiments(listLimit.value, undefined, statusFilter.value || undefined)
    experiments.value = response.experiments ?? []
  } catch (e) {
    experiments.value = []
    listError.value = `读不到实验列表：${messageOf(e)}`
  } finally {
    listLoading.value = false
  }
}

// ---- ② 新建 -----------------------------------------------------------------

const showCreate = ref(true)
const choicesLoading = ref(false)
const choicesError = ref('')
const strategies = ref<Strategy[]>([])
const assets = ref<Asset[]>([])
const versions = ref<StrategyVersion[]>([])
const versionsLoading = ref(false)
const formError = ref('')
const formNotice = ref('')
/** '' = 没在提交；'draft' / 'run' = 正在提交哪一路。 */
const saving = ref<'' | 'draft' | 'run'>('')
const formName = ref('')
const formNotes = ref('')
const formStrategyId = ref('')
const formVersionId = ref('')
const formParameters = ref('')
const formSymbol = ref('')
const formTimeframe = ref('')
const formStart = ref('')
const formEnd = ref('')

async function loadChoices() {
  choicesLoading.value = true
  choicesError.value = ''
  const failures: string[] = []
  try {
    const [strategyRows, assetRows] = await Promise.all([
      api.strategies().catch(() => {
        failures.push('策略列表')
        return [] as Strategy[]
      }),
      api.assets().catch(() => {
        failures.push('标的列表')
        return [] as Asset[]
      }),
    ])
    strategies.value = strategyRows
    assets.value = assetRows.filter((asset) => asset.is_active)
    if (failures.length) {
      choicesError.value = `${failures.join('、')}读取失败：新建实验时这两项要么手填、要么点「重新读取选项」再试一次。`
    }
  } catch (e) {
    choicesError.value = `读不到新建实验需要的选项：${messageOf(e)}`
  } finally {
    choicesLoading.value = false
  }
}

function pickCurrentVersionVersion(rows: StrategyVersion[]): string {
  const current = rows.find((row) => row.is_current)
  if (current) return String(current.id)
  return rows.length ? String(rows[0].id) : ''
}

async function onStrategyChange() {
  const raw = formStrategyId.value
  formVersionId.value = ''
  versions.value = []
  formError.value = ''
  if (!raw) return
  versionsLoading.value = true
  try {
    const rows = await api.strategyVersions(Number(raw))
    versions.value = rows
    formVersionId.value = pickCurrentVersionVersion(rows)
  } catch (e) {
    formError.value = `读不到这个策略的版本：${messageOf(e)}`
  } finally {
    versionsLoading.value = false
  }
}

const selectedVersion = computed<StrategyVersion | null>(() => {
  const id = Number(formVersionId.value)
  if (!id) return null
  return versions.value.find((row) => row.id === id) ?? null
})

/**
 * 初始资金**不在** `POST /experiments` 的请求体里：`ExperimentCreate` 没有这个键
 * （服务端 `extra="forbid"`，多发一键就是 422）。它来自这一版策略自己的
 * `execution.initial_capital`，服务端在实验建立那一刻把它冻结进记录。这里只如实读出来。
 */
const frozenCapital = computed<number | null>(() => {
  const execution = recordAt(recordAt(selectedVersion.value?.dsl)?.['execution'])
  const value = execution ? execution['initial_capital'] : null
  return typeof value === 'number' && Number.isFinite(value) ? value : null
})

const frozenCapitalText = computed(() =>
  frozenCapital.value === null ? '未知' : formatNumber(frozenCapital.value, 2),
)

interface JsonCheck {
  value: Record<string, unknown> | null
  problem: string
}

/** 参数框：留空 = 用这一版策略自己的参数；非空就必须是一个 JSON 对象。 */
function parseParameters(text: string): JsonCheck {
  const trimmed = text.trim()
  if (!trimmed) return { value: null, problem: '' }
  let parsed: unknown
  try {
    parsed = JSON.parse(trimmed)
  } catch {
    return {
      value: null,
      problem: '不是合法的 JSON：按示例写成一个对象，例如 {"risk_pct": 2}。留空表示用这一版策略自己的参数。',
    }
  }
  const record = recordAt(parsed)
  if (!record) {
    return {
      value: null,
      problem: '要写成用 { } 包起来的对象，不能是数组或单个值。留空表示用这一版策略自己的参数。',
    }
  }
  return { value: record, problem: '' }
}

const parametersCheck = computed(() => parseParameters(formParameters.value))

/** 点不动「保存」的原因，逐条写清楚（ADR-138）。空串 = 可以提交。 */
const createBlockedReason = computed<string>(() => {
  if (choicesLoading.value) return '正在读取策略与标的列表，读完就能提交。'
  if (versionsLoading.value) return '正在读取这个策略的版本。'
  if (!formStrategyId.value) return '先选策略，再选版本：实验必须绑定某一版策略。'
  if (!selectedVersion.value) return '这一版策略还没选好：等版本列表读出来，或重选一次策略。'
  if (!formSymbol.value.trim()) return '要写一个标的代码：服务端按「标的 + 周期」去找那段行情数据。'
  if (parametersCheck.value.problem) return `参数不能提交：${parametersCheck.value.problem}`
  if (formStart.value && formEnd.value && formStart.value > formEnd.value) {
    return '开始日期比结束日期晚：这样取不到任何数据，先把日期改过来。'
  }
  return ''
})

function defaultExperimentName(version: StrategyVersion): string {
  const strategy = strategies.value.find((row) => row.id === version.strategy_id)
  const parts = [strategy ? strategy.name : `策略 #${version.strategy_id}`, version.version]
  const symbol = formSymbol.value.trim().toUpperCase()
  if (symbol) parts.push(symbol)
  return `${parts.join(' · ')} 实验`
}

/** 只发需要发的键：服务端按 `model_fields_set` 判断「这次点名要了什么」。 */
function buildPayload(draft: boolean): ExperimentCreatePayload | null {
  const version = selectedVersion.value
  if (!version) return null
  const payload: ExperimentCreatePayload = {
    name: (formName.value.trim() || defaultExperimentName(version)).slice(0, 120),
    kind: 'backtest',
    strategy_version_id: version.id,
  }
  const notes = formNotes.value.trim()
  if (notes) payload.notes = notes
  const symbol = formSymbol.value.trim().toUpperCase()
  if (symbol) payload.symbol = symbol
  const timeframe = formTimeframe.value
  if (timeframe) payload.timeframe = timeframe
  const start = formStart.value
  if (start) payload.start = start
  const end = formEnd.value
  if (end) payload.end = end
  const parameters = parametersCheck.value.value
  if (parameters) payload.parameters = parameters
  // 初始资金故意不发（见 frozenCapital 上面的说明）。
  // 也只有真的要存草稿时才发 draft：有没有这个键本身就是「要不要现在跑」。
  if (draft) payload.draft = true
  return payload
}

function describeCreated(created: ExperimentDetailOut): string {
  if (created.status === 'failed') {
    return `实验「${created.name}」记下来了，但引擎这次没有跑完：${
      created.error_message ?? '服务端没有给出原因。'
    }`
  }
  if (created.status === 'running') {
    return `实验「${created.name}」已经开始运行：结果出来之前状态会一直是「运行中」，过一会儿点列表里的「查看」看结果。`
  }
  if (created.status === 'completed') {
    return `实验「${created.name}」已经跑完了，结果就在下面的详情里。`
  }
  return `实验「${created.name}」记下来了，当前状态：${statusLabel(created.status)}。`
}

function createFailureMessage(e: unknown, draft: boolean): string {
  const what = draft ? '草稿没有存下来' : '实验没有建成'
  if (e instanceof ApiError && e.status === 422) {
    return `${what}：服务端拒绝了这次输入——${e.message}。这次请求没有写入任何记录，改完上面的字段再试一次。`
  }
  return `${what}：${messageOf(e)}`
}

async function submitCreate(draft: boolean) {
  if (saving.value || createBlockedReason.value) return
  const payload = buildPayload(draft)
  if (!payload) return
  saving.value = draft ? 'draft' : 'run'
  formError.value = ''
  formNotice.value = ''
  try {
    const created = await api.createExperiment(payload)
    await loadExperiments()
    detail.value = created
    detailError.value = ''
    formNotice.value = draft
      ? `草稿「${created.name}」已经存下了：服务端只冻结了这次的配置，没有跑任何量化代码。下面打开的就是它。`
      : describeCreated(created)
  } catch (e) {
    formError.value = createFailureMessage(e, draft)
  } finally {
    saving.value = ''
  }
}

// ---- ③ 详情 -----------------------------------------------------------------

const detail = ref<ExperimentDetailOut | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
const busyRunId = ref<number | null>(null)
const archivingId = ref<number | null>(null)

async function openDetail(id: number) {
  if (detailLoading.value) return
  detailLoading.value = true
  detailError.value = ''
  accountError.value = ''
  accountNotice.value = ''
  try {
    detail.value = await api.experiment(id)
    await loadRunAnalysis(detail.value)
  } catch (e) {
    detail.value = null
    detailError.value =
      e instanceof ApiError && e.status === 404
        ? '这条实验后端已经找不到了（可能刚被删掉）：刷新列表后重新选一条。'
        : `读不到这条实验：${messageOf(e)}`
  } finally {
    detailLoading.value = false
  }
}

/**
 * Phase C：这条实验背后的那条回测的绩效 / 风险 / 对照（docs/30 §9.2）。
 *
 * 实验自己不算任何东西：收养来的实验只是逐字复制了那条回测的结果（ADR-183），
 * 所以「比简单持有好吗」这个问题要去问那条回测的分析端点。拿不到分析（老数据、
 * 敏感性 / Walk-Forward 型实验没有回测）就什么都不显示，不猜、也不拿 0 顶替。
 */
const analysis = ref<BacktestAnalysis | null>(null)

async function loadRunAnalysis(current: ExperimentDetailOut | null) {
  analysis.value = null
  const runId = current?.backtest_run_id
  if (runId === null || runId === undefined) return
  try {
    analysis.value = await api.backtestAnalysis(runId)
  } catch {
    analysis.value = null
  }
}

const detailResults = computed<ExperimentResultOut[]>(() => detail.value?.results ?? [])

/** 结果区那八个读数；服务端没给的写「未知」。 */
const HEADLINE_METRICS: ReadonlyArray<{ label: string; keys: readonly string[] }> = [
  { label: '最终资产', keys: ['final_equity'] },
  { label: '总收益', keys: ['total_return'] },
  { label: '年化', keys: ['cagr'] },
  { label: '最大回撤', keys: ['max_drawdown'] },
  { label: 'Sharpe', keys: ['sharpe'] },
  { label: '胜率', keys: ['win_rate'] },
  { label: '交易次数', keys: ['number_of_trades'] },
  { label: '手续费', keys: ['total_fees'] },
]

interface HeadlineRow {
  label: string
  key: string
  text: string
  missing: boolean
}

/**
 * 取值顺序：第一条结果行（引擎为这次运行存下的完整指标）→ 实验行上平铺的那五个 →
 * `summary` 顶层（`final_equity` / `initial_capital` 在这里）。三个桶都没有就写「未知」。
 */
const headlineRows = computed<HeadlineRow[]>(() => {
  const current = detail.value
  const firstResult = (current?.results ?? [])[0] ?? null
  const buckets: Array<Record<string, unknown> | null> = [
    firstResult ? firstResult.metrics : null,
    current ? current.metrics : null,
    current ? current.summary : null,
  ]
  return HEADLINE_METRICS.map((item) => {
    for (const key of item.keys) {
      for (const bucket of buckets) {
        const value = numberAt(bucket, key)
        if (value !== null) {
          return { label: item.label, key, text: formatMetric(key, value), missing: false }
        }
      }
    }
    return { label: item.label, key: item.keys[0] ?? '', text: '未知', missing: true }
  })
})

/**
 * 手续费由服务端算：它把这次回测**已经存下来的**逐笔成交费用加起来投影成 `total_fees`
 * （收养来的实验在收养时算好），这一页只显示。值为 null（或键缺失）就照旧写「未知」。
 */
const totalFeesMissing = computed(() =>
  headlineRows.value.some((row) => row.key === 'total_fees' && row.missing),
)

const parametersInline = computed(() => {
  const parameters = detail.value ? recordAt(detail.value.parameters) : null
  if (!parameters) return '未知'
  const entries = Object.entries(parameters)
  if (!entries.length) return '（没有参数：用这一版策略自己的默认值）'
  return entries.map(([key, value]) => `${key} = ${JSON.stringify(value)}`).join('，')
})

const parametersRaw = computed(() => {
  const parameters = detail.value ? detail.value.parameters : null
  return parameters ? JSON.stringify(parameters, null, 2) : '（没有参数）'
})

const requestRaw = computed(() => {
  const request = detail.value ? detail.value.request : null
  return request ? JSON.stringify(request, null, 2) : '（服务端没有存这次的请求体）'
})

const resultHashText = computed(() => {
  const summary = detail.value ? recordAt(detail.value.summary) : null
  const hash = summary ? summary['result_hash'] : null
  return typeof hash === 'string' && hash ? hash : '未知'
})

const archivedText = computed(() => {
  const current = detail.value
  if (!current) return '未知'
  if (current.status === 'archived') return dateTimeOr(current.archived_at)
  return '还没有归档'
})

const detailCapitalText = computed(() => {
  const value = detail.value?.initial_capital
  return typeof value === 'number' && Number.isFinite(value) ? formatNumber(value, 2) : '未知'
})

const detailTimeframeText = computed(() => {
  const value = detail.value?.timeframe
  if (!value) return '未知'
  return timeframeLabel(value)
})

// ---- 运行 / 归档（列表行与详情共用） ----------------------------------------

function canRun(item: ExperimentSummaryOut): boolean {
  return item.status === 'draft' || item.status === 'failed'
}

function runDisabledReason(item: ExperimentSummaryOut): string {
  if (item.status === 'running') return '正在运行，等它跑完'
  if (item.status === 'completed') return '已经跑完了，不能重跑；要换参数就新建一次实验'
  if (item.status === 'archived') return '已归档，归档的实验不能运行'
  if (item.status === 'draft') return ''
  return `当前状态（${statusLabel(item.status)}）不能运行`
}

function runBlockedText(item: ExperimentSummaryOut): string {
  if (busyRunId.value !== null && busyRunId.value !== item.id) return '正在等另一条实验运行结束'
  if (archivingId.value !== null) return '正在归档另一条实验'
  if (!canRun(item)) return runDisabledReason(item)
  return ''
}

function canArchive(item: ExperimentSummaryOut): boolean {
  return item.status !== 'archived' && item.status !== 'running'
}

function archiveDisabledReason(item: ExperimentSummaryOut): string {
  if (item.status === 'archived') return '已经归档过了'
  if (item.status === 'running') return '正在运行，等它跑完再归档'
  return '状态未知，先刷新列表'
}

function archiveBlockedText(item: ExperimentSummaryOut): string {
  if (busyRunId.value !== null) return '正在等一条实验运行结束'
  if (archivingId.value !== null && archivingId.value !== item.id) return '正在归档另一条实验'
  if (!canArchive(item)) return archiveDisabledReason(item)
  return ''
}

function archiveConfirmText(item: ExperimentSummaryOut): string {
  const lines = [
    `把实验「${item.name}」归档。`,
    '',
    '归档不是删除：这条实验会留在实验列表里（状态显示「已归档」），它背后那次回测运行、结果和成交明细也都保留在「回测」页里，随时能回读。',
    '归档之后服务端不允许它再运行；而且现在没有「取消归档」的接口，所以它会一直带着「已归档」这个标记（要彻底移走它只能用删除）。',
  ]
  if (statusFilter.value) {
    lines.push('', `注意：现在列表筛选的是「${currentFilterLabel.value}」，归档后这一行会从现在这个筛选里消失，切到「全部（含已归档）」还能看到它。`)
  }
  return lines.join('\n')
}

async function runExperiment(item: ExperimentSummaryOut) {
  if (busyRunId.value !== null || archivingId.value !== null || !canRun(item)) return
  busyRunId.value = item.id
  listError.value = ''
  listNotice.value = ''
  try {
    const updated = await api.runExperiment(item.id)
    listNotice.value = describeCreated(updated)
    if (detail.value && detail.value.id === item.id) detail.value = updated
    await loadExperiments()
  } catch (e) {
    listError.value = `运行实验「${item.name}」没有成功：${messageOf(e)}`
  } finally {
    busyRunId.value = null
  }
}

async function archiveExperiment(item: ExperimentSummaryOut) {
  if (archivingId.value !== null || busyRunId.value !== null || !canArchive(item)) return
  if (!window.confirm(archiveConfirmText(item))) return
  archivingId.value = item.id
  listError.value = ''
  listNotice.value = ''
  try {
    const updated = await api.archiveExperiment(item.id)
    if (detail.value && detail.value.id === item.id) detail.value = updated
    await loadExperiments()
    listNotice.value =
      statusFilter.value && statusFilter.value !== 'archived'
        ? `实验「${item.name}」已归档。当前筛选是「${currentFilterLabel.value}」，所以它可能不在上面的列表里；切到「全部（含已归档）」就能看到它。`
        : `实验「${item.name}」已归档：它还留在列表里，状态显示「已归档」。`
  } catch (e) {
    listError.value = `归档实验「${item.name}」没有成功：${messageOf(e)}`
  } finally {
    archivingId.value = null
  }
}

// ---- ④ 比较 -----------------------------------------------------------------

const compareIds = ref<number[]>([])
const compare = ref<ExperimentCompareOut | null>(null)
const compareError = ref('')
const comparing = ref(false)

function isCompared(id: number): boolean {
  return compareIds.value.includes(id)
}

function toggleCompare(id: number) {
  const index = compareIds.value.indexOf(id)
  if (index >= 0) compareIds.value.splice(index, 1)
  else compareIds.value.push(id)
  // 选中集合一变，上次的比较结果就不再对应，直接作废，免得看着旧的往下判断。
  compare.value = null
  compareError.value = ''
}

function clearCompare() {
  compareIds.value = []
  compare.value = null
  compareError.value = ''
}

async function runCompare() {
  if (comparing.value || compareIds.value.length < 2) return
  comparing.value = true
  compareError.value = ''
  try {
    compare.value = await api.compareExperiments([...compareIds.value])
  } catch (e) {
    compare.value = null
    compareError.value = `对比没有成功：${messageOf(e)}`
  } finally {
    comparing.value = false
  }
}

const compareRows = computed<Array<Record<string, unknown>>>(() => {
  const rows = compare.value?.experiments ?? []
  return rows.map((row) => recordAt(row) ?? {})
})

const compareMetrics = computed<readonly string[]>(() => compare.value?.metrics ?? [])

const compareDifferences = computed<readonly string[]>(() => compare.value?.differences ?? [])

function compareRowKey(row: Record<string, unknown>): string {
  const id = row['id']
  return typeof id === 'number' ? String(id) : compareColumnTitle(row)
}

function compareRowId(row: Record<string, unknown>): number | null {
  const id = row['id']
  return typeof id === 'number' ? id : null
}

function compareColumnTitle(row: Record<string, unknown>): string {
  const config = recordAt(row['config'])
  const name = textOr(row['name'])
  const version = config ? textOr(config['version']) : '未知'
  const symbols = config ? configSymbolsText(config) : '未知'
  return `${name}（${version} / ${symbols}）`
}

function configSymbolsText(config: Record<string, unknown> | null): string {
  if (!config) return '未知'
  const symbols = listAt(config['symbols'])
    .map((value) => String(value))
    .filter((value) => value)
  if (symbols.length) return symbols.join('、')
  const symbol = config['symbol']
  return typeof symbol === 'string' && symbol ? symbol : '未知'
}

function configStrategyText(row: Record<string, unknown>): string {
  const config = recordAt(row['config'])
  if (!config) return '未知'
  return `${textOr(config['strategy_name'])}（${textOr(config['version'])}）`
}

function configTimeframeText(row: Record<string, unknown>): string {
  const config = recordAt(row['config'])
  const timeframe = config ? config['timeframe'] : null
  if (typeof timeframe !== 'string' || !timeframe) return '未知'
  // 复用 wording.ts 的日线/小时线词表；没有词条时原样回传，不猜。
  const label = timeframeLabel(timeframe)
  return label === timeframe ? timeframe : `${label}（${timeframe}）`
}

function configRangeText(row: Record<string, unknown>): string {
  const config = recordAt(row['config'])
  if (!config) return '未知'
  const start = typeof config['start'] === 'string' ? config['start'] : ''
  const end = typeof config['end'] === 'string' ? config['end'] : ''
  if (!start && !end) return '未知'
  return `${start || '未知'} ~ ${end || '未知'}`
}

function configCapitalText(row: Record<string, unknown>): string {
  const config = recordAt(row['config'])
  const value = config ? config['initial_capital'] : null
  return typeof value === 'number' && Number.isFinite(value) ? formatNumber(value, 2) : '未知'
}

function configParametersText(row: Record<string, unknown>): string {
  const config = recordAt(row['config'])
  const parameters = config ? recordAt(config['parameters']) : null
  if (!parameters) return '未知'
  const entries = Object.entries(parameters)
  if (!entries.length) return '（没有参数）'
  return entries.map(([key, value]) => `${key} = ${JSON.stringify(value)}`).join('，')
}

function configRawText(row: Record<string, unknown>): string {
  const config = recordAt(row['config'])
  return config ? JSON.stringify(config, null, 2) : '（服务端没有给出配置）'
}

function compareMetricText(row: Record<string, unknown>, metric: string): string {
  const value = numberAt(row, metric)
  return value === null ? '未知' : formatMetric(metric, value)
}

function compareResultCountText(row: Record<string, unknown>): string {
  const value = row['result_count']
  return typeof value === 'number' ? String(value) : '未知'
}

function compareStatusText(row: Record<string, unknown>): string {
  const status = row['status']
  return statusLabel(typeof status === 'string' ? status : null)
}

// ---- 权益曲线 / 交易记录：用链接把用户送回「回测」页 --------------------------

/**
 * `/backtest?run_id=<id>` 直接打开这条实验跑过的那一次回测（ADR-200），
 * 所以不必再让用户在「回测记录」里按编号自己找。`strategy_version_id` / `symbol` /
 * `timeframe` 一并带上：它们是那一页的上下文，万一那次回测已被删除，
 * 落到的也还是一个跟这条实验说得通的状态。
 */
const backtestLink = computed(() => {
  const current = detail.value
  if (!current || current.backtest_run_id === null || current.backtest_run_id === undefined) return null
  const query: Record<string, string> = {
    strategy_version_id: String(current.strategy_version_id),
    run_id: String(current.backtest_run_id),
  }
  if (current.symbol) query.symbol = current.symbol
  if (current.timeframe) query.timeframe = current.timeframe
  return { path: '/backtest', query }
})

const backtestRunId = computed(() => detail.value?.backtest_run_id ?? null)

/**
 * `/signals?strategy_version_id=<id>` 把「信号」页收窄到这条实验用的那一版（ADR-201）：
 * 收窄在服务端做，不是把这一页取回来的信号本地筛一遍。读不到版本时退回不带参数的那一页，
 * 那仍然是这一页说得通的状态（看的是全部信号）。
 */
const signalsLink = computed(() =>
  detail.value?.strategy_version_id
    ? { path: '/signals', query: { strategy_version_id: String(detail.value.strategy_version_id) } }
    : '/signals',
)

// ---- 用这个实验创建模拟账户 --------------------------------------------------

const creatingAccount = ref(false)
const accountError = ref('')
const accountNotice = ref('')

const paperBlockedReason = computed<string>(() => {
  const current = detail.value
  if (!current) return '先在上面选一条实验。'
  if (creatingAccount.value) return '正在创建模拟账户…'
  if (current.backtest_run_id === null || current.backtest_run_id === undefined) {
    return '这条实验还没有对应的回测，先运行它。'
  }
  const capital = current.initial_capital
  if (typeof capital !== 'number' || !Number.isFinite(capital)) {
    return '这条实验没有记下初始资金，服务端也没规定该拿哪个数当本金：先运行它，或到「模拟」页手动建一个账户。'
  }
  return ''
})

async function createPaperFromDetail() {
  const current = detail.value
  if (!current || paperBlockedReason.value) return
  const runId = current.backtest_run_id
  const capital = current.initial_capital
  if (runId === null || runId === undefined || typeof capital !== 'number') return
  creatingAccount.value = true
  accountError.value = ''
  accountNotice.value = ''
  try {
    const created = await api.createPaperAccount(`${current.name} · 模拟`.slice(0, 120), capital, {
      backtestRunId: runId,
    })
    accountNotice.value = `已经用这条实验建好模拟账户（本金 ${formatNumber(capital, 2)}，绑定回测 #${runId}），正在打开它。`
    await router.push({ path: '/paper', query: { account: String(created.id) } })
  } catch (e) {
    accountError.value = `创建模拟账户没有成功：${messageOf(e)}`
  } finally {
    creatingAccount.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadExperiments(), loadChoices()])
})
</script>

<template>
  <div>
    <h1 class="page-title">实验</h1>
    <p class="page-sub">
      一次实验 = 一条留在服务端的记录：它冻住了当时的策略版本、参数、标的与时间范围。
      这一页可以看列表、新建、看详情、并排比较。每个数字都来自服务端存下来的结果，取不到就写「未知」。
    </p>

    <!-- ① 新建 -->
    <div class="card" style="margin-top: 14px">
      <div class="row" style="justify-content: space-between; align-items: center">
        <h3 style="margin: 0">新建实验</h3>
        <button class="ghost" @click="showCreate = !showCreate">
          {{ showCreate ? '收起' : '展开' }}
        </button>
      </div>

      <div v-if="showCreate" style="margin-top: 12px">
        <p v-if="choicesLoading" class="muted">正在读取策略与标的列表…</p>
        <p v-if="choicesError" class="error">{{ choicesError }}</p>
        <p v-if="formNotice" class="notice">{{ formNotice }}</p>
        <p v-if="formError" class="error">{{ formError }}</p>

        <div class="grid cols-2">
          <label style="display: block">
            <span class="muted">策略</span>
            <select v-model="formStrategyId" @change="onStrategyChange">
              <option value="">—— 选一个策略 ——</option>
              <option v-for="item in strategies" :key="item.id" :value="String(item.id)">
                {{ item.name }}
              </option>
            </select>
          </label>

          <label style="display: block">
            <span class="muted">策略版本</span>
            <select v-model="formVersionId" :disabled="!formStrategyId || versionsLoading">
              <option value="">
                {{ versionsLoading ? '正在读取版本…' : formStrategyId ? '—— 选一版 ——' : '先选策略' }}
              </option>
              <option v-for="item in versions" :key="item.id" :value="String(item.id)">
                {{ item.version }}{{ item.is_current ? '（当前版本）' : '' }} · {{ item.validation_status }}
              </option>
            </select>
          </label>

          <label style="display: block">
            <span class="muted">标的（标的 + 周期决定用哪一段行情）</span>
            <input v-model="formSymbol" list="experiment-symbol-options" placeholder="例如 AAPL" />
            <datalist id="experiment-symbol-options">
              <option v-for="asset in assets" :key="asset.id" :value="asset.symbol">
                {{ asset.display_name ?? asset.symbol }}
              </option>
            </datalist>
          </label>

          <label style="display: block">
            <span class="muted">周期（可选）</span>
            <select v-model="formTimeframe">
              <option value="">不指定：服务端按日线（1d）找行情</option>
              <option v-for="item in TIMEFRAME_CHOICES" :key="item" :value="item">{{ item }}</option>
            </select>
          </label>

          <label style="display: block">
            <span class="muted">开始日期（可选）</span>
            <input v-model="formStart" type="date" />
          </label>

          <label style="display: block">
            <span class="muted">结束日期（可选）</span>
            <input v-model="formEnd" type="date" />
          </label>
        </div>

        <label style="display: block; margin-top: 12px">
          <span class="muted">
            策略参数（JSON 对象，可留空 = 用这一版策略自己的参数）
          </span>
          <textarea
            v-model="formParameters"
            rows="5"
            style="min-height: 110px"
            placeholder='留空即可；要覆盖时写成一个对象，例如 {"risk_pct": 2}'
          ></textarea>
        </label>
        <p v-if="parametersCheck.problem" class="error">{{ parametersCheck.problem }}</p>
        <p v-else-if="formParameters.trim()" class="muted">参数是合法的 JSON 对象，会随这次请求一起发给服务端。</p>

        <div class="grid cols-2" style="margin-top: 12px">
          <label style="display: block">
            <span class="muted">实验名称（可留空，会自动起一个）</span>
            <input v-model="formName" placeholder="例如 均线交叉 · v1 · AAPL" />
          </label>
          <label style="display: block">
            <span class="muted">备注（可选）</span>
            <input v-model="formNotes" placeholder="这次想验证什么" />
          </label>
        </div>

        <p class="muted" style="margin-top: 12px">
          初始资金：<strong>{{ frozenCapitalText }}</strong>
          ——
          它由这一版策略自己的 <span class="mono">execution.initial_capital</span> 决定，在实验建立那一刻被服务端冻结；
          <span class="mono">POST /experiments</span> 的请求体里没有这个键（服务端 <span class="mono">extra="forbid"</span>，多发一键就是 422），所以这里只如实读出来，不能在这一页改。
        </p>

        <div class="row" style="margin-top: 12px">
          <button :disabled="!!createBlockedReason || !!saving" @click="submitCreate(true)">
            {{ saving === 'draft' ? '正在存草稿…' : '保存为草稿' }}
          </button>
          <button :disabled="!!createBlockedReason || !!saving" @click="submitCreate(false)">
            {{ saving === 'run' ? '正在创建并运行…' : '保存并运行' }}
          </button>
          <button class="ghost" :disabled="choicesLoading" @click="loadChoices">
            {{ choicesLoading ? '正在读取选项…' : '重新读取选项' }}
          </button>
        </div>
        <p v-if="createBlockedReason" class="muted" style="margin-top: 8px">
          现在还不能提交：{{ createBlockedReason }}
        </p>
        <p v-else class="muted" style="margin-top: 8px">
          「保存为草稿」只冻结这次的配置，不跑任何量化代码；「保存并运行」会立刻让引擎跑一次。
        </p>
      </div>
    </div>

    <!-- ② 列表 -->
    <div class="card" style="margin-top: 14px">
      <div class="row" style="justify-content: space-between; align-items: center">
        <h3 style="margin: 0">实验列表</h3>
        <div class="row">
          <label class="row" style="gap: 6px">
            <span class="muted">状态</span>
            <select v-model="statusFilter" style="width: auto" @change="loadExperiments">
              <option v-for="item in STATUS_FILTERS" :key="item.value" :value="item.value">
                {{ item.label }}
              </option>
            </select>
          </label>
          <label class="row" style="gap: 6px">
            <span class="muted">条数</span>
            <select v-model.number="listLimit" style="width: auto" @change="loadExperiments">
              <option :value="10">10</option>
              <option :value="20">20</option>
              <option :value="50">50</option>
            </select>
          </label>
          <button class="ghost" :disabled="listLoading" @click="loadExperiments">
            {{ listLoading ? '正在读取…' : '刷新' }}
          </button>
        </div>
      </div>

      <p v-if="listError" class="error" style="margin-top: 10px">{{ listError }}</p>
      <p v-if="listNotice" class="notice" style="margin-top: 10px">{{ listNotice }}</p>
      <p v-if="listLoading" class="muted" style="margin-top: 10px">正在读实验列表…</p>

      <div v-if="experiments.length" style="overflow-x: auto; margin-top: 10px">
        <table>
          <thead>
            <tr>
              <th>选</th>
              <th>名称</th>
              <th>策略 / 版本</th>
              <th>状态</th>
              <th>标的</th>
              <th>冻结的起止</th>
              <th>总收益</th>
              <th>最大回撤</th>
              <th>Sharpe</th>
              <th>结果条数</th>
              <th>创建时间</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in experiments" :key="item.id">
              <td>
                <input
                  type="checkbox"
                  style="width: auto"
                  :checked="isCompared(item.id)"
                  :aria-label="`把「${item.name}」加入比较`"
                  @change="toggleCompare(item.id)"
                />
              </td>
              <td>
                {{ item.name }}
                <span v-if="isAdvanced" class="muted mono">#{{ item.id }}</span>
                <div v-if="item.is_adopted" class="muted">（从一条已有回测收养来的）</div>
              </td>
              <td>
                {{ strategyText(item) }}
                <span v-if="isAdvanced" class="muted mono">版本 #{{
                  item.strategy_version_id ?? '未知'
                }}</span>
              </td>
              <td>
                <span class="badge" :class="{ archived: item.status === 'archived' }">
                  {{ statusLabel(item.status) }}
                </span>
              </td>
              <td>{{ symbolsText(item) }}</td>
              <td>{{ rangeText(item) }}</td>
              <td :class="toneOf(numberAt(item.metrics, 'total_return'))">
                {{ metricText(item.metrics, 'total_return') }}
              </td>
              <td>{{ metricText(item.metrics, 'max_drawdown') }}</td>
              <td>{{ metricText(item.metrics, 'sharpe') }}</td>
              <td>{{ resultCountText(item) }}</td>
              <td>{{ dateTimeOr(item.created_at) }}</td>
              <td>
                <button class="ghost" :disabled="detailLoading" @click="openDetail(item.id)">
                  {{ detailLoading ? '读取中…' : '查看' }}
                </button>
                <button
                  class="ghost"
                  :disabled="!canRun(item) || busyRunId !== null || archivingId !== null"
                  @click="runExperiment(item)"
                >
                  {{ busyRunId === item.id ? '运行中…' : '运行' }}
                </button>
                <button
                  class="ghost danger"
                  :disabled="!canArchive(item) || archivingId !== null || busyRunId !== null"
                  @click="archiveExperiment(item)"
                >
                  {{ archivingId === item.id ? '归档中…' : '归档' }}
                </button>
                <div v-if="runBlockedText(item)" class="muted" style="font-size: 12px">
                  {{ runBlockedText(item) }}
                </div>
                <div v-if="archiveBlockedText(item)" class="muted" style="font-size: 12px">
                  {{ archiveBlockedText(item) }}
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else-if="!listLoading" class="muted" style="margin-top: 10px">
        {{
          statusFilter
            ? `现在这个筛选（${currentFilterLabel}）里没有实验。`
            : '还没有任何实验。用上面的「新建实验」跑一次，或者到「回测」页把一条跑完的回测收养成实验。'
        }}
      </p>
    </div>

    <!-- ③ 详情 -->
    <div class="card" style="margin-top: 14px">
      <h3 style="margin: 0">实验详情</h3>
      <p v-if="detailError" class="error" style="margin-top: 10px">{{ detailError }}</p>
      <p v-if="detailLoading" class="muted" style="margin-top: 10px">正在读这条实验…</p>
      <p v-if="!detail && !detailLoading && !detailError" class="muted" style="margin-top: 10px">
        在上面的列表里点某一行的「查看」，这条实验的完整读数就展开在这里。
      </p>

      <template v-if="detail">
        <div class="row" style="justify-content: space-between; align-items: flex-start; margin-top: 10px">
          <div>
            <h2 style="margin: 0 0 4px">{{ detail.name }}</h2>
            <div class="muted">
              {{ kindLabel(detail.kind) }} ·
              <span class="badge" :class="{ archived: detail.status === 'archived' }">
                {{ statusLabel(detail.status) }}
              </span>
              · {{ strategyText(detail) }}
              <span v-if="isAdvanced" class="mono">
                （实验 #{{ detail.id }}，策略版本 #{{ detail.strategy_version_id }}）
              </span>
            </div>
          </div>
          <div class="row">
            <button
              :disabled="!canRun(detail) || busyRunId !== null || archivingId !== null"
              @click="runExperiment(detail)"
            >
              {{ busyRunId === detail.id ? '运行中…' : '运行这条实验' }}
            </button>
            <button
              class="ghost danger"
              :disabled="!canArchive(detail) || archivingId !== null || busyRunId !== null"
              @click="archiveExperiment(detail)"
            >
              {{ archivingId === detail.id ? '归档中…' : '归档' }}
            </button>
          </div>
        </div>
        <p v-if="runBlockedText(detail)" class="muted">{{ runBlockedText(detail) }}</p>
        <p v-if="archiveBlockedText(detail)" class="muted">{{ archiveBlockedText(detail) }}</p>

        <p v-if="detail.error_message" class="error" style="margin-top: 10px">
          ⚠️ 这条实验没有跑完，服务端给出的原因是：{{ detail.error_message }}
        </p>

        <div class="grid cols-3" style="margin-top: 12px">
          <div>
            <div class="muted">备注</div>
            <div>{{ detail.notes ? detail.notes : '没有备注' }}</div>
          </div>
          <div>
            <div class="muted">标的</div>
            <div>{{ symbolsText(detail) }}</div>
          </div>
          <div>
            <div class="muted">周期</div>
            <div>{{ detailTimeframeText }}</div>
          </div>
          <div>
            <div class="muted">冻结的起止</div>
            <div>{{ rangeText(detail) }}</div>
          </div>
          <div>
            <div class="muted">初始资金</div>
            <div>{{ detailCapitalText }}</div>
            <div class="muted" style="font-size: 12px">
              这个数字是创建实验时从策略版本的执行配置里冻结下来的，改策略默认值不会改它。
            </div>
          </div>
          <div>
            <div class="muted">结果条数</div>
            <div>{{ resultCountText(detail) }}</div>
          </div>
          <div>
            <div class="muted">创建时间</div>
            <div>{{ dateTimeOr(detail.created_at) }}</div>
          </div>
          <div>
            <div class="muted">跑完时间</div>
            <div>{{ detail.completed_at ? dateTimeOr(detail.completed_at) : '还没有跑完' }}</div>
          </div>
          <div>
            <div class="muted">归档时间</div>
            <div>{{ archivedText }}</div>
          </div>
        </div>

        <div style="margin-top: 12px">
          <div class="muted">参数</div>
          <div>{{ parametersInline }}</div>
          <template v-if="isAdvanced">
            <div class="muted" style="margin-top: 6px">参数原样读数</div>
            <pre class="code-block mono">{{ parametersRaw }}</pre>
            <div class="muted" style="margin-top: 6px">这次提交给服务端的请求体</div>
            <pre class="code-block mono">{{ requestRaw }}</pre>
            <div class="muted" style="margin-top: 6px">result_hash</div>
            <pre class="code-block mono">{{ resultHashText }}</pre>
          </template>
        </div>

        <h4>结果</h4>
        <div class="grid cols-4">
          <div v-for="row in headlineRows" :key="row.label" class="stat small">
            <div class="muted" style="font-size: 12px">{{ row.label }}</div>
            <div>{{ row.text }}</div>
          </div>
        </div>
        <p v-if="totalFeesMissing" class="muted" style="margin-top: 6px">
          「手续费」写「未知」，是因为这条实验存下来的手续费是空的（服务端给 null 时这一页不拿 0 顶替）。
          服务端算的是这次回测已经存下来的逐笔成交费用之和，这一页只显示，不重算、也不拿别的数字凑一个合计。
        </p>

        <!-- Phase C：实验背后那条回测的对照（docs/30 §9.2）。数字来自分析端点，
             这一页不重算；没有回测（敏感性 / Walk-Forward 型）就不显示。 -->
        <div v-if="analysis && analysis.benchmark" class="grid cols-4" style="margin-top: 8px">
          <div class="stat small">
            <div class="muted" style="font-size: 12px">
              对照收益（{{ analysis.benchmark.label }}）
            </div>
            <div>
              {{
                analysis.benchmark.total_return == null
                  ? '未知'
                  : formatPercent(analysis.benchmark.total_return)
              }}
            </div>
          </div>
          <div class="stat small">
            <div class="muted" style="font-size: 12px">超额收益（策略 − 对照）</div>
            <div :class="toneOf(analysis.performance.derived.excess_return)">
              {{
                analysis.performance.derived.excess_return == null
                  ? '未知'
                  : formatPercent(analysis.performance.derived.excess_return)
              }}
            </div>
          </div>
          <div class="stat small">
            <div class="muted" style="font-size: 12px">对照最大回撤</div>
            <div>
              {{
                analysis.benchmark.max_drawdown == null
                  ? '未知'
                  : formatPercent(analysis.benchmark.max_drawdown)
              }}
            </div>
          </div>
          <div class="stat small">
            <div class="muted" style="font-size: 12px">样本是否够</div>
            <div>{{ analysis.sample.tier_text }}</div>
          </div>
        </div>
        <p v-if="analysis && analysis.benchmark" class="muted" style="margin-top: 6px">
          对照是同一段区间、同样初始资金的「买入并一直拿着」，不含手续费与滑点，所以它是一把偏乐观的尺子。
          <RouterLink v-if="backtestLink" :to="backtestLink">到「回测」页看绩效 / 风险 / 对照明细</RouterLink>
        </p>

        <h4>逐条结果</h4>
        <p v-if="!detailResults.length" class="muted">
          这条实验没有存下任何结果行：草稿本来就是空的；没跑完的实验也不会有结果。
        </p>
        <div v-else style="overflow-x: auto">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>标签</th>
                <th>类型</th>
                <th v-for="key in RESULT_METRIC_KEYS" :key="key">{{ metricKeyLabel(key) }}</th>
                <th>回测记录</th>
                <th>建立时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in detailResults" :key="row.id">
                <td>
                  {{ index + 1 }}
                  <span v-if="isAdvanced" class="muted mono">#{{ row.id }}</span>
                </td>
                <td>{{ textOr(row.label) }}</td>
                <td>{{ kindLabel(row.kind) }}</td>
                <td
                  v-for="key in RESULT_METRIC_KEYS"
                  :key="key"
                  :class="key === 'total_return' ? toneOf(numberAt(row.metrics, key)) : ''"
                >
                  {{ metricText(row.metrics, key) }}
                </td>
                <td>
                  <template v-if="row.backtest_run_id !== null && row.backtest_run_id !== undefined">
                    <RouterLink v-if="backtestLink" :to="backtestLink">
                      到「回测」页看 #{{ row.backtest_run_id }}
                    </RouterLink>
                    <span v-else class="muted">#{{ row.backtest_run_id }}</span>
                  </template>
                  <span v-else class="muted">没有对应的回测</span>
                </td>
                <td>{{ dateTimeOr(row.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <template v-if="isAdvanced && detailResults.length">
          <div class="muted" style="margin-top: 6px">逐条结果的原始 payload</div>
          <pre class="code-block mono">{{ JSON.stringify(detailResults, null, 2) }}</pre>
        </template>

        <h4>接着看</h4>
        <ul class="answer-list">
          <li>
            <template v-if="backtestLink">
              <RouterLink :to="backtestLink">权益曲线</RouterLink>
              —— 到「回测」页直接打开这次回测 <strong>#{{ backtestRunId }}</strong>，
              权益曲线与下面的解读都在那一页上。
              <span class="muted">
                （地址里的 <span class="mono">?run_id={{ backtestRunId }}</span>
                指名的就是这一条回测：刷新或把地址发给别人，落到的还是它。）
              </span>
            </template>
            <span v-else class="muted">这条实验还没有对应的回测，先运行它，跑完就有权益曲线了。</span>
          </li>
          <li>
            <template v-if="backtestLink">
              <RouterLink :to="backtestLink">交易记录</RouterLink>
              —— 同一条回测，那一页往下就是这次运行的成交明细（每笔的手续费在那张表里）。
            </template>
            <span v-else class="muted">这条实验还没有对应的回测，先运行它，跑完就有成交明细了。</span>
          </li>
          <li>
            <RouterLink :to="signalsLink">到「信号」页</RouterLink>
            —— 这一页看的是「这一版策略现在还发不发信号」；这条实验用的是
            {{ strategyText(detail) }}。打开的是<strong>这一版</strong>的信号，
            下面的结果追踪也是同一版的范围。
            <span class="muted">
              （地址里的 <span class="mono">?strategy_version_id=…</span>
              把范围钉在这一版上：刷新或把地址发给别人，落到的还是它。）
            </span>
            <span v-if="isAdvanced">
              （服务端过滤：<span class="mono">GET /signals?strategy_version_id=</span>，
              不是把「信号」页取回来的那一页本地筛一遍 —— 本地筛会把更早的信号漏掉。）
            </span>
          </li>
          <li>
            <button :disabled="!!paperBlockedReason" @click="createPaperFromDetail">
              {{ creatingAccount ? '正在创建模拟账户…' : '用这个实验创建模拟账户' }}
            </button>
            <span v-if="paperBlockedReason" class="muted">{{ paperBlockedReason }}</span>
            <span v-else class="muted">
              会用这条实验记下的本金 {{ detailCapitalText }}
              建一个模拟账户，并绑定它的回测 #{{ backtestRunId }}。
            </span>
            <p v-if="accountError" class="error" style="margin: 6px 0 0">{{ accountError }}</p>
            <p v-if="accountNotice" class="notice" style="margin: 6px 0 0">{{ accountNotice }}</p>
          </li>
        </ul>
      </template>
    </div>

    <!-- ④ 比较 -->
    <div class="card" style="margin-top: 14px">
      <div class="row" style="justify-content: space-between; align-items: center">
        <h3 style="margin: 0">比较</h3>
        <div class="row">
          <span class="muted">已勾选 {{ compareIds.length }} 条</span>
          <button :disabled="compareIds.length < 2 || comparing" @click="runCompare">
            {{ comparing ? '比较中…' : `比较选中的 ${compareIds.length} 条` }}
          </button>
          <button class="ghost" :disabled="!compareIds.length || comparing" @click="clearCompare">
            清空选择
          </button>
        </div>
      </div>

      <p v-if="compareIds.length < 2" class="muted" style="margin-top: 8px">
        在上面的列表里勾选两条以上（勾选框在每行最左边），再点「比较」。勾选集合一变，上一次的比较结果就作废。
      </p>
      <p v-if="compareError" class="error" style="margin-top: 8px">{{ compareError }}</p>

      <template v-if="compare">
        <div v-if="compare.comparability === 'different-config'" class="notice warn" style="margin-top: 10px">
          <strong>这些实验的条件并不相同，指标高低不能直接当成谁更好。</strong>
          <ul class="answer-list" style="margin: 6px 0 0">
            <li v-for="difference in compareDifferences" :key="difference">{{ difference }}</li>
          </ul>
          <p style="margin: 6px 0 0">
            条件不同仍然可以比较，但差异必须看得见：上面这几条是服务端拿各条实验已存的配置比出来的，
            这一页只显示它，不重算任何数字。
          </p>
        </div>
        <p v-else-if="compare.comparability === 'same-config'" class="notice" style="margin-top: 10px">
          服务端的结论是「配置相同，可以直接比较」：策略版本、参数、标的、时间范围、初始资金都一致。
        </p>
        <p v-else class="notice" style="margin-top: 10px">
          服务端这次没有给出「配置是否相同」的判断，所以差异也无从确认：下面只并列显示各条实验自己存下来的读数。
        </p>

        <div v-if="compareRows.length" style="overflow-x: auto; margin-top: 10px">
          <table>
            <thead>
              <tr>
                <th>指标</th>
                <th v-for="row in compareRows" :key="compareRowKey(row)">
                  <div>{{ compareColumnTitle(row) }}</div>
                  <div class="muted">
                    {{ compareStatusText(row) }} · 结果 {{ compareResultCountText(row) }} 条
                    <span v-if="isAdvanced && compareRowId(row) !== null" class="mono">
                      #{{ compareRowId(row) }}
                    </span>
                  </div>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="metric in compareMetrics" :key="metric">
                <td>
                  {{ metricKeyLabel(metric) }}
                  <span v-if="isAdvanced" class="muted mono">{{ metric }}</span>
                </td>
                <td v-for="row in compareRows" :key="compareRowKey(row)">
                  {{ compareMetricText(row, metric) }}
                </td>
              </tr>
              <tr>
                <td>配置</td>
                <td v-for="row in compareRows" :key="compareRowKey(row)">
                  <div class="muted" style="min-width: 220px">
                    <div>策略版本：{{ configStrategyText(row) }}</div>
                    <div>标的：{{ configSymbolsText(recordAt(row['config'])) }}</div>
                    <div>周期：{{ configTimeframeText(row) }}</div>
                    <div>时间范围：{{ configRangeText(row) }}</div>
                    <div>初始资金：{{ configCapitalText(row) }}</div>
                    <div>参数：{{ configParametersText(row) }}</div>
                  </div>
                  <pre v-if="isAdvanced" class="code-block mono">{{ configRawText(row) }}</pre>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="muted" style="margin-top: 10px">服务端这次没有返回任何实验行。</p>
      </template>
    </div>
  </div>
</template>
