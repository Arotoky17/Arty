"""
API FastAPI - Point d'entrée HTTP/WebSocket.
Endpoints de santé, configuration, MT5 et données de marché.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from arty_trading import __version__
from arty_trading.config import get_settings
from arty_trading.core.enums import LogCategory, TimeFrame
from arty_trading.infrastructure.mt5 import MT5Connector, MT5MarketDataProvider
from arty_trading.logging import setup_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Cycle de vie de l'application : init logging et MT5 au démarrage."""
    settings = get_settings()
    setup_logging(
        level=settings.log_level,
        logs_dir=settings.logs_dir,
        app_env=settings.app_env,
    )
    logger = get_logger(LogCategory.SYSTEM)
    logger.info(
        "Arty démarré | v%s | mode=%s",
        __version__,
        settings.trading_mode.value,
    )

    # Initialisation du connecteur MT5
    mt5_connector = MT5Connector(settings=settings)
    app.state.mt5_connector = mt5_connector

    # Initialisation du provider de données de marché
    market_data = MT5MarketDataProvider()
    app.state.market_data = market_data

    # Tentative de connexion MT5 (non bloquante si MT5 non disponible)
    try:
        connected = await mt5_connector.connect()
        if connected:
            logger.info("MT5 connecté avec succès")
        else:
            logger.info("MT5 non connecté - mode dégradé")
    except Exception as exc:
        logger.warning("Échec connexion MT5 au démarrage: %s", exc)

    yield

    # Déconnexion MT5 propre à l'arrêt
    try:
        await mt5_connector.disconnect()
    except Exception as exc:
        logger.warning("Erreur déconnexion MT5: %s", exc)
    logger.info("Arrêt de la plateforme")


