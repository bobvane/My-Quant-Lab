# 13 UI/UX Specification

本文件描述界面**现在**长什么样，以及哪些还只是计划。每一节都带一行 `状态：`，取值只有三种：

- `状态：已实现（<路径>）` —— 括号里点名交付它的文件，必须真的存在；一个已实现的承诺必须指得出代码。
- `状态：部分实现（缺少 <清单>）` —— 已经做到的部分照实写，缺的部分写在同一行里。
- `状态：尚未实现（<计划或「未安排」>）` —— 只出现在本文件末尾的欠账一节，正文不再用将来时描述不存在的东西。

守卫 `backend/tests/test_ui_promises.py` 把第 1 节的导航树与 `frontend/src/main.ts:19-27` 的路由、`frontend/src/App.vue:66-79` 的导航标签逐条对照，并核对每个 `已实现` 点名的文件存在 —— 所以这份文件不会再悄悄承诺一个不存在的页面（ADR-107）。

## 1. Navigation

状态：已实现（frontend/src/App.vue, frontend/src/main.ts）

左栏导航与路由一一对应，共 9 条：

```text
研究首页          /
研究策略          /research
我的策略          /strategies
回测              /backtest
模拟验证          /paper
信号              /signals
数据              /data
系统资源          /resources
系统管理          /settings
```

导航按**用户要做的事**命名，不按模块命名：`/` 是每天打开的地方，`/research` 是从「我想研究 SPY」走到一次回测的四步入口，`/strategies` 是策略库（创建、选择、版本、导入），`/backtest` 是研究的主工具，`/paper` 是验证，`/signals` 是研究信号，`/data` 只管行情数据与数据质量，`/resources` 与 `/settings` 是运维读数。第 8 条「系统资源」只在高级模式出现，它的前面有一条只属于高级模式的「高级」分组标题；用 `v-if` 包起来的 `<RouterLink>` 仍然算在那九条里，因为路由与文档都没变（ADR-126、ADR-131）。

v1.9.3 把原来那一页「行情与策略」按评审 §7 拆成三页：`/research`（选标的、选策略、设少量参数、开始研究）、`/strategies`（策略库：我的策略、版本、创建与 GitHub 导入）、`/data`（同步行情、数据质量、删除与归档）。`/market` 保留为重定向到 `/strategies`：它是这套界面前一版唯一的名字，直接 404 会让旧书签与旧文档一起断掉，而重定向没有自己的导航条目，所以它不是第十行（ADR-131）。评审 §19 另外建议一条「高级工具」导航项，这一版**没有**为它新开页面：它点名的东西（样本外分割、滚动 Walk-Forward、参数敏感性、Monte Carlo、策略集成）都已经在 `/backtest` 的高级模式里，一条只负责把用户再送回回测页的导航条目正是「假装成页面的导航条目」（ADR-113），所以导航里只留一个「高级」分组标题，指向真正的高级读数 `/resources`（ADR-133）。

早期版本在这一节画过一棵更长的树：Strategies 下挂 Strategy Library、GitHub Sources、Experimental、Strategy Detail，另有两个顶级项 Portfolio Context 与 Data Health。这些**都不是独立页面**：策略库、GitHub 导入、策略血统与版本、组合概览（Ghostfolio 上下文）、数据健康分别作为 `/strategies` 与 `/` 的区块存在。本文件按现状记录，不再列不存在的页面。

唯一的例外是第 3 节的策略详情页：它有自己的一条路由 `/strategy/:strategyId`（`frontend/src/main.ts:34`），但**不占导航**——它从 `/strategies` 策略库每一行的「详情」进入，所以上面的九条仍然是九条。守卫把带参数的路由与导航路由分开核对：详情路由不得改变导航树的行数，但必须在本文件里被点名（ADR-114）。

## 2. Dashboard

状态：已实现（frontend/src/views/DashboardView.vue）

`/` 只回答四个问题，按顺序排在页首（ADR-127）：

