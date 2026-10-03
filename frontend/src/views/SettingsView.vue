<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import {
  api,
  type AIProviderRecord,
  type AppSettingsEnvironment,
  type HealthResponse,
  type NotificationConfig,
  type NotificationTestResult,
  type ProviderTestResult,
  type SystemInfo,
  type TemporaryAccessState,
} from '@/api'
import StatCard from '@/components/StatCard.vue'
import { formatDateTime, formatNumber } from '@/format'
// 界面模式在这一页有两处作用：模式切换开关本身，以及决定工程读数是否出现（ADR-126）。
import { isAdvanced, mode, setMode } from '@/mode'

const events = ref<Array<Record<string, unknown>>>([])
const auditTotal = ref(0)
// 软件工程读数（系统状态、版本、引擎、数据库、Redis、特征版本、DSL Schema）原本挤在
// 首页第一屏，现在归到这一页的「系统信息」组里：它回答「软件本身怎么样」，不是
// 「我现在该做什么」（评审 §6；ADR-134）。这一页的 `info` 是操作结果横幅，所以读数
// 另起一个名字。
const health = ref<HealthResponse | null>(null)
const healthError = ref('')
const serverInfo = ref<SystemInfo | null>(null)
const environment = ref<Partial<AppSettingsEnvironment>>({})
const systemSettings = ref<Array<Record<string, any>>>([])
const newSettingKey = ref('')
const newSettingValue = ref('')

