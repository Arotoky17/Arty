"""
Stratégies de trading — 6 stratégies implémentant IStrategy.

1. SMCTrendStrategy    — SMC Trend Following (BOS + FVG + OB)
2. BreakoutStrategy   — Breakout (cassure de range + volume)
3. MomentumStrategy   — Momentum (déplacement fort + FVG)
4. ReversalStrategy    — Reversal (CHoCH + Liquidity Sweep)
5. ScalpingStrategy   — Scalping (FVG + spread serré)
6. SwingStrategy      — Swing Trading (BOS + OTE + OB)
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from arty_trading.config.settings import OBQualitySettings
from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, SignalType, TimeFrame
from arty_trading.modules.smc.confirmation import M5ConfirmationChecker
from arty_trading.modules.smc.order_block_quality import (
    OBGrade,
    OrderBlockQuality,
    OrderBlockQualityScorer,
)
from arty_trading.modules.smc.order_block_tracker import OrderBlockTracker, TrackedOB
from arty_trading.modules.strategies.base import BaseStrategy

# =============================================================================
# 1. SMC Trend Following
# =============================================================================


class SMCTrendStrategy(BaseStrategy):
    """
    Stratégie SMC Trend Following.

    Cherche une tendance confirmée par BOS, un FVG dans le sens de la tendance
    et un Order Block non mitigé comme point d'entrée.
    """

    def __init__(
        self,
        enabled: bool = True,
        risk_reward_min: float = 1.5,
        confidence_min: float = 0.3,
        *,
        ob_scorer: OrderBlockQualityScorer | None = None,
        ob_tracker: OrderBlockTracker | None = None,
        m5_confirmation: M5ConfirmationChecker | None = None,
        ob_config: OBQualitySettings | None = None,
        use_ob_quality_filter: bool = True,
    ) -> None:
        super().__init__(enabled, risk_reward_min, confidence_min)
        self.ob_scorer = ob_scorer
        self.ob_tracker = ob_tracker
        self.m5_confirmation = m5_confirmation
        self.ob_config = ob_config
        self.use_ob_quality_filter = use_ob_quality_filter
        self.last_ob_rejection: dict[str, Any] | None = None
        self._last_tracker_time: datetime | None = None

    @property
    def ob_quality_filter_configured(self) -> bool:
        """Indique que tous les composants du nouveau filtre sont injectés."""
        return bool(
            self.use_ob_quality_filter
            and self.ob_config is not None
            and self.ob_config.use_ob_quality_filter
            and self.ob_scorer is not None
            and self.ob_tracker is not None
            and self.m5_confirmation is not None
        )

    @property
    def name(self) -> str:
        return "SMC Trend Following"

    async def analyze(
        self,
        candles: list[Candle],
        smc_data: list[dict],
        htf_smc_data: list[dict] | None = None,
        htf_trend: str | None = None,
        market_context: Any | None = None,
    ) -> Signal | None:
        self.last_ob_rejection = None
        if not self._enabled or len(candles) < 10:
            return None

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        current_price = candles[-1].close

        # Sens primaire : la direction du BOS le plus récent (bullish OU bearish).
        # Le sens primaire est dicté par le BOS le plus récent. Si ce sens
        # est bloqué par l'alignement HTF, aucun signal n'est généré par
        # cette stratégie (elle est purement trend-following).
        # Pour du contre-trend, utiliser ReversalStrategy.
        bullish_bos = self._filter_smc(smc_data, "break_of_structure", "bullish")
        bearish_bos = self._filter_smc(smc_data, "break_of_structure", "bearish")

        latest_bull = max(bullish_bos, key=lambda d: d.get("index", -1), default=None)
        latest_bear = max(bearish_bos, key=lambda d: d.get("index", -1), default=None)

        candidates: list[Signal] = []

        def _try_direction(direction: Direction) -> None:
            if not self._is_htf_aligned(direction, htf_trend, htf_smc_data):
                return
            quality: OrderBlockQuality | None = None
            selected_ob: TrackedOB | None = None
            rr_min: float | None = None
            if self.ob_quality_filter_configured:
                qualified = self._select_qualified_ob(
                    candles,
                    smc_data,
                    htf_smc_data or [],
                    direction,
                    market_context,
                )
                if qualified is None:
                    return
                selected_ob, quality = qualified
                assert self.ob_config is not None
                rr_min = (
                    self.ob_config.rr_min_grade_a
                    if quality.grade == OBGrade.A
                    else self.ob_config.rr_min_grade_b
                )
            sig = self._build_trend_signal(
                symbol,
                timeframe,
                current_price,
                smc_data,
                candles,
                direction,
                quality=quality,
                rr_min=rr_min,
                ob=selected_ob,
            )
            if sig is not None:
                if selected_ob is not None and self.ob_tracker is not None:
                    self.ob_tracker.mark_as_used(selected_ob.ob_id, str(sig.id))
                candidates.append(sig)
            elif quality is not None and selected_ob is not None:
                self.last_ob_rejection = {
                    "reason": "signal_construction_failed",
                    "ob_id": selected_ob.ob_id,
                    "grade": quality.grade.value,
                    "score": quality.score,
                }

        if latest_bull and (
            not latest_bear or latest_bull["index"] > latest_bear["index"]
        ):
            _try_direction(Direction.BUY)
        elif latest_bear:
            _try_direction(Direction.SELL)

        if not candidates:
            return None
        return max(candidates, key=lambda signal: signal.confidence)

    def _build_trend_signal(
        self,
        symbol: str,
        timeframe: TimeFrame,
        current_price: Decimal,
        smc_data: list[dict],
        candles: list[Candle],
        direction: Direction,
        *,
        quality: OrderBlockQuality | None = None,
        rr_min: float | None = None,
        ob: TrackedOB | None = None,
    ) -> Signal | None:
        """Construit un signal SMC pour la direction et l'OB qualifié donnés."""
        dir_tag = "bullish" if direction == Direction.BUY else "bearish"
        signal_type = SignalType.BUY if direction == Direction.BUY else SignalType.SELL

        confirmed_rejection = False
        for concept in ("fair_value_gap", "order_block"):
            items = self._filter_smc(smc_data, concept, dir_tag)
            if not items:
                continue
            latest = max(items, key=lambda detection: detection.get("index", -1))
            details = latest.get("details", {})
            if concept == "fair_value_gap":
                zone_top = details.get("gap_top")
                zone_bottom = details.get("gap_bottom")
            else:
                zone_top = details.get("ob_top")
                zone_bottom = details.get("ob_bottom")
            if zone_top is not None and zone_bottom is not None and self._has_confirmed_rejection(
                candles, float(zone_top), float(zone_bottom), dir_tag
            ):
                confirmed_rejection = True
                break

        if not confirmed_rejection:
            return None

        confluences = 0
        concepts: list[str] = []
        if self._has_concept(smc_data, "fair_value_gap", dir_tag):
            confluences += 2
            concepts.append(f"FVG {dir_tag}")
        if self._has_concept(smc_data, "order_block", dir_tag):
            confluences += 2
            concepts.append(f"Order Block {dir_tag}")
        if self._has_concept(smc_data, "optimal_trade_entry", dir_tag):
            confluences += 1
            concepts.append(f"OTE {dir_tag}")
        if self._has_concept(smc_data, "liquidity_sweep", dir_tag):
            confluences += 1
            concepts.append(f"Liquidity Sweep {dir_tag}")
        confluences += 1
        concepts.append(f"BOS {dir_tag}")

        confidence = self._calculate_confidence(confluences, 7)
        if confidence < self._confidence_min:
            return None

        stop_loss, take_profit = self._calculate_atr_based_sl_tp(
            current_price, direction, candles
        )
        if rr_min is not None:
            min_reward = abs(current_price - stop_loss) * Decimal(str(rr_min))
            if abs(take_profit - current_price) < min_reward:
                take_profit = (
                    current_price + min_reward
                    if direction == Direction.BUY
                    else current_price - min_reward
                )

        trend_word = "haussière" if direction == Direction.BUY else "baissière"
        metadata = (
            {
                "ob_id": ob.ob_id if ob is not None else None,
                "ob_grade": quality.grade.value,
                "ob_score": quality.score,
                "minimum_risk_reward": rr_min,
            }
            if quality is not None
            else None
        )
        return self._build_signal(
            symbol=symbol,
            signal_type=signal_type,
            direction=direction,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            timeframe=timeframe,
            smc_concepts=concepts,
            justification=(
                f"Tendance {trend_word} confirmée par BOS (le plus récent) "
                f"avec {confluences} confluences"
            ),
            metadata=metadata,
        )

    def _select_qualified_ob(
        self,
        candles: list[Candle],
        smc_data: list[dict[str, Any]],
        htf_smc_data: list[dict[str, Any]],
        direction: Direction,
        market_context: Any | None,
    ) -> tuple[TrackedOB, OrderBlockQuality] | None:
        """Note les OB frais dans le sens du signal et exige leur confirmation M5."""
        assert self.ob_config is not None
        assert self.ob_scorer is not None
        assert self.ob_tracker is not None
        assert self.m5_confirmation is not None

        expected_direction = "bullish" if direction == Direction.BUY else "bearish"
        raw_obs = [
            detection
            for detection in smc_data
            if detection.get("concept") == "order_block"
            and detection.get("direction") == expected_direction
        ]
        self._advance_tracker_before_current_candle(candles)
        tracked_indices: dict[str, int] = {}
        rejected: list[dict[str, Any]] = []
        for detection in raw_obs:
            tracked_data = self._to_tracked_ob(detection, candles)
            if tracked_data is None:
                rejected.append(
                    {
                        "index": detection.get("index"),
                        "reason": "invalid_ob_zone_or_candle",
                        "grade": None,
                        "score": None,
                    }
                )
                continue
            tracked, index = tracked_data
            if not self.ob_tracker.is_registered(tracked):
                history = self._candles_frame(candles[index + 1 : -1])
                self.ob_tracker.register_with_history(tracked, history)
            tracked_indices[tracked.ob_id] = index

        fresh_obs = [
            tracked
            for tracked in self.ob_tracker.get_fresh(exclude_used=True)
            if tracked.symbol == candles[0].symbol
            and tracked.direction == expected_direction
        ]
        if not fresh_obs:
            self.last_ob_rejection = {
                "reason": "no_fresh_ob",
                "rejected_obs": rejected,
                "total_tracked": len(self.ob_tracker.get_all()),
            }
            self._finish_tracker_update(candles)
            return None

        atr_value = float(self._calculate_atr(candles))
        htf_obs = [d for d in htf_smc_data if d.get("concept") == "order_block"]
        htf_fvgs = [d for d in htf_smc_data if d.get("concept") == "fair_value_gap"]
        liquidity_sweeps = [d for d in smc_data if d.get("concept") == "liquidity_sweep"]
        fvgs_m5 = [d for d in smc_data if d.get("concept") == "fair_value_gap"]
        qualified: list[tuple[TrackedOB, OrderBlockQuality, int]] = []

        for tracked in fresh_obs:
            index = tracked_indices.get(tracked.ob_id)
            if index is None:
                index = next(
                    (
                        offset
                        for offset, candle in enumerate(candles)
                        if pd.Timestamp(candle.time) == pd.Timestamp(tracked.created_at)
                    ),
                    None,
                )
            if index is None:
                rejected.append(
                    {
                        "ob_id": tracked.ob_id,
                        "reason": "ob_candle_unavailable",
                        "grade": tracked.grade,
                        "score": None,
                    }
                )
                continue
            score_candles = self._candles_frame(candles[index + 1 : -1])
            try:
                quality = self.ob_scorer.score(
                    ob_candle=self._candle_series(candles[index]),
                    next_candles=score_candles,
                    atr_value=atr_value,
                    htf_obs=htf_obs,
                    htf_fvgs=htf_fvgs,
                    liquidity_sweeps=liquidity_sweeps,
                    fvgs_m5=fvgs_m5,
                )
            except Exception as exc:
                rejected.append(
                    {
                        "ob_id": tracked.ob_id,
                        "reason": "ob_scoring_failed",
                        "grade": tracked.grade,
                        "score": None,
                        "error": str(exc),
                    }
                )
                continue
            self.ob_tracker.set_grade(tracked.ob_id, quality.grade.value)

            grade_ok = quality.grade in (OBGrade.A, OBGrade.B)
            score_ok = quality.score >= self.ob_config.min_score
            fresh_ok = not self.ob_config.require_fresh or quality.is_fresh
            htf_ok = (
                not self.ob_config.require_htf_confluence or quality.htf_confluence
            )
            sweep_ok = (
                not self.ob_config.require_liquidity_sweep
                or quality.has_liquidity_sweep
            )
            if grade_ok and score_ok and fresh_ok and htf_ok and sweep_ok:
                qualified.append((tracked, quality, index))
            else:
                rejected.append(
                    {
                        "ob_id": tracked.ob_id,
                        "reason": self._diagnose_ob_rejection(quality),
                        "grade": quality.grade.value,
                        "score": quality.score,
                    }
                )

        if not qualified:
            self.last_ob_rejection = {
                "reason": "no_qualified_ob",
                "rejected_obs": rejected,
                "fresh_count": len(fresh_obs),
            }
            self._finish_tracker_update(candles)
            return None

        best_ob, best_quality, best_index = max(qualified, key=lambda item: item[1].score)
        if rejected:
            self.last_ob_rejection = {
                "reason": "some_obs_rejected",
                "rejected_obs": rejected,
                "selected_ob_id": best_ob.ob_id,
                "selected_grade": best_quality.grade.value,
                "selected_score": best_quality.score,
            }
        if self.ob_config.require_m5_confirmation:
            m5_structure = getattr(market_context, "ltf_smc_data", None) or smc_data
            confirmation = self.m5_confirmation.check(
                best_ob,
                self._candles_frame(candles[best_index + 1 :]),
                {"events": m5_structure},
            )
            if not confirmation.confirmed:
                self.last_ob_rejection = {
                    "reason": "no_m5_confirmation",
                    "ob_id": best_ob.ob_id,
                    "grade": best_quality.grade.value,
                    "score": best_quality.score,
                    "details": {
                        "confirmation_type": (
                            confirmation.type.value if confirmation.type is not None else None
                        ),
                        "confirmation": confirmation.details,
                    },
                }
                self._finish_tracker_update(candles)
                return None

        self._finish_tracker_update(candles)
        return best_ob, best_quality

    def _diagnose_ob_rejection(self, quality: OrderBlockQuality) -> str:
        """Retourne le premier critère qualité qui a rejeté l'OB."""
        assert self.ob_config is not None
        if quality.grade not in (OBGrade.A, OBGrade.B):
            return "grade_c"
        if quality.score < self.ob_config.min_score:
            return "score_below_min"
        if self.ob_config.require_fresh and not quality.is_fresh:
            return "not_fresh"
        if self.ob_config.require_htf_confluence and not quality.htf_confluence:
            return "no_htf_confluence"
        if self.ob_config.require_liquidity_sweep and not quality.has_liquidity_sweep:
            return "no_liquidity_sweep"
        return "unknown_rejection"
        liquidity_sweeps = [d for d in smc_data if d.get("concept") == "liquidity_sweep"]
        fvgs_m5 = [d for d in smc_data if d.get("concept") == "fair_value_gap"]
        qualified: list[tuple[TrackedOB, OrderBlockQuality, int]] = []

        for tracked in fresh_obs:
            index = tracked_indices.get(tracked.ob_id)
            if index is None:
                index = next(
                    (
                        offset
                        for offset, candle in enumerate(candles)
                        if pd.Timestamp(candle.time) == pd.Timestamp(tracked.created_at)
                    ),
                    None,
                )
            if index is None:
                rejected.append(
                    {
                        "ob_id": tracked.ob_id,
                        "reason": "ob_candle_unavailable",
                        "grade": tracked.grade,
                        "score": None,
                    }
                )
                continue
            score_candles = self._candles_frame(candles[index + 1 : -1])
            try:
                quality = self.ob_scorer.score(
                    ob_candle=self._candle_series(candles[index]),
                    next_candles=score_candles,
                    atr_value=atr_value,
                    htf_obs=htf_obs,
                    htf_fvgs=htf_fvgs,
                    liquidity_sweeps=liquidity_sweeps,
                    fvgs_m5=fvgs_m5,
                )
            except Exception as exc:
                rejected.append(
                    {
                        "ob_id": tracked.ob_id,
                        "reason": "ob_scoring_failed",
                        "grade": tracked.grade,
                        "score": None,
                        "error": str(exc),
                    }
                )
                continue
            self.ob_tracker.set_grade(tracked.ob_id, quality.grade.value)

            grade_ok = quality.grade in (OBGrade.A, OBGrade.B)
            score_ok = quality.score >= self.ob_config.min_score
            fresh_ok = not self.ob_config.require_fresh or quality.is_fresh
            htf_ok = (
                not self.ob_config.require_htf_confluence or quality.htf_confluence
            )
            sweep_ok = (
                not self.ob_config.require_liquidity_sweep
                or quality.has_liquidity_sweep
            )
            if grade_ok and score_ok and fresh_ok and htf_ok and sweep_ok:
                qualified.append((tracked, quality, index))
            else:
                rejected.append(
                    {
                        "ob_id": tracked.ob_id,
                        "reason": self._diagnose_ob_rejection(quality),
                        "grade": quality.grade.value,
                        "score": quality.score,
                    }
                )

        if not qualified:
            self.last_ob_rejection = {
                "reason": "no_qualified_ob",
                "rejected_obs": rejected,
                "fresh_count": len(fresh_obs),
            }
            self._finish_tracker_update(candles)
            return None

        best_ob, best_quality, best_index = max(qualified, key=lambda item: item[1].score)
        if self.ob_config.require_m5_confirmation:
            m5_structure = getattr(market_context, "ltf_smc_data", None) or smc_data
            confirmation = self.m5_confirmation.check(
                best_ob,
                self._candles_frame(candles[best_index + 1 :]),
                {"events": m5_structure},
            )
            if not confirmation.confirmed:
                self.last_ob_rejection = {
                    "reason": "no_m5_confirmation",
                    "ob_id": best_ob.ob_id,
                    "grade": best_quality.grade.value,
                    "score": best_quality.score,
                    "details": {
                        "confirmation_type": (
                            confirmation.type.value if confirmation.type is not None else None
                        ),
                        "confirmation": confirmation.details,
                    },
                }
                self._finish_tracker_update(candles)
                return None

        self._finish_tracker_update(candles)
        return best_ob, best_quality

    def _diagnose_ob_rejection(self, quality: OrderBlockQuality) -> str:
        """Retourne le premier critère qualité qui a rejeté l'OB."""
        assert self.ob_config is not None
        if quality.grade not in (OBGrade.A, OBGrade.B):
            return "grade_c"
        if quality.score < self.ob_config.min_score:
            return "score_below_min"
        if self.ob_config.require_fresh and not quality.is_fresh:
            return "not_fresh"
        if self.ob_config.require_htf_confluence and not quality.htf_confluence:
            return "no_htf_confluence"
        if self.ob_config.require_liquidity_sweep and not quality.has_liquidity_sweep:
            return "no_liquidity_sweep"
        return "unknown_rejection"

    @staticmethod
    def _to_tracked_ob(
        detection: dict[str, Any], candles: list[Candle]
    ) -> tuple[TrackedOB, int] | None:
        """Construit un identifiant stable de tracker depuis une détection SMC."""
        try:
            index = int(detection.get("index", -1))
            details = detection.get("details", {})
            high = float(details["ob_top"])
            low = float(details["ob_bottom"])
        except (KeyError, TypeError, ValueError):
            return None
        if index < 0 or index >= len(candles) or high < low:
            return None
        candle = candles[index]
        ob_id = f"{candle.symbol}:{candle.time.isoformat()}:{detection['direction']}:{high}:{low}"
        return (
            TrackedOB.create(
                ob_id=ob_id,
                symbol=candle.symbol,
                timeframe=candle.timeframe.value,
                direction=str(detection["direction"]),
                high=high,
                low=low,
                created_at=candle.time,
            ),
            index,
        )

    def _advance_tracker_before_current_candle(self, candles: list[Candle]) -> None:
        """Traite l'historique avant la bougie courante qui peut confirmer l'OB."""
        if self.ob_tracker is None or len(candles) < 2:
            return
        for candle in candles[:-1]:
            if self._last_tracker_time is not None and candle.time <= self._last_tracker_time:
                continue
            self.ob_tracker.update(self._candle_series(candle))
            self._last_tracker_time = candle.time

    def _finish_tracker_update(self, candles: list[Candle]) -> None:
        """Consomme la bougie courante après l'évaluation de la confirmation."""
        if not candles or self.ob_tracker is None:
            return
        current = candles[-1]
        if self._last_tracker_time is None or current.time > self._last_tracker_time:
            self.ob_tracker.update(self._candle_series(current))
            self._last_tracker_time = current.time

    @staticmethod
    def _candle_series(candle: Candle) -> pd.Series:
        return pd.Series(
            {
                "open": float(candle.open),
                "high": float(candle.high),
                "low": float(candle.low),
                "close": float(candle.close),
                "timestamp": candle.time,
            }
        )

    @classmethod
    def _candles_frame(cls, candles: list[Candle]) -> pd.DataFrame:
        return pd.DataFrame([cls._candle_series(candle) for candle in candles])


