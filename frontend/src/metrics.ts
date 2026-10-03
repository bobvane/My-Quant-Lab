// Professional metrics explain themselves in place (ADR-110, ADR-127, docs/13_UI_UX.md section 7).
//
// Every entry answers the same four questions, in this order:
//   what  - what this number is
//   how   - how it is computed
//   why   - why somebody looks at it
//   watch - what it does NOT tell you
// `what` is also the sentence every metric card shows by default, without being
// asked: the audit found that an explanation hidden behind a button the reader has
// to discover is an explanation most readers never see (audit §11). The remaining
// three answers stay behind 「详细解释」, rendered by MetricHint.vue, which is used
// by StatCard.vue (every metric card) and by the metric tables in BacktestView.vue.
// A new metric card must either get an entry here or be listed in NOT_A_METRIC;
// backend/tests/test_ui_promises.py fails when it is neither.

export type MetricNote = {
  what: string
  how: string
  why: string
  watch: string
}

const LABELLED: Record<string, MetricNote> = {
  总收益率: {
    what: '整段回测结束时相对期初本金赚了或亏了多少。',
    how: '期末权益 ÷ 期初本金 − 1，本金取这次回测的起始资金。',
    why: '最直观的「赚了多少」，也是和别的策略比较的第一眼。',
    watch: '它不区分时间长短：一个月赚 30% 和一年赚 30% 不是一回事，要连年化与回撤一起看。',
  },
  总收益: {
    what: '模拟盘当前基准上赚了或亏了多少（只算已平仓交易）。',
    how: '已实现盈亏 ÷ 当前盈亏基准（净入金）。',
    why: '看这套信号在模拟盘上是不是真的在赚钱。',
    watch: '入金与提现会改变基准，所以它衡量的是交易，不是账户余额的涨跌。',
  },
  最大回撤: {
    what: '从任意历史高点跌到之后最低点的最深一段。',
    how: '逐点跟踪历史峰值，取 max((峰值 − 当前权益) ÷ 峰值)。',
    why: '它决定这条曲线你拿不拿得住，也决定带杠杆时会不会被强平。',
    watch: '它只描述已经走过的路径；样本越短越乐观，未来可能更深。',
  },
  夏普比率: {
    what: '每承担一单位波动，换来多少超额收益。',
    how: '(年化收益 − 无风险利率) ÷ 年化波动率，波动率由逐 bar 收益的标准差折年得到。',
    why: '让波动水平不同的策略可以放在同一把尺子上比较。',
    watch: '它把向上的波动也算作风险，并假设收益大致正态；收益越厚尾，这个数字越不可信。',
  },
  组合夏普: {
    what: '组合权益路径的夏普比率。',
    how: '同「夏普比率」，只是收益序列换成多成员投票后的组合路径。',
    why: '看组合是不是在拿更少的波动换同样的收益。',
    watch: '成员之间越相关，组合的波动就越接近单个成员，夏普也不会变好。',
  },
  胜率: {
    what: '已平仓交易里盈利笔数占的比例。',
    how: '盈利笔数 ÷ 已平仓笔数。',
    why: '衡量「方向猜对」的频率，是交易者最熟悉的直觉指标。',
    watch: '高胜率可以配着小赚大亏：必须和盈亏比、最大回撤一起看；交易次数太少时它几乎没有意义。',
  },
  盈利概率: {
    what: '把已实现的交易顺序打乱很多次以后，最终仍然盈利的次数占比。',
    how: '对已平仓交易的 pnl 做有放回重采样若干次，统计每次重排的末点权益高于起点。',
    why: '回答「如果交易顺序换一换，结论还成立吗」。',
    watch: '重排只改变顺序、不改变单笔的分布，所以它会低估连续亏损的真实概率。',
  },
  收益中位数: {
    what: '所有重排样本里中间那一次的收益率。',
    how: '把每次重排的收益率排序后取中位数。',
    why: '比均值更抗极端路径，代表「典型情况下」的结果。',
    watch: '中位为正不代表你能拿到；均值明显低于中位时，说明少数路径非常惨。',
  },
  回撤中位数: {
    what: '所有重排样本里典型的最大回撤。',
    how: '每次重排各自计算最大回撤，再取中位数。',
    why: '给出「通常要忍受多深」的直觉。',
    watch: '它是一半路径的水平，意味着另一半比它更深。',
  },
  清零概率: {
    what: '重排样本里权益跌破零（爆仓）的次数占比。',
    how: '统计每一条重排路径里权益是否降到零或以下。',
    why: '直接回答「这套交易会不会把账户打光」。',
    watch: '重排无法制造真实的相关性冲击，所以它通常偏乐观；不带杠杆时它应当是零。',
  },
  组合总收益: {
    what: '多个策略版本按权重投票后的合计收益。',
    how: '逐根 K 线统计票数，票数严格超过阈值才开仓，按同一份数据回测。',
    why: '看「互相认同」是不是比单打独斗更稳。',
    watch: '成员相关性越高，组合越接近单打独斗；成员越多不等于越分散。',
  },
  组合最大回撤: {
    what: '组合权益路径的最大回撤。',
    how: '同「最大回撤」，作用在投票后的组合路径上。',
    why: '看共识仓位在最坏的连续亏损里有多深。',
    watch: '组合回撤不会被成员平均掉，它取决于成员同时回撤的概率。',
  },
  认同并开仓: {
    what: '票数达到阈值、真的开仓的 bar 数量。',
    how: '统计票数严格超过阈值的 bar。',
    why: '说明「共识」有多罕见：阈值越高越少，但每次越「干净」。',
    watch: '这个数字很小时，组合的结论也只建立在很小的样本上。',
  },
  期末权益: {
    what: '模拟账户现在值多少。',
    how: '净入金 + 已实现盈亏 + 未实现盈亏（口径见绩效端点）。',
    why: '一眼看清账户状态，也是权益曲线的最后一个点。',
    watch: '入金会抬高它：它变大不等于策略赚钱，赚没赚要看总收益。',
  },
  目标指标均值: {
    what: '参数网格上所有网格点的目标指标平均值。',
    how: '对网格里每个点各跑一次回测，取目标指标的算术平均。',
    why: '给出「参数在这一带大概什么水平」的基准线。',
    watch: '均值会被少数极端网格点拉走，要看它和极差、邻域稳健一起读。',
  },
  '极差 (max − min)': {
    what: '网格里目标指标的最大值与最小值之差。',
    how: 'max(指标) − min(指标)。',
    why: '衡量结论对参数有多敏感：极差越大，参数一动结论就翻。',
    watch: '它只看两端，中间多少点是平的它不关心。',
  },
  邻域稳健: {
    what: '最优网格点周围的点是否也表现不错。',
    how: '比较最好点与其邻居的指标符号/水平是否一致。',
    why: '尖峰式的「最优」通常只是过拟合噪声，成片的平台才值得信。',
    watch: '它只是符号一致性的粗略判断，不构成推荐，也不做参数拟合。',
  },
  '参与排名 / 网格点': {
    what: '进入这次排序的网格点数量（其余点因预热不足等原因无法评估）。',
    how: '统计成功跑完并算出目标指标的网格点。',
    why: '说明这次的排序建立在多少样本上。',
    watch: '点太少时，下面所有排序结论都不可靠。',
  },
  可执行信号: {
    what: '当前处于可执行状态（BUY/SELL）的信号条数。',
    how: '统计最近一次持久化信号里 state 为可执行的那些。',
    why: '决定今天有没有事情要做。',
    watch: '可执行不等于该执行：这里只做提示，下单永远由人决定。',
  },
  观察中: {
    what: '方向已出、但还没达到执行条件的信号条数。',
    how: '统计 WAIT 一类状态的信号。',
    why: '提醒你有些标的正在接近触发条件。',
    watch: '它是状态计数，不是收益预估。',
  },
}

