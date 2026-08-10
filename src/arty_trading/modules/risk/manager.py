"""
Gestionnaire de risque — implémente IRiskManager.
"""

from __future__ import annotations

from decimal import Decimal

from arty_trading.config.settings import RiskSettings, get_settings
from arty_trading.core.entities import Signal, Trade, TradingAccount
from arty_trading.core.enums import LogCategory
from arty_trading.core.interfaces import IMarketDataProvider, IRiskManager
from arty_trading.logging.logger import get_logger

logger = get_logger(LogCategory.RISK)


class RiskManager(IRiskManager):
    """Gestionnaire de risque central."""

    def __init__(
        self,
        settings: RiskSettings | None = None,
        min_confidence: float = 0.3,
        min_risk_reward: float = 1.0,
        market_data: IMarketDataProvider | None = None,
    ) -> None:
        self._settings = settings or get_settings().risk
        self._min_confidence = min_confidence
        self._min_rr = min_risk_reward
        self._market_data = market_data
        self._open_trades: list[Trade] = []
        self._daily_loss: Decimal = Decimal("0")
        self._consecutive_losses: int = 0
        self._peak_equity: Decimal = Decimal("0")
        self._current_drawdown: float = 0.0

    @property
    def market_data(self) -> IMarketDataProvider | None:
        """Provider de données de marché utilisé pour le calcul de position."""
        return self._market_data

    @market_data.setter
    def market_data(self, value: IMarketDataProvider | None) -> None:
        self._market_data = value

    @property
    def settings(self) -> RiskSettings:
        return self._settings

    @property
    def open_trades(self) -> list[Trade]:
        return list(self._open_trades)

    @property
    def open_positions_count(self) -> int:
        return len(self._open_trades)

    @property
    def daily_loss(self) -> Decimal:
        return self._daily_loss

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    @property
    def current_drawdown(self) -> float:
        return self._current_drawdown

    async def validate_signal(self, signal: Signal, account: TradingAccount) -> bool:
        """Valide si un signal respecte toutes les règles de risque."""
        if signal.stop_loss == signal.entry_price:
            return False
        if signal.take_profit == signal.entry_price:
            return False
        if signal.confidence < self._min_confidence:
            return False
        rr = signal.risk_reward_ratio
        if rr < self._min_rr:
            return False
        if len(self._open_trades) >= self._settings.max_open_positions:
            return False
        max_daily = float(account.balance) * self._settings.max_daily_risk
        if float(self._daily_loss) >= max_daily:
            return False
        if self._current_drawdown >= self._settings.max_drawdown:
            return False
        if self._consecutive_losses >= self._settings.max_consecutive_losses:
            return False
        if self._settings.one_trade_per_symbol:
            for trade in self._open_trades:
                if trade.symbol == signal.symbol:
                    return False
        logger.info(
            "Signal validé | %s | %s | confiance=%.2f | R/R=%.2f",
            signal.symbol, signal.direction.value, signal.confidence, rr,
        )
        return True

    async def calculate_position_size(
        self,
        signal: Signal,
        account: TradingAccount,
    ) -> float:
        """Calcule la taille de position optimale basée sur le risque par trade.

        Le calcul s'appuie sur les vraies caractéristiques du symbole
        (``trade_tick_size`` et ``trade_tick_value``) fournies par le provider
        de données de marché plutôt que sur des constantes. En l'absence de
        provider (ou en cas d'erreur), on retombe sur une heuristique.
        """
        risk_amount = float(account.balance) * self._settings.risk_per_trade
        sl_distance = abs(float(signal.entry_price - signal.stop_loss))
        if sl_distance == 0:
            return 0.01

        tick_size, tick_value = await self._get_tick_info(signal.symbol)
        if tick_size and tick_value:
            sl_ticks = sl_distance / tick_size
            if sl_ticks == 0:
                return 0.01
            loss_per_lot = sl_ticks * tick_value
            if loss_per_lot <= 0:
                return 0.01
            volume = risk_amount / loss_per_lot
            sl_ref = sl_ticks
        else:
            # Repli heuristique : constantes approximatives (pip 10 USD/lot).
            pip_value_per_lot = 10.0
            pip_size = 0.01 if "JPY" in signal.symbol else 0.0001
            sl_pips = sl_distance / pip_size
            if sl_pips == 0:
                return 0.01
            volume = risk_amount / (sl_pips * pip_value_per_lot)
            sl_ref = sl_pips

        volume = round(volume, 2)
        if volume < 0.01:
            volume = 0.01
        logger.info(
            "Position size | %s | volume=%.2f | risk=%.2f | SL=%.1f ticks",
            signal.symbol, volume, risk_amount, sl_ref,
        )
        return volume

    async def _get_tick_info(self, symbol: str) -> tuple[float | None, float | None]:
        """Interroge les vraies infos du symbole (tick size/value).

        Returns:
            Un tuple (``trade_tick_size``, ``trade_tick_value``) ou (None, None)
            si le provider n'est pas disponible ou si les données sont invalides.
        """
        if self._market_data is None:
            return None, None
        try:
            info = await self._market_data.get_symbol_info(symbol)
        except Exception as exc:  # noqa: BLE001 - repli heuristique en cas d'erreur
            logger.warning(
                "Impossible de lire les infos symbole | %s | %s", symbol, exc,
            )
            return None, None
        tick_size = info.get("trade_tick_size")
        tick_value = info.get("trade_tick_value")
        if not tick_size or not tick_value:
            return None, None
        return float(tick_size), float(tick_value)

    async def can_open_trade(self, symbol: str) -> bool:
        """Vérifie si un nouveau trade peut être ouvert sur ce symbole."""
        if len(self._open_trades) >= self._settings.max_open_positions:
            return False
        if self._settings.one_trade_per_symbol:
            for trade in self._open_trades:
                if trade.symbol == symbol:
                    return False
        return True

    def register_trade(self, trade: Trade) -> None:
        """Enregistre un trade ouvert dans le suivi du risque."""
        self._open_trades.append(trade)
        logger.info(
            "Trade enregistré | %s | %s | volume=%s",
            trade.symbol, trade.direction.value, trade.volume,
        )

    def close_trade(self, trade: Trade, profit: Decimal) -> None:
        """Ferme un trade et met à jour les compteurs de risque."""
        self._open_trades = [t for t in self._open_trades if t.id != trade.id]
        if profit < 0:
            self._daily_loss += abs(profit)
            self._consecutive_losses += 1
            logger.warning(
                "Perte | %s | profit=%s | pertes consécutives=%d",
                trade.symbol, profit, self._consecutive_losses,
            )
        else:
            self._consecutive_losses = 0
            logger.info(
                "Gain | %s | profit=%s | pertes consécutives réinitialisées",
                trade.symbol, profit,
            )

    def update_equity(self, equity: Decimal) -> None:
        """Met à jour l'équité et calcule le drawdown courant."""
        if equity > self._peak_equity:
            self._peak_equity = equity
        if self._peak_equity > 0:
            self._current_drawdown = float(
                (self._peak_equity - equity) / self._peak_equity
            )

    def reset_daily(self) -> None:
        """Réinitialise les compteurs journaliers."""
        self._daily_loss = Decimal("0")
        logger.info("Compteurs journaliers réinitialisés")

    def get_risk_report(self) -> dict:
        """Retourne un rapport de l'état du risque."""
        return {
            "open_positions": len(self._open_trades),
            "max_open_positions": self._settings.max_open_positions,
            "daily_loss": str(self._daily_loss),
            "max_daily_risk_pct": self._settings.max_daily_risk * 100,
            "consecutive_losses": self._consecutive_losses,
            "max_consecutive_losses": self._settings.max_consecutive_losses,
            "current_drawdown_pct": round(self._current_drawdown * 100, 2),
            "max_drawdown_pct": self._settings.max_drawdown * 100,
            "risk_per_trade_pct": self._settings.risk_per_trade * 100,
            "one_trade_per_symbol": self._settings.one_trade_per_symbol,
        }
