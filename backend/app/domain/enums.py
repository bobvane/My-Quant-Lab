"""Domain enumerations.

These enums are the single source of truth for allowed values. They are stored as
strings in the database so that historical rows stay readable.
"""

from __future__ import annotations

from enum import StrEnum


class AssetClass(StrEnum):
    STOCK = "stock"
    ETF = "etf"
    CRYPTO = "crypto"
    INDEX = "index"
    FUTURE = "future"


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"


class StrategySourceType(StrEnum):
    BUILTIN = "builtin"
    GITHUB = "github"
    CUSTOM = "custom"
    AI_GENERATED = "ai_generated"


class StrategyStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class StrategyValidationStatus(StrEnum):
    PENDING = "pending"
    VALID = "valid"
    INVALID = "invalid"


class StrategyLifecycle(StrEnum):
    IMPORTED = "imported"
    NORMALIZED = "normalized"
    VALIDATED = "validated"
    BACKTESTED = "backtested"
    OOS_TESTED = "oos_tested"
    PAPER_TRADING = "paper_trading"
    REFERENCE_SIGNAL = "reference_signal"
    DEGRADED = "degraded"
    RETIRED = "retired"


class SignalState(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"
    NO_SIGNAL = "NO_SIGNAL"


class SignalDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PaperAccountStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    CLOSED = "closed"


class PaperOrderStatus(StrEnum):
    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class DataQualityStatus(StrEnum):
    UNKNOWN = "unknown"
    VALID = "valid"
    PARTIAL = "partial"
    INVALID = "invalid"


class AIProviderType(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"


class AITaskType(StrEnum):
    DAILY_SUMMARY = "daily_summary"
    STRATEGY_EXPLANATION = "strategy_explanation"
    STRATEGY_REVIEW = "strategy_review"
    BACKTEST_ANALYSIS = "backtest_analysis"
    RESEARCH_REPORT = "research_report"
    REPOSITORY_ANALYSIS = "repository_analysis"
    SIGNAL_EXPLANATION = "signal_explanation"


class AITaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
