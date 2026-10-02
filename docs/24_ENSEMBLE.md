# 24 策略集成（Strategy Ensemble）

> 状态：v1.3 已实现（后端 + API）。决策见 `docs/17_DECISIONS.md` ADR-047。

## 1. 它回答什么问题

不是「哪个策略最好」，而是「**互相认同**的策略是否比其中任何一个都更稳」。

两个入场信号很少同时出现的策略，其交集在历史上样本极少，但一旦出现往往是两边都
认为有把握的时刻。反过来，如果两个策略几乎总是同时开仓，那它们并没有带来分散。

## 2. 它是怎么算的

1. 每个成员（一个策略版本）在**同一份行情**上各自算特征、各自评估规则，得到逐 bar 的
   `entry_long` / `exit_long`（以及可能的多头反向信号）。
2. 逐 bar **加权投票**，只有票数**严格超过**阈值才算数：
   `sum(weight_i × flag_i) > vote_threshold`。
3. 合并后的决策序列驱动**一个**组合，用与单策略完全相同的成交/成本/风控语义执行
   （`next_bar_open` 成交、滑点手续费、保守的止损止盈同 bar 处理）。

权重会被归一化，所以 `[2, 1]` 与 `[4, 2]` 是同一个集成。

### 为什么是「决策合并」而不是「交易列表拼接」

把各成员的成交记录拼在一起，会得到一个**同时持有多笔仓位**的组合。而本引擎是单仓位、
单一现金账户，那样拼出来的结果它无法诚实地执行。投票得到的是**一条**决策序列，
现有引擎本来就懂怎么执行。

## 3. 阈值语义（这里踩过一次坑）

投票判定用的是**严格大于**：

```text
sum(weight_i × flag_i) > vote_threshold      # 注意是 >，不是 >=
```

两成员等权时每票恰好 0.5，若用 `>= 0.5`，**单个成员就能单独通过**「多数」判定，
集成会退化成各成员的**并集**（实测：两个交集为 0 的成员却产生了 11 个入场）。
「多数」的意思是「超过一半」，不是「达到一半」。因此有效范围是 `[0, 1)`。

- `vote_threshold = 0.5`、两个等权成员 → 需要**两个都**同意。
- `vote_threshold = 0.1`、三个等权成员 → 任一成员即可。
- 阈值越高越保守（可交易 bar 不会变多）。

因为判定是严格大于，**任何阈值都不是「连续可调」的**：票数只能取到联盟总数，两个相邻值之间的
阈值完全等价。想看到这个旋钮的全部台阶，用第 7 节的阈值扫描，而不是逐个猜。

## 4. API

```http
POST /api/v1/research/ensemble
```

```json
{
  "members": [
    { "strategy_version_id": 3, "weight": 1.0 },
    { "strategy_version_id": 7, "weight": 1.0 }
  ],
  "symbol": "AAPL",
  "timeframe": "1d",
  "vote_threshold": 0.5,
  "execution_overrides": { "initial_capital": 10000, "fee_bps": 10 }
}
```

响应（节选）：

```json
{
  "ensemble_version": "1.2.0",
  "vote_threshold": 0.5,
  "bars_evaluated": 400,
  "dataset_version_id": 12,
  "symbol": "AAPL",
  "timeframe": "1d",
  "engine_version": "ensemble-1.2.0",
  "feature_version": "ensemble",
  "members": [
    {
      "label": "3@1.0.0",
      "weight": 0.5,
      "entry_bars": 10,
      "exit_bars": 9,
      "entry_agreed": 1,
      "solo_entries": 9,
      "entry_support_rate": 0.1,
      "vote_agreement_rate": 0.98
    },
    { "label": "7@1.0.0", "weight": 0.5, "entry_bars": 4, "exit_bars": 4 }
  ],
  "agreement": {
    "entry_bars": 1,
    "exit_bars": 1,
    "short_entry_bars": 0,
    "entries_taken": 1,
    "signalled_bars": 13,
    "solo_signalled_bars": 12,
    "entry_support_rate": 0.077,
    "exit_support_rate": 0.2
  },
  "metrics": { "total_return": 0.013, "sharpe": 0.06, "max_drawdown": -0.016, "...": "..." },
  "final_equity": 10129.2,
  "member_runs": [
    {
      "label": "3@1.0.0",
      "weight": 0.5,
      "initial_capital": 5000.0,
      "final_equity": 4734.0,
      "entries_taken": 14,
      "metrics": { "total_return": -0.053, "max_drawdown": -0.081, "...": "..." },
      "equity_curve": ["..."]
    }
  ],
  "trades": ["..."],
  "warnings": ["member '3@1.0.0' has a 20-bar warm-up"]
}
```

