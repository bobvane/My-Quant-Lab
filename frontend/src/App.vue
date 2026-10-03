<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type HealthResponse } from '@/api'
import { formatNumber } from '@/format'

const health = ref<HealthResponse | null>(null)

type Theme = 'dark' | 'light'
const theme = ref<Theme>('dark')

function applyTheme(value: Theme) {
  theme.value = value
  document.documentElement.dataset.theme = value
  try {
    localStorage.setItem('mql-theme', value)
  } catch {
    /* storage may be unavailable (private mode) */
  }
}

function toggleTheme() {
  applyTheme(theme.value === 'dark' ? 'light' : 'dark')
}

// Initial theme: saved choice, else the OS preference.
try {
  const saved = localStorage.getItem('mql-theme') as Theme | null
  const prefersLight = window.matchMedia?.('(prefers-color-scheme: light)').matches
  applyTheme(saved ?? (prefersLight ? 'light' : 'dark'))
} catch {
  applyTheme('dark')
}

onMounted(async () => {
  try {
    health.value = await api.health()
  } catch {
    health.value = null
  }
})
</script>

<template>
  <div class="app-shell">
    <button
      class="theme-toggle ghost"
      type="button"
      :title="theme === 'dark' ? '切换到浅色' : '切换到深色'"
      @click="toggleTheme"
    >
      {{ theme === 'dark' ? '浅色' : '深色' }}
    </button>

    <aside class="sidebar">
      <div class="brand">
        My Quant Lab
        <small>个人量化策略实验室 · v{{ health?.version ?? '—' }}</small>
      </div>

      <nav class="nav">
        <RouterLink to="/">研究仪表盘</RouterLink>
        <RouterLink to="/market">行情与策略</RouterLink>
        <RouterLink to="/signals">信号</RouterLink>
        <RouterLink to="/backtest">回测实验室</RouterLink>
        <RouterLink to="/paper">模拟盘</RouterLink>
        <RouterLink to="/resources">系统资源</RouterLink>
        <RouterLink to="/settings">系统与审计</RouterLink>
      </nav>

      <div class="sidebar-meta muted" style="margin-top: 24px">
        <div>引擎 {{ health?.engine_version ?? '—' }}</div>
        <div>特征 {{ health?.feature_version ?? '—' }}</div>
        <div>数据库 {{ health?.database ?? '—' }}</div>
      </div>

      <p class="sidebar-note notice warn" style="margin-top: 20px">
        研究工具，不自动交易。<br />所有数字由量化引擎计算。
      </p>
    </aside>

    <main class="main">
      <RouterView />
      <footer class="muted" style="margin-top: 28px">
        My Quant Lab v{{ health?.version ?? '0.0.1' }} — 本项目仅用于策略研究、回测与模拟，
        不构成投资建议，也不会连接任何券商。
      </footer>
    </main>
  </div>
</template>
