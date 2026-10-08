<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  api,
  ApiError,
  type AIResearchCapabilityReport,
  type AIResearchDraftConfirmation,
  type AIResearchDraftContent,
  type AIResearchDraftDecision,
  type AIResearchHypothesisContent,
  type AIResearchRun,
  type AIResearchRunSummary,
  type AIResearchSourceInput,
  type AIResearchSourceMeta,
  type AIResearchViolation,
  type AIResearchWarning,
  type Asset,
  type BacktestSummary,
  type CompileRejection,
  type ExperimentCompareOut,
  type ExperimentCreatePayload,
  type ExperimentDetailOut,
  type ExperimentKind,
  type ExperimentResultOut,
  type ExperimentSummaryOut,
  type Strategy,
  type StrategyVersion,
} from '@/api'
import MetricHint from '@/components/MetricHint.vue'
import { isAdvanced } from '@/mode'
import { formatDateTime, formatMetric, toneOf } from '@/format'
import { metricKeyLabel, qualityLabel, timeframeLabel, validationLabel } from '@/wording'

// 「AI 研究实验室」（§17 + v2.4.0 的编译/激活）：把一条最小但诚实的链路走完——
// 研究输入 → AI 理解 → 策略假设 → 策略草案 → 人工确认 → 编译 → 策略版本 → 激活 → 回测。
//
// 这一页刻意不做的事，也不假装做了：
// ① 草案本身永远不可执行（`executable === false`）：编译之前没有策略、没有回测，
//    所以草案里不会出现收益、回撤、夏普这类数字（模型写了会被标成未验证）；
// ② AI 自己补的假设必须被看见：`origin === 'ASSUMED'` 的规则在普通模式和高级
//    模式下都会写明「AI 提出的假设，不是你的原话」；
// ③ 能力结论用服务端算出来的那一份（`capability_status` / `capability_report`）；
// ④ 「编译」与「激活」是两件事，也是两道不同的门：编译需要人工确认过草案，激活
//    需要人来点（`window.confirm` 守卫）。两处都由服务端兜底，页面只是照实说。
//
// 普通模式（默认）只讲人话：AI 理解到了什么、规则说了什么、还缺什么、能不能编译、
// 版本是不是当前版本。来源徽标、置信度、派生关系、能力 token、违规代码、运行号、
// 编译报告的原始 JSON 只在高级模式或折叠区里出现。

const QUESTION_MIN = 3
const QUESTION_MAX = 4000
const SOURCE_MAX = 40000
const MAX_SOURCES = 8

// 后台研究是异步的：POST 只把运行排进队列（202 + status="queued"），所以状态要靠轮询。
const POLL_MS = 2000
const POLL_FAILURE_LIMIT = 3
/** 等多久之后补一句「可以关掉这一页」的说明。 */
const POLL_HINT_SECONDS = 60

const router = useRouter()

const question = ref('')
const sourceLabel = ref('')
const sourceText = ref('')

// 材料可以是自己贴的一段文字，也可以是一个网址（ADR-191 之外的另一处「不要假装没有」：
// 后端从 v2.1.0 起就能抓网页，`POST /ai/sources/url` 带着 SSRF 守卫，只有这一页没接）。
const materialKind = ref<'text' | 'url'>('text')
const sourceUri = ref('')
const ingesting = ref(false)
const ingestError = ref('')
const ingestNote = ref('')
/** 第三方网页默认只把前 500 字交给 AI；声明「我有权使用」才保留全文。 */
const sourceFullRetention = ref(false)

const run = ref<AIResearchRun | null>(null)
const runs = ref<AIResearchRunSummary[]>([])
const busy = ref(false)
const formalizing = ref(false)
const confirming = ref(false)
const confirmationNote = ref('')
const loadingRuns = ref(false)
const loadingRun = ref(false)
const error = ref('')
const notice = ref('')
const notConfigured = ref(false)
const configDetail = ref('')

// 轮询状态。`polling` 是给模板看的镜像：`pollTimer` 本身不是响应式的。
let pollTimer: number | undefined
const polling = ref(false)
const pollFailures = ref(0)
const pollStartedAt = ref<number | null>(null)
const elapsedSeconds = ref(0)

// 编译 / 版本：这三块各自有独立的错误位置，因为它们在页面下方，顶部那条提示看不见。
const strategies = ref<Strategy[]>([])
const loadingStrategies = ref(false)
const compileStrategyId = ref('')
const newStrategyName = ref('')
const creatingStrategy = ref(false)
const compiling = ref(false)
const activating = ref(false)
const compileError = ref('')
const compileDetail = ref('')
const versionError = ref('')
const compiledVersion = ref<StrategyVersion | null>(null)
const loadingVersion = ref(false)
const compileReport = ref<Record<string, any> | null>(null)
/** 编译器自己拒绝（422）时的结论；`null` = 这次编译没有 422。 */
const compileOutcome = ref<{ result: string; report: Record<string, any> | null } | null>(null)
/** 已经为哪一份版本发过读回请求：避免每次轮询都重复发一次。 */
const requestedVersionId = ref<number | null>(null)

// 交接口的标的选择（ADR-191）：标的不是策略内容——DSL 里没有它，编译器把「标的」
// 归到不可表达那一类（`backend/app/compiler/compiler.py` 的 `not_expressible`），
// 它属于运行参数。所以这一页在交接口问一次，而不是替用户猜一个。
const assets = ref<Asset[]>([])
const seriesList = ref<Array<Record<string, unknown>>>([])
const loadingSeries = ref(false)
const backtestSeriesId = ref<number | null>(null)

type BacktestTarget = {
  symbol: string
  seriesId: number
  timeframe: string
  start: string
  end: string
  quality: string
}

/** 与「研究策略」页第①步同一个来源、同一套映射（那里是 `symbolOptions`）。 */
const backtestTargets = computed<BacktestTarget[]>(() =>
  seriesList.value
    .filter((s) => !s.is_archived)
    .map((s) => ({
      symbol: assetSymbol(Number(s.asset_id)),
      seriesId: Number(s.id),
      timeframe: String(s.timeframe ?? ''),
      start: String(s.series_start ?? '').slice(0, 10),
      end: String(s.series_end ?? '').slice(0, 10),
      quality: String(s.quality_status ?? 'unknown'),
    }))
    .sort((a, b) => a.symbol.localeCompare(b.symbol)),
)

function assetSymbol(assetId: number): string {
  const found = assets.value.find((a) => a.id === assetId)
  return found?.symbol ?? `#${assetId}`
}

const chosenBacktestTarget = computed<BacktestTarget | null>(
  () => backtestTargets.value.find((t) => t.seriesId === backtestSeriesId.value) ?? null,
)

/** 灰按钮必须说明为什么（ADR-138）；没有数据时给一条出口，而不是一个点不动的按钮。 */
const backtestBlockedReason = computed(() => {
  if (loadingSeries.value) return '正在读你同步过的行情数据…'
  if (!backtestTargets.value.length) return ''
  if (chosenBacktestTarget.value === null) {
    return '先选一个标的：策略规则里没有「买什么」，得由你指定用哪份数据验证这一版。'
  }
  return ''
})

// --------------------------------------------------------------------------- //
// 输入校验：把后端会拒绝的情况提前说清楚，而不是等 400 回来
// --------------------------------------------------------------------------- //
const questionProblem = computed(() => {
  const text = question.value.trim()
  if (!text) return `先写一个研究问题（至少 ${QUESTION_MIN} 个字）：你想让 AI 帮你看清什么？`
  if (text.length < QUESTION_MIN) return `研究问题至少 ${QUESTION_MIN} 个字，太短的问句 AI 只能替你猜。`
  if (text.length > QUESTION_MAX) return `研究问题最多 ${QUESTION_MAX} 个字，现在有 ${text.length} 个。`
  return ''
})

const sourceProblem = computed(() => {
  const text = sourceText.value
  if (!text.trim()) return '至少贴一段材料：研报摘录、公告、你自己的策略想法都可以。'
  if (text.length > SOURCE_MAX) {
    return `一段材料最多 ${SOURCE_MAX} 个字，现在有 ${text.length} 个；请拆成几段分开研究。`
  }
  return ''
})

const uriProblem = computed(() => {
  if (materialKind.value !== 'url') return ''
  const uri = sourceUri.value.trim()
  if (!uri) return '贴一个网址：https:// 开头的网页地址。'
  if (!/^https?:\/\//i.test(uri)) return '网址要以 http:// 或 https:// 开头。'
  if (uri.length > 2048) return `网址最多 2048 个字符，现在有 ${uri.length} 个。`
  return ''
})

/** 当前这一种材料是否已经填好；两种材料一次只用一种。 */
const materialProblem = computed(() =>
  materialKind.value === 'url' ? uriProblem.value : sourceProblem.value,
)

const inputProblem = computed(() => questionProblem.value || materialProblem.value)
const canSubmit = computed(() => !inputProblem.value && !busy.value && !ingesting.value)
const blockedReason = computed(() => (busy.value ? '' : inputProblem.value))

// --------------------------------------------------------------------------- //
// 结果投影
// --------------------------------------------------------------------------- //
const hypothesisContent = computed<AIResearchHypothesisContent | null>(
  () => run.value?.hypothesis?.content ?? null,
)
const draftContent = computed<AIResearchDraftContent | null>(() => run.value?.draft?.content ?? null)
const capability = computed<AIResearchCapabilityReport | null>(
  () => run.value?.draft?.capability_report ?? null,
)
const violations = computed<AIResearchViolation[]>(() => run.value?.violations ?? [])
const warnings = computed<AIResearchWarning[]>(() => run.value?.warnings ?? [])
// 人工确认：后端只回最新一次；null / 缺失 = 还没有人拍过板。
const confirmation = computed<AIResearchDraftConfirmation | null>(
  () => run.value?.draft?.confirmation ?? null,
)
// 这份草案编译出来的版本号；有值 = 编译过了，页面该显示版本而不是「编译」按钮。
const draftCompiledVersionId = computed<number | null>(
  () => run.value?.draft?.compiled_strategy_version_id ?? null,
)

const DECISION_LABELS: Record<string, string> = {
  confirmed: '已确认',
  rejected: '已驳回',
  needs_revision: '需修改',
}

// 未知取值不把引擎的词直接印在普通模式上，说一句人话就够。
function decisionLabel(decision: string | null | undefined): string {
  if (!decision) return '尚未人工确认'
  return DECISION_LABELS[decision] ?? '已记录人工判断'
}

const STATUS_LABELS: Record<string, string> = {
  pending: '已排队，还没开始',
  queued: '已排队，正在后台执行',
  running: 'AI 正在分析',
  completed: '已完成',
  rejected: '没有通过校验',
  failed: '没有跑完',
}

function statusLabel(status: string | null | undefined): string {
  if (!status) return '—'
  return STATUS_LABELS[status] ?? status
}

/** 还在跑（或还没开始跑）的状态：轮询只在这些状态上有意义。 */
function isLiveStatus(status: string | null | undefined): boolean {
  return status === 'queued' || status === 'running' || status === 'pending'
}

const liveSentence = computed(() => {
  const status = run.value?.status
  if (status === 'queued') {
    return '这次研究已经排队，后台正在准备执行。这一步不需要你等着：页面可以关掉，稍后在下面的「最近的研究」里重新打开就能看到结果。'
  }
  if (status === 'running') {
    return `AI 正在读材料、写假设与草案。页面每 ${POLL_MS / 1000} 秒自动读一次状态，这一步不需要你等着，也可以先去忙别的。`
  }
  return ''
})

const statusSentence = computed(() => {
  const status = run.value?.status
  if (status === 'completed') return 'AI 已经读完材料，下面是它给出的理解、假设、草案和能力结论。'
  if (status === 'rejected') return 'AI 的回答没有通过校验。系统没有替它修改，也没有把这份回答当成结论存下来。'
  if (status === 'failed') return '这次研究没有跑完，下面是后端记下来的原因。'
  if (status === 'queued' || status === 'running') return '这次研究还在后台跑，页面会自动更新状态。'
  return ''
})

// --------------------------------------------------------------------------- //
// 人话词汇表
// --------------------------------------------------------------------------- //
const FIELD_LABELS: Record<string, string> = {
  market: '市场',
  universe: '标的范围',
  timeframe: '周期',
  indicator: '指标',
  entry: '入场',
  exit: '出场',
  risk: '风控',
  sizing: '仓位',
  execution: '成交',
  parameter: '参数',
}

function fieldLabel(field: string | null | undefined): string {
  if (!field) return '其他'
  return FIELD_LABELS[field] ?? field
}

function fieldList(values: string[] | undefined): string {
  return (values ?? []).map((value) => fieldLabel(value)).join('、')
}

const CONFIDENCE_LABELS: Record<string, string> = { low: '低', medium: '中', high: '高' }

function confidenceLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return CONFIDENCE_LABELS[value] ?? value
}

/** 已知缺口的中文说法；表里没有的就原样显示，不替后端编名字。 */
const CAPABILITY_LABELS: Record<string, string> = {
  short_selling: '做空',
  cross_sectional_universe: '全市场选股 / 横截面排序',
  portfolio_rules: '组合构建（多标的权重与再平衡）',
  leverage: '杠杆与保证金',
  market_microstructure: '交易所规则（T+1、涨跌停、最小交易单位、交易日历）',
  var_cvar: 'VaR / CVaR 风险指标',
  calmar: '卡玛比率与恢复因子',
  fundamentals: '基本面数据',
  news: '新闻与情绪数据',
  vision: '图表 / 截图理解',
  rag: '向量检索（RAG）',
  live_execution: '连接券商的实盘执行',
}

function capabilityLabel(token: string): string {
  const key = String(token).trim().toLowerCase().replace(/ /g, '_')
  const bare = key.includes(':') ? key.split(':')[1] : key
  return CAPABILITY_LABELS[bare] ?? token
}

function capabilityList(tokens: string[] | undefined): string {
  return (tokens ?? []).map((token) => capabilityLabel(token)).join('、')
}

const VERDICT_LABELS: Record<string, string> = {
  SUPPORTED: '完整支持',
  PARTIALLY_SUPPORTED: '部分支持',
  NEEDS_CAPABILITY: '缺少能力',
}

function verdictLabelOf(value: string | null | undefined): string {
  if (!value) return '—'
  return VERDICT_LABELS[value] ?? value
}

const verdict = computed(() => run.value?.capability_status ?? capability.value?.verdict ?? '')

const verdictSentence = computed(() => {
  switch (verdict.value) {
    case 'SUPPORTED':
      return '完整支持：这份草案需要的能力，系统都有对应的实现。'
    case 'PARTIALLY_SUPPORTED':
      return '部分支持：这份草案里有一部分能按原样实现，另一部分需要的能力系统还没有。缺的是什么、影响哪一条规则，下面都列出来了。'
    case 'NEEDS_CAPABILITY':
      return '缺少能力：这份草案最需要的能力，系统目前都没有，所以它没法按原样实现。'
    default:
      return ''
  }
})

const verdictTone = computed(() => {
  if (verdict.value === 'SUPPORTED') return 'ok'
  if (verdict.value === 'PARTIALLY_SUPPORTED') return 'warn'
  return 'bad'
})

/** 违规代码 → 人话。后端同时给了英文 message，高级模式里原样保留。 */
const VIOLATION_REASONS: Record<string, string> = {
  fabricated_metric:
    'AI 给出了收益、回撤、夏普这类回测结果数字。这类数字只能由引擎算出来，而这一版没有跑过任何回测。',
  forbidden_content: 'AI 的回答里出现了不该由它决定的内容（例如可以直接执行的定义、密钥或命令）。',
  schema_invalid: 'AI 的回答不符合约定的字段要求。',
  evidence_missing: '有一条规则标成「原文明确写到」，却没有指出它引用的是哪一段材料。',
  evidence_unknown_source: '有一条规则引用了一段本次研究根本没有提供的材料。',
  assumed_not_disclosed: '有一条规则是 AI 自己补的假设，却没有在假设清单里说明。',
  unknown_not_disclosed: '有一条规则标成「原文没说清楚」，但未知清单里没有提到它。',
  duplicate_rule_id: '规则编号重复了，无法分辨说的是哪一条。',
  domain_invalid: '内容不完整：例如某条规则是空的，或者一个含糊说法没有列出可能的读法。',
  not_executable: 'AI 把草案标成了「可以直接运行」。这一版任何草案都不可执行。',
  new_rule_must_be_assumed: 'AI 自己新增了一条原文里没有的规则，却标成「原文明确写到」。',
  unknown_derivation: '草案里有一条规则说它来自某条假设规则，但那条规则不存在。',
  derivation_field_mismatch: '草案规则和它来源的假设规则说的不是同一件事（例如一个说入场、一个说风控）。',
  provenance_stronger_than_hypothesis: '草案把一条规则的来源说得比研究阶段更确定。',
  dropped_explicit_rule: '原文里明确写到的规则，草案既没有落实，也没有列为未知。',
  dropped_unknown: '研究阶段留下的未知项，在草案里消失了。',
  unknown_affected_rule: '有一条能力说它影响某条规则，但那条规则不存在。',
  alternative_not_marked_experimental: 'AI 提出的替代做法没有标明是「实验性缩减版」。',
  capability_overclaim: '草案声称系统支持某些能力，但服务端的能力清单并不支持。',
}

