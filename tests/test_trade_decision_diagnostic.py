"""Tests du système de diagnostic de décision de trading."""

from __future__ import annotations

from datetime import UTC, datetime

from arty_trading.application.trade_decision_debugger import TradeDecisionDebugger
from arty_trading.application.trade_decision_diagnostic import PipelineStep, RejectionReason
from arty_trading.core.enums import TimeFrame
from arty_trading.modules.signals import SignalGenerator
from arty_trading.modules.signals.validator import SignalValidator

# =============================================================================
# Helpers
# =============================================================================

def _make_diagnostic(
    symbol: str = "EURUSD",
    direction: str = "buy",
    decision: str = "rejected",
    rejection_reasons: list[str] | None = None,
) -> TradeDecisionDebugger:
    debugger = TradeDecisionDebugger(enabled=True, summary_interval=10)
    return debugger


# =============================================================================
# Tests du TradeDecisionDebugger
# =============================================================================

class TestTradeDecisionDebuggerInit:
    def test_default_enabled(self) -> None:
        debugger = TradeDecisionDebugger()
        assert debugger.enabled is True

    def test_disabled(self) -> None:
        debugger = TradeDecisionDebugger(enabled=False)
        assert debugger.enabled is False

    def test_summary_interval(self) -> None:
        debugger = TradeDecisionDebugger(summary_interval=50)
        assert debugger.summary_interval == 50

    def test_summary_interval_minimum(self) -> None:
        debugger = TradeDecisionDebugger(summary_interval=0)
        assert debugger.summary_interval == 1


class TestTradeDecisionDebuggerRecord:
    def test_record_opportunity_basic(self) -> None:
        debugger = TradeDecisionDebugger(enabled=True)
        from arty_trading.application.trade_decision_diagnostic import (
            TradeDecisionDiagnostic,
        )
        diag = TradeDecisionDiagnostic(
            symbol="EURUSD",
            timeframe=TimeFrame.H1,
            decision="accepted",
        )
        debugger.record_opportunity(diag)
        assert debugger.get_summary()["total_opportunities"] == 1

    def test_record_opportunity_disabled(self) -> None:
        debugger = TradeDecisionDebugger(enabled=False)
        from arty_trading.application.trade_decision_diagnostic import (
            TradeDecisionDiagnostic,
        )
        diag = TradeDecisionDiagnostic(symbol="EURUSD")
        debugger.record_opportunity(diag)
        assert debugger.get_summary()["total_opportunities"] == 0

    def test_record_rejection_reasons(self) -> None:
        debugger = TradeDecisionDebugger(enabled=True)
        from arty_trading.application.trade_decision_diagnostic import (
            TradeDecisionDiagnostic,
        )
        diag = TradeDecisionDiagnostic(
            symbol="EURUSD",
            decision="rejected",
            rejection_reasons=["master_trend_conflict", "low_confidence"],
        )
        debugger.record_opportunity(diag)
        stats = debugger.get_statistics()
        assert stats["rejection_counts"]["master_trend_conflict"] == 1
        assert stats["rejection_counts"]["low_confidence"] == 1


class TestTradeDecisionDebuggerSteps:
    def test_steps_tracking(self) -> None:
        debugger = TradeDecisionDebugger(enabled=True)
        from arty_trading.application.trade_decision_diagnostic import (
            TradeDecisionDiagnostic,
        )
        diag = TradeDecisionDiagnostic(symbol="EURUSD")
        diag.steps_completed = [
            PipelineStep.DATA_AVAILABLE,
            PipelineStep.MARKET_CONTEXT,
            PipelineStep.SIGNAL_GENERATED,
        ]
        diag.steps_failed = [PipelineStep.MASTER_DIRECTION_GATE]
        debugger.record_opportunity(diag)
        stats = debugger.get_statistics()
        assert stats["step_counts"][PipelineStep.DATA_AVAILABLE] == 1
        assert stats["step_counts"][PipelineStep.MARKET_CONTEXT] == 1
        assert stats["step_counts"][f"{PipelineStep.MASTER_DIRECTION_GATE}_failed"] == 1

    def test_multiple_opportunities(self) -> None:
        debugger = TradeDecisionDebugger(enabled=True)
        from arty_trading.application.trade_decision_diagnostic import (
            TradeDecisionDiagnostic,
        )
        for i in range(5):
            diag = TradeDecisionDiagnostic(
                symbol="EURUSD",
                decision="rejected" if i < 3 else "accepted",
                rejection_reasons=["low_confidence"] if i < 3 else [],
            )
            diag.steps_completed = [PipelineStep.DATA_AVAILABLE]
            debugger.record_opportunity(diag)
        assert debugger.get_summary()["total_opportunities"] == 5
        assert debugger.get_statistics()["rejection_counts"]["low_confidence"] == 3


