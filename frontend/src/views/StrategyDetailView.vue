<script setup lang="ts">
// The ten-part strategy detail page (docs/13_UI_UX.md §3, ADR-114 + ADR-207). Everything a
// decision needs about one strategy used to be scattered over /market, /backtest,
// /paper and /signals; this page puts the parts in one place — and where a
// reading does not exist it says so instead of leaving a blank that reads as zero.
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import {
  ApiError,
  api,
  type AIStatus,
  type BacktestSummary,
  type ExperimentSummaryOut,
  type ExplainResult,
  type PaperAccount,
  type Strategy,
  type StrategyLifecycle,
  type StrategyVersion,
} from '@/api'
import StatCard from '@/components/StatCard.vue'
import { formatDateTime, formatNumber, formatPercent, toneOf } from '@/format'
// 数据集版本号、接口路径这类工程读数只在高级模式出现（ADR-126）。
import { isAdvanced } from '@/mode'
import {
  accountStatusLabel,
  experimentKindLabel,
  experimentStatusLabel,
  stageLabel,
  validationLabel,
} from '@/wording'

const route = useRoute()
const strategyId = Number(route.params.strategyId)

const strategy = ref<Strategy | null>(null)
const lifecycle = ref<StrategyLifecycle | null>(null)
const lineage = ref<Record<string, any> | null>(null)
const versions = ref<StrategyVersion[]>([])
const runs = ref<BacktestSummary[]>([])
const accounts = ref<PaperAccount[]>([])
const trades = ref<Array<Record<string, any>>>([])
const hashes = ref<
  Record<number, { intact: boolean; stored_hash: string; recomputed_hash: string }>
>({})
const ai = ref<AIStatus | null>(null)
const preview = ref<Record<string, any> | null>(null)
const evidence = ref<Record<string, any> | null>(null)
const explanation = ref<ExplainResult | null>(null)
const symbol = ref('')
const loading = ref(true)
const error = ref('')
const info = ref('')
const busy = ref('')

const currentVersion = computed(
  () => versions.value.find((version) => version.is_current) ?? versions.value[0] ?? null,
)
const latestRun = computed(() => runs.value[0] ?? null)

/**
 * 「去回测」的落点：带上当前版本，让那一页一打开就选中它，而不是回到默认版本（ADR-200）。
 * 还没有版本时退回不带参数的那一页 —— 这一节要说的仍然是「先把这一版跑一次」。
 */
const backtestLink = computed(() =>
  currentVersion.value
    ? { path: '/backtest', query: { strategy_version_id: String(currentVersion.value.id) } }
    : '/backtest',
)

/** 某一次已存回测的落点：`?run_id=` 让「回测」页直接打开它（ADR-200）。 */
function runLink(id: number) {
  return { path: '/backtest', query: { run_id: String(id) } }
}

/**
 * 这一版发过的信号：`/signals?strategy_version_id=<id>`（ADR-201）。
 * 没有版本时退回不带参数的那一页 —— 那里看的是全部信号，仍然是说得通的状态。
 */
const signalsLink = computed(() =>
  currentVersion.value
    ? { path: '/signals', query: { strategy_version_id: String(currentVersion.value.id) } }
    : '/signals',
)

/**
 * 这一版跑过的实验（ADR-207）：`GET /experiments?strategy_version_id=<id>` 在服务端筛，
 * 不是把「实验」页取回来的那一页本地筛一遍 —— 本地筛会把更早的实验漏掉。
 * 这一页只读最近几条当入口，看全部还是到「实验」页。
 */
const EXPERIMENT_PREVIEW = 5
const experiments = ref<ExperimentSummaryOut[]>([])
const experimentsError = ref('')

async function loadExperiments() {
  const version = currentVersion.value
  if (!version) {
    experiments.value = []
    experimentsError.value = ''
    return
  }
  try {
    const response = await api.experiments(EXPERIMENT_PREVIEW, version.id)
    experiments.value = response.experiments ?? []
    experimentsError.value = ''
  } catch (e) {
    experiments.value = []
    experimentsError.value = `这一版跑过的实验没能载入：${(e as Error).message}`
  }
}