const RAW_KEYS: Record<string, MetricNote> = {
  total_return: LABELLED['总收益率'],
  max_drawdown: LABELLED['最大回撤'],
  sharpe: LABELLED['夏普比率'],
  win_rate: LABELLED['胜率'],
  number_of_trades: {
    what: '这次回测里完整平仓的交易笔数。',
    how: '统计回测区间内已经平仓的交易。',
    why: '它决定上面所有指标有多少样本支撑。',
    watch: '笔数很少时，胜率与夏普基本是噪声；样内外对比时尤其要看它。',
  },
}

export const METRIC_NOTES: Record<string, MetricNote> = { ...LABELLED, ...RAW_KEYS }

// Labels that are readouts of the app itself, not professional metrics, so they carry no
// explanation. Everything else rendered as a metric label must have a note above.
export const NOT_A_METRIC: readonly string[] = ['系统状态', '版本']

export function metricNote(label: string): MetricNote | null {
  return METRIC_NOTES[label] ?? null
}

/**
 * The one sentence a metric card shows by default (audit §11).
 *
 * Empty for labels that are readouts of the app rather than metrics, so the card
 * simply renders no explanation line instead of an empty paragraph.
 */
export function metricPlain(label: string): string {
  return METRIC_NOTES[label]?.what ?? ''
}
