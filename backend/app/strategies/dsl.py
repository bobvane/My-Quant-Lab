"""Unified Strategy DSL schema (see docs/04_STRATEGY_DSL.md).

Declarative first, explicit timing, no hidden state. The DSL is the only contract
shared by the validator, the backtest engine, the paper engine and the scanner.

The document shape follows the V1 specification::

    schema_version: "1.0"
    strategy: {id, name, version, source, description}
    market: {asset_classes, timeframes, allow_short}
    indicators: [{id, type, period, input}]
    features: [body_ratio, breakout, ...]
    parameters: {lookback: 20, ...}
    entry:
      long:  {all: [{op, left, right}]}
      short: {all: [...]}
    exit:
      long:  {any: [...]}
    risk:
      stop_loss: {type: atr_multiple, multiple: 2.0}
      take_profit: {type: risk_multiple, multiple: 2.0}
      max_position_pct: 0.10
    execution:
      fill_model: next_bar_open
      fee_bps: 10
      slippage_bps: 5
      allow_fractional: true
      initial_capital: 10000
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"

ComparisonOp = Literal["gt", "gte", "lt", "lte", "eq", "ne", "crosses_above", "crosses_below"]
FillModel = Literal["next_bar_open", "close_bar"]


class Condition(BaseModel):
    """A single boolean expression over price/feature columns."""

    model_config = ConfigDict(extra="forbid")

    op: ComparisonOp
    left: str
    right: str
    threshold: float | None = None


class ConditionGroup(BaseModel):
    """``all`` / ``any`` group of conditions or nested groups."""

    model_config = ConfigDict(extra="forbid")

    all: list[ConditionNode] | None = None
    any: list[ConditionNode] | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> ConditionGroup:
        if (self.all is None) == (self.any is None):
            raise ValueError("exactly one of 'all' or 'any' must be provided")
        return self


ConditionNode = Condition | ConditionGroup


class IndicatorSpec(BaseModel):
    """A declared indicator that the feature engine materialises as a column.

    ``period`` may be given directly or looked up from ``parameters`` via
    ``period_ref`` (e.g. ``period_ref: fast_period``), which makes strategies
    parameterisable without editing the rule set.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    period: int | None = None
    period_ref: str | None = None
    input: str = "close"
    params: dict[str, Any] = Field(default_factory=dict)


class SideRules(BaseModel):
    """``long`` / ``short`` rule sets of the ``entry`` or ``exit`` block."""

    model_config = ConfigDict(extra="forbid")

    long: ConditionGroup | None = None
    short: ConditionGroup | None = None


