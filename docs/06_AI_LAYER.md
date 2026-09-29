# 06 AI Layer / AI 智能层

## 1. 总体目标

让专业工作被 AI 和量化引擎承担，用户得到通俗、可操作、可追溯的结果。

## 2. Provider abstraction

```python
class AIProvider(Protocol):
    async def generate_structured(...): ...
    async def generate_text(...): ...
```

配置：
- provider_name
- base_url
- api_key
- model
- timeout
- max_tokens
- temperature
- daily_budget
- enabled

优先支持 OpenAI-compatible API，因此可以接入不同供应商。

## 3. AI 角色

### R1 Strategy Researcher
阅读 GitHub 项目，识别策略逻辑。

### R2 Quant Tutor
把指标、统计结果、回测概念翻译成人话。

### R3 Signal Explainer
解释已经由规则引擎确定的 BUY/SELL/WAIT。

### R4 Research Assistant
根据测试结果提出下一步研究假设，但不能修改历史结果。

### R5 Daily Analyst
生成每日摘要。

## 4. AI 不可信边界

LLM 输出必须经过 schema validation。

AI 不得直接产生以下事实：
- 当前价格
- 历史收益率
- 胜率
- 最大回撤
- 持仓数量
- 账户余额

这些数据必须来自数据库/行情/回测计算。

AI 只能引用 system-provided structured facts。

## 5. Prompt 分层

每个 AI 任务使用：

```text
System Prompt
+ Task Prompt
+ Structured Data
+ Provenance Metadata
```

Prompt 必须版本化，例如 `signal_explain@1.2.0`。

## 6. AI 输出协议

Signal Explanation：

```json
{
  "summary": "...",
  "why": ["..."],
  "what_could_invalidate": ["..."],
  "what_to_watch_next": ["..."],
  "risk_notes": ["..."],
  "plain_language": "..."
}
```

不得要求模型输出"保证盈利""确定上涨等结论"。

## 7. Model routing

按任务分层：

- Low cost：日报、基础解释、普通策略摘要
- Medium：策略理解、一般回测解读
- High capability：复杂 GitHub 项目、复杂策略组合、异常结果分析

可以按每日预算自动切换或拒绝非关键任务。

## 8. Cache

同一：

- prompt version
- model
- structured input hash

产生的解释可以缓存。

实时价格每次变化导致 input hash 变化时，缓存失效。

## 9. 用户体验

优先展示：

> "发生了什么？"
> "为什么？"
> "需要关注什么？"

再提供：

> "查看专业数据"\n
## 10. AI Daily Budget

系统提供预算设置：

- ¥2/day
- ¥5/day
- ¥10/day
- custom

预算达到阈值以后：
- 仍允许量化计算
- 停止非关键 AI 任务
- 核心信号解释可以按优先级保留