# =============================================================================
# 2. Breakout
# =============================================================================


class BreakoutStrategy(BaseStrategy):
    """
    Stratégie Breakout.

    Cherche une cassure de range avec un déplacement fort (FVG)
    et un volume élevé.
    """

    @property
    def name(self) -> str:
        return "Breakout"

    async def analyze(
        self,
        candles: list[Candle],
        smc_data: list[dict],
        htf_smc_data: list[dict] | None = None,
        htf_trend: str | None = None,
    ) -> Signal | None:
        if not self._enabled or len(candles) < 10:
            return None

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        current_price = candles[-1].close

        # Chercher un BOS + FVG (cassure avec déplacement)
        bullish_bos = self._filter_smc(smc_data, "break_of_structure", "bullish")
        bullish_fvg = self._filter_smc(smc_data, "fair_value_gap", "bullish")
        bearish_bos = self._filter_smc(smc_data, "break_of_structure", "bearish")
        bearish_fvg = self._filter_smc(smc_data, "fair_value_gap", "bearish")

        candidates: list[Signal] = []

        if bullish_bos and bullish_fvg and self._is_htf_aligned(
            Direction.BUY, htf_trend, htf_smc_data
        ):
            avg_volume = sum(c.volume for c in candles[-20:]) / min(20, len(candles))
            if candles[-1].volume > avg_volume * 1.1:
                confidence = self._calculate_confidence(5, 6)
                sl, tp = self._calculate_atr_based_sl_tp(
                    current_price, Direction.BUY, candles
                )
                candidates.append(self._build_signal(
                    symbol=symbol,
                    signal_type=SignalType.BUY,
                    direction=Direction.BUY,
                    entry_price=current_price,
                    stop_loss=sl,
                    take_profit=tp,
                    confidence=confidence,
                    timeframe=timeframe,
                    smc_concepts=["BOS bullish", "FVG bullish", "Volume élevé"],
                    justification="Cassure de range avec FVG et volume élevé",
                ))

        if bearish_bos and bearish_fvg and self._is_htf_aligned(
            Direction.SELL, htf_trend, htf_smc_data
        ):
            avg_volume = sum(c.volume for c in candles[-20:]) / min(20, len(candles))
            if candles[-1].volume > avg_volume * 1.1:
                confidence = self._calculate_confidence(5, 6)
                sl, tp = self._calculate_atr_based_sl_tp(
                    current_price, Direction.SELL, candles
                )
                candidates.append(self._build_signal(
                    symbol=symbol,
                    signal_type=SignalType.SELL,
                    direction=Direction.SELL,
                    entry_price=current_price,
                    stop_loss=sl,
                    take_profit=tp,
                    confidence=confidence,
                    timeframe=timeframe,
                    smc_concepts=["BOS bearish", "FVG bearish", "Volume élevé"],
                    justification="Cassure de range avec FVG et volume élevé",
                ))

        if not candidates:
            return None

        return max(candidates, key=lambda s: s.confidence)


