"""
Connecteur MetaTrader 5 - Implémentation robuste avec reconnexion automatique.

Fonctionnalités :
- Connexion/déconnexion MT5
- Reconnexion automatique
- Vérification du compte et du terminal
- Mode dégradé (mock) si MT5 non disponible
- Sécurité : compte réel forcé en démo sans autorisation
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from arty_trading.config.settings import Settings
from arty_trading.core.entities import TradingAccount
from arty_trading.core.enums import LogCategory, TradingMode
from arty_trading.core.interfaces import IMT5Connector
from arty_trading.logging.logger import get_logger

# Tentative d'import du package MetaTrader5
# Si l'import échoue (non installé, non Windows, etc.), le connecteur
# fonctionne en mode dégradé (mock) pour permettre le développement et les tests.
try:
    import MetaTrader5 as mt5

    MT5_AVAILABLE = True
except (ImportError, OSError):
    mt5 = None  # type: ignore[assignment]
    MT5_AVAILABLE = False

logger = get_logger(LogCategory.MT5)


# =============================================================================
# Exceptions
# =============================================================================


class MT5ConnectionError(Exception):
    """Erreur de connexion MetaTrader 5."""


class MT5TerminalError(Exception):
    """Erreur liée au terminal MetaTrader 5."""


class MT5AccountError(Exception):
    """Erreur liée au compte MetaTrader 5."""


# =============================================================================
# Connecteur
# =============================================================================


class MT5Connector(IMT5Connector):
    """
    Connecteur MetaTrader 5 robuste.

    Implémente le port ``IMT5Connector`` et fournit :
    - Connexion initialisée avec chemin terminal optionnel
    - Login sécurisé avec timeout configurable
    - Vérification du terminal (build, connected, trade_allowed)
    - Vérification du compte (mode démo forcé si pas de trading réel)
    - Reconnexion automatique
    - Mode dégradé si MetaTrader5 n'est pas disponible

    Attributes:
        _settings: Configuration globale de l'application
        _mt5_settings: Sous-configuration MT5 (login, password, server, path, timeout)
        _connected: État de connexion interne
        _account: Dernier compte récupéré (None si non connecté)
        _terminal_info: Dernières infos du terminal (None si non connecté)
    """

    def __init__(self, settings: Settings) -> None:
        """
        Initialise le connecteur MT5.

        Args:
            settings: Configuration globale de l'application
        """
        self._settings = settings
        self._mt5_settings = settings.mt5
        self._connected: bool = False
        self._account: TradingAccount | None = None
        self._terminal_info: dict[str, Any] | None = None

    # -------------------------------------------------------------------------
    # Connexion / Déconnexion
    # -------------------------------------------------------------------------

    async def connect(self) -> bool:
        """
        Établit la connexion MT5.

        Étapes :
        1. Vérifier la disponibilité du package MetaTrader5
        2. Initialiser le terminal MT5 (avec chemin si configuré)
        3. Vérifier les infos du terminal (connected, trade_allowed)
        4. Authentifier (login, password, server)
        5. Récupérer les infos du compte
        6. Forcer le mode démo si le compte est réel sans autorisation

        Returns:
            True si la connexion a réussi, False sinon
        """
        # Déjà connecté
        if self._connected:
            return True

        # MetaTrader5 non disponible - mode dégradé
        if not MT5_AVAILABLE:
            logger.warning("MetaTrader5 non disponible - mode dégradé activé")
            return False

        try:
            # 1. Initialiser le terminal MT5
            if self._mt5_settings.path:
                initialized = await asyncio.to_thread(
                    mt5.initialize, self._mt5_settings.path
                )
            else:
                initialized = await asyncio.to_thread(mt5.initialize)

            if not initialized:
                error = mt5.last_error()
                logger.error("MT5 initialize échoué | error=%s", error)
                return False

            # 2. Vérifier les infos du terminal
            terminal = await asyncio.to_thread(mt5.terminal_info)
            if terminal is None:
                error = mt5.last_error()
                logger.error("MT5 terminal_info indisponible | error=%s", error)
                await asyncio.to_thread(mt5.shutdown)
                return False

            self._terminal_info = self._parse_terminal_info(terminal)

            # Vérifier que le terminal est connecté
            if not terminal.connected:
                logger.error("Terminal MT5 non connecté")
                await asyncio.to_thread(mt5.shutdown)
                return False

            # 3. Authentifier
            login_result = await asyncio.to_thread(
                mt5.login,
                login=self._mt5_settings.login,
                password=self._mt5_settings.password,
                server=self._mt5_settings.server,
                timeout=self._mt5_settings.timeout,
            )

            if not login_result:
                error = mt5.last_error()
                logger.error(
                    "MT5 login échoué | login=%s | server=%s | error=%s",
                    self._mt5_settings.login,
                    self._mt5_settings.server,
                    error,
                )
                await asyncio.to_thread(mt5.shutdown)
                return False

            # 4. Récupérer les infos du compte
            account_info = await asyncio.to_thread(mt5.account_info)
            if account_info is None:
                error = mt5.last_error()
                logger.error("MT5 account_info indisponible | error=%s", error)
                await asyncio.to_thread(mt5.shutdown)
                return False

            # 5. Parser et valider le compte
            self._account = self._parse_account_info(account_info)
            self._connected = True

            logger.info(
                "MT5 connecté avec succès | login=%s | server=%s | balance=%s | mode=%s",
                self._account.login,
                self._account.server,
                self._account.balance,
                self._account.mode.value,
            )
            return True

        except Exception as exc:
            logger.error("Erreur inattendue lors de la connexion MT5: %s", exc)
            self._connected = False
            self._account = None
            self._terminal_info = None
            return False

    async def disconnect(self) -> None:
        """
        Ferme la connexion MT5 proprement.

        Appelle ``mt5.shutdown()`` si MT5 est disponible et connecté,
        puis réinitialise l'état interne.
        """
        if MT5_AVAILABLE and self._connected:
            try:
                await asyncio.to_thread(mt5.shutdown)
            except Exception as exc:
                logger.warning("Erreur lors du shutdown MT5: %s", exc)

        self._connected = False
        self._account = None
        self._terminal_info = None
        logger.info("MT5 déconnecté")

    async def reconnect(self) -> bool:
        """
        Tente une reconnexion automatique.

        Déconnecte d'abord, puis tente une nouvelle connexion.

        Returns:
            True si la reconnexion a réussi, False sinon
        """
        logger.info("Tentative de reconnexion MT5")
        await self.disconnect()
        return await self.connect()

    # -------------------------------------------------------------------------
    # État et infos
    # -------------------------------------------------------------------------

    async def is_connected(self) -> bool:
        """
        Vérifie l'état de la connexion en temps réel.

        Interroge le terminal MT5 pour vérifier que la connexion est
        toujours active. Si le terminal indique une déconnexion,
        met à jour l'état interne.

        Returns:
            True si connecté, False sinon
        """
        if not MT5_AVAILABLE or not self._connected:
            return False

        try:
            terminal = await asyncio.to_thread(mt5.terminal_info)
            if terminal is None or not terminal.connected:
                self._connected = False
                return False
            return True
        except Exception:
            self._connected = False
            return False

    async def get_account_info(self) -> TradingAccount:
        """
        Récupère les informations du compte de trading.

        Comportement :
        - Si MT5 n'est pas disponible : retourne un compte mock (mode dégradé)
        - Si MT5 est disponible mais non connecté : lève ``MT5ConnectionError``
        - Sinon : retourne les infos du compte (cache ou refresh)

        Returns:
            Entité ``TradingAccount`` avec les infos du compte

        Raises:
            MT5ConnectionError: Si MT5 est disponible mais non connecté
        """
        # Mode dégradé : MT5 non disponible
        if not MT5_AVAILABLE:
            return TradingAccount(
                login=0,
                server="mock",
                is_connected=False,
            )

        # MT5 disponible mais non connecté
        if not self._connected:
            raise MT5ConnectionError(
                "MT5 non connecté - appelez connect() d'abord"
            )

        # Retourner le compte en cache si disponible
        if self._account is not None:
            return self._account

        # Rafraîchir les infos du compte
        account_info = await asyncio.to_thread(mt5.account_info)
        if account_info is None:
            raise MT5AccountError("Impossible de récupérer les infos du compte")

        self._account = self._parse_account_info(account_info)
        return self._account

    async def get_connection_status(self) -> dict[str, Any]:
        """
        Retourne le statut complet de la connexion MT5.

        Returns:
            Dictionnaire avec :
            - connected: bool
            - mt5_available: bool
            - login: int
            - server: str
            - account: dict | None (login, server, balance, equity, leverage, is_demo, mode)
        """
        account_dict: dict[str, Any] | None = None
        if self._account is not None:
            account_dict = {
                "login": self._account.login,
                "server": self._account.server,
                "balance": float(self._account.balance),
                "equity": float(self._account.equity),
                "leverage": self._account.leverage,
                "is_demo": self._account.is_demo,
                "mode": self._account.mode.value,
            }

        return {
            "connected": self._connected,
            "mt5_available": MT5_AVAILABLE,
            "login": self._mt5_settings.login,
            "server": self._mt5_settings.server,
            "account": account_dict,
        }

    async def get_terminal_info(self) -> dict[str, Any] | None:
        """
        Retourne les informations du terminal MT5.

        Returns:
            Dictionnaire avec les infos du terminal, ou None si non connecté.
            Clés : build, connected, trade_allowed, community_account,
            community_connection, started, dlls_allowed, trade_expert, code
        """
        if not MT5_AVAILABLE or not self._connected:
            return None

        try:
            terminal = await asyncio.to_thread(mt5.terminal_info)
            if terminal is None:
                return None
            return self._parse_terminal_info(terminal)
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # Helpers internes
    # -------------------------------------------------------------------------

    def _parse_terminal_info(self, terminal: Any) -> dict[str, Any]:
        """
        Convertit un objet terminal MT5 en dictionnaire.

        Args:
            terminal: Objet namedtuple retourné par ``mt5.terminal_info()``

        Returns:
            Dictionnaire avec les infos du terminal
        """
        return {
            "build": terminal.build,
            "connected": terminal.connected,
            "trade_allowed": terminal.trade_allowed,
            "community_account": getattr(terminal, "community_account", False),
            "community_connection": getattr(terminal, "community_connection", False),
            "started": getattr(terminal, "started", False),
            "dlls_allowed": getattr(terminal, "dlls_allowed", False),
            "trade_expert": getattr(terminal, "trade_expert", False),
            "code": getattr(terminal, "code", 0),
        }

    def _parse_account_info(self, account: Any) -> TradingAccount:
        """
        Convertit un objet compte MT5 en entité ``TradingAccount``.

        Gère la sécurité : un compte réel (trade_mode=1) est forcé
        en mode démo si ``allow_live_trading`` est False.

        Args:
            account: Objet namedtuple retourné par ``mt5.account_info()``

        Returns:
            Entité ``TradingAccount`` avec les infos du compte
        """
        # trade_mode: 0 = démo, 1 = réel, 2 = concours
        is_real = account.trade_mode == 1

        # Sécurité : forcer le mode démo si le trading réel n'est pas autorisé
        if is_real and not self._settings.allow_live_trading:
            mode = TradingMode.DEMO
            logger.warning(
                "Compte réel détecté (login=%s) mais trading réel non autorisé "
                "- forcé en mode DEMO",
                account.login,
            )
        else:
            mode = TradingMode.REAL if is_real else TradingMode.DEMO

        return TradingAccount(
            login=account.login,
            server=account.server,
            name=account.name,
            currency=account.currency,
            balance=Decimal(str(account.balance)),
            equity=Decimal(str(account.equity)),
            margin=Decimal(str(account.margin)),
            free_margin=Decimal(str(account.margin_free)),
            leverage=account.leverage,
            mode=mode,
            is_connected=True,
        )