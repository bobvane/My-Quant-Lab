<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  api,
  type GithubAnalysis,
  type GithubSnapshot,
  type GithubVersionPlan,
  type SignalIntent,
  type Strategy,
  type StrategyLifecycle,
  type StrategyValidation,
} from '@/api'
import { formatDateTime, formatNumber } from '@/format'
import { isAdvanced } from '@/mode'
// 阶段名称只有一处定义：普通模式和高阶视图必须用同一句话（ADR-127）。
import { stageLabel } from '@/wording'

// What a check actually did. ``checked`` is what older rows stored for all three
// non-events, so it is labelled as history rather than guessed at (ADR-058).
// ``review_required`` is not a failure: the watcher refused to import a draft
// that does not pass the validator and is waiting for a human (ADR-062).
const SOURCE_STATUS_LABELS: Record<string, string> = {
  imported: '已导入新版本',
  no_change: '已检查，策略无变化',
  unchanged: '未变化（同一 commit）',
  incomplete: '未完整读取，未导入',
  review_required: '待人工审阅',
  error: '检查失败',
  checked: '已检查（旧记录）',
}

function sourceStatusLabel(status: string | null | undefined): string {
  if (!status) return '从未检查'
  return SOURCE_STATUS_LABELS[status] ?? status
}

function sourceStatusTone(status: string | null | undefined): string {
  if (status === 'error') return 'error'
  if (status === 'incomplete') return 'error'
  if (status === 'review_required') return 'wait'
  return 'muted'
}

const lifecycles = ref<StrategyLifecycle[]>([])
const applyingLifecycle = ref<number | null>(null)

const strategies = ref<Strategy[]>([])
const signals = ref<SignalIntent[]>([])
const error = ref('')
const info = ref('')

const SAMPLE_DSL = {
  schema_version: '1.0',
  strategy: { id: 'ema-cross', name: 'EMA 交叉过滤趋势', version: '1.0.0' },
  market: { asset_classes: ['stock', 'crypto'], timeframes: ['1d'] },
  indicators: [
    { id: 'fast', type: 'EMA', period: 20, input: 'close' },
    { id: 'slow', type: 'EMA', period: 50, input: 'close' },
  ],
  features: ['body_ratio', 'close_position', 'atr14'],
  entry: {
    long: {
      all: [
        { op: 'crosses_above', left: 'ema20', right: 'ema50' },
        { op: 'gt', left: 'close', right: 'ema20' },
      ],
    },
  },
  exit: {
    long: {
      any: [
        { op: 'crosses_below', left: 'ema20', right: 'ema50' },
        { op: 'lt', left: 'close_position', right: '0.4' },
      ],
    },
  },
  risk: { stop_loss_atr_multiple: 2, take_profit_r_multiple: 2, max_position_pct: 0.5 },
  execution: { fill_model: 'next_bar_open', fee_bps: 10, slippage_bps: 5, initial_capital: 10000 },
}

const dslText = ref(JSON.stringify(SAMPLE_DSL, null, 2))
const strategyName = ref('EMA 交叉趋势')
const validation = ref<StrategyValidation | null>(null)

async function load() {
  error.value = ''
  try {
    const [s, lc] = await Promise.all([api.strategies(), api.lifecycles()])
    strategies.value = s
    lifecycles.value = lc
    await loadGhSources()
  } catch (e) {
    error.value = (e as Error).message
  }
}

function evidenceSummary(row: StrategyLifecycle): string {
  const e = row.evidence
  return [
    `版本 ${e.version_count ?? 0}`,
    `回测 ${e.backtest_runs ?? 0}`,
    `样本外 ${e.oos_runs ?? 0}`,
    `模拟成交 ${e.paper_trades ?? 0}`,
  ].join(' · ')
}

async function applyLifecycle(row: StrategyLifecycle, target: string | null) {
  if (!target) return
  error.value = ''
  info.value = ''
  applyingLifecycle.value = row.strategy_id
  try {
    const result = await api.applyLifecycle(row.strategy_id, target)
    info.value = `策略「${row.name}」生命周期：${stageLabel(result.previous)} → ${stageLabel(result.current)}`
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    applyingLifecycle.value = null
  }
}

const deletingStrategy = ref<number | null>(null)
const versions = ref<Array<Record<string, any>>>([])
const expandedId = ref<number | null>(null)
const activating = ref<number | null>(null)
const lineage = ref<Record<string, any> | null>(null)
const expandedVersion = ref<number | null>(null)
const versionParams = ref<Array<Record<string, any>>>([])

