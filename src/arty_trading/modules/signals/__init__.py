"""
Module Signaux — Générateur et validateur de signaux de trading.

Le ``SignalGenerator`` orchestre les stratégies activées, fusionne les données
SMC, et retourne le meilleur signal basé sur le score de confiance.

Le ``SignalValidator`` vérifie que toutes les conditions SMC/ICT sont réunies
avant d'autoriser un trade. Le ``SignalGenerator`` ne décide plus seul : il
délègue la validation finale au ``SignalValidator``.
"""

from arty_trading.modules.signals.generator import SignalGenerator
from arty_trading.modules.signals.news import EconomicCalendar, EconomicEvent
from arty_trading.modules.signals.validator import (
    ALL_CONDITIONS,
    COND_BOS,
    COND_CHOCH,
    COND_FVG,
    COND_HTF_TREND,
    COND_LIQUIDITY_SWEEP,
    COND_NEWS,
    COND_ORDER_BLOCK,
    COND_PREMIUM_DISCOUNT,
    COND_RR,
    COND_SESSION,
    COND_SPREAD,
    SignalValidator,
    ValidationResult,
)

__all__ = [
    "SignalGenerator",
    "EconomicCalendar",
    "EconomicEvent",
    "SignalValidator",
    "ValidationResult",
    "ALL_CONDITIONS",
    "COND_HTF_TREND",
    "COND_BOS",
    "COND_CHOCH",
    "COND_ORDER_BLOCK",
    "COND_FVG",
    "COND_LIQUIDITY_SWEEP",
    "COND_PREMIUM_DISCOUNT",
    "COND_SESSION",
    "COND_SPREAD",
    "COND_NEWS",
    "COND_RR",
]
