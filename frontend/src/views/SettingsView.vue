<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import {
  api,
  ApiError,
  type AIProviderRecord,
  type AIStatus,
  type AppSettingsEnvironment,
  type HealthResponse,
  type NotificationConfig,
  type NotificationTestResult,
  type ProviderTestResult,
  type SystemInfo,
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
// 版本号在构建期注入（评审报告 P0-1），与侧边栏、页脚显示同一个值。
const APP_VERSION = __APP_VERSION__

// 这一页原本是一条很长的滚动条（评审报告 P1-9），现在按用途分成四个标签页。
// 用 `v-show` 而不是 `v-if`：切标签不重新请求，也不会动到各分组自己的
// `isAdvanced` 门（高级模式该显示的读数一个都不会少）。
type SettingsTab = 'ai' | 'notify' | 'system' | 'runtime'
const SETTINGS_TABS: Array<{ id: SettingsTab; label: string; hint: string }> = [
  { id: 'ai', label: 'AI 设置', hint: '模型服务与解释提示词' },
  { id: 'notify', label: '通知', hint: '邮件 / Webhook 与测试' },
  { id: 'system', label: '系统设置', hint: '运行参数与安全边界' },
  { id: 'runtime', label: '运行与审计', hint: '版本、环境与审计日志' },
]
const activeTab = ref<SettingsTab>('ai')
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
const saving = ref(false)
const testingNew = ref(false)
const testResult = ref<ProviderTestResult | null>(null)
const testingId = ref<number | null>(null)
const busyId = ref<number | null>(null)
// provider 表格里那三个按钮的结果显示在表格正下方。这一页顶部的横幅在按钮滚出视野时
// 等于没有反馈，而「删除被拒绝」恰恰是最需要当场说清楚的一种结果：历史记录必须保留，
// 所以正确做法是停用而不是删除（ADR-083）。
const providerError = ref('')
const providerNotice = ref('')
// 模型目录那一列「状态 / 操作」的反馈同理：停用模型被拒绝时必须写在模型表旁边，
// 而不是页面顶部。两层开关（供应商、模型）独立，各自的反馈也各自留痕（ADR-173）。
const modelError = ref('')
const modelNotice = ref('')
const modelBusyId = ref<number | null>(null)
// 「当前实际路由」只能由后端回答：它要看供应商与模型两层开关叠加后的结果，
// 前端自己推会出现「目录写着启用、实际没在用」这类误读（ADR-173）。
const aiRoute = ref<AIStatus | null>(null)

// --- AI 模型发现与选择（ADR-176）---------------------------------------------
// 「读取/加载模型」把 GET {base_url}/models 的**全量**结果拿回来（后端不再截断 40
// 条），用户搜索、勾选、再保存。勾选才是入库的唯一入口：发现到 500 个模型不等于
// 建 500 条记录。手动添加的条目按原始文本交给服务端解析，因为真实模型 ID 里就有
// 冒号（`openrouter/free`、`google/gemma-4-31b-it:free`），前端 split(':') 会改名。
//
// discoveryTargetId：0 = 还没保存的新 provider 表单，>0 = 某个已保存 provider。
const discoveryTargetId = ref<number | null>(null)
const discovered = ref<string[]>([])
const discoverySearch = ref('')
const discoveryPicked = ref<string[]>([])
const manualEntries = ref<string[]>([])
const manualDraft = ref('')
const discoveryNotice = ref('')
const loadingModels = ref(false)
const savingModels = ref(false)

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
      systemHealth,
      systemInfo,
      aiStatusResult,
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
      // /health asks PostgreSQL, Redis and the Celery workers, so on a bare
      // install it can take seconds; it fills its own card when it arrives
      // instead of holding the rest of the page hostage (ADR-069).
      api.health().catch(() => note('系统信息') ?? null),
      api.systemInfo().catch(() => note('系统信息') ?? null),
      // 这一行只读 /ai/status，不做任何推断：供应商与模型两层开关叠加后的结果只有
      // 后端知道（ADR-173）。
      api.aiStatus().catch(() => note('AI 路由状态') ?? null),
    ])
    health.value = systemHealth
    if (!systemHealth) healthError.value = '健康检查没有响应'
    serverInfo.value = systemInfo
    aiRoute.value = aiStatusResult ?? null
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

// 发现面板当前对准的 provider（null = 面板关闭，0 表示还没保存的新表单）。
const discoveryProvider = computed(() =>
  discoveryTargetId.value ? providers.value.find((p) => p.id === discoveryTargetId.value) ?? null : null,
)

