"""
Router SMC — Analyse Smart Money Concepts.

Endpoints pour détecter les concepts SMC sur les bougies
et gérer l'activation/désactivation des détecteurs.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from arty_trading.core.enums import TimeFrame
from arty_trading.logging.logger import get_logger

router = APIRouter(prefix="/smc", tags=["SMC"])
logger = get_logger()


@router.get("/concepts")
async def list_concepts() -> dict:
    """Liste les détecteurs SMC disponibles."""
    return {
        "concepts": [
            "structure",
            "fair_value_gap",
            "order_blocks",
            "liquidity",
            "premium_discount",
            "sessions",
        ]
    }


@router.get("/detect/{symbol}")
async def detect_smc(
    symbol: str,
    request: Request,
    timeframe: str = "H1",
    count: int = 100,
) -> dict:
    """
    Détecte les concepts SMC sur les dernières bougies d'un symbole.

    Args:
        symbol: Symbole (ex: EURUSD)
        timeframe: Timeframe (M1, M5, M15, M30, H1, H4, D1, W1, MN1)
        count: Nombre de bougies à analyser (10-500, défaut 100)
    """
    smc_detector = request.app.state.smc_detector
    market_data = request.app.state.market_data

    tf = TimeFrame(timeframe.upper())
    candles = await market_data.get_latest_candles(symbol, tf, count)

    if not candles:
        return {"symbol": symbol.upper(), "count": 0, "detections": []}

    detections = await smc_detector.detect(candles, symbol)

    return {
        "symbol": symbol.upper(),
        "timeframe": tf.value,
        "count": len(detections),
        "enabled_concepts": smc_detector.get_enabled_concepts(),
        "detections": detections,
    }


@router.get("/enabled")
async def get_enabled_concepts(request: Request) -> dict:
    """Retourne les détecteurs SMC activés."""
    smc_detector = request.app.state.smc_detector
    return {"enabled": smc_detector.get_enabled_concepts()}


@router.post("/enable/{concept}")
async def enable_concept(concept: str, request: Request) -> dict:
    """Active un détecteur SMC spécifique."""
    smc_detector = request.app.state.smc_detector
    smc_detector.enable_concept(concept)
    return {"success": True, "enabled": smc_detector.get_enabled_concepts()}


@router.post("/disable/{concept}")
async def disable_concept(concept: str, request: Request) -> dict:
    """Désactive un détecteur SMC spécifique."""
    smc_detector = request.app.state.smc_detector
    smc_detector.disable_concept(concept)
    return {"success": True, "enabled": smc_detector.get_enabled_concepts()}


@router.post("/enable-all")
async def enable_all(request: Request) -> dict:
    """Active tous les détecteurs SMC."""
    smc_detector = request.app.state.smc_detector
    smc_detector.enable_all()
    return {"success": True, "enabled": smc_detector.get_enabled_concepts()}


@router.post("/disable-all")
async def disable_all(request: Request) -> dict:
    """Désactive tous les détecteurs SMC."""
    smc_detector = request.app.state.smc_detector
    smc_detector.disable_all()
    return {"success": True, "enabled": smc_detector.get_enabled_concepts()}