"""
Configuration centralisée de la plateforme Arty Trading.

Utilise pydantic-settings pour charger les paramètres depuis :
1. Variables d'environnement
2. Fichier .env
3. Valeurs par défaut sécurisées

Le mode REAL est DÉSACTIVÉ par défaut pour garantir la sécurité.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from arty_trading.core.enums import TimeFrame, TradingMode


class MT5Settings(BaseSettings):
    """Paramètres de connexion MetaTrader 5."""

    model_config = SettingsConfigDict(env_prefix="MT5_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

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

    @field_validator("risk_per_trade", "max_daily_risk", "max_drawdown")
    @classmethod
    def validate_risk_range(cls, v: float) -> float:
        if not 0 < v <= 1:
            raise ValueError("Les valeurs de risque doivent être entre 0 et 1")
        return v


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

    # Trading - SÉCURITÉ : mode demo par défaut
    trading_mode: TradingMode = Field(default=TradingMode.DEMO, alias="TRADING_MODE")
    allow_live_trading: bool = Field(default=False, alias="ALLOW_LIVE_TRADING")

    # Symboles et timeframe
    default_symbols: str = Field(
        default="EURUSD,GBPUSD,USDJPY,XAUUSD", alias="DEFAULT_SYMBOLS"
    )
    default_timeframe: TimeFrame = Field(default=TimeFrame.H1, alias="DEFAULT_TIMEFRAME")

    # Sous-configurations
    mt5: MT5Settings = Field(default_factory=MT5Settings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
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

    @field_validator("trading_mode", mode="before")
    @classmethod
    def parse_trading_mode(cls, v: str | TradingMode) -> TradingMode:
        if isinstance(v, TradingMode):
            return v
        return TradingMode(v.lower())

    @model_validator(mode="after")
    def enforce_demo_safety(self) -> Settings:
        """
        Garde-fou de sécurité : empêche le trading réel si non explicitement autorisé.
        """
        if self.trading_mode == TradingMode.REAL and not self.allow_live_trading:
            self.trading_mode = TradingMode.DEMO
        return self

    @property
    def symbols_list(self) -> list[str]:
        """Retourne la liste des symboles configurés."""
        return [s.strip().upper() for s in self.default_symbols.split(",") if s.strip()]

    @property
    def is_live_trading_enabled(self) -> bool:
        """Indique si le trading réel est autorisé ET activé."""
        return self.allow_live_trading and self.trading_mode == TradingMode.REAL

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