async function saveSetting(key: string, value: string) {
  error.value = ''
  info.value = ''
  try {
    await api.updateSetting(key, value)
    info.value = `已保存设置 ${key}`
    await load()
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function addSetting() {
  if (!newSettingKey.value.trim()) return
  await saveSetting(newSettingKey.value.trim(), newSettingValue.value)
  newSettingKey.value = ''
  newSettingValue.value = ''
}
const error = ref('')
const info = ref('')

const notify = ref<NotificationConfig | null>(null)
const notifyEnabled = ref(false)
const notifyIncludeWait = ref(false)
const quietHours = ref('')
const dailyMax = ref(0)
const cooldown = ref(0)
const notifyBaseUrl = ref('')
const channels = ref<Array<Record<string, any>>>([])
const savingNotify = ref(false)
const testingNotify = ref(false)
const notifyResult = ref<NotificationTestResult | null>(null)
const notifyEvents = ref<Array<Record<string, any>>>([])

// Channel type metadata: which fields to render and whether they are secret.
interface ChannelField {
  key: string
  label: string
  secret?: boolean
  optional?: boolean
  kind?: 'text' | 'number' | 'bool'
}
const CHANNEL_TYPES: Record<string, { label: string; fields: ChannelField[] }> = {
  webhook: {
    label: 'Generic Webhook',
    fields: [
      { key: 'url', label: 'Webhook URL', secret: true },
      { key: 'secret', label: '签名密钥（可选）', secret: true, optional: true },
    ],
  },
  feishu: {
    label: '飞书',
    fields: [
      { key: 'url', label: '机器人 Webhook', secret: true },
      { key: 'secret', label: '签名校验（可选）', secret: true, optional: true },
    ],
  },
  telegram: {
    label: 'Telegram',
    fields: [
      { key: 'bot_token', label: 'Bot Token', secret: true },
      { key: 'chat_id', label: 'Chat ID' },
    ],
  },
  pushplus: {
    label: 'PushPlus',
    fields: [
      { key: 'token', label: 'Token', secret: true },
      { key: 'topic', label: 'Topic（可选）', optional: true },
    ],
  },
  email: {
    label: 'Email (SMTP)',
    fields: [
      { key: 'host', label: 'SMTP 主机' },
      { key: 'port', label: '端口', kind: 'number' },
      { key: 'username', label: '用户名（可选）', optional: true },
      { key: 'password', label: '密码（可选）', secret: true, optional: true },
      { key: 'from_address', label: '发件人（可选）', optional: true },
      { key: 'to_address', label: '收件人（逗号分隔）' },
      { key: 'use_tls', label: 'STARTTLS', kind: 'bool' },
      { key: 'use_ssl', label: 'SSL', kind: 'bool' },
    ],
  },
}
const CHANNEL_ORDER = ['webhook', 'feishu', 'telegram', 'pushplus', 'email']

function channelFields(channel: Record<string, any>): ChannelField[] {
  return CHANNEL_TYPES[channel.type]?.fields ?? []
}

function channelLabel(type: string): string {
  return CHANNEL_TYPES[type]?.label ?? type
}

function fieldPlaceholder(channel: Record<string, any>, field: ChannelField): string {
  if (field.secret && channel[`${field.key}_set`]) {
    const masked = channel[`${field.key}_masked`]
    return masked ? `已设置（${masked}），留空保持` : '已设置，留空保持'
  }
  return field.label
}

function defaultChannel(type: string): Record<string, any> {
  const channel: Record<string, any> = {
    id: `${type}-${Math.random().toString(36).slice(2, 8)}`,
    type,
    enabled: true,
  }
  for (const field of channelFields(channel)) {
    if (field.kind === 'bool') channel[field.key] = field.key !== 'use_ssl'
    else if (field.kind === 'number') channel[field.key] = 587
    else channel[field.key] = ''
  }
  return channel
}

function addChannel(type: string) {
  channels.value.push(defaultChannel(type))
}

function removeChannel(index: number) {
  channels.value.splice(index, 1)
}

const providers = ref<AIProviderRecord[]>([])
const aiModels = ref<Array<Record<string, any>>>([])
const aiPrompts = ref<Array<Record<string, any>>>([])
const aiUsage = ref<Array<Record<string, any>>>([])
const aiTasks = ref<Array<Record<string, any>>>([])
const taskDetail = ref<Record<string, any> | null>(null)
const taskDetailId = ref<number | null>(null)
const filterEntityType = ref('')
const filterEntityId = ref('')

async function loadEntityAudit() {
  error.value = ''
  if (!filterEntityType.value.trim() || !filterEntityId.value.trim()) return
  try {
    const result = await api.auditForEntity(filterEntityType.value.trim(), filterEntityId.value.trim())
    events.value = result.events
    auditTotal.value = result.total
  } catch (e) {
    error.value = (e as Error).message
  }
}
const providerName = ref('')
const baseUrl = ref('')
const apiKey = ref('')
const defaultModel = ref('')
const budget = ref(2)
const modelsCsv = ref('')
const saving = ref(false)
const testingNew = ref(false)
const testResult = ref<ProviderTestResult | null>(null)
const testingId = ref<number | null>(null)
const busyId = ref<number | null>(null)

// --- Temporary remote access (ADR-125) ---------------------------------------
// A Cloudflare Quick Tunnel the operator opens on purpose. The API owns the
// process and its deadline; this panel only mirrors that state, counts the
// remaining minutes down, and never assumes a tunnel survived a page reload or an
// API restart — a public address is not something to restore behind someone's back.
const temporaryAccess = ref<TemporaryAccessState>({
  status: 'disabled',
  url: null,
  started_at: null,
  expires_at: null,
  remaining_seconds: null,
  max_duration_seconds: 3600,
  enabled: true,
  target_url: '',
  detail: null,
})
const tunnelBusy = ref(false)
const tunnelCopied = ref(false)
const tunnelDeadline = ref<number | null>(null)
const tunnelNow = ref(Date.now())
let tunnelTimer: number | undefined
let tunnelTicks = 0

const tunnelRemaining = computed(() => {
  if (tunnelDeadline.value === null) return null
  return Math.max(0, Math.round((tunnelDeadline.value - tunnelNow.value) / 1000))
})

const tunnelClock = computed(() => {
  const seconds = tunnelRemaining.value
  if (seconds === null) return ''
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
})

function applyTemporaryAccess(state: TemporaryAccessState) {
  temporaryAccess.value = state
  tunnelDeadline.value =
    state.remaining_seconds === null ? null : Date.now() + state.remaining_seconds * 1000
  tunnelNow.value = Date.now()
}

async function refreshTunnel() {
  try {
    // The card is informational: a failed poll must not take over the page's
    // error bar, which belongs to real failures.
    applyTemporaryAccess(await api.temporaryAccess())
  } catch {
    /* keep showing the last known state */
  }
}

function tickTunnel() {
  tunnelNow.value = Date.now()
  tunnelTicks += 1
  const status = temporaryAccess.value.status
  // `starting` becomes `active` as soon as cloudflared prints its address; `active`
  // can also end without this page asking (deadline, unexpected exit).
  if (status === 'starting' && tunnelTicks % 2 === 0) void refreshTunnel()
  else if (status === 'active' && (tunnelTicks % 10 === 0 || tunnelRemaining.value === 0)) {
    void refreshTunnel()
  }
}

async function startTunnel() {
  error.value = ''
  info.value = ''
  tunnelBusy.value = true
  tunnelCopied.value = false
  try {
    applyTemporaryAccess(await api.startTemporaryAccess())
  } catch (e) {
    error.value = (e as Error).message
    await refreshTunnel()
  } finally {
    tunnelBusy.value = false
  }
}

async function stopTunnel() {
  error.value = ''
  info.value = ''
  tunnelBusy.value = true
  try {
    applyTemporaryAccess(await api.stopTemporaryAccess())
    tunnelCopied.value = false
    info.value = '临时访问已关闭，公网地址已失效'
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    tunnelBusy.value = false
  }
}

async function copyTunnelUrl() {
  const url = temporaryAccess.value.url
  if (!url) return
  try {
    await navigator.clipboard.writeText(url)
    tunnelCopied.value = true
  } catch {
    // The clipboard needs a secure context; the address is on screen regardless.
    error.value = '复制失败，请手动选择地址'
  }
}

async function load() {
  error.value = ''
  try {
    // Eleven independent panels share this page, so each request answers for
    // itself: one failing endpoint must not blank the other ten (ADR-088).
    const failures: string[] = []
    const note = (label: string) => {
      failures.push(label)
      return null
    }
    const [
      audit,
      settings,
      ai,
      notification,
      models,
      usage,
      notifyLog,
      prompts,
      tasks,
      tunnel,
      systemHealth,
      systemInfo,
    ] = await Promise.all([
      api.audit().catch(() => {
        note('审计日志')
        return { total: 0, events: [] }
      }),
      api.settings().catch(() => note('系统设置')),
      api.aiProviders().catch(() => note('AI 服务商')),
      api.notificationConfig().catch(() => note('通知配置')),
      api.aiModels().catch(() => ({ models: [] })),
      api.aiUsage().catch(() => ({ usage: [] })),
      api.notificationEvents().catch(() => ({ events: [] })),
      api.aiPrompts().catch(() => ({ prompts: [] })),
      api.aiTasksList().catch(() => []),
      api.temporaryAccess().catch(() => note('临时远程访问')),
      // /health asks PostgreSQL, Redis and the Celery workers, so on a bare
      // install it can take seconds; it fills its own card when it arrives
      // instead of holding the rest of the page hostage (ADR-069).
      api.health().catch(() => note('系统信息') ?? null),
      api.systemInfo().catch(() => note('系统信息') ?? null),
    ])
    health.value = systemHealth
    if (!systemHealth) healthError.value = '健康检查没有响应'
    serverInfo.value = systemInfo
    if (tunnel) applyTemporaryAccess(tunnel)
    notifyEvents.value = notifyLog.events
    aiPrompts.value = prompts.prompts
    aiTasks.value = tasks
    events.value = audit.events
    auditTotal.value = audit.total
    environment.value = settings?.environment ?? {}
    systemSettings.value = settings?.settings ?? []
    providers.value = ai?.providers ?? []
    aiModels.value = models.models
    aiUsage.value = usage.usage
    if (notification) applyNotification(notification)
    if (failures.length) {
      error.value = `${failures.join('、')} 加载失败，页面其余内容仍然可用`
    }
  } catch (e) {
    error.value = (e as Error).message
  }
}

function applyNotification(config: NotificationConfig) {
  notify.value = config
  notifyEnabled.value = config.enabled
  notifyIncludeWait.value = config.include_wait
  quietHours.value = config.quiet_hours
  dailyMax.value = config.daily_max
  cooldown.value = config.cooldown_minutes
  notifyBaseUrl.value = config.base_url
  // Secrets are write-only: keep the *_set/*_masked flags for placeholders but
  // never prefill the value itself.
  channels.value = (config.channels ?? []).map((channel) => ({ ...channel }))
}

function channelPayload(channel: Record<string, any>): Record<string, unknown> {
  const out: Record<string, unknown> = { id: channel.id, type: channel.type, enabled: channel.enabled }
  for (const field of channelFields(channel)) {
    const value = channel[field.key]
    if (field.secret) {
      // Only send a secret when the operator typed a new one, so an untouched
      // field keeps the stored value.
      if (typeof value === 'string' && value.trim()) out[field.key] = value.trim()
    } else if (field.kind === 'bool') {
      out[field.key] = Boolean(value)
    } else if (field.kind === 'number') {
      out[field.key] = Number(value)
    } else {
      out[field.key] = typeof value === 'string' ? value.trim() : value
    }
  }
  return out
}

async function saveNotification() {
  error.value = ''
  info.value = ''
  savingNotify.value = true
  try {
    const payload: Record<string, unknown> = {
      enabled: notifyEnabled.value,
      include_wait: notifyIncludeWait.value,
      quiet_hours: quietHours.value.trim(),
      daily_max: dailyMax.value,
      cooldown_minutes: cooldown.value,
      base_url: notifyBaseUrl.value.trim(),
      channels: channels.value.map(channelPayload),
    }
    const saved = await api.updateNotificationConfig(payload)
    applyNotification(saved)
    info.value = '通知设置已保存'
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    savingNotify.value = false
  }
}

async function testNotification() {
  error.value = ''
  notifyResult.value = null
  testingNotify.value = true
  try {
    notifyResult.value = await api.testNotification()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    testingNotify.value = false
  }
}

function modelList(): Array<Record<string, unknown>> {
  // "model" or "model:capability" or "model:capability:inCost:outCost".
  return modelsCsv.value
    .split(',')
    .map((m) => m.trim())
    .filter(Boolean)
    .map((entry) => {
      const parts = entry.split(':').map((p) => p.trim())
      const out: Record<string, unknown> = { model_name: parts[0] }
      if (parts[1]) out.capability_tier = parts[1]
      if (parts[2]) out.input_cost_per_mtok = Number(parts[2])
      if (parts[3]) out.output_cost_per_mtok = Number(parts[3])
      return out
    })
}

async function testBeforeSave() {
  error.value = ''
  testResult.value = null
  if (!baseUrl.value.trim() || !apiKey.value.trim()) {
    error.value = '请先填写 base_url 与 API Key 再测试'
    return
  }
  testingNew.value = true
  try {
    testResult.value = await api.testNewAiProvider(baseUrl.value.trim(), apiKey.value.trim())
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    testingNew.value = false
  }
}

async function save() {
  error.value = ''
  info.value = ''
  if (!providerName.value.trim() || !baseUrl.value.trim() || !apiKey.value.trim()) {
    error.value = '名称、base_url、API Key 都是必填项'
    return
  }
  saving.value = true
  try {
    const created = await api.createAiProvider({
      name: providerName.value.trim(),
      base_url: baseUrl.value.trim(),
      api_key: apiKey.value.trim(),
      default_model: defaultModel.value.trim() || undefined,
      daily_budget_usd: budget.value,
      models: modelList(),
    })
    info.value = `已添加 provider「${created.name}」，密钥已加密存储（${created.key_masked}）`
    // The key only ever lives on the server: clear it from the form immediately.
    apiKey.value = ''
    providerName.value = ''
    defaultModel.value = ''
    modelsCsv.value = ''
    testResult.value = null
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    saving.value = false
  }
}

async function testStored(id: number) {
  error.value = ''
  testResult.value = null
  testingId.value = id
  try {
    testResult.value = await api.testAiProvider(id)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    testingId.value = null
  }
}

async function toggleActive(row: AIProviderRecord) {
  error.value = ''
  busyId.value = row.id
  try {
    await api.updateAiProvider(row.id, { is_active: !row.is_active })
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busyId.value = null
  }
}

async function remove(row: AIProviderRecord) {
  const ok = window.confirm(
    `确定删除 provider「${row.name}」？它的模型配置会一并删除，且不可恢复；有 AI 调用记录时后端会拒绝，请改为停用。`,
  )
  if (!ok) return
  error.value = ''
  info.value = ''
  busyId.value = row.id
  try {
    await api.deleteAiProvider(row.id)
    info.value = `已删除 provider「${row.name}」`
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busyId.value = null
  }
}

async function openTask(task: Record<string, any>) {
  error.value = ''
  const id = Number(task.id)
  if (taskDetailId.value === id) {
    taskDetail.value = null
    taskDetailId.value = null
    return
  }
  taskDetailId.value = id
  taskDetail.value = null
  try {
    taskDetail.value = await api.aiTask(id)
  } catch (e) {
    error.value = (e as Error).message
    taskDetailId.value = null
  }
}

function prettyJson(value: unknown): string {
  if (value === null || value === undefined) return '—'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

onMounted(async () => {
  await load()
  // One timer drives the whole card: it counts the remaining minutes down and
  // re-asks the API while a tunnel is starting or active (ADR-125).
  tunnelTimer = window.setInterval(tickTunnel, 1000)
})

onUnmounted(() => {
  if (tunnelTimer !== undefined) window.clearInterval(tunnelTimer)
})
</script>

<template>
  <div>
    <h1 class="page-title">系统管理</h1>
    <p class="page-sub">
      密钥只写入、永不回显；AI 只负责解释引擎算好的数字。所有关键操作都会留下审计记录。
      普通模式下这一页只留日常要用的分组；运行环境与审计日志这类排查读数在高级模式里。
    </p>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="info" class="notice">{{ info }}</p>

    <div class="card">
      <h3>使用模式</h3>
      <p class="muted">
        普通模式隐藏工程细节（数据集与系列编号、哈希、引擎版本、原始 JSON 与技术错误文本），
        高级模式把它们全部显示出来。切换只影响显示，不改变任何计算结果，也不会少算任何东西。
      </p>
      <div class="mode-switch">
        <button
          class="ghost"
          :class="{ on: mode === 'basic' }"
          :aria-pressed="mode === 'basic'"
          @click="setMode('basic')"
        >
          ○ 普通模式
        </button>
        <button
          class="ghost"
          :class="{ on: mode === 'advanced' }"
          :aria-pressed="mode === 'advanced'"
          @click="setMode('advanced')"
        >
          ● 高级模式
        </button>
      </div>
    </div>

    <h2 class="group-head">AI 设置</h2>

    <div class="card">
      <h3>AI Provider（用于自然语言解释，非必需）</h3>
      <p class="muted">
        填入任意 OpenAI 兼容端点即可，例如 OpenAI、DeepSeek、通义千问、Kimi、GLM、
        OpenRouter 或本地 vLLM。base_url 填到 API 根路径（如
        <code>https://api.openai.com/v1</code>）。不配置时所有量化功能照常工作。
      </p>

      <div class="row" style="margin-bottom: 8px">
        <input v-model="providerName" style="max-width: 180px" placeholder="名称（如 openai）" />
        <input v-model="baseUrl" style="max-width: 300px" placeholder="https://api.example.com/v1" />
        <input v-model="apiKey" type="password" style="max-width: 240px" placeholder="API Key（仅提交时使用）" />
      </div>
      <div class="row" style="margin-bottom: 8px">
        <input v-model="defaultModel" style="max-width: 200px" placeholder="默认模型（可选）" />
        <input v-model="modelsCsv" style="max-width: 280px" placeholder="其他模型，逗号分隔（可选）" />
        <input v-model.number="budget" type="number" step="0.5" min="0" style="max-width: 130px" />
        <button class="ghost" :disabled="testingNew" @click="testBeforeSave">
          {{ testingNew ? '测试中…' : '测试连接' }}
        </button>
        <button :disabled="saving" @click="save">{{ saving ? '保存中…' : '添加' }}</button>
      </div>

      <p v-if="testResult" :class="testResult.ok ? 'notice' : 'error'">
        {{ testResult.ok ? '连接成功' : '连接失败' }}：{{ testResult.detail }}
        <span v-if="testResult.models_found.length" class="muted">
          （可用模型：{{ testResult.models_found.slice(0, 6).join('、') }}…）
        </span>
      </p>

      <table v-if="providers.length" style="margin-top: 10px">
        <thead>
          <tr>
            <th>名称</th>
            <th>base_url</th>
            <th>默认模型</th>
            <th>密钥</th>
            <th>日预算</th>
            <th>状态</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in providers" :key="p.id">
            <td>{{ p.name }}</td>
            <td class="muted">{{ p.base_url }}</td>
            <td>{{ p.default_model ?? '—' }}</td>
            <td class="muted">{{ p.key_masked || '未设置' }}</td>
            <td>{{ formatNumber(p.daily_budget_usd) }} USD</td>
            <td>{{ p.is_active ? '启用' : '停用' }}</td>
            <td>
              <button class="ghost" :disabled="testingId === p.id" @click="testStored(p.id)">
                {{ testingId === p.id ? '测试中…' : '测试' }}
              </button>
              <button class="ghost" :disabled="busyId === p.id" @click="toggleActive(p)">
                {{ p.is_active ? '停用' : '启用' }}
              </button>
              <button class="ghost" :disabled="busyId === p.id" @click="remove(p)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">
        尚未配置 AI provider —— 信号与回测的「AI 解释」按钮会在配置后可用。
      </p>

      <p class="muted" style="margin-top: 8px">
        模型可用 <code>模型名:能力档:输入价:输出价</code> 填写（如
        <code>gpt-4o-mini:cheap:0.15:0.6</code>）；路由按任务能力档 + 成本 + 预算选模型。
      </p>
    </div>

    <div class="card" style="margin-top: 14px">
      <h3>AI 模型目录（路由用）</h3>
      <table v-if="aiModels.length">
        <thead>
          <tr>
            <th>供应商</th>
            <th>模型</th>
            <th>能力档</th>
            <th>输入/输出价 (per Mtok)</th>
            <th>启用</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in aiModels" :key="String(m.id)">
            <td>{{ m.provider }}</td>
            <td>{{ m.model_name }}</td>
            <td>{{ m.capability_tier }}</td>
            <td class="muted">{{ formatNumber(m.input_cost_per_mtok, 3) }} / {{ formatNumber(m.output_cost_per_mtok, 3) }}</td>
            <td>{{ m.is_active ? '是' : '否' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">还没有模型条目。</p>
    </div>

    <div v-if="aiPrompts.length" class="card" style="margin-top: 14px">
      <h3>AI 提示词模板（{{ aiPrompts.length }}）</h3>
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>版本</th>
            <th>任务</th>
            <th>能力档</th>
            <th>启用</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in aiPrompts" :key="String(p.id)">
            <td>{{ p.name }}</td>
            <td>{{ p.version }}</td>
            <td>{{ p.task_type }}</td>
            <td>{{ p.capability_tier }}</td>
            <td>{{ p.is_active ? '是' : '否' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="card" style="margin-top: 14px">
      <h3>AI 用量（最近 {{ aiUsage.length }} 条）</h3>
      <table v-if="aiUsage.length">
        <thead>
          <tr>
            <th>日期</th>
            <th>供应商</th>
            <th>任务</th>
            <th>调用</th>
            <th>Tokens</th>
            <th>费用 USD</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in aiUsage" :key="String(u.id)">
            <td>{{ String(u.usage_date).slice(0, 10) }}</td>
            <td>{{ u.provider_id ?? '—' }}</td>
            <td>{{ u.task_type }}</td>
            <td>{{ u.call_count }}</td>
            <td>{{ u.total_tokens }}</td>
            <td>{{ formatNumber(u.total_cost_usd, 4) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">暂无 AI 调用记录。</p>
    </div>

    <div class="card" style="margin-top: 14px">
      <h3>AI 任务记录（最近 {{ aiTasks.length }} 条）</h3>
      <p class="muted">
        每次 AI 解释都会留下任务记录（提示词版本、模型、费用、耗时）。
        相同输入的再次解释会命中缓存、费用为 0。密钥永不写入任务记录。
      </p>
      <table v-if="aiTasks.length">
        <thead>
          <tr>
            <th>ID</th>
            <th>时间</th>
            <th>任务</th>
            <th>提示词</th>
            <th>状态</th>
            <th>费用 USD</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in aiTasks" :key="String(t.id)">
            <td>{{ t.id }}</td>
            <td class="muted">{{ formatDateTime(String(t.created_at)) }}</td>
            <td>{{ t.task_type }}</td>
            <td class="muted">{{ t.prompt_name }} v{{ t.prompt_version }}</td>
            <td>{{ t.status }}</td>
            <td>{{ formatNumber(t.cost_usd, 4) }}</td>
            <td>
              <button class="ghost" @click="openTask(t)">
                {{ taskDetailId === t.id ? '收起' : '详情' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">暂无 AI 任务记录。配置 provider 后使用「AI 解释」按钮即可产生。</p>

      <div v-if="taskDetail" class="card" style="margin-top: 10px; padding: 10px 12px">
        <div class="row" style="align-items: center; justify-content: space-between">
          <h3 style="margin: 0">任务 #{{ taskDetail.id }} 详情</h3>
          <button class="ghost" @click="openTask(taskDetail)">收起</button>
        </div>
        <table style="margin-top: 8px">
          <tbody>
            <tr>
              <td class="muted">状态</td>
              <td>{{ taskDetail.status }}</td>
            </tr>
            <tr>
              <td class="muted">任务 / 提示词</td>
              <td>{{ taskDetail.task_type }} · {{ taskDetail.prompt_name }} v{{ taskDetail.prompt_version }}</td>
            </tr>
            <tr>
              <td class="muted">Provider / 模型</td>
              <td>{{ taskDetail.provider_id ?? '—' }} / {{ taskDetail.model_id ?? '—' }}</td>
            </tr>
            <tr>
              <td class="muted">输入哈希</td>
              <td><code>{{ taskDetail.input_hash }}</code></td>
            </tr>
            <tr>
              <td class="muted">费用 USD</td>
              <td>{{ formatNumber(taskDetail.cost_usd, 4) }}</td>
            </tr>
            <tr>
              <td class="muted">创建 / 完成</td>
              <td>
                {{ formatDateTime(String(taskDetail.created_at)) }} /
                {{ formatDateTime(taskDetail.completed_at ? String(taskDetail.completed_at) : null) }}
              </td>
            </tr>
          </tbody>
        </table>

        <p v-if="taskDetail.error_message" class="error" style="margin-top: 8px">
          错误：{{ taskDetail.error_message }}
        </p>

        <h4 style="margin: 10px 0 4px">Token 用量</h4>
        <pre class="code-block">{{ prettyJson(taskDetail.token_usage) }}</pre>

        <h4 style="margin: 10px 0 4px">输出（结构化解释）</h4>
        <pre class="code-block">{{ prettyJson(taskDetail.output) }}</pre>
      </div>
    </div>

    <h2 class="group-head">通知</h2>

    <div class="card" style="margin-top: 14px">
      <h3>信号通知（多渠道）</h3>
      <p class="muted">
        当扫描产生看多 / 看空信号时（可选「暂不确认」也通知），向所有启用的渠道发送：Generic Webhook、飞书、
        Telegram、PushPlus、Email(SMTP)。密钥字段只写入、永不回显（留空表示保持原值）。
        通知仅在已收盘 K 线评估后触发，同一事件不会重复发送；AI 文案不会作为收益承诺。
      </p>

      <div class="row" style="margin-bottom: 8px">
        <label class="muted" style="display: flex; align-items: center; gap: 6px">
          <input v-model="notifyEnabled" type="checkbox" style="width: auto" />
          启用通知
        </label>
        <label class="muted" style="display: flex; align-items: center; gap: 6px">
          <input v-model="notifyIncludeWait" type="checkbox" style="width: auto" />
          同时通知 WAIT
        </label>
        <input v-model="quietHours" style="max-width: 160px" placeholder="免打扰 22:00-07:00（UTC）" />
        <input v-model.number="dailyMax" type="number" min="0" style="max-width: 130px" placeholder="每日上限 0=不限" />
        <input v-model.number="cooldown" type="number" min="0" style="max-width: 150px" placeholder="冷却分钟 0=不限" />
      </div>
      <div class="row" style="margin-bottom: 8px">
        <input v-model="notifyBaseUrl" style="max-width: 300px" placeholder="站点地址（用于通知里的链接，可选）" />
        <button class="ghost" :disabled="testingNotify" @click="testNotification">
          {{ testingNotify ? '发送中…' : '发送测试' }}
        </button>
        <button :disabled="savingNotify" @click="saveNotification">
          {{ savingNotify ? '保存中…' : '保存通知设置' }}
        </button>
      </div>

      <div
        v-for="(ch, idx) in channels"
        :key="ch.id"
        class="card"
        style="margin: 6px 0; padding: 8px 12px"
      >
        <div class="row" style="align-items: center">
          <strong>{{ channelLabel(ch.type) }}</strong>
          <label class="muted" style="display: flex; align-items: center; gap: 6px">
            <input v-model="ch.enabled" type="checkbox" style="width: auto" />
            启用
          </label>
          <span class="muted">{{ ch.id }}</span>
          <button class="ghost" @click="removeChannel(idx)">移除</button>
        </div>
        <div class="row" style="margin-top: 6px">
          <template v-for="f in channelFields(ch)" :key="f.key">
            <label
              v-if="f.kind === 'bool'"
              class="muted"
              style="display: flex; align-items: center; gap: 6px"
            >
              <input v-model="ch[f.key]" type="checkbox" style="width: auto" />
              {{ f.label }}
            </label>
            <input
              v-else
              v-model="ch[f.key]"
              :type="f.secret ? 'password' : f.kind === 'number' ? 'number' : 'text'"
              style="max-width: 240px"
              :placeholder="fieldPlaceholder(ch, f)"
            />
          </template>
        </div>
      </div>
      <p v-if="!channels.length" class="muted">还没有渠道，用下面的按钮添加。</p>
      <div class="row" style="margin-top: 8px">
        <button v-for="t in CHANNEL_ORDER" :key="t" class="ghost" @click="addChannel(t)">
          + {{ channelLabel(t) }}
        </button>
      </div>

      <p v-if="notifyResult" :class="notifyResult.ok ? 'notice' : 'error'">
        测试通知{{ notifyResult.ok ? '已发送' : '失败' }}：{{ notifyResult.detail }}
      </p>
      <p v-else-if="notify && !notify.configured" class="muted">
        当前未启用或未配置任何渠道，所有量化功能不受影响，只是不会外发通知。
      </p>

      <h3 style="margin-top: 14px">最近通知（{{ notifyEvents.length }} 条）</h3>
      <table v-if="notifyEvents.length">
        <thead>
          <tr>
            <th>时间</th>
            <th>事件</th>
            <th>渠道/信号</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in notifyEvents" :key="String(e.id)">
            <td class="muted">{{ formatDateTime(String(e.created_at)) }}</td>
            <td>{{ e.event_type }}</td>
            <td class="muted">
              {{ (e.payload && (e.payload.channel || (e.payload.channels || []).join(','))) || (e.payload && e.payload.symbol) || '—' }}
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">暂无通知记录。</p>
    </div>

    <h2 class="group-head">系统设置</h2>

    <div class="card" style="margin-top: 14px">
      <h3>系统参数（system_settings）</h3>
      <p class="muted">
        用于运行时可调的键值（如 <code>proxy_url</code>）。通知相关键请在「信号通知」卡片设置。
      </p>
      <table v-if="systemSettings.length">
        <thead>
          <tr>
            <th>键</th>
            <th>值</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in systemSettings" :key="s.key">
            <td>{{ s.key }}</td>
            <td>
              <span v-if="s.is_secret" class="muted">{{ s.value }}（{{ s.is_set ? '已设置' : '未设置' }}）</span>
              <input
                v-else
                :value="s.value ?? ''"
                style="max-width: 260px"
                @change="saveSetting(String(s.key), ($event.target as HTMLInputElement).value)"
              />
            </td>
            <td>
              <span v-if="s.is_secret" class="muted">写-only</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">暂无系统参数。</p>
      <div class="row" style="margin-top: 8px">
        <input v-model="newSettingKey" style="max-width: 200px" placeholder="键（如 proxy_url）" />
        <input v-model="newSettingValue" style="max-width: 260px" placeholder="值" />
        <button class="ghost" :disabled="!newSettingKey.trim()" @click="addSetting">新增/更新</button>
      </div>
    </div>

    <template v-if="isAdvanced">
      <h2 class="group-head">系统信息</h2>

      <div class="card" style="margin-top: 14px">
        <h3>系统信息</h3>
        <p class="muted">
          这一组读数回答的是「软件本身怎么样」：服务是否健康、跑的是哪个版本、引擎与特征版本、
          行情源和 DSL Schema。它原来挤在首页第一屏，但那块地方应该回答「我现在该做什么」，
          所以搬到这里（评审 §6；ADR-134）。
        </p>
        <div class="grid cols-4" style="margin-top: 14px">
          <StatCard
            label="系统状态"
            :value="health?.status ?? '—'"
            :sub="
              health ? `数据库 ${health.database} / Redis ${health.redis}` : healthError || '连接中…'
            "
          />
          <StatCard
            label="版本"
            :value="health?.version ?? '—'"
            :sub="`引擎 ${health?.engine_version ?? '—'}`"
          />
        </div>
        <div class="row" style="margin-top: 12px">
          <span v-for="m in serverInfo?.modules ?? []" :key="m" class="badge">{{ m }}</span>
        </div>
        <p class="muted" style="margin-top: 10px">
          行情源：{{ serverInfo?.market_data_provider ?? '—' }} · 特征版本：{{ serverInfo?.feature_version ?? '—' }} ·
          DSL Schema：{{ serverInfo?.strategy_schema_version ?? '—' }}
        </p>
        <p class="muted" style="margin-top: 8px">
          CPU、内存、磁盘与容器明细在<RouterLink to="/resources">系统资源</RouterLink>页。
        </p>
      </div>
    </template>

    <template v-if="isAdvanced">
      <h2 class="group-head">运行环境</h2>

      <div class="card" style="margin-top: 14px">
        <h3>运行环境</h3>
        <p class="muted">
          后端进程实际读到的那份配置，出问题时用来对账。密钥类字段不会出现在这里。
        </p>
        <table>
          <tbody>
            <tr v-for="(value, key) in environment" :key="key">
              <td>{{ key }}</td>
              <td>{{ value }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <template v-if="isAdvanced">
      <h2 class="group-head">审计日志</h2>

      <div class="card" style="margin-top: 14px">
        <h3>审计日志（最近 {{ events.length }} 条，共 {{ auditTotal }} 条）</h3>
      <div class="row" style="margin-bottom: 8px">
        <input v-model="filterEntityType" style="max-width: 160px" placeholder="实体类型（如 strategy）" />
        <input v-model="filterEntityId" style="max-width: 140px" placeholder="实体 ID（如 1）" />
        <button class="ghost" :disabled="!filterEntityType || !filterEntityId" @click="loadEntityAudit">
          按实体过滤
        </button>
        <button class="ghost" @click="load">显示全部</button>
      </div>
      <table v-if="events.length">
        <thead>
          <tr>
            <th>时间</th>
            <th>事件</th>
            <th>对象</th>
            <th>动作</th>
            <th>操作者</th>
            <th>详情</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in events" :key="String(e.id)">
            <td>{{ formatDateTime(String(e.created_at)) }}</td>
            <td>{{ e.event_type }}</td>
            <td>{{ e.entity_type }}#{{ e.entity_id }}</td>
            <td>{{ e.action }}</td>
            <td>{{ e.actor ?? '—' }}</td>
            <td><code>{{ JSON.stringify(e.payload ?? {}) }}</code></td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">暂无审计记录。</p>
      </div>
    </template>

    <h2 class="group-head">临时远程访问</h2>

    <!-- 临时远程访问（ADR-125）：按需开启的 Cloudflare Quick Tunnel，只代理内置
         Web 容器，默认 60 分钟自动过期，服务重启后不会自动恢复。 -->
    <div class="card" style="margin-top: 14px">
      <h3>临时远程访问</h3>
      <p class="muted" style="margin-top: 0">
        <span class="badge">开发 / 测试工具</span>
        只在临时 UX 测试、远程演示和故障排查时打开：开启后把本项目的 Web 界面发布成临时公网地址，
        最多运行 {{ Math.round(temporaryAccess.max_duration_seconds / 60) }} 分钟，关闭或到期后地址立即失效；
        隧道只指向内置 Web 服务，不开放任何新端口。
      </p>

      <p v-if="!temporaryAccess.enabled" class="muted">
        状态：未开启　
        <span class="muted">（此部署已通过 TEMPORARY_ACCESS_ENABLED=false 关闭该功能）</span>
      </p>

      <template v-else>
        <p>
          状态：
          <span v-if="temporaryAccess.status === 'disabled'" class="muted">未开启</span>
          <span v-else-if="temporaryAccess.status === 'starting'" class="wait">正在启动……</span>
          <span v-else-if="temporaryAccess.status === 'active'" class="pos">● 已开启</span>
          <span v-else-if="temporaryAccess.status === 'stopping'" class="wait">正在关闭……</span>
          <span v-else class="error">启动失败</span>
        </p>

        <p v-if="temporaryAccess.status === 'starting'" class="muted">
          正在等待 Cloudflare Tunnel 地址……
        </p>

        <template v-if="temporaryAccess.status === 'active' && temporaryAccess.url">
          <p style="margin-bottom: 4px">访问地址：</p>
          <p><code>{{ temporaryAccess.url }}</code></p>
          <p class="muted">
            剩余时间：{{ tunnelClock }}（到期自动关闭）
          </p>
          <div class="row">
            <button class="ghost" @click="copyTunnelUrl()">
              {{ tunnelCopied ? '已复制' : '复制地址' }}
            </button>
            <button class="danger" :disabled="tunnelBusy" @click="stopTunnel()">关闭临时访问</button>
          </div>
          <p class="notice warn" style="margin-top: 12px">
            ⚠️ 此地址将在关闭或自动过期后失效，请勿长期公开分享。
          </p>
        </template>

        <template v-else-if="temporaryAccess.status === 'disabled'">
          <div class="row">
            <button :disabled="tunnelBusy" @click="startTunnel()">开启临时访问</button>
          </div>
          <p v-if="temporaryAccess.detail" class="muted" style="margin-top: 10px">
            {{ temporaryAccess.detail }}
          </p>
        </template>

        <template v-else-if="temporaryAccess.status === 'error'">
          <p class="error">错误信息：{{ temporaryAccess.detail ?? '未知错误' }}</p>
          <div class="row">
            <button :disabled="tunnelBusy" @click="startTunnel()">重试</button>
            <button class="ghost" :disabled="tunnelBusy" @click="stopTunnel()">清除状态</button>
          </div>
        </template>
      </template>

      <p class="muted" style="margin-bottom: 0">
        隧道由后端进程按需启动，关闭、超时或服务重启都会终止它；不会自动恢复，也不会代理 NAS
        上的其它服务。
      </p>
    </div>

    <h2 class="group-head">安全</h2>

    <div class="card" style="margin-top: 14px">
      <h3>安全边界</h3>
      <ul class="muted" style="margin: 0; padding-left: 18px">
        <li>V1 不提供任何券商下单接口，也不会自动执行真实交易。</li>
        <li>API Key 加密存储、只写入不回显；审计记录也绝不包含密钥。</li>
        <li>AI 只能解释引擎算出的事实，不得生成价格、收益率、胜率等数字。</li>
        <li>GitHub 导入的代码视为不可信输入，V1 只做文本与结构分析，不执行。</li>
        <li>策略版本与已完成的回测结果不可修改，历史结论永远可复现。</li>
      </ul>
    </div>
  </div>
</template>