1. **① 我在研究什么** —— `标的 · 周期 · 策略名` 一行（例：`SPY · 日线 · EMA 趋势策略`），下一行写当前阶段与已经回测过几次。研究中的那个策略＝最近创建的策略（`GET /strategies` 按创建时间倒序），它的版本、回测与生命周期分别来自 `GET /strategies/{id}/versions`、`GET /backtests?strategy_version_id=`、`GET /lifecycle/strategies/{id}`。一次都没有时写「还没有策略可选」，不编一个。
2. **② 最近一次研究结论** —— 一句人话：`这套策略在这段约 N 年的历史数据里整体是赚钱的：累计收益 +32.4%，最大回撤 -18.7%。`，下面一行给决定可信度的读数（胜率、交易笔数、历史回测已完成、样本外验证已完成/未完成）。回测还没跑完就写「还没有跑完」，没有回测就写「到「回测」运行一次」。
3. **③ 下一步** —— 生命周期有证据支撑的下一个阶段（`suggested_next`）翻成人话并给一个入口；被挡住时把 `blocked_reason` 写在同一条里；没有证据支持任何下一步时退回主流程（没回测 → 去回测；没策略 → 去策略库），不替它建议一个阶段。
4. **④ 需要你注意的事情** —— 只列能算出来的：没有回测 / 交易笔数少于 30 / 历史最大回撤超过 20% / 这段历史整体是亏的 / 数据不足 5 年 / 完成回测但没有样本外运行（`lifecycle.evidence.oos_runs === 0`）/ 策略被标为降级 / 推进被挡住 / 最近一次信号距今超过 6 个月。一条都算不出来时写「暂时没有需要特别注意的事情」，而不是留着空白让人以为没检查。

四问下面是统计行（可执行信号 / 观察中）、我的 Ghostfolio 持仓（真实持仓只读，与模拟盘完全隔离）、数据健康（周期与质量用人话写，原始 `quality_status` 枚举只在高级模式出现）、信号扫描（只用已收盘 K 线，带「这是策略研究信号，不是自动交易指令」与参考价免责句）与模拟验证（与真实持仓完全隔离）。

系统状态、版本、引擎版本、特征版本与 DSL Schema 这类软件工程读数不在首页了：v1.9.4 把它们整组搬到 `/settings` 的「系统信息」组（ADR-134），首页只在高级模式下留一句指路（「系统状态、版本……已经搬到 系统管理 → 系统信息」）。首页的四问与统计行在任何一次请求失败时都还会渲染 —— 每个面板自己 `.catch`，失败的模块名写在顶部横幅里（ADR-088）。

## 3. Strategy Detail

状态：已实现（frontend/src/views/StrategyDetailView.vue, frontend/src/main.ts, frontend/src/views/StrategiesView.vue）

`/strategy/:strategyId` 是独立页面，从 `/strategies` 策略库每一行的「详情」进入（`frontend/src/main.ts:34`），九段都在这一页上（ADR-114）：

