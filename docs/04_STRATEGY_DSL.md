# 04 Unified Strategy DSL / 统一策略规范

## 1. 目标

Strategy DSL 是 My Quant Lab 的核心稳定契约。所有来自 GitHub、自定义或 AI 生成的策略，最终都要映射到 DSL。

DSL 必须能被：
- validator
- backtest engine
- paper engine
- signal scanner

共同消费。

## 2. V1 DSL 示例

```yaml
schema_version: "1.0"
strategy:
  id: "pa-breakout"
  name: "PA Breakout"
  version: "2.0.0"
  source:
    type: "github"
    repository: "example/repo"
    commit: "abc123"
  description: "Price Action breakout with confirmation"

market:
  asset_classes: [stock, crypto]
  timeframes: [1d]

indicators:
  - id: ema20
    type: EMA
    period: 20
    input: close
  - id: atr14
    type: ATR
    period: 14

features:
  - body_ratio
  - breakout
  - breakout_follow_through

entry:
  long:
    all:
      - { op: gt, left: close, right: previous_high }
      - { op: gt, left: volume, right: volume_sma_20 }
      - { op: gt, left: close, right: ema20 }

exit:
  long:
    any:
      - { op: lt, left: close, right: ema20 }
      - { op: lt, left: close, right: stop_price }

risk:
  stop_loss:
    type: atr_multiple
    multiple: 2.0
  take_profit:
    type: risk_multiple
    multiple: 2.0
  max_position_pct: 0.10

execution:
  fill_model: next_bar_open
  fee_bps: 10
  slippage_bps: 5
  allow_fractional: true

filters:
  market_regime: optional

outputs:
  signal_states: [BUY, SELL, WAIT]
```

## 3. DSL 设计要求

### 3.1 Declarative first
优先使用声明式条件，不允许导入器直接把任意 Python 代码塞进 DSL。

### 3.2 No hidden state
所有跨 K 线状态必须显式声明，例如 rolling high、previous close、position state。

### 3.3 Explicit timing
每个条件必须有 evaluation timestamp；执行必须显式说明 next bar/open/close 等。

### 3.4 Version immutable
策略修改必须生成新 version；已有 backtest run 永远指向原版本。

## 4. Strategy output

内部标准输出：

```json
{
  "state": "BUY",
  "direction": "LONG",
  "symbol": "NVDA",
  "timeframe": "1d",
  "strategy_version": "pa-breakout@2.0.0",
  "bar_time": "2026-09-29T20:00:00Z",
  "triggered_rules": ["entry.long.0", "entry.long.1"],
  "price_reference": 180.25,
  "stop_reference": 174.10,
  "target_reference": 192.55,
  "feature_snapshot_id": "..."
}
```

AI 不负责生成上述事实字段；它只解释这些字段。

## 5. 策略类型

V1：RULE_BASED、PRICE_ACTION、COMPOSITE。

P2：STATISTICAL、ML、LLM_ASSISTED。

ML/LLM 策略必须增加模型版本、训练区间和特征血统，不能与简单规则策略混淆。

---

## 6. 实现说明（当前 V1 schema 边界）

本文档的示例是「目标形态」。当前 V1 的 `StrategySpec`（backend/app/strategies/dsl.py）以 `extra="forbid"` 严格解析，实际支持：

- 指标：EMA / SMA / RSI / ATR / MACD / Bollinger（`indicators[].id` 会被物化为特征列；周期可用 `period` 或 `period_ref` 指向 `parameters`）。
- 条件算子：gt / gte / lt / lte / eq / ne / crosses_above / crosses_below。
- 列的别名：`previous_high`→`prior_high`、`previous_low`→`prior_low`、`rolling_*_prev`；成交量均线 `volume_sma_20`。
- 执行：`fill_model`(next_bar_open/close_bar)、`entry_order_type`(market/limit/stop) + `limit_offset_atr`/`stop_offset_atr`/`order_valid_bars`、`fee_bps`、`slippage_bps`、`allow_fractional`、`initial_capital`。

**尚未支持（示例中的示意项）**：`filters` / `outputs` 顶级块、`stop_price` 之类未注册列。引用它们会被 schema/校验器拒绝（刻意如此：宁可拒绝，也不静默忽略）。
