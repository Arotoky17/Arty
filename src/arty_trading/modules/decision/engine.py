"""Couche de décision déterministe au-dessus des détecteurs SMC.

Ce module ne place jamais d'ordre. Il transforme des détections SMC et un
signal candidat en une décision traçable, avec des filtres durs et un score
sur 100. Il peut donc être utilisé à l'identique en live et en backtest.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from arty_trading.config.settings import DecisionSettings
from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, SMCConcept
from arty_trading.modules.smc.sessions import SessionDetector


@dataclass(frozen=True)
class DecisionResult:
    """Résultat complet et sérialisable d'une décision de trading."""

    approved: bool
    score: int
    confidence: float
    tier: str
    reasons: list[str] = field(default_factory=list)
    rejected_by: list[str] = field(default_factory=list)
    atr: Decimal = Decimal("0")
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None


class DecisionEngine:
    """Applique les règles SMC/ICT à un signal candidat.

    Les poids sont plafonnés à 100. Les éléments de sécurité (news, spread,
    premium/discount et alignement HTF) restent des filtres bloquants, même
    lorsqu'un score élevé est atteint.
    """

    _WEIGHTS = {
        SMCConcept.BOS.value: 20,
        SMCConcept.CHOCH.value: 20,
        SMCConcept.MSS.value: 15,
        SMCConcept.LIQUIDITY_SWEEP.value: 20,
        SMCConcept.ORDER_BLOCK.value: 15,
        SMCConcept.BREAKER_BLOCK.value: 10,
        SMCConcept.FVG.value: 10,
        SMCConcept.IFVG.value: 5,
        SMCConcept.OTE.value: 15,
    }

    def __init__(
        self, settings: DecisionSettings, session_detector: SessionDetector | None = None
    ) -> None:
        self._settings = settings
        self._sessions = session_detector or SessionDetector()

    def decide(
        self,
        signal: Signal,
        candles: list[Candle],
        smc_data: list[dict],
        *,
        htf_trends: dict[str, str] | None = None,
        spread: int | None = None,
        has_high_impact_news: bool = False,
    ) -> DecisionResult:
        """Score et valide un signal sans effet de bord."""
        direction = "bullish" if signal.direction == Direction.BUY else "bearish"
        current_spread = spread if spread is not None else (
            candles[-1].spread if candles else 0
        )
        atr = self._atr(candles, self._settings.atr_period)
        concepts = self._directional_concepts(smc_data, direction)
        score = sum(self._WEIGHTS.get(concept, 0) for concept in concepts)
        reasons = sorted(concepts)
        rejected: list[str] = []

        if self._has_aligned_htf(htf_trends, direction):
            score += 15
            reasons.append("htf_trend")
        elif self._settings.enable_mtf:
            rejected.append("htf_trend")

        if self._ema_alignment(candles, direction):
            score += 10
            reasons.append("ema_trend")

        if self._in_correct_dealing_range(smc_data, signal.direction):
            score += 15
            reasons.append("premium_discount")
        elif self._settings.enable_premium_discount:
            rejected.append("premium_discount")

        if self._in_kill_zone(candles):
            score += 5
            reasons.append("kill_zone")
        elif self._settings.enable_kill_zone:
            rejected.append("kill_zone")

        if has_high_impact_news and self._settings.enable_news_filter:
            score -= 100
            rejected.append("high_impact_news")
        if self._settings.enable_spread_filter and current_spread > self._settings.maximum_spread:
            score -= 20
            rejected.append("spread")
        if self._settings.enable_atr_filter and not (
            Decimal(str(self._settings.min_atr))
            <= atr
            <= Decimal(str(self._settings.max_atr))
        ):
            rejected.append("atr")

        # Une entrée ICT requiert retracement dans une zone et confirmation.
        has_zone = bool(
            {
                SMCConcept.ORDER_BLOCK.value,
                SMCConcept.FVG.value,
                SMCConcept.BREAKER_BLOCK.value,
            }
            & concepts
        )
        has_confirmation = bool({SMCConcept.BOS.value, SMCConcept.CHOCH.value} & concepts)
        if not (has_zone and SMCConcept.OTE.value in concepts and has_confirmation):
            rejected.append("entry_confluence")

        score = max(0, min(100, score))
        sl, tp = self._structural_levels(signal, candles, smc_data, atr)
        if self._rr(signal.entry_price, sl, tp) < self._settings.minimum_risk_reward:
            rejected.append("risk_reward")
        tier = (
            "premium"
            if score >= 90
            else "valid"
            if score >= 80
            else "weak"
            if score >= 60
            else "no_trade"
        )
        approved = not rejected and score >= self._settings.minimum_score
        if score < self._settings.minimum_score:
            rejected.append("minimum_score")
        return DecisionResult(
            approved, score, score / 100, tier, reasons, rejected, atr, sl, tp
        )

    def enrich(self, signal: Signal, result: DecisionResult) -> Signal | None:
        """Retourne le signal enrichi ou ``None`` lorsque le trade est refusé."""
        if not result.approved:
            return None
        metadata = dict(signal.metadata)
        metadata["decision"] = {
            "score": result.score,
            "tier": result.tier,
            "confluences": result.reasons,
            "atr": str(result.atr),
        }
        return signal.model_copy(
            update={
                "confidence": result.confidence,
                "stop_loss": result.stop_loss,
                "take_profit": result.take_profit,
                "metadata": metadata,
            }
        )

    @staticmethod
    def _directional_concepts(data: Iterable[dict], direction: str) -> set[str]:
        return {
            d.get("concept", "")
            for d in data
            if d.get("direction") in (direction, "neutral")
        }

    @staticmethod
    def _has_aligned_htf(trends: dict[str, str] | None, direction: str) -> bool:
        return bool(trends) and all(value == direction for value in trends.values())

    def _in_correct_dealing_range(self, data: list[dict], direction: Direction) -> bool:
        required = "discount" if direction == Direction.BUY else "premium"
        for item in data:
            if item.get("concept") == SMCConcept.PREMIUM_DISCOUNT.value:
                return item.get("details", {}).get("current_zone") == required
            if item.get("concept") == required:
                return bool(item.get("details", {}).get(f"in_{required}"))
        return False

    def _in_kill_zone(self, candles: list[Candle]) -> bool:
        if not candles:
            return False
        return self._sessions.is_kill_zone(candles[-1].time)

    @staticmethod
    def _atr(candles: list[Candle], period: int = 14) -> Decimal:
        if len(candles) < 2:
            return Decimal("0")
        ranges = []
        for previous, candle in zip(candles[-period - 1 : -1], candles[-period:]):
            ranges.append(
                max(
                    candle.high - candle.low,
                    abs(candle.high - previous.close),
                    abs(candle.low - previous.close),
                )
            )
        if not ranges:
            return Decimal("0")
        return sum(ranges, Decimal("0")) / Decimal(len(ranges))

    @staticmethod
    def _ema_alignment(candles: list[Candle], direction: str) -> bool:
        if len(candles) < 50:
            return False
        closes = [float(c.close) for c in candles]

        def ema(values: list[float], span: int) -> float:
            value, alpha = values[0], 2 / (span + 1)
            for price in values[1:]:
                value = alpha * price + (1 - alpha) * value
            return value

        ema50 = ema(closes[-50:], min(50, len(closes)))
        ema200 = ema(closes, min(200, len(closes)))
        return ema50 > ema200 if direction == "bullish" else ema50 < ema200

    def _structural_levels(
        self, signal: Signal, candles: list[Candle], data: list[dict], atr: Decimal
    ) -> tuple[Decimal, Decimal]:
        """Place le SL derrière la structure et le TP sur la liquidité ou 2R."""
        entry = signal.entry_price
        directional = "bullish" if signal.direction == Direction.BUY else "bearish"
        lows = [c.low for c in candles[-20:]] or [entry]
        highs = [c.high for c in candles[-20:]] or [entry]
        buffer = atr * Decimal(str(self._settings.atr_multiplier))
        if signal.direction == Direction.BUY:
            candidates = [
                Decimal(str(d["price"]))
                for d in data
                if d.get("direction") == directional
                and d.get("concept")
                in (SMCConcept.ORDER_BLOCK.value, SMCConcept.LIQUIDITY_SWEEP.value)
            ]
            sl = min(candidates + lows) - buffer
            risk = entry - sl
            targets = [
                Decimal(str(d["price"]))
                for d in data
                if d.get("concept") in (SMCConcept.EQUAL_HIGH.value,)
            ] + highs
            tp = max(
                (
                    p
                    for p in targets
                    if p >= entry + risk * Decimal(str(self._settings.minimum_risk_reward))
                ),
                default=entry + risk * Decimal(str(self._settings.minimum_risk_reward)),
            )
        else:
            candidates = [
                Decimal(str(d["price"]))
                for d in data
                if d.get("direction") == directional
                and d.get("concept")
                in (SMCConcept.ORDER_BLOCK.value, SMCConcept.LIQUIDITY_SWEEP.value)
            ]
            sl = max(candidates + highs) + buffer
            risk = sl - entry
            targets = [
                Decimal(str(d["price"]))
                for d in data
                if d.get("concept") in (SMCConcept.EQUAL_LOW.value,)
            ] + lows
            tp = min(
                (
                    p
                    for p in targets
                    if p <= entry - risk * Decimal(str(self._settings.minimum_risk_reward))
                ),
                default=entry - risk * Decimal(str(self._settings.minimum_risk_reward)),
            )
        return sl, tp

    @staticmethod
    def _rr(entry: Decimal, sl: Decimal, tp: Decimal) -> float:
        risk = abs(entry - sl)
        return float(abs(tp - entry) / risk) if risk else 0.0
