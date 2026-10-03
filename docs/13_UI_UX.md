# 13 UI/UX Specification

本文件描述界面**现在**长什么样，以及哪些还只是计划。每一节都带一行 `状态：`，取值只有三种：

- `状态：已实现（<路径>）` —— 括号里点名交付它的文件，必须真的存在；一个已实现的承诺必须指得出代码。
- `状态：部分实现（缺少 <清单>）` —— 已经做到的部分照实写，缺的部分写在同一行里。
- `状态：尚未实现（<计划或「未安排」>）` —— 只出现在本文件末尾的欠账一节，正文不再用将来时描述不存在的东西。

守卫 `backend/tests/test_ui_promises.py` 把第 1 节的导航树与 `frontend/src/main.ts:16-22` 的路由、`frontend/src/App.vue:60-68` 的导航标签逐条对照，并核对每个 `已实现` 点名的文件存在 —— 所以这份文件不会再悄悄承诺一个不存在的页面（ADR-107）。

## 1. Navigation

状态：已实现（frontend/src/App.vue, frontend/src/main.ts）

左栏导航与路由一一对应，共 7 条：

```text
研究仪表盘        /
行情与策略        /market
信号              /signals
回测实验室        /backtest
模拟盘            /paper
系统资源          /resources
系统与审计        /settings
```

早期版本在这一节画过一棵更长的树：Strategies 下挂 Strategy Library、GitHub Sources、Experimental、Strategy Detail，另有两个顶级项 Portfolio Context 与 Data Health。这些**都不是独立页面**：策略库、GitHub 导入、策略血统与版本、组合概览（Ghostfolio 上下文）、数据健康分别作为 `/market` 与 `/` 的区块存在。本文件按现状记录，不再列不存在的页面。

## 2. Dashboard

状态：已实现（frontend/src/views/DashboardView.vue）

`/` 从上到下：

### 顶部状态卡
- 版本与引擎/特征版本
- 数据库与市场数据状态
- 数据新鲜度

### 我的 Ghostfolio 持仓
- 当前市值与权重
- 集中度提醒（真实持仓只读，与模拟盘完全隔离）

### 组合概览
### 数据健康
- 覆盖范围与最近一次同步情况
### 信号扫描（只用已收盘 K 线）
### 模拟账户（与真实持仓完全隔离）
### 系统构成

## 3. Strategy Detail

状态：部分实现（缺少 独立的策略详情页：Overview、Rules、Current Signals、AI Explanation 四段在 `/market` 上没有对应区块）

策略的细节目前分布在三个页面，而不是一个九段的详情页：

- 策略库与策略血统（Provenance）：`frontend/src/views/StrategiesView.vue` 的「策略血统」区块
- 版本历史（Version History）与生命周期：「策略 #<id> 版本」「策略生命周期（基于证据，无 AI 介入）」
- 规则（Rules）：同页的「策略 DSL（声明式，JSON 形式）」
- 回测与样本外/滚动验证（Backtest、OOS / Walk-forward）：`frontend/src/views/BacktestView.vue`
- 模拟盘（Paper Trading）：`frontend/src/views/PaperView.vue`
- 当前信号与 AI 解释（Current Signals、AI Explanation）：`frontend/src/views/SignalsView.vue`

九段式详情页仍是计划，尚未实现。

## 4. Backtest Lab

状态：已实现（frontend/src/views/BacktestView.vue）

顶部配置：strategy、symbol(s)、timeframe、date range、initial capital、fees、slippage。

结果区：权益曲线、回撤曲线、指标明细（metric cards）、交易明细、月度/年度视图、OOS 分割、滚动 Walk-Forward、参数敏感性、Monte Carlo 重采样、策略集成与投票阈值扫描、参数信息、AI 解读（只解释已有数字）。

「查看假设」是一个独立区块，列出这次回测实际使用的成交模型、订单类型与有效期、手续费、滑点；结果可复现性区块列出结果哈希、数据集哈希、引擎版本、特征版本。两者相邻，因为假设本来就是复现记录的一部分（ADR-107）。