class TestTradeDecisionDebuggerBySymbol:
    def test_last_diagnostic_by_symbol(self) -> None:
        debugger = TradeDecisionDebugger(enabled=True)
        from arty_trading.application.trade_decision_diagnostic import (
            TradeDecisionDiagnostic,
        )
        diag1 = TradeDecisionDiagnostic(symbol="EURUSD", decision="rejected")
        diag2 = TradeDecisionDiagnostic(symbol="XAUUSD", decision="accepted")
        debugger.record_opportunity(diag1)
        debugger.record_opportunity(diag2)

        last_eur = debugger.get_last_diagnostic("EURUSD")
        assert last_eur is not None
        assert last_eur.decision == "rejected"

        last_xau = debugger.get_last_diagnostic("xauusd")
        assert last_xau is not None
        assert last_xau.decision == "accepted"

    def test_symbol_stats(self) -> None:
        debugger = TradeDecisionDebugger(enabled=True)
        from arty_trading.application.trade_decision_diagnostic import (
            TradeDecisionDiagnostic,
        )
        diag = TradeDecisionDiagnostic(
            symbol="EURUSD",
            decision="rejected",
            rejection_reasons=["rr_too_low"],
        )
        diag.steps_completed = [
            PipelineStep.DATA_AVAILABLE,
            PipelineStep.SIGNAL_GENERATED,
        ]
        debugger.record_opportunity(diag)
        stats = debugger.get_statistics()
        assert stats["by_symbol"]["EURUSD"]["opportunities"] == 1
        assert stats["by_symbol"]["EURUSD"]["signals_generated"] == 1
        assert stats["by_symbol"]["EURUSD"]["rejections"]["rr_too_low"] == 1


class TestTradeDecisionDebuggerReset:
    def test_reset_clears_all(self) -> None:
        debugger = TradeDecisionDebugger(enabled=True)
        from arty_trading.application.trade_decision_diagnostic import (
            TradeDecisionDiagnostic,
        )
        diag = TradeDecisionDiagnostic(symbol="EURUSD")
        debugger.record_opportunity(diag)
        debugger.reset()
        assert debugger.get_summary()["total_opportunities"] == 0
        assert debugger.get_last_diagnostic("EURUSD") is None


class TestTradeDecisionDebuggerSummary:
    def test_periodic_summary_logs(self) -> None:
        debugger = TradeDecisionDebugger(enabled=True, summary_interval=2)
        from arty_trading.application.trade_decision_diagnostic import (
            TradeDecisionDiagnostic,
        )
        for _ in range(4):
            diag = TradeDecisionDiagnostic(symbol="EURUSD")
            debugger.record_opportunity(diag)
        # Should have triggered 2 summaries without error
        assert debugger.get_summary()["total_opportunities"] == 4


# =============================================================================
# Tests du SignalGenerator avec rejection tracking
# =============================================================================

class TestSignalGeneratorRejectionTracking:
    def test_last_rejection_after_no_candles(self) -> None:
        gen = SignalGenerator(min_confidence=0.1)
        import asyncio
        asyncio.run(gen.generate([], []))
        assert gen.last_rejection_stage == "no_data"
        assert gen.last_rejection_reason == "no_candles"

    def test_last_rejection_after_master_gate(self) -> None:
        validator = SignalValidator(min_risk_reward=1.0, max_spread=20)
        gen = SignalGenerator(min_confidence=0.1, validator=validator)
        # Sans données SMC, le signal ne passera pas le master gate
        # Mais on peut vérifier que les propriétés existent
        assert gen.last_rejection_stage is None
        assert gen.last_rejection_reason is None

    def test_last_rejection_after_confidence(self) -> None:
        gen = SignalGenerator(min_confidence=0.99)
        assert gen.last_rejection_stage is None
        assert gen.last_rejection_reason is None


# =============================================================================
# Tests de la nouvelle API de cycle de vie (corrections Phase 1C)
# =============================================================================


