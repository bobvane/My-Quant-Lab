# 13 UI/UX Specification

本文件描述界面**现在**长什么样，以及哪些还只是计划。每一节都带一行 `状态：`，取值只有三种：

- `状态：已实现（<路径>）` —— 括号里点名交付它的文件，必须真的存在；一个已实现的承诺必须指得出代码。
- `状态：部分实现（缺少 <清单>）` —— 已经做到的部分照实写，缺的部分写在同一行里。
- `状态：尚未实现（<计划或「未安排」>）` —— 只出现在本文件末尾的欠账一节，正文不再用将来时描述不存在的东西。

守卫 `backend/tests/test_ui_promises.py` 把第 1 节的导航树与 `frontend/src/main.ts:17-23` 的路由、`frontend/src/App.vue:60-68` 的导航标签逐条对照，并核对每个 `已实现` 点名的文件存在 —— 所以这份文件不会再悄悄承诺一个不存在的页面（ADR-107）。

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

唯一的例外是第 3 节的策略详情页：它有自己的一条路由 `/strategy/:strategyId`（`frontend/src/main.ts:26`），但**不占导航**——它从 `/market` 策略库每一行的「详情」进入，所以上面的七条仍然是七条。守卫把带参数的路由与导航路由分开核对：详情路由不得改变导航树的行数，但必须在本文件里被点名（ADR-114）。

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

状态：已实现（frontend/src/views/StrategyDetailView.vue, frontend/src/main.ts, frontend/src/views/StrategiesView.vue）

`/strategy/:strategyId` 是独立页面，从 `/market` 策略库每一行的「详情」进入（`frontend/src/main.ts:26`），九段都在这一页上（ADR-114）：

1. 概览（Overview）：`GET /strategies/{id}` 的身份字段（ID、Slug、来源类型、状态与生命周期、版本数、创建时间），加上 `GET /lifecycle/strategies/{id}` 的当前阶段、证据支持的下一步、被挡原因、退步标记与逐门通过情况；没有证据支持任何下一步时就写没有，而不是替它建议一个。
2. 血统（Provenance）：来源类型/地址/许可/作者，以及每个版本的提交、来源地址、提示词版本与不可变哈希。没记下的写「未记录」，不猜。
3. 版本历史（Version History）：所有版本的 Schema、校验状态、是否当前、创建时间与哈希；「校验」按钮调用 `GET /strategies/versions/{version_id}/verify` 重新计算哈希并与存档比对，不一致就写「哈希不一致（文本已变）」。
4. 规则（Rules）：当前版本 DSL 的指标、入场、出场（`all`/`any` 组按策略自己的词渲染）、风险与执行参数——读的是策略自己说的话，不是回测的结论。
5. 回测（Backtest）：当前版本的回测运行列表与最近一次的关键读数（总收益率、最大回撤、夏普比率、胜率、期末权益）。没有运行时写「不是零，是还没有数据」，并把读者送到回测实验室。
6. 样本外与滚动验证（OOS / Walk-forward）：只从生命周期证据里读「已记录过几次」与最近一次摘要，并明说这些结果**不落库**——`POST /research/oos` 与 `POST /research/walk-forward` 按需计算，系统留下的只有审计事件，所以这一节不是一份完整评估。
7. 模拟盘（Paper Trading）：绑定到这个策略的账户（`GET /paper/accounts` 的 `strategy_id`）与它们的交易，并明说归因按账户：模拟盘交易只带账户与当时的策略版本名，系统不会把一笔成交倒推给某个策略。
8. 当前信号（Current Signals）：`GET /signals/preview/{strategy_version_id}` 的当前（未持久化）信号与 `GET /signals/evidence/{strategy_version_id}` 的五层确定性证据（规则匹配、经验统计、模拟盘统计、组合上下文、信号意图）。预览需要标的，因为策略自己不点名任何标的，所以标的那一格是必填的。
9. AI 解释（AI Explanation）：`GET /ai/status` 说明是否配置；已配置时可以解释当前信号预览（`POST /signals/preview-explain`）或最近一次回测（`POST /backtests/{run_id}/explain`），只解释已有数字、不参与计算；未配置时明说未配置，并写明九段在没有任何 AI 的情况下全部可用。

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

状态：已实现（frontend/src/views/SignalsView.vue, frontend/src/api.ts）

`/signals` 的每一行都有「详情」按钮，打开四段此前一直缺的内容，和已有的三张卡片（信号结果追踪 / 信号证据 / AI 解释）合起来覆盖原计划的八段：

