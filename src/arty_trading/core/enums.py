"""
Énumérations du domaine de trading.
Centralise toutes les constantes typées utilisées dans la plateforme.
"""

from enum import Enum, IntEnum


class TradingMode(str, Enum):
    """Mode de trading - REAL désactivé par défaut pour la sécurité."""

    DEMO = "demo"
    REAL = "real"


class Direction(str, Enum):
    """Direction d'un trade ou d'un signal."""

    BUY = "buy"
    SELL = "sell"
    NEUTRAL = "neutral"


class SignalType(str, Enum):
    """Type de signal généré par le moteur."""

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE = "close"


class OrderType(str, Enum):
    """Types d'ordres MetaTrader 5."""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeFrame(str, Enum):
    """
    Timeframes supportés (alignés sur MT5).
    La valeur correspond au nom MT5 pour faciliter le mapping.
    """

    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"
    MN1 = "MN1"

    @property
    def minutes(self) -> int:
        """Retourne la durée en minutes du timeframe."""
        mapping = {
            "M1": 1,
            "M5": 5,
            "M15": 15,
            "M30": 30,
            "H1": 60,
            "H4": 240,
            "D1": 1440,
            "W1": 10080,
            "MN1": 43200,
        }
        return mapping[self.value]


class TradingSession(str, Enum):
    """Sessions de trading ICT/SMC."""

    ASIA = "asia"
    LONDON = "london"
    NEW_YORK = "new_york"
    OVERLAP_LONDON_NY = "overlap_london_ny"


class SMCConcept(str, Enum):
    """Concepts Smart Money Concepts détectables."""

    BOS = "break_of_structure"
    CHOCH = "change_of_character"
    MSS = "market_structure_shift"
    FVG = "fair_value_gap"
    IFVG = "inverse_fvg"
    ORDER_BLOCK = "order_block"
    BREAKER_BLOCK = "breaker_block"
    MITIGATION_BLOCK = "mitigation_block"
    LIQUIDITY_SWEEP = "liquidity_sweep"
    EQUAL_HIGH = "equal_high"
    EQUAL_LOW = "equal_low"
    PREMIUM_DISCOUNT = "premium_discount"
    OTE = "optimal_trade_entry"
    POI = "point_of_interest"
    KILL_ZONE = "kill_zone"


class StrategyType(str, Enum):
    """Types de stratégies disponibles."""

    SMC_TREND = "smc_trend_following"
    BREAKOUT = "breakout"
    MOMENTUM = "momentum"
    REVERSAL = "reversal"
    SCALPING = "scalping"
    SWING = "swing_trading"


class LogCategory(str, Enum):
    """Catégories de journalisation."""

    SYSTEM = "system"
    MT5 = "mt5"
    MARKET_DATA = "market_data"
    SMC = "smc"
    STRATEGY = "strategy"
    SIGNAL = "signal"
    RISK = "risk"
    EXECUTION = "execution"
    POSITION = "position"
    BACKTEST = "backtest"
    AI = "ai"
    NOTIFICATION = "notification"
    ERROR = "error"