// 可选列表 = 这次读回来的模型 ∪ 该 provider 已有的条目。并集是必要的：已有条目即使
// 不在这次的 /models 结果里（比如供应商下架了它），也必须能看见、能被重新勾选。
const discoveryOptions = computed(() => {
  const existing = discoveryProvider.value?.models.map((m) => m.model_name) ?? []
  const all = [...new Set([...discovered.value, ...existing])]
  const q = discoverySearch.value.trim().toLowerCase()
  return q ? all.filter((name) => name.toLowerCase().includes(q)) : all
})

// 已勾选但不在当前搜索结果里的，也要在「已选」计数里算上，否则用户搜完一轮就以为
// 选择丢了。
const pickedCount = computed(() => discoveryPicked.value.length)

function isPicked(name: string): boolean {
  return discoveryPicked.value.includes(name)
}

function togglePick(name: string) {
  discoveryPicked.value = isPicked(name)
    ? discoveryPicked.value.filter((n) => n !== name)
    : [...discoveryPicked.value, name]
}

function pickAllVisible() {
  discoveryPicked.value = [...new Set([...discoveryPicked.value, ...discoveryOptions.value])]
}

function clearVisible() {
  const visible = new Set(discoveryOptions.value)
  discoveryPicked.value = discoveryPicked.value.filter((n) => !visible.has(n))
}

function addManualEntry() {
  const text = manualDraft.value.trim()
  if (!text) return
  // 原样保留：`google/gemma-4-31b-it:free` 必须一字不改地进库（ADR-176）。
  if (!discoveryPicked.value.includes(text) && !manualEntries.value.includes(text)) {
    manualEntries.value = [...manualEntries.value, text]
  }
  manualDraft.value = ''
}

function dropManualEntry(text: string) {
  manualEntries.value = manualEntries.value.filter((t) => t !== text)
}

/** 打开发现面板。target 为 null 表示对准「还没保存」的新表单。 */
function openDiscovery(target: AIProviderRecord | null) {
  discoveryTargetId.value = target ? target.id : 0
  discovered.value = []
  discoverySearch.value = ''
  manualEntries.value = []
  manualDraft.value = ''
  discoveryNotice.value = ''
  // 已有的 active 模型预先勾上：保存就是「当前目录」的样子，取消勾选 = 停用。
  discoveryPicked.value = target
    ? target.models.filter((m) => m.is_active).map((m) => m.model_name)
    : []
}

function closeDiscovery() {
  discoveryTargetId.value = null
}

async function loadModels() {
  error.value = ''
  discoveryNotice.value = ''
  const target = discoveryTargetId.value
  if (!target && (!baseUrl.value.trim() || !apiKey.value.trim())) {
    discoveryNotice.value = '请先填写 base_url 与 API Key 再读取模型'
    return
  }
  loadingModels.value = true
  try {
    const result = target
      ? await api.testAiProvider(target)
      : await api.testNewAiProvider(baseUrl.value.trim(), apiKey.value.trim())
    discovered.value = result.models_found
    discoveryNotice.value = result.ok
      ? `读取到 ${result.models_total} 个模型：搜索后勾选，再点「保存模型选择」。`
      : `读取失败：${result.detail}`
  } catch (e) {
    discoveryNotice.value = (e as Error).message
  } finally {
    loadingModels.value = false
  }
}

function selectionPayload(): { models: Array<Record<string, unknown>>; manual: string[] } {
  return {
    models: discoveryPicked.value.map((name) => ({ model_name: name })),
    manual: manualEntries.value,
  }
}

/** 发现面板是否对准「还没保存的新 provider」（表单场景）。 */
const discoveryForNewProvider = computed(() => !discoveryTargetId.value)

/** 新 provider 场景这个按钮会一并创建 provider，文案要如实说明（ADR-178）。 */
const discoverySaveLabel = computed(() => {
  if (savingModels.value || saving.value) {
    return discoveryForNewProvider.value ? '创建中…' : '保存中…'
  }
  return discoveryForNewProvider.value ? '创建 Provider 并保存模型' : '保存模型选择'
})