# =============================================================================
# 3. Momentum
# =============================================================================


class MomentumStrategy(BaseStrategy):
    """
    Stratégie Momentum.

    Cherche un déplacement fort (FVG) avec plusieurs bougies
    consécutives dans la même direction.
    """

    @property
    def name(self) -> str:
        return "Momentum"

    async def analyze(
        self,
        candles: list[Candle],
        smc_data: list[dict],
        htf_smc_data: list[dict] | None = None,
        htf_trend: str | None = None,
    ) -> Signal | None:
        if not self._enabled or len(candles) < 10:
            return None

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        current_price = candles[-1].close

        # Vérifier 3 bougies haussières consécutives + FVG
        candidates: list[Signal] = []
        if len(candles) >= 3:
            last3 = candles[-3:]
            if all(c.is_bullish for c in last3) and self._is_htf_aligned(
                Direction.BUY, htf_trend, htf_smc_data
            ):
                if self._has_concept(smc_data, "fair_value_gap", "bullish"):
                    confidence = self._calculate_confidence(4, 5)
                    sl, tp = self._calculate_atr_based_sl_tp(
                        current_price, Direction.BUY, candles
                    )
                    candidates.append(self._build_signal(
                        symbol=symbol,
                        signal_type=SignalType.BUY,
                        direction=Direction.BUY,
                        entry_price=current_price,
                        stop_loss=sl,
                        take_profit=tp,
                        confidence=confidence,
                        timeframe=timeframe,
                        smc_concepts=["FVG bullish", "3 bougies haussières"],
                        justification="Momentum haussier avec FVG et 3 bougies vertes",
                    ))

            if all(not c.is_bullish for c in last3) and self._is_htf_aligned(
                Direction.SELL, htf_trend, htf_smc_data
            ):
                if self._has_concept(smc_data, "fair_value_gap", "bearish"):
                    confidence = self._calculate_confidence(4, 5)
                    sl, tp = self._calculate_atr_based_sl_tp(
                        current_price, Direction.SELL, candles
                    )
                    candidates.append(self._build_signal(
                        symbol=symbol,
                        signal_type=SignalType.SELL,
                        direction=Direction.SELL,
                        entry_price=current_price,
                        stop_loss=sl,
                        take_profit=tp,
                        confidence=confidence,
                        timeframe=timeframe,
                        smc_concepts=["FVG bearish", "3 bougies baissières"],
                        justification="Momentum baissier avec FVG et 3 bougies rouges",
                    ))

        if not candidates:
            return None

        return max(candidates, key=lambda s: s.confidence)


