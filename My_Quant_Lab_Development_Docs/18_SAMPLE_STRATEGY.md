# 18 Sample Strategy — PA Breakout V1

该示例仅用于验证系统结构，不代表真实投资建议，也不应视为已经验证有效的策略。

## Intent

识别收盘价突破过去 N 根 K 线高点、并满足趋势/波动确认条件的事件。

## Example DSL

```yaml
schema_version: "1.0"
strategy:
  id: "sample-pa-breakout"
  name: "Sample PA Breakout"
  version: "1.0.0"
  source:
    type: "builtin"

market:
  asset_classes: [stock, crypto]
  timeframes: [1d]

parameters:
  lookback: 20
  ema_period: 20
  atr_period: 14
  atr_stop_multiple: 2.0

indicators:
  - id: ema
    type: EMA
    period_ref: ema_period
    input: close
  - id: atr
    type: ATR
    period_ref: atr_period
    input: ohlc

entry:
  long:
    all:
      - {op: gt, left: close, right: rolling_high_prev}
      - {op: gt, left: close, right: ema}

exit:
  long:
    any:
      - {op: lt, left: close, right: ema}
      - {op: lte, left: close, right: stop_price}

risk:
  stop_loss:
    type: atr_multiple
    multiple_ref: atr_stop_multiple

execution:
  fill_model: next_bar_open
  fee_bps: 10
  slippage_bps: 5
```

## Expected system behavior

Backtest engine computes rolling high from data strictly before current signal bar. It does not use current bar's high as a known breakout threshold.

Signal output is a structured fact; AI later explains it.
