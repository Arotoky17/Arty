"""
Debugger de décision de trading — observabilité et diagnostic.

Le ``TradeDecisionDebugger`` enregistre chaque décision du pipeline Arty
et produit des statistiques périodiques pour identifier précisément
pourquoi les setups/signaux sont rejetés.

Ce module ne modifie AUCUNE logique de trading. Il observe uniquement
les résultats déjà produits par les composants existants.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

from arty_trading.application.trade_decision_diagnostic import (
    PipelineStep,
    RejectionReason,
    TradeDecisionDiagnostic,
)
from arty_trading.core.enums import TimeFrame

logger = logging.getLogger(__name__)

_BUY = "buy"
_SELL = "sell"


class TradeDecisionDebugger:
    """
    Debugger centralisé pour les décisions de trading.

    Cycle de vie d'une opportunité (1 opportunité = 1 ``TradeDecisionDiagnostic``) ::

        diagnostic = debugger.start_opportunity(symbol, timeframe)
        debugger.record_step(diagnostic, PipelineStep.DATA_AVAILABLE)
        debugger.fail_step(
            diagnostic, PipelineStep.MASTER_DIRECTION_GATE,
            RejectionReason.MASTER_TREND_CONFLICT,
        )
        debugger.finalize_opportunity(diagnostic)

    La comptabilisation finale (``opportunities_seen`` / ``approved`` /
    ``rejected``, ``signals_generated``, ``orders_submitted`` /
    ``orders_executed``, etc.) n'est effectuée qu'une **seule** fois par
    opportunité. Les étapes, raisons, bougies et setups sont dédupliqués.

    Ce module ne modifie AUCUNE logique de trading : il observe uniquement
    les résultats déjà produits par les composants existants.
    """

    def __init__(self, enabled: bool = True, summary_interval: int = 100) -> None:
        """
        Initialise le debugger.

        Args:
            enabled: Active/désactive la collecte de diagnostic.
            summary_interval: Nombre d'opportunités entre chaque résumé périodique.
        """
        self._enabled = enabled
        self._summary_interval = 100
        self.summary_interval = summary_interval

        self._opportunity_counter: int = 0
        self._diagnostics: list[TradeDecisionDiagnostic] = []
        self._last_diagnostic_by_symbol: dict[str, TradeDecisionDiagnostic] = {}
        self._active_diagnostic_by_symbol: dict[str, TradeDecisionDiagnostic] = {}
        self._finalized: set[str] = set()

        # Compteurs globaux
        self._opportunities_seen: int = 0
        self._opportunities_rejected: int = 0
        self._opportunities_approved: int = 0
        self._candles_analyzed: int = 0
        self._setups_detected: int = 0
        self._buy_candidates: int = 0
        self._sell_candidates: int = 0
        self._signals_generated: int = 0
        self._orders_submitted: int = 0
        self._orders_executed: int = 0

        self._step_counts: dict[str, int] = defaultdict(int)
        self._step_fail_counts: dict[str, int] = defaultdict(int)
        self._rejection_counts: dict[str, int] = defaultdict(int)

        # Déduplication
        self._seen_candles: set[str] = set()
        self._seen_setup_ids: set[str] = set()
        self._direction_counted: set[str] = set()

        self._symbol_stats: dict[str, dict[str, Any]] = defaultdict(
            self._empty_symbol_stats
        )

    @property
    def enabled(self) -> bool:
        """Indique si le debugger est activé."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def summary_interval(self) -> int:
        """Intervalle de résumé périodique."""
        return self._summary_interval

    @summary_interval.setter
    def summary_interval(self, value: int) -> None:
        self._summary_interval = max(1, value)

    @staticmethod
    def _empty_symbol_stats() -> dict[str, Any]:
        """Structure initiale des statistiques d'un symbole."""
        return {
            "opportunities_seen": 0,
            "opportunities_rejected": 0,
            "opportunities_approved": 0,
            "candles_analyzed": 0,
            "setups_detected": 0,
            "buy_candidates": 0,
            "sell_candidates": 0,
            "signals_generated": 0,
            "orders_submitted": 0,
            "orders_executed": 0,
            "rejections": defaultdict(int),
        }

    # -------------------------------------------------------------------------
    # API de cycle de vie (1 opportunité = 1 diagnostic)
    # -------------------------------------------------------------------------

    def start_opportunity(
        self,
        symbol: str,
        timeframe: TimeFrame | None = None,
        direction: str = "neutral",
        candle_time: datetime | None = None,
    ) -> TradeDecisionDiagnostic:
        """
        Crée (et compte une seule fois) une nouvelle opportunité.
        """
        self._opportunity_counter += 1
        diagnostic = TradeDecisionDiagnostic(
            opportunity_id=f"{symbol.upper()}_{self._opportunity_counter}",
            symbol=symbol.upper(),
            timeframe=timeframe or TimeFrame.H1,
            direction_candidate=direction,
            candle_time=candle_time,
        )
        self._active_diagnostic_by_symbol[diagnostic.symbol] = diagnostic

        if not self._enabled:
            return diagnostic

        self._opportunities_seen += 1
        self._symbol_stats[diagnostic.symbol]["opportunities_seen"] += 1
        return diagnostic

    def log_ob_rejection(self, payload: dict[str, Any]) -> None:
        """Journalise un OB rejeté et l'attache au diagnostic actif du symbole."""
        symbol = str(payload.get("symbol", "")).upper()
        diagnostic = self._active_diagnostic_by_symbol.get(symbol)
        if diagnostic is not None and self._enabled:
            diagnostic.ob_rejections.append(dict(payload))
        logger.info("OB quality rejected | %s", payload)

    def record_step(
        self, diagnostic: TradeDecisionDiagnostic, step: PipelineStep | str
    ) -> None:
        """
        Marque une étape comme PASS (dédupliquée).

        N'incrémente jamais ``opportunities_seen``.
        """
        step_str = step.value if isinstance(step, PipelineStep) else str(step)
        if step_str in diagnostic.steps_completed:
            return
        diagnostic.steps_completed.append(step_str)
        if self._enabled:
            self._step_counts[step_str] += 1

    def fail_step(
        self,
        diagnostic: TradeDecisionDiagnostic,
        step: PipelineStep | str,
        reason: RejectionReason | str,
        message: str = "",
    ) -> None:
        """
        Marque une étape comme FAIL et enregistre la raison de rejet.

        Le diagnostic est marqué ``rejected``.
        """
        step_str = step.value if isinstance(step, PipelineStep) else str(step)
        if step_str not in diagnostic.steps_failed:
            diagnostic.steps_failed.append(step_str)
            if self._enabled:
                self._step_fail_counts[step_str] += 1

        reason_str = reason.value if isinstance(reason, RejectionReason) else str(reason)
        if reason_str:
            if reason_str not in diagnostic.rejection_reasons:
                diagnostic.rejection_reasons.append(reason_str)
                if self._enabled:
                    self._rejection_counts[reason_str] += 1
                    self._symbol_stats[diagnostic.symbol]["rejections"][reason_str] += 1
            if not diagnostic.primary_rejection_reason:
                diagnostic.primary_rejection_reason = reason_str

        diagnostic.decision = "rejected"
        if message:
            diagnostic.metadata[f"{step_str}_message"] = message

    def add_rejection_reason(
        self, diagnostic: TradeDecisionDiagnostic, reason: RejectionReason | str
    ) -> None:
        """
        Ajoute une raison de rejet secondaire (détail) sans marquer d'étape.

        Préserve la raison primaire si elle est déjà définie.
        """
        reason_str = reason.value if isinstance(reason, RejectionReason) else str(reason)
        if not reason_str or reason_str in diagnostic.rejection_reasons:
            return
        diagnostic.rejection_reasons.append(reason_str)
        if self._enabled:
            self._rejection_counts[reason_str] += 1
            self._symbol_stats[diagnostic.symbol]["rejections"][reason_str] += 1
        if not diagnostic.primary_rejection_reason:
            diagnostic.primary_rejection_reason = reason_str

    def set_direction(
        self, diagnostic: TradeDecisionDiagnostic, direction: str | None
    ) -> None:
        """
        Propage la direction réelle du candidat (BUY / SELL).

        Met à jour ``direction_candidate`` et compte le candidat BUY/SELL
        une seule fois par opportunité. Ne devine jamais.
        """
        norm = (direction or "").strip().lower()
        if norm not in (_BUY, _SELL):
            return

        diagnostic.direction_candidate = norm.upper()

        if not self._enabled:
            return
        if diagnostic.opportunity_id in self._direction_counted:
            return
        self._direction_counted.add(diagnostic.opportunity_id)
        if norm == _BUY:
            self._buy_candidates += 1
            self._symbol_stats[diagnostic.symbol]["buy_candidates"] += 1
        else:
            self._sell_candidates += 1
            self._symbol_stats[diagnostic.symbol]["sell_candidates"] += 1

    def record_candle_analyzed(
        self,
        symbol: str,
        timeframe: TimeFrame | str,
        candle_time: datetime | None,
    ) -> None:
        """
        Compte une bougie réellement traitée (dédupliquée par
        symbole + timeframe + candle_time).
        """
        if candle_time is None or not self._enabled:
            return
        tf = timeframe.value if isinstance(timeframe, TimeFrame) else str(timeframe)
        key = f"{symbol.upper()}|{tf}|{candle_time.isoformat()}"
        if key in self._seen_candles:
            return
        self._seen_candles.add(key)
        self._candles_analyzed += 1
        self._symbol_stats[symbol.upper()]["candles_analyzed"] += 1

    def record_setup_detected(
        self, symbol: str, setup_id: str, direction: str | None = None
    ) -> None:
        """
        Compte un nouveau setup détecté (dédupliqué par ``setup_id``).
        """
        if not self._enabled or not setup_id:
            return
        if setup_id in self._seen_setup_ids:
            return
        self._seen_setup_ids.add(setup_id)
        self._setups_detected += 1
        self._symbol_stats[symbol.upper()]["setups_detected"] += 1

    def finalize_opportunity(self, diagnostic: TradeDecisionDiagnostic) -> None:
        """
        Comptabilise une seule fois le résultat final de l'opportunité.

        Détermine approved/rejected et compte signaux, ordres soumis et
        ordres exécutés. Idempotent.
        """
        if not self._enabled:
            return
        if diagnostic.opportunity_id in self._finalized:
            return
        self._finalized.add(diagnostic.opportunity_id)

        self._diagnostics.append(diagnostic)
        self._last_diagnostic_by_symbol[diagnostic.symbol] = diagnostic
        if self._active_diagnostic_by_symbol.get(diagnostic.symbol) is diagnostic:
            del self._active_diagnostic_by_symbol[diagnostic.symbol]

        stats = self._symbol_stats[diagnostic.symbol]

        if diagnostic.decision == "accepted":
            self._opportunities_approved += 1
            stats["opportunities_approved"] += 1
        elif diagnostic.decision == "rejected":
            self._opportunities_rejected += 1
            stats["opportunities_rejected"] += 1

        if PipelineStep.SIGNAL_GENERATED.value in diagnostic.steps_completed:
            self._signals_generated += 1
            stats["signals_generated"] += 1
        if PipelineStep.ORDER_SUBMITTED.value in diagnostic.steps_completed:
            self._orders_submitted += 1
            stats["orders_submitted"] += 1
        if PipelineStep.ORDER_EXECUTED.value in diagnostic.steps_completed:
            self._orders_executed += 1
            stats["orders_executed"] += 1

        logger.debug(diagnostic.to_log_summary())

        if self._opportunities_seen % self._summary_interval == 0:
            self._log_periodic_summary()

    # -------------------------------------------------------------------------
    # API de compatibilité (enregistrement d'un diagnostic déjà complet)
    # -------------------------------------------------------------------------

    def record_opportunity(self, diagnostic: TradeDecisionDiagnostic) -> None:
        """
        Enregistre un diagnostic **déjà complet** comme une seule opportunité.

        Rétro-compatible : applique les étapes, raisons et la comptabilisation
        finale en une seule passe (aucun double comptage).
        """
        if not self._enabled:
            return

        if not diagnostic.opportunity_id:
            self._opportunity_counter += 1
            diagnostic.opportunity_id = (
                f"{diagnostic.symbol.upper()}_{self._opportunity_counter}"
            )

        self._opportunities_seen += 1
        self._symbol_stats[diagnostic.symbol]["opportunities_seen"] += 1

        for step in diagnostic.steps_completed:
            self._step_counts[step] += 1
        for step in diagnostic.steps_failed:
            self._step_fail_counts[step] += 1
        for reason in diagnostic.rejection_reasons:
            self._rejection_counts[reason] += 1
            self._symbol_stats[diagnostic.symbol]["rejections"][reason] += 1

        self.finalize_opportunity(diagnostic)

    def get_last_diagnostic(self, symbol: str) -> TradeDecisionDiagnostic | None:
        """
        Retourne le dernier diagnostic pour un symbole.

        Args:
            symbol: Symbole à interroger.

        Returns:
            Le dernier diagnostic ou None.
        """
        return self._last_diagnostic_by_symbol.get(symbol.upper())

    def get_statistics(self) -> dict[str, Any]:
        """
        Retourne les statistiques agrégées (globales + par symbole).

        Returns:
            Dictionnaire avec les statistiques complètes.
        """
        step_counts = dict(self._step_counts)
        for step, count in self._step_fail_counts.items():
            step_counts[f"{step}_failed"] = count

        return {
            # Clés rétro-compatibles
            "total_opportunities": self._opportunities_seen,
            "total_setups_detected": self._setups_detected,
            "total_signals_generated": self._signals_generated,
            "total_orders_submitted": self._orders_submitted,
            "total_orders_executed": self._orders_executed,
            # Nouveaux compteurs
            "opportunities_seen": self._opportunities_seen,
            "opportunities_rejected": self._opportunities_rejected,
            "opportunities_approved": self._opportunities_approved,
            "candles_analyzed": self._candles_analyzed,
            "setups_detected": self._setups_detected,
            "buy_candidates": self._buy_candidates,
            "sell_candidates": self._sell_candidates,
            "signals_generated": self._signals_generated,
            "orders_submitted": self._orders_submitted,
            "orders_executed": self._orders_executed,
            "step_counts": step_counts,
            "rejection_counts": dict(self._rejection_counts),
            "by_symbol": {
                symbol: {
                    "opportunities": stats["opportunities_seen"],
                    "opportunities_seen": stats["opportunities_seen"],
                    "opportunities_rejected": stats["opportunities_rejected"],
                    "opportunities_approved": stats["opportunities_approved"],
                    "candles_analyzed": stats["candles_analyzed"],
                    "setups_detected": stats["setups_detected"],
                    "buy_candidates": stats["buy_candidates"],
                    "sell_candidates": stats["sell_candidates"],
                    "signals_generated": stats["signals_generated"],
                    "orders_submitted": stats["orders_submitted"],
                    "orders_executed": stats["orders_executed"],
                    "rejections": dict(stats["rejections"]),
                }
                for symbol, stats in self._symbol_stats.items()
            },
            "last_by_symbol": {
                symbol: diag.to_dict()
                for symbol, diag in self._last_diagnostic_by_symbol.items()
            },
        }

    def get_summary(self) -> dict[str, Any]:
        """
        Retourne un résumé léger.

        Returns:
            Dictionnaire résumé.
        """
        return {
            "total_opportunities": self._opportunities_seen,
            "opportunities_seen": self._opportunities_seen,
            "opportunities_rejected": self._opportunities_rejected,
            "opportunities_approved": self._opportunities_approved,
            "candles_analyzed": self._candles_analyzed,
            "setups_detected": self._setups_detected,
            "buy_candidates": self._buy_candidates,
            "sell_candidates": self._sell_candidates,
            "signals_generated": self._signals_generated,
            "orders_submitted": self._orders_submitted,
            "orders_executed": self._orders_executed,
            "total_setups_detected": self._setups_detected,
            "total_signals_generated": self._signals_generated,
            "total_orders_submitted": self._orders_submitted,
            "total_orders_executed": self._orders_executed,
            "top_rejections": dict(
                sorted(self._rejection_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            ),
            "by_symbol": {
                symbol: {
                    "opportunities": stats["opportunities_seen"],
                    "opportunities_seen": stats["opportunities_seen"],
                    "opportunities_rejected": stats["opportunities_rejected"],
                    "opportunities_approved": stats["opportunities_approved"],
                    "candles_analyzed": stats["candles_analyzed"],
                    "setups_detected": stats["setups_detected"],
                    "buy_candidates": stats["buy_candidates"],
                    "sell_candidates": stats["sell_candidates"],
                    "signals_generated": stats["signals_generated"],
                    "orders_submitted": stats["orders_submitted"],
                    "orders_executed": stats["orders_executed"],
                    "rejections": dict(stats["rejections"]),
                }
                for symbol, stats in self._symbol_stats.items()
            },
        }

    def reset(self) -> None:
        """Réinitialise toutes les statistiques."""
        self._opportunity_counter = 0
        self._diagnostics.clear()
        self._last_diagnostic_by_symbol.clear()
        self._active_diagnostic_by_symbol.clear()
        self._finalized.clear()
        self._opportunities_seen = 0
        self._opportunities_rejected = 0
        self._opportunities_approved = 0
        self._candles_analyzed = 0
        self._setups_detected = 0
        self._buy_candidates = 0
        self._sell_candidates = 0
        self._signals_generated = 0
        self._orders_submitted = 0
        self._orders_executed = 0
        self._step_counts.clear()
        self._step_fail_counts.clear()
        self._rejection_counts.clear()
        self._seen_candles.clear()
        self._seen_setup_ids.clear()
        self._direction_counted.clear()
        self._symbol_stats.clear()

    def _log_periodic_summary(self) -> None:
        """Log un résumé périodique des statistiques."""
        summary = self.get_summary()

        logger.info("=" * 56)
        logger.info("ARTY DECISION DIAGNOSTICS")
        logger.info("=" * 56)
        logger.info(
            "Seen: %d | Rejected: %d | Approved: %d | Candles: %d",
            summary["opportunities_seen"],
            summary["opportunities_rejected"],
            summary["opportunities_approved"],
            summary["candles_analyzed"],
        )
        logger.info(
            "Setups: %d | BUY: %d | SELL: %d | Signals: %d | "
            "Submitted: %d | Executed: %d",
            summary["setups_detected"],
            summary["buy_candidates"],
            summary["sell_candidates"],
            summary["signals_generated"],
            summary["orders_submitted"],
            summary["orders_executed"],
        )

        if summary["top_rejections"]:
            logger.info("Top rejections:")
            for reason, count in summary["top_rejections"].items():
                logger.info("  %s: %d", reason, count)

        for symbol, stats in summary.get("by_symbol", {}).items():
            logger.info(
                "%s | seen=%d | rejected=%d | approved=%d | candles=%d | "
                "setups=%d | buy=%d | sell=%d | signals=%d | "
                "submitted=%d | executed=%d",
                symbol,
                stats["opportunities_seen"],
                stats["opportunities_rejected"],
                stats["opportunities_approved"],
                stats["candles_analyzed"],
                stats["setups_detected"],
                stats["buy_candidates"],
                stats["sell_candidates"],
                stats["signals_generated"],
                stats["orders_submitted"],
                stats["orders_executed"],
            )
            if stats["rejections"]:
                for reason, count in stats["rejections"].items():
                    logger.info("  %s: %d", reason, count)

        logger.info("=" * 56)
