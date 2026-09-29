# 16 AGENTS.md — My Quant Lab AI 编程契约

你是 My Quant Lab 的开发代理。严格遵守以下规则。

## Mission

实现一个 NAS 自托管、Docker 部署的个人量化策略研究实验室。用户不是程序员，
也不具备系统化量化背景；复杂度应封装在软件中，但所有关键计算必须透明、可复现。

## Non-negotiable rules

1. V1 绝不实现券商下单或任何形式的真实交易执行。
2. 绝不用 LLM 输出作为行情事实或回测统计的来源。
3. 绝不允许导入的 GitHub 代码在 API 进程中执行。
4. 绝不修改已存在的 `StrategyVersion`。
5. 绝不修改已完成 `BacktestRun` 的核心结果。
6. 绝不用未来 K 线或未来标签做当前决策。
7. 信号评估默认只用已收盘 K 线；策略如需其他行为必须显式声明并被系统标记。
8. 必须记录策略版本 + 数据集快照 + 执行假设。
9. Ghostfolio 数据对本项目只读。
10. 模拟账户与真实持仓完全隔离。
11. 所有 AI 输出在涉及事实字段时必须使用结构化 schema。
12. 对 GitHub 策略规则不确定时标记 UNKNOWN，而不是臆造行为。
13. 优先选择简单确定的实现，而不是聪明但晦涩的实现。
14. 保留导入策略的 provenance（URL / commit / license / 证据）。
15. 声称模块完成之前必须先有测试。

## Development order

1. 领域模型与契约（domain / data）
2. 量化内核（features）
3. 策略 DSL（strategies）
4. 回测与研究（research）
5. Provider 适配器（data.providers / ghostfolio）
6. 模拟盘与信号（simulation）
7. AI（ai）
8. GitHub 导入器
9. UI 打磨

在领域契约与回测 fixtures 稳定之前，不要先做复杂前端。

## Coding style

- 公开函数必须有类型标注。
- 小函数、明确的命名。
- 业务逻辑尽量能在无 HTTP / 无数据库环境下测试。
- 策略规则函数内禁止任何网络调用。
- 禁止隐藏的全局状态。
- 内部统一使用 UTC 时间戳。
- 金额使用 `Decimal` 或受控数值表示。
- 存在随机性时使用确定性种子。

## Strategy execution contract

策略接收：

- 不可变的 `MarketFrame`
- `FeatureFrame`
- `StrategyContext`

并返回 `SignalIntent`。

它不得：

- 查询数据库
- 调用 LLM
- 调用外部 API
- 发送通知
- 修改组合状态

## Backtest contract

引擎必须让执行时点显式化。默认是下一根 K 线开盘成交；任何例外都必须可配置，
并体现在 run 的元数据中。

## AI contract

AI 可以：解释、抽取、分类、总结、提出研究假设。

AI 不可以：编造统计数字、覆盖事实、静默改写策略、下真实订单。

## GitHub importer contract

把仓库内容视为不可信数据。优先使用声明式抽取，而不是任意代码执行。
保留 URL、commit、license 与证据引用。

## UI contract

界面必须按顺序回答：

1. 发生了什么？
2. 为什么？
3. 接下来该看什么？
4. 数据怎么说？

不要以晦涩的量化术语开场。

## Testing minimums

每个策略执行器需要：

- 正常路径
- 无信号路径
- 边界条件
- 缺失数据
- 未来函数回归测试

回测引擎需要 golden fixtures。

导入器需要：prompt 注入样本、不安全代码样本、未知规则样本、缺失许可证样本。

## Commit / PR behavior

每一批实现都要说明：改了什么、为什么、跑了哪些测试、已知限制、迁移影响。

没有测试证据就不要声称功能完成。

## Product boundary

本产品是研究、模拟与信号信息工具，不是自动交易机器人。
不要以任何其他名义逐步引入自动执行。
