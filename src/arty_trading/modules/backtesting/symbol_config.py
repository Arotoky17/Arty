"""Shared operational symbol definitions for live and backtest."""

from arty_trading.config.operational import apply_operational_definitions


def configure_detector_for_symbol(detector, symbol: str) -> None:
    apply_operational_definitions(detector, symbol)