## 5. Paper Trading

状态：已实现（frontend/src/views/PaperView.vue）

`/paper` 有：新建模拟账户、账户明细（cash / equity / 未实现与已实现盈亏）、持仓、执行信号（虚拟成交）、绩效（期末权益、总收益、最大回撤、胜率）、**权益曲线**与**最新信号**两个区块。

权益曲线由账户明细里的「权益曲线」按钮载入，调用 `GET /paper/accounts/{id}/equity` 的 `equity_curve`：起点是账户创建（或上次重置）时的期初现金，入金/提现按发生时抬高或压低基准，已平仓交易逐个加上 `pnl`。所以入金是一级台阶而不是收益，最后一点等于净入金 + 已实现盈亏；`curve_note` 把这两条与「重置只覆盖当前生命周期」一并写在图下方（ADR-108）。

「最新信号」列出最近 10 条已持久化信号（ID、生成时间、资产、周期、方向、状态），每行可以直接对某个账户执行，不必手抄信号 ID。

## 6. Signal Detail

状态：部分实现（缺少 第 1 段「当前状态」、第 4 段「策略历史统计」、第 6 段「真实持仓上下文」、第 7 段「风险/失效条件」）

`/signals` 现在按「信号结果追踪」→「信号证据」→「AI 解释」三段呈现，覆盖了原计划的第 2 段（发生了什么：证据与指标）、第 3 段（为什么触发：规则命中与 AI 解释）、第 5 段（最近模拟情况：结果追踪）、第 8 段（专业技术数据：证据明细）。四个缺口见上面的状态行。

## 7. 初学者友好

状态：已实现（frontend/src/metrics.ts, frontend/src/components/MetricHint.vue, frontend/src/components/StatCard.vue）

不默认把公式墙推给用户：任何专业指标都能在旁边就地解释自己。`frontend/src/metrics.ts` 是唯一的事实来源，每个指标固定回答四条 —— 是什么 / 怎么算 / 为什么看它 / 注意什么；`frontend/src/components/MetricHint.vue` 把它渲染成指标名旁边的「四问」按钮（悬停先给出「是什么」，点开列出四条），`StatCard.vue` 让每一张指标卡自动带上它，回测里按指标名成行的表格（样本内 / 样本外）也逐行带上。不需要解释的标签必须显式列进 `metrics.ts` 的 `NOT_A_METRIC`（目前只有「系统状态」与「版本」两个读数），否则守卫会让它红 —— 新指标要么解释自己，要么被点名承认自己不是指标。

## 8. GitHub Import

状态：部分实现（缺少 七步向导：Repository → Analysis → Detected Strategies → Warnings → DSL Preview → Validation → Import）

`/market` 的「从 GitHub 导入（只读分析，不执行仓库代码）」区块把分析结果、检测到的策略、警告与导入放在一个页面里一次呈现，「已导入来源（自动监视更新）」列出后续同步。导入仍然是只读分析：仓库代码永不执行。

## 9. Mobile/desktop

状态：已实现（frontend/src/style.css）

桌面仍然优先，但手机宽度可用：`frontend/src/style.css` 末尾的 `@media (max-width: 820px)` 是唯一的断点 —— 外壳从横排改为竖排，232px 的侧栏变成整宽横幅、导航变成可横向滑动的标签条，`/settings` 已经给过的引擎/特征/数据库读数在手机上隐藏，宽表格在页面内横向滚动而不是把整页撑开，主题按钮收进角落不再压住内容（ADR-111）。桌面布局不变。

## 10. 欠账（尚未实现的承诺，按本文件顺序）

- 独立的策略详情页与其中的 Overview、Rules、Current Signals、AI Explanation 四段（第 3 节）
- 信号页的第 1、4、6、7 段（第 6 节）
- GitHub 导入七步向导（第 8 节）

这一节是上面 `尚未实现` / `缺少` 的汇总视图；两份清单必须一致，守卫会核对。
