<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type Asset, type SignalIntent, type Strategy } from '@/api'
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

async function syncData() {
  syncing.value = true
  error.value = ''
  info.value = ''
  try {
    const result = await api.syncMarketData(symbol.value)
    info.value = `同步完成：新增 ${result.inserted} 根 K 线（series ${result.series_id}）`
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
        <div class="row" style="margin-bottom: 10px">
          <select v-model="symbol" style="max-width: 220px">
            <option v-for="a in assets" :key="a.id" :value="a.symbol">
              {{ a.symbol }}（{{ a.asset_class }}）
            </option>
            <option v-if="!assets.length" value="DEMO-AAPL">DEMO-AAPL</option>
          </select>
          <button :disabled="syncing" @click="syncData">
            {{ syncing ? '同步中…' : '同步日线数据' }}
          </button>
        </div>
        <table v-if="series.length">
          <thead>
            <tr>
              <th>ID</th>
              <th>资产</th>
              <th>周期</th>
              <th>数据版本</th>
              <th>质量</th>
              <th>最后更新</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in series" :key="String(s.id)">
              <td>{{ s.id }}</td>
              <td>{{ s.asset_id }}</td>
              <td>{{ s.timeframe }}</td>
              <td>{{ s.dataset_version }}</td>
              <td>{{ s.quality_status }}</td>
              <td>{{ formatDateTime(String(s.last_sync_at ?? '')) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">还没有数据序列，先同步一次。</p>
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
      <h3>说明</h3>
      <p class="muted">
        DSL 只允许使用已注册的指标与价格行为特征；引用未来数据（例如 <code>future_close</code>）会被校验器直接拒绝。
        停止与目标默认按 ATR 倍数计算，成交模型为「信号确认后的下一根 K 线开盘价」。
      </p>
    </div>
  </div>
</template>
