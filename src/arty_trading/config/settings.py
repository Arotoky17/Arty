"""
Configuration centralisée de la plateforme Arty Trading.

Utilise pydantic-settings pour charger les paramètres depuis :
1. Variables d'environnement
2. Fichier .env
3. Valeurs par défaut sécurisées

Le mode REAL est DÉSACTIVÉ par défaut pour garantir la sécurité.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from arty_trading.core.enums import TimeFrame, TradingMode


class MT5Settings(BaseSettings):
    """Paramètres de connexion MetaTrader 5."""

    model_config = SettingsConfigDict(
        env_prefix="MT5_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    login: int = 0
    password: str = ""
    server: str = ""
    path: str = ""  # Chemin vers terminal64.exe si nécessaire
    timeout: int = 60000  # ms


class RiskSettings(BaseSettings):
    """Paramètres de gestion du risque."""

    model_config = SettingsConfigDict(env_prefix="")

    risk_per_trade: float = Field(default=0.01, alias="RISK_PER_TRADE")
    max_daily_risk: float = Field(default=0.03, alias="MAX_DAILY_RISK")
    max_drawdown: float = Field(default=0.10, alias="MAX_DRAWDOWN")
    max_open_positions: int = Field(default=3, alias="MAX_OPEN_POSITIONS")
    max_consecutive_losses: int = Field(default=3, alias="MAX_CONSECUTIVE_LOSSES")
    one_trade_per_symbol: bool = Field(default=True, alias="ONE_TRADE_PER_SYMBOL")
    max_spread: int = Field(
        default=30,
        alias="MAX_SPREAD_POINTS",
        description="Spread maximum autorisé en points ; au-delà, le trade est bloqué",
    )
    max_spread_by_symbol: dict[str, int] = Field(
        default_factory=lambda: {
            "EURUSD": 30,
            "GBPUSD": 40,
            "USDJPY": 30,
            "XAUUSD": 200,
        },
        alias="MAX_SPREAD_BY_SYMBOL",
        description="Spread maximum par symbole en points",
    )

    @field_validator("risk_per_trade", "max_daily_risk", "max_drawdown")
    @classmethod
    def validate_risk_range(cls, v: float) -> float:
        if not 0 < v <= 1:
            raise ValueError("Les valeurs de risque doivent être entre 0 et 1")
        return v


@dataclass(frozen=True)
class InstrumentProfile:
    """Profil de trading par instrument (symbole).

    Toutes les valeurs distance/ATR sont exprimées en multiples d'ATR,
    jamais en distance fixe. Cela permet d'adapter automatiquement le
    filtre à la volatilité courante du symbole.
    """

    symbol: str
    atr_period: int = 14
    displacement_atr_mult: float = 1.5
    retest_atr_mult: float = 1.0
    sl_buffer_atr_mult: float = 0.5
    min_risk_reward: float = 2.0
    max_spread_points: int = 30
    max_zone_age_bars: int = 20
    max_mitigations: int = 2
    # Nombre minimum de confluences SMC à valider (parmi FVG, Order Block,
    # Liquidity Sweep, Premium/Discount) pour autoriser un signal.
    min_confluence_count: int = 2


class SignalSettings(BaseSettings):
    """Paramètres du générateur de signaux.

    Pendant le développement, seule la stratégie ``active_strategy`` est
    utilisée. Les autres stratégies sont désactivées proprement (non
    supprimées) et le ``SignalGenerator`` ne retourne jamais un signal
    provenant d'une autre stratégie.
    """

    model_config = SettingsConfigDict(env_prefix="")

    min_confidence: float = Field(
        default=0.85,
        alias="SIGNAL_MIN_CONFIDENCE",
        description="Confiance minimale pour accepter un signal (0-1)",
    )
    active_strategy: str = Field(
        default="SMC Trend Following",
        alias="SIGNAL_ACTIVE_STRATEGY",
        description="Nom de la stratégie active (seule autorisée à générer des signaux)",
    )

    @field_validator("min_confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if not 0 < v <= 1:
            raise ValueError("min_confidence doit être entre 0 et 1 (exclusif)")
        return v


class ValidatorSettings(BaseSettings):
    """Paramètres du validateur de signaux.

    Le ``SignalValidator`` vérifie que toutes les conditions SMC/ICT sont
    réunies avant d'autoriser un trade. Ces paramètres contrôlent les seuils
    de validation.
    """

    model_config = SettingsConfigDict(env_prefix="")

    min_risk_reward: float = Field(
        default=1.5,
        alias="VALIDATOR_MIN_RR",
        description="Ratio risque/rendement minimum pour valider un signal",
    )
    max_spread: int = Field(
        default=20,
        alias="VALIDATOR_MAX_SPREAD",
        description="Spread maximum autorisé en points",
    )
    require_htf_alignment: bool = Field(
        default=True,
        alias="VALIDATOR_HTF_ALIGNMENT",
        description="Vérifier l'alignement de la tendance HTF",
    )
    require_news_filter: bool = Field(
        default=True,
        alias="VALIDATOR_NEWS_FILTER",
        description="Vérifier le filtre de news (bloque les trades pendant les news)",
    )
    min_confluence_count: int = Field(
        default=2,
        alias="VALIDATOR_MIN_CONFLUENCE",
        ge=0,
        le=4,
        description=(
            "Nombre minimum de confluences SMC (FVG, OB, Liquidity Sweep, "
            "Premium/Discount) à valider pour autoriser un signal. Les "
            "confluences sont comptées (elles ne bloquent pas), contrairement "
            "aux conditions HARD de sécurité."
        ),
    )


class DecisionSettings(BaseSettings):
    """Options du moteur de décision ICT/SMC professionnel.

    Les filtres sont volontairement activés par défaut, mais chacun peut être
    désactivé via une variable d'environnement pour les backtests.
    """

    model_config = SettingsConfigDict(env_prefix="")

    enabled: bool = Field(default=False, alias="DECISION_ENGINE_ENABLED")
    minimum_score: int = Field(default=70, alias="MINIMUM_SCORE", ge=0, le=100)
    minimum_risk_reward: float = Field(default=2.0, alias="RISK_REWARD", ge=1.0)
    enable_mtf: bool = Field(default=True, alias="ENABLE_MTF")
    enable_news_filter: bool = Field(default=True, alias="ENABLE_NEWS_FILTER")
    enable_kill_zone: bool = Field(default=False, alias="ENABLE_KILL_ZONE")
    enable_spread_filter: bool = Field(default=True, alias="ENABLE_SPREAD_FILTER")
    enable_premium_discount: bool = Field(default=True, alias="ENABLE_PREMIUM_DISCOUNT")
    enable_atr_filter: bool = Field(default=True, alias="ENABLE_ATR_FILTER")
    maximum_spread: int = Field(default=20, alias="MAXIMUM_SPREAD", ge=0)
    atr_period: int = Field(default=14, alias="ATR_PERIOD", ge=2)
    min_atr: float = Field(default=0.0, alias="MIN_ATR", ge=0.0)
    max_atr: float = Field(default=999999.0, alias="MAX_ATR", gt=0.0)
    atr_multiplier: float = Field(default=1.0, alias="ATR_MULTIPLIER", gt=0.0)
    htf_timeframe: str = Field(default="H1", alias="HTF_TIMEFRAME")
    entry_timeframe: str = Field(default="M5", alias="ENTRY_TIMEFRAME")
    master_trend_enabled: bool = Field(default=True, alias="MASTER_TREND_ENABLED")
    allow_counter_trend: bool = Field(default=False, alias="ALLOW_COUNTER_TREND")
    use_bos: bool = Field(default=True, alias="USE_BOS")
    use_choch: bool = Field(default=True, alias="USE_CHOCH")
    use_mss: bool = Field(default=True, alias="USE_MSS")
    use_ob: bool = Field(default=True, alias="USE_OB")
    use_fvg: bool = Field(default=True, alias="USE_FVG")
    use_ifvg: bool = Field(default=True, alias="USE_IFVG")
    use_liquidity: bool = Field(default=True, alias="USE_LIQUIDITY")
    use_premium_discount: bool = Field(default=True, alias="USE_PREMIUM_DISCOUNT")


class PositionSettings(BaseSettings):
    """Règles de suivi actif des positions, exprimées en multiples de R."""

    model_config = SettingsConfigDict(env_prefix="", populate_by_name=True)

    enabled: bool = Field(default=True, alias="POSITION_MANAGER_ENABLED")
    enable_break_even: bool = Field(default=True, alias="ENABLE_BREAK_EVEN")
    enable_partial_tp: bool = Field(default=True, alias="ENABLE_PARTIAL_TP")
    enable_trailing_stop: bool = Field(default=True, alias="ENABLE_TRAILING_STOP")
    break_even_at_r: float = Field(default=1.0, alias="BREAK_EVEN_AT_R", ge=0.1)
    partial_tp_at_r: float = Field(default=1.5, alias="PARTIAL_TP_AT_R", ge=0.1)
    partial_close_percent: float = Field(default=0.4, alias="PARTIAL_CLOSE_PERCENT", gt=0, le=1)
    trailing_at_r: float = Field(default=1.5, alias="TRAILING_AT_R", ge=0.1)
    trailing_distance_r: float = Field(default=1.0, alias="TRAILING_DISTANCE_R", ge=0.1)


class NewsSettings(BaseSettings):
    """Filtre de calendrier économique à impact élevé."""

    model_config = SettingsConfigDict(env_prefix="", populate_by_name=True)

    enabled: bool = Field(default=False, alias="ENABLE_NEWS_FILTER")
    calendar_file: str = Field(default="data/economic_calendar.json", alias="NEWS_CALENDAR_FILE")
    block_minutes_before: int = Field(default=30, alias="NEWS_BLOCK_MINUTES_BEFORE", ge=0)
    block_minutes_after: int = Field(default=30, alias="NEWS_BLOCK_MINUTES_AFTER", ge=0)


class JournalSettings(BaseSettings):
    """Persistance du journal de trading."""

    model_config = SettingsConfigDict(env_prefix="", populate_by_name=True)

    enabled: bool = Field(default=False, alias="ENABLE_TRADE_JOURNAL")
    directory: str = Field(default="data/journal", alias="TRADE_JOURNAL_DIRECTORY")


class SessionSettings(BaseSettings):
    """Horaires des sessions de trading (UTC)."""

    model_config = SettingsConfigDict(env_prefix="SESSION_")

    asia_start: str = "00:00"
    asia_end: str = "09:00"
    london_start: str = "07:00"
    london_end: str = "16:00"
    newyork_start: str = "12:00"
    newyork_end: str = "21:00"


class DatabaseSettings(BaseSettings):
    """Paramètres PostgreSQL."""

    model_config = SettingsConfigDict(env_prefix="DATABASE_")

    url: str = Field(
        default="postgresql+asyncpg://arty:arty_secret@localhost:5432/arty_trading",
        alias="DATABASE_URL",
    )
    url_sync: str = Field(
        default="postgresql://arty:arty_secret@localhost:5432/arty_trading",
        alias="DATABASE_URL_SYNC",
    )


class APISettings(BaseSettings):
    """Paramètres de l'API FastAPI."""

    model_config = SettingsConfigDict(env_prefix="API_")

    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = True


