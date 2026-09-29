"""Simulation layer: paper trading and the live signal engine."""

from app.simulation.signal_engine import ScanResult, latest_intent_for_series, scan_all, scan_series

__all__ = ["ScanResult", "latest_intent_for_series", "scan_all", "scan_series"]