function violationReason(item: AIResearchViolation): string {
  return VIOLATION_REASONS[item.code] ?? `未归类的校验项：${item.code}`
}

function warningText(item: AIResearchWarning): string {
  if (item.kind === 'truncated') {
    return `材料「${item.source_ref ?? '未命名'}」太长，只读了前面 ${item.kept_chars ?? '—'} 个字（原本 ${item.original_chars ?? '—'} 个）。`
  }
  if (item.kind === 'UNVERIFIED') {
    return `AI 的回答（或它读到的材料）里出现了「${item.metric ?? '结果数字'}」这样的数字：${item.text ?? ''}。这个系统没有算过它，也无法确认它。`
  }
  return item.note ?? item.text ?? '有一条需要你注意的提醒。'
}

function sourceTitle(item: AIResearchSourceMeta, index: number): string {
  return item.label?.trim() || `材料 ${index + 1}`
}

const draftMarketSentence = computed(() => {
  const market = draftContent.value?.market
  if (!market) return ''
  const parts: string[] = []
  if (market.markets?.length) parts.push(`市场：${market.markets.join('、')}`)
  if (market.asset_classes?.length) parts.push(`品种：${market.asset_classes.join('、')}`)
  if (market.timeframes?.length) parts.push(`周期：${market.timeframes.join('、')}`)
  if (market.universe) parts.push(`标的范围：${market.universe}`)
  return parts.join(' · ')
})

// --------------------------------------------------------------------------- //
// 编译：拒绝码 → 人话。这些码是服务端的契约（ADR-167/ADR-171），不是文案。
// --------------------------------------------------------------------------- //
const COMPILE_ERROR_REASONS: Record<string, string> = {
  draft_not_confirmed:
    '这份草案还没有人工确认过，所以服务端不允许编译。先在上面「人工确认」里选一个结论——只有「确认」过的草案才允许编译。',
  draft_already_compiled:
    '这份草案已经编译过了，同一份草案不会编译出第二个版本。下面显示的就是它生成的策略版本。',
  version_unassignable:
    '目标策略现有的版本号读不出「主版本.次版本.补丁」的形式，服务端没法自动算下一个版本号。换一个策略，或者先去「我的策略」把这个策略的版本整理成这种格式。',
  version_conflict:
    '这个版本号刚刚已经被占用了（可能是别处同时编译出来的）。再点一次「编译为策略版本」，服务端会分配下一个空闲版本号。',
  strategy_not_found:
    '选中的策略已经不存在了。点「刷新策略列表」重新选一个目标策略。',
  draft_not_found:
    '这条草案后端已经找不到了。点「最近的研究」里的「刷新列表」，然后重新打开这次研究。',
}

/** 编译器拒绝码 → 人话（docs/29 §9.1 的固定词表）。 */
const REJECTION_REASONS: Record<string, string> = {
  needs_user_decision: '有一处必须由你来定：编译器不会替你在两种读法之间选一个。',
  unknown_blocks_slot: '有一条规则用到了原文没说清的字段，而这个字段是必须填的。',
  ambiguous_phrase: '草案里有一句话可以有两种读法，需要人先定下来。',
  capability_missing: '草案需要的能力，系统目前没有实现。',
  not_expressible: '草案里的内容无法用这套策略语言表达。',
  indicator_unmapped: '草案用到的指标，在指标库里找不到对应的实现。',
  parameter_invalid: '某个参数的值不合法（超出范围、类型不对，或者这份草案本身读不出来）。',
  rule_unmapped: '有一条规则没法落到策略语言里的任何位置。',
  rule_conflict: '有两条规则互相冲突，同时生效会自相矛盾。',
  indicator_collision: '两个不同的指标被映射成了同一个名字。',
  missing_required_slot: '策略必须填的某个位置（例如入场或风控）是空的。',
  validation_failed: '编译出来的策略定义没有通过校验器。',
  engine_incompatible: '编译出来的定义，回测引擎跑不了。',
  provenance_invalid: '规则来源的标记不合法。',
  version_conflict: '版本号冲突。',
}

function rejectionReason(item: CompileRejection): string {
  return REJECTION_REASONS[item.code] ?? `编译器给出的原因（未归类：${item.code}）`
}

const compileOutcomeLabel = computed(() => {
  const result = compileOutcome.value?.result
  if (result === 'NEEDS_USER_DECISION') return '需要你先做决定'
  if (result === 'REJECTED') return '编译器拒绝了'
  return result || '编译器没有通过'
})

const compileOutcomeTone = computed(() =>
  compileOutcome.value?.result === 'NEEDS_USER_DECISION' ? 'warn' : 'bad',
)

const compileOutcomeSentence = computed(() => {
  const result = compileOutcome.value?.result
  if (result === 'NEEDS_USER_DECISION') {
    return '编译器在草案里遇到了必须由人来定的地方。它不会替你做这个决定，所以这次没有生成策略版本，草案也没有被改动。'
  }
  if (result === 'REJECTED') {
    return '编译器没法把这份草案变成一条可执行的策略。下面每一条都是它给出的原因；这次没有生成策略版本，草案也没有被改动。'
  }
  return '编译器没有生成策略版本。'
})

const compileRejections = computed<CompileRejection[]>(() => {
  const raw = compileOutcome.value?.report?.rejections
  return Array.isArray(raw) ? (raw as CompileRejection[]) : []
})

/** 折叠区里的原始报告：刚编译成功的那份，或这次 422 的那份。 */
const compileReportText = computed(() => {
  const report = compileReport.value ?? compileOutcome.value?.report ?? null
  return report ? JSON.stringify(report, null, 2) : ''
})

const targetStrategyId = computed<number | null>(() => {
  const value = Number(compileStrategyId.value)
  return Number.isInteger(value) && value > 0 ? value : null
})

const canCompile = computed(
  () =>
    !compiling.value &&
    !!run.value?.draft &&
    !draftCompiledVersionId.value &&
    confirmation.value?.decision === 'confirmed' &&
    targetStrategyId.value !== null,
)

/** 编译按钮为什么是灰的——逐条说清，服务端的同一条规则在这里提前讲。 */
const compileBlockedReason = computed(() => {
  const draft = run.value?.draft
  if (!draft) return ''
  if (draftCompiledVersionId.value) {
    return '这条草案已经编译过了：下面是它生成的策略版本，不能再编译第二次。'
  }
  if (!confirmation.value) {
    return '还没有人工确认。先在上面「人工确认」里选一个结论；服务端只接受「已确认」的草案，所以现在按不了。'
  }
  if (confirmation.value.decision !== 'confirmed') {
    return `当前人工结论是「${decisionLabel(confirmation.value.decision)}」，不是「已确认」。要编译请重新选「确认」。`
  }
  if (targetStrategyId.value === null) {
    return '还没有选目标策略：在下面挑一个已存在的策略，或者新建一个——编译出来的版本要挂在某个策略下面。'
  }
  return ''
})

// --------------------------------------------------------------------------- //
// 请求
// --------------------------------------------------------------------------- //
function resetNotices() {
  error.value = ''
  notice.value = ''
  notConfigured.value = false
  configDetail.value = ''
  compileError.value = ''
  compileDetail.value = ''
  versionError.value = ''
  compileOutcome.value = null
}

/** 503 = 没有可用的 AI 提供方：这不是页面错误，是配置缺失，单独渲染。 */
function handleFailure(e: unknown, target: 'page' | 'compile' | 'version' = 'page') {
  if (e instanceof ApiError && e.status === 503) {
    notConfigured.value = true
    configDetail.value = e.message
    return
  }
  const text = (e as Error).message
  if (target === 'compile') compileError.value = text
  else if (target === 'version') versionError.value = text
  else error.value = text
}

async function loadRuns() {
  loadingRuns.value = true
  try {
    const result = await api.aiResearchRuns()
    runs.value = result.runs ?? []
  } catch (e) {
    error.value = error.value || (e as Error).message
  } finally {
    loadingRuns.value = false
  }
}

async function loadStrategies() {
  loadingStrategies.value = true
  try {
    strategies.value = await api.strategies()
    // 只有一个策略时替用户选上；有多个就不猜，让用户自己选，避免编译到不相干的策略上。
    if (strategies.value.length === 1 && targetStrategyId.value === null) {
      compileStrategyId.value = String(strategies.value[0].id)
    }
  } catch (e) {
    handleFailure(e, 'compile')
  } finally {
    loadingStrategies.value = false
  }
}

// --------------------------------------------------------------------------- //
// 交接用的标的：数据是「研究策略」页和「数据」页的同一份，这里只读不写
// --------------------------------------------------------------------------- //
async function loadBacktestTargets() {
  loadingSeries.value = true
  try {
    const [assetRows, seriesRows] = await Promise.all([api.assets(), api.series()])
    assets.value = assetRows
    seriesList.value = seriesRows as Array<Record<string, unknown>>
    // 和 `loadStrategies` 同一条规矩：只有一个候选时才替用户选上；有多个就不猜，
    // 因为「用哪份数据验证」是用户的决定，猜错会让他拿错标的的结论。
    if (backtestTargets.value.length === 1 && backtestSeriesId.value === null) {
      backtestSeriesId.value = backtestTargets.value[0].seriesId
    }
  } catch (e) {
    // 读不到数据不该挡住这一页：研究本身不依赖它，只有交接那一栏会说明「先同步数据」。
    error.value = error.value || (e as Error).message
  } finally {
    loadingSeries.value = false
  }
}

// --------------------------------------------------------------------------- //
// 轮询：POST 只返回 202 + status="queued"，真正的结果要自己读回来
// --------------------------------------------------------------------------- //
function stopPolling() {
  if (pollTimer !== undefined) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
  polling.value = false
}

function startPolling() {
  stopPolling()
  pollFailures.value = 0
  pollStartedAt.value = Date.now()
  elapsedSeconds.value = 0
  polling.value = true
  pollTimer = window.setInterval(() => {
    void tick()
  }, POLL_MS)
}

async function tick() {
  const current = run.value
  if (!current) {
    stopPolling()
    return
  }
  if (pollStartedAt.value !== null) {
    elapsedSeconds.value = Math.floor((Date.now() - pollStartedAt.value) / 1000)
  }
  try {
    const fresh = await api.aiResearchRun(current.run_id)
    pollFailures.value = 0
    applyRun(fresh)
    if (!isLiveStatus(fresh.status)) {
      stopPolling()
      if (fresh.status === 'completed') {
        notice.value = '这次研究已经跑完，下面是它的结果。'
      }
      await loadRuns()
    }
  } catch (e) {
    pollFailures.value += 1
    // 连续读不到就停：一个永远转下去的圈比一句错误更糟。
    if (pollFailures.value >= POLL_FAILURE_LIMIT) {
      stopPolling()
      error.value = `连续 ${POLL_FAILURE_LIMIT} 次都没读到这次运行的状态（${(e as Error).message}）。已经停止自动刷新，可以点「刷新状态」再试一次。`
    }
  }
}

/** 把一次运行读回页面：换运行就清掉上一条的编译结果，状态变了就开/关轮询。 */
function applyRun(fresh: AIResearchRun) {
  const previousRunId = run.value?.run_id ?? null
  run.value = fresh
  rememberOpenRun(fresh.run_id)
  if (previousRunId !== fresh.run_id) {
    compiledVersion.value = null
    compileReport.value = null
    compileOutcome.value = null
    requestedVersionId.value = null
    elapsedSeconds.value = 0
  }
  const versionId = fresh.draft?.compiled_strategy_version_id ?? null
  if (versionId) {
    if (requestedVersionId.value !== versionId) {
      requestedVersionId.value = versionId
      // 编译报告不随运行保存，重新打开这次研究只能看到版本本身。
      compileReport.value = null
      void loadCompiledVersion(versionId)
    }
  } else if (compiledVersion.value) {
    compiledVersion.value = null
    compileReport.value = null
    requestedVersionId.value = null
  }
  // 已经在轮询就不要重启：重启会把计时清零，进度看起来像卡住了。
  if (isLiveStatus(fresh.status)) {
    if (!polling.value) startPolling()
  } else {
    stopPolling()
  }
}

async function loadCompiledVersion(versionId: number) {
  loadingVersion.value = true
  try {
    const all = await api.allStrategyVersions()
    const found = all.find((item) => item.id === versionId) ?? null
    compiledVersion.value = found
    if (!found) {
      versionError.value = `这次编译生成的策略版本 #${versionId} 现在读不到了（可能已经被删除）。`
    }
  } catch (e) {
    handleFailure(e, 'version')
  } finally {
    loadingVersion.value = false
  }
}

// --------------------------------------------------------------------------- //
// 动作
// --------------------------------------------------------------------------- //
async function submit() {
  if (inputProblem.value || busy.value || ingesting.value) return
  resetNotices()
  stopPolling()
  busy.value = true
  run.value = null
  try {
    const source = await buildSource()
    // 材料没准备好（网址抓不到 / 被拒绝）就到此为止：绝不拿一次失败的抓取去换一次 AI 调用。
    if (!source) return
    // 后端默认异步：这里拿到的大多是 202 + status="queued"，也可能（异步关闭时）
    // 直接是终态。applyRun 两种都处理：是终态就不轮询。
    applyRun(await api.aiResearchStart({ question: question.value.trim(), sources: [source] }))
    await loadRuns()
  } catch (e) {
    handleFailure(e)
  } finally {
    busy.value = false
  }
}

/** 声明「这份材料我有权使用」时随抓取一起交给服务端的说明（最多 2000 字）。 */
const FULL_RETENTION_NOTE = '用户在页面上声明：这份材料由本人拥有，或已获得保留全文的授权。'

/**
 * 把当前这一种材料变成交给服务端的一份 source。
 *
 * 网址这一步先让服务端去看一眼（`POST /ai/sources/url`）：抓不到就不返回 source，
 * 于是这次研究连排队都不会排。抓成功之后按 `snapshot_id` 交给研究，服务端只读回当时
 * 保留的摘要（`source_snapshot_service.material_from_snapshot`）——**不会再联网抓第二次**。
 */
async function buildSource(): Promise<AIResearchSourceInput | null> {
  const label = sourceLabel.value.trim()
  if (materialKind.value === 'text') {
    const source: AIResearchSourceInput = {
      text: sourceText.value,
      kind: 'user_input',
      source_ref: 'source_1',
    }
    if (label) source.label = label
    return source
  }

  const uri = sourceUri.value.trim()
  ingesting.value = true
  ingestError.value = ''
  ingestNote.value = ''
  try {
    const snapshot = await api.aiSourceUrl({
      uri,
      source_ref: 'source_1',
      ...(label ? { label } : {}),
      ...(sourceFullRetention.value
        ? { retention: 'full' as const, license_note: FULL_RETENTION_NOTE }
        : {}),
    })
    const kept = snapshot.retention?.retained_chars ?? 0
    ingestNote.value =
      `已读过 ${snapshot.final_uri || snapshot.original_uri || uri}：全文 ${snapshot.chars_read ?? 0} 个字，` +
      `服务端保留 ${kept} 个字（${snapshot.retention?.policy === 'full' ? '按你的声明保留全文' : '第三方材料只保留摘要'}）。`
    const source: AIResearchSourceInput = {
      kind: 'url',
      uri,
      snapshot_id: snapshot.snapshot_id,
      source_ref: 'source_1',
    }
    if (label) source.label = label
    return source
  } catch (e) {
    ingestError.value = ingestProblem(e)
    return null
  } finally {
    ingesting.value = false
  }
}

