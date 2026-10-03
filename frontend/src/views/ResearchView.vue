<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, type Asset, type Strategy, type StrategyVersion } from '@/api'
import { isAdvanced } from '@/mode'
import { qualityLabel, timeframeLabel, validationLabel } from '@/wording'

// 「研究策略」这一页就是把「从想法到结论」的第一步讲清楚（评审 §4、§7、§24；ADR-131、ADR-132）。
//
// 四步：① 选择标的 ② 选择策略 ③ 设置少量参数 ④ 开始研究。
// 这一页不算任何量化事实：数据范围、K 线根数、质量、版本与校验状态都是后端已经存下来的读数；
// 真正的回测在「回测」页由引擎跑，这里只负责把选择带过去（`/backtest?...&run=1`）。
const router = useRouter()

const assets = ref<Asset[]>([])
const seriesList = ref<Array<Record<string, unknown>>>([])
const strategies = ref<Strategy[]>([])
const versions = ref<StrategyVersion[]>([])
const error = ref('')

type SymbolOption = {
  symbol: string
  seriesId: number
  timeframe: string
  start: string
  end: string
  quality: string
  archived: boolean
}

const symbolOptions = computed<SymbolOption[]>(() =>
  seriesList.value
    .map((s) => ({
      symbol: assetSymbol(Number(s.asset_id)),
      seriesId: Number(s.id),
      timeframe: String(s.timeframe ?? ''),
      start: String(s.series_start ?? '').slice(0, 10),
      end: String(s.series_end ?? '').slice(0, 10),
      quality: String(s.quality_status ?? 'unknown'),
      archived: Boolean(s.is_archived),
    }))
    .sort((a, b) => a.symbol.localeCompare(b.symbol)),
)

const chosenSymbol = ref('')
const chosenSeriesId = ref<number | null>(null)
/** The chosen series' stored readings, fetched on demand (never recomputed here). */
const dataCheck = ref<Record<string, any> | null>(null)
const checkingData = ref(false)

const strategyId = ref<number | null>(null)
const versionId = ref<number | null>(null)

const startDate = ref('')
const endDate = ref('')
const sizeMode = ref<'strategy' | 'fixed_fraction' | 'risk_per_trade'>('strategy')
const sizeFraction = ref(0.5)
const sizeRiskPct = ref(0.01)

function assetSymbol(assetId: number): string {
  const found = assets.value.find((a) => a.id === assetId)
  return found?.symbol ?? `#${assetId}`
}

const chosenOption = computed<SymbolOption | null>(
  () => symbolOptions.value.find((o) => o.seriesId === chosenSeriesId.value) ?? null,
)

const chosenStrategy = computed<Strategy | null>(
  () => strategies.value.find((s) => s.id === strategyId.value) ?? null,
)

const chosenVersion = computed<StrategyVersion | null>(
  () => versions.value.find((v) => v.id === versionId.value) ?? null,
)

const dateOrderWrong = computed(
  () => Boolean(startDate.value && endDate.value && startDate.value > endDate.value),
)

const sizeInvalid = computed(() => {
  if (sizeMode.value === 'fixed_fraction') return !(sizeFraction.value > 0 && sizeFraction.value <= 1)
  if (sizeMode.value === 'risk_per_trade') return !(sizeRiskPct.value > 0 && sizeRiskPct.value <= 1)
  return false
})

const dataWarning = computed(() => {
  const option = chosenOption.value
  if (!option) return ''
  if (option.quality === 'invalid') {
    return '这份数据里有坏 K 线（非正数或缺失的开高低收）。先修数据，结论才有意义。'
  }
  if (option.quality === 'partial') {
    return `这份数据的质量是 partial：相邻两根 K 线之间有超过 5 天的空洞。结论只在数据真正覆盖的地方成立。`
  }
  if (option.quality === 'unknown') {
    return '这一系列还没有 K 线：可能是刚建档，或者上一次同步没有取到数据。'
  }
  return ''
})

const versionWarning = computed(() => {
  const version = chosenVersion.value
  if (!version) return ''
  if (version.validation_status && version.validation_status !== 'valid') {
    return `这个版本在库里记录的校验状态是「${validationLabel(version.validation_status)}」，还没有通过校验。研究之前先确认它的规则。`
  }
  return ''
})

const canStart = computed(
  () => chosenSeriesId.value !== null && versionId.value !== null && !dateOrderWrong.value && !sizeInvalid.value,
)

