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

GOLD_SYMBOL = "XAUUSD"


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
        default=200,
        alias="MAX_SPREAD_POINTS",
        description="Spread maximum autorisé en points ; au-delà, le trade est bloqué",
    )
    max_spread_by_symbol: dict[str, int] = Field(
        default_factory=lambda: {
            GOLD_SYMBOL: 200,
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
    # --- Phase 3 : qualité de détection (0 = désactivé / comportement historique) ---
    # Taille minimale d'un FVG en multiple d'ATR (filtre les micro-gaps).
    min_fvg_atr: float = 0.0
    # Ratio de rejet minimum d'un liquidity sweep (clôture au-delà du niveau /
    # profondeur du dépassement). Distingue un vrai sweep d'un simple wick.
    sweep_min_rejection_ratio: float = 0.0
    # Facteur ATR du corps du displacement confirmant un sweep. 0 = pas
    # d'exigence de displacement.
    sweep_displacement_atr_mult: float = 0.0
    # Hauteur maximale d'un Order Block en multiple d'ATR (filtre les clumps).
    max_ob_atr_mult: float = 0.0
    # Nombre de bougies directionnelles consécutives confirmant le
    # displacement après un Order Block.
    displacement_confirmation_bars: int = 1

@dataclass(frozen=True)
class GoldMarketConfig:
    """Configuration centralisee du marche actif XAUUSD."""

    symbol: str = GOLD_SYMBOL
    context_timeframe: TimeFrame = TimeFrame.H4
    htf_timeframe: TimeFrame = TimeFrame.H1
    entry_timeframe: TimeFrame = TimeFrame.M5
    atr_period: int = 14
    displacement_atr_mult: float = 1.2
    retest_atr_mult: float = 1.5
    sl_buffer_atr_mult: float = 0.8
    min_risk_reward: float = 2.0
    max_spread_points: int = 200
    max_zone_age_bars: int = 25
    max_mitigations: int = 2
    min_confluence_count: int = 2
    min_fvg_atr: float = 0.25
    sweep_min_rejection_ratio: float = 0.5
    sweep_displacement_atr_mult: float = 1.0
    max_ob_atr_mult: float = 3.0
    displacement_confirmation_bars: int = 2
    min_sl_atr_mult: float = 0.4
    max_sl_atr_mult: float = 4.0
    momentum_body_atr_mult: float = 1.0
    rejection_wick_body_ratio: float = 1.5

    def instrument_profile(self) -> InstrumentProfile:
        """Retourne le profil instrument consomme par le pipeline existant."""
        return InstrumentProfile(
            symbol=self.symbol,
            atr_period=self.atr_period,
            displacement_atr_mult=self.displacement_atr_mult,
            retest_atr_mult=self.retest_atr_mult,
            sl_buffer_atr_mult=self.sl_buffer_atr_mult,
            min_risk_reward=self.min_risk_reward,
            max_spread_points=self.max_spread_points,
            max_zone_age_bars=self.max_zone_age_bars,
            max_mitigations=self.max_mitigations,
            min_confluence_count=self.min_confluence_count,
            min_fvg_atr=self.min_fvg_atr,
            sweep_min_rejection_ratio=self.sweep_min_rejection_ratio,
            sweep_displacement_atr_mult=self.sweep_displacement_atr_mult,
            max_ob_atr_mult=self.max_ob_atr_mult,
            displacement_confirmation_bars=self.displacement_confirmation_bars,
        )


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
    min_score: int = Field(
        default=70,
        alias="VALIDATOR_MIN_SCORE",
        ge=0,
        le=100,
        description="Score minimum (0-100) pour qu'un signal soit accepté après weighted scoring.",
    )
    excellent_score: int = Field(
        default=80,
        alias="VALIDATOR_EXCELLENT_SCORE",
        ge=0,
        le=100,
        description="Score minimum pour un setup EXCELLENT.",
    )
    watch_score: int = Field(
        default=60,
        alias="VALIDATOR_WATCH_SCORE",
        ge=0,
        le=100,
        description="Score minimum pour un setup WATCH (no trade).",
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
    context_timeframe: str = Field(default="H4", alias="CONTEXT_TIMEFRAME")
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
    """Règles de suivi actif des positions, exprimées en multiples de R.

    Nouveau système (activé par défaut) :
    - **Profit Lock** : sécurisation progressive par paliers ``trigger_r:lock_r`` ;
    - **Partial Profit** : prises partielles multi-niveaux ``trigger_r:fraction`` ;
    - **Runner** : la position peut courir au-delà du seuil de sécurisation ;
    - **Structure Trailing** : SL sous le HL / au-dessus du LH confirmé (buffer ATR).

    Le Break-Even historique est remplacé par le Profit Lock (le palier
    ``1.0:0.0`` reproduit exactement l'ancien comportement). Les anciens
    paramètres restent fonctionnels en mode legacy (``PROFIT_LOCK_ENABLED=false``).
    """

    model_config = SettingsConfigDict(env_prefix="", populate_by_name=True)

    enabled: bool = Field(default=True, alias="POSITION_MANAGER_ENABLED")

    # --- Système actif : Profit Lock / Partial / Runner / Structure -----------
    enable_profit_lock: bool = Field(default=True, alias="PROFIT_LOCK_ENABLED")
    profit_lock_levels: str = Field(
        default="0.5:0.1,1.0:0.4,1.5:0.8,2.0:1.2,3.0:2.0",
        alias="PROFIT_LOCK_LEVELS",
        description="Paliers trigger_r:lock_r séparés par des virgules",
    )
    enable_partial_profit: bool = Field(default=True, alias="PARTIAL_PROFIT_ENABLED")
    partial_profit_levels: str = Field(
        default="1.0:0.25",
        alias="PARTIAL_PROFIT_LEVELS",
        description="Niveaux trigger_r:fraction séparés par des virgules",
    )
    enable_runner: bool = Field(default=True, alias="RUNNER_ENABLED")
    runner_tp_enabled: bool = Field(
        default=False, alias="RUNNER_TP_ENABLED",
        description="TP optionnel du runner (sinon sortie SL/structurelle uniquement)",
    )
    runner_tp_r: float = Field(default=3.0, alias="RUNNER_TP_R", ge=0.1)
    runner_exit_on_structure_break: bool = Field(
        default=True, alias="RUNNER_EXIT_ON_STRUCTURE_BREAK"
    )
    enable_structure_trailing: bool = Field(default=True, alias="STRUCTURE_TRAILING_ENABLED")
    structure_trailing_buffer_atr: float = Field(
        default=0.2, alias="STRUCTURE_TRAILING_BUFFER_ATR", ge=0.0
    )
    min_sl_update_r: float = Field(
        default=0.05, alias="MIN_SL_UPDATE_DISTANCE_R", ge=0.0,
        description="Amélioration minimale du SL (en fraction de R) pour justifier un modify",
    )
    never_lower_sl: bool = Field(default=True, alias="NEVER_LOWER_SL")
    state_directory: str = Field(
        default="data/position_states", alias="POSITION_STATE_DIRECTORY"
    )

    # --- Legacy (rétro-compatibilité, actif si PROFIT_LOCK_ENABLED=false) -----
    enable_break_even: bool = Field(default=True, alias="ENABLE_BREAK_EVEN")
    enable_partial_tp: bool = Field(default=True, alias="ENABLE_PARTIAL_TP")
    enable_trailing_stop: bool = Field(default=True, alias="ENABLE_TRAILING_STOP")
    break_even_at_r: float = Field(default=1.0, alias="BREAK_EVEN_AT_R", ge=0.1)
    partial_tp_at_r: float = Field(default=1.5, alias="PARTIAL_TP_AT_R", ge=0.1)
    partial_close_percent: float = Field(default=0.4, alias="PARTIAL_CLOSE_PERCENT", gt=0, le=1)
    trailing_at_r: float = Field(default=1.5, alias="TRAILING_AT_R", ge=0.1)
    trailing_distance_r: float = Field(default=1.0, alias="TRAILING_DISTANCE_R", ge=0.1)

    @staticmethod
    def _parse_levels(raw: str) -> list[tuple[float, float]]:
        """Parse une chaîne ``"a:b,c:d"`` en liste de couples (a, b)."""
        levels: list[tuple[float, float]] = []
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            left, _, right = chunk.partition(":")
            levels.append((float(left), float(right)))
        return levels

    @property
    def profit_lock_level_list(self) -> list[tuple[float, float]]:
        """Paliers de profit lock parsés et triés par déclencheur croissant."""
        return sorted(self._parse_levels(self.profit_lock_levels))

    @property
    def partial_profit_level_list(self) -> list[tuple[float, float]]:
        """Niveaux de partial profit parsés et triés par déclencheur croissant."""
        return sorted(self._parse_levels(self.partial_profit_levels))


# Grades de qualité d'un Order Block (Phase 12) — du meilleur au moins bon.
OB_QUALITY_GRADES: tuple[str, ...] = ("A", "B", "C", "D")


class OBQualitySettings(BaseSettings):
    """Notation de qualité des Order Blocks (Grade A/B/C/D) — Phase 12.

    Le bot ne trade plus « tous les Order Blocks » : quand ``enabled`` est vrai,
    chaque OB détecté sur le timeframe de setup est noté sur 100 puis classé en
    Grade A/B/C/D. Seuls les OB dont le grade est >= ``min_grade`` sont
    transformés en setups, et la confirmation M5 (``require_m5_confirmation``)
    est exigée avant qu'un signal puisse être produit.

    Le gate historique de création de setups reste désactivé par défaut via
    ``OB_QUALITY_ENABLED``. Le filtre de la stratégie active est contrôlé
    séparément par ``USE_OB_QUALITY_FILTER`` (activé par défaut).
    """

    model_config = SettingsConfigDict(env_prefix="", populate_by_name=True)

    enabled: bool = Field(
        default=False,
        alias="OB_QUALITY_ENABLED",
        description="Active la notation de qualité des Order Blocks (Grade A/B/C/D)",
    )
    use_ob_quality_filter: bool = Field(
        default=True,
        alias="USE_OB_QUALITY_FILTER",
        description="Active le filtre Grade A/B + confirmation M5 de la stratégie SMC",
    )
    min_score: float = Field(
        default=0.55,
        alias="OB_MIN_SCORE",
        ge=0.0,
        le=1.0,
        description="Score minimal (0-1) pour accepter un OB Grade A/B",
    )
    require_fresh: bool = Field(
        default=True,
        alias="OB_REQUIRE_FRESH",
        description="Exige que le scorer confirme qu'aucune bougie postérieure n'a mitigé l'OB",
    )
    require_htf_confluence: bool = Field(
        default=False,
        alias="OB_REQUIRE_HTF_CONFLUENCE",
        description="Exige une confluence de zone avec un OB ou FVG HTF",
    )
    require_liquidity_sweep: bool = Field(
        default=False,
        alias="OB_REQUIRE_LIQUIDITY_SWEEP",
        description="Exige un sweep de liquidité avant l'OB",
    )
    rr_min_grade_a: float = Field(
        default=2.5,
        alias="OB_RR_MIN_GRADE_A",
        gt=0.0,
        description="R/R minimal d'un signal basé sur un OB Grade A",
    )
    rr_min_grade_b: float = Field(
        default=2.0,
        alias="OB_RR_MIN_GRADE_B",
        gt=0.0,
        description="R/R minimal d'un signal basé sur un OB Grade B",
    )
    min_grade: str = Field(
        default="B",
        alias="OB_MIN_GRADE",
        description="Grade minimum accepté pour créer un setup OB (A, B, C ou D)",
    )
    require_m5_confirmation: bool = Field(
        default=True,
        alias="OB_REQUIRE_M5_CONFIRMATION",
        description="Exige une confirmation M5 (retest + rejet) avant tout signal OB",
    )

    # --- Seuils de grade (score 0-100) --------------------------------------
    grade_a_threshold: int = Field(default=85, alias="OB_GRADE_A_THRESHOLD", ge=0, le=100)
    grade_b_threshold: int = Field(default=70, alias="OB_GRADE_B_THRESHOLD", ge=0, le=100)
    grade_c_threshold: int = Field(default=50, alias="OB_GRADE_C_THRESHOLD", ge=0, le=100)

    # --- Pondérations (normalisées : la somme n'a pas besoin de valoir 100) ---
    weight_displacement: float = Field(default=25.0, alias="OB_WEIGHT_DISPLACEMENT", ge=0.0)
    weight_zone_height: float = Field(default=10.0, alias="OB_WEIGHT_ZONE_HEIGHT", ge=0.0)
    weight_mitigation: float = Field(default=10.0, alias="OB_WEIGHT_MITIGATION", ge=0.0)
    weight_freshness: float = Field(default=10.0, alias="OB_WEIGHT_FRESHNESS", ge=0.0)
    weight_trend: float = Field(default=15.0, alias="OB_WEIGHT_TREND", ge=0.0)
    weight_confluence: float = Field(default=20.0, alias="OB_WEIGHT_CONFLUENCE", ge=0.0)
    weight_premium_discount: float = Field(
        default=10.0, alias="OB_WEIGHT_PREMIUM_DISCOUNT", ge=0.0
    )

    # --- Paramètres de notation --------------------------------------------
    displacement_reference_atr: float = Field(
        default=1.2,
        alias="OB_DISPLACEMENT_REFERENCE_ATR",
        gt=0.0,
        description="Multiple d'ATR du déplacement donnant 50 % du score displacement",
    )
    min_zone_height_atr: float = Field(default=0.15, alias="OB_MIN_ZONE_HEIGHT_ATR", ge=0.0)
    max_zone_height_atr: float = Field(default=3.0, alias="OB_MAX_ZONE_HEIGHT_ATR", gt=0.0)
    max_zone_age_bars: int = Field(default=25, alias="OB_MAX_ZONE_AGE_BARS", ge=1)
    confluence_lookback_bars: int = Field(default=30, alias="OB_CONFLUENCE_LOOKBACK_BARS", ge=1)

    # --- Confirmation M5 ----------------------------------------------------
    m5_confirmation_lookback_bars: int = Field(
        default=30, alias="OB_M5_LOOKBACK_BARS", ge=1
    )
    m5_min_rejection_ratio: float = Field(
        default=0.5,
        alias="OB_M5_MIN_REJECTION_RATIO",
        ge=0.0,
        description="Clôture minimale dans la zone (0-1) pour valider le rejet M5",
    )
    m5_require_displacement: bool = Field(
        default=True,
        alias="OB_M5_REQUIRE_DISPLACEMENT",
        description="Exige un corps de bougie de rejet >= N x ATR sur M5",
    )
    m5_displacement_atr_mult: float = Field(
        default=1.0, alias="OB_M5_DISPLACEMENT_ATR_MULT", ge=0.0
    )

    @field_validator("min_grade")
    @classmethod
    def validate_min_grade(cls, value: str) -> str:
        grade = str(value).strip().upper()
        if grade not in OB_QUALITY_GRADES:
            raise ValueError(
                f"OB_MIN_GRADE doit être l'un de {OB_QUALITY_GRADES} (reçu: {value!r})"
            )
        return grade

    @model_validator(mode="after")
    def validate_grade_thresholds(self) -> OBQualitySettings:
        """Les seuils doivent décroître strictement : A > B > C."""
        if not (
            self.grade_a_threshold > self.grade_b_threshold > self.grade_c_threshold
        ):
            raise ValueError(
                "Les seuils de grade doivent décroître strictement : "
                "OB_GRADE_A_THRESHOLD > OB_GRADE_B_THRESHOLD > OB_GRADE_C_THRESHOLD"
            )
        if self.max_zone_height_atr <= self.min_zone_height_atr:
            raise ValueError(
                "OB_MAX_ZONE_HEIGHT_ATR doit être supérieur à OB_MIN_ZONE_HEIGHT_ATR"
            )
        return self


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
    gold: GoldMarketConfig = Field(default_factory=GoldMarketConfig)
    active_symbol: str = Field(default=GOLD_SYMBOL, alias="ACTIVE_SYMBOL")
    default_symbols: str = Field(default=GOLD_SYMBOL, alias="DEFAULT_SYMBOLS")
    enable_legacy_symbols: bool = Field(default=False, alias="ENABLE_LEGACY_SYMBOLS")
    default_timeframe: TimeFrame = Field(default=TimeFrame.H1, alias="DEFAULT_TIMEFRAME")

    # Timeframes du flux Gold : H4 = contexte, H1 = biais, M5 = setup/entree.
    context_timeframe: TimeFrame = Field(default=TimeFrame.H4, alias="CONTEXT_TIMEFRAME")
    htf_timeframe: TimeFrame = Field(default=TimeFrame.H1, alias="HTF_TIMEFRAME")
    setup_timeframe: TimeFrame = Field(default=TimeFrame.M5, alias="SETUP_TIMEFRAME")
    entry_timeframe: TimeFrame = Field(default=TimeFrame.M5, alias="ENTRY_TIMEFRAME")

    # Profils instruments conserves pour tests/diagnostics ; seul XAUUSD est actif.
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
                # Phase 3 : qualité de détection (multiples d'ATR, pas de
                # distance fixe). Valeurs à calibrer via backtest comparatif.
                min_fvg_atr=0.25,
                sweep_min_rejection_ratio=0.5,
                sweep_displacement_atr_mult=1.0,
                max_ob_atr_mult=3.0,
                displacement_confirmation_bars=2,
            ),
        },
        description="Profil de trading par symbole (execution specialisee XAUUSD)",
    )

    # Sous-configurations
    mt5: MT5Settings = Field(default_factory=MT5Settings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    signals: SignalSettings = Field(default_factory=SignalSettings)
    validator: ValidatorSettings = Field(default_factory=ValidatorSettings)
    decision: DecisionSettings = Field(default_factory=DecisionSettings)
    position: PositionSettings = Field(default_factory=PositionSettings)
    ob_quality: OBQualitySettings = Field(default_factory=OBQualitySettings)
    news: NewsSettings = Field(default_factory=NewsSettings)
    journal: JournalSettings = Field(default_factory=JournalSettings)
    sessions: SessionSettings = Field(default_factory=SessionSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    api: APISettings = Field(default_factory=APISettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    ai: AISettings = Field(default_factory=AISettings)

    @field_validator(
        "default_timeframe",
        "context_timeframe",
        "htf_timeframe",
        "setup_timeframe",
        "entry_timeframe",
        mode="before",
    )
    @classmethod
    def parse_timeframe(cls, v: str | TimeFrame) -> TimeFrame:
        if isinstance(v, TimeFrame):
            return v
        return TimeFrame(v.upper())

    @field_validator("active_symbol", mode="before")
    @classmethod
    def parse_active_symbol(cls, v: str) -> str:
        symbol = str(v).strip().upper()
        if symbol != GOLD_SYMBOL:
            raise ValueError("Arty est specialise pour XAUUSD : ACTIVE_SYMBOL doit etre XAUUSD")
        return symbol

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
        self.active_symbol = GOLD_SYMBOL
        self.default_symbols = GOLD_SYMBOL
        self.context_timeframe = self.gold.context_timeframe
        self.htf_timeframe = self.gold.htf_timeframe
        self.setup_timeframe = self.gold.entry_timeframe
        self.entry_timeframe = self.gold.entry_timeframe
        self.default_timeframe = self.gold.htf_timeframe
        self.instrument_profiles[GOLD_SYMBOL] = self.gold.instrument_profile()
        self.risk.max_spread = self.gold.max_spread_points
        self.risk.max_spread_by_symbol = {GOLD_SYMBOL: self.gold.max_spread_points}
        return self

    @property
    def symbols_list(self) -> list[str]:
        """Retourne la liste des symboles configurés."""
        return [self.active_symbol]

    @property
    def configured_symbols_list(self) -> list[str]:
        """Retourne les symboles demandes par l'environnement, pour diagnostic."""
        return [s.strip().upper() for s in self.default_symbols.split(",") if s.strip()]

    @property
    def supported_symbols(self) -> list[str]:
        """Retourne la liste des symboles supportés par la stratégie ICT/SMC."""
        return [self.active_symbol]

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