async function toggleParams(versionId: number) {
  if (expandedVersion.value === versionId) {
    expandedVersion.value = null
    return
  }
  try {
    versionParams.value = await api.versionParameters(versionId)
    expandedVersion.value = versionId
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function showLineage(s: Strategy) {
  error.value = ''
  try {
    lineage.value = s.id === expandedId.value && lineage.value ? null : await api.strategyLineage(s.id)
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function toggleVersions(s: Strategy) {
  error.value = ''
  if (expandedId.value === s.id) {
    expandedId.value = null
    return
  }
  try {
    versions.value = await api.strategyVersions(s.id)
    expandedId.value = s.id
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function activateVersion(v: Record<string, any>) {
  error.value = ''
  info.value = ''
  activating.value = Number(v.id)
  try {
    await api.activateVersion(Number(v.id))
    info.value = `已切换策略 #${v.strategy_id} 到版本 v${v.version}`
    if (expandedId.value !== null) {
      versions.value = await api.strategyVersions(expandedId.value)
    }
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    activating.value = null
  }
}

async function deleteStrategy(id: number, name: string) {
  const ok = window.confirm(
    `确定删除策略「${name}」？它没有被回测、信号或模拟盘引用时会连同全部版本一并删除，且不可恢复。`,
  )
  if (!ok) return
  error.value = ''
  info.value = ''
  deletingStrategy.value = id
  try {
    const result = await api.deleteStrategy(id)
    info.value = `已删除策略「${result.name}」`
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    deletingStrategy.value = null
  }
}

async function validate() {
  error.value = ''
  try {
    validation.value = await api.validateDsl(JSON.parse(dslText.value))
  } catch (e) {
    error.value = `DSL 解析失败：${(e as Error).message}`
  }
}

async function createStrategy() {
  error.value = ''
  info.value = ''
  try {
    const dsl = JSON.parse(dslText.value)
    const strategy = await api.createStrategy(strategyName.value, '由 Web UI 创建')
    const version = await api.createVersion(strategy.id, '1.0.0', dsl)
    info.value = `已创建策略 #${strategy.id} 版本 ${version.version}（哈希 ${version.immutable_hash.slice(0, 12)}…）`
    await load()
  } catch (e) {
    error.value = (e as Error).message
  }
}

// A 普通用户 creates a strategy by answering a few questions, not by writing JSON
// (评审 §8, ADR-132). The form builds the same DSL document the advanced editor
// shows, and writes it into the *same* draft (`dslText`), so there is exactly one
// draft on this page: whoever edits the JSON later is editing what the form made —
// not a second copy that can drift. The numbers the form does not ask about
// (fee/slippage/fill model/initial capital) are printed next to it rather than
// hidden, and they are the values the engine will read.
const formName = ref('均线交叉策略')
const formFast = ref(20)
const formSlow = ref(50)
const formTrendFilter = ref(true)
const formExitOnFastCross = ref(true)
const formStopAtr = ref(2)
const formTakeProfitR = ref(2)
const EMA_FAST_ID = 'fast'
const EMA_SLOW_ID = 'slow'

/** The DSL the form describes. Same shape as SAMPLE_DSL above. */
function formDsl(): Record<string, unknown> {
  const exitAny: Array<Record<string, unknown>> = []
  if (formExitOnFastCross.value) {
    exitAny.push({ op: 'crosses_below', left: EMA_FAST_ID, right: EMA_SLOW_ID })
  }
  if (!exitAny.length) {
    exitAny.push({ op: 'lt', left: 'close', right: EMA_FAST_ID })
  }
  const entryAll: Array<Record<string, unknown>> = [
    { op: 'crosses_above', left: EMA_FAST_ID, right: EMA_SLOW_ID },
  ]
  if (formTrendFilter.value) {
    entryAll.push({ op: 'gt', left: 'close', right: EMA_FAST_ID })
  }
  return {
    schema_version: '1.0',
    strategy: {
      id: `ema-cross-${formFast.value}-${formSlow.value}`,
      name: formName.value,
      version: '1.0.0',
    },
    market: { asset_classes: ['stock', 'crypto'], timeframes: ['1d'] },
    indicators: [
      { id: EMA_FAST_ID, type: 'EMA', period: formFast.value, input: 'close' },
      { id: EMA_SLOW_ID, type: 'EMA', period: formSlow.value, input: 'close' },
    ],
    features: ['atr14'],
    entry: { long: { all: entryAll } },
    exit: { long: { any: exitAny } },
    risk: {
      stop_loss_atr_multiple: formStopAtr.value,
      take_profit_r_multiple: formTakeProfitR.value,
    },
    execution: {
      fill_model: 'next_bar_open',
      fee_bps: 10,
      slippage_bps: 5,
      initial_capital: 10000,
    },
  }
}

/** The rule in words, so the reader can check the JSON against a sentence. */
const formSentence = computed(
  () =>
    `当 ${formFast.value} 日均线${formTrendFilter.value ? '上穿' : '高于'} ` +
    `${formSlow.value} 日均线${formTrendFilter.value ? `，并且收盘价在 ${formFast.value} 日均线上方时买入` : '时买入'}；` +
    (formExitOnFastCross.value
      ? `${formFast.value} 日均线跌破 ${formSlow.value} 日均线时卖出。`
      : `收盘价跌破 ${formFast.value} 日均线时卖出。`) +
    `止损用 ${formStopAtr.value} 倍 ATR，止盈 ${formTakeProfitR.value} 倍风险。`,
)

const formError = computed(() => {
  if (!formName.value.trim()) return '策略名不能为空。'
  if (!(formFast.value > 0) || !(formSlow.value > 0)) return '均线周期必须是正整数。'
  if (formFast.value >= formSlow.value) return '快线周期需要小于慢线周期，否则没有交叉。'
  return ''
})

/** Writes the form's document into the one draft, which also withdraws a stale verdict. */
function applyForm() {
  if (formError.value) return
  dslText.value = JSON.stringify(formDsl(), null, 2)
  strategyName.value = formName.value.trim()
}

async function validateForm() {
  applyForm()
  if (formError.value) {
    error.value = formError.value
    return
  }
  await validate()
}

// The form and the JSON editor describe one document, so the draft follows the form
// as it changes — and a passing validation made about earlier numbers is dropped by
// the `watch(dslText, …)` below, exactly as it is for a hand edit (ADR-113).
watch(
  [formName, formFast, formSlow, formTrendFilter, formExitOnFastCross, formStopAtr, formTakeProfitR],
  applyForm,
)

/** Create from the form: the validator still decides, not the button (ADR-113). */
async function createFromForm() {
  applyForm()
  if (formError.value) {
    error.value = formError.value
    return
  }
  await validate()
  if (!validation.value?.is_valid) {
    info.value = ''
    error.value = error.value || '校验没有通过，先按上面的问题改一改。'
    return
  }
  await createStrategy()
}

const ghSources = ref<Array<Record<string, any>>>([])
const ghUpdates = ref<Record<number, boolean>>({})
const checkingSource = ref<number | null>(null)

async function loadGhSources() {
  try {
    ghSources.value = await api.githubSources()
  } catch {
    ghSources.value = []
  }
}

async function checkSourceNow(s: Record<string, any>) {
  error.value = ''
  info.value = ''
  checkingSource.value = Number(s.id)
  try {
    const r = await api.githubCheckSource(Number(s.id))
    ghUpdates.value[Number(s.id)] = r.has_update
    info.value = r.has_update
      ? `${s.repository_url} 有新 commit：${String(r.head).slice(0, 12)}`
      : `${s.repository_url} 已是最新`
    await loadGhSources()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    checkingSource.value = null
  }
}

// Why a check ended the way it did is stored with the snapshot, so the row can
// answer "it says incomplete - incomplete how?" instead of leaving the operator
// with an English enum (ADR-058).
const ghSnapshots = ref<Record<number, GithubSnapshot[]>>({})
const ghSnapshotOpen = ref<number | null>(null)
const ghSnapshotLoading = ref<number | null>(null)

async function toggleSourceSnapshots(s: Record<string, any>) {
  const id = Number(s.id)
  if (ghSnapshotOpen.value === id) {
    ghSnapshotOpen.value = null
    return
  }
  ghSnapshotOpen.value = id
  if (ghSnapshots.value[id]) return
  ghSnapshotLoading.value = id
  try {
    ghSnapshots.value[id] = await api.githubSnapshots(id, 5)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    ghSnapshotLoading.value = null
  }
}

function latestSnapshot(s: Record<string, any>): GithubSnapshot | null {
  return ghSnapshots.value[Number(s.id)]?.[0] ?? null
}

function snapshotExplanation(snapshot: GithubSnapshot | null): string {
  if (!snapshot) return '这个来源还没有检查记录。'
  const extraction = snapshot.extraction ?? {}
  const reason = String(extraction.reason ?? '')
  if (reason === 'incomplete_analysis') {
    const coverage = extraction.coverage ?? {}
    const read = `${coverage.attempted_files ?? 0} / ${coverage.candidate_files ?? '?'} 个候选文件`
    return extraction.transient
      ? `读取因超出时间预算或网络中断而停止，只读了 ${read}；这个 commit 没有标记为已处理，下一轮检查会重试。`
      : `读取受抓取上限限制，只读了 ${read}；同样的设置重读会得到同样的结果，所以这个 commit 已标记为处理过，不会自动导入。`
  }
  if (reason === 'unparseable_python') {
    const coverage = extraction.coverage ?? {}
    const files = Number(coverage.unparsed_python_files ?? 0)
    const names = Array.isArray(extraction.files_unparsed)
      ? extraction.files_unparsed.map((f: Record<string, unknown>) => String(f.path)).join('、')
      : ''
    return `${files} 个 Python 文件下载到了但解析失败${names ? `（${names}）` : ''}，它们里面的规则无法进入草案；重读不会让它们变得可解析，所以这个 commit 已标记为处理过，不会自动导入。`
  }
  if (reason === 'requires_review') {
    const detail = String(extraction.detail ?? '')
    return `草案没有通过导入校验${detail ? `（${detail}）` : ''}：机器不会替你做这个决定。补上缺失的规则后人工导入这个 commit；自动化在此之前不会导入它，也不会重复抓取它（同一个 commit 的结论是一样的）。`
  }
  if (reason === 'import_failed') {
    const detail = String(extraction.detail ?? '')
    return `导入失败${detail ? `：${detail}` : ''}。这个 commit 没有标记为已处理，下一轮检查会重试。`
  }
  if (reason === 'no_linked_strategy') {
    return '读取了这个 commit，但没有任何策略声明来源于这个仓库，所以没有可更新的版本。'
  }
  if (reason === 'dsl_unchanged') {
    return '读取了这个 commit，抽取出的策略与最新版本一致，没有导入。'
  }
  if (reason === 'manual_import') return '这个 commit 是人工审核后导入的。'
  if (extraction.imported === true) return '已从这个 commit 生成新的策略版本。'
  if (extraction.imported === false) return '已读取这个 commit，但抽取出的策略没有变化。'
  return '这条快照没有记录原因（早期版本留下的记录）。'
}

function snapshotCoverage(snapshot: GithubSnapshot | null): string {
  const coverage = snapshot?.extraction?.coverage
  if (!coverage) return ''
  return `读取 ${coverage.downloaded_files} / ${coverage.candidate_files} 个候选文件（解析 ${coverage.parsed_files} 个 Python、登记 ${coverage.inventoried_files} 个非 Python）${
    coverage.unread_python_files > 0 ? `，其中 ${coverage.unread_python_files} 个 Python 没被读到` : ''
  }${
    coverage.unparsed_python_files > 0
      ? `，其中 ${coverage.unparsed_python_files} 个 Python 解析失败`
      : ''
  }。`
}

function snapshotWarnings(snapshot: GithubSnapshot | null): string[] {
  const warnings = snapshot?.extraction?.warnings
  return Array.isArray(warnings) ? warnings.map((w: unknown) => String(w)) : []
}

const repoUrl = ref('')
const repoRef = ref('')
const repoToken = ref('')
const repoMaxFiles = ref(12)
const repoMaxSeconds = ref(120)
const importName = ref('')
const importVersion = ref('')
const versionPlan = ref<GithubVersionPlan | null>(null)
const versionPlanLoading = ref(false)
const analyzing = ref(false)
const importing = ref(false)
const analysis = ref<GithubAnalysis | null>(null)

// Numbering belongs to the service that owns the version ledger: ask it what an
// import of this name would do instead of hard-coding `1.0.0` and discovering the
// collision from a 422 (ADR-061).
let versionPlanTimer: ReturnType<typeof setTimeout> | null = null

async function refreshVersionPlan() {
  const name = importName.value.trim()
  if (!name) {
    versionPlan.value = null
    return
  }
  versionPlanLoading.value = true
  try {
    versionPlan.value = await api.githubVersionPlan(name)
  } catch {
    // A ledger we could not read is not a ledger we can promise anything about.
    versionPlan.value = null
  } finally {
    versionPlanLoading.value = false
  }
}

watch(importName, () => {
  if (versionPlanTimer) clearTimeout(versionPlanTimer)
  versionPlanTimer = setTimeout(() => void refreshVersionPlan(), 300)
})

const namedVersion = computed(() => importVersion.value.trim())

// Only a version the caller names can collide: an assigned one is chosen from the
// ledger we just read.
const namedVersionTaken = computed(
  () =>
    !!namedVersion.value &&
    !!versionPlan.value?.versions.includes(namedVersion.value),
)

const plannedVersionNote = computed(() => {
  if (namedVersion.value) {
    return namedVersionTaken.value
      ? `版本 ${namedVersion.value} 已存在于这个策略上，服务器会拒绝；换一个版本号或留空让服务器分配。`
      : `将以版本 ${namedVersion.value} 导入（由你命名）。`
  }
  const plan = versionPlan.value
  if (!plan) return versionPlanLoading.value ? '正在查询版本账本…' : ''
  if (!plan.can_assign) {
    return `${plan.reason}；请手动填写版本号。`
  }
  return plan.strategy_id === null
    ? `将新建策略（slug ${plan.slug}），版本 ${plan.next_version}。`
    : `将在已有策略 #${plan.strategy_id}（slug ${plan.slug}）上创建新版本 ${plan.next_version}；已有版本：${plan.versions.join('、') || '无'}。`
})

// The analysis is a review surface, not a guarantee: say out loud how much of
// the repository it actually read, because the files it never fetched are the
// ones nobody has reviewed (docs/05 §4.1).
const coverageHeadline = computed(() => {
  const coverage = analysis.value?.coverage
  if (!coverage) return ''
  if (coverage.complete) {
    return coverage.unparsed_python_files > 0
      ? '已完整读取仓库中的全部候选文件——但其中有 Python 文件没有解析成功，见下方。'
      : '已完整读取仓库中的全部候选文件。'
  }
  if (coverage.budget_exhausted) {
    const budget = coverage.max_seconds === null ? '时间预算' : `${coverage.max_seconds} 秒的时间预算`
    const rest = coverage.skipped_files > 0 ? `，另有 ${coverage.skipped_files} 个获取后无法读取` : ''
    return `读取因超出${budget}而中断：只读了 ${coverage.attempted_files} / ${coverage.candidate_files} 个候选文件${rest}。把预算调大，或减少最多读取文件数后再试——下面的结论只覆盖已列出的文件。`
  }
  const parts = [
    `${coverage.not_attempted_files} 个候选文件从未获取（上限 ${coverage.cap}，仓库共 ${coverage.candidate_files} 个）`,
  ]
  if (coverage.skipped_files > 0) parts.push(`${coverage.skipped_files} 个获取后无法读取`)
  return `未完整读取：${parts.join('，')}。下面的结论只覆盖已列出的文件。`
})

const unreadPythonWarning = computed(() => {
  const n = analysis.value?.coverage.unread_python_files ?? 0
  return n > 0 ? `其中 ${n} 个 Python 文件没被读到——它们里面的规则不会出现在下面的发现里。` : ''
})

// Read and understood are different claims: a file we downloaded but could not
// parse contributed nothing, and it must not look like a file we analysed
// (ADR-059). Saying "read everything" without this line is how a report
// overstates itself one layer deeper than ADR-056 did.
const unparsedPythonWarning = computed(() => {
  const n = analysis.value?.coverage.unparsed_python_files ?? 0
  return n > 0 ? `其中 ${n} 个 Python 文件下载到了、但没能解析——它们里面的规则不会出现在下面的发现里。` : ''
})

const COMMIT_SHA_RE = /^[0-9a-fA-F]{7,40}$/

/**
 * A commit is the only revision that can be re-read later. Anything else in a
 * `source_commit` column is a branch name recorded before ADR-060, and it has to
 * look different from a commit instead of passing as one.
 */
function commitLabel(value?: string | null): string {
  const text = (value ?? '').trim()
  if (!text) return '—'
  return COMMIT_SHA_RE.test(text) ? `${text.slice(0, 12)}…` : `${text}（早期记录的分支名）`
}

function shortCommit(value?: string | null): string {
  const text = (value ?? '').trim()
  return COMMIT_SHA_RE.test(text) ? text.slice(0, 12) : text || '未知'
}

async function analyzeRepo() {
  error.value = ''
  info.value = ''
  analysis.value = null
  if (!repoUrl.value.trim()) {
    error.value = '请填写 GitHub 仓库地址'
    return
  }
  analyzing.value = true
  try {
    analysis.value = await api.analyzeGithubRepo(
      repoUrl.value.trim(),
      repoRef.value.trim() || undefined,
      repoToken.value.trim() || undefined,
      repoMaxFiles.value,
      repoMaxSeconds.value,
    )
    importName.value = analysis.value.repo
    dslText.value = JSON.stringify(analysis.value.draft_dsl, null, 2)
    strategyName.value = analysis.value.repo
    unsafeReviewed.value = false
    validation.value = null
    gotoStep(2)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    analyzing.value = false
  }
}

async function importReviewed() {
  error.value = ''
  info.value = ''
  if (!analysis.value) {
    error.value = '请先分析仓库'
    gotoStep(1)
    return
  }
  if (!validatedDraft.value) {
    error.value = '第 6 步的校验还没通过（或者校验之后 DSL 又被改过）：先校验草案，再导入'
    gotoStep(6)
    return
  }
  importing.value = true
  try {
    const dsl = JSON.parse(dslText.value)
    const result = await api.importGithubStrategy(
      repoUrl.value.trim(),
      importName.value.trim() || analysis.value.repo,
      dsl,
      analysis.value.commit,
      analysis.value.ref,
      namedVersion.value || undefined,
    )
    const how = result.version_assigned ? '由服务器分配' : '由你命名'
    info.value = `已导入策略 #${result.strategy_id}（版本 ${result.version}，${how}，${result.validation_status}，来源 commit ${shortCommit(result.source_commit)}）`
    analysis.value = null
    importVersion.value = ''
    versionPlan.value = null
    validation.value = null
    unsafeReviewed.value = false
    gotoStep(1)
    await load()
    await loadGhSources()
  } catch (e) {
    error.value = (e as Error).message
    // The ledger moved under us (or was read wrong): re-read it so the note and
    // the disabled state match what the server just said.
    await refreshVersionPlan()
  } finally {
    importing.value = false
  }
}

// docs/13_UI_UX.md §8 promises seven steps: Repository → Analysis → Detected
// Strategies → Warnings → DSL Preview → Validation → Import. Each step is unlocked
// by its own evidence, and a passing validation belongs to one exact document —
// edit the text and it stops being true (ADR-113).
const WIZARD_STEPS = [
  '仓库（Repository）',
  '分析（Analysis）',
  '检测到的策略（Detected Strategies）',
  '警告（Warnings）',
  'DSL 预览（DSL Preview）',
  '校验（Validation）',
  '导入（Import）',
] as const

const wizardStep = ref(1)
const unsafeReviewed = ref(false)
const validating = ref(false)

function gotoStep(step: number) {
  wizardStep.value = Math.min(Math.max(step, 1), WIZARD_STEPS.length)
}

/** The draft has to be an object before the validator can say anything about it. */
const draftDsl = computed<Record<string, unknown> | null>(() => {
  try {
    const parsed = JSON.parse(dslText.value)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null
  } catch {
    return null
  }
})

const draftIsJson = computed(() => draftDsl.value !== null)
const validatedDraft = computed(() => validation.value?.is_valid === true)
const unsafeCount = computed(() => analysis.value?.unsafe_flags.length ?? 0)
const warningsAcknowledged = computed(() => unsafeCount.value === 0 || unsafeReviewed.value)

const draftIndicatorCount = computed(() => {
  const indicators = draftDsl.value?.indicators
  return Array.isArray(indicators) ? indicators.length : 0
})

const draftEntryCount = computed(() => {
  const entry = draftDsl.value?.entry as { long?: { all?: unknown[] } } | undefined
  return Array.isArray(entry?.long?.all) ? entry.long.all.length : 0
})

/** What unlocks each step, in one place so a button cannot drift from it. */
const stepUnlocked = computed<boolean[]>(() => {
  const hasAnalysis = analysis.value !== null
  return [
    repoUrl.value.trim().length > 0,
    hasAnalysis,
    hasAnalysis,
    hasAnalysis && warningsAcknowledged.value,
    hasAnalysis && draftIsJson.value,
    hasAnalysis && draftIsJson.value && validatedDraft.value,
    hasAnalysis && validatedDraft.value && !!importName.value.trim() && !namedVersionTaken.value,
  ]
})

const stepBlockedReason = computed(() => {
  switch (wizardStep.value) {
    case 1:
      return '先填仓库地址'
    case 2:
    case 3:
      return '还没有分析结果'
    case 4:
      return unsafeCount.value > 0
        ? `请先勾选「已审查这 ${unsafeCount.value} 个不安全构造」`
        : '还没有分析结果'
    case 5:
      return 'DSL 不是合法的 JSON 对象'
    case 6:
      return '校验还没通过'
    default:
      return ''
  }
})

async function validateDraft() {
  error.value = ''
  info.value = ''
  const dsl = draftDsl.value
  if (!dsl) {
    error.value = 'DSL 不是合法的 JSON 对象，先修好文本再校验'
    return
  }
  validating.value = true
  try {
    validation.value = await api.validateDsl(dsl)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    validating.value = false
  }
}

// A passing check is a statement about one exact document. The moment the text
// changes, that statement is about a document nobody has validated any more, so it
// is dropped and the wizard steps back behind the gate (ADR-113).
watch(dslText, () => {
  if (validation.value !== null) validation.value = null
  if (wizardStep.value > 5) wizardStep.value = 5
})

onMounted(load)
</script>

<template>
  <div>
    <h1 class="page-title">我的策略</h1>
    <p class="page-sub">
      这是你的策略库：用一句人话创建策略、管理版本、从 GitHub 导入策略。版本一旦创建即不可修改。
      行情数据现在在「数据」页；从想法到回测的四步在「研究策略」页。
    </p>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="info" class="notice">{{ info }}</p>

    <div class="grid cols-2">
      <div class="card card-quiet">
        <h3>用一句人话创建策略</h3>
        <p class="muted" style="margin-bottom: 8px">
          回答几个问题就能建一个策略：系统把你的回答写成规则，规则就是这份策略的版本内容。
          想直接写 JSON 的话，高级模式下有「策略 DSL」编辑器。
        </p>
        <div class="row" style="margin-bottom: 8px">
          <input v-model="formName" style="max-width: 190px" placeholder="策略名" />
          <label class="muted">
            快线
            <input v-model.number="formFast" type="number" min="2" max="200" style="max-width: 80px" />
          </label>
          <label class="muted">
            慢线
            <input v-model.number="formSlow" type="number" min="3" max="400" style="max-width: 80px" />
          </label>
        </div>
        <div class="row" style="margin-bottom: 8px">
          <label class="muted">
            <input v-model="formTrendFilter" type="checkbox" /> 要求收盘价在快线上方
          </label>
          <label class="muted">
            <input v-model="formExitOnFastCross" type="checkbox" /> 快线跌破慢线时卖出
          </label>
        </div>
        <div class="row" style="margin-bottom: 8px">
          <label class="muted">
            止损（ATR 倍数）
            <input v-model.number="formStopAtr" type="number" min="0.5" max="10" step="0.5" style="max-width: 90px" />
          </label>
          <label class="muted">
            止盈（风险倍数）
            <input v-model.number="formTakeProfitR" type="number" min="0.5" max="10" step="0.5" style="max-width: 90px" />
          </label>
        </div>
        <p class="conclusion-sentence">{{ formSentence }}</p>
        <p v-if="formError" class="error">{{ formError }}</p>
        <div class="row" style="margin: 8px 0">
          <button class="ghost" :disabled="validating || !!formError" @click="validateForm">
            {{ validating ? '校验中…' : '校验这套规则' }}
          </button>
          <button :disabled="!!formError" @click="createFromForm">创建策略与版本</button>
        </div>
        <p v-if="validation" class="notice" :class="{ warn: !validation.is_valid }">
          {{
            validation.is_valid
              ? '校验通过：这套规则可以用。'
              : '校验没有通过，具体问题列在下面「策略 DSL」编辑器里。'
          }}
        </p>
        <p class="muted">
          这里的文本与高级模式下的「策略 DSL」是同一份草稿：表单生成它、编辑器改它，两者不会各存一份。
          表单不问、但引擎真正会用的假设是：手续费 10bp、滑点 5bp、次日开盘成交、初始资金 10000。
        </p>
      </div>

      <div class="card">
        <h3>策略库</h3>
        <table v-if="strategies.length">
          <thead>
            <tr>
              <th>ID</th>
              <th>名称</th>
              <th>版本数</th>
              <th>来源</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in strategies" :key="s.id">
              <td>{{ s.id }}</td>
              <td>{{ s.name }}</td>
              <td>{{ s.version_count }}</td>
              <td>{{ s.source_type }}</td>
              <td>
                <button class="ghost" :disabled="deletingStrategy === s.id" @click="deleteStrategy(s.id, s.name)">
                  {{ deletingStrategy === s.id ? '删除中…' : '删除' }}
                </button>
              </td>
              <td>
                <!-- The nine-part detail page lives at its own route, reached from here (ADR-114). -->
                <RouterLink class="ghost" :to="`/strategy/${s.id}`">详情</RouterLink>
                <button class="ghost" @click="toggleVersions(s)">
                  {{ expandedId === s.id ? '收起版本' : '版本' }}
                </button>
                <button class="ghost" @click="showLineage(s)">血统</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="lineage" class="card" style="margin-top: 10px">
          <h3>策略血统 #{{ lineage.strategy_id }}</h3>
          <p class="muted">
            来源 {{ lineage.source_type || '—' }} · 许可 {{ lineage.license || '未知' }} ·
            作者 {{ lineage.author || '—' }}
            <span v-if="lineage.source_url"> · {{ lineage.source_url }}</span>
          </p>
          <table v-if="lineage.versions?.length">
            <thead>
              <tr>
                <th>版本</th>
                <th>来源 commit</th>
                <th>Prompt</th>
                <th>哈希</th>
                <th>当前</th>
                <th>参数</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="v in lineage.versions" :key="v.id">
                <tr>
                  <td>{{ v.version }}</td>
                  <td class="muted">{{ commitLabel(v.source_commit) }}</td>
                  <td class="muted">{{ v.prompt_version || '—' }}</td>
                  <td class="muted">{{ String(v.immutable_hash).slice(0, 12) }}…</td>
                  <td>{{ v.is_current ? '是' : '' }}</td>
                  <td>
                    <button class="ghost" @click="toggleParams(Number(v.id))">
                      {{ expandedVersion === v.id ? '收起参数' : '参数' }}
                    </button>
                  </td>
                </tr>
                <tr v-if="expandedVersion === v.id">
                  <td colspan="6">
                    <code>{{ JSON.stringify(versionParams) }}</code>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
          <p v-else class="muted">该策略还没有版本。</p>
        </div>
        <div v-if="expandedId !== null" class="card" style="margin-top: 10px">
          <h3>策略 #{{ expandedId }} 版本</h3>
          <table v-if="versions.length">
            <thead>
              <tr>
                <th>版本</th>
                <th>状态</th>
                <th>哈希</th>
                <th>创建时间</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="v in versions" :key="v.id">
                <td>{{ v.version }}</td>
                <td>
                  <span v-if="v.is_current" class="badge BUY">当前</span>
                  <span v-else class="muted">—</span>
                </td>
                <td class="muted">{{ v.immutable_hash.slice(0, 12) }}…</td>
                <td class="muted">{{ formatDateTime(v.created_at) }}</td>
                <td>
                  <button
                    v-if="!v.is_current"
                    class="ghost"
                    :disabled="activating === v.id"
                    @click="activateVersion(v)"
                  >
                    {{ activating === v.id ? '切换中…' : '设为当前' }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-else class="muted">该策略还没有版本。</p>
        </div>
        <p v-else class="muted">还没有策略。</p>
      </div>
    </div>

    <div class="card" style="margin-top: 14px">
      <h3>策略生命周期（基于证据，无 AI 介入）</h3>
      <p class="muted">
        阶段推进只依据已记录的证据：版本校验、完成回测、样本外窗口、模拟成交。
        每次只前进一阶段；<strong>参考信号</strong>与<strong>退役</strong>只能手动确认。
        每次晋级/降级都会连同证据写入审计日志。
      </p>
      <table v-if="lifecycles.length">
        <thead>
          <tr>
            <th>ID</th>
            <th>名称</th>
            <th>当前阶段</th>
            <th>建议下一步</th>
            <th>证据</th>
            <th>参考信号</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in lifecycles" :key="row.strategy_id">
            <td>{{ row.strategy_id }}</td>
            <td>{{ row.name }}</td>
            <td>
              {{ stageLabel(row.current) }}
              <span v-if="row.degraded" class="error" style="margin-left: 4px">亏损降级</span>
            </td>
            <td>
              <span v-if="row.suggested_next">{{ stageLabel(row.suggested_next) }}</span>
              <span v-else class="muted">—</span>
            </td>
            <td class="muted">{{ evidenceSummary(row) }}</td>
            <td>
              <button
                v-if="row.reference_eligible && row.current !== 'reference_signal'"
                class="ghost"
                :disabled="applyingLifecycle === row.strategy_id"
                @click="applyLifecycle(row, 'reference_signal')"
              >
                升级为参考信号
              </button>
              <span v-else class="muted">条件未满足</span>
            </td>
            <td>
              <button
                v-if="row.suggested_next"
                :disabled="applyingLifecycle === row.strategy_id"
                @click="applyLifecycle(row, row.suggested_next)"
              >
                {{ applyingLifecycle === row.strategy_id ? '应用中…' : '应用下一步' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">还没有策略。</p>
    </div>

    <!-- 评审 §8：普通用户看上面的表单，JSON 编辑器属于高级层（ADR-132）。 -->
    <template v-if="isAdvanced">
      <div class="card" style="margin-top: 14px">
        <h3>策略 DSL（声明式，JSON 形式）</h3>
        <p class="muted">
          这里和上面的表单共用同一份草稿：表单会把它写进这个文本框，你在这里改完，回到表单也会跟着变。
        </p>
        <div class="row" style="margin-bottom: 10px">
          <input v-model="strategyName" style="max-width: 260px" />
          <button class="ghost" @click="validate">校验 DSL</button>
          <button @click="createStrategy">创建策略与版本</button>
        </div>
        <textarea v-model="dslText" spellcheck="false" />
        <div v-if="validation" style="margin-top: 10px">
          <p v-if="validation.is_valid" class="notice">校验通过：规则列与数据来源均合法。</p>
          <ul v-else class="error">
            <li v-for="(i, idx) in validation.issues" :key="idx">
              [{{ i.severity }}] {{ i.code }} — {{ i.message }}
            </li>
          </ul>
        </div>
      </div>
    </template>

    <div class="card card-quiet" style="margin-top: 14px">
      <h3>从 GitHub 导入（只读分析，不执行仓库代码）</h3>
      <p class="muted">
        七步只有拿到自己的证据才放行：没有分析结果就到不了第 2 步，不安全构造没有人工确认就出不了第 4 步，
        DSL 不是合法 JSON 就到不了第 6 步，校验通过之后又改过文本就回到第 5 步。
      </p>
      <ol class="wizard-steps">
        <li
          v-for="(step, index) in WIZARD_STEPS"
          :key="step"
          :class="{
            current: wizardStep === index + 1,
            done: wizardStep > index + 1,
            locked: !stepUnlocked[index],
          }"
          @click="stepUnlocked[index] && gotoStep(index + 1)"
        >
          {{ index + 1 }}. {{ step }}
        </li>
      </ol>

      <div v-if="wizardStep === 1">
        <div class="row" style="margin-bottom: 10px">
          <input
            v-model="repoUrl"
            style="max-width: 340px"
            placeholder="https://github.com/owner/repo"
          />
          <input v-model="repoRef" style="max-width: 140px" placeholder="分支/tag（可选）" />
        </div>
        <div class="row" style="margin-bottom: 10px">
          <label class="muted" for="repo-max-files">最多读取文件数</label>
          <input
            id="repo-max-files"
            v-model.number="repoMaxFiles"
            type="number"
            min="1"
            max="30"
            style="max-width: 90px"
          />
          <label class="muted" for="repo-max-seconds">最长等待秒数</label>
          <input
            id="repo-max-seconds"
            v-model.number="repoMaxSeconds"
            type="number"
            min="10"
            max="600"
            style="max-width: 90px"
          />
          <span class="muted">超时就停下并说明读了哪些，不会一直转圈</span>
        </div>
        <div class="row" style="margin-bottom: 10px">
          <input
            v-model="repoToken"
            type="password"
            style="max-width: 340px"
            placeholder="GitHub token（可选，仅提限额用，不存储）"
          />
        </div>
        <div class="row">
          <button :disabled="analyzing || !repoUrl.trim()" @click="analyzeRepo">
            {{ analyzing ? '分析中…（视网络情况可能需要一两分钟）' : '分析仓库' }}
          </button>
          <span class="muted">输入公开仓库地址后，系统只下载文本做静态分析，绝不执行仓库里的任何代码</span>
        </div>
        <p v-if="!analyzing && !repoUrl.trim()" class="muted" style="margin-top: 8px">
          「分析仓库」现在点不动，因为上面还没有填仓库地址：粘一个公开的 GitHub 仓库链接再试。
        </p>
      </div>

      <div v-else-if="wizardStep === 2 && analysis">
        <p class="muted">
          {{ analysis.owner }}/{{ analysis.repo }} @ {{ analysis.ref }} · commit
          {{ shortCommit(analysis.commit) }} ·
          读取 {{ analysis.coverage.downloaded_files }} / {{ analysis.coverage.candidate_files }} 个候选文件
          （解析 {{ analysis.coverage.parsed_files }} 个 Python<span
            v-if="analysis.coverage.unparsed_python_files"
          >、{{ analysis.coverage.unparsed_python_files }} 个解析失败</span>、登记
          {{ analysis.coverage.inventoried_files }} 个非 Python）·
          许可证 {{ analysis.license ?? '未知' }}
        </p>
        <p :class="analysis.coverage.complete ? 'muted' : 'error'">{{ coverageHeadline }}</p>
        <p v-if="unreadPythonWarning" class="error">{{ unreadPythonWarning }}</p>
        <p v-if="unparsedPythonWarning" class="error">{{ unparsedPythonWarning }}</p>
        <details v-if="analysis.files_unparsed.length" class="muted">
          <summary>解析失败的文件（{{ analysis.files_unparsed.length }}）</summary>
          <table>
            <thead>
              <tr>
                <th>文件</th>
                <th>原因</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(f, idx) in analysis.files_unparsed" :key="idx">
                <td>{{ f.path }}</td>
                <td class="muted">{{ f.reason }}</td>
              </tr>
            </tbody>
          </table>
        </details>
        <details v-if="analysis.files_skipped.length" class="muted">
          <summary>被跳过的文件（{{ analysis.files_skipped.length }}）</summary>
          <table>
            <thead>
              <tr>
                <th>文件</th>
                <th>原因</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(f, idx) in analysis.files_skipped" :key="idx">
                <td>{{ f.path }}</td>
                <td class="muted">{{ f.reason }}</td>
              </tr>
            </tbody>
          </table>
        </details>
        <p class="muted">
          这一步只报告读取与解析的事实，判断对错留给你自己：分析结果的每个数字都能在仓库里找到对应的文件。
        </p>
      </div>

      <div v-else-if="wizardStep === 3 && analysis">
        <p class="muted">
          这里列的是导入器真的找到的东西（扁平发现：每个指标、规则、参数各自一条），
          它不会替你决定「这是一个策略」——组合成草案的是第 5 步，判定草案对错的是第 6 步。
        </p>
        <table v-if="analysis.indicators.length">
          <thead>
            <tr>
              <th>指标</th>
              <th>类型</th>
              <th>周期</th>
              <th>证据</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(i, idx) in analysis.indicators" :key="idx">
              <td>{{ i.source_name }}</td>
              <td>{{ i.kind }}</td>
              <td>{{ i.period ?? '—' }}</td>
              <td class="muted">{{ i.evidence_path }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">没有识别出指标声明。</p>
        <table v-if="analysis.rules.length">
          <thead>
            <tr>
              <th>识别出的规则</th>
              <th>操作符</th>
              <th>左值</th>
              <th>右值</th>
              <th>证据</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, idx) in analysis.rules" :key="idx">
              <td>{{ idx + 1 }}</td>
              <td>{{ r.op }}</td>
              <td>{{ r.left }}</td>
              <td>{{ r.right }}</td>
              <td class="muted">{{ r.evidence_path }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">没有识别出可映射的规则，请检查 unknowns。</p>
        <table v-if="analysis.params.length">
          <thead>
            <tr>
              <th>识别出的参数</th>
              <th>取值</th>
              <th>证据</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(p, idx) in analysis.params" :key="idx">
              <td>{{ p.name }}</td>
              <td>{{ p.value }}</td>
              <td class="muted">{{ p.evidence_path }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="analysis.unknowns.length" class="muted">
          另有 {{ analysis.unknowns.length }} 处无法映射的内容（已保留证据，未编造规则）。
        </p>
      </div>

      <div v-else-if="wizardStep === 4 && analysis">
        <ul v-if="analysis.warnings.length" class="error">
          <li v-for="(w, idx) in analysis.warnings" :key="idx">{{ w }}</li>
        </ul>
        <p v-else class="muted">分析过程没有留下警告。</p>
        <p v-if="unsafeCount" class="error">
          发现 {{ unsafeCount }} 个不安全构造（导入器不会执行它们，但请先审查）。
        </p>
        <label v-if="unsafeCount" class="muted">
          <input v-model="unsafeReviewed" type="checkbox" />
          我已人工审查这 {{ unsafeCount }} 个不安全构造
        </label>
        <p v-if="analysis.unknowns.length" class="muted">
          另有 {{ analysis.unknowns.length }} 处无法映射的内容（已保留证据，未编造规则）。
        </p>
        <p class="muted">
          没有不安全构造时这一步自动通过；有的话必须由你勾选确认，系统不会替你点这个勾。
        </p>
      </div>

      <div v-else-if="wizardStep === 5">
        <p class="muted">
          草案就是上面「策略 DSL」编辑器里的那份文本（同一个 draft，改它请回到那张卡）。
          这一步只检查它还是不是一个合法的 JSON 对象，判定它是否可用是第 6 步的事。
        </p>
        <p :class="draftIsJson ? 'muted' : 'error'">
          <template v-if="draftIsJson">
            当前文本是合法 JSON 对象：{{ draftIndicatorCount }} 个指标声明、{{ draftEntryCount }} 条入场条件。
          </template>
          <template v-else>当前文本不是合法的 JSON 对象，第 6 步无法校验。</template>
        </p>
        <pre class="code-block">{{ dslText }}</pre>
        <p class="muted">缺失的离场规则需要你手动补齐：导入器只写它看得懂的部分，不会编造。</p>
      </div>

      <div v-else-if="wizardStep === 6">
        <div class="row">
          <button :disabled="!draftIsJson || validating" @click="validateDraft">
            {{ validating ? '校验中…' : '校验这份草案' }}
          </button>
          <span class="muted">校验说的是「这份文本此刻」：改一个字它就过期，第 5 步会重新挡住你</span>
        </div>
        <p v-if="validation" :class="validation.is_valid ? 'muted' : 'error'">
          {{
            validation.is_valid
              ? '校验通过：没有 error 级问题，可以进入第 7 步。'
              : '校验未通过：下面是它列出的问题，先修文本再回来。'
          }}
        </p>
        <table v-if="validation && validation.issues.length">
          <thead>
            <tr>
              <th>级别</th>
              <th>代码</th>
              <th>说明</th>
              <th>路径</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(issue, idx) in validation.issues" :key="idx">
              <td>{{ issue.severity }}</td>
              <td>{{ issue.code }}</td>
              <td>{{ issue.message }}</td>
              <td class="muted">{{ issue.path ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else-if="validation" class="muted">没有列出任何问题。</p>
        <p v-if="validation" class="muted">
          校验器认识这些列：{{ validation.available_columns.join('、') }}；不在其中的名字一律被它否决。
        </p>
        <p v-else class="muted">还没有校验过这份草案。</p>
      </div>

      <div v-else-if="wizardStep === 7">
        <p class="muted">
          导入会把这个 commit 的分析结果写成一份不可变版本：来源、commit、哈希与校验状态一起存档，之后不可改写。
        </p>
        <div class="row" style="margin-top: 10px">
          <input v-model="importName" style="max-width: 260px" placeholder="策略名称" />
          <input
            v-model="importVersion"
            style="max-width: 200px"
            placeholder="版本（留空 = 服务器分配）"
          />
          <button
            :disabled="importing || namedVersionTaken || !validatedDraft"
            @click="importReviewed"
          >
            {{ importing ? '导入中…' : '确认导入' }}
          </button>
        </div>
        <p v-if="plannedVersionNote" :class="namedVersionTaken ? 'error' : 'muted'">
          {{ plannedVersionNote }}
        </p>
      </div>

      <p v-else class="muted">这一步需要先有分析结果：请回到第 1 步。</p>

      <div class="row" style="margin-top: 12px">
        <button class="ghost" :disabled="wizardStep === 1" @click="gotoStep(wizardStep - 1)">
          上一步
        </button>
        <button
          v-if="wizardStep < 7"
          class="ghost"
          :disabled="!stepUnlocked[wizardStep]"
          @click="gotoStep(wizardStep + 1)"
        >
          下一步：{{ WIZARD_STEPS[wizardStep] }}
        </button>
        <span v-if="wizardStep < 7 && !stepUnlocked[wizardStep]" class="muted">
          {{ stepBlockedReason }}
        </span>
      </div>
    </div>

    <div v-if="ghSources.length" class="card" style="margin-top: 14px">
      <h3>已导入来源（自动监视更新）</h3>
      <table>
        <thead>
          <tr>
            <th>仓库</th>
            <th>当前 commit</th>
            <th>最近检查</th>
            <th>状态</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="s in ghSources" :key="s.id">
            <tr>
              <td>{{ s.repository_url }}</td>
              <td class="muted">{{ String(s.current_commit || '').slice(0, 12) || '—' }}</td>
              <td class="muted">{{ s.last_checked_at ? formatDateTime(String(s.last_checked_at)) : '—' }}</td>
              <td>
                <span v-if="ghUpdates[s.id]" class="badge WAIT">有更新</span>
                <span v-else :class="sourceStatusTone(s.last_import_status)">
                  {{ sourceStatusLabel(s.last_import_status) }}
                </span>
                <span v-if="s.pending_review_commit" class="muted" style="display: block">
                  待审阅 commit <code>{{ shortCommit(String(s.pending_review_commit)) }}</code>
                </span>
              </td>
              <td>
                <button class="ghost" :disabled="checkingSource === s.id" @click="checkSourceNow(s)">
                  {{ checkingSource === s.id ? '检查中…' : '检查更新' }}
                </button>
                <button class="ghost" @click="toggleSourceSnapshots(s)">
                  {{ ghSnapshotOpen === Number(s.id) ? '收起详情' : '详情' }}
                </button>
              </td>
            </tr>
            <tr v-if="ghSnapshotOpen === Number(s.id)">
              <td colspan="5">
                <p v-if="ghSnapshotLoading === Number(s.id)" class="muted">读取检查记录…</p>
                <template v-else>
                  <p
                    :class="
                      [
                        'incomplete_analysis',
                        'unparseable_python',
                        'requires_review',
                        'import_failed',
                      ].includes(String(latestSnapshot(s)?.extraction?.reason ?? ''))
                        ? 'error'
                        : 'muted'
                    "
                  >
                    {{ snapshotExplanation(latestSnapshot(s)) }}
                  </p>
                  <p v-if="snapshotCoverage(latestSnapshot(s))" class="muted">
                    {{ snapshotCoverage(latestSnapshot(s)) }}
                  </p>
                  <ul v-if="snapshotWarnings(latestSnapshot(s)).length" class="error">
                    <li v-for="(w, i) in snapshotWarnings(latestSnapshot(s))" :key="i">{{ w }}</li>
                  </ul>
                  <p v-if="latestSnapshot(s)" class="muted" style="margin-bottom: 0">
                    本次检查的 commit <code>{{ String(latestSnapshot(s)?.commit || '').slice(0, 12) }}</code>
                    · {{ latestSnapshot(s)?.fetched_at ? formatDateTime(String(latestSnapshot(s)?.fetched_at)) : '—' }}
                    <span v-if="(ghSnapshots[Number(s.id)]?.length ?? 0) > 1">
                      · 共 {{ ghSnapshots[Number(s.id)]?.length }} 条记录（只显示最近一条）
                    </span>
                  </p>
                </template>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <p class="muted" style="margin-bottom: 0">
        Worker 每日自动检查；发现新 commit 时会重新解析并（若 DSL 变化）自动生成新策略版本。
      </p>
    </div>

    <div class="card" style="margin-top: 14px">
      <h3>说明</h3>
      <p class="muted">
        DSL 只允许使用已注册的指标与价格行为特征；引用未来数据（例如 <code>future_close</code>）会被校验器直接拒绝。
        停止与目标默认按 ATR 倍数计算，成交模型为「信号确认后的下一根 K 线开盘价」。
      </p>
    </div>
  </div>
</template>
