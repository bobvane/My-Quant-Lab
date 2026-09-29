<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type PaperAccount } from '@/api'
import StatCard from '@/components/StatCard.vue'
import { formatDateTime, formatNumber, formatPercent, toneOf } from '@/format'

const accounts = ref<PaperAccount[]>([])
const error = ref('')
const name = ref('PA Strategy')
const cash = ref(100000)
const creating = ref(false)

async function load() {
  error.value = ''
  try {
    accounts.value = await api.paperAccounts()
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function create() {
  creating.value = true
  error.value = ''
  try {
    await api.createPaperAccount(name.value, cash.value)
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    creating.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h1 class="page-title">模拟盘</h1>
    <p class="page-sub">
      模拟账户使用虚拟资金，与 Ghostfolio 真实持仓完全隔离。V1 不包含任何券商下单接口。
    </p>

    <p v-if="error" class="error">{{ error }}</p>

    <div class="card">
      <h3>新建模拟账户</h3>
      <div class="row">
        <input v-model="name" style="max-width: 220px" placeholder="账户名称" />
        <input v-model.number="cash" type="number" style="max-width: 180px" />
        <button :disabled="creating" @click="create">创建</button>
      </div>
    </div>

    <div v-if="accounts.length" class="grid cols-3" style="margin-top: 14px">
      <StatCard
        v-for="a in accounts"
        :key="a.id"
        :label="a.name"
        :value="formatNumber(a.cash)"
        :tone="toneOf(a.cash - a.initial_cash)"
        :sub="`初始 ${formatNumber(a.initial_cash)} ${a.base_currency} · 盈亏 ${formatPercent((a.cash - a.initial_cash) / a.initial_cash)}`"
      />
    </div>

    <div v-if="accounts.length" class="card" style="margin-top: 14px">
      <h3>账户明细</h3>
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>名称</th>
            <th>初始资金</th>
            <th>当前现金</th>
            <th>状态</th>
            <th>重置次数</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="a in accounts" :key="a.id">
            <td>{{ a.id }}</td>
            <td>{{ a.name }}</td>
            <td>{{ formatNumber(a.initial_cash) }}</td>
            <td :class="toneOf(a.cash - a.initial_cash)">{{ formatNumber(a.cash) }}</td>
            <td>{{ a.status }}</td>
            <td>{{ a.reset_count }}</td>
            <td>{{ formatDateTime(a.created_at) }}</td>
          </tr>
        </tbody>
      </table>
      <p class="notice warn" style="margin-top: 12px">
        重置模拟账户会清空全部虚拟持仓与交易记录，并写入审计日志。真实账户不受任何影响。
      </p>
    </div>

    <p v-else class="muted" style="margin-top: 14px">还没有模拟账户，先创建一个吧。</p>
  </div>
</template>
