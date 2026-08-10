"""
Statistiques de trading en temps réel (TradingStatistics).

Enregistre toutes les statistiques pour les trois modes de trading :
- **ANALYSIS** : nombre d'analyses, signaux générés
- **PAPER** : analyses + signaux + trades simulés (profits/pertes)
- **LIVE** : analyses + signaux + trades réels (profits/pertes)

Les statistiques sont calculées à la demande via ``get_stats()`` et
comprennent : win rate, profit factor, expectancy, drawdown, Sharpe ratio,
équity curve, etc.
"""

from __future__ import annotations

from decimal import Decimal
from datetime import datetime
from typing import Any

from arty_trading.core.entities import Signal, Trade
from arty_trading.core.enums import TradingMode


class TradingStatistics:
    """
    Suivi des statistiques de trading en temps réel.

    Cette classe est injectée dans le ``TradingEngine`` et enregistre
    chaque événement du pipeline (analyse, signal, trade ouvert, trade
    fermé) afin de pouvoir calculer des métriques de performance à tout
    moment via ``get_stats()``.

    Attributes:
        _mode: Mode de trading actuel (ANALYSIS, PAPER, LIVE)
        _initial_balance: Solde initial pour le calcul du P&L
        _current_balance: Solde courant (mis à jour à chaque trade fermé)
        _total_analyses: Nombre total de cycles d'analyse
        _analyses_by_symbol: Nombre d'analyses par symbole
        _signals: Historique des signaux générés
        _trades_opened: Historique des trades ouverts
        _trades_closed: Historique des trades fermés
        _equity_curve: Courbe d'équité (liste de soldes)
    """

    def __init__(
        self,
        mode: TradingMode = TradingMode.ANALYSIS,
        initial_balance: Decimal = Decimal("10000"),
    ) -> None:
        """
        Initialise le tracker de statistiques.

        Args:
            mode: Mode de trading actuel.
            initial_balance: Solde initial pour le calcul du P&L.
        """
        self._mode: TradingMode = mode
        self._initial_balance: Decimal = initial_balance
        self._current_balance: Decimal = initial_balance

        self._total_analyses: int = 0
        self._analyses_by_symbol: dict[str, int] = {}

        self._signals: list[dict[str, Any]] = []
        self._trades_opened: list[dict[str, Any]] = []
        self._trades_closed: list[dict[str, Any]] = []
        self._equity_curve: list[Decimal] = [initial_balance]

    # -------------------------------------------------------------------------
    # Propriétés
    # -------------------------------------------------------------------------

    @property
    def mode(self) -> TradingMode:
        """Mode de trading actuel."""
        return self._mode

    @property
    def total_analyses(self) -> int:
        """Nombre total de cycles d'analyse."""
        return self._total_analyses

    @property
    def total_signals(self) -> int:
        """Nombre total de signaux générés."""
        return len(self._signals)

    @property
    def total_trades(self) -> int:
        """Nombre total de trades ouverts."""
        return len(self._trades_opened)

    @property
    def total_closed_trades(self) -> int:
        """Nombre total de trades fermés."""
        return len(self._trades_closed)

    @property
    def equity_curve(self) -> list[Decimal]:
        """Courbe d'équité."""
        return list(self._equity_curve)

    @property
    def current_balance(self) -> Decimal:
        """Solde courant."""
        return self._current_balance

    # -------------------------------------------------------------------------
    # Mutateurs
    # -------------------------------------------------------------------------

    def set_mode(self, mode: TradingMode) -> None:
        """Met à jour le mode de trading."""
        self._mode = mode

    def set_initial_balance(self, balance: Decimal) -> None:
        """Met à jour le solde initial (et réinitialise le solde courant)."""
        self._initial_balance = balance
        self._current_balance = balance
        self._equity_curve = [balance]

    # -------------------------------------------------------------------------
    # Enregistrement des événements
    # -------------------------------------------------------------------------

    def record_analysis(self, symbol: str) -> None:
        """
        Enregistre un cycle d'analyse terminé.

        Args:
            symbol: Symbole analysé.
        """
        self._total_analyses += 1
        self._analyses_by_symbol[symbol] = (
            self._analyses_by_symbol.get(symbol, 0) + 1
        )

    def record_signal(self, signal: Signal) -> None:
        """
        Enregistre un signal généré.

        Args:
            signal: Le signal généré par le moteur.
        """
        self._signals.append(
            {
                "id": str(signal.id),
                "symbol": signal.symbol,
                "direction": signal.direction.value,
                "signal_type": signal.signal_type.value,
                "entry_price": float(signal.entry_price),
                "stop_loss": float(signal.stop_loss),
                "take_profit": float(signal.take_profit),
                "confidence": signal.confidence,
                "strategy_name": signal.strategy_name,
                "risk_reward_ratio": signal.risk_reward_ratio,
                "time": signal.created_at.isoformat(),
            }
        )

    def record_trade_opened(self, trade: Trade) -> None:
        """
        Enregistre un trade ouvert.

        Args:
            trade: Le trade ouvert.
        """
        self._trades_opened.append(
            {
                "id": str(trade.id),
                "symbol": trade.symbol,
                "direction": trade.direction.value,
                "entry_price": float(trade.entry_price),
                "stop_loss": float(trade.stop_loss),
                "take_profit": float(trade.take_profit),
                "volume": float(trade.volume),
                "ticket": trade.ticket,
                "strategy_name": trade.strategy_name,
                "opened_at": trade.opened_at.isoformat(),
                "risk_reward_ratio": self._risk_reward(trade),
            }
        )

    def record_trade_closed(self, trade: Trade) -> None:
        """
        Enregistre un trade fermé et met à jour la courbe d'équité.

        Args:
            trade: Le trade fermé (avec profit et close_price renseignés).
        """
        profit = trade.profit if trade.profit is not None else Decimal("0")
        self._trades_closed.append(
            {
                "id": str(trade.id),
                "symbol": trade.symbol,
                "direction": trade.direction.value,
                "entry_price": float(trade.entry_price),
                "close_price": float(trade.close_price) if trade.close_price else None,
                "volume": float(trade.volume),
                "ticket": trade.ticket,
                "profit": float(profit),
                "strategy_name": trade.strategy_name,
                "opened_at": trade.opened_at.isoformat(),
                "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
            }
        )
        self._current_balance += profit
        self._equity_curve.append(self._current_balance)

    # -------------------------------------------------------------------------
    # Calcul des statistiques
    # -------------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """
        Calcule et retourne toutes les statistiques de performance.

        Returns:
            Dictionnaire avec :
            - mode: str
            - total_analyses: int
            - total_signals: int
            - total_trades: int
            - total_closed_trades: int
            - winning_trades: int
            - losing_trades: int
            - win_rate: float
            - profit_factor: float
            - total_profit: float
            - gross_profit: float
            - gross_loss: float
            - average_profit: float
            - average_win: float
            - average_loss: float
            - largest_win: float
            - largest_loss: float
            - max_consecutive_wins: int
            - max_consecutive_losses: int
            - initial_balance: float
            - current_balance: float
            - total_return_pct: float
            - max_drawdown: float
            - max_drawdown_amount: float
            - sharpe_ratio: float
            - expectancy: float
            - analyses_by_symbol: dict
            - signals: list
            - equity_curve: list
        """
        total_trades = len(self._trades_closed)
        winning = [t for t in self._trades_closed if t["profit"] > 0]
        losing = [t for t in self._trades_closed if t["profit"] < 0]

        gross_profit = sum(t["profit"] for t in winning)
        gross_loss = sum(abs(t["profit"]) for t in losing)
        total_profit = gross_profit - gross_loss

        win_rate = len(winning) / total_trades if total_trades > 0 else 0.0
        profit_factor = (
            gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0
        )

        avg_profit = total_profit / total_trades if total_trades > 0 else 0.0
        avg_win = gross_profit / len(winning) if winning else 0.0
        avg_loss = gross_loss / len(losing) if losing else 0.0

        largest_win = max((t["profit"] for t in winning), default=0.0)
        largest_loss = min((t["profit"] for t in losing), default=0.0)

        # Séries consécutives
        max_consec_wins = 0
        max_consec_losses = 0
        cur_wins = 0
        cur_losses = 0
        for t in self._trades_closed:
            if t["profit"] > 0:
                cur_wins += 1
                cur_losses = 0
                max_consec_wins = max(max_consec_wins, cur_wins)
            elif t["profit"] < 0:
                cur_losses += 1
                cur_wins = 0
                max_consec_losses = max(max_consec_losses, cur_losses)
            else:
                cur_wins = 0
                cur_losses = 0

        # Expectancy = (Win% * Avg Win) - (Loss% * Avg Loss)
        if total_trades > 0:
            win_pct = len(winning) / total_trades
            loss_pct = len(losing) / total_trades
            expectancy = (win_pct * avg_win) - (loss_pct * avg_loss)
        else:
            expectancy = 0.0

        # Max drawdown
        max_dd = Decimal("0")
        max_dd_pct = 0.0
        if self._equity_curve:
            peak = self._equity_curve[0]
            for equity in self._equity_curve:
                if equity > peak:
                    peak = equity
                dd = peak - equity
                if dd > max_dd:
                    max_dd = dd
                    if peak > 0:
                        max_dd_pct = float(dd / peak)

        # Sharpe ratio (simplified, risk-free rate = 0)
        profits = [t["profit"] for t in self._trades_closed]
        sharpe = 0.0
        if len(profits) > 1:
            avg = sum(profits) / len(profits)
            variance = sum((p - avg) ** 2 for p in profits) / (len(profits) - 1)
            std = variance ** 0.5
            sharpe = avg / std if std > 0 else 0.0

        total_return_pct = (
            float(self._current_balance / self._initial_balance * 100 - 100)
            if self._initial_balance > 0
            else 0.0
        )

        by_symbol = self._performance_by("symbol")
        by_strategy = self._performance_by("strategy_name")
        durations = [
            (datetime.fromisoformat(t["closed_at"]) - datetime.fromisoformat(t["opened_at"])).total_seconds()
            for t in self._trades_closed
            if t["closed_at"]
        ]
        average_rr = (
            sum(t["risk_reward_ratio"] for t in self._trades_opened) / len(self._trades_opened)
            if self._trades_opened
            else 0.0
        )

        return {
            "mode": self._mode.value,
            "total_analyses": self._total_analyses,
            "total_signals": len(self._signals),
            "total_trades": len(self._trades_opened),
            "total_closed_trades": total_trades,
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 4),
            "total_profit": round(total_profit, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "average_profit": round(avg_profit, 2),
            "average_win": round(avg_win, 2),
            "average_loss": round(avg_loss, 2),
            "largest_win": round(largest_win, 2),
            "largest_loss": round(largest_loss, 2),
            "max_consecutive_wins": max_consec_wins,
            "max_consecutive_losses": max_consec_losses,
            "initial_balance": float(self._initial_balance),
            "current_balance": float(self._current_balance),
            "total_return_pct": round(total_return_pct, 2),
            "max_drawdown": round(max_dd_pct, 4),
            "max_drawdown_amount": float(max_dd),
            "sharpe_ratio": round(sharpe, 4),
            "expectancy": round(expectancy, 2),
            "average_risk_reward": round(average_rr, 2),
            "average_time_in_position_seconds": round(sum(durations) / len(durations), 2) if durations else 0.0,
            "performance_by_symbol": by_symbol,
            "performance_by_strategy": by_strategy,
            "analyses_by_symbol": dict(self._analyses_by_symbol),
            "signals": list(self._signals),
            "equity_curve": [float(e) for e in self._equity_curve],
        }

    def get_summary(self) -> dict[str, Any]:
        """
        Retourne un résumé léger (sans les listes détaillées).

        Utile pour les endpoints API où l'on ne veut pas la totalité
        de l'historique.
        """
        stats = self.get_stats()
        return {
            k: v for k, v in stats.items() if k not in ("signals", "equity_curve")
        }

    def _performance_by(self, key: str) -> dict[str, dict[str, float]]:
        """Agrège le P&L et le nombre de trades fermés par dimension."""
        result: dict[str, dict[str, float]] = {}
        for trade in self._trades_closed:
            value = str(trade[key])
            bucket = result.setdefault(value, {"trades": 0.0, "profit": 0.0})
            bucket["trades"] += 1
            bucket["profit"] += float(trade["profit"])
        return result

    @staticmethod
    def _risk_reward(trade: Trade) -> float:
        risk = abs(trade.entry_price - trade.stop_loss)
        reward = abs(trade.take_profit - trade.entry_price)
        return float(reward / risk) if risk else 0.0

    def reset(self) -> None:
        """Réinitialise toutes les statistiques."""
        self._total_analyses = 0
        self._analyses_by_symbol = {}
        self._signals = []
        self._trades_opened = []
        self._trades_closed = []
        self._current_balance = self._initial_balance
        self._equity_curve = [self._initial_balance]