1. 概览（Overview）：`GET /strategies/{id}` 的身份字段（ID、Slug、来源类型、状态与生命周期、版本数、创建时间），加上 `GET /lifecycle/strategies/{id}` 的当前阶段、证据支持的下一步、被挡原因、退步标记与逐门通过情况；没有证据支持任何下一步时就写没有，而不是替它建议一个。
2. 血统（Provenance）：来源类型/地址/许可/作者，以及每个版本的提交、来源地址、提示词版本与不可变哈希。没记下的写「未记录」，不猜。
3. 版本历史（Version History）：所有版本的 Schema、校验状态、是否当前、创建时间与哈希；「校验」按钮调用 `GET /strategies/versions/{version_id}/verify` 重新计算哈希并与存档比对，不一致就写「哈希不一致（文本已变）」。
4. 规则（Rules）：当前版本 DSL 的指标、入场、出场（`all`/`any` 组按策略自己的词渲染）、风险与执行参数——读的是策略自己说的话，不是回测的结论。
5. 回测（Backtest）：当前版本的回测运行列表与最近一次的关键读数（总收益率、最大回撤、夏普比率、胜率、期末权益）。没有运行时写「不是零，是还没有数据」，并把读者送到回测实验室。
6. 样本外与滚动验证（OOS / Walk-forward）：只从生命周期证据里读「已记录过几次」与最近一次摘要，并明说这些结果**不落库**——`POST /research/oos` 与 `POST /research/walk-forward` 按需计算，系统留下的只有审计事件，所以这一节不是一份完整评估。
7. 模拟验证（Paper Trading）：绑定到这个策略的账户（`GET /paper/accounts` 的 `strategy_id`）与它们的交易，并明说归因按账户：模拟验证交易只带账户与当时的策略版本名，系统不会把一笔成交倒推给某个策略。这一段的标题在 v1.9.1 从「模拟盘」改成「模拟验证」（ADR-127）：这一页是验证工具，不是账户管理，名字必须说清它替用户做什么。
8. 当前信号（Current Signals）：`GET /signals/preview/{strategy_version_id}` 的当前（未持久化）信号与 `GET /signals/evidence/{strategy_version_id}` 的五层确定性证据（规则匹配、经验统计、模拟盘统计、组合上下文、信号意图）。预览需要标的，因为策略自己不点名任何标的，所以标的那一格是必填的。
9. AI 解释（AI Explanation）：`GET /ai/status` 说明是否配置；已配置时可以解释当前信号预览（`POST /signals/preview-explain`）或最近一次回测（`POST /backtests/{run_id}/explain`），只解释已有数字、不参与计算；未配置时明说未配置，并写明九段在没有任何 AI 的情况下全部可用。

## 4. Backtest Lab

状态：已实现（frontend/src/views/BacktestView.vue）

顶部配置：strategy、symbol(s)、timeframe、date range、initial capital、fees、slippage。

结果区按「先回答，再列读数」排列（ADR-128）：第一屏是**历史回测结论**卡 —— 一句话结论（`conclusionHeadline`）、一个结论标签（`verdict`：值得继续验证 / 样本太少先别下结论 / 历史回测没有赚钱）、一级指标（总收益率、最大回撤、胜率、交易次数、盈亏效率、年化复合收益率）、「最大风险」（回撤与最长连续亏损）与「下一步」（`RouterLink` 指到下一个该做的页面）；紧跟一张**可信程度怎么样？** 卡，把历史回测 / 样本外验证（OOS）/ 滚动验证（Walk-Forward）/ 模拟验证四项的完成状态与已存证据逐行列出。样本少于 10 笔时结论卡直接写「先别下结论」，这个门槛与后端生命周期同一个数（`min_backtest_trades`）。

一级指标之后才轮到研究工具：权益曲线、回撤曲线、指标明细、交易明细、月度/年度视图、参数信息。二级指标（Sharpe、Sortino、CAGR、Expectancy、Exposure 等）收在「**查看详细分析**」按钮后面（`showDetailAnalytics`），不是删掉；三级分析（OOS 分割、滚动 Walk-Forward、参数敏感性、Monte Carlo 重采样、策略集成与投票阈值扫描）整段包在高级模式里，普通模式只留一句「高级分析（普通模式下不占第一屏）」和切换到高级模式的提示。

「查看假设」是一个独立区块，列出这次回测实际使用的成交模型、订单类型与有效期、手续费、滑点；结果可复现性区块列出结果哈希、数据集哈希、引擎版本、特征版本。两者相邻，因为假设本来就是复现记录的一部分（ADR-107）。

