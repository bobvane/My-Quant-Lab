<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
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
  type AIResearchSourceMeta,
  type AIResearchViolation,
  type AIResearchWarning,
} from '@/api'
import { isAdvanced } from '@/mode'

// 「AI 研究实验室」（§17）：把一条最小但诚实的链路走完——
// 研究输入 → AI 理解 → 策略假设 → 策略草案 → 能力检查。
//
// 这一页刻意不做三件事，也不假装做了：
// ① 草案永远不可执行（`executable === false`），这一版没有编译、没有回测，
//    所以页面上不会出现收益、回撤、夏普这类结果数字（模型写了会被标记为未验证）；
// ② AI 自己补的假设必须被看见：`origin === 'ASSUMED'` 的规则在普通模式和高级
//    模式下都会写明「AI 提出的假设，不是你的原话」；
// ③ 能力结论用服务端算出来的那一份（`capability_status` / `capability_report`），
//    缺什么就说什么，绝不把「部分支持」说成「可以运行」。
//
// 普通模式（默认）只讲人话：AI 理解到了什么、规则说了什么、还缺什么、能力结论是什么。
// 来源徽标、置信度、派生关系、能力 token、违规代码、运行号与尝试次数只在高级模式出现。

const QUESTION_MIN = 3
const QUESTION_MAX = 4000
const SOURCE_MAX = 40000
const MAX_SOURCES = 8

const question = ref('')
const sourceLabel = ref('')
const sourceText = ref('')

const run = ref<AIResearchRun | null>(null)
const runs = ref<AIResearchRunSummary[]>([])
const busy = ref(false)
const formalizing = ref(false)
const confirming = ref(false)
const confirmationNote = ref('')
const loadingRuns = ref(false)
const error = ref('')
const notice = ref('')
const notConfigured = ref(false)
const configDetail = ref('')

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

const inputProblem = computed(() => questionProblem.value || sourceProblem.value)
const canSubmit = computed(() => !inputProblem.value && !busy.value)
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
  running: 'AI 正在分析',
  completed: '已完成',
  rejected: '没有通过校验',
  failed: '没有跑完',
}

function statusLabel(status: string | null | undefined): string {
  if (!status) return '—'
  return STATUS_LABELS[status] ?? status
}