async function saveModels() {
  const target = discoveryTargetId.value
  error.value = ''
  discoveryNotice.value = ''
  if (!target) {
    // 面板对准的是还没保存的新 provider：这里就把 provider 连同勾选/手动模型一起建出来，
    // 不再要求用户回去点表单下方的「添加」（ADR-178）。没有勾选也允许创建——发现不是创建前提。
    const selected = discoveryPicked.value.length + manualEntries.value.length
    const created = await save()
    info.value = created
      ? selected > 0
        ? `Provider 已创建，并保存 ${selected} 个模型`
        : 'Provider 已创建'
      : ''
    if (!created) {
      // 失败原因本来只在表单顶部的 error 里，面板也要显示，否则又变成「点了没反应」。
      discoveryNotice.value = error.value || '创建失败：请检查名称、base_url 与 API Key'
    }
    return
  }
  savingModels.value = true
  try {
    const { models, manual } = selectionPayload()
    await api.saveAiProviderModels(target, { models, manual_models: manual })
    discoveryNotice.value = `已保存：${pickedCount.value} 个勾选模型已启用，未勾选的保留为停用（不会删除）`
    await load()
    closeDiscovery()
  } catch (e) {
    discoveryNotice.value = (e as Error).message
  } finally {
    savingModels.value = false
  }
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

/**
 * 创建 provider。表单上的「添加」与发现面板在**新 provider** 场景下的「创建 Provider 并
 * 保存模型」都走这里；返回是否真的创建成功，调用方据此决定提示什么（ADR-178）。
 */
async function save(): Promise<boolean> {
  error.value = ''
  info.value = ''
  if (!providerName.value.trim() || !baseUrl.value.trim() || !apiKey.value.trim()) {
    error.value = '名称、base_url、API Key 都是必填项'
    return false
  }
  saving.value = true
  try {
    const { models, manual } = selectionPayload()
    const created = await api.createAiProvider({
      name: providerName.value.trim(),
      base_url: baseUrl.value.trim(),
      api_key: apiKey.value.trim(),
      default_model: defaultModel.value.trim() || undefined,
      daily_budget_usd: budget.value,
      models,
      manual_models: manual,
    })
    info.value = `已添加 provider「${created.name}」，密钥已加密存储（${created.key_masked}）`
    // The key only ever lives on the server: clear it from the form immediately.
    apiKey.value = ''
    providerName.value = ''
    defaultModel.value = ''
    testResult.value = null
    closeDiscovery()
    await load()
    return true
  } catch (e) {
    error.value = (e as Error).message
    return false
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
  providerError.value = ''
  providerNotice.value = ''
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

// 模型行的「启用 / 停用」。供应商已停用时，模型仍然可以被单独设置——两层开关互不
// 联动（ADR-173）；状态列会说明它当前为什么不在路由里。
function modelState(row: Record<string, any>): string {
  if (!row.provider_is_active) return '供应商已停用'
  return row.is_active ? '启用' : '停用'
}

async function toggleModelActive(row: Record<string, any>) {
  modelError.value = ''
  modelNotice.value = ''
  modelBusyId.value = Number(row.id)
  const id = Number(row.id)
  const next = !row.is_active
  try {
    await api.updateAiModel(id, next)
    modelNotice.value = `${next ? '已启用' : '已停用'}模型「${row.model_name}」`
    await load()
  } catch (e) {
    // 409 = 这是该供应商最后一个可用模型，而且它就是 default_model。后端拒绝而不是
    // 偷偷改 default_model（ADR-173），所以这里必须把下一步动作说清楚。
    modelError.value =
      e instanceof ApiError && e.status === 409
        ? `不能停用模型「${row.model_name}」：它是供应商「${row.provider}」当前唯一可用模型。请先启用另一个模型并把默认模型换成它，或者直接停用整个供应商。`
        : (e as Error).message
  } finally {
    modelBusyId.value = null
  }
}

const routeSummary = computed(() => {
  const status = aiRoute.value
  // null 与「未配置」是两件事：前者是没读到，后者是后端明确回答没有可路由的模型。
  if (!status) return '未知（状态读取失败）'
  if (!status.configured) return '未配置'
  return `${status.provider_name || '—'} / ${status.model || '—'}`
})

async function remove(row: AIProviderRecord) {
  const ok = window.confirm(
    `确定删除 provider「${row.name}」？它的模型配置会一并删除，且不可恢复。已有的 AI 调用记录、用量与成本历史不会被删除，历史页面仍会显示「${row.name}」。`,
  )
  if (!ok) return
  error.value = ''
  info.value = ''
  providerError.value = ''
  providerNotice.value = ''
  busyId.value = row.id
  try {
    await api.deleteAiProvider(row.id)
    providerNotice.value = `已删除 provider「${row.name}」及其模型配置；历史 AI 任务与用量记录保留`
    await load()
  } catch (e) {
    providerError.value = (e as Error).message
  } finally {
    busyId.value = null
  }
}

// 模型行的删除：删掉的只是当前配置。历史 AI 任务 / 用量里那个模型名称会被保留
// （ADR-177），所以这里不再有「有调用记录就删不掉」的说法。
async function removeModel(row: Record<string, any>) {
  const name = String(row.model_name)
  const ok = window.confirm(
    `确定删除模型「${name}」？只删除当前模型配置；历史 AI 任务与用量记录会保留这个模型名称。`,
  )
  if (!ok) return
  modelError.value = ''
  modelNotice.value = ''
  modelBusyId.value = Number(row.id)
  try {
    await api.deleteAiModel(Number(row.id))
    modelNotice.value = `已删除模型「${name}」（历史记录保留）`
    await load()
  } catch (e) {
    modelError.value = (e as Error).message
  } finally {
    modelBusyId.value = null
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

    <div class="tabs" role="tablist">
      <button
        v-for="tab in SETTINGS_TABS"
        :key="tab.id"
        class="ghost tab"
        :class="{ on: activeTab === tab.id }"
        role="tab"
        :aria-selected="activeTab === tab.id"
        @click="activeTab = tab.id"
      >
        {{ tab.label }}
        <small>{{ tab.hint }}</small>
      </button>
    </div>

    <div v-show="activeTab === 'ai'">
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
        <input v-model.number="budget" type="number" step="0.5" min="0" style="max-width: 130px" />
        <button class="ghost" :disabled="testingNew" @click="testBeforeSave">
          {{ testingNew ? '测试中…' : '测试连接' }}
        </button>
        <button class="ghost" @click="openDiscovery(null)">读取/加载模型</button>
        <button :disabled="saving" @click="save">{{ saving ? '保存中…' : '添加' }}</button>
      </div>

      <p v-if="testResult" :class="testResult.ok ? 'notice' : 'error'">
        {{ testResult.ok ? '连接成功' : '连接失败' }}：{{ testResult.detail }}
        <span v-if="testResult.models_total" class="muted">
          （实际 {{ testResult.models_total }} 个模型；点「读取/加载模型」可搜索并勾选）
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
              <button class="ghost" @click="openDiscovery(p)">模型</button>
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

      <p v-if="providerError" class="error" style="margin-top: 8px">{{ providerError }}</p>
      <p v-if="providerNotice" class="notice" style="margin-top: 8px">{{ providerNotice }}</p>

      <div v-if="discoveryTargetId !== null" class="card" style="margin-top: 12px">
        <h4 style="margin: 0 0 6px">
          模型发现与选择 ——
          {{ discoveryProvider ? discoveryProvider.name : '新 provider（保存时一并写入）' }}
        </h4>
        <div class="row" style="margin-bottom: 8px">
          <button class="ghost" :disabled="loadingModels" @click="loadModels">
            {{ loadingModels ? '读取中…' : '读取/加载模型' }}
          </button>
          <input v-model="discoverySearch" style="max-width: 220px" placeholder="搜索模型（如 gemma）" />
          <span class="muted">已选择 {{ pickedCount }}</span>
          <button class="ghost" @click="pickAllVisible">全选当前搜索结果</button>
          <button class="ghost" @click="clearVisible">取消当前搜索结果</button>
        </div>
        <p v-if="discoveryNotice" class="muted" style="margin: 4px 0">{{ discoveryNotice }}</p>

        <div
          style="
            max-height: 260px;
            overflow: auto;
            border: 1px solid rgba(127, 127, 127, 0.25);
            border-radius: 6px;
            padding: 6px 10px;
          "
        >
          <label
            v-for="name in discoveryOptions"
            :key="name"
            style="display: block; font-size: 13px; padding: 2px 0"
          >
            <input
              type="checkbox"
              :checked="isPicked(name)"
              style="width: auto; margin-right: 6px"
              @change="togglePick(name)"
            />
            <code>{{ name }}</code>
          </label>
          <p v-if="!discoveryOptions.length" class="muted">
            还没有可选模型：先点「读取/加载模型」，或者用下面的输入框手动添加。
          </p>
        </div>

        <div class="row" style="margin-top: 8px; margin-bottom: 8px">
          <input
            v-model="manualDraft"
            style="max-width: 320px"
            placeholder="手动添加模型 ID（如 google/gemma-4-31b-it:free）"
            @keyup.enter="addManualEntry"
          />
          <button class="ghost" @click="addManualEntry">+</button>
        </div>
        <p v-if="manualEntries.length" class="muted" style="margin: 4px 0">
          手动添加（保存后写入）：
          <span v-for="entry in manualEntries" :key="entry" style="margin-right: 10px">
            <code>{{ entry }}</code>
            <button class="ghost" @click="dropManualEntry(entry)">×</button>
          </span>
        </p>

        <div class="row" style="margin-top: 8px">
          <button :disabled="savingModels || saving" @click="saveModels">
            {{ discoverySaveLabel }}
          </button>
          <button class="ghost" @click="closeDiscovery">取消</button>
        </div>
        <p class="muted" style="margin-top: 6px">
          只有勾选（或手动添加）的模型会进入目录并启用；取消勾选只会把已有模型设为停用，不会删除。
        </p>
      </div>

      <p class="muted" style="margin-top: 8px">
        模型 ID 原样保存，含 <code>:</code> 也保留（如 <code>openrouter/free</code>、
        <code>google/gemma-4-31b-it:free</code>）。手动添加时可选带上 MQL 字段
        <code>模型名:能力档:输入价:输出价</code>（如 <code>gpt-4o-mini:cheap:0.15:0.6</code>）；
        路由按任务能力档 + 成本 + 预算选模型。
      </p>
    </div>

    <p v-if="!isAdvanced" class="muted">
      模型目录、提示词模板、用量与任务记录都是排查用的读数，在高级模式下显示；普通模式下只要上面这一张卡填好就能用 AI 解释了。
    </p>

    <template v-if="isAdvanced">
    <div class="card" style="margin-top: 14px">
      <h3>AI 模型目录（路由用）</h3>
      <p class="muted" style="margin-top: 4px">当前实际路由：{{ routeSummary }}</p>
      <table v-if="aiModels.length">
        <thead>
          <tr>
            <th>供应商</th>
            <th>模型</th>
            <th>能力档</th>
            <th>输入/输出价 (per Mtok)</th>
            <th>状态</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in aiModels" :key="String(m.id)">
            <td>{{ m.provider }}</td>
            <td>{{ m.model_name }}</td>
            <td>{{ m.capability_tier }}</td>
            <td class="muted">{{ formatNumber(m.input_cost_per_mtok, 3) }} / {{ formatNumber(m.output_cost_per_mtok, 3) }}</td>
            <td>{{ modelState(m) }}</td>
            <td>
              <button
                class="ghost"
                :disabled="modelBusyId === Number(m.id)"
                @click="toggleModelActive(m)"
              >
                {{ m.is_active ? '停用' : '启用' }}
              </button>
              <button class="ghost" :disabled="modelBusyId === Number(m.id)" @click="removeModel(m)">
                删除
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">还没有模型条目。</p>
      <p v-if="modelError" class="error" style="margin-top: 8px">{{ modelError }}</p>
      <p v-if="modelNotice" class="notice" style="margin-top: 8px">{{ modelNotice }}</p>
      <p class="muted" style="margin-top: 8px">
        一个模型要真正参与路由，需要它自己启用、它的供应商也启用，并且供应商的 API Key 可用；停用不会删除模型，历史 AI 任务与用量都保留。删除只移除当前模型配置，历史记录里的模型名称同样保留。
      </p>
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
            <th>模型</th>
            <th>任务</th>
            <th>调用</th>
            <th>Tokens</th>
            <th>费用 USD</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in aiUsage" :key="String(u.id)">
            <td>{{ String(u.usage_date).slice(0, 10) }}</td>
            <td>{{ u.provider_name || u.provider_id || '—' }}</td>
            <td>{{ u.model_name || '—' }}</td>
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
              <td>
                {{ taskDetail.provider_name ?? '—' }} / {{ taskDetail.model_name ?? '—' }}
              </td>
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

    </template>

    </div>

    <div v-show="activeTab === 'notify'">
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

    </div>

    <div v-show="activeTab === 'system'">
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

    </div>

    <div v-show="activeTab === 'runtime'">
    <template v-if="isAdvanced">
      <h2 class="group-head">系统信息</h2>

      <div class="card" style="margin-top: 14px">
        <h3>系统信息</h3>
        <p class="muted">
          这一组读数回答的是「软件本身怎么样」：服务是否健康、跑的是哪个版本、引擎与特征版本、
          行情源和 DSL Schema。它原来挤在首页第一屏，但那块地方应该回答「我现在该做什么」，
          所以搬到这里。
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
            :value="APP_VERSION"
            :sub="`引擎 ${health?.engine_version ?? '—'} · 后端自报 ${health?.version ?? '—'}`"
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

    </div>

    <div v-show="activeTab === 'system'">
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
  </div>
</template>