/**
 * 拒绝码 → 一句人话。
 *
 * 服务端的拒绝理由用英文装在 `detail.code` 里（`backend/app/sources/guard.py`、
 * `backend/app/sources/ingest.py`），这一层把它翻译成用户能照做的说法：
 * 「地址不能用」和「地址读不回来」是两件事，前者换地址，后者过一会儿再试。
 */
const SOURCE_REFUSAL_TEXT: Record<string, string> = {
  invalid_url: '这个地址不像一个网页地址，检查一下有没有多打空格。',
  scheme_not_allowed: '只支持 http 或 https 开头的网页地址。',
  credentials_not_allowed: '地址里不要带账号密码。',
  host_missing: '这个地址缺少网站名。',
  port_not_allowed: '这个地址用的端口不允许访问。',
  host_not_allowed: '这个地址指向本机或局域网，出于安全考虑不能抓。',
  dns_failed: '找不到这个网站名对应的地址。',
  dns_no_addresses: '这个网站名没有解析出可用地址。',
  address_unreadable: '这个网站名解析出来的地址读不到。',
  address_not_allowed: '这个地址解析后落在局域网内，出于安全考虑不能抓。',
  robots_disallowed: '这个网站声明不允许自动读取，换一个来源吧。',
  empty_response: '这个地址返回了空白内容，换一个来源吧。',
  response_too_large: '这个网页太大，超出了单次读取的上限。',
}

/** 抓取失败要说清是「这个地址不允许抓」还是「这个地址读不到」（ADR-138 的同一条规矩）。 */
function ingestProblem(e: unknown): string {
  // 注意：`/ai/sources/*` 的拒绝是 `{detail: {...}}`，没有 `error` 信封，所以
  // `ApiError.details` 是空的、`ApiError.message` 是那个对象；原因只能从 `body.detail` 读。
  const detail = (e instanceof ApiError ? e.body?.detail : null) as Record<string, any> | null
  const code = typeof detail?.code === 'string' ? detail.code : ''
  if (code) {
    const known = SOURCE_REFUSAL_TEXT[code]
    if (known) return known
    if (code.startsWith('parse_')) {
      return '这个地址能打开，但读出来的不是能用的正文（可能是 PDF 或需要脚本的页面）。'
    }
  }
  if (e instanceof ApiError && e.status === 422) {
    return '这个地址不允许抓：只支持公开网站的 http(s) 地址。'
  }
  if (e instanceof ApiError && e.status === 502) {
    return '这个地址没能读回来：网页打不开，或者没有可读的文字。'
  }
  const message = (e as Error)?.message
  return typeof message === 'string' && message ? message : '抓取这个地址时出了点问题。'
}

async function openRun(runId: number) {
  resetNotices()
  stopPolling()
  loadingRun.value = true
  try {
    applyRun(await api.aiResearchRun(runId))
  } catch (e) {
    handleFailure(e)
  } finally {
    loadingRun.value = false
  }
}

/** 手动刷新：不改动页面上的提示，只把状态读回来；读回来的还是进行中就会继续轮询。 */
async function refreshRun() {
  const current = run.value
  if (!current) return
  loadingRun.value = true
  error.value = ''
  try {
    applyRun(await api.aiResearchRun(current.run_id))
  } catch (e) {
    handleFailure(e)
  } finally {
    loadingRun.value = false
  }
}

async function formalize() {
  const current = run.value
  if (!current || formalizing.value) return
  resetNotices()
  stopPolling()
  formalizing.value = true
  try {
    await api.aiStrategyFormalize({ run_id: current.run_id })
    applyRun(await api.aiResearchRun(current.run_id))
    notice.value = '草案已经重新生成，下面是新的这一份。'
    await loadRuns()
  } catch (e) {
    // 被拒绝时后端回 422 + 结构化的违规清单，而不是一句人话；违规同时会落到
    // 这次运行上，所以重新读一次运行就能拿到真正的拒绝原因。
    if (e instanceof ApiError && e.status === 422) {
      try {
        applyRun(await api.aiResearchRun(current.run_id))
        notice.value = 'AI 这次给的草案没有通过校验，原因见下面的「AI 理解」卡片。'
      } catch {
        error.value = '这次改写没有通过校验，而且重新读取运行详情也失败了。'
      }
    } else {
      handleFailure(e)
    }
  } finally {
    formalizing.value = false
  }
}

// 人工确认：POST 之后重新读一次运行，页面上看到的永远是后端记下来的那一份。
async function confirmDraft(decision: AIResearchDraftDecision) {
  const current = run.value
  if (!current?.draft || confirming.value) return
  resetNotices()
  confirming.value = true
  try {
    const note = confirmationNote.value.trim()
    await api.confirmStrategyDraft(current.draft.draft_id, decision, note || undefined)
    applyRun(await api.aiResearchRun(current.run_id))
    confirmationNote.value = ''
    notice.value =
      decision === 'confirmed'
        ? '这次人工决定已经记下来了：已确认。草案本身没有被改动；现在可以往下走「编译为策略版本」了。'
        : `这次人工决定已经记下来了：${decisionLabel(decision)}。草案本身没有被改动，也不会生成策略版本——服务端只接受「已确认」的草案去编译。`
  } catch (e) {
    // 404 = 这条草案后端已经不在了；422 = 备注超长等可以在本地避免的输入问题。
    if (e instanceof ApiError && e.status === 404) {
      error.value = '这条草案后端已经找不到了：刷新「最近的研究」后重新打开这次研究。'
    } else {
      handleFailure(e)
    }
  } finally {
    confirming.value = false
  }
}

async function createStrategyInline() {
  const name = newStrategyName.value.trim()
  if (!name || creatingStrategy.value) return
  compiling.value = false
  creatingStrategy.value = true
  compileError.value = ''
  try {
    const created = await api.createStrategy(name)
    newStrategyName.value = ''
    strategies.value = [...strategies.value, created]
    compileStrategyId.value = String(created.id)
    notice.value = `策略「${created.name}」已经建好，并选为这次编译的目标策略。`
  } catch (e) {
    handleFailure(e, 'compile')
  } finally {
    creatingStrategy.value = false
  }
}

/** 编译拒绝时按码说人话；422 是编译器自己拒绝，正文是 `{result, report}`。 */
async function handleCompileFailure(e: unknown) {
  if (e instanceof ApiError && e.status === 422 && e.body && typeof e.body.result === 'string') {
    compileOutcome.value = {
      result: e.body.result,
      report: (e.body.report ?? null) as Record<string, any> | null,
    }
    return
  }
  const code = e instanceof ApiError ? e.code : null
  const known = code ? COMPILE_ERROR_REASONS[code] : undefined
  if (known) {
    // 这两条说明页面上的认知已经过时了（还没确认 / 已经编译过）：跟着服务端重读一次。
    if (code === 'draft_not_confirmed' || code === 'draft_already_compiled') {
      const current = run.value
      if (current) {
        try {
          applyRun(await api.aiResearchRun(current.run_id))
        } catch {
          /* 读不回来也不影响下面这句提示 */
        }
      }
    }
    compileError.value = known
    compileDetail.value = (e as ApiError).message
    return
  }
  handleFailure(e, 'compile')
}

async function compile() {
  const current = run.value
  const draft = current?.draft
  const strategyId = targetStrategyId.value
  if (!current || !draft || strategyId === null || compiling.value) return
  resetNotices()
  compiling.value = true
  compileReport.value = null
  try {
    const result = await api.compileStrategyDraft(draft.draft_id, strategyId)
    compileReport.value = result.report ?? null
    requestedVersionId.value = result.strategy_version_id
    await loadCompiledVersion(result.strategy_version_id)
    notice.value = `编译成功：草案已经冻结成策略版本 ${result.version}。它还不是当前版本——要让它生效，在下面点「激活为当前版本」。`
    applyRun(await api.aiResearchRun(current.run_id))
    await loadRuns()
  } catch (e) {
    await handleCompileFailure(e)
  } finally {
    compiling.value = false
  }
}

async function activate() {
  const version = compiledVersion.value
  if (!version || activating.value) return
  const ok = window.confirm(
    `确定把策略版本 ${version.version} 激活为当前版本吗？\n\n激活只改「哪一版是当前版本」：不改这一版的内容，不触发回测，也不会下单。之后可以随时再激活别的版本。`,
  )
  if (!ok) return
  versionError.value = ''
  notice.value = ''
  activating.value = true
  try {
    const updated = await api.activateVersion(version.id)
    compiledVersion.value = updated
    notice.value = `策略版本 ${updated.version} 现在是当前版本。要验证它，下一步去「回测」用这一版跑一次。`
  } catch (e) {
    handleFailure(e, 'version')
  } finally {
    activating.value = false
  }
}

/**
 * 交接到「回测」：把版本、标的、周期一次带过去，并要求那边直接跑（ADR-191）。
 *
 * 带 `run=1` 是「研究策略」页已经用了很久的做法（`frontend/src/views/ResearchView.vue`）：
 * 用户在交接口已经选过一次标的，到回测页不该再让他选第二次、更不该让他自己点「开始回测」。
 * 标的不是策略内容，所以它必须由人在这里指定；没选就按钮点不动（见 `backtestBlockedReason`）。
 */
function goToBacktest() {
  const version = compiledVersion.value
  const target = chosenBacktestTarget.value
  if (!version || !target) return
  void router.push({
    path: '/backtest',
    query: {
      strategy_version_id: String(version.id),
      symbol: target.symbol,
      timeframe: target.timeframe || '1d',
      run: '1',
    },
  })
}

// --------------------------------------------------------------------------- //
// 实验（Strategy Experiment）：把「跑一次」变成一条留在服务端的记录
// --------------------------------------------------------------------------- //
//
// 一次实验 = 用某一版策略按一种跑法跑一次，并把结果（每个网格点的参数与指标）存下来。
// 这一节刻意不做的事：
// ① 不自己算任何指标：表里的分数全部来自服务端的 `results` / `summary`，页面只翻译单位；
// ② 不推荐参数：敏感性按目标指标排序，只说明「哪一组分数高」，排序不是推荐（引擎的立场）；
// ③ 201 不等于成功：引擎跑挂了也回 201 + `status: "failed"`；页面照样把这条记录摆出来，
//    并把 `error_message` 放在最显眼的地方——记录本身就是那次尝试的交付物；
// ④ 删除实验只删这条记录：它背后那次 `BacktestRun` 是独立产物，不会被一起删掉。

/** 服务端 `ExperimentCreate.kind` 的五个取值，以及每一种要求你给什么。 */
const EXPERIMENT_KINDS: Array<{ value: ExperimentKind; label: string; hint: string }> = [
  {
    value: 'backtest',
    label: '跑一次回测',
    hint: '用这一版策略跑一次完整回测。这一次运行本身就是实验记录。',
  },
  {
    value: 'sensitivity',
    label: '参数敏感性（扫一遍）',
    hint: '给每个参数几个候选值，所有组合各跑一遍，看哪一组分数最高。',
  },
  {
    value: 'monte_carlo',
    label: '成交重采样（蒙特卡洛）',
    hint: '挑一次已经跑完的回测，把它的成交记录重新洗牌再算一遍，看结果稳不稳。不会重跑回测。',
  },
  {
    value: 'walk_forward',
    label: '滚动前进',
    hint: '用一段数据算、紧接着的一段检验，然后整段往前挪：看它在不同时间段是不是都成立。',
  },
  {
    value: 'oos',
    label: '样本外检验',
    hint: '把数据按时间切成前后两段，只看后一段（样本外）的结果。',
  },
]

/** 敏感性扫描可用的目标指标：服务端 `TRACKED_METRICS`，顺序照抄引擎的报告顺序。 */
const EXPERIMENT_METRICS: readonly string[] = [
  'total_return',
  'cagr',
  'sharpe',
  'sortino',
  'max_drawdown',
  'win_rate',
  'profit_factor',
  'expectancy',
  'number_of_trades',
  'exposure',
]

/** 服务端 `MAX_GRID_POINTS`：超过这个点数会被 422 拒绝，先在页面上说清楚。 */
const EXPERIMENT_MAX_GRID_POINTS = 144

const EXPERIMENT_TIMEFRAMES = ['1d', '1h', '4h', '1w']

const EXPERIMENT_STATUS_LABELS: Record<string, string> = {
  queued: '已排队',
  running: '运行中',
  completed: '已完成',
  failed: '没有跑完',
}

const route = useRoute()

const experimentList = ref<ExperimentSummaryOut[]>([])
const experimentVersions = ref<StrategyVersion[]>([])
const experimentAssets = ref<Asset[]>([])
const experimentBacktests = ref<BacktestSummary[]>([])
const loadingExperiments = ref(false)
const loadingExperiment = ref(false)
const loadingExperimentVersions = ref(false)
const loadingExperimentBacktests = ref(false)
const creatingExperiment = ref(false)
const comparingExperiments = ref(false)
const deletingExperimentId = ref<number | null>(null)
const experimentError = ref('')
const experimentNotice = ref('')
const experimentDetailError = ref('')

const experimentVersionId = ref('')
const experimentKind = ref<ExperimentKind>('backtest')
const experimentName = ref('')
const experimentSymbol = ref('')
const experimentTimeframe = ref('1d')
const experimentMetric = ref('sharpe')
const experimentNotes = ref('')
const experimentStart = ref('')
const experimentEnd = ref('')
const experimentSeriesId = ref('')
const experimentParametersText = ref('')
const experimentGridText = ref('')
const experimentRuns = ref('1000')
const experimentSeed = ref('0')
const experimentTradesPerRun = ref('')
const experimentTrainBars = ref('250')
const experimentTestBars = ref('60')
const experimentStep = ref('')
const experimentOosPct = ref('0.2')
const experimentBacktestRunId = ref('')

const openExperiment = ref<ExperimentDetailOut | null>(null)
const openExperimentId = ref<number | null>(null)
const compareExperimentIds = ref<number[]>([])
const experimentCompare = ref<ExperimentCompareOut | null>(null)

/** 地址栏里的 `?experiment=<id>`：刷新之后从这里回到当时打开的那一条。 */
const requestedExperimentId = (() => {
  const raw = route.query.experiment
  const text = Array.isArray(raw) ? raw[0] : raw
  const parsed = text ? Number.parseInt(text, 10) : Number.NaN
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
})()

/** 地址栏里的 `?run=<id>`：刷新或换页面回来之后，重新落下当时那一次研究。 */
const requestedRunId = (() => {
  const raw = route.query.run
  const text = Array.isArray(raw) ? raw[0] : raw
  const parsed = text ? Number.parseInt(text, 10) : Number.NaN
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
})()

function experimentKindLabel(kind: string): string {
  return EXPERIMENT_KINDS.find((item) => item.value === kind)?.label ?? '未知跑法'
}

function experimentKindHint(kind: string): string {
  return EXPERIMENT_KINDS.find((item) => item.value === kind)?.hint ?? ''
}

function experimentStatusLabel(status: string): string {
  return EXPERIMENT_STATUS_LABELS[status] ?? '状态未知'
}

function strategyName(strategyId: number): string {
  return strategies.value.find((item) => item.id === strategyId)?.name ?? `策略 #${strategyId}`
}

function experimentVersionLabel(version: StrategyVersion): string {
  const current = version.is_current ? '（当前版本）' : ''
  return `${strategyName(version.strategy_id)} · ${version.version}${current} · ${validationLabel(version.validation_status)}`
}

const experimentNeedsSeries = computed(() => experimentKind.value !== 'monte_carlo')

const selectedExperimentVersion = computed<StrategyVersion | null>(
  () => experimentVersions.value.find((item) => String(item.id) === experimentVersionId.value) ?? null,
)

const experimentDefaultName = computed(
  () =>
    `${experimentKindLabel(experimentKind.value)} · ${selectedExperimentVersion.value?.version ?? '未选版本'}`,
)