def create_app() -> FastAPI:
    """Factory pour créer l'application FastAPI."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Arty - Plateforme professionnelle de trading Forex (SMC/ICT + IA)",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["Système"])
    async def health_check() -> dict:
        """Vérification de l'état du service."""
        return {
            "status": "healthy",
            "bot": "Arty",
            "name": settings.app_name,
            "version": __version__,
            "trading_mode": settings.trading_mode.value,
            "live_trading_enabled": settings.is_live_trading_enabled,
        }

    @app.get("/config/symbols", tags=["Configuration"])
    async def get_symbols() -> dict:
        """Retourne les symboles configurés."""
        return {
            "symbols": settings.symbols_list,
            "timeframe": settings.default_timeframe.value,
        }

    @app.get("/config/risk", tags=["Configuration"])
    async def get_risk_config() -> dict:
        """Retourne la configuration de risque."""
        return {
            "risk_per_trade": settings.risk.risk_per_trade,
            "max_daily_risk": settings.risk.max_daily_risk,
            "max_drawdown": settings.risk.max_drawdown,
            "max_open_positions": settings.risk.max_open_positions,
            "one_trade_per_symbol": settings.risk.one_trade_per_symbol,
        }

    @app.get("/mt5/status", tags=["MT5"])
    async def mt5_status() -> dict:
        """Retourne le statut de la connexion MetaTrader 5."""
        mt5_connector: MT5Connector = app.state.mt5_connector
        return await mt5_connector.get_connection_status()

    @app.post("/mt5/connect", tags=["MT5"])
    async def mt5_connect() -> dict:
        """Tente de connecter MetaTrader 5."""
        mt5_connector: MT5Connector = app.state.mt5_connector
        success = await mt5_connector.connect()
        return {
            "success": success,
            "status": await mt5_connector.get_connection_status(),
        }

    @app.post("/mt5/disconnect", tags=["MT5"])
    async def mt5_disconnect() -> dict:
        """Déconnecte MetaTrader 5."""
        mt5_connector: MT5Connector = app.state.mt5_connector
        await mt5_connector.disconnect()
        return {
            "success": True,
            "status": await mt5_connector.get_connection_status(),
        }

    @app.post("/mt5/reconnect", tags=["MT5"])
    async def mt5_reconnect() -> dict:
        """Tente une reconnexion MetaTrader 5."""
        mt5_connector: MT5Connector = app.state.mt5_connector
        success = await mt5_connector.reconnect()
        return {
            "success": success,
            "status": await mt5_connector.get_connection_status(),
        }

    # =====================================================================
    # Endpoints Données de marché (Phase 3)
    # =====================================================================

    @app.get("/market/symbols", tags=["Marché"])
    async def get_available_symbols() -> dict:
        """Retourne tous les symboles disponibles dans MT5."""
        market_data: MT5MarketDataProvider = app.state.market_data
        symbols = await market_data.get_available_symbols()
        return {"count": len(symbols), "symbols": symbols}

    @app.get("/market/symbol-info/{symbol}", tags=["Marché"])
    async def get_symbol_info(symbol: str) -> dict:
        """Retourne les informations d'un symbole (digits, point, volume min/max)."""
        market_data: MT5MarketDataProvider = app.state.market_data
        return await market_data.get_symbol_info(symbol)

    @app.get("/market/tick/{symbol}", tags=["Marché"])
    async def get_tick(symbol: str) -> dict:
        """Retourne le tick actuel d'un symbole (bid, ask, last, volume)."""
        market_data: MT5MarketDataProvider = app.state.market_data
        tick = await market_data.get_tick(symbol)
        if tick is None:
            return {"symbol": symbol.upper(), "available": False}
        return tick

    @app.get("/market/spread/{symbol}", tags=["Marché"])
    async def get_spread(symbol: str) -> dict:
        """Retourne le spread actuel en points."""
        market_data: MT5MarketDataProvider = app.state.market_data
        spread = await market_data.get_spread(symbol)
        return {"symbol": symbol.upper(), "spread": spread}

    @app.get("/market/candles/{symbol}", tags=["Marché"])
    async def get_candles(
        symbol: str,
        timeframe: str = "H1",
        count: int = 100,
    ) -> dict:
        """
        Retourne les dernières bougies OHLCV d'un symbole.

        Args:
            symbol: Symbole (ex: EURUSD)
            timeframe: Timeframe (M1, M5, M15, M30, H1, H4, D1, W1, MN1)
            count: Nombre de bougies (1-1000, défaut 100)
        """
        market_data: MT5MarketDataProvider = app.state.market_data
        tf = TimeFrame(timeframe.upper())
        candles = await market_data.get_latest_candles(symbol, tf, count)
        return {
            "symbol": symbol.upper(),
            "timeframe": tf.value,
            "count": len(candles),
            "candles": [
                {
                    "time": c.time.isoformat(),
                    "open": float(c.open),
                    "high": float(c.high),
                    "low": float(c.low),
                    "close": float(c.close),
                    "volume": c.volume,
                    "spread": c.spread,
                }
                for c in candles
            ],
        }

    @app.get("/market/historical/{symbol}", tags=["Marché"])
    async def get_historical(
        symbol: str,
        timeframe: str = "H1",
        start: str = "2024-01-01T00:00:00",
        end: str | None = None,
    ) -> dict:
        """
        Retourne les données historiques OHLCV entre deux dates.

        Args:
            symbol: Symbole (ex: EURUSD)
            timeframe: Timeframe (M1, M5, M15, M30, H1, H4, D1, W1, MN1)
            start: Date de début ISO (ex: 2024-01-01T00:00:00)
            end: Date de fin ISO (maintenant si non fourni)
        """
        market_data: MT5MarketDataProvider = app.state.market_data
        tf = TimeFrame(timeframe.upper())
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end) if end else None
        df = await market_data.get_historical(symbol, tf, start_dt, end_dt)
        return {
            "symbol": symbol.upper(),
            "timeframe": tf.value,
            "count": len(df),
            "data": df.to_dict(orient="records") if len(df) > 0 else [],
        }

    @app.websocket("/market/ticks/{symbol}")
    async def ws_ticks(websocket: WebSocket, symbol: str) -> None:
        """
        WebSocket diffusant les ticks temps réel d'un symbole.

        Envoie un dictionnaire {symbol, bid, ask, last, volume, time}
        toutes les 500ms tant que la connexion est active.
        """
        await websocket.accept()
        market_data: MT5MarketDataProvider = app.state.market_data
        try:
            async for tick in market_data.subscribe_ticks(symbol):
                await websocket.send_json(tick)
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger = get_logger(LogCategory.MARKET_DATA)
            logger.error("Erreur WebSocket ticks | %s | %s", symbol, exc)
            await websocket.close(code=1011, reason=str(exc))

    return app


app = create_app()
