"""
Gestionnaire de risque — implémente IRiskManager.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from arty_trading.config.settings import RiskSettings, get_settings
from arty_trading.core.entities import Signal, Trade, TradingAccount
from arty_trading.core.enums import LogCategory
from arty_trading.core.interfaces import IMarketDataProvider, IRiskManager
from arty_trading.logging.logger import get_logger
from arty_trading.utils.helpers import get_pip_size, pip_value

logger = get_logger(LogCategory.RISK)


class RiskManager(IRiskManager):
    """Gestionnaire de risque central."""

    def __init__(
        self,
        settings: RiskSettings | None = None,
        min_confidence: float = 0.3,
        min_risk_reward: float = 1.0,
        market_data: IMarketDataProvider | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings or get_settings().risk
        self._min_confidence = min_confidence
        self._min_rr = min_risk_reward
        self._market_data = market_data
        self._clock = clock or (lambda: datetime.now(UTC))
        self._loss_breaker_until: datetime | None = None
        self._loss_breaker_rearms = 0
        self.last_rejection_reason: str | None = None
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
        self.last_rejection_reason = None
        if signal.stop_loss == signal.entry_price:
            return self._reject("invalid_stop_loss")
        if signal.take_profit == signal.entry_price:
            return self._reject("invalid_take_profit")
        if signal.confidence < self._min_confidence:
            return self._reject("confidence")
        # Blocage si le spread est trop élevé (conditions de marché dégradées).
        if not await self._spread_within_limit(signal.symbol):
            return self._reject("spread")
        rr = signal.risk_reward_ratio
        if rr < self._min_rr:
            return self._reject("risk_reward")
        if len(self._open_trades) >= self._settings.max_open_positions:
            return self._reject("max_open_positions")
        max_daily = float(account.balance) * self._settings.max_daily_risk
        if float(self._daily_loss) >= max_daily:
            return self._reject("daily_loss")
        if self._current_drawdown >= self._settings.max_drawdown:
            return self._reject("drawdown")
        if self.consecutive_loss_breaker_active():
            return self._reject("consecutive_losses")
        if self._settings.one_trade_per_symbol:
            for trade in self._open_trades:
                if trade.symbol == signal.symbol:
                    return self._reject("one_trade_per_symbol")
        logger.info(
            "Signal validé | %s | %s | confiance=%.2f | R/R=%.2f",
            signal.symbol, signal.direction.value, signal.confidence, rr,
        )
        return True

    def _reject(self, reason: str) -> bool:
        self.last_rejection_reason = reason
        logger.info("Risk rejection | reason=%s", reason)
        return False

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
            pip_size = float(get_pip_size(signal.symbol))
            sl_pips = sl_distance / pip_size
            if sl_pips == 0:
                return 0.01
            pip_val = pip_value(signal.symbol, lot_size=1.0)
            volume = risk_amount / (sl_pips * pip_val)
            sl_ref = sl_pips

        volume = round(volume, 2)
        if volume < 0.01:
            volume = 0.01
        logger.info(
            "Position size | %s | volume=%.2f | risk=%.2f | SL=%.1f ticks",
            signal.symbol, volume, risk_amount, sl_ref,
        )
        return volume

    async def _spread_within_limit(self, symbol: str) -> bool:
        """
        Vérifie que le spread actuel du symbole reste sous le seuil configuré.

        Si le provider de données de marché n'est pas disponible, la règle
        est considérée comme respectée (on ne bloque pas par défaut).

        Returns:
            True si le spread est acceptable (ou inconnu), sinon False.
        """
        if self._market_data is None:
            return True
        try:
            spread = await self._market_data.get_spread(symbol)
        except Exception as exc:  # noqa: BLE001 - on ne bloque pas sur une erreur de lecture
            logger.warning(
                "Impossible de lire le spread | %s | %s", symbol, exc,
            )
            return True
        max_spread = self._settings.max_spread_by_symbol.get(
            symbol.upper(), self._settings.max_spread
        )
        if spread > max_spread:
            logger.warning(
                "Spread trop élevé | %s | spread=%d points | max=%d points - trade bloqué",
                symbol, spread, max_spread,
            )
            return False
        return True

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
        self.consecutive_loss_breaker_active()
        self._open_trades = [t for t in self._open_trades if t.id != trade.id]
        if profit < 0:
            self._daily_loss += abs(profit)
            self._consecutive_losses += 1
            if (
                self._consecutive_losses >= self._settings.max_consecutive_losses
                and self._loss_breaker_until is None
            ):
                self._loss_breaker_until = self._clock() + timedelta(
                    hours=self._settings.consecutive_loss_cooldown_hours
                )
            logger.warning(
                "Perte | %s | profit=%s | pertes consécutives=%d",
                trade.symbol, profit, self._consecutive_losses,
            )
        else:
            self._consecutive_losses = 0
            self._loss_breaker_until = None
            logger.info(
                "Gain | %s | profit=%s | pertes consécutives réinitialisées",
                trade.symbol, profit,
            )

    def consecutive_loss_breaker_active(self) -> bool:
        """Rearm after cooldown using UTC live time or the injected replay clock.

        Daily loss and drawdown limits remain independent of this reset.
        """
        if self._loss_breaker_until is not None and self._clock() >= self._loss_breaker_until:
            self._consecutive_losses = 0
            self._loss_breaker_until = None
            self._loss_breaker_rearms += 1
            logger.info("Circuit breaker rearmed after consecutive-loss cooldown")
        return self._consecutive_losses >= self._settings.max_consecutive_losses

    def update_equity(self, equity: Decimal) -> None:
        """Met à jour l'équité et calcule le drawdown courant."""
        if equity > self._peak_equity:
            self._peak_equity = equity
        if self._peak_equity > 0:
            self._current_drawdown = float(
                (self._peak_equity - equity) / self._peak_equity
            )

    def reset_daily(self) -> None:
        """
        Réinitialise les compteurs journaliers (perte du jour).

        Exécuté automatiquement par le ``TradingEngine`` à minuit UTC afin
        de garantir un reset journalier fiable des circuit breakers.
        """
        self._daily_loss = Decimal("0")
        logger.info("Compteurs journaliers réinitialisés (reset UTC)")

    def get_risk_report(self) -> dict:
        """Retourne un rapport de l'état du risque."""
        active = self.consecutive_loss_breaker_active()
        return {
            "consecutive_loss_breaker_active": active,
            "consecutive_loss_breaker_rearms": self._loss_breaker_rearms,
            "consecutive_loss_breaker_until": (
                self._loss_breaker_until.isoformat() if self._loss_breaker_until else None
            ),
            "consecutive_loss_cooldown_hours": self._settings.consecutive_loss_cooldown_hours,
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