/** 某一版实验的落点：`?strategy_version_id=` 让「实验」页只列这一版（ADR-207）。 */
function experimentsLink(versionId: number) {
  return { path: '/experiments', query: { strategy_version_id: String(versionId) } }
}

const dsl = computed<Record<string, any> | null>(
  () => (currentVersion.value?.dsl as Record<string, any> | undefined) ?? null,
)
const indicators = computed<Array<Record<string, any>>>(
  () => (dsl.value?.indicators as Array<Record<string, any>> | undefined) ?? [],
)
const riskBlock = computed<Record<string, any>>(
  () => (dsl.value?.risk as Record<string, any> | undefined) ?? {},
)
const executionBlock = computed<Record<string, any>>(
  () => (dsl.value?.execution as Record<string, any> | undefined) ?? {},
)
const entryLong = computed(() => describeGroup(dsl.value?.entry?.long))
const entryShort = computed(() => describeGroup(dsl.value?.entry?.short))
const exitLong = computed(() => describeGroup(dsl.value?.exit?.long))
const exitShort = computed(() => describeGroup(dsl.value?.exit?.short))

const oosSummary = computed<Record<string, any>>(
  () => (lifecycle.value?.evidence?.oos_latest_summary as Record<string, any> | undefined) ?? {},
)
const oosRuns = computed(() => Number(lifecycle.value?.evidence?.oos_runs ?? 0))
const oosWindows = computed(() => Number(lifecycle.value?.evidence?.oos_windows ?? 0))
const gates = computed<Array<[string, boolean]>>(() => Object.entries(lifecycle.value?.gates ?? {}))

const myAccounts = computed(() => accounts.value.filter((account) => account.strategy_id === strategyId))
const myTrades = computed(() => {
  const ids = new Set(myAccounts.value.map((account) => account.id))
  return trades.value.filter((trade) => ids.has(Number(trade.account_id)))
})

/** `all`/`any` groups of the DSL, rendered in the strategy's own words. */
function describeGroup(group: unknown): string[] {
  if (!group || typeof group !== 'object') return []
  return Object.entries(group as Record<string, unknown>).map(
    ([side, node]) => `${side}：${conditionText(node)}`,
  )
}

function conditionText(node: unknown): string {
  if (!node || typeof node !== 'object') return String(node)
  const record = node as Record<string, any>
  if (Array.isArray(record.all)) return `全部满足（${record.all.map(conditionText).join('；')}）`
  if (Array.isArray(record.any)) return `任一满足（${record.any.map(conditionText).join('；')}）`
  if (record.left !== undefined) {
    const base = `${record.left} ${record.op ?? '?'} ${record.right ?? record.threshold ?? '?'}`
    return record.threshold !== undefined && record.right !== undefined
      ? `${base} → ${record.threshold}`
      : base
  }
  return JSON.stringify(node)
}

function level(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') return formatNumber(value, 4)
  return String(value)
}

/** 把服务端那句英文换成这一页自己的说法：能认的状态说出人话，认不出的照实转述。 */
function evidenceFailure(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 404) return '服务端已经没有这一版了。'
    if (e.status === 422) return '这个标的（或时间范围）服务端不认。'
    if (e.status === 503) return '这台部署现在取不到证据。'
  }
  return (e as Error).message
}

// 发出去的标的永远是去掉首尾空白的那一个：粘进来的「  DEMO-AAPL  」不是另一个标的（ADR-208）。
const requestedSymbol = computed(() => symbol.value.trim())

// 没有标的时这两处（当前信号预览、五层证据）都问不出东西，理由也是同一句（ADR-208）。
const SYMBOL_NEEDED =
  '策略自己不点名任何标的，所以先在「当前信号」那一格填一个（例如 DEMO-AAPL）再载入；' +
  '这一版跑过回测之后，那一格会自带最近一次回测的标的。'

