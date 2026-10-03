<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  api,
  type Asset,
  type GithubAnalysis,
  type GithubSnapshot,
  type SignalIntent,
  type Strategy,
  type StrategyLifecycle,
} from '@/api'
import { formatDateTime, formatNumber } from '@/format'

const STAGE_LABELS: Record<string, string> = {
  imported: '已导入',
  normalized: '已规范化',
  validated: '已校验',
  backtested: '已回测',
  oos_tested: '已做样本外',
  paper_trading: '模拟盘',
  reference_signal: '参考信号',
  degraded: '已降级',
  retired: '已退役',
}

function stageLabel(stage: string | null | undefined): string {
  if (!stage) return '—'
  return STAGE_LABELS[stage] ?? stage
}

// What a check actually did. ``checked`` is what older rows stored for all three
// non-events, so it is labelled as history rather than guessed at (ADR-058).
const SOURCE_STATUS_LABELS: Record<string, string> = {
  imported: '已导入新版本',
  no_change: '已检查，策略无变化',
  unchanged: '未变化（同一 commit）',
  incomplete: '未完整读取，未导入',
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
  return 'muted'
}

const lifecycles = ref<StrategyLifecycle[]>([])
const applyingLifecycle = ref<number | null>(null)

const assets = ref<Asset[]>([])
const strategies = ref<Strategy[]>([])
const symbol = ref('DEMO-AAPL')
const series = ref<Array<Record<string, unknown>>>([])
const signals = ref<SignalIntent[]>([])
const error = ref('')
const info = ref('')
const syncing = ref(false)
const lookbackDays = ref(400)

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
const validation = ref<{ is_valid: boolean; issues: Array<Record<string, unknown>> } | null>(null)

