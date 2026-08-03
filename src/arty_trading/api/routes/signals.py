"""
Router Signaux — Génération de signaux de trading.

Endpoints pour générer des signaux et gérer les stratégies.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from arty_trading.core.enums import TimeFrame
from arty_trading.logging.logger import get_logger

router = APIRouter(prefix="/signals", tags=["Signaux"])
logger = get_logger()


@router.get("/strategies")
async def list_strategies(request: Request) -> dict:
    """Liste toutes les stratégies disponibles."""
    signal_generator = request.app.state.signal_generator
    strategies = []
    for name, strategy in signal_generator.strategies.items():
        strategies.append({
            "name": name,
            "enabled": strategy.enabled,
        })
    return {"strategies": strategies}


@router.get("/strategies/enabled")
async def get_enabled_strategies(request: Request) -> dict:
    """Retourne les stratégies activées."""
    signal_generator = request.app.state.signal_generator
    return {"enabled": signal_generator.get_enabled_strategies()}


@router.post("/strategies/{name}/enable")
async def enable_strategy(name: str, request: Request) -> dict:
    """Active une stratégie spécifique."""
    signal_generator = request.app.state.signal_generator
    signal_generator.enable_strategy(name)
    return {"success": True, "enabled": signal_generator.get_enabled_strategies()}


@router.post("/strategies/{name}/disable")
async def disable_strategy(name: str, request: Request) -> dict:
    """Désactive une stratégie spécifique."""
    signal_generator = request.app.state.signal_generator
    signal_generator.disable_strategy(name)
    return {"success": True, "enabled": signal_generator.get_enabled_strategies()}


@router.post("/strategies/enable-all")
async def enable_all_strategies(request: Request) -> dict:
    """Active toutes les stratégies."""
    signal_generator = request.app.state.signal_generator
    signal_generator.enable_all()
    return {"success": True, "enabled": signal_generator.get_enabled_strategies()}


@router.post("/strategies/disable-all")
async def disable_all_strategies(request: Request) -> dict:
    """Désactive toutes les stratégies."""
    signal_generator = request.app.state.signal_generator
    signal_generator.disable_all()
    return {"success": True, "enabled": signal_generator.get_enabled_strategies()}


@router.get("/generate/{symbol}")
async def generate_signal(
    symbol: str,
    request: Request,
    timeframe: str = "H1",
    count: int = 100,
) -> dict:
    """
    Génère le meilleur signal pour un symbole.

    Args:
        symbol: Symbole (ex: EURUSD)
        timeframe: Timeframe (M1, M5, M15, M30, H1, H4, D1, W1, MN1)
        count: Nombre de bougies à analyser (défaut 100)
    """
    signal_generator = request.app.state.signal_generator
    smc_detector = request.app.state.smc_detector
    market_data = request.app.state.market_data

    tf = TimeFrame(timeframe.upper())
    candles = await market_data.get_latest_candles(symbol, tf, count)

    if not candles:
        return {"symbol": symbol.upper(), "signal": None}

    smc_data = await smc_detector.detect(candles, symbol)
    signal = await signal_generator.generate(candles, smc_data)

    if signal is None:
        return {"symbol": symbol.upper(), "signal": None}

    return {
        "symbol": symbol.upper(),
        "signal": {
            "id": str(signal.id),
            "symbol": signal.symbol,
            "signal_type": signal.signal_type.value,
            "direction": signal.direction.value,
            "entry_price": float(signal.entry_price),
            "stop_loss": float(signal.stop_loss),
            "take_profit": float(signal.take_profit),
            "confidence": signal.confidence,
            "strategy_name": signal.strategy_name,
            "timeframe": signal.timeframe.value,
            "risk_reward_ratio": signal.risk_reward_ratio,
            "justification": signal.justification,
            "smc_concepts": signal.smc_concepts,
        },
    }


@router.get("/generate-all/{symbol}")
async def generate_all_signals(
    symbol: str,
    request: Request,
    timeframe: str = "H1",
    count: int = 100,
) -> dict:
    """
    Génère tous les signaux pour un symbole (toutes stratégies activées).

    Args:
        symbol: Symbole (ex: EURUSD)
        timeframe: Timeframe (M1, M5, M15, M30, H1, H4, D1, W1, MN1)
        count: Nombre de bougies à analyser (défaut 100)
    """
    signal_generator = request.app.state.signal_generator
    smc_detector = request.app.state.smc_detector
    market_data = request.app.state.market_data

    tf = TimeFrame(timeframe.upper())
    candles = await market_data.get_latest_candles(symbol, tf, count)

    if not candles:
        return {"symbol": symbol.upper(), "signals": []}

    smc_data = await smc_detector.detect(candles, symbol)
    signals = await signal_generator.generate_all(candles, smc_data)

    return {
        "symbol": symbol.upper(),
        "count": len(signals),
        "signals": [
            {
                "id": str(s.id),
                "symbol": s.symbol,
                "signal_type": s.signal_type.value,
                "direction": s.direction.value,
                "entry_price": float(s.entry_price),
                "stop_loss": float(s.stop_loss),
                "take_profit": float(s.take_profit),
                "confidence": s.confidence,
                "strategy_name": s.strategy_name,
                "risk_reward_ratio": s.risk_reward_ratio,
                "justification": s.justification,
            }
            for s in signals
        ],
    }