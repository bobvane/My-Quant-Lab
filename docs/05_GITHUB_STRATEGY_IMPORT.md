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

- `POST /importer/github/analyze` 返回 `coverage` 块、`files_parsed` / `files_inventoried` / `files_skipped[{path, reason}]` / `files_unparsed[{path, reason}]`，并把覆盖率结论写进 `warnings`（"25 candidate file(s) were never fetched…"）。
- `analysis_version`（当前 `1.4.0`）随字段语义变化提升：`files_scanned` 曾把"只登记未解析"的非 Python 文件也算成已扫描，`files_skipped` 曾只有路径、丢掉 `skipped_reason`；`1.2.0` 起 `coverage` 区分"没读是因为上限"与"没读是因为时间预算用完了"；`1.3.0` 起"下载到了但解析失败"的 Python 文件不再被算作已解析（见 §4.3）；`1.4.0` 起报告用 commit（而不是分支名）命名它读的那个修订（见 §4.4）。
- **无人值守的 watcher 不得从不完整的读取中自动导入**：若还有 Python 文件没被读到（超出抓取上限或下载失败），或有 Python 文件下载到了却解析失败，`check_source` 记 `last_import_status = "incomplete"`、写 `GitHubSnapshot.extraction_json = {"imported": false, "reason": ..., "transient": ..., "coverage": ..., "files_unparsed": [...], "warnings": ...}`，并且**不新建策略版本**——变化的规则可能就在没读到的（或没看懂的）文件里，导入部分草案等于静默降级策略。
- 只登记不解析的 `.md`/`.json` **不**阻断导入（这是常见情况），但会出现在报告里。

### 4.2 抓取时间预算（ADR-057）

单次请求的超时**约束不了整个循环**：30 个文件 × 2 次尝试 × 15s 是十几分钟的等待。所以 `fetch_repository` 还接受一个**墙钟预算** `max_seconds`（默认 `DEFAULT_FETCH_BUDGET_SECONDS = 120`，端点字段 `max_seconds` 默认 120、范围 10–600）。预算用完时：

- 停止抓取，剩余候选记入 `coverage.not_attempted_files`，并把 `coverage.budget_exhausted` 置为 `true`、`max_seconds` 记下实际预算值；
- `warnings` 说的是"the fetch stopped after 120s: N candidate file(s) were left unread. Raise the time budget or lower max_files…"，而不是把责任推给上限——两者的建议相反（`max_files` 根本没被碰到）；
- 客户端可以据此区分"仓库太大"（上限）与"网络太慢"（预算），后者重试有意义。

抓取**必须**把配置的超时传给每一个 HTTP 调用：`GitHubClient(timeout=15.0)` 曾经只作用于文件下载，`get_json`（repo / tree / commit）静默使用 httpx 的默认值。

watcher 里这条区别决定了是否"记为已见"：上限造成的缺口是结构性的（同一个 commit 再读一次结果相同），标记已见以免每次调度都重复几十次请求；预算/网络造成的缺口是**瞬时**的（`extraction_json["transient"] = true`），此时**不推进 `current_commit`**，下一轮调度会重试——否则一次网络抖动就等于永久放弃这次更新。

### 4.3 读到了 ≠ 看懂了（ADR-059）

覆盖率回答的是"读到了多少"，不回答"看懂了没有"。一个 `.py` 可以**下载成功却完全无法解析**：Python 2 的 `print 'x'`、内容里的 NUL 字节、解析器拒绝的构造。这样的文件贡献是**零**（没有规则、没有指标、连一条 unknown 都没有），它以前却被无条件写进 `files_parsed`：`coverage.complete` 照样是 `true`、`unread_python_files` 是 0、`warnings` 一句不说，于是 watcher 照常无人值守导入——规则藏在这个文件里的策略被静默降级。这是 ADR-056/057/058 的同族缺陷，只是深了一层。

