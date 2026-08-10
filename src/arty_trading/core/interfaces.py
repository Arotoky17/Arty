"""
Interfaces (Ports) - Contrats du domaine.
Définissent les abstractions que l'infrastructure doit implémenter.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, AsyncIterator

import pandas as pd

from arty_trading.core.entities import Candle, Signal, Trade, TradingAccount
from arty_trading.core.enums import TimeFrame


class IMarketDataProvider(ABC):
    """Port pour la récupération des données de marché."""

    @abstractmethod
    async def get_historical(
        self,
        symbol: str,
        timeframe: TimeFrame,
        start: datetime,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        """Récupère les données historiques OHLCV."""

    @abstractmethod
    async def get_latest_candles(
        self,
        symbol: str,
        timeframe: TimeFrame,
        count: int = 100,
    ) -> list[Candle]:
        """Récupère les dernières bougies."""

    @abstractmethod
    async def get_spread(self, symbol: str) -> int:
        """Retourne le spread actuel en points."""

    @abstractmethod
    async def subscribe_ticks(self, symbol: str) -> AsyncIterator[dict]:
        """Souscrit au flux de ticks temps réel."""

    @abstractmethod
    async def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        """Retourne les infos d'un symbole (digits, point, tick size/value, volumes)."""


class IMT5Connector(ABC):
    """Port pour la connexion MetaTrader 5."""

    @abstractmethod
    async def connect(self) -> bool:
        """Établit la connexion MT5."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Ferme la connexion MT5."""

    @abstractmethod
    async def is_connected(self) -> bool:
        """Vérifie l'état de la connexion."""

    @abstractmethod
    async def get_account_info(self) -> TradingAccount:
        """Récupère les informations du compte."""

    @abstractmethod
    async def reconnect(self) -> bool:
        """Tente une reconnexion automatique."""


class IOrderExecutor(ABC):
    """Port pour l'exécution des ordres."""

    @abstractmethod
    async def open_order(self, signal: Signal, volume: float) -> Trade:
        """Ouvre un ordre basé sur un signal."""

    @abstractmethod
    async def close_order(self, trade: Trade) -> Trade:
        """Ferme un ordre ouvert."""

    @abstractmethod
    async def modify_order(
        self,
        trade: Trade,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> Trade:
        """Modifie SL/TP d'un ordre existant."""


class ISMCDetector(ABC):
    """Port pour la détection des concepts SMC."""

    @abstractmethod
    async def detect(self, candles: list[Candle], symbol: str) -> list[dict]:
        """Détecte les concepts SMC sur les bougies fournies."""


class IStrategy(ABC):
    """Port pour une stratégie de trading."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Nom unique de la stratégie."""

    @abstractmethod
    async def analyze(
        self,
        candles: list[Candle],
        smc_data: list[dict],
    ) -> Signal | None:
        """Analyse le marché et retourne un signal ou None."""


class IRiskManager(ABC):
    """Port pour la gestion du risque."""

    @abstractmethod
    async def validate_signal(self, signal: Signal, account: TradingAccount) -> bool:
        """Valide si un signal respecte les règles de risque."""

    @abstractmethod
    async def calculate_position_size(
        self,
        signal: Signal,
        account: TradingAccount,
    ) -> float:
        """Calcule la taille de position optimale."""

    @abstractmethod
    async def can_open_trade(self, symbol: str) -> bool:
        """Vérifie si un nouveau trade peut être ouvert."""


class INotifier(ABC):
    """Port pour l'envoi de notifications."""

    @abstractmethod
    async def send(self, title: str, message: str, level: str = "info") -> bool:
        """Envoie une notification."""