async function load() {
  error.value = ''
  try {
    const [a, s, sr, lc] = await Promise.all([
      api.assets(),
      api.strategies(),
      api.series(),
      api.lifecycles(),
    ])
    assets.value = a
    strategies.value = s
    series.value = sr
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

function assetSymbol(assetId: number): string {
  const found = assets.value.find((a) => a.id === assetId)
  return found?.symbol ?? `#${assetId}`
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

async function deleteStrategy(id: number) {
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

async function deleteSeries(id: number) {
  error.value = ''
  try {
    const result = await api.deleteSeries(id)
    info.value = `已删除 ${result.symbol} 的行情数据（系列 #${id}）`
    await load()
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function syncData() {
  syncing.value = true
  error.value = ''
  info.value = ''
  try {
    const result = await api.syncMarketData(symbol.value.trim(), '1d', lookbackDays.value) as Record<string, any>
    if (result.inserted > 0) {
      info.value = `✅ 同步完成：${symbol.value} 新增 ${result.inserted} 根 K 线（系列 #${result.series_id}，其中 ${result.closed_bars_in_fetch} 根已收盘）。现在可以去「回测实验室」用它跑回测了。`
    } else {
      info.value = `ℹ ${symbol.value} 数据已是最新（${result.message ?? '无新增'}）。可以到「回测实验室」用它跑回测。`
    }
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    syncing.value = false
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
  if (reason === 'manual_import') return '这个 commit 是人工审核后导入的。'
  if (extraction.imported === true) return '已从这个 commit 生成新的策略版本。'
  if (extraction.imported === false) return '已读取这个 commit，但抽取出的策略没有变化。'
  return '这条快照没有记录原因（ADR-058 之前的旧记录）。'
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
const analyzing = ref(false)
const importing = ref(false)
const analysis = ref<GithubAnalysis | null>(null)

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
  return COMMIT_SHA_RE.test(text) ? `${text.slice(0, 12)}…` : `${text}（ADR-060 之前记的是分支名）`
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
    return
  }
  importing.value = true
  try {
    const dsl = JSON.parse(dslText.value)
    const result = await api.importGithubStrategy(
      repoUrl.value.trim(),
      importName.value.trim() || analysis.value.repo,
      '1.0.0',
      dsl,
      analysis.value.commit,
      analysis.value.ref,
    )
    info.value = `已导入策略 #${result.strategy_id}（版本 ${result.version}，${result.validation_status}，来源 commit ${shortCommit(result.source_commit)}）`
    analysis.value = null
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    importing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h1 class="page-title">行情与策略</h1>
    <p class="page-sub">同步标准化 OHLCV，编写并校验统一策略 DSL。策略版本一旦创建即不可修改。</p>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="info" class="notice">{{ info }}</p>

    <div class="grid cols-2">
      <div class="card">
        <h3>行情同步</h3>
        <p class="muted" style="margin-bottom: 8px">
          输入 Yahoo Finance 代码（美股如 AAPL、MSFT，ETF 如 SPY、QQQ，加密货币如 BTC-USD），
          点击同步获取真实日线。
        </p>
        <div class="row" style="margin-bottom: 10px">
          <input
            v-model="symbol"
            list="symbol-suggestions"
            style="max-width: 220px"
            placeholder="输入代码，如 AAPL、QQQ、BTC-USD"
            @keyup.enter="syncData"
          />
          <datalist id="symbol-suggestions">
            <option v-for="a in assets" :key="a.id" :value="a.symbol" />
            <option value="SPY" />
            <option value="QQQ" />
            <option value="MSFT" />
            <option value="BTC-USD" />
          </datalist>
          <select v-model.number="lookbackDays" style="max-width: 140px">
            <option :value="90">近 3 个月</option>
            <option :value="180">近 6 个月</option>
            <option :value="365">近 1 年</option>
            <option :value="730">近 2 年</option>
            <option :value="1825">近 5 年</option>
            <option :value="3650">近 10 年</option>
          </select>
          <button :disabled="syncing || !symbol.trim()" @click="syncData">
            {{ syncing ? '同步中…（可能需要几秒）' : '同步日线数据' }}
          </button>
        </div>
        <table v-if="series.length">
          <thead>
            <tr>
              <th>代码</th>
              <th>周期</th>
              <th>数据范围</th>
              <th>质量</th>
              <th>最后同步</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in series" :key="String(s.id)">
              <td>{{ assetSymbol(Number(s.asset_id)) }}</td>
              <td>{{ s.timeframe }}</td>
              <td class="muted">{{ String(s.series_start ?? '').slice(0, 10) }} → {{ String(s.series_end ?? '').slice(0, 10) }}</td>
              <td>{{ s.quality_status }}</td>
              <td>{{ formatDateTime(String(s.last_sync_at ?? '')) }}</td>
              <td><button class="ghost" @click="deleteSeries(Number(s.id))">删除</button></td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">还没有数据，在上面输入代码点同步。</p>
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
                <button class="ghost" :disabled="deletingStrategy === s.id" @click="deleteStrategy(s.id)">
                  {{ deletingStrategy === s.id ? '删除中…' : '删除' }}
                </button>
              </td>
              <td>
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

    <div class="card" style="margin-top: 14px">
      <h3>策略 DSL（声明式，JSON 形式）</h3>
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

    <div class="card" style="margin-top: 14px">
      <h3>从 GitHub 导入（只读分析，不执行仓库代码）</h3>
      <div class="row" style="margin-bottom: 10px">
        <input v-model="repoUrl" style="max-width: 340px" placeholder="https://github.com/owner/repo" />
        <input v-model="repoRef" style="max-width: 140px" placeholder="分支/tag（可选）" />
        <button :disabled="analyzing" @click="analyzeRepo">
          {{ analyzing ? '分析中…（视网络情况可能需要一两分钟）' : '分析仓库' }}
        </button>
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
      <div v-if="analysis">
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
        <ul v-if="analysis.warnings.length" class="error">
          <li v-for="(w, idx) in analysis.warnings" :key="idx">{{ w }}</li>
        </ul>
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
        <p v-if="analysis.unsafe_flags.length" class="error">
          发现 {{ analysis.unsafe_flags.length }} 个不安全构造（导入器不会执行它们，但请先审查）。
        </p>
        <p v-if="analysis.unknowns.length" class="muted">
          另有 {{ analysis.unknowns.length }} 处无法映射的内容（已保留证据，未编造规则）。
        </p>
        <p class="notice">
          草案已填入下方 DSL 编辑器（缺失的离场规则需手动补齐），确认无误后导入。
        </p>
        <div class="row" style="margin-top: 10px">
          <input v-model="importName" style="max-width: 260px" placeholder="策略名称" />
          <button :disabled="importing" @click="importReviewed">
            {{ importing ? '导入中…' : '确认导入' }}
          </button>
        </div>
      </div>
      <p v-else class="muted">
        输入公开仓库地址后，系统只下载文本做静态分析：识别指标、规则与参数，
        无法确认的一律标记未知，绝不执行仓库里的任何代码。
      </p>
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
                      ['incomplete_analysis', 'unparseable_python'].includes(
                        String(latestSnapshot(s)?.extraction?.reason ?? ''),
                      )
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
