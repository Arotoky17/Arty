"""
Entités du domaine (Domain Entities).
Objets métier immuables représentant les concepts centraux du trading.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field

from arty_trading.core.enums import Direction, SignalType, TimeFrame, TradingMode


class Candle(BaseModel):
    """Représente une bougie OHLCV."""

    model_config = {"frozen": True}

    symbol: str
    timeframe: TimeFrame
    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = 0
    spread: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def body_size(self) -> Decimal:
        """Taille du corps de la bougie."""
        return abs(self.close - self.open)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_bullish(self) -> bool:
        """True si la bougie est haussière."""
        return self.close > self.open


class Signal(BaseModel):
    """
    Signal de trading généré par le moteur.
    Contient toutes les informations nécessaires à l'exécution.
    """

    model_config = {"frozen": True}

    id: UUID = Field(default_factory=uuid4)
    symbol: str
    signal_type: SignalType
    direction: Direction
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    confidence: float = Field(ge=0.0, le=1.0, description="Score de confiance 0-1")
    strategy_name: str
    timeframe: TimeFrame
    justification: str = ""
    smc_concepts: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def risk_reward_ratio(self) -> float:
        """Calcule le ratio risque/rendement."""
        risk = abs(float(self.entry_price - self.stop_loss))
        reward = abs(float(self.take_profit - self.entry_price))
        if risk == 0:
            return 0.0
        return round(reward / risk, 2)


class Trade(BaseModel):
    """Représente un trade ouvert ou fermé."""

    id: UUID = Field(default_factory=uuid4)
    symbol: str
    direction: Direction
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    volume: Decimal
    signal_id: UUID | None = None
    strategy_name: str = ""
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: datetime | None = None
    close_price: Decimal | None = None
    profit: Decimal | None = None
    ticket: int | None = None  # Ticket MT5
    is_open: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class TradingAccount(BaseModel):
    """Informations du compte de trading MT5."""

    login: int
    server: str
    name: str = ""
    currency: str = "USD"
    balance: Decimal = Decimal("0")
    equity: Decimal = Decimal("0")
    margin: Decimal = Decimal("0")
    free_margin: Decimal = Decimal("0")
    leverage: int = 100
    mode: TradingMode = TradingMode.ANALYSIS
    is_connected: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_demo(self) -> bool:
        """Vérifie si le compte n'est pas en mode live (rétro-compatibilité)."""
        return self.mode != TradingMode.LIVE

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_paper(self) -> bool:
        """Vérifie si le compte est en mode paper (simulation)."""
        return self.mode == TradingMode.PAPER