class RiskSpec(BaseModel):
    """Risk block.

    Both the nested specification form and a flat form are accepted::

        risk:
          stop_loss: {type: atr_multiple, multiple: 2.0}
          take_profit: {type: risk_multiple, multiple: 2.0}

        risk:
          stop_loss_atr_multiple: 2.0
          take_profit_r_multiple: 2.0
    """

    model_config = ConfigDict(extra="forbid")

    stop_loss_atr_multiple: float | None = Field(default=None, gt=0)
    take_profit_r_multiple: float | None = Field(default=None, gt=0)
    take_profit_atr_multiple: float | None = Field(default=None, gt=0)
    max_position_pct: float | None = Field(default=None, gt=0, le=1)

    @model_validator(mode="before")
    @classmethod
    def _normalise_nested(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        stop = data.pop("stop_loss", None)
        take = data.pop("take_profit", None)
        if isinstance(stop, dict):
            if stop.get("type") == "atr_multiple":
                data["stop_loss_atr_multiple"] = stop.get("multiple")
            else:
                raise ValueError("unsupported stop_loss type; V1 supports 'atr_multiple'")
        elif isinstance(stop, (int, float)):
            data["stop_loss_atr_multiple"] = stop
        if isinstance(take, dict):
            kind = take.get("type")
            if kind == "risk_multiple":
                data["take_profit_r_multiple"] = take.get("multiple")
            elif kind == "atr_multiple":
                data["take_profit_atr_multiple"] = take.get("multiple")
            else:
                raise ValueError(
                    "unsupported take_profit type; V1 supports 'risk_multiple' or 'atr_multiple'"
                )
        elif isinstance(take, (int, float)):
            data["take_profit_r_multiple"] = take
        return data

    @model_validator(mode="after")
    def _at_least_one(self) -> RiskSpec:
        if not any(
            [
                self.stop_loss_atr_multiple,
                self.take_profit_r_multiple,
                self.take_profit_atr_multiple,
            ]
        ):
            raise ValueError("risk block must define a stop loss or a take profit")
        return self


OrderType = Literal["market", "limit", "stop"]

SizingMode = Literal["fixed_fraction", "risk_per_trade", "atr_risk"]


class SizingSpec(BaseModel):
    """How much to buy/sell on entry (docs/23, ADR-045).

    ``fixed_fraction`` is the historical behaviour and the default, so existing
    strategy versions keep producing exactly the same numbers.
    """

    model_config = ConfigDict(extra="forbid")

    mode: SizingMode = "fixed_fraction"
    # Fraction of *current cash* to deploy. Ignored unless mode=fixed_fraction.
    fraction: float | None = Field(default=None, gt=0, le=1)
    # Fraction of current equity to risk between entry and the stop.
    risk_pct: float = Field(default=0.01, gt=0, le=1)
    # ATR multiple used as the risk distance when mode=atr_risk.
    atr_multiple: float = Field(default=2.0, gt=0)

    @model_validator(mode="after")
    def _check(self) -> SizingSpec:
        if self.mode == "risk_per_trade" and self.risk_pct <= 0:
            raise ValueError("risk_per_trade requires risk_pct > 0")
        return self


class ExecutionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fill_model: FillModel = "next_bar_open"
    entry_order_type: OrderType = "market"
    limit_offset_atr: float | None = Field(default=None, ge=0)
    stop_offset_atr: float | None = Field(default=None, ge=0)
    order_valid_bars: int = Field(default=1, ge=1, le=100)
    fee_bps: float = Field(default=0.0, ge=0)
    slippage_bps: float = Field(default=0.0, ge=0)
    allow_fractional: bool = True
    initial_capital: float = Field(default=10_000.0, gt=0)
    sizing: SizingSpec = Field(default_factory=SizingSpec)

    @field_validator("fee_bps", "slippage_bps")
    @classmethod
    def _cap_bps(cls, value: float) -> float:
        if value > 1000:
            raise ValueError("fee/slippage must be <= 1000 bps (10%)")
        return value


class MarketSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_classes: list[str] = Field(default_factory=lambda: ["stock"])
    timeframes: list[str] = Field(default_factory=lambda: ["1d"])
    allow_short: bool = False


class StrategyBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: str
    description: str | None = None
    source: dict[str, Any] = Field(default_factory=dict)


class StrategySpec(BaseModel):
    """Full strategy document."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    strategy: StrategyBlock
    market: MarketSpec = Field(default_factory=MarketSpec)
    indicators: list[IndicatorSpec] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    entry: SideRules
    exit: SideRules = Field(default_factory=SideRules)
    risk: RiskSpec | None = None
    execution: ExecutionSpec = Field(default_factory=ExecutionSpec)

    @model_validator(mode="after")
    def _check_entry(self) -> StrategySpec:
        if self.entry.long is None and self.entry.short is None:
            raise ValueError("at least one entry rule (entry.long / entry.short) is required")
        if self.entry.short is not None and not self.market.allow_short:
            raise ValueError("entry.short requires market.allow_short = true")
        if self.exit.long is None and self.exit.short is None:
            raise ValueError("at least one exit rule is required")
        return self


ConditionGroup.model_rebuild()