AI 区块叫「AI 汇总（只解释已有数字，不重新计算）」，固定五段：① 结论（`summary` + `plain_language`）、② 原因（`key_drivers`，旧结果回退到 `why`）、③ 风险（`risks` + `risk_notes`）、④ 可信程度（本地算出的 `evidenceSentence`，即上面那张证据卡的同一批事实，外加 `what_could_invalidate`）、⑤ 下一步（`what_to_watch_next`）。**第四段不来自 AI**：没配置 AI Provider 时它也照样给出「已经做完的是…／还没有做的是…」，页面明说未配置 AI 不影响结论（ADR-129）。

## 5. Paper Trading

状态：已实现（frontend/src/views/PaperView.vue）

`/paper` 有：新建模拟账户、账户明细（cash / equity / 未实现与已实现盈亏）、持仓、执行信号（虚拟成交）、绩效（期末权益、总收益、最大回撤、胜率）、**回测 vs 模拟**、**权益曲线**与**最新信号**两个区块。

**回测 vs 模拟**是这个页面最有用的模块：在账户明细里按「回测 vs 模拟」载入（`loadComparison`），把两列并排放出来 —— 左边是该账户所绑定策略**已经存下来的**那次回测（`PaperAccount.strategy_id` → `api.strategyVersions` 取当前版本 → `api.backtests` 取最近一条 `completed`，再从 `api.backtest` 的 `equity_curve` 首尾算它覆盖的窗口），右边是该账户的绩效端点（`api.paperPerformance` 的 `metrics` 与 `closed_trades`，窗口从 `api.paperEquity` 的曲线推）。三行读数分别是累计收益、最大回撤、交易次数，每列下面写明数据来源（「策略版本 vX 的第 #N 次回测」／「模拟账户 #N 的 M 笔成交记录」）。

这一页不新算任何数字，只把两份已经存下来的记录摆在一起，并补上评审要的那句人话：**模拟盘目前运行的时间比历史回测短得多，所以两边的收益不能直接比大小，暂时不能与多年历史回测直接比较**。这句话由两条曲线的真实时间戳算出（`spanLabel`），因此没有配置 AI 时同样成立（ADR-130）；账户没有绑定策略时改为明说「这个账户没有绑定策略，所以没有可以对照的回测」。

权益曲线由账户明细里的「权益曲线」按钮载入，调用 `GET /paper/accounts/{id}/equity` 的 `equity_curve`：起点是账户创建（或上次重置）时的期初现金，入金/提现按发生时抬高或压低基准，已平仓交易逐个加上 `pnl`。所以入金是一级台阶而不是收益，最后一点等于净入金 + 已实现盈亏；`curve_note` 把这两条与「重置只覆盖当前生命周期」一并写在图下方（ADR-108）。

「最新信号」列出最近 10 条已持久化信号（ID、生成时间、资产、周期、方向、状态），每行可以直接对某个账户执行，不必手抄信号 ID。

这一页在导航里叫「模拟验证」：它是**验证工具**而不是账户管理 —— 账户、持仓与绩效都在，但读它的目的是看一个策略在真实节奏下的表现，而不是维护一张资金表（ADR-127）。

## 6. Signal Detail

状态：已实现（frontend/src/views/SignalsView.vue, frontend/src/api.ts）

`/signals` 的每一行都有「详情」按钮，打开四段此前一直缺的内容，和已有的三张卡片（信号结果追踪 / 信号证据 / AI 解释）合起来覆盖原计划的八段：

