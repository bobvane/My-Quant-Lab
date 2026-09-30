<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type Asset, type GithubAnalysis, type SignalIntent, type Strategy } from '@/api'
import { formatDateTime, formatNumber } from '@/format'

const assets = ref<Asset[]>([])
const strategies = ref<Strategy[]>([])
const symbol = ref('DEMO-AAPL')
const series = ref<Array<Record<string, unknown>>>([])
const signals = ref<SignalIntent[]>([])
const error = ref('')
const info = ref('')
const syncing = ref(false)

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
    const [a, s, sr] = await Promise.all([api.assets(), api.strategies(), api.series()])
    assets.value = a
    strategies.value = s
    series.value = sr
  } catch (e) {
    error.value = (e as Error).message
  }
}

function assetSymbol(assetId: number): string {
  const found = assets.value.find((a) => a.id === assetId)
  return found?.symbol ?? `#${assetId}`
}

async function syncData() {
  syncing.value = true
  error.value = ''
  info.value = ''
  try {
    const result = await api.syncMarketData(symbol.value.trim()) as Record<string, any>
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

const repoUrl = ref('')
const repoRef = ref('')
const repoToken = ref('')
const importName = ref('')
const analyzing = ref(false)
const importing = ref(false)
const analysis = ref<GithubAnalysis | null>(null)

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
      analysis.value.ref,
    )
    info.value = `已导入策略 #${result.strategy_id}（版本 ${result.version}，${result.validation_status}）`
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
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in series" :key="String(s.id)">
              <td>{{ assetSymbol(Number(s.asset_id)) }}</td>
              <td>{{ s.timeframe }}</td>
              <td class="muted">{{ String(s.series_start ?? '').slice(0, 10) }} → {{ String(s.series_end ?? '').slice(0, 10) }}</td>
              <td>{{ s.quality_status }}</td>
              <td>{{ formatDateTime(String(s.last_sync_at ?? '')) }}</td>
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
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in strategies" :key="s.id">
              <td>{{ s.id }}</td>
              <td>{{ s.name }}</td>
              <td>{{ s.version_count }}</td>
              <td>{{ s.source_type }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">还没有策略。</p>
      </div>
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
        <input
          v-model="repoToken"
          type="password"
          style="max-width: 340px"
          placeholder="GitHub token（可选，仅提限额用，不存储）"
        />
      </div>
      <div v-if="analysis">
        <p class="muted">
          {{ analysis.owner }}/{{ analysis.repo }} @ {{ analysis.ref }} ·
          扫描 {{ analysis.files_scanned.length }} 个文件 ·
          许可证 {{ analysis.license ?? '未知' }}
        </p>
        <ul v-if="analysis.warnings.length" class="error">
          <li v-for="(w, idx) in analysis.warnings" :key="idx">{{ w }}</li>
        </ul>
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

    <div class="card" style="margin-top: 14px">
      <h3>说明</h3>
      <p class="muted">
        DSL 只允许使用已注册的指标与价格行为特征；引用未来数据（例如 <code>future_close</code>）会被校验器直接拒绝。
        停止与目标默认按 ATR 倍数计算，成交模型为「信号确认后的下一根 K 线开盘价」。
      </p>
    </div>
  </div>
</template>
