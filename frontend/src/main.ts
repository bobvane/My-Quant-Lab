import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import DashboardView from './views/DashboardView.vue'
import ResearchView from './views/ResearchView.vue'
import StrategiesView from './views/StrategiesView.vue'
import BacktestView from './views/BacktestView.vue'
import PaperView from './views/PaperView.vue'
import SignalsView from './views/SignalsView.vue'
import DataView from './views/DataView.vue'
import SettingsView from './views/SettingsView.vue'
import StrategyDetailView from './views/StrategyDetailView.vue'
import LabView from './views/LabView.vue'
import ExperimentsView from './views/ExperimentsView.vue'
import NotFoundView from './views/NotFoundView.vue'
import './style.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: DashboardView },
    { path: '/lab', name: 'lab', component: LabView },
    { path: '/strategies', name: 'strategies', component: StrategiesView },
    { path: '/research', name: 'research', component: ResearchView },
    { path: '/backtest', name: 'backtest', component: BacktestView },
    { path: '/experiments', name: 'experiments', component: ExperimentsView },
    { path: '/signals', name: 'signals', component: SignalsView },
    { path: '/paper', name: 'paper', component: PaperView },
    { path: '/data', name: 'data', component: DataView },
    { path: '/settings', name: 'settings', component: SettingsView },
    // The old name of the strategy library. It had been the only name, so it stays
    // as a redirect rather than 404ing on every bookmark (ADR-131): a redirect has
    // no navigation entry of its own, so it is not an eleventh row in the tree.
    { path: '/market', redirect: '/strategies' },
    // The nine-part strategy detail page: a detail route reached from /strategies, not
    // an eleventh navigation entry (docs/13_UI_UX.md §1 and §3, ADR-114).
    { path: '/strategy/:strategyId', name: 'strategy-detail', component: StrategyDetailView },
    // A wrong address used to render an empty RouterView: no page, no explanation, and no
    // way to tell "still loading" from "this link is wrong" (ADR-216). The catch-all is
    // last on purpose so every real route above wins.
    { path: '/:pathMatch(.*)*', name: 'not-found', component: NotFoundView },
  ],
})

createApp(App).use(router).mount('#app')
