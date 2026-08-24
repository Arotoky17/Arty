"""Moteur de backtest multi-timeframe (Phase 4) — H1 contexte / M15 setup / M5 entrée.

Reproduit le pipeline live du ``TradingEngine`` :

    H1 (contexte, cache par bougie H1 fermée — MarketContextBuilder)
      → M15 (setup — SetupTracker via ``update_setups_from_market_context``,
      MÊME fonction que le live) → M5 (confirmation — SMCTrendStrategy via
      SignalGenerator) → SignalValidator → DecisionEngine → exécution simulée
      (héritée de ``BacktestEngine`` : sizing, SL/TP, position management).

RÈGLE DE SYNCHRONISATION (anti look-ahead) : à chaque clôture M5 à l'instant
``t = candle.time + 5min``, le contexte disponible est : H1 = bougies dont
``time + 60min <= t`` ; M15 = bougies dont ``time + 15min <= t`` ; M5 =
toutes les bougies jusqu'à la courante incluse (jamais de bougie future).

ORDRE INTRABAR (déterministe, pessimiste) : hérité de
``BacktestEngine._check_open_trades`` — si une même bougie M5 touche SL et
TP, le **SL est évalué en premier**. Jamais le résultat favorable n'est
choisi arbitrairement.

PARITY GAP documenté : le ``RiskManager`` live complet (gardes de compte,
daily loss) n'est pas appelé ; le sizing équivalent (risque fixe % / SL en
pips) de ``BacktestEngine`` est utilisé, et un seul trade ouvert à la fois
est autorisé (miroir du ``can_open_trade`` one-trade-per-symbol du live).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from arty_trading.application.market_context_builder import MarketContextBuilder
from arty_trading.application.setup_service import update_setups_from_market_context
from arty_trading.config.settings import Settings
from arty_trading.core.entities import Candle
from arty_trading.core.enums import LogCategory, TimeFrame
from arty_trading.logging.logger import get_logger
from arty_trading.modules.backtesting.engine import BacktestEngine
from arty_trading.modules.backtesting.stats import calculate_stats

logger = get_logger(LogCategory.BACKTEST)

_MIN_H1 = 30   # bougies H1 minimales avant analyse
_MIN_M15 = 20  # bougies M15 minimales avant analyse
_MIN_M5 = 20   # bougies M5 minimales avant analyse


class _CachedSMCDetector:
    """Wrapper de cache autour de ``SMCDetector.detect`` (performance only).

    ``MarketContextBuilder`` relance la détection SMC H1/M15 à chaque bougie
    M5 alors que ces vues ne changent qu'à leur propre clôture. Le cache,
    indexé par (symbole, timeframe, bornes, longueur, dernier close), renvoie
    exactement le même résultat — aucun changement sémantique.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self._cache: dict[tuple, list] = {}

    @property
    def detectors(self) -> dict:
        # Transparence pour configure_detector_for_symbol (profil instrument).
        return getattr(self._inner, "detectors", {})

    async def detect(self, candles: list, symbol: str) -> list:
        if not candles:
            return await self._inner.detect(candles, symbol)
        key = (
            symbol,
            candles[0].timeframe.value if hasattr(candles[0].timeframe, "value") else str(candles[0].timeframe),
            candles[0].time,
            candles[-1].time,
            len(candles),
            candles[-1].close,
        )
        if key not in self._cache:
            self._cache[key] = await self._inner.detect(candles, symbol)
        return self._cache[key]


