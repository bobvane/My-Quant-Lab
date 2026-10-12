# My Quant Lab — 文档索引

这是 **My Quant Lab / 个人量化策略研究实验室** 的文档索引。
本文件只负责**指路**，不重复文档内容；项目当前是什么状态，以「现行文档」一节的四份为准。

## 现行文档（以代码为准，随版本更新）

| 文档 | 内容 |
|---|---|
| [`../README.md`](../README.md) | 部署步骤、版本与发布节奏、安全与暴露面、常见报错 |
| [`PROJECT_OVERVIEW.md`](./PROJECT_OVERVIEW.md) | 项目是什么、适合谁、能做什么、当前可用状态、已知限制 |
| [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md) | 当前开发计划：已完成 / 已实现未验证 / 已知限制 / 未来计划 / 需要用户决定的事项 |
| [`DEVELOPMENT_GUIDE.md`](./DEVELOPMENT_GUIDE.md) | 目录与模块、开发测试与 CI、迁移与兼容、安全边界、冻结契约、完成判定 |
| [`USER_MANUAL.md`](./USER_MANUAL.md) | **零基础中文使用手册**：部署、更新与回退、日常研究操作、排错、备份 |

长期有效、需要时查阅的设计与契约：

- [`04_STRATEGY_DSL.md`](./04_STRATEGY_DSL.md)：策略 DSL（统一策略标准）
- [`07_BACKTEST_ENGINE.md`](./07_BACKTEST_ENGINE.md)：回测引擎契约（成交语义、可复现性）
- [`08_PAPER_TRADING.md`](./08_PAPER_TRADING.md)：模拟盘规则
- [`09_SIGNAL_ENGINE.md`](./09_SIGNAL_ENGINE.md)：信号引擎
- [`11_DATA_MODEL.md`](./11_DATA_MODEL.md)：数据模型
- [`12_API_SPEC.md`](./12_API_SPEC.md)：API 契约（每个端点都标注实现状态）
- [`13_UI_UX.md`](./13_UI_UX.md)：界面与用户体验（每节标注实现状态）
- [`14_SECURITY_LICENSE.md`](./14_SECURITY_LICENSE.md)：安全与许可
- [`16_AGENTS.md`](./16_AGENTS.md)：给 AI 编程工具的硬性开发规则
- [`17_DECISIONS.md`](./17_DECISIONS.md)：架构决策记录（ADR 日志，单文件追加式）
- [`29_STRATEGY_COMPILER_CONTRACT.md`](./29_STRATEGY_COMPILER_CONTRACT.md)：策略编译器契约，**已冻结**，改动需用户明确批准

## 属于项目所有者自己的文件（不要覆盖、不要提交）

`02_ARCHITECTURE.md`、`15_ROADMAP_ACCEPTANCE.md`、`31_*`、`32_*`、`33_*`，以及仓库根的 `AGENTS.md` 与 `docs/agents/`。
它们带有所有者尚未提交的改动，**不得**覆盖、删除、重置或替他提交；需要时只读参考。

## 原始设计规格包（历史起点，不必从头读）

`00_README.md`、`01_PRODUCT_SPEC.md`、`02_ARCHITECTURE.md`、`03_MODULES.md`、`05_GITHUB_STRATEGY_IMPORT.md`、
`06_AI_LAYER.md`、`10_GHOSTFOLIO_INTEGRATION.md`、`15_ROADMAP_ACCEPTANCE.md`、`18_SAMPLE_STRATEGY.md`、
`19_DEVELOPMENT_PLAYBOOK.md` 是项目开工时的设计文档。它们描述的是**当时的计划**，
其中一部分后来被实现、一部分被改写；**判断现状一律以代码与「现行文档」为准**，不要把它们当验收结论。

## 历史参考（只在追溯历史决策时读）

- [`21_PARAMETER_SENSITIVITY.md`](./21_PARAMETER_SENSITIVITY.md)、[`22_MONTE_CARLO.md`](./22_MONTE_CARLO.md)、[`23_POSITION_SIZING.md`](./23_POSITION_SIZING.md)、[`24_ENSEMBLE.md`](./24_ENSEMBLE.md)：高级分析的使用说明
- [`25_AI_QUANT_RESEARCH_LAYER_PLAN.md`](./25_AI_QUANT_RESEARCH_LAYER_PLAN.md)：AI 研究层计划
- [`26_AI_QUANT_LAYER_GAP_ANALYSIS.md`](./26_AI_QUANT_LAYER_GAP_ANALYSIS.md)、[`27_SOURCE_INGESTION_GAP_ANALYSIS.md`](./27_SOURCE_INGESTION_GAP_ANALYSIS.md)、[`28_STRATEGY_COMPILER_GAP_ANALYSIS.md`](./28_STRATEGY_COMPILER_GAP_ANALYSIS.md)：当时的缺口分析快照（**其中的待办多数已实现，未核实前不要当成待办清单**）
- [`30_PHASE_C_PERFORMANCE_RISK_BENCHMARK.md`](./30_PHASE_C_PERFORMANCE_RISK_BENCHMARK.md)：性能与风险基准
- [`34_RESEARCH_JOURNEY_CLOSURE.md`](./34_RESEARCH_JOURNEY_CLOSURE.md)：v2.7.0 研究旅程闭环的收尾记录
- [`35_OVERALL_COMPLETION_AUDIT.md`](./35_OVERALL_COMPLETION_AUDIT.md)：全项目完成度审计（含已修断点、剩余缺口与待决清单）
- [`36_NIGHTLY_DEPLOYMENT_ACCEPTANCE.md`](./36_NIGHTLY_DEPLOYMENT_ACCEPTANCE.md)：Nightly 部署后验收报告（目前只存在于工作区，未提交）
- `My_Quant_Lab_Development_Spec_V1.0.docx`、`V1.1.docx`、`V1.1.md`：从 Markdown 生成的产品规格文档，**Markdown 才是唯一事实来源**

## 最重要的设计

```text
开放社区策略
      ↓
AI 理解/转换
      ↓
Strategy DSL
      ↓
确定性回测
      ↓
OOS / Walk-forward
      ↓
Paper Trading
      ↓
Live Signal
      ↓
AI 通俗解释
```

**核心规则：AI 不代替量化引擎；所有策略必须版本化；真实投资组合与模拟盘隔离；V1 不自动交易。**