1. 当前状态：信号自己的时刻、状态、方向、参考价，加上**最新已收盘 K 线**（本地数据集里最新的一根，写明「不是实时报价」）、它的收盘价、相对参考价的变化，以及信号之后已收盘的 K 线根数（读到 120 根窗口上限时写「实际可能更多」）。数据来自 `GET /market-data/latest/{symbol}`。
2. 策略历史统计：该策略版本最新一次已完成回测的样本数、历史胜率、最大回撤、总收益、夏普与数据集版本，加上模拟盘的已平仓笔数与已实现盈亏，再加上该策略已经评估过的信号结果（并明确写出这是另一组样本，不要和回测混在一起）；没有完成的回测就写「不是零，是还没有数据」。数据来自 `GET /signals/evidence/{strategy_version_id}`（`api.strategyEvidence`）与 `/signals/outcome-summary` 的 `strategy:<name>` 分组。
3. 真实持仓上下文：信号自带的 `portfolio_context`（是否连上 Ghostfolio、持有数量与占比、note），加上策略证据里的组合快照（持仓数、总值、本标的持仓）；未配置就明说未配置，而不是留白。
4. 风险 / 失效条件：策略自己给出的止损与目标价位，以及它们相对参考价的距离；AI 解释点名过的失效条件只在**确实解释过**的时候列出，否则明说「这条信号还没有做过 AI 解释，上面的价位来自策略本身，不是 AI 的判断」。这一段的参考价、止损与目标价旁边挂着 `REFERENCE_PRICE_DISCLAIMER`：它们是模型算出来的参考值，不是价格预测。

页首的免责句是 `SIGNAL_DISCLAIMER`（「这是策略研究信号，不是自动交易指令。」）。状态与方向不再直接印后端枚举：`frontend/src/wording.ts` 的 `SIGNAL_LABELS` 把 `BUY / SELL / WAIT / NO_SIGNAL` 渲染成「看多信号 / 看空 / 退出信号 / 暂不确认 / 暂无信号」，`signalLabel` 也用在筛选按钮上；结果分组的键（`state:` / `direction:` / `timeframe:` / `strategy:`）由 `groupLabel` 翻译成人话，原始枚举与触发规则 ID 只在高级模式出现；信号证据里的特征哈希与特征键名同理，普通模式写「特征明细在高级模式里」（ADR-127）。

## 7. 初学者友好

状态：已实现（frontend/src/metrics.ts, frontend/src/components/MetricHint.vue, frontend/src/components/StatCard.vue）

不默认把公式墙推给用户：任何专业指标都能在旁边就地解释自己。`frontend/src/metrics.ts` 是唯一的事实来源，每个指标固定回答四条 —— 是什么 / 怎么算 / 为什么看它 / 注意什么；`frontend/src/components/MetricHint.vue` 把它渲染成指标名旁边的「详细解释」按钮（悬停先给出「是什么」，点开列出四条），`StatCard.vue` 让每一张指标卡自动带上它，回测里按指标名成行的表格（样本内 / 样本外）也逐行带上。不需要解释的标签必须显式列进 `metrics.ts` 的 `NOT_A_METRIC`（目前只有「系统状态」与「版本」两个读数），否则守卫会让它红 —— 新指标要么解释自己，要么被点名承认自己不是指标。

**第一句话不能要求用户先点一下**（ADR-127）：指标卡在名字旁边直接印出「是什么」那一句（例：`最大回撤 / -18.7% / 历史上从最高点跌到最低点的最大跌幅。这代表策略曾经经历过约 18.7% 的回撤。`），四问留在「详细解释」按钮后面。专业名词第一次出现时同时给一句人话别名（`frontend/src/wording.ts` 的 `METRIC_WORDING`：夏普比率＝收益 / 波动效率、盈亏效率＝赚的钱是亏的钱的几倍、持仓时间占比＝实际持仓时间占比…），`StatCard.vue` 把它渲染成指标名后面的括号。方向是**术语可以保留，但必须同时告诉用户它是干什么的**，不是把术语换成土话。

## 8. GitHub Import

状态：已实现（frontend/src/views/StrategiesView.vue, frontend/src/api.ts）

`/strategies` 的「从 GitHub 导入（只读分析，不执行仓库代码）」区块是一份七步向导，步骤名与顺序固定：Repository → Analysis → Detected Strategies → Warnings → DSL Preview → Validation → Import（`const WIZARD_STEPS`）。每一步由它自己的证据解锁，而不是由一个按钮解锁（ADR-113）：

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

