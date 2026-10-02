# My Quant Lab — 开发文档包

这是 **My Quant Lab / 个人量化策略实验室** 的完整产品与开发规格包。

## 文档使用方式

- `00_README.md`：项目总纲
- `01_PRODUCT_SPEC.md`：产品需求
- `02_ARCHITECTURE.md`：系统架构
- `03_MODULES.md`：功能模块
- `04_STRATEGY_DSL.md`：统一策略标准，核心文件
- `05_GITHUB_STRATEGY_IMPORT.md`：社区策略持续吸收机制
- `06_AI_LAYER.md`：AI API 与模型层
- `07_BACKTEST_ENGINE.md`：回测核心
- `08_PAPER_TRADING.md`：模拟盘
- `09_SIGNAL_ENGINE.md`：实时信号
- `10_GHOSTFOLIO_INTEGRATION.md`：Ghostfolio 集成
- `11_DATA_MODEL.md`：数据模型
- `12_API_SPEC.md`：API 契约
- `13_UI_UX.md`：界面与用户体验
- `14_SECURITY_LICENSE.md`：安全和开源许可证
- `15_ROADMAP_ACCEPTANCE.md`：路线图与验收标准
- `16_AGENTS.md`：给 AI 编程工具的硬性开发规则
- `17_DECISIONS.md`：架构决策记录
- `18_SAMPLE_STRATEGY.md`：示例策略
- `19_DEVELOPMENT_PLAYBOOK.md`：分阶段 AI 编程实施方法
- `20_RESOURCE_MONITOR.md`：内置轻量系统资源监控

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