class NotificationSettings(BaseSettings):
    """Paramètres des notifications."""

    model_config = SettingsConfigDict(env_prefix="")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    discord_webhook_url: str = Field(default="", alias="DISCORD_WEBHOOK_URL")
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    notification_email: str = Field(default="", alias="NOTIFICATION_EMAIL")


class AISettings(BaseSettings):
    """Paramètres de l'assistant IA."""

    model_config = SettingsConfigDict(env_prefix="AI_")

    api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    model: str = "gpt-4o-mini"
    max_tokens: int = 2048


class Settings(BaseSettings):
    """
    Configuration principale de la plateforme.
    Point d'entrée unique pour tous les paramètres.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = Field(default="Arty", alias="APP_NAME")
    app_env: Literal["development", "staging", "production"] = Field(
        default="development", alias="APP_ENV"
    )
    debug: bool = Field(default=True, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Trading - SÉCURITÉ : mode ANALYSIS par défaut (aucune position)
    trading_mode: TradingMode = Field(default=TradingMode.ANALYSIS, alias="TRADING_MODE")
    allow_live_trading: bool = Field(default=False, alias="ALLOW_LIVE_TRADING")

    # Mode calibration : assouplit temporairement les filtres pour identifier
    # les setups valides. Ne retire PAS les hard rejects (H1 RANGE/TRANSITION,
    # spread trop élevé, news, contre-trend, setup dupliqué).
    debug_calibration_mode: bool = Field(default=False, alias="DEBUG_CALIBRATION_MODE")

    # Diagnostic de décision de trading — observabilité du pipeline.
    decision_diagnostics_enabled: bool = Field(
        default=False, alias="DECISION_DIAGNOSTICS_ENABLED",
        description="Active le diagnostic détaillé des décisions de trading",
    )
    decision_diagnostics_interval: int = Field(
        default=100, alias="DECISION_DIAGNOSTICS_INTERVAL", ge=1,
        description="Nombre d'opportunités entre chaque résumé périodique",
    )

    # Symboles et timeframe
    default_symbols: str = Field(default="EURUSD,XAUUSD", alias="DEFAULT_SYMBOLS")
    enable_legacy_symbols: bool = Field(default=False, alias="ENABLE_LEGACY_SYMBOLS")
    default_timeframe: TimeFrame = Field(default=TimeFrame.H1, alias="DEFAULT_TIMEFRAME")

    # Profils par instrument (EURUSD + XAUUSD uniquement en phase 1).
    instrument_profiles: dict[str, InstrumentProfile] = Field(
        default_factory=lambda: {
            "EURUSD": InstrumentProfile(
                symbol="EURUSD",
                atr_period=14,
                displacement_atr_mult=1.5,
                retest_atr_mult=1.0,
                sl_buffer_atr_mult=0.5,
                min_risk_reward=1.5,
                max_spread_points=30,
                max_zone_age_bars=20,
                max_mitigations=2,
                min_confluence_count=2,
            ),
            "XAUUSD": InstrumentProfile(
                symbol="XAUUSD",
                atr_period=14,
                displacement_atr_mult=1.2,
                retest_atr_mult=1.5,
                sl_buffer_atr_mult=0.8,
                min_risk_reward=2.0,
                max_spread_points=200,
                max_zone_age_bars=25,
                max_mitigations=2,
                min_confluence_count=2,
            ),
        },
        description="Profil de trading par symbole (seuls EURUSD et XAUUSD en phase 1)",
    )

    # Sous-configurations
    mt5: MT5Settings = Field(default_factory=MT5Settings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    signals: SignalSettings = Field(default_factory=SignalSettings)
    validator: ValidatorSettings = Field(default_factory=ValidatorSettings)
    decision: DecisionSettings = Field(default_factory=DecisionSettings)
    position: PositionSettings = Field(default_factory=PositionSettings)
    news: NewsSettings = Field(default_factory=NewsSettings)
    journal: JournalSettings = Field(default_factory=JournalSettings)
    sessions: SessionSettings = Field(default_factory=SessionSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    api: APISettings = Field(default_factory=APISettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    ai: AISettings = Field(default_factory=AISettings)

    @field_validator("default_timeframe", mode="before")
    @classmethod
    def parse_timeframe(cls, v: str | TimeFrame) -> TimeFrame:
        if isinstance(v, TimeFrame):
            return v
        return TimeFrame(v.upper())

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, value: bool | str) -> bool | str:
        """Accepte les valeurs usuelles injectées par des environnements externes.

        Certains outils définissent ``DEBUG=release`` pour leur propre usage.
        Cette valeur ne doit pas empêcher le démarrage d'Arty ; elle correspond
        ici à un niveau non-debug et devient donc ``False``.
        """
        if isinstance(value, str) and value.strip().lower() in {"release", "production"}:
            return False
        return value

    @field_validator("trading_mode", mode="before")
    @classmethod
    def parse_trading_mode(cls, v: str | TradingMode) -> TradingMode:
        if isinstance(v, TradingMode):
            return v
        return TradingMode(v.lower())

    @model_validator(mode="after")
    def enforce_safety(self) -> Settings:
        """
        Garde-fou de sécurité : empêche le trading réel (LIVE) si non
        explicitement autorisé via ``ALLOW_LIVE_TRADING=true``.

        Si le mode LIVE est demandé sans autorisation, le moteur bascule
        en mode PAPER (simulation complète sans ordres MT5 réels).

        Le mode **DEMO** n'est pas concerné : il connecte un compte démo
        MT5 et exécute de vrais ordres sur ce compte démo (aucun argent
        réel). La vérification du type de compte (démo vs réel) est faite
        au moment de la connexion par le ``MT5Connector``.
        """
        if self.trading_mode == TradingMode.LIVE and not self.allow_live_trading:
            self.trading_mode = TradingMode.PAPER
        return self

    @property
    def symbols_list(self) -> list[str]:
        """Retourne la liste des symboles configurés."""
        return [s.strip().upper() for s in self.default_symbols.split(",") if s.strip()]

    @property
    def supported_symbols(self) -> list[str]:
        """Retourne la liste des symboles supportés par la stratégie ICT/SMC."""
        base = ["EURUSD", "XAUUSD"]
        if self.enable_legacy_symbols:
            base.extend(["GBPUSD", "USDJPY"])
        return base

    def get_instrument_profile(self, symbol: str) -> InstrumentProfile | None:
        """Retourne le profil d'un instrument, ou None si non configuré."""
        return self.instrument_profiles.get(symbol.upper())

    @property
    def is_analysis_mode(self) -> bool:
        """Indique si le moteur est en mode analyse (aucune position)."""
        return self.trading_mode == TradingMode.ANALYSIS

    @property
    def is_paper_mode(self) -> bool:
        """Indique si le moteur est en mode paper (simulation complète)."""
        return self.trading_mode == TradingMode.PAPER

    @property
    def is_demo_mode(self) -> bool:
        """Indique si le moteur est en mode démo (compte démo MT5 réel)."""
        return self.trading_mode == TradingMode.DEMO

    @property
    def is_live_mode(self) -> bool:
        """Indique si le moteur est en mode live (trading réel)."""
        return self.trading_mode == TradingMode.LIVE

    @property
    def is_live_trading_enabled(self) -> bool:
        """Indique si le trading réel (argent réel) est autorisé ET activé."""
        return self.allow_live_trading and self.trading_mode == TradingMode.LIVE

    @property
    def is_trading_active(self) -> bool:
        """
        Indique si le moteur exécute de **vrais** ordres MT5 (démo ou live).

        True en mode ``DEMO`` ou ``LIVE`` (avec autorisation), False dans
        les modes ``ANALYSIS`` et ``PAPER``.
        """
        return self.is_demo_mode or self.is_live_trading_enabled

    @property
    def logs_dir(self) -> str:
        return "logs"

    @property
    def data_dir(self) -> str:
        return "data"


@lru_cache
def get_settings() -> Settings:
    """
    Retourne l'instance singleton des settings.
    Utilise lru_cache pour éviter de recharger le .env à chaque appel.
    """
    return Settings()
