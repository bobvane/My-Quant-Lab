<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, type Asset } from '@/api'
import { formatDateTime } from '@/format'
import { isAdvanced } from '@/mode'
import { defaultSymbolFor, providerLabel, qualityLabel } from '@/wording'

// 数据这一页只做一件事：把「数据」从「我的策略」里分出来（评审 §7、§19；ADR-131）。
//
// 普通模式回答三个问题：我有哪些数据？它覆盖哪段时间、质量如何？删掉会发生什么。
// 工程读数（系列 ID、来源、数据集版本、内容哈希、K 线根数）留在高级模式，
// 但它们是同一份后端读数的另一层展示，不是另算一套数（ADR-126、ADR-131）。
// 评审报告 P0-2：普通模式里不出现原始英文枚举、后端文件路径与 ADR / 评审编号。
const assets = ref<Asset[]>([])
const series = ref<Array<Record<string, unknown>>>([])
const error = ref('')
const info = ref('')
// 默认代码跟着当前行情源走（评审报告 P1-7）：演示数据源只服务 DEMO-AAPL / DEMO-BTC，
// 真行情源用真实代码。用户自己输入过之后就不再动它。
const symbol = ref('')
const symbolTouched = ref(false)
const provider = ref('')
const syncing = ref(false)
// The default has to be one of these options: a value the <select> does not offer
// leaves the browser rendering an empty box (measured on /data at 375px).
const LOOKBACK_OPTIONS: Array<{ days: number; label: string }> = [
  { days: 90, label: '近 3 个月' },
  { days: 180, label: '近 6 个月' },
  { days: 365, label: '近 1 年' },
  { days: 730, label: '近 2 年' },
  { days: 1825, label: '近 5 年' },
  { days: 3650, label: '近 10 年' },
]
const lookbackDays = ref(365)

const providerText = computed(() => providerLabel(provider.value))
const defaultSymbol = computed(() => defaultSymbolFor(provider.value))
// Archived series are hidden by default: they are kept because backtests still
// point at them (ADR-081), not because they are still in use.
const showArchived = ref(false)

/** Raw readings for one series, fetched on demand and shown in advanced mode. */
const rawSeries = ref<Record<string, any> | null>(null)
const rawLoading = ref<number | null>(null)

const QUALITY_MEANING: Array<{ status: string; text: string }> = [
  {
    status: 'valid',
    text: '每一根 K 线的开高低收都是正数，相邻两根之间没有超过 5 天的空洞。',
  },
  {
    status: 'partial',
    text: '数据本身没问题，但中间有超过 5 天的空洞（停牌、缺的交易日，或数据源没有给）。',
  },
  {
    status: 'invalid',
    text: '出现了非正数或缺失的开高低收，或者最高价低于最低价 —— 这种数据不能直接用来研究。',
  },
  {
    status: 'unknown',
    text: '还没有 K 线：刚建档，或者上一次同步没有取到数据。',
  },
]

function assetSymbol(assetId: number): string {
  const found = assets.value.find((a) => a.id === assetId)
  return found?.symbol ?? `#${assetId}`
}

async function load() {
  error.value = ''
  try {
    const [a, sr, appInfo] = await Promise.all([
      api.assets(),
      api.series(showArchived.value),
      api.systemInfo().catch(() => null),
    ])
    assets.value = a
    series.value = sr
    if (appInfo) {
      provider.value = String(appInfo.market_data_provider ?? '')
      // 只在用户还没自己输入过的时候给默认值（评审报告 P1-7）。
      if (!symbolTouched.value && !symbol.value.trim()) symbol.value = defaultSymbol.value
    }
  } catch (e) {
    error.value = (e as Error).message
  }
}

// Archived rows only exist in the response when they are asked for, so the toggle
// is a re-fetch rather than a client-side filter.
watch(showArchived, () => {
  void load()
})