### `entry_bars` 与 `entries_taken` 的区别

- `entry_bars`：**票数过阈值**的 bar 数。持仓期间再次触发也计入。
- `entries_taken`：真正**开出仓位**的次数（投票通过 **且** 当时空仓）。

只有 `entries_taken` 与成员的 `entry_bars` 可比，且必然 `<= min(成员 entry_bars)`。
把两者混为一谈会高估集成的信号质量。

## 5. 认同统计（谁被投票否决了）

组合的收益/回撤只说明**合并后的决策**值不值，不说明**谁被否决了**。两个结果完全不同
的集成（一个「成员高度一致」、一个「成员几乎从不同时开仓」）在只报 `entries_taken` 时
看起来一模一样，因此响应里带上归因：

| 字段 | 位置 | 含义 |
|---|---|---|
| `signalled_bars` | `agreement` | 至少有一个成员想入场的 bar 数（投票的分子分母基准） |
| `solo_signalled_bars` | `agreement` | 其中**只有单个成员**想入场、因而被投票否决的 bar 数 |
| `entry_support_rate` | `agreement` | `entry_bars / signalled_bars`：成员的入场意愿有多少活过了投票 |
| `exit_support_rate` | `agreement` | 同上，针对退出信号 |
| `entry_bars` / `entry_agreed` | `members[]` | 该成员自己提议的入场根数 / 其中票数过阈值的根数 |
| `solo_entries` | `members[]` | 该成员单独提议、被否决的根数 |
| `entry_support_rate` | `members[]` | `entry_agreed / entry_bars`；该成员从没提议时为 `null` |
| `vote_agreement_rate` | `members[]` | 该成员在**全部**评估 bar 上与最终结果一致的占比 |

读法：

- `solo_signalled_bars` 接近 `signalled_bars` → 成员之间**几乎没有共识**，集成的收益不是
  分散化带来的，而是「恰好没怎么交易」。这不是稳健，是没交易。
- `entry_support_rate` 低 → 集成在大量否决成员的信号；策略集成在这种成员组合上不会比
  单策略给出更多信息。
- `vote_agreement_rate` 很高但 `entry_support_rate` 很低是**正常**的：多数 bar 上大家都不
  想开仓，此时「一致」平凡成立。判断成员是否被否决要看 `entry_support_rate`。

所有认定都在集成**自己的共同 bar** 上用同一套成本假设计算，因此不与成员各自的历史回测
结果混淆。

## 6. 同口径成员对比（`member_runs`）

「集成比成员好吗」不能用成员**各自的历史回测**回答：那次回测的标的、周期、数据集版本、
成本模型和初始资金都**可能和本次集成不同**，数字并排放着，差异可能只来自数据窗口。
所以响应里直接带上**每个成员在同一批 bar、同一套成本模型下**的独立跑分：

| 字段 | 含义 |
|---|---|
| `label` / `weight` | 与 `members[]` 一一对应、同序 |
| `initial_capital` | `组合初始资金 × 归一化权重`（各成员之和恰为组合初始资金） |
| `final_equity` | 该成员独立跑完的期末权益 |
| `entries_taken` | 该成员**真正开仓**的次数（= `metrics.number_of_trades`） |
| `metrics` | 与组合同一套指标（总收益/回撤/夏普…） |
| `equity_curve` | 与组合**同一长度、同一时间戳**的权益曲线 |

这样对比表里的每一行都由服务端用**同一个模拟器**算出来（`ensemble.py` 的 `_simulate`
是组合与成员唯一的执行路径），任何差异都只能来自**决策序列**，不能来自执行假设。

