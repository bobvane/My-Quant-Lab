# 05 GitHub Strategy Importer

## 1. 目标

持续吸收社区中的量化/K线策略，但做到“可追溯、可验证、可撤销、不可静默改变”。

## 2. 导入入口

V1：用户手动输入 GitHub repository URL。
P1：监控 repository branch/tag/release。
P2：GitHub search/source watcher。

## 3. 导入流水线

```text
Repository URL
→ fetch metadata
→ snapshot commit
→ repository inventory
→ locate candidate strategy files
→ AST/static analysis
→ AI strategy extraction
→ DSL draft
→ rule validator
→ lookahead detector
→ backtest compatibility test
→ import report
→ normalized strategy version
```

## 4. 不可信代码原则

GitHub 是不可信输入。绝不能：

- 在 API 进程中 import 任意用户仓库模块。
- 给导入代码 NAS 主机网络权限。
- 把宿主机目录暴露为可写目录。
- 把 GitHub 仓库里的 shell script 当成部署脚本直接执行。

需要执行原始代码时必须使用隔离 worker：
- 独立容器
- 非 root
- 网络默认关闭
- CPU/memory/time quota
- 只读源码挂载
- 最小文件系统权限
- 执行结束销毁容器

V1 可以完全不执行原始代码，只做 AST/文本/AI 提取和 DSL 重建。

## 5. AI Extraction 输出

必须结构化：

```json
{
  "strategy_candidates": [
    {
      "name": "Breakout strategy",
      "entry_rules": [],
      "exit_rules": [],
      "indicators": [],
      "parameters": [],
      "timeframes": [],
      "asset_classes": [],
      "risk_rules": [],
      "execution_assumptions": [],
      "unknowns": [],
      "evidence_files": []
    }
  ],
  "confidence": "extraction_confidence",
  "warnings": []
}
```

`confidence` 只是 AI 对“我是否正确理解代码”的信心，不是策略盈利概率。

## 6. Evidence-first

AI 提取出的每一项规则都应尽可能指向：
- 文件路径
- 行号范围
- 函数名
- 原始代码片段 hash

UI 中显示“来源证据”。

## 7. 更新检测

如果 watched repository 的 commit 变化：

```text
old commit
→ diff
→ identify strategy-affecting changes
→ new Strategy version
→ new validation
```

如果只是 README 拼写变化，可以标记为 metadata-only，不生成策略新版本。

## 8. License handling

导入器必须记录仓库许可证、commit、作者、URL、导入时间。

默认规则：
- 不复制大段源代码到策略库。
- 不删除原作者和许可证信息。
- 只保存必要的规则摘要和可复现 provenance。
- 如果项目许可证未知/不允许再分发，策略仍可作为个人研究输入，但 UI 应明确显示 license warning。
- 开源发布本项目时，对任何直接复制的代码、prompt、规则文本做单独许可证审查。

## 9. Import result

用户最终看到：

```text
导入成功
策略：Adaptive Breakout
来源：github.com/example/repo
Commit：abc123
类型：Breakout
已识别：EMA20 / ATR14 / breakout confirmation
警告：原项目使用当前未确认 K 线，已按系统规则改为 closed bar
状态：Experimental
```
