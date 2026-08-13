"""
Énumérations du domaine de trading.
Centralise toutes les constantes typées utilisées dans la plateforme.
"""

from enum import Enum, IntEnum


class TradingMode(str, Enum):
    """
    Mode de trading de la plateforme.

    Quatre modes sont disponibles :

    - **ANALYSIS** : Aucune position, uniquement les analyses.
      Le moteur exécute le pipeline jusqu'à la génération du signal
      mais n'ouvre aucun trade.

    - **PAPER** : Simulation complète, aucun ordre MT5.
      Le moteur exécute le pipeline complet (risque, exécution, monitoring)
      mais les ordres sont simulés (PaperOrderExecutor).

    - **DEMO** : Connexion réelle à un compte **DÉMO** MT5 + exécution réelle
      des ordres sur ce compte démo. Contrairement à ``LIVE``, ce mode ne
      nécessite **pas** ``ALLOW_LIVE_TRADING=true`` (aucun argent réel engagé).
      Garde-fou : si le compte connecté s'avère **réel** (trade_mode=1),
      le trading est bloqué pour ne jamais risquer de fonds réels.

    - **LIVE** : Trading réel.
      Le moteur exécute le pipeline complet avec des ordres MT5 réels.
      Nécessite ``ALLOW_LIVE_TRADING=true``.
    """

    ANALYSIS = "analysis"
    PAPER = "paper"
    DEMO = "demo"
    LIVE = "live"


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

    # Structure de marché
    BOS = "break_of_structure"
    INTERNAL_BOS = "internal_bos"
    EXTERNAL_BOS = "external_bos"
    CHOCH = "change_of_character"
    MSS = "market_structure_shift"

    # Fair Value Gap
    FVG = "fair_value_gap"
    IFVG = "inverse_fvg"

    # Order Blocks
    ORDER_BLOCK = "order_block"
    BREAKER_BLOCK = "breaker_block"
    MITIGATION_BLOCK = "mitigation_block"

    # Liquidité
    LIQUIDITY_SWEEP = "liquidity_sweep"
    EQUAL_HIGH = "equal_high"
    EQUAL_LOW = "equal_low"

    # Premium / Discount
    PREMIUM = "premium"
    DISCOUNT = "discount"
    PREMIUM_DISCOUNT = "premium_discount"
    OTE = "optimal_trade_entry"

    # Sessions
    SESSION = "session"
    KILL_ZONE = "kill_zone"

    # Divers
    POI = "point_of_interest"


class StrategyType(str, Enum):
    """Types de stratégies disponibles."""

    SMC_TREND = "smc_trend_following"
    BREAKOUT = "breakout"
    MOMENTUM = "momentum"
    REVERSAL = "reversal"
    SCALPING = "scalping"
    SWING = "swing_trading"


class MarketRegime(str, Enum):
    """Régime de marché déterminé par le MarketStructureEngine.

    Permet de dépasser le simple BULLISH/BEARISH/NEUTRAL pour refléter la
    force de la tendance et les états de transition / range.

    - STRONG_BULLISH / STRONG_BEARISH : tendance forte, structure alignée.
    - BULLISH / BEARISH : tendance directionnelle simple.
    - WEAK_BULLISH / WEAK_BEARISH : tendance faible, peu de confirmations.
    - RANGE : absence de tendance claire (oscillation, compression).
    - TRANSITION : bascule de structure en cours, non confirmée.
    """

    STRONG_BULLISH = "strong_bullish"
    BULLISH = "bullish"
    WEAK_BULLISH = "weak_bullish"
    RANGE = "range"
    WEAK_BEARISH = "weak_bearish"
    BEARISH = "bearish"
    STRONG_BEARISH = "strong_bearish"
    TRANSITION = "transition"


class NoTradeReason(str, Enum):
    """Raisons explicites d'une décision NO TRADE.

    Chaque raison est un motif structuré, sérialisable, qui explique pourquoi
    le bot n'entre pas. C'est une décision de premier ordre, pas un défaut.
    """

    H1_NEUTRAL = "h1_neutral"
    H1_RANGE = "h1_range"
    H1_TRANSITION = "h1_transition"
    COUNTER_TREND = "counter_trend"
    NO_STRUCTURE = "no_structure"
    NO_VALID_ZONE = "no_valid_zone"
    NO_LIQUIDITY_SWEEP = "no_liquidity_sweep"
    NO_M5_CONFIRMATION = "no_m5_confirmation"
    INVALID_OB = "invalid_ob"
    INVALID_FVG = "invalid_fvg"
    RR_TOO_LOW = "rr_too_low"
    SPREAD_TOO_HIGH = "spread_too_high"
    NEWS_FILTER = "news_filter"
    VOLATILITY_INVALID = "volatility_invalid"
    DUPLICATE_SETUP = "duplicate_setup"
    SETUP_EXPIRED = "setup_expired"
    UNSUPPORTED_SYMBOL = "unsupported_symbol"
    NO_DISPLACEMENT = "no_displacement"
    NO_REJECTION = "no_rejection"
    RISK_INVALID = "risk_invalid"


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
