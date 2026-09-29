"""Strategy DSL: schema, validator and deterministic executor."""

from app.strategies.dsl import SCHEMA_VERSION, StrategySpec
from app.strategies.executor import SignalIntent, evaluate_group, run_strategy
from app.strategies.validator import ValidationReport, validate_strategy

__all__ = [
    "SCHEMA_VERSION",
    "SignalIntent",
    "StrategySpec",
    "ValidationReport",
    "evaluate_group",
    "run_strategy",
    "validate_strategy",
]