async function loadEvidence() {
  if (!currentVersion.value) return
  // 证据是「这一版在这个标的上」的证据：没有标的就没有可问的对象 ——
  // 不去问服务端（问了只有 422），也不把服务端那句英文印给用户。
  if (!requestedSymbol.value) {
    evidence.value = null
    info.value = `五层证据要按标的算：${SYMBOL_NEEDED}`
    return
  }
  try {
    evidence.value = await api.strategyEvidence(currentVersion.value.id, requestedSymbol.value)
  } catch (e) {
    evidence.value = null
    info.value = `五层证据没能载入：${evidenceFailure(e)}`
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    strategy.value = await api.strategy(strategyId)
    versions.value = await api.strategyVersions(strategyId)
    lifecycle.value = await api.lifecycle(strategyId)
    lineage.value = await api.strategyLineage(strategyId)
    if (currentVersion.value) {
      runs.value = await api.backtests(currentVersion.value.id)
    }
    symbol.value = runs.value.find((run) => run.symbol)?.symbol ?? ''
    await loadExperiments()
    accounts.value = await api.paperAccounts()
    trades.value = await api.paperTrades()
    ai.value = await api.aiStatus()
    await loadEvidence()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

async function verify(version: StrategyVersion) {
  error.value = ''
  busy.value = `verify-${version.id}`
  try {
    const body = await api.verifyStrategyVersion(version.id)
    hashes.value = { ...hashes.value, [version.id]: body }
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = ''
  }
}

async function loadPreview() {
  if (!currentVersion.value) return
  error.value = ''
  info.value = ''
  // 预览要按标的算：没有标的就不发这次请求，只说缺什么（ADR-208）。
  if (!requestedSymbol.value) {
    info.value = `当前信号要按标的算：${SYMBOL_NEEDED}`
    return
  }
  busy.value = 'preview'
  try {
    preview.value = await api.signalPreview(currentVersion.value.id, requestedSymbol.value)
    await loadEvidence()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = ''
  }
}

async function explainSignalNow() {
  if (!currentVersion.value) return
  error.value = ''
  busy.value = 'explain'
  try {
    explanation.value = await api.explainSignalPreview(
      currentVersion.value.id,
      requestedSymbol.value || undefined,
    )
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = ''
  }
}

async function explainLatestRun() {
  if (!latestRun.value) return
  error.value = ''
  busy.value = 'explain-run'
  try {
    explanation.value = await api.explainBacktest(latestRun.value.id)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <section>
    <p>
      <RouterLink to="/strategies">← 返回我的策略</RouterLink>
    </p>
    <h1>{{ strategy?.name ?? `策略 #${strategyId}` }}</h1>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="info" class="muted">{{ info }}</p>
    <p v-if="loading" class="muted">载入中…</p>

    <template v-if="strategy">
      <section class="card">
        <h3>概览（Overview）</h3>
        <p class="muted">
          {{ strategy.description ?? '这个策略没有写描述。' }}
        </p>
        <table>
          <tbody>
            <tr>
              <td>ID</td>
              <td>{{ strategy.id }}</td>
            </tr>
            <tr>
              <td>Slug</td>
              <td>{{ strategy.slug }}</td>
            </tr>
            <tr>
              <td>来源类型</td>
              <td>{{ strategy.source_type }}</td>
            </tr>
            <tr>
              <td>状态 / 生命周期</td>
              <td>{{ strategy.status }} · {{ stageLabel(strategy.lifecycle) }}</td>
            </tr>
            <tr>
              <td>版本数</td>
              <td>{{ strategy.version_count }}</td>
            </tr>
            <tr>
              <td>创建时间</td>
              <td>{{ formatDateTime(strategy.created_at) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="lifecycle">
          生命周期当前阶段：<strong>{{ lifecycle.current }}</strong>（{{ lifecycle.current_group }}）。
          <template v-if="lifecycle.suggested_next">
            证据支持的下一步是 {{ lifecycle.suggested_next }}。
          </template>
          <template v-else>没有证据支持任何下一步，所以系统不建议推进。</template>
          <template v-if="lifecycle.blocked_reason">被挡住的原因：{{ lifecycle.blocked_reason }}</template>
          <template v-if="lifecycle.degraded">
            这个策略已被标为退步：{{ lifecycle.degrade_reason ?? '没有写明原因' }}。
          </template>
          <template v-if="lifecycle.manual_only_stages.length">
            只能人工推进的阶段：{{ lifecycle.manual_only_stages.join('、') }}。
          </template>
        </p>
        <table v-if="gates.length">
          <thead>
            <tr>
              <th>门</th>
              <th>是否通过</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="[gate, passed] in gates" :key="gate">
              <td>{{ gate }}</td>
              <td>{{ passed ? '通过' : '未通过' }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="card">
        <h3>血统（Provenance）</h3>
        <p class="muted">
          来源、许可与作者是抄来的代码能不能被相信的前提：这里写的是系统真的存下来的字段，缺的
          写「未记录」，不猜。
        </p>
        <table>
          <tbody>
            <tr>
              <td>来源类型</td>
              <td>{{ strategy.source_type }}</td>
            </tr>
            <tr>
              <td>来源地址</td>
              <td>{{ strategy.source_url ?? '未记录' }}</td>
            </tr>
            <tr>
              <td>许可</td>
              <td>{{ strategy.license ?? '未记录' }}</td>
            </tr>
            <tr>
              <td>作者</td>
              <td>{{ strategy.author ?? '未记录' }}</td>
            </tr>
          </tbody>
        </table>
        <table v-if="lineage">
          <thead>
            <tr>
              <th>版本</th>
              <th>提交</th>
              <th>来源地址</th>
              <th>提示词版本</th>
              <th>不可变哈希</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in lineage.versions ?? []" :key="row.id">
              <td>{{ row.version }}</td>
              <td>{{ row.source_commit ?? '未记录' }}</td>
              <td>{{ row.source_url ?? '未记录' }}</td>
              <td>{{ row.prompt_version ?? '未记录' }}</td>
              <td class="mono">{{ row.immutable_hash }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">还没能读到血统记录。</p>
      </section>

      <section class="card">
        <h3>版本历史（Version History）</h3>
        <p class="muted">
          版本是不可变的：每条都带着它的哈希。「校验」重新计算这份文本的哈希并与存档比对，
          不一致就说明这份策略已经不是当初被存档的那一份。
        </p>
        <table>
          <thead>
            <tr>
              <th>版本</th>
              <th>Schema</th>
              <th>校验状态</th>
              <th>当前</th>
              <th>创建时间</th>
              <th>哈希</th>
              <th>实验</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="version in versions" :key="version.id">
              <td>{{ version.version }}</td>
              <td>{{ version.schema_version }}</td>
              <td>{{ validationLabel(version.validation_status) }}</td>
              <td>{{ version.is_current ? '是' : '否' }}</td>
              <td>{{ formatDateTime(version.created_at) }}</td>
              <td>
                <button :disabled="busy === `verify-${version.id}`" @click="verify(version)">
                  校验
                </button>
                <span v-if="hashes[version.id]" class="muted">
                  {{ hashes[version.id].intact ? '哈希一致' : '哈希不一致（文本已变）' }}
                </span>
              </td>
              <td>
                <RouterLink :to="experimentsLink(version.id)">这一版跑过的实验</RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="!versions.length" class="muted">这个策略还没有任何版本。</p>
      </section>

      <section class="card">
        <h3>规则（Rules）</h3>
        <p v-if="!currentVersion" class="muted">没有版本，就没有规则可读。</p>
        <template v-else>
          <p class="muted">
            下面读的是当前版本（{{ currentVersion.version }}）的声明式 DSL —— 策略自己说的话，
            不是回测的结论。
          </p>
          <h4>指标</h4>
          <table v-if="indicators.length">
            <thead>
              <tr>
                <th>ID</th>
                <th>类型</th>
                <th>周期</th>
                <th>输入</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="indicator in indicators" :key="String(indicator.id)" class="mono">
                <td>{{ indicator.id }}</td>
                <td>{{ indicator.type }}</td>
                <td>{{ indicator.period ?? '—' }}</td>
                <td>{{ indicator.input ?? '—' }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="muted">这个版本没有声明指标。</p>
          <h4>入场</h4>
          <ul>
            <li v-for="line in entryLong" :key="`el-${line}`">多头：{{ line.replace(/^long：/, '') }}</li>
            <li v-for="line in entryShort" :key="`es-${line}`">
              空头：{{ line.replace(/^short：/, '') }}
            </li>
          </ul>
          <p v-if="!entryLong.length && !entryShort.length" class="muted">没有入场条件。</p>
          <h4>出场</h4>
          <ul>
            <li v-for="line in exitLong" :key="`xl-${line}`">多头：{{ line.replace(/^long：/, '') }}</li>
            <li v-for="line in exitShort" :key="`xs-${line}`">
              空头：{{ line.replace(/^short：/, '') }}
            </li>
          </ul>
          <p v-if="!exitLong.length && !exitShort.length" class="muted">没有出场条件。</p>
          <h4>风险与执行</h4>
          <table>
            <tbody>
              <tr v-for="(value, key) in riskBlock" :key="`risk-${key}`">
                <td>risk.{{ key }}</td>
                <td>{{ level(value) }}</td>
              </tr>
              <tr v-for="(value, key) in executionBlock" :key="`exec-${key}`">
                <td>execution.{{ key }}</td>
                <td>{{ level(value) }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </section>

      <section class="card">
        <h3>回测（Backtest）</h3>
        <template v-if="latestRun">
          <p class="muted">
            当前版本最近一次回测（运行
            <RouterLink :to="runLink(latestRun.id)">#{{ latestRun.id }}</RouterLink>，{{ latestRun.status
            }}<span v-if="isAdvanced">，数据集 {{ latestRun.dataset_version_id }}</span
            >）。点编号到「回测」页看完整的成交明细与参数。
          </p>
          <div class="grid">
            <StatCard label="总收益率" :value="formatPercent(latestRun.total_return)" :tone="toneOf(latestRun.total_return)" />
            <StatCard label="最大回撤" :value="formatPercent(latestRun.max_drawdown)" tone="plain" />
            <StatCard label="夏普比率" :value="formatNumber(latestRun.sharpe)" :tone="toneOf(latestRun.sharpe)" />
            <StatCard label="胜率" :value="formatPercent(latestRun.win_rate)" tone="plain" />
            <StatCard label="期末权益" :value="formatNumber(latestRun.final_equity)" tone="plain" />
          </div>
        </template>
        <p v-else class="muted">
          当前版本还没有跑过回测。不是零，是还没有数据 —— 去
          <RouterLink :to="backtestLink">回测</RouterLink> 跑一次，再回来看这一节。
        </p>
        <table v-if="runs.length > 1">
          <thead>
            <tr>
              <th>运行</th>
              <th>状态</th>
              <th>标的</th>
              <th>周期</th>
              <th>总收益</th>
              <th>最大回撤</th>
              <th>交易数</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="run in runs" :key="run.id">
              <td>
                <RouterLink :to="runLink(run.id)">#{{ run.id }}</RouterLink>
              </td>
              <td>{{ run.status }}</td>
              <td>{{ run.symbol ?? '—' }}</td>
              <td>{{ run.timeframe ?? '—' }}</td>
              <td>{{ formatPercent(run.total_return) }}</td>
              <td>{{ formatPercent(run.max_drawdown) }}</td>
              <td>{{ run.number_of_trades ?? '—' }}</td>
              <td>{{ formatDateTime(run.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="card">
        <h3>样本外与滚动验证（OOS / Walk-forward）</h3>
        <p class="muted">
          这些结果<strong>不落库</strong>：样本外与滚动验证由「回测」页按需计算，系统留下的只有审计事件，
          所以下面是从生命周期证据里读到的<strong>发生过几次</strong>，不是一份完整评估。要看每一次的读数，
          去「回测」重跑。
          <span v-if="isAdvanced">
            （接口：<code>POST /research/oos</code>、<code>POST /research/walk-forward</code>）
          </span>
        </p>
        <table>
          <tbody>
            <tr>
              <td>已记录的样本外运行</td>
              <td>{{ oosRuns }}</td>
            </tr>
            <tr>
              <td>已记录的滚动窗口</td>
              <td>{{ oosWindows }}</td>
            </tr>
            <tr>
              <td>最近一次样本外摘要</td>
              <td>{{ Object.keys(oosSummary).length ? JSON.stringify(oosSummary) : '未记录' }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="card">
        <h3>模拟验证（Paper Trading）</h3>
        <p class="muted">
          归因按<strong>账户</strong>：模拟验证的交易只带账户与它当时的策略版本名，所以这里列的是「绑定到这个
          策略的账户」，而不是声称这些盈亏都属于这个策略。逐笔流水会写出每一笔成交自己的来历（产生它的
          成交单与信号编号，ADR-204）——那是那一行的出处，不是把这个账户的盈亏算到这个策略头上。
        </p>
        <p v-if="!myAccounts.length" class="muted">
          还没有绑定到这个策略的模拟账户。在 <RouterLink to="/paper">模拟验证</RouterLink>
          新建账户时选上它，这条线才会接起来。
        </p>
        <template v-else>
          <table>
            <thead>
              <tr>
                <th>账户</th>
                <th>现金</th>
                <th>净入金</th>
                <th>状态</th>
                <th>重置次数</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="account in myAccounts" :key="account.id">
                <td>{{ account.name }}（#{{ account.id }}）</td>
                <td>{{ formatNumber(account.cash) }}</td>
                <td>{{ formatNumber(account.net_deposits) }}</td>
                <td>{{ accountStatusLabel(account.status) }}</td>
                <td>{{ account.reset_count }}</td>
              </tr>
            </tbody>
          </table>
          <table v-if="myTrades.length">
            <thead>
              <tr>
                <th>账户</th>
                <th>方向</th>
                <th>策略版本</th>
                <th>入场</th>
                <th>出场</th>
                <th>盈亏</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="trade in myTrades" :key="trade.id">
                <td>#{{ trade.account_id }}</td>
                <td>{{ trade.direction }}</td>
                <td>{{ trade.strategy_version ?? '未记录' }}</td>
                <td>{{ formatDateTime(trade.entry_time) }}</td>
                <td>{{ formatDateTime(trade.exit_time) }}</td>
                <td>{{ formatNumber(trade.pnl) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="muted">这些账户还没有平仓的交易。</p>
        </template>
      </section>

      <section class="card">
        <h3>当前信号（Current Signals）</h3>
        <p class="muted">
          这一版<strong>过去</strong>发过什么信号、后来怎么走：
          <RouterLink :to="signalsLink">到「信号」页看这一版</RouterLink>
          —— 列表与结果统计都收窄到这一版，不是全部信号里自己挑。
        </p>
        <p class="muted">
          预览是「此刻这根已收盘 K 线会给出什么」，需要代码：策略自己不点名任何标的，
          所以下面这一格是必填的。
        </p>
        <label>
          标的
          <input v-model="symbol" placeholder="例如 DEMO-AAPL" />
        </label>
        <button :disabled="busy === 'preview' || !requestedSymbol" @click="loadPreview">载入当前信号</button>
        <pre v-if="preview" class="code-block">{{ JSON.stringify(preview, null, 2) }}</pre>
        <p v-else class="muted">还没有载入预览。</p>
        <h4>五层确定性证据</h4>
        <p v-if="evidence" class="muted">
          证据是算出来的，不是模型说出来的：规则匹配、经验统计（最近几次回测的摘要）、模拟盘
          统计、组合上下文、信号意图。
        </p>
        <pre v-if="evidence" class="code-block">{{ JSON.stringify(evidence, null, 2) }}</pre>
        <p v-else class="muted">还没有证据快照：先选一个标的载入。</p>
      </section>

      <section class="card">
        <h3>实验（Experiments）</h3>
        <p v-if="!currentVersion" class="muted">没有版本，就没有实验可看。</p>
        <template v-else>
          <p class="muted">
            一条实验 = 一条留在服务端的记录：它冻住了当时的这一版策略、参数、标的与时间范围，
            以及跑出来的读数。下面读的是<strong>当前版本（{{ currentVersion.version }}）</strong>跑过的
            最近几条；换一版要按版本看 —— 版本历史那一列每一版都有自己的入口。
            <span v-if="isAdvanced">
              （服务端过滤：<span class="mono">GET /experiments?strategy_version_id=</span>，
              不是把「实验」页取回来的那一页本地筛一遍。）
            </span>
          </p>
          <p v-if="experimentsError" class="error">{{ experimentsError }}</p>
          <p v-else-if="!experiments.length" class="muted">
            这一版还没有跑过实验。到「实验」页新建一条，或者把一条跑完的回测收养成实验。
          </p>
          <template v-else>
            <table>
              <thead>
                <tr>
                  <th>实验</th>
                  <th>怎么跑的</th>
                  <th>状态</th>
                  <th>创建时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in experiments" :key="row.id">
                  <td>#{{ row.id }} {{ row.name }}</td>
                  <td>{{ experimentKindLabel(row.kind, isAdvanced) }}</td>
                  <td>{{ experimentStatusLabel(row.status, isAdvanced) }}</td>
                  <td>{{ formatDateTime(row.created_at) }}</td>
                </tr>
              </tbody>
            </table>
            <p class="muted">
              这里只列最近 {{ EXPERIMENT_PREVIEW }} 条，不是这一版的全部；每条实验的完整读数在
              「实验」页上。
            </p>
          </template>
          <p>
            <RouterLink :to="experimentsLink(currentVersion.id)">
              到「实验」页看这一版跑过的实验
            </RouterLink>
          </p>
        </template>
      </section>

      <section class="card">
        <h3>AI 解释（AI Explanation）</h3>
        <p class="muted">
          AI 只解释已有数字，不参与任何计算：它读的是上面的回测、信号与证据，写不成一条交易。
        </p>
        <p v-if="ai && !ai.configured" class="muted">
          还没有配置 AI：{{ ai.note }}。上面的十段在没有任何 AI 的情况下也全部可用。
        </p>
        <template v-if="ai && ai.configured">
          <p class="muted">
            提供商 {{ ai.provider_name ?? '—' }} · 模型 {{ ai.model ?? '—' }} · 今日已花
            {{ formatNumber(ai.spent_today_usd, 4) }} USD / 预算
            {{ ai.daily_budget_usd === null ? '未设' : formatNumber(ai.daily_budget_usd) }}。
          </p>
          <button :disabled="busy === 'explain' || !requestedSymbol" @click="explainSignalNow">
            解释当前信号
          </button>
          <button :disabled="busy === 'explain-run' || !latestRun" @click="explainLatestRun">
            解释最近一次回测
          </button>
        </template>
        <template v-if="explanation">
          <p>{{ explanation.explanation.summary }}</p>
          <p class="muted">
            大白话：{{ explanation.explanation.plain_language }}（{{ explanation.cached ? '缓存命中' : '本次生成' }}，
            模型 {{ explanation.model ?? '—' }}，估算成本
            {{ formatNumber(explanation.cost_usd_estimated, 4) }} USD）
          </p>
          <h4>为什么</h4>
          <ul>
            <li v-for="line in explanation.explanation.why ?? []" :key="`why-${line}`">{{ line }}</li>
          </ul>
          <h4>风险</h4>
          <ul>
            <li v-for="line in explanation.explanation.risks ?? []" :key="`risk-${line}`">{{ line }}</li>
          </ul>
          <h4>什么会让它失效</h4>
          <ul>
            <li v-for="line in explanation.explanation.what_could_invalidate ?? []" :key="`inv-${line}`">
              {{ line }}
            </li>
          </ul>
          <p v-if="!(explanation.explanation.why ?? []).length" class="muted">
            这次解释没有分点给出「为什么」。
          </p>
        </template>
        <p v-else-if="ai?.configured" class="muted">还没有解释过：按钮在请求，结果不会自己出现。</p>
      </section>
    </template>
  </section>
</template>