const statusSentence = computed(() => {
  const status = run.value?.status
  if (status === 'completed') return 'AI 已经读完材料，下面是它给出的理解、假设、草案和能力结论。'
  if (status === 'rejected') return 'AI 的回答没有通过校验。系统没有替它修改，也没有把这份回答当成结论存下来。'
  if (status === 'failed') return '这次研究没有跑完，下面是后端记下来的原因。'
  if (status === 'running' || status === 'pending') return '这次研究还在进行中，过一会儿点「重新读一次结果」再看。'
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

function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 16)
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

const nextStep = computed(() => {
  const current = run.value
  if (notConfigured.value) {
    return { text: '到「系统管理」把 AI 提供方配好，再回到这一页重试。', to: '/settings', linkText: '去「系统管理」' }
  }
  if (!current) {
    return { text: '写好研究问题和材料，点「开始研究」；结果会留在这一页，也会进「最近的研究」。', to: '', linkText: '' }
  }
  if (current.status === 'failed') {
    return { text: '先看上面的原因：如果是 AI 没配置或额度用完，去「系统管理」处理；否则改一下研究输入再试一次。', to: '/settings', linkText: '检查 AI 配置' }
  }
  if (current.status === 'rejected') {
    return { text: '系统不会自动修改 AI 的回答。把问题问得更具体、材料补得更完整，然后重新开始一次研究。', to: '', linkText: '' }
  }
  if (current.draft) {
    return { text: '读一遍上面的草案，判断这件事值不值得做。要往下走，就得先把缺的能力补上，或者按 AI 的替代思路把策略缩小。', to: '', linkText: '' }
  }
  return { text: '这次只拿到了假设、还没有草案：点「重新生成策略草案」让 AI 再写一次。', to: '', linkText: '' }
})

// --------------------------------------------------------------------------- //
// 请求
// --------------------------------------------------------------------------- //
function resetNotices() {
  error.value = ''
  notice.value = ''
  notConfigured.value = false
  configDetail.value = ''
}

function handleFailure(e: unknown) {
  // 503 = 没有可用的 AI 提供方：把后端的原文照实显示，并指向设置页。
  if (e instanceof ApiError && e.status === 503) {
    notConfigured.value = true
    configDetail.value = e.message
    return
  }
  error.value = (e as Error).message
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

async function submit() {
  if (inputProblem.value || busy.value) return
  resetNotices()
  busy.value = true
  run.value = null
  const source: { text: string; kind: string; source_ref: string; label?: string } = {
    text: sourceText.value,
    kind: 'user_input',
    source_ref: 'source_1',
  }
  const label = sourceLabel.value.trim()
  if (label) source.label = label
  try {
    run.value = await api.aiResearchStart({ question: question.value.trim(), sources: [source] })
    await loadRuns()
  } catch (e) {
    handleFailure(e)
  } finally {
    busy.value = false
  }
}

async function openRun(runId: number) {
  resetNotices()
  try {
    run.value = await api.aiResearchRun(runId)
  } catch (e) {
    handleFailure(e)
  }
}

async function refreshRun() {
  const current = run.value
  if (!current) return
  await openRun(current.run_id)
}

async function formalize() {
  const current = run.value
  if (!current || formalizing.value) return
  resetNotices()
  formalizing.value = true
  try {
    await api.aiStrategyFormalize({ run_id: current.run_id })
    run.value = await api.aiResearchRun(current.run_id)
    notice.value = '草案已经重新生成，下面是新的这一份。'
    await loadRuns()
  } catch (e) {
    // 被拒绝时后端回 422 + 结构化的违规清单，而不是一句人话；违规同时会落到
    // 这次运行上，所以重新读一次运行就能拿到真正的拒绝原因。
    if (e instanceof ApiError && e.status === 422) {
      try {
        run.value = await api.aiResearchRun(current.run_id)
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
    run.value = await api.aiResearchRun(current.run_id)
    confirmationNote.value = ''
    notice.value = `这次人工决定已经记下来了：${decisionLabel(decision)}。草案本身没有被改动，也没有生成可执行的策略。`
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

onMounted(loadRuns)
</script>

<template>
  <div>
    <h1 class="page-title">AI 研究实验室</h1>
    <p class="page-sub">
      把你手上的一段材料和一个问题交给 AI：它先说出自己理解到了什么，再给出一份策略草案，
      最后系统检查这份草案需要的能力有没有。这一页只做这一条链路——不执行、不回测、不下单。
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
          只是把文字材料交给 AI 读，看它理解出什么。
        </li>
        <li><b>你能做什么：</b>写一个研究问题、贴一段材料，点「开始研究」。一次只放一段材料。</li>
        <li>
          <b>结果怎么看：</b>先看「AI 理解」对不对得上你的意思，再看规则——尤其是它自己补的假设；
          最后看能力结论：这份草案系统能不能实现。
        </li>
        <li>
          <b>下一步：</b>读一遍草案决定值不值得做；缺能力就去「系统管理」看配置；
          问题问得不好就改输入重来。
        </li>
      </ul>
    </div>

    <!-- ① 研究输入 -->
    <div class="card" style="margin-top: 14px">
      <h3>① 研究输入</h3>
      <p class="muted">
        一个问句 + 一段材料就够了。材料可以是一段研报摘录、一条公告、或者你自己写下的策略想法。
        这一版不会去抓网页或解析 PDF，只有你贴进来的文字会被读到。
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

      <label class="muted" for="lab-source-label">材料名称（可选）</label>
      <input
        id="lab-source-label"
        v-model="sourceLabel"
        type="text"
        placeholder="例如：某券商 2024 年均线策略摘要"
      />

      <label class="muted" for="lab-source-text" style="margin-top: 10px">材料正文</label>
      <textarea
        id="lab-source-text"
        v-model="sourceText"
        placeholder="把材料原文粘贴到这里。"
      ></textarea>
      <p class="muted">
        {{ sourceText.length }} / {{ SOURCE_MAX }} 字 · 后端最多接受 {{ MAX_SOURCES }} 段材料，这一页先支持一段。
      </p>

      <p v-if="inputProblem" class="notice warn">⚠️ {{ inputProblem }}</p>

      <button :disabled="!canSubmit" style="margin-top: 10px" @click="submit">
        {{ busy ? 'AI 正在读…' : '开始研究' }}
      </button>
      <p v-if="busy" class="muted" style="margin-top: 8px">
        AI 要读完材料再回答，通常要等十几秒到一分钟；这一步不能中途取消，请勿重复点击。
      </p>
      <p v-else-if="blockedReason" class="muted" style="margin-top: 8px">{{ blockedReason }}</p>
    </div>

    <!-- ② AI 理解 -->
    <div v-if="run" class="card" style="margin-top: 14px">
      <div class="row" style="justify-content: space-between; align-items: flex-start">
        <h3 style="margin: 0">② AI 理解到了什么</h3>
        <button class="ghost" @click="refreshRun">重新读一次结果</button>
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
        <template v-if="isAdvanced">
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
        </template>
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
            · 由 {{ confirmation.decided_by }} 于 {{ formatTime(confirmation.decided_at) }} 记录
            <template v-if="isAdvanced">
              · 审计号 {{ confirmation.audit_id }} · 人工裁决
              {{ confirmation.is_human_decision ? '是' : '否' }}
            </template>
          </template>
        </p>
        <p v-if="confirmation?.note" class="muted">备注：{{ confirmation.note }}</p>
        <p class="muted">
          这一步只是把你自己的判断记下来：不改动这份草案，不调用 AI，也不会生成可执行的策略。
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
          {{ run?.draft?.capability_status ?? '—' }} · 模型 {{ run?.draft?.model ?? '—' }}
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
        所以既不能说它可行，也不能说它不行。草案本身也不能直接运行，还需要先补齐能力、再编译成正式策略。
      </p>

      <template v-if="isAdvanced">
        <h4>逐项明细（服务端核对，不采信模型的声称）</h4>
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
      </template>
    </div>

    <p class="next-line">下一步：{{ nextStep.text }}<RouterLink v-if="nextStep.to" :to="nextStep.to">{{ nextStep.linkText }}</RouterLink></p>

    <!-- 最近的研究 -->
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
            <td class="muted">{{ formatTime(item.created_at) }}</td>
            <td>{{ item.question }}</td>
            <td>{{ statusLabel(item.status) }}</td>
            <td>{{ verdictLabelOf(item.capability_status) }}</td>
            <td v-if="isAdvanced" class="mono">#{{ item.run_id }}</td>
            <td>
              <button class="ghost" @click="openRun(item.run_id)">打开</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="runs.length" class="muted" style="margin-top: 8px">
        打开一条记录只会读回当时的结果，不会重新调用 AI，也不会产生任何费用。
      </p>
    </div>
  </div>
</template>