**必须说清的限制**：每个成员曲线是**独立账户**的模拟（各自 `initial_capital`），
不是「组合同时持有了这些仓位」。引擎只有一个仓位、一个现金账户，同时持有多个仓位它执行
不了。因此成员曲线回答的是「**如果只让这一个成员交易会怎样**」，而不是「这个成员在投票
运行时贡献了多少」。想回答后者，只能看 `agreement` / `members[]` 里的认同统计。

### 为什么以前只能标注「不可比」

在 `member_runs` 之前，对比表的成员列只能取自**该成员最近一次已完成的单策略回测**，
窗口是否一致无法保证。v1.3.9 的做法是返回 `dataset_version_id` / `symbol` / `timeframe`
让前端把不可比的行标出来 —— 那是「据实说明」，不是「解决问题」。现在同口径跑分由集成
自己产出，这个警告只在**回退**到历史回测时才可能出现。

## 7. 投票阈值扫描（`/research/ensemble/sweep`，ADR-052）

阈值是集成**唯一的旋钮**，而它此前只能一次跑一个值 —— 靠猜。更麻烦的是它的形状：

加权票是各成员权重之和，所以票数**只能落在联盟总数**（coalition totals）上。两成员等权时
可能的票数只有 `0 / 0.5 / 1.0`；**阈值 0.4 和阈值 0.5 的行为完全相同**，因为两者等待的都是
「两个成员都同意」。所以这个曲面是**阶梯**，不是曲线，一次跑一个值会隐藏「跳过了哪些联盟」。

```http
POST /api/v1/research/ensemble/sweep
```

请求体与 `POST /research/ensemble` 相同（`members` / `symbol` / `timeframe` /
`execution_overrides` 全部复用），额外的 `thresholds` 是**可选**的显式阈值列表；
基类的 `vote_threshold` 在这里被忽略。省略 `thresholds` 时服务端只在**答案会发生变化**
的阈值上跑（即 `possible_votes` 中严格落在 `(0, 1)` 内的值），一次请求最多 12 个点。

```json
{
  "members": [
    { "strategy_version_id": 3, "weight": 1.0 },
    { "strategy_version_id": 7, "weight": 1.0 }
  ],
  "symbol": "AAPL",
  "timeframe": "1d",
  "thresholds": [0.0, 0.25, 0.5, 0.75]
}
```

响应（节的选）：

```json
{
  "ensemble_version": "1.2.0",
  "engine_version": "ensemble-1.2.0",
  "feature_version": "ensemble",
  "bars_evaluated": 400,
  "initial_capital": 10000.0,
  "dataset_version_id": 12,
  "symbol": "AAPL",
  "timeframe": "1d",
  "thresholds": [0.0, 0.25, 0.5, 0.75],
  "possible_votes": [0.0, 0.5, 1.0],
  "members": [
    { "label": "3@1.0.0", "weight": 0.5, "weight_share": 0.5 },
    { "label": "7@1.0.0", "weight": 0.5, "weight_share": 0.5 }
  ],
  "points": [
    {
      "vote_threshold": 0.0,
      "effective_vote": 0.5,
      "entries_taken": 14,
      "entry_bars": 14,
      "signalled_bars": 23,
      "solo_signalled_bars": 11,
      "final_equity": 9631.0,
      "total_return": -0.037,
      "max_drawdown": -0.092,
      "sharpe": -0.24,
      "win_rate": 0.36,
      "number_of_trades": 14
    },
    { "vote_threshold": 0.25, "effective_vote": 0.5, "entries_taken": 14, "...": "..." },
    { "vote_threshold": 0.5, "effective_vote": 1.0, "entries_taken": 1, "...": "..." },
    { "vote_threshold": 0.75, "effective_vote": 1.0, "entries_taken": 1, "...": "..." }
  ],
  "warnings": []
}
```

### `effective_vote` 与 `possible_votes`

| 字段 | 含义 |
|---|---|
| `possible_votes` | 加权票**所有可能取值**（联盟总数），升序 |
| `points[].effective_vote` | `possible_votes` 中**第一个严格大于**该阈值的票数 —— 也就是这个阈值实际在等哪个联盟 |