桌面仍然优先，但手机宽度可用：`frontend/src/style.css` 末尾的 `@media (max-width: 820px)` 是唯一的断点 —— 外壳从横排改为竖排，232px 的侧栏变成整宽横幅、导航变成可横向滑动的标签条，`/settings` 里「系统信息」「运行环境」那一组的引擎/特征/数据库读数在手机上隐藏，宽表格在页面内横向滚动而不是把整页撑开，主题按钮收进角落不再压住内容（ADR-111）。桌面布局不变。

## 10. 界面模式（普通 / 高级）

状态：已实现（frontend/src/mode.ts, frontend/src/App.vue, frontend/src/views/SettingsView.vue）

左上角有一个 `○ 普通模式 / ● 高级模式` 开关（`frontend/src/mode.ts`，localStorage 键 `mql-mode`，默认普通模式）。它**只改变显示的东西，不改变计算的东西**：两种模式下引擎读到的是同一份策略、同一份数据集、同一个参数，回测结果哈希一致。

普通模式隐藏的（它们仍然存在，只是不在第一屏）：JSON DSL 编辑器与 Dataset/Series ID、`result_hash` / `dataset_hash` / `engine_version` / `feature_version` 的可复现性区块、审计日志整节、运行环境逐键读数、**系统信息整节（系统状态 / 版本 / 引擎 / 数据库 / Redis / 特征版本 / DSL Schema）**、信号证据里的特征哈希与特征键名、触发规则 ID、`quality_status` 原始枚举、`BUY/SELL/WAIT/NO_SIGNAL` 原始状态词。高级模式把它们全部放回原位，方便自己和 AI 一起排查问题（ADR-126、ADR-134）。

模式是**界面层的开关，不是权限**：它不隐藏任何 API，也不改变任何认证；`/settings` 仍然只有现有认证能进。写在用户可见文案里的原始读数（哈希、枚举、ID）走 `<span v-if="isAdvanced">` 或整卡 `v-if`，不是从数据里删掉。

## 11. 研究策略（从想法到一次回测）

状态：已实现（frontend/src/views/ResearchView.vue, frontend/src/views/BacktestView.vue, frontend/src/api.ts）

`/research` 是评审 §4 那条主流程的入口，固定四步，一步一屏：

1. **① 选择标的** —— 只列出真的已经同步下来的系列（`GET /market-data/series`），每条写 `代码 · 周期`；选中后立刻显示这份数据的覆盖范围（`series_start` → `series_end`）、`quality_status` 与 `bar_count`（`GET /market-data/series/{id}`）。质量是 `invalid` / `partial` / `unknown` 时在同一张卡里给出警告，说清结论因此受什么限制；什么都没有时给一个通往 `/data` 的链接，而不是一个空下拉框。
2. **② 选择策略** —— `GET /strategies` 与 `GET /strategies/{id}/versions` 两个下拉框；版本行写「版本 · 校验状态 · 是否当前」。库里记录的校验状态不是 `valid` 时给出警告。没有策略时链到 `/strategies`。
3. **③ 设置少量参数** —— 只有两件事：研究哪一段时间（两个日期，留空＝全部已同步数据）与每次用多少钱（沿用策略里的仓位设置 / 固定比例 / 按每笔风险）。其余假设（成交模型、手续费、滑点、初始资金）不在这里改，回测页把它们原样列出来。日期顺序反了或比例越界时按钮不可用并写明原因。
4. **④ 开始研究** —— 先给一句人话说明接下来会发生什么（`将用「X」的版本 v，在 SYMBOL 的日线上跑一次历史回测，区间 …`），再跳转到 `/backtest?strategy_version_id=…&symbol=…&timeframe=…&run=1[&start=&end=&size_mode=&size_fraction=&size_risk_pct=]`。

