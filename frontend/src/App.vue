<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type HealthResponse } from '@/api'
import { formatNumber } from '@/format'
import { initMode, isAdvanced, mode, setMode } from '@/mode'

const health = ref<HealthResponse | null>(null)

// 版本号在构建期注入（评审报告 P0-1）：不再等 `GET /health` 回来才有值，
// 所以侧边栏与页脚在任何页面、任何时刻都显示同一个版本。
const APP_VERSION = __APP_VERSION__

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

// 普通 / 高级模式（ADR-126）：默认是给人看的界面，工程读数留在高级模式。
// The attribute is set before the first paint so a reload never flashes the
// engineering rows on their way out.
initMode()

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
        <small>个人量化策略实验室 · v{{ APP_VERSION }}</small>
      </div>

      <nav class="nav">
        <RouterLink to="/">研究首页</RouterLink>
        <RouterLink to="/research">研究策略</RouterLink>
        <RouterLink to="/strategies">我的策略</RouterLink>
        <RouterLink to="/backtest">回测</RouterLink>
        <RouterLink to="/experiments">实验</RouterLink>
        <RouterLink to="/paper">模拟验证</RouterLink>
        <RouterLink to="/signals">信号</RouterLink>
        <RouterLink to="/data">数据</RouterLink>
        <RouterLink to="/lab">AI 研究实验室</RouterLink>
        <RouterLink to="/settings">系统管理</RouterLink>
      </nav>

      <div class="mode-switch" role="group" aria-label="使用模式">
        <button
          type="button"
          class="ghost"
          :class="{ on: mode === 'basic' }"
          :aria-pressed="mode === 'basic'"
          @click="setMode('basic')"
        >
          ○ 普通模式
        </button>
        <button
          type="button"
          class="ghost"
          :class="{ on: mode === 'advanced' }"
          :aria-pressed="mode === 'advanced'"
          @click="setMode('advanced')"
        >
          ● 高级模式
        </button>
      </div>
      <p class="muted mode-note">
        {{
          isAdvanced
            ? '高级模式：显示引擎版本、哈希与原始数据，方便排查问题。'
            : '普通模式：只显示做决定要看的数字。'
        }}
      </p>

      <div v-if="isAdvanced" class="sidebar-meta muted" style="margin-top: 24px">
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
        My Quant Lab v{{ APP_VERSION }} — 本项目仅用于策略研究、回测与模拟，
        不构成投资建议，也不会连接任何券商。
      </footer>
    </main>
  </div>
</template>