上例中阈值 `0.0` 与 `0.25` 的 `effective_vote` 都是 `0.5`（一个成员就够），阈值 `0.5` 与 `0.75`
都是 `1.0`（必须两个成员同意）。**相邻两个点行为相同时，说明它们之间没有台阶** —— 这不是
数据不够，而是这个旋钮的固有颗粒度。

注意 `effective_vote` 不是「需要几个成员」，而是「第一个能过线的票数」。三成员各 `1/3` 时，
阈值 `0.3` 的 `effective_vote` 是 `1/3`（一个成员已经够），而不是 `2/3`。

### 这个端点不做什么

- **不返回权益曲线**，也不返回 `member_runs`：曲线不在本端点的契约里，12 个点各带一条曲线
  会让响应体积失控。
- **不推荐阈值**。它与参数敏感性扫描（`docs/21`）同族：只描述形状。跨台阶比较收益并挑最高的
  那个，就是在同一个数据集上做选择，属于过拟合，本工具不替用户做这个决定。
- 扫描点与 `POST /research/ensemble` 在**同一阈值下必须逐项一致**：两者共用
  `_prepare_ensemble` + `_run_vote`，特征与成员决策只评估一次并复用。若扫描点能与直接运行
  不一致，这张图描述的将是用户无法复现的集成 —— `test_ensemble_sweep.py` 把这条锁成了断言。

## 8. 成本与风控从哪来

组合需要一个成本模型和一套止损规则。默认**继承第一个成员**的，并可用
`execution_overrides` 覆盖（成本、初始资金、仓位管理都属于**组合**，不属于单个成员）。
风险线取「第一个定义了它的成员」——一个组合只能带一个止损，逐 bar 混用各成员的止损
是任意且不可解释的。

## 9. 约束

| 情况 | 行为 |
|---|---|
| 成员为空 / 超过 12 个 | `422` |
| **同一版本重复出现** | `422`（见下） |
| `vote_threshold` 不在 `[0, 1)` | `422` |
| `thresholds` 为空列表 / 超过 12 个 / 有重复 / 有值不在 `[0, 1)` | `422`（仅扫描端点） |
| 权重为负 / 全为 0 | `422` |
| 成员之间没有共同 bar（预热期完全不重叠） | `422` |
| 预热期部分重叠 | 正常执行，但在共同 bar 上评估并写入 `warnings` |
| 成员规则引用了该成员特征里没有的列 | 报错，**不静默丢弃该成员** |
| 该版本已被更新的版本取代（非 current） | **允许**。策略版本是不可变快照，旧版本仍是合法成员 |

### 为什么重复成员必须被拒绝

把同一个版本提交两次会归一化成 0.5 + 0.5 两票。由于判定是「**严格超过**阈值」，
`0.5 + 0.5 = 1.0` 只需该版本**自己的信号**就能满足 —— 报告会显示一次「投票」，
实际参与者只有一个。这正是集成本应避免的误导。

想让某个策略话语权更大，请保留其他成员并**调高它的权重**，而不是重复添加它。

## 10. 可复现性

同一组成员 + 权重 + 数据集 + 阈值 → 完全相同的决策与指标（无随机性）。
成员权重会被归一化后记录在 `members[].weight`，便于事后核对。扫描端点同理：
同一组 `thresholds` 两次调用得到相同的 `points`。

## 11. 实现位置

| 位置 | 作用 |
|---|---|
| `backend/app/research/ensemble.py` | 投票、决策合并、组合执行；`_prepare_ensemble`（阈值无关的公共部分）、`_run_vote`（单阈值）、`run_ensemble`、`run_ensemble_sweep` |
| `backend/app/strategies/dsl.py` | `merge_spec_overrides`（唯一经过校验的覆盖合并入口） |
| `backend/tests/test_ensemble.py` | 投票语义、阈值边界、权重归一化、成本/仓位覆盖、现金上限、同口径成员运行 |
| `backend/tests/test_ensemble_sweep.py` | 扫描点与直接运行逐项一致、联盟总数、`effective_vote`、单调性、参数校验 |
| `backend/tests/test_ensemble_api.py` | 端点契约、404/422、审计、可复现性、gzip |
| `backend/tests/test_ensemble_sweep_api.py` | 扫描端点契约、联盟总数、单调性、审计、422 |
