"""
Provider de données de marché MetaTrader 5.

Implémente le port ``IMarketDataProvider`` et fournit :
- Récupération des données historiques (OHLCV) via ``copy_rates_range``
- Récupération des dernières bougies via ``copy_rates_from_pos``
- Spread actuel via ``symbol_info_tick``
- Infos symbole (digits, point, volume min/max)
- Souscription aux ticks temps réel (polling)
- Cache en mémoire avec TTL pour éviter les appels redondants
- Mode dégradé (mock) si MetaTrader5 non disponible
- Gestion complète des erreurs
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, AsyncIterator

import pandas as pd

from arty_trading.core.entities import Candle
from arty_trading.core.enums import LogCategory, TimeFrame
from arty_trading.core.interfaces import IMarketDataProvider
from arty_trading.infrastructure.cache import InMemoryCache
from arty_trading.logging.logger import get_logger

# Tentative d'import du package MetaTrader5
try:
    import MetaTrader5 as mt5

    MT5_AVAILABLE = True
except (ImportError, OSError):
    mt5 = None  # type: ignore[assignment]
    MT5_AVAILABLE = False

logger = get_logger(LogCategory.MARKET_DATA)


# =============================================================================
# Mapping TimeFrame → constante MT5
# =============================================================================

_TIMEFRAME_MAP: dict[TimeFrame, Any] = {}

def _init_timeframe_map() -> None:
    """Initialise le mapping TimeFrame → MT5 (après import de mt5)."""
    global _TIMEFRAME_MAP
    if not MT5_AVAILABLE:
        return
    _TIMEFRAME_MAP = {
        TimeFrame.M1: mt5.TIMEFRAME_M1,
        TimeFrame.M5: mt5.TIMEFRAME_M5,
        TimeFrame.M15: mt5.TIMEFRAME_M15,
        TimeFrame.M30: mt5.TIMEFRAME_M30,
        TimeFrame.H1: mt5.TIMEFRAME_H1,
        TimeFrame.H4: mt5.TIMEFRAME_H4,
        TimeFrame.D1: mt5.TIMEFRAME_D1,
        TimeFrame.W1: mt5.TIMEFRAME_W1,
        TimeFrame.MN1: mt5.TIMEFRAME_MN1,
    }

_init_timeframe_map()


# =============================================================================
# Exceptions
# =============================================================================


class MarketDataError(Exception):
    """Erreur lors de la récupération des données de marché."""


class SymbolNotFoundError(MarketDataError):
    """Le symbole demandé n'existe pas ou n'est pas disponible."""


# =============================================================================
# Provider
# =============================================================================


class MT5MarketDataProvider(IMarketDataProvider):
    """
    Provider de données de marché via MetaTrader 5.

    Implémente le port ``IMarketDataProvider`` et fournit :
    - Données historiques OHLCV (``copy_rates_range``)
    - Dernières bougies (``copy_rates_from_pos``)
    - Spread actuel (``symbol_info_tick``)
    - Infos symbole (digits, point, volume min/max)
    - Souscription aux ticks temps réel (polling)
    - Cache en mémoire avec TTL

    Attributes:
        _cache: Cache en mémoire pour les données de marché
        _candles_ttl: TTL du cache pour les bougies (secondes)
        _spread_ttl: TTL du cache pour le spread (secondes)
        _symbol_info_ttl: TTL du cache pour les infos symbole (secondes)
    """

    def __init__(
        self,
        cache_ttl: float = 5.0,
        spread_ttl: float = 5.0,
        symbol_info_ttl: float = 300.0,
    ) -> None:
        """
        Initialise le provider de données de marché.

        Args:
            cache_ttl: TTL du cache pour les bougies (5s par défaut - court pour détecter les nouvelles bougies rapidement)
            spread_ttl: TTL du cache pour le spread (5s par défaut)
            symbol_info_ttl: TTL du cache pour les infos symbole (300s par défaut)
        """
        self._cache = InMemoryCache(default_ttl=cache_ttl)
        self._candles_ttl = cache_ttl
        self._spread_ttl = spread_ttl
        self._symbol_info_ttl = symbol_info_ttl

    # -------------------------------------------------------------------------
    # IMarketDataProvider
    # -------------------------------------------------------------------------

    async def get_historical(
        self,
        symbol: str,
        timeframe: TimeFrame,
        start: datetime,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        """
        Récupère les données historiques OHLCV entre deux dates.

        Args:
            symbol: Symbole (ex: EURUSD)
            timeframe: Timeframe (ex: TimeFrame.H1)
            start: Date de début
            end: Date de fin (maintenant si None)

        Returns:
            DataFrame avec colonnes : time, open, high, low, close, volume, spread

        Raises:
            MarketDataError: Si MT5 non disponible ou erreur de récupération
            SymbolNotFoundError: Si le symbole n'existe pas
        """
        symbol = symbol.upper()
        end = end or datetime.now(timezone.utc)
        from arty_trading.config.operational import definitions

        if symbol == "XAUUSD" and timeframe == TimeFrame.D1:
            daily = definitions()["daily_bars"]
            source = TimeFrame(daily["source_timeframe"])
            raw = await self.get_historical(
                symbol, source, start - timedelta(days=daily["source_history_padding_days"]), end
            )
            if raw.empty:
                return raw
            from arty_trading.validation.daily_context import daily_bars

            frame = raw.set_index(pd.DatetimeIndex(pd.to_datetime(raw.time, utc=True)))
            bars = daily_bars(frame, source.minutes, end)
            bars = bars.loc[bars.index >= start].copy()
            bars["time"] = bars.index
            return bars.reset_index(drop=True)

        # Clé de cache
        cache_key = f"hist:{symbol}:{timeframe.value}:{start.isoformat()}:{end.isoformat()}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("Cache hit | historical | %s | %s", symbol, timeframe.value)
            return cached.copy()

        # Mode dégradé
        if not MT5_AVAILABLE:
            logger.warning("MT5 non disponible - retour DataFrame vide | %s", symbol)
            return self._empty_rates_df()

        # S'assurer que le symbole est visible
        await self._ensure_symbol_visible(symbol)

        mt5_tf = self._get_mt5_timeframe(timeframe)

        try:
            rates = await asyncio.to_thread(
                mt5.copy_rates_range, symbol, mt5_tf, start, end
            )
        except Exception as exc:
            logger.error("copy_rates_range échoué | %s | %s | %s", symbol, timeframe.value, exc)
            raise MarketDataError(f"Erreur récupération historique {symbol}: {exc}") from exc

        if rates is None or len(rates) == 0:
            error = mt5.last_error()
            logger.warning("Aucune donnée historique | %s | error=%s", symbol, error)
            return self._empty_rates_df()

        df = self._rates_to_dataframe(rates)
        self._cache.set(cache_key, df, ttl=self._candles_ttl)
        logger.info(
            "Historique récupéré | %s | %s | %d bougies | %s → %s",
            symbol,
            timeframe.value,
            len(df),
            start.isoformat(),
            end.isoformat(),
        )
        return df

    async def get_latest_candles(
        self,
        symbol: str,
        timeframe: TimeFrame,
        count: int = 100,
    ) -> list[Candle]:
        """
        Récupère les dernières bougies OHLCV.

        Args:
            symbol: Symbole (ex: EURUSD)
            timeframe: Timeframe (ex: TimeFrame.H1)
            count: Nombre de bougies (100 par défaut, max 1000)

        Returns:
            Liste d'entités ``Candle``

        Raises:
            MarketDataError: Si MT5 non disponible ou erreur de récupération
        """
        symbol = symbol.upper()
        count = max(1, min(count, 1000))  # Limiter entre 1 et 1000
        if symbol == "XAUUSD" and timeframe == TimeFrame.D1:
            from arty_trading.config.operational import definitions
            from arty_trading.validation.daily_context import daily_candles

            daily = definitions()["daily_bars"]
            source = TimeFrame(daily["source_timeframe"])
            cutoff = datetime.now(UTC)
            # Range retrieval avoids the 1000-bar latest-H1 cap.
            raw = await self.get_historical(
                symbol, source,
                cutoff - timedelta(days=(
                    count * daily["source_calendar_days_per_bar"]
                    + daily["source_history_padding_days"]
                )), cutoff,
            )
            if raw.empty:
                return []
            frame = raw.set_index(pd.DatetimeIndex(pd.to_datetime(raw.time, utc=True)))
            return daily_candles(frame, symbol, source.minutes, cutoff)[-count:]

        # Clé de cache
        cache_key = f"latest:{symbol}:{timeframe.value}:{count}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("Cache hit | latest_candles | %s | %s | %d", symbol, timeframe.value, count)
            return cached

        # Mode dégradé
        if not MT5_AVAILABLE:
            logger.warning("MT5 non disponible - retour liste vide | %s", symbol)
            return []

        # S'assurer que le symbole est visible
        await self._ensure_symbol_visible(symbol)

        mt5_tf = self._get_mt5_timeframe(timeframe)

        try:
            # start_pos=1 exclut la bougie 0 encore en formation.
            rates = await asyncio.to_thread(
                mt5.copy_rates_from_pos, symbol, mt5_tf, 1, count
            )
        except Exception as exc:
            logger.error("copy_rates_from_pos échoué | %s | %s | %s", symbol, timeframe.value, exc)
            raise MarketDataError(f"Erreur récupération bougies {symbol}: {exc}") from exc

        if rates is None or len(rates) == 0:
            error = mt5.last_error()
            logger.warning("Aucune bougie | %s | error=%s", symbol, error)
            return []

        candles = self._rates_to_candles(rates, symbol, timeframe)
        self._cache.set(cache_key, candles, ttl=self._candles_ttl)
        logger.info(
            "Bougies récupérées | %s | %s | %d bougies",
            symbol,
            timeframe.value,
            len(candles),
        )
        return candles

    async def get_spread(self, symbol: str) -> int:
        """
        Récupère le spread actuel en points.

        Args:
            symbol: Symbole (ex: EURUSD)

        Returns:
            Spread en points (int)

        Raises:
            MarketDataError: Si MT5 non disponible ou erreur
        """
        symbol = symbol.upper()

        # Clé de cache (TTL court car le spread change souvent)
        cache_key = f"spread:{symbol}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        # Mode dégradé
        if not MT5_AVAILABLE:
            logger.warning("MT5 non disponible - spread par défaut | %s", symbol)
            return 0

        # S'assurer que le symbole est visible
        await self._ensure_symbol_visible(symbol)

        try:
            tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
        except Exception as exc:
            logger.error("symbol_info_tick échoué | %s | %s", symbol, exc)
            raise MarketDataError(f"Erreur récupération spread {symbol}: {exc}") from exc

        if tick is None:
            error = mt5.last_error()
            logger.warning("Tick indisponible | %s | error=%s", symbol, error)
            return 0

        # spread = ask - bid en points
        info = await self.get_symbol_info(symbol)
        point = info.get("point", 0.00001)
        spread = int(round((tick.ask - tick.bid) / point)) if point else 0

        self._cache.set(cache_key, spread, ttl=self._spread_ttl)
        return spread

    async def subscribe_ticks(self, symbol: str) -> AsyncIterator[dict]:
        """
        Souscrit au flux de ticks temps réel.

        Utilise un polling de ``symbol_info_tick`` à intervalle régulier
        (alternative au WebSocket MT5 qui nécessite un callback synchrone).

        Args:
            symbol: Symbole (ex: EURUSD)

        Yields:
            Dictionnaire avec : bid, ask, last, volume, time

        Raises:
            MarketDataError: Si MT5 non disponible
        """
        symbol = symbol.upper()

        if not MT5_AVAILABLE:
            raise MarketDataError("MT5 non disponible - souscription ticks impossible")

        await self._ensure_symbol_visible(symbol)

        logger.info("Souscription ticks démarrée | %s", symbol)

        try:
            while True:
                try:
                    tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
                except Exception as exc:
                    logger.error("Erreur lors du polling tick | %s | %s", symbol, exc)
                    await asyncio.sleep(1.0)
                    continue

                if tick is None:
                    await asyncio.sleep(0.5)
                    continue

                yield {
                    "symbol": symbol,
                    "bid": float(tick.bid),
                    "ask": float(tick.ask),
                    "last": float(tick.last),
                    "volume": int(tick.volume),
                    "time": datetime.fromtimestamp(tick.time, tz=timezone.utc).isoformat(),
                }

                # Polling à 500ms
                await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            logger.info("Souscription ticks annulée | %s", symbol)
            raise

    # -------------------------------------------------------------------------
    # Méthodes supplémentaires (hors interface)
    # -------------------------------------------------------------------------

    async def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        """
        Récupère les informations d'un symbole.

        Args:
            symbol: Symbole (ex: EURUSD)

        Returns:
            Dictionnaire avec : name, digits, point, volume_min, volume_max,
            volume_step, trade_mode, spread

        Raises:
            SymbolNotFoundError: Si le symbole n'existe pas
        """
        symbol = symbol.upper()

        # Clé de cache (TTL long car les infos symbole changent rarement)
        cache_key = f"symbol_info:{symbol}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        # Mode dégradé
        if not MT5_AVAILABLE:
            return {
                "name": symbol,
                "digits": 5,
                "point": 0.00001,
                "volume_min": 0.01,
                "volume_max": 100.0,
                "volume_step": 0.01,
                "trade_mode": 0,
                "spread": 0,
                "trade_tick_size": 0.00001,
                "trade_tick_value": 1.0,
                "trade_contract_size": 100000,
            }

        try:
            info = await asyncio.to_thread(mt5.symbol_info, symbol)
        except Exception as exc:
            logger.error("symbol_info échoué | %s | %s", symbol, exc)
            raise SymbolNotFoundError(f"Symbole {symbol} introuvable: {exc}") from exc

        if info is None:
            raise SymbolNotFoundError(f"Symbole {symbol} introuvable")

        result = {
            "name": info.name,
            "digits": info.digits,
            "point": info.point,
            "volume_min": info.volume_min,
            "volume_max": info.volume_max,
            "volume_step": info.volume_step,
            "trade_mode": info.trade_mode,
            "spread": info.spread,
            "trade_tick_size": info.trade_tick_size,
            "trade_tick_value": info.trade_tick_value,
            "trade_contract_size": info.trade_contract_size,
        }

        self._cache.set(cache_key, result, ttl=self._symbol_info_ttl)
        return result

    async def get_available_symbols(self) -> list[dict[str, Any]]:
        """
        Récupère la liste des symboles disponibles dans MT5.

        Returns:
            Liste de dictionnaires avec : name, path, digits, point, spread
        """
        # Clé de cache
        cache_key = "all_symbols"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        # Mode dégradé
        if not MT5_AVAILABLE:
            return []

        try:
            symbols_info = await asyncio.to_thread(mt5.symbols_get)
        except Exception as exc:
            logger.error("symbols_get échoué | %s", exc)
            return []

        if symbols_info is None:
            return []

        result = [
            {
                "name": s.name,
                "path": s.path,
                "digits": s.digits,
                "point": s.point,
                "spread": s.spread,
            }
            for s in symbols_info
        ]

        self._cache.set(cache_key, result, ttl=self._symbol_info_ttl)
        logger.info("Symboles disponibles récupérés | %d symboles", len(result))
        return result

    async def get_tick(self, symbol: str) -> dict[str, Any] | None:
        """
        Récupère le tick actuel d'un symbole.

        Args:
            symbol: Symbole (ex: EURUSD)

        Returns:
            Dictionnaire avec bid, ask, last, volume, time, ou None
        """
        symbol = symbol.upper()

        if not MT5_AVAILABLE:
            return None

        await self._ensure_symbol_visible(symbol)

        try:
            tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
        except Exception as exc:
            logger.error("symbol_info_tick échoué | %s | %s", symbol, exc)
            return None

        if tick is None:
            return None

        return {
            "symbol": symbol,
            "bid": float(tick.bid),
            "ask": float(tick.ask),
            "last": float(tick.last),
            "volume": int(tick.volume),
            "time": datetime.fromtimestamp(tick.time, tz=timezone.utc).isoformat(),
        }

    def clear_cache(self) -> None:
        """Vide le cache des données de marché."""
        self._cache.clear()
        logger.info("Cache données de marché vidé")

    # -------------------------------------------------------------------------
    # Helpers internes
    # -------------------------------------------------------------------------

    def _get_mt5_timeframe(self, timeframe: TimeFrame) -> Any:
        """
        Convertit un TimeFrame en constante MT5.

        Args:
            timeframe: TimeFrame de notre enum

        Returns:
            Constante MT5 (mt5.TIMEFRAME_*)

        Raises:
            MarketDataError: Si le timeframe n'est pas supporté
        """
        if not MT5_AVAILABLE:
            raise MarketDataError("MT5 non disponible")
        tf = _TIMEFRAME_MAP.get(timeframe)
        if tf is None:
            raise MarketDataError(f"Timeframe non supporté: {timeframe}")
        return tf

    async def _ensure_symbol_visible(self, symbol: str) -> None:
        """
        S'assure qu'un symbole est visible dans Market Watch.

        Args:
            symbol: Symbole à activer

        Raises:
            SymbolNotFoundError: Si le symbole n'existe pas
        """
        if not MT5_AVAILABLE:
            return

        try:
            visible = await asyncio.to_thread(mt5.symbol_select, symbol, True)
            if not visible:
                # Vérifier si le symbole existe
                info = await asyncio.to_thread(mt5.symbol_info, symbol)
                if info is None:
                    raise SymbolNotFoundError(f"Symbole {symbol} introuvable dans MT5")
        except SymbolNotFoundError:
            raise
        except Exception as exc:
            logger.warning("symbol_select échoué | %s | %s", symbol, exc)

    def _rates_to_dataframe(self, rates: Any) -> pd.DataFrame:
        """
        Convertit les rates MT5 en DataFrame pandas.

        Args:
            rates: Tableau numpy de rates MT5

        Returns:
            DataFrame avec colonnes : time, open, high, low, close, volume, spread
        """
        df = pd.DataFrame(rates)
        # Convertir le timestamp (secondes) en datetime UTC
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    def _rates_to_candles(
        self,
        rates: Any,
        symbol: str,
        timeframe: TimeFrame,
    ) -> list[Candle]:
        """
        Convertit les rates MT5 en liste d'entités Candle.

        Args:
            rates: Tableau numpy de rates MT5
            symbol: Symbole
            timeframe: Timeframe

        Returns:
            Liste d'entités ``Candle``
        """
        candles: list[Candle] = []
        for rate in rates:
            candle = Candle(
                symbol=symbol,
                timeframe=timeframe,
                time=datetime.fromtimestamp(rate["time"], tz=timezone.utc),
                open=Decimal(str(rate["open"])),
                high=Decimal(str(rate["high"])),
                low=Decimal(str(rate["low"])),
                close=Decimal(str(rate["close"])),
                volume=int(rate["tick_volume"]),
                spread=int(rate["spread"]),
            )
            candles.append(candle)
        return candles

    def _empty_rates_df(self) -> pd.DataFrame:
        """Retourne un DataFrame vide avec les bonnes colonnes."""
        return pd.DataFrame(
            columns=["time", "open", "high", "low", "close", "tick_volume", "spread"]
        )