/** 按钮点不动的时候要说清为什么（评审报告 P0-4），而不是留一个灰按钮。 */
const startBlockedReason = computed(() => {
  if (chosenSeriesId.value === null) return '还不能开始：上面第①步还没有选标的数据。'
  if (versionId.value === null) return '还不能开始：第②步还没有选策略版本。'
  if (dateOrderWrong.value) return '还不能开始：起始日期晚于结束日期。'
  if (sizeInvalid.value) return '还不能开始：仓位比例需要大于 0 且不超过 1。'
  return ''
})

/** Execution overrides, mirroring the backtest page's own options (docs/23, ADR-045). */
function sizingQuery(): Record<string, string> {
  if (sizeMode.value === 'strategy') return {}
  if (sizeMode.value === 'fixed_fraction') {
    return { size_mode: 'fixed_fraction', size_fraction: String(sizeFraction.value) }
  }
  return { size_mode: sizeMode.value, size_risk_pct: String(sizeRiskPct.value) }
}

const planSentence = computed(() => {
  const option = chosenOption.value
  const version = chosenVersion.value
  const strategy = chosenStrategy.value
  if (!option || !strategy || !version) return ''
  const window = startDate.value || endDate.value
    ? `${startDate.value || '最早'} → ${endDate.value || '最新'}`
    : `${option.start} → ${option.end}（全部已同步数据）`
  return `将用「${strategy.name}」的版本 ${version.version}，在 ${option.symbol} 的${timeframeLabel(option.timeframe)}上跑一次历史回测，区间 ${window}。回测由量化引擎计算，这一页只负责把选择带过去。`
})

