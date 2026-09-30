import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import DashboardView from './views/DashboardView.vue'
import BacktestView from './views/BacktestView.vue'
import StrategiesView from './views/StrategiesView.vue'
import PaperView from './views/PaperView.vue'
import SettingsView from './views/SettingsView.vue'
import ResourcesView from './views/ResourcesView.vue'
import './style.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: DashboardView },
    { path: '/market', name: 'market', component: StrategiesView },
    { path: '/backtest', name: 'backtest', component: BacktestView },
    { path: '/paper', name: 'paper', component: PaperView },
    { path: '/resources', name: 'resources', component: ResourcesView },
    { path: '/settings', name: 'settings', component: SettingsView },
  ],
})

createApp(App).use(router).mount('#app')