class MTFBacktestEngine(BacktestEngine):
    """Backtest H1/M15/M5 réutilisant les composants du pipeline live.

    Pipeline reproduit (identique au ``TradingEngine`` live) :

        H1 (contexte, cache par bougie H1 fermée — MarketContextBuilder)
          → gates précoces (is_neutral + _regime_blocks_trade)
          → M15 (setup — SetupTracker via update_setups_from_market_context,
          MÊME fonction que le live)
          → M5 (confirmation — SMCTrendStrategy via SignalGenerator)
          → SignalValidator (dans generate)
          → DecisionEngine (dans generate)
          → exécution simulée (BacktestEngine : sizing, SL/TP, position management)

    BACKTEST/LIVE PARITY GAPS (documentés) :

    1. **Revalidation + Final Gate** — le live appelle
       ``revalidate_before_execution()`` et ``final_gate_before_execution()``
       (retest validity) juste avant l'OrderSend.  En backtest, le signal et
       l'exécution surviennent sur la même bougie M5 clôturée : il n'y a pas de
       fenêtre temporelle pendant laquelle le contexte pourrait changer.
       Ces gardes sont donc omis (aucune incidence sémantique).

    2. **RiskManager complet** — le live appelle
       ``RiskManager.can_open_trade()``, ``validate_signal()`` et
       ``calculate_position_size()`` (gardes de compte, daily loss,
       max positions).  Le backtest utilise le sizing équivalent de
       ``BacktestEngine`` (risque fixe % / SL en pips) et un seul trade ouvert
       à la fois (miroir du ``can_open_trade`` one-trade-per-symbol du live).

    3. **EconomicCalendar / news** — non disponible en backtest (jamais
       bloquant : ``has_high_impact_news=False``).

    Aucun de ces écarts ne modifie la logique de décision (gating H1/M15/M5,
    Validator, DecisionEngine, SL/TP, RR).  Le backtest est représentatif du
    pipeline H1 → M15 → M5.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        setup_tracker: Any | None = None,
        h1_window: int = 300,
        m15_window: int = 200,
        m5_window: int = 120,
        one_trade_at_a_time: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._settings = settings or Settings()
        self._setup_tracker = setup_tracker
        self._h1_window = h1_window
        self._m15_window = m15_window
        self._m5_window = m5_window
        self._one_trade_at_a_time = one_trade_at_a_time
        self._context_builder: MarketContextBuilder | None = None
        self._signal_generator = None
        self._smc_detector = None
        self._regime_at_index: list[tuple[int, int, str]] | None = None

    async def run_mtf_async(
        self,
        m5_candles: list[Candle],
        m15_candles: list[Candle],
        h1_candles: list[Candle],
        signal_generator,
        smc_detector,
        regime_spans: list[tuple[int, int, str]] | None = None,
    ) -> Any:
        """Balaie les bougies M5 avec le pipeline H1/M15/M5 complet."""
        from arty_trading.modules.smc import SetupTracker

        if not m5_candles:
            return calculate_stats([], self._equity_curve, self._initial_balance)

        self._trade_journal = []
        self._journal_by_ticket = {}
        if self._setup_tracker is None:
            self._setup_tracker = SetupTracker()

        async def _no_download(symbol: str, tf: TimeFrame) -> list[Candle] | None:
            # H4 informatif : indisponible en backtest (jamais utilisé pour
            # gater une décision — voir MarketContextBuilder).
            return []

        self._context_builder = MarketContextBuilder(
            smc_detector=_CachedSMCDetector(smc_detector), download_data=_no_download
        )
        self._signal_generator = signal_generator
        self._smc_detector = smc_detector
        self._regime_at_index = regime_spans

        h1_close = [c.time + timedelta(minutes=60) for c in h1_candles]
        m15_close = [c.time + timedelta(minutes=15) for c in m15_candles]
        h1_idx = 0
        m15_idx = 0

        for i, candle in enumerate(m5_candles):
            # 1) Position management + SL/TP sur la bougie M5 courante
            #    (identique à BacktestEngine.run_async : gestion AVANT signal).
            self._apply_position_management(i, m5_candles)
            self._check_open_trades(candle)

            # 2) Vues clôturées uniquement (anti look-ahead).
            close_time = candle.time + timedelta(minutes=5)
            while h1_idx < len(h1_candles) and h1_close[h1_idx] <= close_time:
                h1_idx += 1
            while m15_idx < len(m15_candles) and m15_close[m15_idx] <= close_time:
                m15_idx += 1

            h1_view = h1_candles[max(0, h1_idx - self._h1_window):h1_idx]
            m15_view = m15_candles[max(0, m15_idx - self._m15_window):m15_idx]
            m5_view = m5_candles[max(0, i + 1 - self._m5_window):i + 1]

            if (
                len(h1_view) >= _MIN_H1
                and len(m15_view) >= _MIN_M15
                and len(m5_view) >= _MIN_M5
            ):
                await self._process_m5_close(candle, i, h1_view, m15_view, m5_view)

            self._update_equity(candle)
            self._equity_curve.append(self._equity)

        # Clôture finale des trades restants (comme le backtest legacy).
        if self._open_trades:
            last_candle = m5_candles[-1]
            for trade in list(self._open_trades):
                self._close_trade(trade, last_candle.close)
                if self._position_manager is not None:
                    self._position_manager.forget(trade)

        return calculate_stats(self._trades, self._equity_curve, self._initial_balance)

    async def _process_m5_close(
        self,
        candle: Candle,
        index: int,
        h1_view: list[Candle],
        m15_view: list[Candle],
        m5_view: list[Candle],
    ) -> None:
        """Pipeline complet pour une clôture M5 : contexte → setup → signal → exécution.

        Miroir du ``TradingEngine._process_symbol`` (pipeline live) :

        1. H1 contexte (``MarketContextBuilder.build`` — même composant).
        2. Gates précoces (``is_neutral`` + ``_regime_blocks_trade`` — mêmes
           vérifications que le live, avant tout calcul de signal).
        3. M15 setup (``update_setups_from_market_context`` — même fonction).
        4. M5 confirmation → SignalValidator → DecisionEngine (dans ``generate``).
        5. Exécution simulée (sizing/SL/TP hérités de ``BacktestEngine``).

        PARITY GAP documentés (voir docstring de classe) :
        - ``revalidate_before_execution`` / ``final_gate_before_execution``
          (gardes d'exécution temps-réel — non pertinents en backtest car le
          signal et l'exécution surviennent sur la même bougie).
        - ``RiskManager`` complet (gardes de compte, daily loss) — le sizing
          équivalent (risque fixe % / SL en pips) de ``BacktestEngine`` est
          utilisé.
        """
        symbol = candle.symbol

        # --- H1 contexte (cache interne par bougie H1 fermée) ---
        context = await self._context_builder.build(
            symbol, h1_view, m5_view, setup_tf_candles=m15_view
        )
        if context is None:
            return

        # --- Gates précoces (miroir du live : neutral + regime) ---
        # Identique à ``TradingEngine._process_symbol`` lignes 618-643.
        if context.is_neutral():
            return
        if context._regime_blocks_trade():
            return

        # --- M15 setup : MÊME logique que le live (setup_service) ---
        update_setups_from_market_context(
            self._setup_tracker, symbol, context, self._settings
        )

        # --- M5 confirmation → Validator → DecisionEngine (dans generate) ---
        try:
            signal = await self._signal_generator.generate(
                m5_view,
                context.ltf_smc_data,
                htf_smc_data=context.htf_smc_data,
                master_trend=context.master_trend,
                market_context=context,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Erreur génération signal MTF | %s | %s", symbol, exc)
            return

        if signal is None:
            return

        if self._one_trade_at_a_time and self._open_trades:
            return

        # --- Exécution simulée (sizing/SL/TP hérités de BacktestEngine) ---
        self._open_trade_from_signal(signal, candle)

        # Miroir du live (TradeOrchestrator) : consommer le setup exécuté.
        setup_id = getattr(signal, "setup_id", None) or (
            getattr(signal, "metadata", {}) or {}
        ).get("setup_id")
        if setup_id:
            setup = self._setup_tracker.get_setup_by_id(setup_id)
            if setup is not None:
                self._setup_tracker.mark_consumed(setup, reason="trade_executed")

        # Enrichir le journal (régime, tier/score du DecisionEngine).
        entry = self._journal_by_ticket.get(self._ticket_counter - 1)
        if entry is not None:
            if self._regime_at_index is not None:
                from arty_trading.modules.backtesting.data_generator import (
                    regime_at_index,
                )

                entry["regime"] = regime_at_index(self._regime_at_index, index)
            decision_meta = (getattr(signal, "metadata", {}) or {}).get("decision", {})
            if decision_meta:
                entry["tier"] = decision_meta.get("tier", "unknown")
                entry["score"] = decision_meta.get("score", 0)
                entry["score_bucket"] = _score_bucket(decision_meta.get("score", 0))

    # ------------------------------------------------------------------
    # Breakdowns Phase 4 (direction / setup / tier / score / régime)
    # ------------------------------------------------------------------

    def journal_breakdown(self, field: str) -> dict[str, dict]:
        """Statistiques groupées par champ du journal (regime, tier, ...)."""
        breakdown: dict[str, dict] = {}
        for entry in self._trade_journal:
            key = str(entry.get(field) or "UNKNOWN")
            stats = breakdown.setdefault(
                key,
                {"trades": 0, "wins": 0, "total_profit": 0.0, "total_r": 0.0,
                 "gross_profit": 0.0, "gross_loss": 0.0},
            )
            stats["trades"] += 1
            profit = entry.get("profit", 0.0)
            stats["wins"] += 1 if profit > 0 else 0
            stats["total_profit"] += profit
            stats["total_r"] += entry.get("r_multiple", 0.0)
            if profit >= 0:
                stats["gross_profit"] += profit
            else:
                stats["gross_loss"] += -profit
        for stats in breakdown.values():
            n = stats["trades"]
            stats["win_rate"] = round(stats["wins"] / n, 3) if n else 0.0
            stats["avg_r"] = round(stats["total_r"] / n, 3) if n else 0.0
            stats["expectancy"] = round(stats["total_profit"] / n, 3) if n else 0.0
            stats["profit_factor"] = (
                round(stats["gross_profit"] / stats["gross_loss"], 3)
                if stats["gross_loss"] > 0
                else 0.0
            )
        return breakdown

    def direction_breakdown(self) -> dict[str, dict]:
        return self.journal_breakdown("direction")

    def regime_breakdown(self) -> dict[str, dict]:
        return self.journal_breakdown("regime")

    def tier_breakdown(self) -> dict[str, dict]:
        return self.journal_breakdown("tier")

    def score_bucket_breakdown(self) -> dict[str, dict]:
        return self.journal_breakdown("score_bucket")


def _score_bucket(score) -> str:
    try:
        s = int(score)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if s >= 80:
        return "80+"
    if s >= 70:
        return "70-79"
    if s >= 60:
        return "60-69"
    return "<60"