async function load() {
  error.value = ''
  try {
    const [a, sr, st] = await Promise.all([api.assets(), api.series(), api.strategies()])
    assets.value = a
    seriesList.value = sr
    strategies.value = st
    const first = symbolOptions.value.find((o) => !o.archived) ?? symbolOptions.value[0]
    if (first && chosenSeriesId.value === null) {
      chosenSymbol.value = first.symbol
      chosenSeriesId.value = first.seriesId
    }
    if (strategies.value.length) strategyId.value = strategies.value[0].id
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

async function loadDataCheck() {
  dataCheck.value = null
  if (chosenSeriesId.value === null) return
  checkingData.value = true
  try {
    dataCheck.value = await api.seriesDetail(chosenSeriesId.value)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    checkingData.value = false
  }
}

function pickSymbol(seriesId: number) {
  chosenSeriesId.value = seriesId
  const option = symbolOptions.value.find((o) => o.seriesId === seriesId)
  if (option) chosenSymbol.value = option.symbol
}

function startResearch() {
  if (!canStart.value || chosenSeriesId.value === null || versionId.value === null) return
  const option = chosenOption.value
  if (!option) return
  router.push({
    path: '/backtest',
    query: {
      strategy_version_id: String(versionId.value),
      symbol: option.symbol,
      timeframe: option.timeframe || '1d',
      run: '1',
      ...(startDate.value ? { start: startDate.value } : {}),
      ...(endDate.value ? { end: endDate.value } : {}),
      ...sizingQuery(),
    },
  })
}

watch(strategyId, loadVersions)
watch(chosenSeriesId, loadDataCheck)
onMounted(async () => {
  await load()
  await loadVersions()
  await loadDataCheck()
})
</script>

<template>
  <div>
    <h1 class="page-title">研究策略</h1>
    <p class="page-sub">
      想研究一个策略，只需要四步：选一个标的、选一个策略、设几个参数、开始研究。
      数据集、Series ID、DSL、哈希这些工程概念在高级模式里才出现，普通模式下你不用先理解它们。
    </p>

    <p v-if="error" class="error">{{ error }}</p>

    <div class="card card-quiet">
      <h3>① 选择标的</h3>
      <p class="muted">
        下面列出来的都是已经同步好的数据。没有你要的代码，就先去「数据」页同步一份。
      </p>
      <div v-if="symbolOptions.length" class="row" style="margin-bottom: 10px">
        <select
          :value="chosenSeriesId ?? ''"
          style="max-width: 320px"
          @change="pickSymbol(Number(($event.target as HTMLSelectElement).value))"
        >
          <option v-for="o in symbolOptions" :key="o.seriesId" :value="o.seriesId">
            {{ o.symbol }} · {{ o.timeframe }}{{ o.archived ? '（已归档）' : '' }}
          </option>
        </select>
      </div>
      <p v-else class="muted">
        还没有任何数据。<RouterLink to="/data">去「数据」页同步一份</RouterLink>，再回来研究。
      </p>

      <div v-if="chosenOption" style="margin-top: 8px">
        <p class="muted">
          数据覆盖：{{ chosenOption.start }} → {{ chosenOption.end }} ·
          质量：{{ qualityLabel(chosenOption.quality) }}
          <span v-if="isAdvanced" class="muted">（{{ chosenOption.quality }}）</span>
          <span v-if="checkingData"> · 读取中…</span>
          <span v-else-if="dataCheck"> · 共 {{ dataCheck.bar_count }} 根 K 线</span>
        </p>
        <p v-if="isAdvanced && dataCheck" class="muted">
          系列 #{{ dataCheck.id }} · 来源 {{ dataCheck.source_id ?? '—' }} · 数据集版本
          {{ dataCheck.dataset_version ?? '—' }} · 内容哈希
          {{ String(dataCheck.content_hash ?? '—').slice(0, 12) }}…
        </p>
        <p v-if="dataWarning" class="notice warn">⚠️ {{ dataWarning }}</p>
      </div>
    </div>

    <div class="card card-quiet" style="margin-top: 14px">
      <h3>② 选择策略</h3>
      <div v-if="strategies.length" class="row" style="margin-bottom: 10px">
        <select v-model.number="strategyId" style="max-width: 260px">
          <option v-for="s in strategies" :key="s.id" :value="s.id">
            #{{ s.id }} {{ s.name }}
          </option>
        </select>
        <select v-model.number="versionId" style="max-width: 240px">
          <option :value="0" disabled>先选一个策略版本</option>
          <option v-for="v in versions" :key="v.id" :value="v.id">
            {{ v.version }}{{ v.is_current ? '（当前）' : '' }} ·
            {{ v.validation_status === 'valid' ? '已校验' : `校验状态：${validationLabel(v.validation_status)}` }}
          </option>
        </select>
      </div>
      <p v-else class="muted">
        还没有策略。<RouterLink to="/strategies">去「我的策略」页</RouterLink>用一句人话创建一个，
        或者从 GitHub 导入一个（只读分析，不执行仓库代码）。
      </p>
      <p v-if="versionWarning" class="notice warn">⚠️ {{ versionWarning }}</p>
      <p v-else-if="chosenVersion" class="muted">
        选中版本创建于 {{ chosenVersion.created_at?.slice(0, 10) }}，创建后不可修改；换一套参数就是新版本。
      </p>
    </div>

    <div class="card card-quiet" style="margin-top: 14px">
      <h3>③ 设置少量参数</h3>
      <p class="muted" style="margin-bottom: 8px">
        只在这里改两件事：研究哪一段时间，以及每次用多少钱。其余假设（手续费、滑点、成交模型）
        用策略里存下来的那一套，回测页会把它原样列出来。
      </p>
      <div class="row" style="margin-bottom: 10px">
        <label class="muted" for="research-start">从</label>
        <input id="research-start" v-model="startDate" type="date" style="max-width: 180px" />
        <label class="muted" for="research-end">到</label>
        <input id="research-end" v-model="endDate" type="date" style="max-width: 180px" />
        <span class="muted">留空＝用全部已同步数据</span>
      </div>
      <p v-if="dateOrderWrong" class="error">起始日期不能晚于结束日期。</p>
      <div class="row">
        <select v-model="sizeMode" style="max-width: 220px">
          <option value="strategy">仓位：用策略里的设置</option>
          <option value="fixed_fraction">仓位：每次投入固定比例</option>
          <option value="risk_per_trade">仓位：按每笔风险</option>
        </select>
        <template v-if="sizeMode === 'fixed_fraction'">
          <label class="muted" for="research-fraction">投入比例</label>
          <input
            id="research-fraction"
            v-model.number="sizeFraction"
            type="number"
            min="0.01"
            max="1"
            step="0.05"
            style="max-width: 110px"
          />
        </template>
        <template v-else-if="sizeMode === 'risk_per_trade'">
          <label class="muted" for="research-risk">单笔风险</label>
          <input
            id="research-risk"
            v-model.number="sizeRiskPct"
            type="number"
            min="0.001"
            max="1"
            step="0.005"
            style="max-width: 110px"
          />
        </template>
      </div>
      <p v-if="sizeInvalid" class="error">比例需要大于 0 且不超过 1。</p>
    </div>

    <div class="card" style="margin-top: 14px">
      <h3>④ 开始研究</h3>
      <p v-if="planSentence" class="conclusion-sentence">{{ planSentence }}</p>
      <p v-else class="muted">先选好标的和策略版本，这里会写清楚接下来会发生什么。</p>
      <p class="muted">
        点下面这个按钮会跳到「回测」页并立刻跑这一次回测。回测结果由引擎计算并落库，
        之后在「回测」页随时能再打开来看；这一页不产生任何量化数字。
      </p>
      <button :disabled="!canStart" @click="startResearch">开始研究</button>
      <p v-if="!canStart" class="muted" style="margin-top: 8px">{{ startBlockedReason }}</p>
    </div>
  </div>
</template>