# =============================================================================
# 4. Reversal
# =============================================================================


class ReversalStrategy(BaseStrategy):
    """
    Stratégie Reversal.

    Cherche un CHoCH (retournement) confirmé par un Liquidity Sweep.
    """

    @property
    def name(self) -> str:
        return "Reversal"

    async def analyze(
        self,
        candles: list[Candle],
        smc_data: list[dict],
        htf_smc_data: list[dict] | None = None,
        htf_trend: str | None = None,
    ) -> Signal | None:
        if not self._enabled or len(candles) < 10:
            return None

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        current_price = candles[-1].close

        # CHoCH haussier + Liquidity Sweep bullish
        bullish_choch = self._filter_smc(smc_data, "change_of_character", "bullish")
        bullish_sweep = self._filter_smc(smc_data, "liquidity_sweep", "bullish")
        bearish_choch = self._filter_smc(smc_data, "change_of_character", "bearish")
        bearish_sweep = self._filter_smc(smc_data, "liquidity_sweep", "bearish")

        candidates: list[Signal] = []

        if bullish_choch and bullish_sweep:
            confluences = 3
            concepts = ["CHoCH bullish", "Liquidity Sweep bullish"]

            if self._has_concept(smc_data, "market_structure_shift", "bullish"):
                confluences += 2
                concepts.append("MSS bullish")

            if self._has_concept(smc_data, "order_block", "bullish"):
                confluences += 1
                concepts.append("OB bullish")

            confidence = self._calculate_confidence(confluences, 6)
            sl, tp = self._calculate_atr_based_sl_tp(
                current_price, Direction.BUY, candles
            )
            candidates.append(self._build_signal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                direction=Direction.BUY,
                entry_price=current_price,
                stop_loss=sl,
                take_profit=tp,
                confidence=confidence,
                timeframe=timeframe,
                smc_concepts=concepts,
                justification="Retournement haussier : CHoCH + Liquidity Sweep",
            ))

        # CHoCH baissier + Liquidity Sweep bearish
        if bearish_choch and bearish_sweep:
            confluences = 3
            concepts = ["CHoCH bearish", "Liquidity Sweep bearish"]

            if self._has_concept(smc_data, "market_structure_shift", "bearish"):
                confluences += 2
                concepts.append("MSS bearish")

            if self._has_concept(smc_data, "order_block", "bearish"):
                confluences += 1
                concepts.append("OB bearish")

            confidence = self._calculate_confidence(confluences, 6)
            sl, tp = self._calculate_atr_based_sl_tp(
                current_price, Direction.SELL, candles
            )
            candidates.append(self._build_signal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                direction=Direction.SELL,
                entry_price=current_price,
                stop_loss=sl,
                take_profit=tp,
                confidence=confidence,
                timeframe=timeframe,
                smc_concepts=concepts,
                justification="Retournement baissier : CHoCH + Liquidity Sweep",
            ))

        if not candidates:
            return None

        return max(candidates, key=lambda s: s.confidence)


