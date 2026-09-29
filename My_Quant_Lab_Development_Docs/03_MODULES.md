# 03 功能模块规范

## M01 Dashboard

目标：让不懂量化的用户一眼看到“现在有什么值得关注的事情”。

必须显示：今日信号、模拟账户概况、策略运行状态、数据健康、Ghostfolio 当前组合摘要。

## M02 Market Data

职责：
- 下载/更新 OHLCV
- 数据去重和排序
- 时区统一
- 缺失数据检测
- corporate action / adjusted price 标记
- provider source 和版本记录

数据必须带：symbol、asset_class、timeframe、timestamp、source、is_closed。

## M03 Feature Engine

初始指标：EMA20、EMA50、SMA20、ATR14、RSI14、MACD、Bollinger Bands。

初始 Price Action 特征：
- body_ratio
- upper_wick_ratio
- lower_wick_ratio
- close_position
- range_atr_ratio
- ema_relation
- overlap
- inside_bar_sequence
- outside_bar
- micro_double
- breakout
- breakout_follow_through
- breakout_failure
- distance_to_ema

所有特征都必须是 deterministic function。

## M04 Strategy Library

功能：
- 创建策略
- 导入策略
- 编辑元数据
- 版本化
- 激活/停用
- 查看血统
- 查看测试结果

策略本身不得把数据库访问、HTTP 调用和通知混在规则函数里。

## M05 GitHub Strategy Importer

见《05_GITHUB_STRATEGY_IMPORT.md》。

## M06 Backtest

见《07_BACKTEST_ENGINE.md》。

## M07 Paper Trading

见《08_PAPER_TRADING.md》。

## M08 Signal Engine

见《09_SIGNAL_ENGINE.md》。

## M09 AI

见《06_AI_LAYER.md》。

## M10 Ghostfolio Adapter

见《10_GHOSTFOLIO_INTEGRATION.md》。

## M11 Notification

V1 至少支持 Generic Webhook。
P1 支持 Feishu、Telegram、Email、PushPlus。

通知内容必须包含：symbol、signal、strategy、timeframe、timestamp、reason、link；禁止把 AI 文案当成收益承诺。

## M12 Audit & Observability

必须记录：
- job start/end/error
- strategy version
- data source/version
- backtest parameters
- AI provider/model/prompt version
- signal generation inputs hash
- user configuration changes

## M13 Settings

提供：
- Ghostfolio endpoint/token
- market data provider keys
- AI provider/key/model
- notification webhook
- timezone
- default currency
- paper account defaults
- fees/slippage defaults
- scan schedule

Secrets UI 只能显示掩码；日志绝不能输出完整 key。
