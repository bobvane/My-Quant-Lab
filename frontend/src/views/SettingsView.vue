<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '@/api'
import { formatDateTime } from '@/format'

const events = ref<Array<Record<string, unknown>>>([])
const environment = ref<Record<string, unknown>>({})
const error = ref('')

async function load() {
  error.value = ''
  try {
    const [audit, settings] = await Promise.all([api.audit(), api.settings()])
    events.value = audit.events
    environment.value = (settings.environment as Record<string, unknown>) ?? {}
  } catch (e) {
    error.value = (e as Error).message
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h1 class="page-title">系统与审计</h1>
    <p class="page-sub">密钥只写入、永不回显；所有关键操作都会留下审计记录。</p>

    <p v-if="error" class="error">{{ error }}</p>

    <div class="card">
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
        <li>AI 只能解释引擎算出的事实，不得生成价格、收益率、胜率等数字。</li>
        <li>GitHub 导入的代码视为不可信输入，V1 只做文本与结构分析，不执行。</li>
        <li>策略版本与已完成的回测结果不可修改，历史结论永远可复现。</li>
      </ul>
    </div>
  </div>
</template>