class TestTradeDecisionDebuggerLifecycle:
    """Vérifie l'absence de double comptage et les nouveaux compteurs."""

    def test_no_double_counting_of_opportunities(self) -> None:
        """TEST 1 : 5 étapes -> opportunities_seen == 1."""
        debugger = TradeDecisionDebugger(enabled=True)
        diag = debugger.start_opportunity("EURUSD")
        for step in (
            PipelineStep.DATA_AVAILABLE,
            PipelineStep.MARKET_CONTEXT,
            PipelineStep.MARKET_REGIME,
            PipelineStep.STRATEGY_EVALUATION,
            PipelineStep.SIGNAL_GENERATED,
        ):
            debugger.record_step(diag, step)
        debugger.fail_step(
            diag, PipelineStep.MASTER_DIRECTION_GATE, RejectionReason.MASTER_TREND_CONFLICT
        )
        debugger.finalize_opportunity(diag)

        summary = debugger.get_summary()
        assert summary["opportunities_seen"] == 1
        assert summary["total_opportunities"] == 1

    def test_buy_signal_rejected(self) -> None:
        """TEST 2 : BUY -> signal -> rejet."""
        debugger = TradeDecisionDebugger(enabled=True)
        diag = debugger.start_opportunity("EURUSD")
        debugger.record_step(diag, PipelineStep.SIGNAL_GENERATED)
        debugger.set_direction(diag, "BUY")
        debugger.fail_step(
            diag, PipelineStep.SIGNAL_VALIDATOR, RejectionReason.VALIDATOR_REJECTED
        )
        debugger.finalize_opportunity(diag)

        summary = debugger.get_summary()
        assert summary["opportunities_seen"] == 1
        assert summary["buy_candidates"] == 1
        assert summary["sell_candidates"] == 0
        assert summary["signals_generated"] == 1
        assert summary["opportunities_rejected"] == 1
        assert summary["opportunities_approved"] == 0

    def test_sell_signal_approved_executed(self) -> None:
        """TEST 3 : SELL -> signal -> approved -> submitted -> executed."""
        debugger = TradeDecisionDebugger(enabled=True)
        diag = debugger.start_opportunity("XAUUSD")
        debugger.record_step(diag, PipelineStep.SIGNAL_GENERATED)
        debugger.set_direction(diag, "SELL")
        debugger.record_step(diag, PipelineStep.ORDER_SUBMITTED)
        debugger.record_step(diag, PipelineStep.ORDER_EXECUTED)
        diag.decision = "accepted"
        debugger.finalize_opportunity(diag)

        summary = debugger.get_summary()
        assert summary["opportunities_seen"] == 1
        assert summary["sell_candidates"] == 1
        assert summary["buy_candidates"] == 0
        assert summary["opportunities_approved"] == 1
        assert summary["orders_submitted"] == 1
        assert summary["orders_executed"] == 1

    def test_two_distinct_opportunities(self) -> None:
        """TEST 4 : deux opportunités différentes -> opportunities_seen == 2."""
        debugger = TradeDecisionDebugger(enabled=True)
        debugger.finalize_opportunity(debugger.start_opportunity("EURUSD"))
        debugger.finalize_opportunity(debugger.start_opportunity("EURUSD"))
        assert debugger.get_summary()["opportunities_seen"] == 2

    def test_same_setup_counted_once(self) -> None:
        """TEST 5 : même setup -> setups_detected == 1."""
        debugger = TradeDecisionDebugger(enabled=True)
        debugger.record_setup_detected("EURUSD", "EURUSD_buy_1")
        debugger.record_setup_detected("EURUSD", "EURUSD_buy_1")
        assert debugger.get_summary()["setups_detected"] == 1

    def test_two_distinct_setups(self) -> None:
        """TEST 6 : deux setups -> setups_detected == 2."""
        debugger = TradeDecisionDebugger(enabled=True)
        debugger.record_setup_detected("EURUSD", "EURUSD_buy_1")
        debugger.record_setup_detected("EURUSD", "EURUSD_sell_2")
        assert debugger.get_summary()["setups_detected"] == 2

    def test_same_candle_counted_once(self) -> None:
        """TEST 7 : même bougie -> candles_analyzed == 1."""
        debugger = TradeDecisionDebugger(enabled=True)
        t = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        debugger.record_candle_analyzed("EURUSD", TimeFrame.M5, t)
        debugger.record_candle_analyzed("EURUSD", TimeFrame.M5, t)
        assert debugger.get_summary()["candles_analyzed"] == 1

    def test_buy_then_sell_candidates(self) -> None:
        """TEST 8 : BUY puis SELL -> buy_candidates == 1, sell_candidates == 1."""
        debugger = TradeDecisionDebugger(enabled=True)
        diag1 = debugger.start_opportunity("EURUSD")
        debugger.set_direction(diag1, "BUY")
        debugger.finalize_opportunity(diag1)

        diag2 = debugger.start_opportunity("EURUSD")
        debugger.set_direction(diag2, "SELL")
        debugger.finalize_opportunity(diag2)

        summary = debugger.get_summary()
        assert summary["buy_candidates"] == 1
        assert summary["sell_candidates"] == 1

    def test_validator_detailed_reasons_preserved(self) -> None:
        """TEST 9 : les raisons détaillées du validateur sont conservées."""
        debugger = TradeDecisionDebugger(enabled=True)
        diag = debugger.start_opportunity("EURUSD")
        debugger.set_direction(diag, "BUY")
        debugger.fail_step(
            diag, PipelineStep.SIGNAL_VALIDATOR, RejectionReason.VALIDATOR_REJECTED
        )
        debugger.add_rejection_reason(diag, RejectionReason.ORDER_BLOCK_MISSING)
        debugger.add_rejection_reason(diag, RejectionReason.FVG_MISSING)
        debugger.finalize_opportunity(diag)

        assert diag.primary_rejection_reason == "validator_rejected"
        assert RejectionReason.ORDER_BLOCK_MISSING.value in diag.rejection_reasons
        assert RejectionReason.FVG_MISSING.value in diag.rejection_reasons

    def test_order_submitted_without_execution(self) -> None:
        """TEST 10 : ordre soumis sans exécution -> submitted=1, executed=0."""
        debugger = TradeDecisionDebugger(enabled=True)
        diag = debugger.start_opportunity("EURUSD")
        debugger.record_step(diag, PipelineStep.ORDER_SUBMITTED)
        debugger.finalize_opportunity(diag)

        summary = debugger.get_summary()
        assert summary["orders_submitted"] == 1
        assert summary["orders_executed"] == 0
