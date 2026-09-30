<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type AIProviderRecord, type ProviderTestResult } from '@/api'
import { formatDateTime, formatNumber } from '@/format'

const events = ref<Array<Record<string, unknown>>>([])
const environment = ref<Record<string, unknown>>({})
const error = ref('')
const info = ref('')

const providers = ref<AIProviderRecord[]>([])
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

async function load() {
  error.value = ''
  try {
    const [audit, settings, ai] = await Promise.all([
      api.audit(),
      api.settings(),
      api.aiProviders(),
    ])
    events.value = audit.events
    environment.value = (settings.environment as Record<string, unknown>) ?? {}
    providers.value = ai.providers
  } catch (e) {
    error.value = (e as Error).message
  }
}

function modelList(): Array<Record<string, unknown>> {
  return modelsCsv.value
    .split(',')
    .map((m) => m.trim())
    .filter(Boolean)
    .map((model_name) => ({ model_name }))
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

onMounted(load)
</script>

<template>
  <div>
    <h1 class="page-title">系统与审计</h1>
    <p class="page-sub">
      密钥只写入、永不回显；AI 只负责解释引擎算好的数字。所有关键操作都会留下审计记录。
    </p>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="info" class="notice">{{ info }}</p>

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
    </div>

    <div class="card" style="margin-top: 14px">
      <h3>运行环境</h3>
      <table>
        <tbody>
          <tr v-for="(value, key) in environment" :key="key">
            <td>{{ key }}</td>
            <td>{{ value }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="card" style="margin-top: 14px">
      <h3>审计日志（最近 {{ events.length }} 条）</h3>
      <table v-if="events.length">
        <thead>
          <tr>
            <th>时间</th>
            <th>事件</th>
            <th>对象</th>
            <th>动作</th>
            <th>详情</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in events" :key="String(e.id)">
            <td>{{ formatDateTime(String(e.created_at)) }}</td>
            <td>{{ e.event_type }}</td>
            <td>{{ e.entity_type }}#{{ e.entity_id }}</td>
            <td>{{ e.action }}</td>
            <td><code>{{ JSON.stringify(e.payload ?? {}) }}</code></td>
          </tr>
        </tbody>
      </table>
      <p v-else class="muted">暂无审计记录。</p>
    </div>

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
