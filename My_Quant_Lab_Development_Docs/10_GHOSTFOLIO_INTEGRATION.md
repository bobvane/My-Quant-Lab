# 10 Ghostfolio Integration

## 1. 定位

Ghostfolio 是真实投资组合与交易记录的"事实源"，不是 Quant Lab 的主要 OHLCV 数据源。

## 2. 推荐方式

V1 优先使用 Ghostfolio REST API，而不是直接依赖其 PostgreSQL/Prisma 内部 schema。

原因：降低版本耦合，未来 Ghostfolio 更新时更稳定。

## 3. 需要读取的内容

- activities/orders
- accounts
- symbol profiles
- portfolio holdings
- portfolio details/weights where available
- asset metadata
- market price history only as辅助信息

Quant Lab 不应依赖 Ghostfolio 的内部表结构来计算自己的回测。

## 4. Credentials

支持：

- endpoint
- bearer token / access token
- optional tenant/user context if required by deployment

Key/token 加密存储并掩码显示。

## 5. Sync strategy

首次同步：full import。
以后：incremental sync by updated/date range if endpoint permits。

本地数据库保存：

- source_id
- remote_id
- sync_timestamp
- source_hash

## 6. Data mapping

建议内部模型：

```text
Ghostfolio Activity
→ PortfolioTransaction
Ghostfolio SymbolProfile
→ AssetProfile
Ghostfolio Holding
→ PortfolioHoldingSnapshot
```

## 7. Conflict policy

Ghostfolio 是 real portfolio source of truth。

Quant Lab 不修改 Ghostfolio。

如果本地缓存与 Ghostfolio 不一致：

- 标记 sync conflict
- 优先重新同步
- UI 显示数据同步异常

## 8. MCP

如果未来使用 Ghostfolio MCP，只用于不需要货币数值的辅助读取；涉及持仓数量、金额、成本或权重的核心同步仍以 REST Adapter 为主。
