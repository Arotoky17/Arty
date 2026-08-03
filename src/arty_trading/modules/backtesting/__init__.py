"""
Module Backtesting — Moteur de backtesting et statistiques.

Le BacktestEngine simule l'exécution de stratégies sur des données historiques
et calcule les statistiques de performance (Profit Factor, Win Rate, Drawdown,
Sharpe Ratio, Expectancy).
"""

from arty_trading.modules.backtesting.engine import BacktestEngine
from arty_trading.modules.backtesting.stats import BacktestStats, calculate_stats

__all__ = ["BacktestEngine", "BacktestStats", "calculate_stats"]