# =============================================================================
# 5. Scalping
# =============================================================================


class ScalpingStrategy(BaseStrategy):
    """
    Stratégie Scalping.

    Cherche un FVG avec un spread serré et une entrée rapide.
    SL/TP serrés (10 pips / 10 pips).
    """

    @property
    def name(self) -> str:
        return "Scalping"

    async def analyze(
        self,
        candles: list[Candle],
        smc_data: list[dict],
        htf_smc_data: list[dict] | None = None,
        htf_trend: str | None = None,
    ) -> Signal | None:
        if not self._enabled or len(candles) < 5:
            return None

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        current_price = candles[-1].close

        # FVG haussier + spread acceptable (assoupli de 5 à 20)
        bullish_fvg = self._filter_smc(smc_data, "fair_value_gap", "bullish")
        bearish_fvg = self._filter_smc(smc_data, "fair_value_gap", "bearish")

        candidates: list[Signal] = []

        if bullish_fvg and candles[-1].spread <= 20 and self._is_htf_aligned(
            Direction.BUY, htf_trend, htf_smc_data
        ):
            confidence = self._calculate_confidence(3, 4)
            pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
            sl, tp = self._calculate_sl_tp(current_price, Direction.BUY, 8, 8, pip_size)
            candidates.append(self._build_signal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                direction=Direction.BUY,
                entry_price=current_price,
                stop_loss=sl,
                take_profit=tp,
                confidence=confidence,
                timeframe=timeframe,
                smc_concepts=["FVG bullish", "Spread serré"],
                justification="Scalping haussier : FVG + spread serré",
            ))

        if bearish_fvg and candles[-1].spread <= 20 and self._is_htf_aligned(
            Direction.SELL, htf_trend, htf_smc_data
        ):
            confidence = self._calculate_confidence(3, 4)
            pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
            sl, tp = self._calculate_sl_tp(current_price, Direction.SELL, 8, 8, pip_size)
            candidates.append(self._build_signal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                direction=Direction.SELL,
                entry_price=current_price,
                stop_loss=sl,
                take_profit=tp,
                confidence=confidence,
                timeframe=timeframe,
                smc_concepts=["FVG bearish", "Spread serré"],
                justification="Scalping baissier : FVG + spread serré",
            ))

        if not candidates:
            return None

        return max(candidates, key=lambda s: s.confidence)