/** JSON 输入在本地先解析：写错了就在这里说，不拿半个请求去问服务端。 */
function parseJsonObject(
  text: string,
  what: string,
  requireArrays = false,
): { value: Record<string, unknown> | null; problem: string } {
  const trimmed = text.trim()
  if (!trimmed) return { value: null, problem: '' }
  let parsed: unknown
  try {
    parsed = JSON.parse(trimmed)
  } catch {
    return { value: null, problem: `${what}不是合法的 JSON：按示例写成一个对象，例如 {"risk_pct": 2}。` }
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return { value: null, problem: `${what}要写成用 { } 包起来的对象，不能是数组或单个值。` }
  }
  if (requireArrays) {
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (!Array.isArray(value) || value.length === 0) {
        return {
          value: null,
          problem: `${what}里的 ${key} 要是一个非空数组，例如 {"risk_pct": [1, 2, 3]}。`,
        }
      }
    }
  }
  return { value: parsed as Record<string, unknown>, problem: '' }
}

function integerProblem(text: string, what: string, min: number, max: number): string {
  if (!text.trim()) return ''
  const value = Number(text)
  if (!Number.isInteger(value) || value < min || value > max) {
    return `${what}要填 ${min} 到 ${max} 之间的整数。`
  }
  return ''
}

const experimentGridPoints = computed<number | null>(() => {
  if (experimentKind.value !== 'sensitivity') return null
  const { value, problem } = parseJsonObject(experimentGridText.value, '网格', true)
  if (problem || !value) return null
  return Object.values(value).reduce<number>(
    (total, values) => total * (Array.isArray(values) ? values.length : 1),
    1,
  )
})

const experimentBlockedReason = computed<string>(() => {
  if (!experimentVersionId.value) {
    return '还差一个策略版本：在上面选一版；一版都没有的话，先去「策略」页编译出一版。'
  }
  if (
    experimentNeedsSeries.value &&
    !experimentSymbol.value.trim() &&
    !experimentSeriesId.value.trim()
  ) {
    return '还差一个标的代码：填一个代码（例如 SPY），或在「高级设置」里填数据序列号。'
  }
  const parameters = parseJsonObject(experimentParametersText.value, '策略参数')
  if (parameters.problem) return parameters.problem
  if (experimentKind.value === 'sensitivity') {
    if (!experimentGridText.value.trim()) {
      return '还差一个网格：在「高级设置」里写要扫的参数，例如 {"risk_pct": [1, 2, 3]}。'
    }
    const grid = parseJsonObject(experimentGridText.value, '网格', true)
    if (grid.problem) return grid.problem
    const points = experimentGridPoints.value
    if (points !== null && points > EXPERIMENT_MAX_GRID_POINTS) {
      return `网格太大了：${points} 个点超过服务端上限 ${EXPERIMENT_MAX_GRID_POINTS} 个，而每个点都要跑一次回测——请缩小范围。`
    }
  }
  if (experimentKind.value === 'monte_carlo') {
    if (!experimentBacktestRunId.value) {
      return '还差一次已经跑完的回测：在上面选一条记录；一条都没有的话，先去「回测」页跑一次。'
    }
    const runs = integerProblem(experimentRuns.value, '重采样次数', 1, 5000)
    if (runs) return runs
    const trades = integerProblem(experimentTradesPerRun.value, '每次取的成交笔数', 1, 100000)
    if (trades) return trades
    const seed = integerProblem(experimentSeed.value, '随机种子', 0, 2147483647)
    if (seed) return seed
  }
  if (experimentKind.value === 'walk_forward') {
    const train = integerProblem(experimentTrainBars.value, '训练窗口（根）', 60, 100000)
    if (train) return train
    const test = integerProblem(experimentTestBars.value, '检验窗口（根）', 20, 100000)
    if (test) return test
    const step = integerProblem(experimentStep.value, '步长（根）', 1, 100000)
    if (step) return step
  }
  if (experimentKind.value === 'oos') {
    const text = experimentOosPct.value.trim()
    if (text) {
      const pct = Number(text)
      if (!Number.isFinite(pct) || pct <= 0 || pct >= 1) {
        return '样本外比例要大于 0 且小于 1：0.2 表示用最后 20% 的数据做样本外检验。'
      }
    }
  }
  return ''
})

function buildExperimentPayload(): ExperimentCreatePayload | null {
  const version = selectedExperimentVersion.value
  if (!version) return null
  const payload: ExperimentCreatePayload = {
    name: (experimentName.value.trim() || experimentDefaultName.value).slice(0, 120),
    kind: experimentKind.value,
    strategy_version_id: version.id,
  }
  const notes = experimentNotes.value.trim()
  if (notes) payload.notes = notes
  if (experimentNeedsSeries.value) {
    const symbol = experimentSymbol.value.trim()
    if (symbol) payload.symbol = symbol.toUpperCase()
    const seriesId = experimentSeriesId.value.trim()
    if (seriesId) payload.series_id = Number(seriesId)
    if (experimentTimeframe.value) payload.timeframe = experimentTimeframe.value
    const start = experimentStart.value.trim()
    if (start) payload.start = start
    const end = experimentEnd.value.trim()
    if (end) payload.end = end
  }
  // 重采样读的是一次已经跑完的回测，策略参数在那一次就已经定下来了。
  if (experimentKind.value !== 'monte_carlo') {
    const parsed = parseJsonObject(experimentParametersText.value, '策略参数').value
    if (parsed) payload.parameters = parsed
  }
  if (experimentKind.value === 'sensitivity') {
    const grid = parseJsonObject(experimentGridText.value, '网格', true).value
    if (grid) payload.grid = grid as Record<string, unknown[]>
    payload.metric = experimentMetric.value
  }
  if (experimentKind.value === 'monte_carlo') {
    payload.backtest_run_id = Number(experimentBacktestRunId.value)
    const runs = Number(experimentRuns.value)
    if (Number.isInteger(runs)) payload.runs = runs
    const trades = experimentTradesPerRun.value.trim()
    if (trades && Number.isInteger(Number(trades))) payload.trades_per_run = Number(trades)
    const seed = Number(experimentSeed.value)
    if (Number.isInteger(seed)) payload.seed = seed
  }
  if (experimentKind.value === 'walk_forward') {
    const train = Number(experimentTrainBars.value)
    if (Number.isInteger(train)) payload.train_bars = train
    const test = Number(experimentTestBars.value)
    if (Number.isInteger(test)) payload.test_bars = test
    const step = experimentStep.value.trim()
    if (step && Number.isInteger(Number(step))) payload.step = Number(step)
  }
  if (experimentKind.value === 'oos') {
    const pct = experimentOosPct.value.trim()
    if (pct) payload.oos_pct = Number(pct)
  }
  return payload
}

function handleExperimentFailure(e: unknown, what = '实验没有建成') {
  if (e instanceof ApiError && e.status === 422) {
    experimentError.value = `${what}：服务端拒绝了这次输入——${e.message}。这次请求没有写入任何记录，改完上面的字段再试一次。`
    return
  }
  experimentError.value = `${what}：${(e as Error).message}`
}

async function loadExperiments() {
  loadingExperiments.value = true
  try {
    const response = await api.experiments()
    experimentList.value = response.experiments
  } catch (e) {
    experimentError.value = `读不到实验列表：${(e as Error).message}`
  } finally {
    loadingExperiments.value = false
  }
}

async function loadExperimentVersions() {
  loadingExperimentVersions.value = true
  try {
    const versions = await api.allStrategyVersions()
    experimentVersions.value = [...versions].sort((a, b) => b.id - a.id)
    if (!experimentVersionId.value && experimentVersions.value.length) {
      const current = experimentVersions.value.find((item) => item.is_current)
      experimentVersionId.value = String((current ?? experimentVersions.value[0]).id)
    }
  } catch (e) {
    experimentError.value = `读不到策略版本列表：${(e as Error).message}`
  } finally {
    loadingExperimentVersions.value = false
  }
}

async function loadExperimentAssets() {
  try {
    experimentAssets.value = await api.assets()
  } catch {
    // 标的列表只是输入建议：读不到就让用户自己敲代码，不该挡住建实验。
    experimentAssets.value = []
  }
}

async function loadExperimentBacktests() {
  loadingExperimentBacktests.value = true
  try {
    const runs = await api.backtests()
    experimentBacktests.value = runs.filter((run) => run.status === 'completed').slice(0, 30)
  } catch (e) {
    experimentError.value = `读不到回测记录：${(e as Error).message}`
  } finally {
    loadingExperimentBacktests.value = false
  }
}

async function reloadExperimentChoices() {
  experimentError.value = ''
  await Promise.all([
    loadExperimentVersions(),
    loadExperimentBacktests(),
    loadExperimentAssets(),
    loadExperiments(),
  ])
}

/**
 * 只改写地址栏里的一个参数，其它原样保留。
 *
 * 列表和结果本身都来自服务端，所以去掉参数只是回到「没打开任何一条」的状态，不是数据丢失。
 */
function queryWith(key: 'experiment' | 'run', id: number | null): Record<string, string> {
  const query: Record<string, string> = {}
  for (const [name, value] of Object.entries(route.query)) {
    if (name === key) continue
    if (typeof value === 'string') query[name] = value
  }
  if (id !== null) query[key] = String(id)
  return query
}

/**
 * 把「打开的是哪一条」写进地址栏。
 *
 * 有了它，刷新页面（甚至把链接发给别人）还能落在同一条实验上。
 */
function rememberOpenExperiment(id: number | null) {
  void router.replace({ path: route.path, query: queryWith('experiment', id) })
}

/**
 * 把「打开的是哪一次研究」写进地址栏。
 *
 * 轮询每 2 秒会重新读一次运行，所以这里先用地址栏现值做一次幂等判断，避免无意义的 replace。
 */
function rememberOpenRun(id: number | null) {
  const raw = route.query.run
  const current = Number.parseInt(typeof raw === 'string' ? raw : '', 10)
  if (Number.isFinite(current) ? current === id : id === null) return
  void router.replace({ path: route.path, query: queryWith('run', id) })
}

function applyExperiment(detail: ExperimentDetailOut) {
  openExperiment.value = detail
  openExperimentId.value = detail.id
  rememberOpenExperiment(detail.id)
}

async function openExperimentById(id: number) {
  loadingExperiment.value = true
  experimentDetailError.value = ''
  try {
    applyExperiment(await api.experiment(id))
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      openExperiment.value = null
      openExperimentId.value = null
      experimentDetailError.value = '这条实验后端已经找不到了（可能刚被删掉）：刷新列表后重新选一条。'
      rememberOpenExperiment(null)
      await loadExperiments()
    } else {
      experimentDetailError.value = `读不到这条实验：${(e as Error).message}`
    }
  } finally {
    loadingExperiment.value = false
  }
}

function refreshOpenExperiment() {
  const id = openExperimentId.value
  if (id !== null) void openExperimentById(id)
}

function closeExperiment() {
  openExperiment.value = null
  openExperimentId.value = null
  experimentDetailError.value = ''
  rememberOpenExperiment(null)
}

async function createExperiment() {
  if (experimentBlockedReason.value || creatingExperiment.value) return
  const payload = buildExperimentPayload()
  if (!payload) return
  experimentError.value = ''
  experimentNotice.value = ''
  experimentDetailError.value = ''
  creatingExperiment.value = true
  try {
    const detail = await api.createExperiment(payload)
    applyExperiment(detail)
    await loadExperiments()
    if (detail.status === 'failed') {
      // 201 只是「记录建成了」：引擎的失败也要留在这条记录上，并被看见。
      experimentError.value = `实验「${detail.name}」记下来了，但引擎这次没有跑完：${
        detail.error_message ?? '服务端没有给出原因。'
      }`
    } else {
      experimentNotice.value = `实验「${detail.name}」跑完了：存下 ${detail.result_count} 条结果。`
    }
  } catch (e) {
    handleExperimentFailure(e)
  } finally {
    creatingExperiment.value = false
  }
}

async function removeExperiment(id: number, name: string) {
  const ok = window.confirm(
    `确定删除实验「${name}」吗？\n\n` +
      '只会删掉这条实验记录和它存下来的结果。它背后那次回测运行不会被删除——在「回测」页里仍然找得到。\n' +
      '删掉之后不能恢复。',
  )
  if (!ok) return
  deletingExperimentId.value = id
  experimentError.value = ''
  experimentNotice.value = ''
  try {
    await api.deleteExperiment(id)
    if (openExperimentId.value === id) {
      openExperiment.value = null
      openExperimentId.value = null
      rememberOpenExperiment(null)
    }
    compareExperimentIds.value = compareExperimentIds.value.filter((item) => item !== id)
    experimentCompare.value = null
    experimentNotice.value = `实验「${name}」已经删除。它背后那次回测运行没有被删掉。`
    await loadExperiments()
  } catch (e) {
    handleExperimentFailure(e, '删除没有成功')
  } finally {
    deletingExperimentId.value = null
  }
}

function toggleExperimentCompare(id: number) {
  compareExperimentIds.value = compareExperimentIds.value.includes(id)
    ? compareExperimentIds.value.filter((item) => item !== id)
    : [...compareExperimentIds.value, id]
  // 选中的集合变了，上一次的对比结果就不再对应它，直接作废而不是让它看起来还是新的。
  experimentCompare.value = null
}

function clearExperimentCompare() {
  compareExperimentIds.value = []
  experimentCompare.value = null
}

async function runExperimentCompare() {
  if (compareExperimentIds.value.length < 2 || comparingExperiments.value) return
  comparingExperiments.value = true
  experimentDetailError.value = ''
  try {
    experimentCompare.value = await api.compareExperiments(compareExperimentIds.value)
  } catch (e) {
    handleExperimentFailure(e, '对比没有成功')
  } finally {
    comparingExperiments.value = false
  }
}

// ---- 打开的那一条实验：把服务端存下的结果翻成人话 -------------------------- //

const experimentSummary = computed<Record<string, unknown> | null>(
  () => openExperiment.value?.summary ?? null,
)