回测页读这组 query：选中版本、标的、周期与仓位，填好日期后立刻跑一次，所以「开始研究」到「历史回测结论」之间不需要用户再点一次运行（ADR-132）。**这一页不产生任何量化事实**：覆盖范围、K 线根数、质量、版本与校验状态都是后端已经存下来的读数，回测仍然由引擎在回测页算。普通模式不显示系列 ID、来源与内容哈希，高级模式才出现（ADR-126、ADR-131）。

## 12. 数据

状态：已实现（frontend/src/views/DataView.vue, frontend/src/api.ts）

`/data` 只做数据这一件事，三张卡：

1. **同步行情** —— 输入代码（如 `AAPL`、`QQQ`、`BTC-USD`）选时间跨度后调 `POST /market-data/sync`，成功时报告新增 K 线根数与系列号，并把「已有 K 线、同步无新增」当成正常结果写出来，而不是报错。表格列出代码、周期、数据范围、质量、最后同步时间；高级模式再加系列 ID、来源与数据集版本三列。
2. **质量那一列是什么意思** —— 只有四种取值，逐条写出人话（与 `backend/app/data/market_data_repo.py` 的 `assess_bars_quality` 同一套判据）：`valid` 每一根 K 线的开高低收都是正数、相邻两根之间没有超过 5 天的空洞；`partial` 数据本身没问题但中间有超过 5 天的空洞；`invalid` 出现非正数或缺失的开高低收，或最高价低于最低价；`unknown` 还没有 K 线。同一张卡写明质量只描述数据、不描述策略，缺口会限制结论成立的范围。
3. **删除会发生什么** —— 已经被回测引用过的数据不会被真正删除，后端把它改成归档（`is_archived`，ADR-081），因为回测结果的可复现性依赖这份数据；勾上「显示已归档」仍然看得到，可以「恢复」。没有任何回测引用的数据才会真正删除，删除前再确认一次。

高级模式另有「原始读数」按钮（`GET /market-data/series/{id}`）：K 线根数、内容哈希、复权、时区、来源与数据集版本 —— 直接来自后端字段，这一页不重新计算它们。

## 13. 排版层级（结论先行，配置退后）

状态：已实现（frontend/src/style.css, frontend/src/views/BacktestView.vue）

评审 §18 的要求不是换一套视觉，而是**重新建立主次**：现状是卡片一样重、表格过多、结果与配置混在同一层，要读很多字才知道下一步。v1.9.4 因此只做两件事（ADR-135）：

1. **结论卡领读** —— 首页四问的答案卡、回测第一屏的「历史回测结论」、模拟验证的「回测 vs 模拟」都带一条强调色左边框（`border-left: 3px solid var(--accent)`），标题用正文色、数字用更大字号（26px）。一页里第一眼落在哪一句，由版式决定，而不是由读者从头读到尾决定。
2. **配置卡退后** —— 只用来填东西的卡片（回测的「运行新回测」、模拟验证的「新建模拟账户」「执行信号」、「用一句人话创建策略」、GitHub 导入、数据页的三张说明/同步卡、研究页的 ① ② ③）加 `.card-quiet`：透明底、虚线边框、标题用弱化色。它们仍然在同一页、同一顺序、同样可点，只是不再是视觉重心；④ 开始研究 是动作，保持实心。

另外 `.page-sub` 限宽 72ch：一句人话不应该横跨整个宽屏。**没有换皮** —— 配色、字体、间距、组件与响应式断点全部沿用，只有主次关系变了。这条原则和 CSS 一起由守卫盯着（`backend/tests/test_frontend_contracts.py` 的 `test_the_typography_separates_a_conclusion_from_its_controls`）。

## 14. 欠账（尚未实现的承诺，按本文件顺序）

（无）

这一节是上面 `尚未实现` / `缺少` 的汇总视图；两份清单必须一致，守卫会核对。截至 v1.7.8，本文件列出的承诺全部已实现：最后一条欠账（独立的策略详情页）由 v1.7.8 收下，这一节从此只保留一处说明——下面不再有「尚未实现」的正文承诺，新的计划写在别处，不写进这份按现状记录的规格。
