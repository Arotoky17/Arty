"""
Router IA - Assistant intelligent de trading.

Endpoints pour interagir avec l'assistant IA :
- Chat general
- Explication de signaux
- Analyse de trades
- Resume de performances
- Suggestions d'ameliorations
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from arty_trading.logging.logger import get_logger

router = APIRouter(prefix="/ai", tags=["IA Assistant"])
logger = get_logger()


class ChatRequest(BaseModel):
    """Requete de chat IA."""

    message: str
    clear_history: bool = False


class ExplainSignalRequest(BaseModel):
    """Requete d'explication de signal."""

    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float
    strategy_name: str
    risk_reward_ratio: float = 0
    smc_concepts: str = ""
    justification: str = ""


class AnalyzeTradeRequest(BaseModel):
    """Requete d'analyse de trade."""

    symbol: str
    direction: str
    entry_price: float
    exit_price: float = 0
    profit: float = 0
    result: str = "win"
    duration: str = ""
    strategy_name: str = ""
    stop_loss: float = 0
    take_profit: float = 0


class PerformanceSummaryRequest(BaseModel):
    """Requete de resume de performances."""

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0
    profit_factor: float = 0
    total_profit: str = "0"
    initial_balance: str = "10000"
    final_balance: str = "10000"
    max_drawdown: float = 0
    sharpe_ratio: float = 0
    expectancy: str = "0"
    total_return_pct: float = 0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0


class SuggestionsRequest(BaseModel):
    """Requete de suggestions d'amelioration."""

    active_strategies: str = ""
    active_concepts: str = ""
    risk_per_trade: float = 0.01
    max_open_positions: int = 3
    max_drawdown: float = 0.10
    symbols: str = "EURUSD,GBPUSD,USDJPY,XAUUSD"
    timeframe: str = "H1"
    recent_win_rate: float = 0
    recent_trades_count: int = 0


@router.get("/status")
async def ai_status(request: Request) -> dict:
    """Retourne le statut de l'assistant IA."""
    assistant = request.app.state.ai_assistant
    if assistant.provider is None:
        return {"configured": False, "provider": None, "model": None}
    return {
        "configured": True,
        "provider": assistant.provider.name,
        "model": assistant.provider.model,
        "history_length": len(assistant.history),
    }


@router.post("/chat")
async def chat(req: ChatRequest, request: Request) -> dict:
    """
    Pose une question a l'assistant IA.

    L'IA ne peut pas ouvrir de positions - elle ne fait que des suggestions.
    """
    assistant = request.app.state.ai_assistant
    if req.clear_history:
        assistant.clear_history()
    response = await assistant.chat(req.message)
    return {
        "response": response.content,
        "model": response.model,
        "usage": response.usage,
        "history_length": len(assistant.history),
    }


@router.post("/explain/signal")
async def explain_signal(req: ExplainSignalRequest, request: Request) -> dict:
    """Demande a l'IA d'expliquer un signal de trading."""
    assistant = request.app.state.ai_assistant
    signal_data = {
        "symbol": req.symbol,
        "direction": req.direction,
        "entry_price": req.entry_price,
        "stop_loss": req.stop_loss,
        "take_profit": req.take_profit,
        "confidence": req.confidence,
        "strategy_name": req.strategy_name,
        "risk_reward_ratio": req.risk_reward_ratio,
        "smc_concepts": req.smc_concepts,
        "justification": req.justification,
    }
    response = await assistant.explain_signal(signal_data)
    return {"response": response.content, "model": response.model}


@router.post("/analyze/trade")
async def analyze_trade(req: AnalyzeTradeRequest, request: Request) -> dict:
    """Demande a l'IA d'analyser un trade ferme."""
    assistant = request.app.state.ai_assistant
    trade_data = {
        "symbol": req.symbol,
        "direction": req.direction,
        "entry_price": req.entry_price,
        "exit_price": req.exit_price,
        "profit": req.profit,
        "result": req.result,
        "duration": req.duration,
        "strategy_name": req.strategy_name,
        "stop_loss": req.stop_loss,
        "take_profit": req.take_profit,
    }
    response = await assistant.analyze_trade(trade_data)
    return {"response": response.content, "model": response.model}


@router.post("/performance-summary")
async def performance_summary(req: PerformanceSummaryRequest, request: Request) -> dict:
    """Demande a l'IA de resumer les performances de trading."""
    assistant = request.app.state.ai_assistant
    stats_data = req.model_dump()
    response = await assistant.performance_summary(stats_data)
    return {"response": response.content, "model": response.model}


@router.post("/suggestions")
async def suggest_improvements(req: SuggestionsRequest, request: Request) -> dict:
    """Demande a l'IA de suggerer des ameliorations."""
    assistant = request.app.state.ai_assistant
    context_data = req.model_dump()
    response = await assistant.suggest_improvements(context_data)
    return {"response": response.content, "model": response.model}


@router.post("/clear-history")
async def clear_history(request: Request) -> dict:
    """Vide l'historique de conversation IA."""
    assistant = request.app.state.ai_assistant
    assistant.clear_history()
    return {"success": True, "history_length": 0}
