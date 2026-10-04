"""
Router Backtesting - Moteur de backtesting.

Endpoints pour lancer un backtest et consulter les resultats.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from arty_trading.logging.logger import get_logger

router = APIRouter(prefix="/backtesting", tags=["Backtesting"])
logger = get_logger()


class BacktestRequest(BaseModel):
    """Requete de backtest."""

    symbol: str = "EURUSD"
    timeframe: str = "H1"
    count: int = 500
    initial_balance: float = 10000
    risk_per_trade: float = 0.01


@router.post("/run")
async def run_backtest(req: BacktestRequest, request: Request) -> dict:
    """
    Lance un backtest sur les donnees historiques.

    Args:
        symbol: Symbole a tester
        timeframe: Timeframe (H1, M15, etc.)
        count: Nombre de bougies
        initial_balance: Solde initial
        risk_per_trade: Risque par trade (%)
    """
    from arty_trading.core.enums import TimeFrame
    from arty_trading.modules.backtesting import BacktestEngine

    market_data = request.app.state.market_data
    smc_detector = request.app.state.smc_detector
    signal_generator = request.app.state.signal_generator

    tf = TimeFrame(req.timeframe.upper())
    candles = await market_data.get_latest_candles(req.symbol, tf, req.count)

    if not candles:
        return {"success": False, "message": "Aucune bougie disponible", "stats": None}

    engine = BacktestEngine(
        initial_balance=Decimal(str(req.initial_balance)),
        risk_per_trade=req.risk_per_trade,
        symbol=req.symbol,
    )

    stats = await engine.run_async(
        candles=candles,
        signal_generator=signal_generator,
        smc_detector=smc_detector,
    )

    return {
        "success": True,
        "symbol": req.symbol,
        "timeframe": tf.value,
        "candles": len(candles),
        "stats": {
            "total_trades": stats.total_trades,
            "winning_trades": stats.winning_trades,
            "losing_trades": stats.losing_trades,
            "win_rate": round(stats.win_rate, 4),
            "profit_factor": round(stats.profit_factor, 2) if stats.profit_factor != float("inf") else "inf",
            "total_profit": str(stats.total_profit),
            "initial_balance": str(stats.initial_balance),
            "final_balance": str(stats.final_balance),
            "max_drawdown": round(stats.max_drawdown, 4),
            "sharpe_ratio": round(stats.sharpe_ratio, 4),
            "expectancy": str(stats.expectancy),
            "expectancy_r": stats.expectancy_r,
            "fill_rate": stats.fill_rate,
            "unfilled_orders": stats.unfilled_orders,
            "volume_capped_trades": stats.volume_capped_trades,
            "volume_capped_share": round(stats.volume_capped_share, 4),
            "leverage_capped_trades": stats.leverage_capped_trades,
            "margin_capped_trades": stats.margin_capped_trades,
            "cost_sensitivity": stats.cost_sensitivity,
            "cost_assumptions": stats.cost_assumptions,
            "xauusd_diagnostics": stats.xauusd_diagnostics,
            "execution_audit": stats.execution_audit,
            "total_return_pct": round(stats.total_return_pct, 2),
            "max_consecutive_wins": stats.max_consecutive_wins,
            "max_consecutive_losses": stats.max_consecutive_losses,
        },
    }


@router.get("/info")
async def backtest_info() -> dict:
    """Retourne les informations sur le moteur de backtesting."""
    return {
        "engine": "BacktestEngine",
        "features": [
            "Simulation historique",
            "Courbe de capital",
            "Profit Factor",
            "Win Rate",
            "Drawdown",
            "Sharpe Ratio",
            "Expectancy",
            "Statistiques detaillees",
        ],
        "default_params": {
            "initial_balance": 10000,
            "risk_per_trade": 0.01,
            "spread_pips": 1.0,
        },
    }