- `AnalysisResult.files_unparsed: list[SkippedFile]`（`{path, reason}`）记录**下载成功但未解析**的文件；`build_coverage()` 给出 `coverage.unparsed_python_files`；`coverage_warnings()` 追加"2 Python file(s) were downloaded but did not parse, so nothing in them was understood and the rules they declare are missing (see files_unparsed)."
- 解析失败**不再**记成 `unknowns` 里的 `category="unparseable"`：那是"无法映射的构造"的映射报告，而这是"整份文件没被看懂"，两者混在一起时，后者唯一的痕迹就是那句"N construct(s) could not be mapped"。
- 解析期异常**捕获 `Exception`**（不只 `SyntaxError`），因为仓库代码是不可信输入：任何解析期失败都必须变成"这个文件没被看懂"，而不是让整份报告 500。
- watcher 的拒绝条件因此是 `unread_python_files > 0 or unparsed_python_files > 0`；快照 `reason` 为 `incomplete_analysis`（有没读到的）或 `unparseable_python`（读到了但没看懂）。解析失败与上限缺口一样是**结构性**的（`transient = false`，重读不会让它变得可解析），所以照常推进 `current_commit`，不会每轮重试。
- 前端：头部行列出"解析 P 个 Python、N 个解析失败"，`files_unparsed` 有独立 `<details>` 表格给出文件名与解析错误；来源详情面板对 `unparseable_python` 给出对应解释。

### 4.4 一个修订必须用 commit 命名（ADR-060）

`ref` 是**请求**，`commit` 是**事实**。同一个分支名 `main` 今天指向的代码和明天指向的不是同一份，所以"我读了 `main`"这句话事后无法复读：报告不能复现、策略来源不能核对，watcher 也只能拿一个 SHA 去和它比较。分析路径以前把分支名既当作抓取的 ref、又当作记录的修订：`get_tree` / `get_raw_file` 都用 `ref` 请求、`RepoMeta` 只有 `ref`、导入时 `StrategyVersion.source_commit` 与 `GitHubSource.current_commit` 直接写分支名、快照也记在 `commit="main"` 上——抓取进行到一半时仓库被推了新提交，这份报告就会把两个修订混在一起，而事后无法分辨。

- `fetch_repository` 先把 `ref` 解析成 commit（`resolve_commit`：`GET /repos/{owner}/{repo}/commits/{ref}`），然后**每一次**读取（tree 与每个文件）都用那个 SHA；`RepoMeta.commit` 是必填字段，`ref` 只表示"当初要的是哪个名字"。
- `POST /importer/github/analyze` 的响应同时给出 `ref` 与 `commit`；页面头部显示 `owner/repo @ ref · commit <12 位>`。解析不出 SHA 时**报错**（`could not resolve ref ...`），不会把一个名字冒充成修订。
- watcher 本来就是拿 `get_head_commit()` 的 SHA 与 `current_commit` 比较，所以传给抓取器的已经是 commit，`resolve_commit` 原样返回——**不额外发一次请求**。
- 导入请求**必须**带 `commit`（`GithubImportRequest.commit`，7–64 位十六进制；不传或不合法 → 422）：人工审阅过的是某一个修订，不是"main 当时的样子"。`StrategyVersion.source_commit`、`evidence_json`、审计记录与来源快照都记这个 SHA；`_persist_github_source` **无条件**记快照（旧代码的 `if ref and ref != "HEAD"` 守卫会让不带 ref 的导入一条记录都没有）。
- 旧数据里 `source_commit` 可能是分支名（如 `main`）。前端把不像 SHA 的值渲染成 `main（ADR-060 之前记的是分支名）`，而不是让它看起来像一个 commit。
- `analysis_version` 提升到 `1.4.0`：响应多了 `commit`，且"读的是哪个修订"的语义变了。

### 4.5 版本号由拥有账本的一方分配（ADR-061）

策略版本一旦创建就不可修改，所以"这个名字的第几版"是一份**账本**，它的所有者是服务端，不是调用方。旧实现把这个决定推给了调用方：`GithubImportRequest.version` 直接默认 `"1.0.0"`，Web UI 更是在 `importReviewed` 里硬编码 `'1.0.0'`——于是同一个仓库第二次导入必然撞上 `version '1.0.0' already exists for this strategy`（422），而页面上没有任何地方能改版本号。调用方既看不到已有版本，也无从知道该填什么；它唯一能做的就是猜，猜错就失败。