async function syncData() {
  syncing.value = true
  error.value = ''
  info.value = ''
  try {
    const result = (await api.syncMarketData(symbol.value.trim(), '1d', lookbackDays.value)) as Record<
      string,
      any
    >
    if (result.inserted > 0) {
      info.value = `✅ 同步完成：${symbol.value} 新增 ${result.inserted} 根 K 线（系列 #${result.series_id}，其中 ${result.closed_bars_in_fetch} 根已收盘）。现在可以去「研究策略」用它跑回测了。`
    } else {
      info.value = `ℹ ${symbol.value} 数据已是最新（${result.message ?? '无新增'}）。可以到「研究策略」用它跑回测。`
    }
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    syncing.value = false
  }
}

async function deleteSeries(id: number, symbol: string) {
  const ok = window.confirm(
    `确定删除 ${symbol} 的行情数据？没有回测引用时数据会被真正删除且不可恢复；被回测引用时会改为归档（数据保留、可从「显示已归档」恢复）。`,
  )
  if (!ok) return
  error.value = ''
  info.value = ''
  try {
    const result = await api.deleteSeries(id)
    info.value = result.archived
      ? `${result.message}（${result.blocking_runs} 次回测仍在引用它）`
      : `已删除 ${result.symbol} 的行情数据（系列 #${id}）`
    await load()
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function restoreSeries(id: number) {
  error.value = ''
  info.value = ''
  try {
    await api.restoreSeries(id)
    info.value = `已恢复系列 #${id}`
    await load()
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function showRawReadings(id: number) {
  rawLoading.value = id
  error.value = ''
  try {
    rawSeries.value = await api.seriesDetail(id)
  } catch (e) {
    error.value = (e as Error).message
    rawSeries.value = null
  } finally {
    rawLoading.value = null
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h1 class="page-title">数据</h1>
    <p class="page-sub">
      这一页只管数据：把一段真实行情同步进来，看清它覆盖了哪段时间、质量如何，以及删掉会发生什么。
      策略本身在「研究策略」和「我的策略」两页，回测在「回测」页。
    </p>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="info" class="notice">{{ info }}</p>

    <div class="card card-quiet">
      <h3>同步行情</h3>
      <p class="muted" style="margin-bottom: 8px">
        输入 Yahoo Finance 代码（美股如 AAPL、MSFT，ETF 如 SPY、QQQ，加密货币如 BTC-USD），
        点击同步获取真实日线。同步只负责把数据取回来，不会改动策略，也不会自动跑回测。
      </p>
      <p class="muted" style="margin-bottom: 8px">当前行情源：{{ providerText }}</p>
      <div class="row" style="margin-bottom: 10px">
        <input
          v-model="symbol"
          list="symbol-suggestions"
          style="max-width: 220px"
          placeholder="输入代码，如 AAPL、QQQ、BTC-USD"
          @input="symbolTouched = true"
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
          <option v-for="opt in LOOKBACK_OPTIONS" :key="opt.days" :value="opt.days">
            {{ opt.label }}
          </option>
        </select>
        <button :disabled="syncing || !symbol.trim()" @click="syncData">
          {{ syncing ? '同步中…（可能需要几秒）' : '同步日线数据' }}
        </button>
      </div>
      <p v-if="!symbol.trim()" class="muted" style="margin-bottom: 8px">
        请先输入要同步的代码，再点「同步日线数据」。
      </p>
      <label class="muted">
        <input v-model="showArchived" type="checkbox" /> 显示已归档（有回测使用，数据为可复现而保留）
      </label>
      <table v-if="series.length">
        <thead>
          <tr>
            <th>代码</th>
            <th>周期</th>
            <th>数据范围</th>
            <th>质量</th>
            <th>最后同步</th>
            <th v-if="isAdvanced">系列 ID</th>
            <th v-if="isAdvanced">来源</th>
            <th v-if="isAdvanced">数据集版本</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in series" :key="String(s.id)">
            <td>
              {{ assetSymbol(Number(s.asset_id)) }}
              <span v-if="s.is_archived" class="badge archived">已归档</span>
            </td>
            <td>{{ s.timeframe }}</td>
            <td class="muted">
              {{ String(s.series_start ?? '').slice(0, 10) }} → {{ String(s.series_end ?? '').slice(0, 10) }}
            </td>
            <td>
              {{ qualityLabel(String(s.quality_status)) }}
              <span v-if="isAdvanced" class="muted">（{{ s.quality_status }}）</span>
            </td>
            <td>{{ formatDateTime(String(s.last_sync_at ?? '')) }}</td>
            <td v-if="isAdvanced" class="muted">#{{ s.id }}</td>
            <td v-if="isAdvanced" class="muted">{{ s.source_id ?? '—' }}</td>
            <td v-if="isAdvanced" class="muted">{{ s.dataset_version ?? '—' }}</td>
            <td>
              <button
                v-if="isAdvanced"
                class="ghost"
                :disabled="rawLoading === Number(s.id)"
                @click="showRawReadings(Number(s.id))"
              >
                {{ rawLoading === Number(s.id) ? '读取中…' : '原始读数' }}
              </button>
              <button v-if="s.is_archived" class="ghost" @click="restoreSeries(Number(s.id))">恢复</button>
              <button
                v-else
                class="ghost danger"
                @click="deleteSeries(Number(s.id), assetSymbol(Number(s.asset_id)))"
              >
                删除
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">还没有数据，在上面输入代码点同步。</p>

      <div v-if="isAdvanced && rawSeries" style="margin-top: 12px">
        <h3>系列 #{{ rawSeries.id }} 的原始读数</h3>
        <p class="muted">
          这些是这一页读数背后的字段，直接来自后端，这一页不重新计算它们。
        </p>
        <table>
          <tbody>
            <tr>
              <td>K 线根数</td>
              <td>{{ rawSeries.bar_count }}</td>
            </tr>
            <tr>
              <td>内容哈希</td>
              <td class="muted">{{ rawSeries.content_hash ?? '—' }}</td>
            </tr>
            <tr>
              <td>复权 / 时区</td>
              <td class="muted">{{ String(rawSeries.adjusted) }} · {{ rawSeries.timezone ?? '—' }}</td>
            </tr>
            <tr>
              <td>来源</td>
              <td class="muted">{{ rawSeries.source_id ?? '—' }}</td>
            </tr>
            <tr>
              <td>数据集版本</td>
              <td class="muted">{{ rawSeries.dataset_version ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="card card-quiet" style="margin-top: 14px">
      <h3>质量那一列是什么意思</h3>
      <p class="muted">
        每次同步完成后，系统都会检查一遍这份数据，结果只有四种：
      </p>
      <ul>
        <li v-for="row in QUALITY_MEANING" :key="row.status">
          <strong>{{ qualityLabel(row.status) }}</strong>
          <span v-if="isAdvanced" class="muted">（<code>{{ row.status }}</code>）</span> —— {{ row.text }}
        </li>
      </ul>
      <p class="muted">
        这四种结果来自后端读数据时的检查（高级模式下可以在「原始读数」里看到对应的字段名）。
        质量只描述数据本身，不描述策略好坏。数据有缺口不等于不能研究，但它会限制结论：
        研究结论只在数据真正覆盖的那段时间里成立。
      </p>
    </div>

    <div class="card card-quiet" style="margin-top: 14px">
      <h3>删除会发生什么</h3>
      <p class="muted">
        已经被回测用过的数据不会被真正删除：系统把它改成「归档」，因为回测结果要能重新算出同一份
        数字，就必须留着当时用的数据。归档后勾上「显示已归档」仍然看得到，点「恢复」就回到正常列表。
        没有任何回测引用的数据才会被真正删除，且不可恢复 —— 删除前会再问一次。
      </p>
    </div>
  </div>
</template>
