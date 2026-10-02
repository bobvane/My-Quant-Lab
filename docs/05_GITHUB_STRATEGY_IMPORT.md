# 05 GitHub Strategy Importer

## 1. 目标

持续吸收社区中的量化/K线策略，但做到"可追溯、可验证、可撤销、不可静默改变"。

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

### 4.1 覆盖率（coverage，ADR-056）

"不可信输入"意味着报告本身也不能夸大它做过的事。分析报告必须回答：**候选文件共几个、实际下载几个、真的解析几个、只登记几个、跳过几个及原因、有几个候选从未尝试**。

- `POST /importer/github/analyze` 返回 `coverage` 块、`files_parsed` / `files_inventoried` / `files_skipped[{path, reason}]`，并把覆盖率结论写进 `warnings`（"25 candidate file(s) were never fetched…"）。
- `analysis_version`（当前 `1.1.0`）随字段语义变化提升：`files_scanned` 曾把"只登记未解析"的非 Python 文件也算成已扫描，`files_skipped` 曾只有路径、丢掉 `skipped_reason`。
- **无人值守的 watcher 不得从不完整的读取中自动导入**：若还有 Python 文件没被读到（超出抓取上限或下载失败），`check_source` 记 `last_import_status = "incomplete"`、写 `GitHubSnapshot.extraction_json = {"imported": false, "reason": "incomplete_analysis", "coverage": ..., "warnings": ...}`，并且**不新建策略版本**——变化的规则可能就在没读到的文件里，导入部分草案等于静默降级策略。
- 只登记不解析的 `.md`/`.json` **不**阻断导入（这是常见情况），但会出现在报告里。

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

"confidence" 只是 AI 对"我是否正确理解代码"的信心，不是策略盈利概率。

## 6. Evidence-first

AI 提取出的每一项规则都应尽可能指向：
- 文件路径
- 行号范围
- 函数名
- 原始代码片段 hash

UI 中显示"来源证据"。

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
覆盖：读取 12 / 30 个候选文件（解析 9 个 Python、登记 3 个非 Python）
警告：原项目使用当前未确认 K 线，已按系统规则改为 closed bar
警告：18 个候选文件从未获取（上限 12）；9 个 Python 文件没被读到
状态：Experimental
```

人审的前提是报告说清了"到底看了多少"。未读到的文件正是没被审阅的代码，所以它们必须出现在用户最终看到的这一段里，而不是只躺在日志中。