function summaryObject(key: string): Record<string, unknown> | null {
  const value = experimentSummary.value?.[key]
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function summaryNumber(key: string): number | null {
  const value = experimentSummary.value?.[key]
  return typeof value === 'number' ? value : null
}

function summaryText(key: string): string | null {
  const value = experimentSummary.value?.[key]
  return typeof value === 'string' ? value : null
}

const experimentSummaryMetrics = computed<Array<{ key: string; value: number | null }>>(() =>
  Object.entries(openExperiment.value?.metrics ?? {}).map(([key, value]) => ({
    key,
    value: typeof value === 'number' ? value : null,
  })),
)

/** 敏感性点：`payload.objective` 是引擎用来排名的分数；没测到的点是 `null`。 */
function resultObjective(row: ExperimentResultOut): number | null {
  const value = row.payload?.objective
  return typeof value === 'number' ? value : null
}

function resultWarmupUnmet(row: ExperimentResultOut): boolean {
  return row.payload?.warmup_unmet === true
}

function resultMetric(row: ExperimentResultOut, key: string): number | null {
  const value = (row.metrics ?? {})[key]
  return typeof value === 'number' ? value : null
}

function resultHash(row: ExperimentResultOut): string | null {
  const value = row.payload?.result_hash
  return typeof value === 'string' ? value : null
}

function isBestResult(row: ExperimentResultOut): boolean {
  const hash = resultHash(row)
  return hash !== null && summaryObject('best')?.result_hash === hash
}

function isWorstResult(row: ExperimentResultOut): boolean {
  const hash = resultHash(row)
  return hash !== null && summaryObject('worst')?.result_hash === hash
}

/**
 * 逐条结果：敏感性按目标指标从高到低重排（服务端存的是网格顺序）。
 *
 * 只重排，不重算：分数还是引擎给的那一个。没测到的点（预热不足 / 指标未定义）排最后。
 */
const experimentResults = computed<ExperimentResultOut[]>(() => {
  const rows = openExperiment.value?.results ?? []
  if (openExperiment.value?.kind !== 'sensitivity') return rows
  return [...rows].sort((a, b) => {
    const left = resultObjective(a)
    const right = resultObjective(b)
    if (left === null && right === null) return a.id - b.id
    if (left === null) return 1
    if (right === null) return -1
    return right - left || a.id - b.id
  })
})

/** 参数列：这一批结果里出现过的参数名。 */
const experimentResultAxes = computed<string[]>(() => {
  const keys: string[] = []
  for (const row of experimentResults.value) {
    for (const key of Object.keys(row.parameters ?? {})) {
      if (!keys.includes(key)) keys.push(key)
    }
  }
  return keys
})

/** 指标列：先按引擎的报告顺序，再补上表里多出来的键。 */
const experimentResultMetrics = computed<string[]>(() => {
  const present = new Set<string>()
  for (const row of experimentResults.value) {
    for (const key of Object.keys(row.metrics ?? {})) present.add(key)
  }
  const ordered = EXPERIMENT_METRICS.filter((key) => present.has(key))
  const extra = [...present].filter((key) => !EXPERIMENT_METRICS.includes(key)).sort()
  return [...ordered, ...extra]
})

const sensitivityWarmupUnmet = computed(
  () =>
    openExperiment.value?.kind === 'sensitivity' &&
    experimentResults.value.some((row) => resultWarmupUnmet(row)),
)

function axisValue(row: ExperimentResultOut, axis: string): string {
  const value = (row.parameters ?? {})[axis]
  if (value === undefined || value === null) return '—'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

/** `summary.best` / `summary.worst` 是 `{parameters, objective, result_hash}`。 */
function compactPointText(point: Record<string, unknown> | null): string {
  if (!point) return '—'
  const parameters = (point.parameters ?? {}) as Record<string, unknown>
  const values = Object.entries(parameters)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(', ')
  const metric = summaryText('metric') ?? 'total_return'
  const objective = typeof point.objective === 'number' ? point.objective : null
  return `${values || '（没有参数）'} → ${metricKeyLabel(metric)} ${formatMetric(metric, objective)}`
}

function compareNumber(row: Record<string, unknown>, key: string): number | null {
  const value = row[key]
  return typeof value === 'number' ? value : null
}

function compareText(row: Record<string, unknown>, key: string): string | null {
  const value = row[key]
  return typeof value === 'string' ? value : null
}

const openExperimentText = computed(() =>
  openExperiment.value ? JSON.stringify(openExperiment.value, null, 2) : '',
)

const experimentNextStep = computed<{ text: string; to: NextStepTarget; linkText: string }>(() => {
  const versionId =
    openExperiment.value?.strategy_version_id ??
    (experimentVersionId.value ? Number(experimentVersionId.value) : null)
  if (versionId === null) {
    return {
      text: '上面选一版策略跑一次实验；跑完之后，这一版随时可以拿去「回测」页看权益曲线和成交明细。',
      to: '',
      linkText: '',
    }
  }
  return {
    text: '实验只把结果存下来做对照；要看权益曲线、逐笔成交和执行模型，用「回测」页打开这一版。',
    to: { path: '/backtest', query: { strategy_version_id: String(versionId) } },
    linkText: '去「回测」',
  }
})

// --------------------------------------------------------------------------- //
// 下一步：永远给一条能走的路，不留下「点了一下什么也没发生」
// --------------------------------------------------------------------------- //
type NextStepTarget = string | { path: string; query: Record<string, string> }

const nextStep = computed<{ text: string; to: NextStepTarget; linkText: string }>(() => {
  const current = run.value
  if (notConfigured.value) {
    return { text: '到「系统管理」把 AI 提供方配好，再回到这一页重试。', to: '/settings', linkText: '去「系统管理」' }
  }
  if (!current) {
    return {
      text: '写好研究问题和材料，点「开始研究」；结果会留在这一页，也会进「最近的研究」。',
      to: '',
      linkText: '',
    }
  }
  if (isLiveStatus(current.status)) {
    return {
      text: '这次研究在后台跑着，状态每 2 秒自动更新一次。这一页可以关掉，稍后在「最近的研究」里重新打开它。',
      to: '',
      linkText: '',
    }
  }
  if (current.status === 'failed') {
    return {
      text: '先看上面的原因：如果是 AI 没配置或额度用完，去「系统管理」处理；否则改一下研究输入再试一次。',
      to: '/settings',
      linkText: '检查 AI 配置',
    }
  }
  if (current.status === 'rejected') {
    return {
      text: '系统不会自动修改 AI 的回答。把问题问得更具体、材料补得更完整，然后重新开始一次研究。',
      to: '',
      linkText: '',
    }
  }
  const version = compiledVersion.value
  if (version) {
    if (version.is_current) {
      return {
        text: `策略版本 ${version.version} 已经是当前版本，可以用它去回测了。`,
        to: { path: '/backtest', query: { strategy_version_id: String(version.id) } },
        linkText: '去「回测」用这一版',
      }
    }
    return {
      text: `草案已经编译成策略版本 ${version.version}，但还没有激活。在下面「策略版本」里点「激活为当前版本」；也可以先去「回测」用它试跑一次（没激活也能回测）。`,
      to: { path: '/backtest', query: { strategy_version_id: String(version.id) } },
      linkText: '先去「回测」试跑这一版',
    }
  }
  if (draftCompiledVersionId.value) {
    return { text: '这次草案编译过，但版本信息还没读回来：点「刷新状态」重试一次。', to: '', linkText: '' }
  }
  if (current.draft) {
    if (!confirmation.value) {
      return {
        text: '读一遍上面的草案，然后在「人工确认」里给出你的结论——只有「已确认」的草案才允许编译成策略版本。',
        to: '',
        linkText: '',
      }
    }
    if (confirmation.value.decision !== 'confirmed') {
      return {
        text: '这次的人工结论不是「已确认」，所以服务端不允许编译。改主意的话，重新选「确认」即可。',
        to: '',
        linkText: '',
      }
    }
    return {
      text: '草案已经人工确认。在下面选一个目标策略（或新建一个），然后点「编译为策略版本」。',
      to: '',
      linkText: '',
    }
  }
  return { text: '这次只拿到了假设、还没有草案：点「重新生成策略草案」让 AI 再写一次。', to: '', linkText: '' }
})

onMounted(() => {
  void loadRuns()
  void loadStrategies()
  void loadBacktestTargets()
  void loadExperiments()
  void loadExperimentVersions()
  void loadExperimentBacktests()
  void loadExperimentAssets()
  // 地址栏里带着 ?experiment=<id>（刷新页面或别人发来的链接）时，把当时那一条读回来。
  if (requestedExperimentId !== null) void openExperimentById(requestedExperimentId)
  // 同理：?run=<id> 时把当时那一次研究读回来，刷新后不用再去列表里找。
  if (requestedRunId !== null) void openRun(requestedRunId)
})

onUnmounted(stopPolling)
</script>

<template>
  <div>
    <h1 class="page-title">AI 研究实验室</h1>
    <p class="page-sub">
      把一个研究问题交给 AI：它先说出自己理解到了什么，再给出一份策略草案；你确认之后，
      系统把它编译成一条正式的策略版本，你决定要不要激活，然后拿去回测。
      每一步都由你拍板——这一页不执行交易、不下单。
    </p>

    <p v-if="error" class="error">{{ error }}</p>
    <div v-if="notConfigured" class="notice warn">
      ⚠️ 这一步做不了：现在还没有可用的 AI 提供方。
      <RouterLink to="/settings">去「系统管理」把 AI 提供方配好</RouterLink>，再回到这一页重试。
      <br />后端原文：{{ configDetail }}
    </div>
    <p v-else-if="notice" class="notice">{{ notice }}</p>

    <!-- 这一页回答产品的四个问题（ADR-127）：你在哪、能做什么、结果怎么看、下一步做什么。 -->
    <div class="card card-quiet" style="margin-top: 14px">
      <h3>这一页在做什么</h3>
      <ul class="answer-list">
        <li>
          <b>你在哪：</b>「AI 研究实验室」。它和「研究策略」不同：这里不碰行情数据、不动回测引擎，
          先把文字材料交给 AI 读，再把它读出来的东西编译成一条正式策略。
        </li>
        <li>
          <b>你能做什么：</b>走完一条完整链路——写问题、贴材料 → 看 AI 的理解和假设 → 读草案 →
          人工确认 → 编译成策略版本 → 激活 → 去回测。
        </li>
        <li>
          <b>结果怎么看：</b>先看「AI 理解」对不对得上你的意思，再看规则（尤其是 AI 自己补的假设），
          然后看能力结论：这份草案系统能不能实现。
        </li>
        <li>
          <b>下一步：</b>页面底部永远写着下一步该做什么；进行中的研究会自动刷新状态，不用一直守着。
        </li>
      </ul>
    </div>

    <!-- ① 研究输入 -->
    <div class="card" style="margin-top: 14px">
      <h3>① 研究输入</h3>
      <p class="muted">
        一个问句 + 一段材料就够了。材料可以是一段研报摘录、一条公告、或者你自己写下的策略想法；
        也可以给一个公开网页的地址，让服务端去读那篇正文。
        后端还支持 PDF 与 GitHub 文件这两种材料，这一步暂时只有「贴文字」和「给网址」两个入口：
        那两种可以先在别处读出来、再贴成文字（ADR-191）。
      </p>

      <label class="muted" for="lab-question">研究问题</label>
      <textarea
        id="lab-question"
        v-model="question"
        rows="3"
        style="min-height: 76px; font-family: inherit; font-size: 13px"
        placeholder="例如：这份材料里的均线突破思路，能不能变成一条我能在日线上验证的规则？"
      ></textarea>
      <p class="muted">{{ question.trim().length }} / {{ QUESTION_MAX }} 字</p>

      <!-- 材料来源：一次只用一种。网址那条会先让服务端去读一次，读不到就不开始研究。 -->
      <div class="mode-switch" role="group" aria-label="材料来源">
        <button
          type="button"
          class="ghost"
          :class="{ on: materialKind === 'text' }"
          :aria-pressed="materialKind === 'text'"
          @click="materialKind = 'text'"
        >
          贴一段文字
        </button>
        <button
          type="button"
          class="ghost"
          :class="{ on: materialKind === 'url' }"
          :aria-pressed="materialKind === 'url'"
          @click="materialKind = 'url'"
        >
          给一个网址
        </button>
      </div>

      <label class="muted" for="lab-source-label">材料名称（可选）</label>
      <input
        id="lab-source-label"
        v-model="sourceLabel"
        type="text"
        placeholder="例如：某券商 2024 年均线策略摘要"
      />

      <template v-if="materialKind === 'text'">
        <label class="muted" for="lab-source-text" style="margin-top: 10px">材料正文</label>
        <textarea
          id="lab-source-text"
          v-model="sourceText"
          placeholder="把材料原文粘贴到这里。"
        ></textarea>
        <p class="muted">
          {{ sourceText.length }} / {{ SOURCE_MAX }} 字 · 后端最多接受 {{ MAX_SOURCES }} 段材料，这一页先支持一段。
        </p>
      </template>

      <template v-else>
        <label class="muted" for="lab-source-url" style="margin-top: 10px">材料网址</label>
        <input
          id="lab-source-url"
          v-model="sourceUri"
          type="url"
          inputmode="url"
          placeholder="https://example.com/some-article"
        />
        <p class="muted">
          点「开始研究」时服务端会先去看一眼这个地址：只允许公开网站，本机与局域网地址会被拒绝；
          读回来的正文按行留下摘要，第三方材料默认只把前 500 字交给 AI，原文不落库。
        </p>
        <label class="row" style="gap: 8px; align-items: center; margin-top: 6px">
          <input v-model="sourceFullRetention" type="checkbox" style="width: auto" />
          <span class="muted">
            这份材料由我本人拥有，或我已获得保留全文的授权（不勾选只保留摘要 500 字，
            勾选后保留前 2 万字）
          </span>
        </label>
      </template>

      <p v-if="ingestNote" class="notice" style="margin-top: 10px">✅ {{ ingestNote }}</p>
      <p v-if="ingestError" class="notice warn" style="margin-top: 10px">
        ⚠️ {{ ingestError }} 材料没有读回来，所以这次研究没有开始——换一个来源再试。
      </p>

      <p v-if="inputProblem" class="notice warn">⚠️ {{ inputProblem }}</p>

      <button :disabled="!canSubmit" style="margin-top: 10px" @click="submit">
        {{ ingesting ? '正在读材料…' : busy ? '正在提交…' : '开始研究' }}
      </button>
      <p v-if="busy" class="muted" style="margin-top: 8px">
        正在把这次研究排进后台队列。
      </p>
      <p v-else-if="run && isLiveStatus(run.status)" class="muted" style="margin-top: 8px">
        现在还有一次研究在跑。再点「开始研究」会开始新的一次，这一页会切到新的那次——
        旧的仍然留在下面的「最近的研究」里，随时能打开。
      </p>
      <p v-else-if="blockedReason" class="muted" style="margin-top: 8px">{{ blockedReason }}</p>
    </div>

    <!-- 后台研究的状态：POST 返回 202 之后，真正的结果要靠轮询读回来 -->
    <div v-if="run && isLiveStatus(run.status)" class="card card-quiet" style="margin-top: 14px">
      <div class="row" style="justify-content: space-between; align-items: flex-start">
        <h3 style="margin: 0">研究进行中</h3>
        <button class="ghost" :disabled="loadingRun" @click="refreshRun">
          {{ loadingRun ? '读取中…' : '刷新状态' }}
        </button>
      </div>
      <p class="conclusion-sentence">{{ liveSentence }}</p>
      <p class="wait">
        已经等了 {{ elapsedSeconds }} 秒 · 研究号 #{{ run.run_id }} · 当前步骤
        {{ run.current_step || '—' }}
      </p>
      <p v-if="elapsedSeconds >= POLL_HINT_SECONDS" class="muted">
        超过 {{ POLL_HINT_SECONDS }} 秒还没结束是正常的（AI 要读完材料再写两份内容）。
        后台会继续跑，这一页可以关掉，稍后在「最近的研究」里重新打开就能看到结果。
      </p>
      <div class="row" style="margin-top: 8px">
        <span v-if="polling" class="muted">正在每 2 秒自动刷新一次。</span>
        <span v-else class="muted">自动刷新已停止；点「刷新状态」手动读一次。</span>
        <button v-if="polling" class="ghost" @click="stopPolling">停止自动刷新</button>
      </div>
      <p v-if="pollFailures" class="muted">
        最近有 {{ pollFailures }} 次没读到状态；连续 {{ POLL_FAILURE_LIMIT }} 次失败会自动停下来。
      </p>
    </div>

    <!-- ② AI 理解 -->
    <div v-if="run" class="card" style="margin-top: 14px">
      <div class="row" style="justify-content: space-between; align-items: flex-start">
        <h3 style="margin: 0">② AI 理解到了什么</h3>
        <button class="ghost" :disabled="loadingRun" @click="refreshRun">
          {{ loadingRun ? '读取中…' : '重新读一次结果' }}
        </button>
      </div>

      <p class="muted" style="margin-top: 8px">
        这次研究的问题：{{ run.question }} · 状态：{{ statusLabel(run.status) }}
        <template v-if="isAdvanced">
          （{{ run.status }} · 运行号 #{{ run.run_id }} · 当前步骤 {{ run.current_step }} · 尝试
          {{ run.attempts ?? 0 }} 次）
        </template>
      </p>
      <p v-if="statusSentence" class="conclusion-sentence">{{ statusSentence }}</p>

      <!-- 被拒绝：说清每一条原因，并明确系统没有替 AI 改。 -->
      <div v-if="run.status === 'rejected'" style="margin-top: 10px">
        <p class="notice warn">
          ⚠️ AI 的回答没有通过校验。系统没有自动修正它，也没有把这份回答当作研究结论。
          下面每一条都是被拒绝的原因：
        </p>
        <ul class="answer-list">
          <li v-for="(item, index) in violations" :key="index">
            {{ violationReason(item) }}
            <template v-if="isAdvanced">
              <span class="badge" style="margin-left: 6px">{{ item.code }}</span>
              <span class="muted">
                {{ item.message }}
                <template v-if="item.field">（字段 {{ item.field }}）</template>
                · 严重程度 {{ item.severity ?? 'blocker' }}
              </span>
            </template>
          </li>
        </ul>
        <p v-if="!violations.length" class="muted">
          后端把这次运行标成了「没有通过校验」，但没有返回具体原因。
        </p>
      </div>

      <!-- 没跑完：照实显示后端给的原因。 -->
      <p v-if="run.status === 'failed'" class="error" style="margin-top: 10px">
        {{ run.error_message || '后端没有给出具体原因。' }}
      </p>
      <p
        v-else-if="run.error_message && run.status !== 'rejected'"
        class="muted"
        style="margin-top: 10px"
      >
        后端附注：{{ run.error_message }}
      </p>

      <!-- 警告不是拒绝：材料被截断、或模型写了没算过的数字。 -->
      <div v-if="warnings.length" style="margin-top: 10px">
        <h4>需要你注意</h4>
        <ul class="answer-list">
          <li v-for="(item, index) in warnings" :key="index">
            {{ warningText(item) }}
            <template v-if="isAdvanced">
              <span class="muted">（{{ item.kind ?? 'warning' }}）</span>
            </template>
          </li>
        </ul>
      </div>

      <template v-if="hypothesisContent">
        <p class="conclusion-sentence">{{ hypothesisContent.understanding }}</p>
        <p class="muted">
          策略名字（AI 起的）：{{ hypothesisContent.strategy_name }}
          <template v-if="hypothesisContent.objective"> · 目标：{{ hypothesisContent.objective }}</template>
        </p>
        <p v-if="hypothesisContent.market?.length || hypothesisContent.timeframe" class="muted">
          它读到的市场：{{ hypothesisContent.market?.join('、') || '—' }}
          <template v-if="hypothesisContent.asset_class"> · 品种：{{ hypothesisContent.asset_class }}</template>
          <template v-if="hypothesisContent.universe"> · 标的范围：{{ hypothesisContent.universe }}</template>
          <template v-if="hypothesisContent.timeframe"> · 周期：{{ hypothesisContent.timeframe }}</template>
        </p>
      </template>
      <p v-else-if="run.status === 'completed'" class="muted">
        这次运行没有留下可读的 AI 理解内容。
      </p>

      <div v-if="run.sources?.length" style="margin-top: 10px">
        <h4>这次读了哪些材料</h4>
        <p class="muted">
          共 {{ run.sources.length }} 段：{{ run.sources.map((s, i) => sourceTitle(s, i)).join('、') }}
        </p>
        <details v-if="isAdvanced" style="margin-top: 6px">
          <summary class="muted">每段材料的读取明细（技术细节）</summary>
          <table>
            <thead>
              <tr>
                <th>source_ref</th>
                <th>类型</th>
                <th>字符</th>
                <th>片段</th>
                <th>内容哈希</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in run.sources" :key="item.source_ref">
                <td class="mono">{{ item.source_ref }}</td>
                <td class="mono">{{ item.kind ?? '—' }}</td>
                <td>{{ item.characters_read ?? '—' }} / {{ item.size_bytes ?? '—' }}</td>
                <td>{{ item.fragment_count ?? '—' }}</td>
                <td class="mono">{{ String(item.text_hash ?? '—').slice(0, 12) }}…</td>
              </tr>
            </tbody>
          </table>
        </details>
      </div>
    </div>

    <!-- ③ 策略假设 -->
    <div v-if="hypothesisContent" class="card" style="margin-top: 14px">
      <h3>③ 策略假设（AI 读出来的规则）</h3>
      <p class="muted">
        每一条规则都有一个来源：原文明确写到的、从原文推断出来的、AI 自己补的假设、或者原文没说清楚的。
        普通模式下只需要留意被标成「AI 提出的假设」的那些——那不是你的原话。
      </p>

      <div v-if="hypothesisContent.rules.length">
        <h4>规则</h4>
        <ul class="answer-list">
          <li v-for="rule in hypothesisContent.rules" :key="rule.id">
            <span>{{ fieldLabel(rule.field) }}：{{ rule.statement }}</span>
            <template v-if="isAdvanced">
              <span class="badge" style="margin-left: 6px">{{ rule.origin }}</span>
              <span class="muted">
                · 置信度 {{ confidenceLabel(rule.confidence) }} · 规则号 {{ rule.id }}
                <template v-if="rule.derived_from"> · 来自 {{ rule.derived_from }}</template>
              </span>
            </template>
            <p v-if="rule.origin === 'ASSUMED'" class="notice warn" style="margin: 6px 0 0">
              ⚠️ AI 提出的假设，不是你的原话
            </p>
            <p v-if="isAdvanced && rule.required_capabilities?.length" class="muted" style="margin: 4px 0 0">
              这条规则需要的能力：{{ rule.required_capabilities.join('、') }}
            </p>
            <p v-if="isAdvanced && rule.evidence?.length" class="muted" style="margin: 4px 0 0">
              引用：
              <span v-for="(ev, index) in rule.evidence" :key="index">
                {{ ev.source_ref }}<template v-if="ev.locator">@{{ ev.locator }}</template>
                <template v-if="ev.quote">「{{ ev.quote }}」</template>
                <template v-if="index < (rule.evidence?.length ?? 1) - 1">；</template>
              </span>
            </p>
            <p v-if="isAdvanced && rule.parameters && Object.keys(rule.parameters).length" class="muted" style="margin: 4px 0 0">
              参数：{{ JSON.stringify(rule.parameters) }}
            </p>
            <p v-if="rule.note" class="muted" style="margin: 4px 0 0">{{ rule.note }}</p>
          </li>
        </ul>
      </div>
      <p v-else class="muted">这次没有读出规则，只留下了下面的未知项。</p>

      <div v-if="hypothesisContent.assumptions.length" style="margin-top: 10px">
        <h4>AI 自己补的假设</h4>
        <ul class="answer-list">
          <li v-for="(item, index) in hypothesisContent.assumptions" :key="index">
            {{ item.statement }}
            <span v-if="item.applies_to?.length" class="muted">（用在：{{ fieldList(item.applies_to) }}）</span>
            <span v-if="item.reason" class="muted"> · {{ item.reason }}</span>
          </li>
        </ul>
      </div>

      <div v-if="hypothesisContent.unknowns.length" style="margin-top: 10px">
        <h4>还不知道的（原文没说，你也没说）</h4>
        <ul class="answer-list">
          <li v-for="(item, index) in hypothesisContent.unknowns" :key="index">
            {{ fieldLabel(item.field) }}：{{ item.why }}
          </li>
        </ul>
      </div>

      <div v-if="hypothesisContent.ambiguities.length" style="margin-top: 10px">
        <h4>一句话有几种读法，需要你来定</h4>
        <ul class="answer-list">
          <li v-for="(item, index) in hypothesisContent.ambiguities" :key="index">
            「{{ item.phrase }}」可以是：{{ item.readings?.join(' / ') || '—' }}
            <span v-if="item.needs_decision" class="muted"> · 需要你决定用哪一种</span>
          </li>
        </ul>
      </div>

      <div v-if="hypothesisContent.limitations?.length" style="margin-top: 10px">
        <h4>这次研究的局限</h4>
        <ul class="answer-list">
          <li v-for="(item, index) in hypothesisContent.limitations ?? []" :key="index">{{ item }}</li>
        </ul>
      </div>

      <template v-if="isAdvanced">
        <p class="muted" style="margin-top: 10px">
          假设置信度 {{ confidenceLabel(hypothesisContent.confidence) }} ·
          假设行 #{{ run?.hypothesis?.hypothesis_id }} · 模型 {{ run?.hypothesis?.model ?? '—' }} ·
          提示词版本 {{ run?.hypothesis?.prompt_version ?? '—' }}
        </p>
        <p v-if="hypothesisContent.capability_requests.length" class="muted">
          模型自己声称需要的能力（服务端会重新核对）：
          <span v-for="(item, index) in hypothesisContent.capability_requests" :key="index">
            {{ item.capability }}<template v-if="item.claimed_supported">（模型认为系统支持）</template>
            <template v-if="index < hypothesisContent.capability_requests.length - 1">、</template>
          </span>
        </p>
      </template>
    </div>

    <!-- ④ 策略草案 -->
    <div v-if="draftContent" class="card" style="margin-top: 14px">
      <h3>④ 策略草案</h3>
      <p class="notice warn">
        ⚠️ 这份草案是给人读的：它不能直接运行，这一版也没有跑过任何回测。
        所以它里面不会有收益、回撤、夏普这类结果数字——那些只能由引擎算出来。
        <template v-if="isAdvanced">（{{ run?.draft?.content?.executable === false ? 'executable: false' : 'executable 字段缺失' }}）</template>
      </p>

      <p v-if="draftContent.understanding_of_original" class="conclusion-sentence">
        {{ draftContent.understanding_of_original }}
      </p>
      <p v-if="draftMarketSentence" class="muted">{{ draftMarketSentence }}</p>

      <div v-if="draftContent.rules.length">
        <h4>草案里的规则</h4>
        <ul class="answer-list">
          <li v-for="rule in draftContent.rules" :key="rule.id">
            <span>{{ fieldLabel(rule.field) }}：{{ rule.statement }}</span>
            <template v-if="isAdvanced">
              <span class="badge" style="margin-left: 6px">{{ rule.origin }}</span>
              <span class="muted">
                · 置信度 {{ confidenceLabel(rule.confidence) }} · 规则号 {{ rule.id }} ·
                {{ rule.derived_from ? `来自假设规则 ${rule.derived_from}` : 'AI 自己新增的规则（没有来源）' }}
              </span>
            </template>
            <p v-if="rule.origin === 'ASSUMED'" class="notice warn" style="margin: 6px 0 0">
              ⚠️ AI 提出的假设，不是你的原话
            </p>
            <p v-if="isAdvanced && rule.required_capabilities?.length" class="muted" style="margin: 4px 0 0">
              需要的能力：{{ rule.required_capabilities.join('、') }}
            </p>
          </li>
        </ul>
      </div>

      <div v-if="draftContent.indicators.length" style="margin-top: 10px">
        <h4>要用到的指标</h4>
        <ul class="answer-list">
          <li v-for="(item, index) in draftContent.indicators" :key="index">
            {{ item.name }}
            <template v-if="isAdvanced">
              <span class="badge" style="margin-left: 6px">{{ item.origin }}</span>
              <span class="muted">· 参数 {{ JSON.stringify(item.parameters ?? {}) }}</span>
            </template>
            <span v-if="item.note" class="muted"> · {{ item.note }}</span>
          </li>
        </ul>
      </div>

      <div v-if="draftContent.unknowns.length" style="margin-top: 10px">
        <h4>草案里仍然不知道的</h4>
        <ul class="answer-list">
          <li v-for="(item, index) in draftContent.unknowns" :key="index">
            {{ fieldLabel(item.field) }}：{{ item.why }}
          </li>
        </ul>
      </div>

      <div v-if="draftContent.required_capabilities.length" style="margin-top: 10px">
        <h4>这条策略要系统有的能力</h4>
        <ul class="answer-list">
          <li v-for="(item, index) in draftContent.required_capabilities" :key="index">
            {{ capabilityLabel(item.capability) }}：{{ item.reason }}
            <template v-if="item.suggested_alternative">
              <br />AI 建议的替代做法：{{ item.suggested_alternative }}
              <span v-if="item.alternative_is_experimental" class="muted">（这是实验性缩减版，不是原来的策略）</span>
            </template>
            <template v-if="isAdvanced">
              <span class="muted">
                （token {{ item.capability }} · 影响规则 {{ item.affected_rule }}）
              </span>
            </template>
          </li>
        </ul>
      </div>

      <div v-if="draftContent.experimental_alternatives.length" style="margin-top: 10px">
        <h4>AI 提出的小实验（不是原策略）</h4>
        <ul class="answer-list">
          <li v-for="(item, index) in draftContent.experimental_alternatives" :key="index">
            <b>{{ item.label }}</b>：{{ item.statement }}
            <span v-if="item.what_it_gives_up?.length" class="muted">
              · 放弃的东西：{{ item.what_it_gives_up.join('、') }}
            </span>
          </li>
        </ul>
      </div>

      <div v-if="draftContent.notes?.length" style="margin-top: 10px">
        <h4>草案附注</h4>
        <ul class="answer-list">
          <li v-for="(item, index) in draftContent.notes ?? []" :key="index">{{ item }}</li>
        </ul>
      </div>

      <!-- 人工确认：把人自己的判断单独记一笔，和 AI 的结论分开放 -->
      <div style="margin-top: 12px; border-top: 1px solid var(--border); padding-top: 10px">
        <h4>人工确认</h4>
        <p class="muted">
          当前人工结论：<b>{{ decisionLabel(confirmation?.decision) }}</b>
          <template v-if="confirmation">
            · 由 {{ confirmation.decided_by }} 于 {{ formatDateTime(confirmation.decided_at) }} 记录
            <template v-if="isAdvanced">
              · 审计号 {{ confirmation.audit_id }} · 人工裁决
              {{ confirmation.is_human_decision ? '是' : '否' }}
            </template>
          </template>
        </p>
        <p v-if="confirmation?.note" class="muted">备注：{{ confirmation.note }}</p>
        <p class="muted">
          这一步只记录你自己的判断：不改动这份草案、不调用 AI、也不生成策略版本。
          它决定的是「这份草案允不允许被编译」——服务端只接受结论为「确认」的草案。
        </p>
        <textarea
          v-model="confirmationNote"
          rows="2"
          maxlength="2000"
          :disabled="confirming"
          placeholder="备注（可选，最多 2000 字）"
          style="min-height: 52px; font-family: inherit; font-size: 13px"
        ></textarea>
        <div class="row" style="margin-top: 8px">
          <button :disabled="confirming" @click="confirmDraft('confirmed')">确认</button>
          <button class="ghost" :disabled="confirming" @click="confirmDraft('rejected')">驳回</button>
          <button class="ghost" :disabled="confirming" @click="confirmDraft('needs_revision')">需修改</button>
          <span v-if="confirming" class="muted">正在记录这次人工决定…</span>
        </div>
      </div>

      <template v-if="isAdvanced">
        <p class="muted" style="margin-top: 10px">
          草案 #{{ run?.draft?.draft_id }} · 版本 {{ run?.draft?.version ?? '—' }} ·
          模型自报结论 {{ run?.draft?.content?.status ?? '—' }} · 服务端判定
          {{ run?.draft?.capability_status ?? '—' }} · 模型 {{ run?.draft?.model ?? '—' }} ·
          已编译版本 {{ run?.draft?.compiled_strategy_version_id ?? '（还没有）' }}
        </p>
        <p v-if="draftContent.parameters && Object.keys(draftContent.parameters).length" class="muted">
          草案参数：{{ JSON.stringify(draftContent.parameters) }}
        </p>
      </template>
    </div>

    <!-- 还没有草案：给一条重跑架构师的路 -->
    <div v-else-if="run && hypothesisContent" class="card card-quiet" style="margin-top: 14px">
      <h3>④ 策略草案</h3>
      <p class="muted">
        这次还没有草案：可能是架构师那一步没有通过校验，也可能还没有跑到那一步。
      </p>
      <button :disabled="formalizing" @click="formalize">
        {{ formalizing ? '正在重新生成…' : '重新生成策略草案' }}
      </button>
      <p class="muted" style="margin-top: 8px">
        这一步只重跑架构师：用的还是上面已经存下来的假设，不会再读一遍材料，也不会执行任何策略。
      </p>
    </div>

    <!-- ⑤ 能力检查 -->
    <div v-if="capability" class="card" style="margin-top: 14px">
      <h3>⑤ 能力检查</h3>
      <p class="verdict-line">
        <span class="verdict" :class="verdictTone">{{ verdictLabelOf(verdict) }}</span>
        <template v-if="isAdvanced"><span class="muted">（{{ verdict }}）</span></template>
      </p>
      <p class="conclusion-sentence">{{ verdictSentence }}</p>

      <p v-if="capability.missing.length" class="notice warn" style="margin-top: 10px">
        ⚠️ 缺少的能力：{{ capabilityList(capability.missing) }}
      </p>
      <p v-if="capability.partial.length" class="notice warn" style="margin-top: 10px">
        ⚠️ 只能部分支持的能力：{{ capabilityList(capability.partial) }}（系统能做一部分，不能做全部）
      </p>
      <p v-if="!capability.missing.length && !capability.partial.length" class="muted" style="margin-top: 10px">
        没有缺项，也没有只能部分支持的能力。
      </p>

      <p class="muted" style="margin-top: 10px">
        这份结论只回答「系统能不能实现它」，不回答「它赚不赚钱」：这一版没有跑过任何回测，
        所以既不能说它可行，也不能说它不行。缺的能力不会因为编译而消失——编译器只做翻译，
        它遇到做不到的地方会直接拒绝。
      </p>

      <template v-if="isAdvanced">
        <details style="margin-top: 6px">
          <summary class="muted">逐项明细（服务端核对，不采信模型的声称）</summary>
          <table>
            <thead>
              <tr>
                <th>能力</th>
                <th>状态</th>
                <th>谁要的</th>
                <th>影响规则</th>
                <th>原因</th>
                <th>模型声称支持</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, index) in capability.items" :key="index">
                <td class="mono">{{ item.capability }}</td>
                <td class="mono">{{ item.status }}</td>
                <td class="mono">{{ item.required_by ?? '—' }}</td>
                <td class="mono">{{ item.affected_rule ?? '—' }}</td>
                <td>{{ item.reason || '—' }}</td>
                <td>
                  {{ item.claimed_supported ? '是' : '否' }}
                  <span v-if="item.overclaimed" class="muted">（与能力清单不符）</span>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="capability.model_capabilities.length" class="muted">
            模型自身的能力项（不计入系统支持度）：{{ capability.model_capabilities.join('、') }}
          </p>
        </details>
      </template>
    </div>

    <!-- ⑥ 编译为策略版本：人工确认之后的第一道实门 -->
    <div v-if="draftContent && !compiledVersion" class="card" style="margin-top: 14px">
      <h3>⑥ 编译为策略版本</h3>
      <p class="muted">
        编译是把这份草案冻结成一条正式的策略版本：内容不会变，之后可以回测、可以激活。
        编译器是确定性的，它只认草案里已经写下来的东西——遇到需要人来定的地方，它会拒绝，而不是替你决定。
        编译不调用 AI，也不产生费用。
      </p>

      <p v-if="compileBlockedReason" class="notice warn">⚠️ {{ compileBlockedReason }}</p>

      <label class="muted" for="lab-compile-strategy">目标策略（编译出来的版本会挂在这个策略下面）</label>
      <div class="row">
        <select
          id="lab-compile-strategy"
          v-model="compileStrategyId"
          :disabled="compiling || loadingStrategies"
        >
          <option value="" disabled>
            {{ loadingStrategies ? '正在读取策略列表…' : '请选择目标策略' }}
          </option>
          <option v-for="item in strategies" :key="item.id" :value="String(item.id)">
            {{ item.name }}（已有 {{ item.version_count }} 个版本）
          </option>
        </select>
        <button class="ghost" :disabled="loadingStrategies" @click="loadStrategies">
          {{ loadingStrategies ? '读取中…' : '刷新策略列表' }}
        </button>
      </div>
      <p v-if="!strategies.length && !loadingStrategies" class="muted" style="margin-top: 6px">
        现在还没有任何策略。用下面的输入框建一个（也可以去「我的策略」页面创建）。
      </p>

      <label class="muted" for="lab-new-strategy" style="margin-top: 10px">新建一个策略</label>
      <div class="row">
        <input
          id="lab-new-strategy"
          v-model="newStrategyName"
          type="text"
          maxlength="200"
          :disabled="creatingStrategy"
          placeholder="例如：均线突破（日线）"
        />
        <button
          class="ghost"
          :disabled="creatingStrategy || !newStrategyName.trim()"
          @click="createStrategyInline"
        >
          {{ creatingStrategy ? '创建中…' : '新建策略' }}
        </button>
      </div>
      <p v-if="!newStrategyName.trim()" class="muted" style="margin-top: 6px">
        新策略的名字不能为空，所以「新建策略」现在是灰的。
      </p>

      <button :disabled="!canCompile" style="margin-top: 10px" @click="compile">
        {{ compiling ? '正在编译…' : '编译为策略版本' }}
      </button>
      <p v-if="compiling" class="muted" style="margin-top: 8px">
        正在把草案交给编译器；这一步是确定性的，不调用 AI。
      </p>

      <p v-if="compileError" class="error" style="margin-top: 10px">{{ compileError }}</p>
      <p v-if="compileDetail" class="muted">后端原文：{{ compileDetail }}</p>

      <!-- 编译器拒绝（422）：说清是哪一类问题，原始报告收进折叠区 -->
      <div v-if="compileOutcome" style="margin-top: 12px">
        <p class="verdict-line">
          <span class="verdict" :class="compileOutcomeTone">{{ compileOutcomeLabel }}</span>
        </p>
        <p class="conclusion-sentence">{{ compileOutcomeSentence }}</p>
        <ul v-if="compileRejections.length" class="answer-list">
          <li v-for="(item, index) in compileRejections" :key="index">
            {{ rejectionReason(item) }}
            <span class="muted">
              （位置：{{ item.slot || '—' }}
              <template v-if="item.rule_ids?.length"> · 涉及规则 {{ item.rule_ids.join('、') }}</template>
              <template v-if="item.user_decidable"> · 需要人决定</template>）
            </span>
            <p v-if="isAdvanced && item.detail" class="muted" style="margin: 4px 0 0">{{ item.detail }}</p>
          </li>
        </ul>
        <p v-if="!compileRejections.length" class="muted">编译器没有列出具体原因。</p>
        <p v-if="compileOutcome.result === 'NEEDS_USER_DECISION'" class="muted">
          需要人定的事只能由你来定：回到上面的假设与草案，把含糊的地方写清楚，
          可以用「需修改」记下你的意见，然后重新生成草案再编译。
        </p>
        <details v-if="compileReportText" style="margin-top: 8px">
          <summary class="muted">编译器完整报告（技术细节）</summary>
          <pre class="code-block" style="max-height: 320px; overflow: auto">{{ compileReportText }}</pre>
        </details>
      </div>
    </div>

    <!-- ⑦ 策略版本与激活 -->
    <div v-if="compiledVersion" class="card" style="margin-top: 14px">
      <h3>⑦ 策略版本</h3>
      <p class="conclusion-sentence">
        草案已经编译成策略版本 {{ compiledVersion.version }}（策略 #{{ compiledVersion.strategy_id }}）。
      </p>
      <p class="muted">
        校验状态：<b>{{ validationLabel(compiledVersion.validation_status) }}</b>
        · 是否当前版本：<b>{{ compiledVersion.is_current ? '是，正在生效' : '不是，还没激活' }}</b>
        · 编译时间：{{ formatDateTime(compiledVersion.created_at) }}
        <template v-if="isAdvanced"> · 不可变哈希 {{ compiledVersion.immutable_hash.slice(0, 12) }}…</template>
      </p>
      <p v-if="compileReport" class="muted">
        这次编译的原始报告在下面的折叠区里。报告不随运行保存，所以重新打开这次研究时，
        只剩版本本身——要看报告就得当场编译。
      </p>

      <!-- 交接口：问一次「用哪份数据验证」，因为标的不是策略内容（ADR-191） -->
      <div style="margin-top: 14px">
        <h4 style="margin: 0 0 4px">用哪份数据验证这一版？</h4>
        <template v-if="backtestTargets.length">
          <label class="muted" for="lab-backtest-target">标的</label>
          <select id="lab-backtest-target" v-model="backtestSeriesId">
            <option :value="null">请选择一个标的…</option>
            <option v-for="option in backtestTargets" :key="option.seriesId" :value="option.seriesId">
              {{ option.symbol }} · {{ timeframeLabel(option.timeframe) }} · {{ option.start }} 至
              {{ option.end }}
            </option>
          </select>
          <p v-if="chosenBacktestTarget" class="muted">
            数据质量：{{ qualityLabel(chosenBacktestTarget.quality) }}。策略规则里没有「买什么」，
            标的是你在这里指定的，规则本身不受影响。
          </p>
        </template>
        <p v-else class="muted">
          还没有可用的行情数据，所以现在没法直接去回测。先到
          <RouterLink to="/data">「数据」</RouterLink> 同步一份，再回到这一页。
        </p>
        <p v-if="backtestBlockedReason" class="muted">{{ backtestBlockedReason }}</p>
      </div>

      <div class="row" style="margin-top: 8px">
        <button v-if="!compiledVersion.is_current" :disabled="activating" @click="activate">
          {{ activating ? '正在激活…' : '激活为当前版本' }}
        </button>
        <span v-else class="verdict ok">已是当前版本</span>
        <button class="ghost" :disabled="!chosenBacktestTarget" @click="goToBacktest">
          去「回测」用这一版
        </button>
      </div>
      <p class="muted" style="margin-top: 8px">
        点「去「回测」用这一版」会带上你选的标的直接开跑，跑完不用你再点一次运行。
      </p>
      <p class="muted">
        激活只改「哪一版是当前版本」：不改这一版的内容，不触发回测，也不会下单。
        之后可以随时再激活别的版本——不激活也能先拿去回测。
      </p>
      <p v-if="versionError" class="error">{{ versionError }}</p>

      <details v-if="compileReportText" style="margin-top: 8px">
        <summary class="muted">编译器完整报告（技术细节）</summary>
        <pre class="code-block" style="max-height: 320px; overflow: auto">{{ compileReportText }}</pre>
      </details>
    </div>

    <!-- 服务器说这条草案编译过，但版本还没读回来 -->
    <div
      v-else-if="draftCompiledVersionId && !compiledVersion"
      class="card card-quiet"
      style="margin-top: 14px"
    >
      <h3>⑦ 策略版本</h3>
      <p class="muted">
        这条草案已经编译过（策略版本 #{{ draftCompiledVersionId }}），正在把版本信息读回来…
      </p>
      <div class="row">
        <button class="ghost" :disabled="loadingVersion" @click="refreshRun">
          {{ loadingVersion ? '读取中…' : '重新读一次' }}
        </button>
      </div>
      <p v-if="versionError" class="error">{{ versionError }}</p>
    </div>

    <p class="next-line">
      下一步：{{ nextStep.text }}<RouterLink v-if="nextStep.to" :to="nextStep.to">{{ nextStep.linkText }}</RouterLink>
    </p>

    <!-- 最近的研究：刷新页面之后从这里把上一次的运行（连同草案、确认、已编译版本）读回来 -->
    <div class="card" style="margin-top: 14px">
      <div class="row" style="justify-content: space-between; align-items: flex-start">
        <h3 style="margin: 0">最近的研究</h3>
        <button class="ghost" :disabled="loadingRuns" @click="loadRuns">
          {{ loadingRuns ? '读取中…' : '刷新列表' }}
        </button>
      </div>
      <p v-if="!runs.length" class="muted">
        还没有任何研究记录。上面提交一次之后，这里会留着它，随时能再打开。
      </p>
      <table v-else>
        <thead>
          <tr>
            <th>时间</th>
            <th>研究问题</th>
            <th>状态</th>
            <th>能力结论</th>
            <th v-if="isAdvanced">运行号</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in runs" :key="item.run_id">
            <td class="muted">{{ formatDateTime(item.created_at) }}</td>
            <td>{{ item.question }}</td>
            <td>{{ statusLabel(item.status) }}</td>
            <td>{{ verdictLabelOf(item.capability_status) }}</td>
            <td v-if="isAdvanced" class="mono">#{{ item.run_id }}</td>
            <td>
              <button class="ghost" :disabled="loadingRun" @click="openRun(item.run_id)">打开</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="runs.length" class="muted" style="margin-top: 8px">
        打开一条记录只会读回当时的结果，不会重新调用 AI，也不会产生任何费用。
        如果那次编译过，页面会直接显示编译出的策略版本和它的激活状态。
      </p>
    </div>

    <!-- 实验：把「跑一次」变成一条留在服务端的记录（新跑 → 看结果 → 刷新后回访 → 对比 → 删除） -->
    <div class="card" style="margin-top: 14px">
      <h3>实验</h3>
      <p class="muted">
        一次实验 = 用某一版策略按一种跑法跑一次，结果存在服务端：每个参数组合对应的分数都留着，
        刷新页面（甚至关掉浏览器再回来）还找得到。跑的是后端已经实现好的引擎，这一页只负责发起和查看。
      </p>

      <p v-if="experimentError" class="error">{{ experimentError }}</p>
      <p v-if="experimentDetailError" class="error">{{ experimentDetailError }}</p>
      <p v-if="experimentNotice && !experimentError && !experimentDetailError" class="notice">
        {{ experimentNotice }}
      </p>

      <h4 style="margin-top: 16px">① 新跑一次</h4>
      <div class="grid cols-2">
        <div>
          <label class="muted" for="experiment-version">用哪一版策略</label>
          <select id="experiment-version" v-model="experimentVersionId" :disabled="loadingExperimentVersions">
            <option value="">（先选一版）</option>
            <option v-for="version in experimentVersions" :key="version.id" :value="String(version.id)">
              {{ experimentVersionLabel(version) }}
            </option>
          </select>
          <p v-if="loadingExperimentVersions" class="muted">正在读策略版本…</p>
          <p v-else-if="!experimentVersions.length" class="muted">
            还没有任何策略版本：先在上面把策略草案编译成一条版本，再回到这里。
          </p>
        </div>
        <div>
          <label class="muted" for="experiment-kind">跑法</label>
          <select id="experiment-kind" v-model="experimentKind">
            <option v-for="kind in EXPERIMENT_KINDS" :key="kind.value" :value="kind.value">
              {{ kind.label }}
            </option>
          </select>
          <p class="muted">{{ experimentKindHint(experimentKind) }}</p>
        </div>
      </div>

      <div class="grid cols-2" style="margin-top: 12px">
        <div>
          <label class="muted" for="experiment-name">给它起个名字</label>
          <input
            id="experiment-name"
            v-model="experimentName"
            type="text"
            maxlength="120"
            :placeholder="experimentDefaultName"
          />
          <p class="muted">留空就用「{{ experimentDefaultName }}」；列表里靠这个名字认出它。</p>
        </div>
        <div v-if="experimentNeedsSeries">
          <label class="muted" for="experiment-symbol">标的代码</label>
          <input
            id="experiment-symbol"
            v-model="experimentSymbol"
            type="text"
            list="experiment-asset-list"
            placeholder="例如 SPY"
          />
          <datalist id="experiment-asset-list">
            <option v-for="asset in experimentAssets" :key="asset.id" :value="asset.symbol" />
          </datalist>
          <p class="muted">填一个有数据的代码；高级设置里也可以直接填数据序列号。</p>
        </div>
      </div>

      <!-- 成交重采样：从已经跑完的回测里挑一次，不重跑那次回测 -->
      <div v-if="experimentKind === 'monte_carlo'" class="grid cols-2" style="margin-top: 12px">
        <div>
          <label class="muted" for="experiment-source-run">重采样哪一次回测</label>
          <select
            id="experiment-source-run"
            v-model="experimentBacktestRunId"
            :disabled="loadingExperimentBacktests"
          >
            <option value="">（选一次已经跑完的回测）</option>
            <option v-for="item in experimentBacktests" :key="item.id" :value="String(item.id)">
              #{{ item.id }} · {{ item.symbol ?? '未标标的' }} · {{ timeframeLabel(item.timeframe) }} ·
              {{ formatDateTime(item.created_at) }}
            </option>
          </select>
          <p v-if="loadingExperimentBacktests" class="muted">正在读回测记录…</p>
          <p v-else-if="!experimentBacktests.length" class="muted">
            还没有已完成的回测记录：先去「回测」页跑一次，再回来重采样它的成交。
          </p>
          <p v-else class="muted">
            只会读那次回测存下来的成交记录，不会重跑回测、也不会产生新的回测运行。
            一次成交都没有的回测不能重采样，服务端会直接拒绝并说明原因。
          </p>
          <p class="muted">选中的回测运行号：{{ experimentBacktestRunId || '（还没选）' }}</p>
        </div>
      </div>
      <div v-if="experimentKind === 'monte_carlo'" class="grid cols-3" style="margin-top: 12px">
        <div>
          <label class="muted" for="experiment-runs">重采样次数</label>
          <input id="experiment-runs" v-model="experimentRuns" type="text" inputmode="numeric" />
        </div>
        <div>
          <label class="muted" for="experiment-trades-per-run">每次取多少笔成交</label>
          <input
            id="experiment-trades-per-run"
            v-model="experimentTradesPerRun"
            type="text"
            inputmode="numeric"
            placeholder="留空 = 和原来一样多"
          />
        </div>
        <div>
          <label class="muted" for="experiment-seed">随机种子</label>
          <input id="experiment-seed" v-model="experimentSeed" type="text" inputmode="numeric" />
          <p class="muted">同一个种子会得到同一批结果，方便和别人核对。</p>
        </div>
      </div>

      <!-- 滚动前进：训练窗口 + 检验窗口 -->
      <div v-if="experimentKind === 'walk_forward'" class="grid cols-3" style="margin-top: 12px">
        <div>
          <label class="muted" for="experiment-train-bars">训练窗口（根）</label>
          <input id="experiment-train-bars" v-model="experimentTrainBars" type="text" inputmode="numeric" />
        </div>
        <div>
          <label class="muted" for="experiment-test-bars">检验窗口（根）</label>
          <input id="experiment-test-bars" v-model="experimentTestBars" type="text" inputmode="numeric" />
        </div>
        <div>
          <label class="muted" for="experiment-step">步长（根）</label>
          <input
            id="experiment-step"
            v-model="experimentStep"
            type="text"
            inputmode="numeric"
            placeholder="留空 = 引擎默认"
          />
          <p class="muted">留空表示按引擎的默认步长往前挪。</p>
        </div>
      </div>

      <!-- 样本外检验：按时间切一刀，只看后面那段 -->
      <div v-if="experimentKind === 'oos'" class="grid cols-2" style="margin-top: 12px">
        <div>
          <label class="muted" for="experiment-oos-pct">样本外比例</label>
          <input id="experiment-oos-pct" v-model="experimentOosPct" type="text" inputmode="decimal" />
          <p class="muted">0 到 1 之间：0.2 表示最后 20% 的数据只用来检验。</p>
        </div>
      </div>

      <!-- 敏感性：扫哪些参数、按哪个指标排名 -->
      <div v-if="experimentKind === 'sensitivity'" class="grid cols-2" style="margin-top: 12px">
        <div>
          <label class="muted" for="experiment-metric">按哪个指标排名</label>
          <select id="experiment-metric" v-model="experimentMetric">
            <option v-for="key in EXPERIMENT_METRICS" :key="key" :value="key">{{ metricKeyLabel(key) }}</option>
          </select>
          <p class="muted">扫描结果按这个指标从高到低排；页面只负责把分数摊开，不代表推荐哪一组参数。</p>
        </div>
        <div>
          <label class="muted" for="experiment-grid">要扫的参数（网格）</label>
          <textarea
            id="experiment-grid"
            v-model="experimentGridText"
            class="mono"
            style="min-height: 96px"
            placeholder='例如 {"risk_pct": [1, 2, 3]}'
          ></textarea>
          <p class="muted">
            每个参数给出候选值，所有组合各跑一遍（点数 = 各参数取值个数相乘）。当前
            {{ experimentGridPoints ?? '—' }} 个点，服务端上限 {{ EXPERIMENT_MAX_GRID_POINTS }} 个。
          </p>
        </div>
      </div>

      <details style="margin-top: 12px">
        <summary class="muted">高级设置（可选，不填就用默认值）</summary>
        <div class="grid cols-2" style="margin-top: 10px">
          <div>
            <label class="muted" for="experiment-timeframe">周期</label>
            <select id="experiment-timeframe" v-model="experimentTimeframe">
              <option v-for="tf in EXPERIMENT_TIMEFRAMES" :key="tf" :value="tf">{{ timeframeLabel(tf) }}</option>
            </select>
          </div>
          <div>
            <label class="muted" for="experiment-parameters">策略参数覆盖</label>
            <textarea
              id="experiment-parameters"
              v-model="experimentParametersText"
              class="mono"
              style="min-height: 72px"
              placeholder='例如 {"risk_pct": 2}'
            ></textarea>
            <p class="muted">只覆盖这一版策略里的同名参数；留空表示按策略版本自己的设定跑。</p>
          </div>
        </div>
        <div class="grid cols-3" style="margin-top: 10px">
          <div>
            <label class="muted" for="experiment-start">开始日期</label>
            <input id="experiment-start" v-model="experimentStart" type="text" placeholder="YYYY-MM-DD" />
          </div>
          <div>
            <label class="muted" for="experiment-end">结束日期</label>
            <input id="experiment-end" v-model="experimentEnd" type="text" placeholder="YYYY-MM-DD" />
          </div>
          <div>
            <label class="muted" for="experiment-series">数据序列号</label>
            <input
              id="experiment-series"
              v-model="experimentSeriesId"
              type="text"
              inputmode="numeric"
              placeholder="例如 12"
            />
            <p class="muted">填了它就可以不填标的代码。</p>
          </div>
        </div>
        <div style="margin-top: 10px">
          <label class="muted" for="experiment-notes">备注</label>
          <input id="experiment-notes" v-model="experimentNotes" type="text" placeholder="这次想验证什么？" />
        </div>
        <p class="muted" style="margin-top: 8px">
          日期和数据序列号是给引擎的精确定位；平时只用标的代码加上默认周期就够了。
        </p>
      </details>

      <div class="row" style="margin-top: 14px; align-items: center">
        <button :disabled="creatingExperiment || !!experimentBlockedReason" @click="createExperiment">
          {{ creatingExperiment ? '正在跑…' : '开始实验' }}
        </button>
        <button
          class="ghost"
          :disabled="loadingExperimentVersions || loadingExperimentBacktests"
          @click="reloadExperimentChoices"
        >
          重新读取可选项
        </button>
      </div>
      <p v-if="experimentBlockedReason" class="muted">{{ experimentBlockedReason }}</p>
      <p v-if="creatingExperiment" class="wait">
        正在跑：实验在服务端同步执行，网格点多的敏感性扫描要等一会儿，请不要关掉这一页。
      </p>

      <h4 style="margin-top: 18px">② 这次实验的结果</h4>
      <p v-if="loadingExperiment" class="wait">正在读这条实验…</p>
      <p v-else-if="!openExperiment" class="muted">
        还没有打开任何一条实验：上面跑一次，或者到下面的「最近的实验」里打开一条。
      </p>
      <template v-else>
        <p>
          <strong>{{ openExperiment.name }}</strong>
          <span class="badge">{{ experimentKindLabel(openExperiment.kind) }}</span>
          <span class="badge">{{ experimentStatusLabel(openExperiment.status) }}</span>
        </p>
        <p class="muted">
          {{ strategyName(openExperiment.strategy_version_id) }}（策略版本 #{{ openExperiment.strategy_version_id }}）
          · {{ openExperiment.symbol ?? '未标标的' }} · {{ timeframeLabel(openExperiment.timeframe) }} · 存下
          {{ openExperiment.result_count }} 条结果 · 建于 {{ formatDateTime(openExperiment.created_at) }}
          <template v-if="openExperiment.completed_at">
            · 跑完于 {{ formatDateTime(openExperiment.completed_at) }}
          </template>
        </p>

        <p v-if="openExperiment.status === 'failed'" class="error">
          ⚠️ 这条实验没有跑完：{{ openExperiment.error_message ?? '服务端没有给出原因。' }}
        </p>
        <p v-if="openExperiment.status === 'failed'" class="muted">
          这条记录仍然留着：它就是这次尝试的结果，参数、目标和报错都在这里。改完输入再跑一次就行。
        </p>

        <p v-if="experimentSummaryMetrics.length" class="muted" style="margin-top: 10px">
          关键指标（同一条实验存下来的对照口径）：
        </p>
        <div v-if="experimentSummaryMetrics.length" class="grid cols-4">
          <div v-for="item in experimentSummaryMetrics" :key="item.key" class="stat small">
            <div class="muted">
              {{ metricKeyLabel(item.key) }}
              <MetricHint :label="item.key" />
            </div>
            <div :class="toneOf(item.value)">{{ formatMetric(item.key, item.value) }}</div>
          </div>
        </div>

        <div v-if="openExperiment.kind === 'sensitivity'" style="margin-top: 12px">
          <p v-if="openExperiment.status === 'completed'" class="muted">
            排名用的指标：{{ metricKeyLabel(summaryText('metric') ?? 'total_return') }}
            <template v-if="summaryNumber('evaluated_points') !== null">
              · 网格 {{ summaryNumber('grid_points') ?? '—' }} 个点，实际测到
              {{ summaryNumber('evaluated_points') }} 个，参与排名 {{ summaryNumber('ranked_points') ?? '—' }} 个
            </template>
          </p>
          <p v-if="summaryObject('best') || summaryObject('worst')" class="conclusion-sentence">
            <span v-if="summaryObject('best')">分数最高的一组：{{ compactPointText(summaryObject('best')) }}</span>
            <span v-if="summaryObject('worst')">　分数最低的一组：{{ compactPointText(summaryObject('worst')) }}</span>
          </p>
          <p v-if="sensitivityWarmupUnmet" class="notice warn">
            ⚠️ 有 {{ summaryNumber('warmup_unmet_points') ?? '若干' }} 个点整段落在预热期内：引擎算不出指标，
            它们不参与平均、极差和排名（ADR-055），下表里会标成「预热不足」。
          </p>
        </div>

        <h4 style="margin-top: 18px">③ 存下来的结果</h4>
        <p v-if="!experimentResults.length" class="muted">
          这条实验没有存下任何结果（引擎跑挂的那次就是这种：以上面的报错为准）。
        </p>
        <template v-else>
          <p class="muted">
            <template v-if="openExperiment.kind === 'sensitivity'">
              每一行是一组参数跑出来的结果，按{{ metricKeyLabel(summaryText('metric') ?? 'total_return') }}从高到低排；
              没测到的点排在最后。排序只是把分数摊开看，不是推荐。
            </template>
            <template v-else>每一行是这次实验存下的一条结果，指标全部来自服务端。</template>
          </p>
          <table>
            <thead>
              <tr>
                <th>结果</th>
                <th v-for="axis in experimentResultAxes" :key="axis" class="mono">{{ axis }}</th>
                <th v-for="key in experimentResultMetrics" :key="key">{{ metricKeyLabel(key) }}</th>
                <th v-if="isAdvanced">结果号</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in experimentResults"
                :key="row.id"
                :class="{
                  muted: resultWarmupUnmet(row),
                  best: isBestResult(row),
                  worst: isWorstResult(row),
                }"
              >
                <td>
                  {{ row.label ?? resultHash(row) ?? '—' }}
                  <span v-if="isBestResult(row)" class="badge">分数最高</span>
                  <span v-if="isWorstResult(row)" class="badge">分数最低</span>
                  <span v-if="resultWarmupUnmet(row)" class="badge">预热不足</span>
                </td>
                <td v-for="axis in experimentResultAxes" :key="axis" class="mono">{{ axisValue(row, axis) }}</td>
                <td v-for="key in experimentResultMetrics" :key="key" :class="toneOf(resultMetric(row, key))">
                  {{ formatMetric(key, resultMetric(row, key)) }}
                </td>
                <td v-if="isAdvanced" class="mono">#{{ row.id }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="openExperiment.kind === 'sensitivity'" class="muted">
            带「预热不足」的行是引擎明确标为不可比的点：它整段落在指标预热期内，没有可用的分数（ADR-055），
            不参与排名，也不进这里的排序。
          </p>
        </template>

        <details style="margin-top: 12px">
          <summary class="muted">技术细节（原始 JSON：这次请求与每个点的引擎返回）</summary>
          <pre class="code-block" style="max-height: 320px; overflow: auto">{{ openExperimentText }}</pre>
        </details>

        <div class="row" style="margin-top: 10px; align-items: center">
          <button class="ghost" :disabled="loadingExperiment" @click="refreshOpenExperiment">重新读一次</button>
          <button class="ghost" @click="closeExperiment">收起</button>
        </div>
      </template>

      <h4 style="margin-top: 18px">④ 最近的实验</h4>
      <div class="row" style="justify-content: space-between; align-items: flex-start">
        <p class="muted" style="margin: 0">
          最新 20 条。列表和结果都存在服务端，刷新页面之后还在；勾选两条以上可以对比。
        </p>
        <button class="ghost" :disabled="loadingExperiments" @click="loadExperiments">
          {{ loadingExperiments ? '读取中…' : '刷新列表' }}
        </button>
      </div>
      <p v-if="!experimentList.length" class="muted">
        还没有任何实验记录。上面跑一次之后，这里会留着它，随时能再打开。
      </p>
      <table v-else>
        <thead>
          <tr>
            <th>对比</th>
            <th>时间</th>
            <th>名字</th>
            <th>跑法</th>
            <th>状态</th>
            <th>结果数</th>
            <th>总收益</th>
            <th>最大回撤</th>
            <th>夏普比率</th>
            <th>胜率</th>
            <th>交易数</th>
            <th v-if="isAdvanced">实验号</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in experimentList" :key="item.id">
            <td>
              <input
                type="checkbox"
                style="width: auto"
                :checked="compareExperimentIds.includes(item.id)"
                @change="toggleExperimentCompare(item.id)"
              />
            </td>
            <td class="muted">{{ formatDateTime(item.created_at) }}</td>
            <td>{{ item.name }}</td>
            <td>{{ experimentKindLabel(item.kind) }}</td>
            <td :class="{ error: item.status === 'failed' }">{{ experimentStatusLabel(item.status) }}</td>
            <td>{{ item.result_count }}</td>
            <td :class="toneOf(item.metrics?.total_return)">
              {{ formatMetric('total_return', item.metrics?.total_return) }}
            </td>
            <td :class="toneOf(item.metrics?.max_drawdown)">
              {{ formatMetric('max_drawdown', item.metrics?.max_drawdown) }}
            </td>
            <td :class="toneOf(item.metrics?.sharpe)">{{ formatMetric('sharpe', item.metrics?.sharpe) }}</td>
            <td :class="toneOf(item.metrics?.win_rate)">{{ formatMetric('win_rate', item.metrics?.win_rate) }}</td>
            <td :class="toneOf(item.metrics?.number_of_trades)">
              {{ formatMetric('number_of_trades', item.metrics?.number_of_trades) }}
            </td>
            <td v-if="isAdvanced" class="mono">#{{ item.id }}</td>
            <td>
              <button class="ghost" :disabled="loadingExperiment" @click="openExperimentById(item.id)">打开</button>
              <button
                class="ghost danger"
                :disabled="deletingExperimentId === item.id"
                @click="removeExperiment(item.id, item.name)"
              >
                {{ deletingExperimentId === item.id ? '删除中…' : '删除' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="experimentList.length" class="muted" style="margin-top: 8px">
        打开一条只会读回当时存下的结果，不会重新跑。删除实验只删这条记录和它的结果，
        它背后那次回测运行不会被删掉——在「回测」页里仍然找得到。
      </p>

      <div class="row" style="margin-top: 12px; align-items: center">
        <button :disabled="comparingExperiments || compareExperimentIds.length < 2" @click="runExperimentCompare">
          {{ comparingExperiments ? '对比中…' : `对比选中（${compareExperimentIds.length}）` }}
        </button>
        <button class="ghost" :disabled="!compareExperimentIds.length" @click="clearExperimentCompare">
          清空选择
        </button>
      </div>
      <p v-if="compareExperimentIds.length < 2" class="muted">
        勾两条以上才能对比（现在选了 {{ compareExperimentIds.length }} 条）：对比表用「总收益 / 最大回撤 /
        夏普比率 / 胜率 / 交易次数」这五项，和「回测」页的对比口径一致。
      </p>

      <div v-if="experimentCompare" style="margin-top: 12px">
        <table>
          <thead>
            <tr>
              <th>指标</th>
              <th v-for="row in experimentCompare.experiments" :key="String(row.id)">
                {{ compareText(row, 'name') ?? `实验 #${row.id}` }}
                <div class="muted">
                  {{ experimentKindLabel(compareText(row, 'kind') ?? '') }} ·
                  {{ experimentStatusLabel(compareText(row, 'status') ?? '') }} ·
                  {{ compareText(row, 'symbol') ?? '未标标的' }} ·
                  {{ timeframeLabel(compareText(row, 'timeframe')) }}
                </div>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="metric in experimentCompare.metrics" :key="metric">
              <td>
                {{ metricKeyLabel(metric) }}
                <MetricHint :label="metric" />
              </td>
              <td
                v-for="row in experimentCompare.experiments"
                :key="String(row.id)"
                :class="toneOf(compareNumber(row, metric))"
              >
                {{ formatMetric(metric, compareNumber(row, metric)) }}
              </td>
            </tr>
            <tr>
              <td>存下的结果数</td>
              <td v-for="row in experimentCompare.experiments" :key="String(row.id)">
                {{ compareNumber(row, 'result_count') ?? '—' }}
              </td>
            </tr>
          </tbody>
        </table>
        <p class="muted">
          指标取自服务端存的同一份结果，页面没有重算任何东西；某条实验在某个指标上没有值
          （比如那条跑挂了），会显示「—」。
        </p>
      </div>

      <p class="next-line">
        下一步：{{ experimentNextStep.text }}
        <RouterLink v-if="experimentNextStep.to" :to="experimentNextStep.to">
          {{ experimentNextStep.linkText }}
        </RouterLink>
      </p>
    </div>
  </div>
</template>
