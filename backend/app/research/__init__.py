"""Research layer: backtest engine, metrics and walk-forward analysis."""

from app.research.engine import ENGINE_VERSION, BacktestResult, run_backtest
from app.research.metrics import Metrics, compute_metrics
from app.research.walk_forward import WalkForwardWindow, run_walk_forward

__all__ = [
    "ENGINE_VERSION",
    "BacktestResult",
    "Metrics",
    "WalkForwardWindow",
    "compute_metrics",
    "run_backtest",
    "run_walk_forward",
]
