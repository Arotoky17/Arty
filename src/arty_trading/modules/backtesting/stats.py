"""
Calcul des statistiques de backtesting.

Calcule : Profit Factor, Win Rate, Drawdown, Sharpe Ratio, Expectancy,
et autres métriques de performance.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class BacktestStats:
    """Statistiques de performance d'un backtest."""

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_profit: Decimal = Decimal("0")
    gross_profit: Decimal = Decimal("0")
    gross_loss: Decimal = Decimal("0")
    average_profit: Decimal = Decimal("0")
    average_win: Decimal = Decimal("0")
    average_loss: Decimal = Decimal("0")
    max_drawdown: float = 0.0
    max_drawdown_amount: Decimal = Decimal("0")
    sharpe_ratio: float = 0.0
    expectancy: Decimal = Decimal("0")
    largest_win: Decimal = Decimal("0")
    largest_loss: Decimal = Decimal("0")
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    initial_balance: Decimal = Decimal("0")
    final_balance: Decimal = Decimal("0")
    total_return_pct: float = 0.0


def calculate_stats(
    trades: list,
    equity_curve: list[Decimal],
    initial_balance: Decimal = Decimal("10000"),
) -> BacktestStats:
    """
    Calcule les statistiques de performance à partir d'une liste de trades.

    Args:
        trades: Liste de trades fermés (avec profit non-None)
        equity_curve: Courbe d'équité (liste de soldes)
        initial_balance: Solde initial

    Returns:
        BacktestStats avec toutes les métriques
    """
    stats = BacktestStats(initial_balance=initial_balance)

    if not trades:
        stats.final_balance = initial_balance
        return stats

    profits = []
    gross_profit = Decimal("0")
    gross_loss = Decimal("0")
    consecutive_wins = 0
    consecutive_losses = 0
    max_consecutive_wins = 0
    max_consecutive_losses = 0
    largest_win = Decimal("0")
    largest_loss = Decimal("0")

    for trade in trades:
        profit = trade.profit if trade.profit is not None else Decimal("0")
        profits.append(float(profit))
        stats.total_profit += profit
        stats.total_trades += 1

        if profit > 0:
            stats.winning_trades += 1
            gross_profit += profit
            consecutive_wins += 1
            consecutive_losses = 0
            if profit > largest_win:
                largest_win = profit
        else:
            stats.losing_trades += 1
            gross_loss += abs(profit)
            consecutive_losses += 1
            consecutive_wins = 0
            if profit < largest_loss:
                largest_loss = profit

        if consecutive_wins > max_consecutive_wins:
            max_consecutive_wins = consecutive_wins
        if consecutive_losses > max_consecutive_losses:
            max_consecutive_losses = consecutive_losses

    stats.gross_profit = gross_profit
    stats.gross_loss = gross_loss
    stats.largest_win = largest_win
    stats.largest_loss = largest_loss
    stats.consecutive_wins = consecutive_wins
    stats.consecutive_losses = consecutive_losses
    stats.max_consecutive_wins = max_consecutive_wins
    stats.max_consecutive_losses = max_consecutive_losses

    # Win rate
    stats.win_rate = stats.winning_trades / stats.total_trades if stats.total_trades > 0 else 0.0

    # Profit factor
    stats.profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    # Average profit
    stats.average_profit = stats.total_profit / stats.total_trades if stats.total_trades > 0 else Decimal("0")
    stats.average_win = gross_profit / stats.winning_trades if stats.winning_trades > 0 else Decimal("0")
    stats.average_loss = gross_loss / stats.losing_trades if stats.losing_trades > 0 else Decimal("0")

    # Expectancy = (Win% * Avg Win) - (Loss% * Avg Loss)
    if stats.total_trades > 0:
        win_pct = stats.winning_trades / stats.total_trades
        loss_pct = stats.losing_trades / stats.total_trades
        stats.expectancy = (Decimal(str(win_pct)) * stats.average_win) - (Decimal(str(loss_pct)) * stats.average_loss)

    # Final balance
    stats.final_balance = initial_balance + stats.total_profit
    stats.total_return_pct = float(stats.total_profit / initial_balance * 100) if initial_balance > 0 else 0.0

    # Max drawdown
    if equity_curve:
        peak = equity_curve[0]
        max_dd = Decimal("0")
        max_dd_pct = 0.0
        for equity in equity_curve:
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
                if peak > 0:
                    max_dd_pct = float(dd / peak)
        stats.max_drawdown = max_dd_pct
        stats.max_drawdown_amount = max_dd

    # Sharpe ratio (simplified, assuming risk-free rate = 0)
    if len(profits) > 1:
        avg = sum(profits) / len(profits)
        variance = sum((p - avg) ** 2 for p in profits) / (len(profits) - 1)
        std = variance ** 0.5
        stats.sharpe_ratio = avg / std if std > 0 else 0.0

    return stats