# =============================================================================
# 6. Swing Trading
# =============================================================================


class SwingStrategy(BaseStrategy):
    """
    Stratégie Swing Trading.

    Cherche un BOS + OTE + Order Block pour une entrée
    sur retracement avec un RR élevé (3:1).
    """

    @property
    def name(self) -> str:
        return "Swing Trading"

    async def analyze(
        self,
        candles: list[Candle],
        smc_data: list[dict],
        htf_smc_data: list[dict] | None = None,
        htf_trend: str | None = None,
    ) -> Signal | None:
        if not self._enabled or len(candles) < 15:
            return None

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        current_price = candles[-1].close

        # BOS haussier + OTE + OB
        bullish_bos = self._filter_smc(smc_data, "break_of_structure", "bullish")
        bullish_ote = self._filter_smc(smc_data, "optimal_trade_entry", "bullish")
        bullish_ob = self._filter_smc(smc_data, "order_block", "bullish")
        bearish_bos = self._filter_smc(smc_data, "break_of_structure", "bearish")
        bearish_ote = self._filter_smc(smc_data, "optimal_trade_entry", "bearish")
        bearish_ob = self._filter_smc(smc_data, "order_block", "bearish")

        candidates: list[Signal] = []

        if bullish_bos and bullish_ote and self._is_htf_aligned(
            Direction.BUY, htf_trend, htf_smc_data
        ):
            confluences = 2
            concepts = ["BOS bullish", "OTE bullish"]

            if bullish_ob:
                confluences += 2
                concepts.append("OB bullish")

            if self._has_concept(smc_data, "premium_discount"):
                # Vérifier si on est en zone discount
                pd = self._filter_smc(smc_data, "premium_discount")
                if pd and pd[0].get("details", {}).get("current_zone") == "discount":
                    confluences += 1
                    concepts.append("Zone discount")

            confidence = self._calculate_confidence(confluences, 5)
            if confidence >= self._confidence_min:
                sl, tp = self._calculate_atr_based_sl_tp(
                    current_price,
                    Direction.BUY,
                    candles,
                    atr_multiplier_sl=2.0,
                    atr_multiplier_tp=6.0,
                )
                candidates.append(self._build_signal(
                    symbol=symbol,
                    signal_type=SignalType.BUY,
                    direction=Direction.BUY,
                    entry_price=current_price,
                    stop_loss=sl,
                    take_profit=tp,
                    confidence=confidence,
                    timeframe=timeframe,
                    smc_concepts=concepts,
                    justification="Swing trade haussier : BOS + OTE + zone discount",
                ))

        if bearish_bos and bearish_ote and self._is_htf_aligned(
            Direction.SELL, htf_trend, htf_smc_data
        ):
            confluences = 2
            concepts = ["BOS bearish", "OTE bearish"]

            if bearish_ob:
                confluences += 2
                concepts.append("OB bearish")

            if self._has_concept(smc_data, "premium_discount"):
                pd = self._filter_smc(smc_data, "premium_discount")
                if pd and pd[0].get("details", {}).get("current_zone") == "premium":
                    confluences += 1
                    concepts.append("Zone premium")

            confidence = self._calculate_confidence(confluences, 5)
            if confidence >= self._confidence_min:
                sl, tp = self._calculate_atr_based_sl_tp(
                    current_price,
                    Direction.SELL,
                    candles,
                    atr_multiplier_sl=2.0,
                    atr_multiplier_tp=6.0,
                )
                candidates.append(self._build_signal(
                    symbol=symbol,
                    signal_type=SignalType.SELL,
                    direction=Direction.SELL,
                    entry_price=current_price,
                    stop_loss=sl,
                    take_profit=tp,
                    confidence=confidence,
                    timeframe=timeframe,
                    smc_concepts=concepts,
                    justification="Swing trade baissier : BOS + OTE + zone premium",
                ))

        if not candidates:
            return None

        return max(candidates, key=lambda s: s.confidence)