- `GithubImportRequest.version` 变成**可选**（`None` = 由服务端分配）。省略时 `import_strategy` 调用 `strategy_version_plan`（`app/data/strategy_service.py`），用 `next_version` 取**下一个空闲补丁号**：没有任何版本 → `1.0.0`；已有 `1.0.0` → `1.0.1`；已有 `1.0.0`/`1.0.9` → `1.0.10`。比较按 `major/minor/patch` 三个整数做，所以 `1.9.0` 之后是 `1.10.0` 而不是字符串比较得到的 `1.9.1`。
- 账本里出现**读不成 `major.minor.patch` 的版本**（例如审阅者自己命名的 `v2-beta`）时，服务端**拒绝分配**（422，理由里点名那个版本），而不是发明一个可能撞车的号；此时调用方必须显式给版本号。人工命名的版本仍然合法，它只是不能被自动递增。
- `GET /importer/github/versions?name=...` 在**写任何东西之前**回答"这个名字现在有什么、下一个会是什么"：`{name, slug, strategy_id, versions, next_version, can_assign, reason}`。UI 用它来（a）显示"将新建策略 / 将在策略 #N 上创建版本 x.y.z"，（b）在审阅者手填的版本号已经存在时直接禁用导入按钮——不再让人点下去才发现 422。
- 导入响应新增 `version_assigned`（`true` = 服务端分配），`evidence_json` 与审计记录也记它。`version` 由审阅者命名时行为不变（重复仍然 422）。

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

### 7.1 一次检查的结果必须能自解释（ADR-058）

`check_source` 的返回值与写进 `GitHubSource.last_import_status` 的值使用**同一套词表**：

| 状态 | 含义 | 是否推进 `current_commit` |
| --- | --- | --- |
| `unchanged` | head 与 `current_commit` 相同，**什么都没抓取** | 不变 |
| `no_change` | 抓取并分析了一个新 commit，但抽出的 DSL 没有变化（或没有草案） | 推进 |
| `imported` | 从新 commit 生成了新的策略版本 | 推进 |
| `incomplete` | 读取不完整（还有 Python 文件没读到），拒绝无人值守导入 | 结构性缺口推进；瞬时缺口（`transient`）不推进，下轮重试 |
| `error` | 网络/解析失败，没有结论 | 不变 |

在 v1.4.8 之前，`unchanged`、`no_change` 与「已检查但没有变化」这三种截然不同的结果**全部**被写成 `last_import_status = "checked"`，于是 UI 里一行「已检查」既可能是「没有任何新东西」，也可能是「抓取并分析过了，策略没变」——用户无法分辨 watcher 到底有没有干活。旧记录里残留的 `checked` 属于历史值，UI 明确标注为「已检查（旧记录）」，不猜测它的具体含义。

`check_github_sources` 的汇总不再硬编码状态列表：它先返回 `checked`（本轮检查过的来源数），再把**本轮实际产生的每一种结果**按原样附上，因此汇总与 `check_source` 的词表不会各自漂移。

原因（`imported` / `reason` / `transient` / `coverage` / `warnings`）写在 `GitHubSnapshot.extraction_json` 里，`GET /importer/github/sources/{id}/snapshots` 必须把它返回给调用方——只返回 commit 等于把「为什么是这个状态」留在数据库里，用户看到的就只是一个英文枚举。人工导入产生的快照同样记录 `{"imported": true, "reason": "manual_import"}`，不留空对象。

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
Ref：main
Commit：9f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f60718293
类型：Breakout
已识别：EMA20 / ATR14 / breakout confirmation
覆盖：读取 12 / 30 个候选文件（解析 9 个 Python、登记 3 个非 Python）
警告：原项目使用当前未确认 K 线，已按系统规则改为 closed bar
警告：18 个候选文件从未获取（上限 12）；9 个 Python 文件没被读到
警告：1 个 Python 文件下载到了但没能解析（legacy.py），它里面的规则不在本次发现里
状态：Experimental
```

人审的前提是报告说清了"到底看了多少、看懂了没有"。未读到的文件正是没被审阅的代码，而**读到了却没能解析**的文件贡献同样是零，两者都必须出现在用户最终看到的这一段里，而不是只躺在日志中。`Ref` 只说"当初要的是哪个名字"，`Commit` 才是这份报告（以及导入出来的策略版本）真正对应的修订：事后要复查"这个策略是从哪份代码来的"，只有 `Commit` 能被重新读取（§4.4）。