1. 当前状态：信号自己的时刻、状态、方向、参考价，加上**最新已收盘 K 线**（本地数据集里最新的一根，写明「不是实时报价」）、它的收盘价、相对参考价的变化，以及信号之后已收盘的 K 线根数（读到 120 根窗口上限时写「实际可能更多」）。数据来自 `GET /market-data/latest/{symbol}`。
2. 策略历史统计：该策略版本最新一次已完成回测的样本数、历史胜率、最大回撤、总收益、夏普与数据集版本，加上模拟盘的已平仓笔数与已实现盈亏，再加上该策略已经评估过的信号结果（并明确写出这是另一组样本，不要和回测混在一起）；没有完成的回测就写「不是零，是还没有数据」。数据来自 `GET /signals/evidence/{strategy_version_id}`（`api.strategyEvidence`）与 `/signals/outcome-summary` 的 `strategy:<name>` 分组。
3. 真实持仓上下文：信号自带的 `portfolio_context`（是否连上 Ghostfolio、持有数量与占比、note），加上策略证据里的组合快照（持仓数、总值、本标的持仓）；未配置就明说未配置，而不是留白。
4. 风险 / 失效条件：策略自己给出的止损与目标价位，以及它们相对参考价的距离；AI 解释点名过的失效条件只在**确实解释过**的时候列出，否则明说「这条信号还没有做过 AI 解释，上面的价位来自策略本身，不是 AI 的判断」。

## 7. 初学者友好

状态：已实现（frontend/src/metrics.ts, frontend/src/components/MetricHint.vue, frontend/src/components/StatCard.vue）

不默认把公式墙推给用户：任何专业指标都能在旁边就地解释自己。`frontend/src/metrics.ts` 是唯一的事实来源，每个指标固定回答四条 —— 是什么 / 怎么算 / 为什么看它 / 注意什么；`frontend/src/components/MetricHint.vue` 把它渲染成指标名旁边的「四问」按钮（悬停先给出「是什么」，点开列出四条），`StatCard.vue` 让每一张指标卡自动带上它，回测里按指标名成行的表格（样本内 / 样本外）也逐行带上。不需要解释的标签必须显式列进 `metrics.ts` 的 `NOT_A_METRIC`（目前只有「系统状态」与「版本」两个读数），否则守卫会让它红 —— 新指标要么解释自己，要么被点名承认自己不是指标。

## 8. GitHub Import

状态：已实现（frontend/src/views/StrategiesView.vue, frontend/src/api.ts）

`/market` 的「从 GitHub 导入（只读分析，不执行仓库代码）」区块是一份七步向导，步骤名与顺序固定：Repository → Analysis → Detected Strategies → Warnings → DSL Preview → Validation → Import（`const WIZARD_STEPS`）。每一步由它自己的证据解锁，而不是由一个按钮解锁（ADR-113）：

1. **Repository**：仓库地址、分支/tag、最多读取文件数与最长等待秒数、可选 token；地址为空时分析按钮是灰的。
2. **Analysis**：候选/读取/解析/登记的文件数、coverage 结论、解析失败与被跳过的文件明细。这一步只报告事实，判断对错留给用户。
3. **Detected Strategies**：导入器真的找到的东西 —— 指标、规则、参数三张表，加上无法映射的计数。它们是**扁平发现**（每个指标、规则、参数各自一条），不是「它认出了这是哪个策略」；把它说成策略识别就是替别人的代码下结论。
4. **Warnings**：分析留下的警告与不安全构造。没有不安全构造时这一步自动通过；有一个就必须由用户勾选「我已人工审查这 N 个不安全构造」，系统不替用户点这个勾。
5. **DSL Preview**：草案就是「策略 DSL」编辑器里的那份文本（同一个 draft，改它请回到那张卡）。这一步只检查它还是不是一个合法的 JSON 对象，并给出指标声明与入场条件的条数。
6. **Validation**：`POST /strategies/validate` 判定这份草案，逐条列出 `severity / code / message / path`，并列出校验器认识的列（`available_columns`），被否决的名字因此总能对照。校验说的是「这份文本此刻」：文本一改，结论立刻被丢掉，向导退回第 5 步、第 7 步重新上锁。
7. **Import**：名称与版本（留空 = 服务器分配，版本账本实时核对是否重名），只有校验通过且文本没有再改过时按钮才可用。导入把这个 commit 的结果写成一份不可变版本，来源、commit、哈希与校验状态一起存档。

导入全程只读，仓库代码永不执行；「已导入来源（自动监视更新）」列出后续同步。

## 9. Mobile/desktop

状态：已实现（frontend/src/style.css）

桌面仍然优先，但手机宽度可用：`frontend/src/style.css` 末尾的 `@media (max-width: 820px)` 是唯一的断点 —— 外壳从横排改为竖排，232px 的侧栏变成整宽横幅、导航变成可横向滑动的标签条，`/settings` 已经给过的引擎/特征/数据库读数在手机上隐藏，宽表格在页面内横向滚动而不是把整页撑开，主题按钮收进角落不再压住内容（ADR-111）。桌面布局不变。

## 10. 欠账（尚未实现的承诺，按本文件顺序）

（无）

这一节是上面 `尚未实现` / `缺少` 的汇总视图；两份清单必须一致，守卫会核对。截至 v1.7.8，本文件列出的承诺全部已实现：最后一条欠账（独立的策略详情页）由 v1.7.8 收下，这一节从此只保留一处说明——下面不再有「尚未实现」的正文承诺，新的计划写在别处，不写进这份按现状记录的规格。
