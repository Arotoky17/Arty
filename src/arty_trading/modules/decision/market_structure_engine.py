"""Moteur de structure de marché — régime, score de tendance, structure courante.

Analyse pure (aucun I/O) construite au-dessus des *swing points* et des
détections SMC. Elle produit pour un timeframe donné :

- un **régime de marché** précis : ``STRONG_BULLISH`` … ``RANGE`` / ``TRANSITION`` ;
- un **score de tendance** numérique (configurable), borné à ``[-100, +100]`` ;
- la **structure courante** valide (HH / HL / LH / LL, dernier BOS / CHoCH /
  MSS valide, âge et validité de la structure).

Principes imposés par l'architecture cible :

1. La **structure courante prime sur l'historique** : un ancien BOS bullish ne
   peut pas générer un BUY lorsque la structure actuelle est bearish.
2. Un événement trop ancien ou invalidé est ignoré (``structure_valid``).
3. Le **régime / score ne contournent jamais les filtres durs** : ``RANGE`` et
   ``TRANSITION`` interdisent toute direction, et ``allow_counter_trend=False``
   bloque toute entrée contre le régime.
4. **Aucune fonction ici n'ouvre de trade.** Le moteur ne produit que des
   informations de marché structurées.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from arty_trading.core.entities import Candle
from arty_trading.core.enums import MarketRegime, NoTradeReason, SMCConcept
from arty_trading.modules.smc.base import (
    SMCDetection,
    SwingPoint,
    find_swing_points,
)


def _clamp(value: float, low: float = -100.0, high: float = 100.0) -> float:
    """Borne une valeur à l'intervalle [low, high]."""
    return max(low, min(high, value))
# Seuils par défaut du score de tendance (configurables).
_DEFAULT_WEIGHTS = {
    "hh": 20,
    "hl": 15,
    "lh": -20,
    "ll": -15,
    "bos_bullish": 15,
    "bos_bearish": -15,
    "choch_bullish": 10,
    "choch_bearish": -10,
    "mss_bullish": 15,
    "mss_bearish": -15,
    "above_equilibrium": 10,
    "below_equilibrium": -10,
}


@dataclass(frozen=True)
class MarketStructureSettings:
    """Réglages du MarketStructureEngine (régime, score, filtres durs)."""

    max_structure_age: int = 40
    swing_window: int = 2
    external_window: int = 5
    strong_bullish: int = 60
    bullish: int = 20
    bearish: int = -20
    strong_bearish: int = -60
    range_band: int = 10
    allow_counter_trend: bool = False
    weights: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_WEIGHTS))


@dataclass
class MarketStructureAnalysis:
    """Résultat structurel complet, sérialisable et traçable."""

    regime: MarketRegime = MarketRegime.RANGE
    trend: str = "neutral"  # bull/bear/neutral simplifié pour compat
    trend_score: float = 0.0
    confidence: float = 0.0

    hh: Decimal | None = None
    hl: Decimal | None = None
    lh: Decimal | None = None
    ll: Decimal | None = None
    swing_high: Decimal | None = None
    swing_low: Decimal | None = None

    latest_bos: dict | None = None
    latest_choch: dict | None = None
    latest_mss: dict | None = None

    structure_age: int = 0
    structure_valid: bool = False
    structure_bias: str = "neutral"

    no_trade_reasons: list[str] = field(default_factory=list)

    justification: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def _directional_ok(self) -> bool:
        return self.regime in {
            MarketRegime.STRONG_BULLISH,
            MarketRegime.BULLISH,
            MarketRegime.WEAK_BULLISH,
            MarketRegime.STRONG_BEARISH,
            MarketRegime.BEARISH,
            MarketRegime.WEAK_BEARISH,
        }

    def allows_buy(self) -> bool:
        if not self._directional_ok():
            return False
        return self.regime in {
            MarketRegime.STRONG_BULLISH,
            MarketRegime.BULLISH,
            MarketRegime.WEAK_BULLISH,
        }

    def allows_sell(self) -> bool:
        if not self._directional_ok():
            return False
        return self.regime in {
            MarketRegime.STRONG_BEARISH,
            MarketRegime.BEARISH,
            MarketRegime.WEAK_BEARISH,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime.value,
            "trend": self.trend,
            "trend_score": round(self.trend_score, 1),
            "confidence": round(self.confidence, 3),
            "structure_bias": self.structure_bias,
            "hh": float(self.hh) if self.hh else None,
            "hl": float(self.hl) if self.hl else None,
            "lh": float(self.lh) if self.lh else None,
            "ll": float(self.ll) if self.ll else None,
            "swing_high": float(self.swing_high) if self.swing_high else None,
            "swing_low": float(self.swing_low) if self.swing_low else None,
            "latest_bos": self.latest_bos,
            "latest_choch": self.latest_choch,
            "latest_mss": self.latest_mss,
            "structure_age": self.structure_age,
            "structure_valid": self.structure_valid,
            "no_trade_reasons": list(self.no_trade_reasons),
            "justification": self.justification,
            "allows_buy": self.allows_buy(),
            "allows_sell": self.allows_sell(),
        }


def _dictify(detection: SMCDetection, total_candles: int) -> dict:
    """Transforme une SMCDetection en dict avec âge en bougies."""
    age = total_candles - detection.index
    return {
        "concept": detection.concept.value,
        "direction": detection.direction,
        "price": float(detection.price),
        "index": detection.index,
        "age_candles": age,
        "details": detection.details,
    }
class MarketStructureEngine:
    """Détermine le régime de marché, le score de tendance et la structure courante."""

    def __init__(self, settings: MarketStructureSettings | None = None) -> None:
        self._settings = settings or MarketStructureSettings()
        self._w = self._settings.weights

    def analyze(
        self,
        candles: list[Candle],
        smc_detections: list[SMCDetection] | None = None,
    ) -> MarketStructureAnalysis:
        """Analyse la structure de marché sur des bougies clôturées.

        Args:
            candles: Bougies du timeframe (du plus ancien au plus récent).
            smc_detections: Détections SMC (BOS, CHoCH, MSS…). Optionnel —
                fournies par le SMCDetector en production.

        Returns:
            Une ``MarketStructureAnalysis`` complète.
        """
        total = len(candles)
        if total < 5:
            return self._empty("insufficient_data")

        closed = candles[:-1] if total > 1 else candles
        swing_points = find_swing_points(
            closed, self._settings.swing_window, self._settings.external_window
        )
        swings_highs = [sp for sp in swing_points if sp.type == "high"]
        swings_lows = [sp for sp in swing_points if sp.type == "low"]

        structure = self._current_structure(
            candles, swings_highs, swings_lows, smc_detections, total
        )
        regime, score, confidence, justification = self._classify(
            candles, swings_highs, swings_lows, smc_detections, structure
        )

        direction = _regime_to_trend(regime)
        reasons = self._no_trade_reasons(regime, structure)

        return MarketStructureAnalysis(
            regime=regime,
            trend=direction,
            trend_score=score,
            confidence=confidence,
            structure_bias=structure["structure_bias"],
            hh=structure["hh"],
            hl=structure["hl"],
            lh=structure["lh"],
            ll=structure["ll"],
            swing_high=structure["swing_high"],
            swing_low=structure["swing_low"],
            latest_bos=structure["latest_bos"],
            latest_choch=structure["latest_choch"],
            latest_mss=structure["latest_mss"],
            structure_age=structure["structure_age"],
            structure_valid=structure["valid"],
            no_trade_reasons=reasons,
            justification=justification,
            details={
                "trend_score_components": structure["evidence"],
                "regime": regime.value,
                "allow_counter_trend": self._settings.allow_counter_trend,
            },
        )

    def _current_structure(
        self,
        candles: list[Candle],
        swings_highs: list[SwingPoint],
        swings_lows: list[SwingPoint],
        detections: list[SMCDetection] | None,
        total: int,
    ) -> dict[str, Any]:
        detections = detections or []

        hh = hl = lh = ll = None
        if len(swings_highs) >= 2:
            hh = (
                swings_highs[-1].price
                if swings_highs[-1].price > swings_highs[-2].price
                else None
            )
            lh = (
                swings_highs[-1].price
                if swings_highs[-1].price < swings_highs[-2].price
                else None
            )
        if len(swings_lows) >= 2:
            hl = (
                swings_lows[-1].price
                if swings_lows[-1].price > swings_lows[-2].price
                else None
            )
            ll = (
                swings_lows[-1].price
                if swings_lows[-1].price < swings_lows[-2].price
                else None
            )

        swing_high = swings_highs[-1].price if swings_highs else None
        swing_low = swings_lows[-1].price if swings_lows else None

        latest_bos = latest_choch = latest_mss = None
        for d in detections:
            if d.index < total - self._settings.max_structure_age:
                continue
            item = _dictify(d, total)
            if d.concept == SMCConcept.BOS:
                latest_bos = item
            elif d.concept == SMCConcept.CHOCH:
                latest_choch = item
            elif d.concept == SMCConcept.MSS:
                latest_mss = item

        last_event_index = 0
        for d in detections:
            if d.concept in {SMCConcept.BOS, SMCConcept.CHOCH, SMCConcept.MSS}:
                last_event_index = max(last_event_index, d.index)

        # La validité de la structure s'appuie aussi sur les swing points récents
        # (pas uniquement sur les BOS/CHoCH/MSS explicites).
        swing_indices = [
            sp.index for sp in (swings_highs + swings_lows)
        ]
        if swing_indices:
            last_event_index = max(last_event_index, max(swing_indices))
        structure_age = total - last_event_index if last_event_index else max(total, 1)

        valid = structure_age <= self._settings.max_structure_age

        # Direction détectée si au moins un côté (hauts OU bas) montre une
        # progression récente, ou si des BOS/CHoCH/MSS contraires existent.
        directional = bool(
            (hh is not None or lh is not None)
            or (hl is not None or ll is not None)
        )

        # Biais structurel : bullish si HH+HL, bearish si LH+LL, sinon neutral.
        if hh is not None and hl is not None:
            structure_bias = "bullish"
        elif lh is not None and ll is not None:
            structure_bias = "bearish"
        else:
            structure_bias = "neutral"

        return {
            "hh": hh,
            "hl": hl,
            "lh": lh,
            "ll": ll,
            "swing_high": swing_high,
            "swing_low": swing_low,
            "latest_bos": latest_bos,
            "latest_choch": latest_choch,
            "latest_mss": latest_mss,
            "structure_age": structure_age,
            "valid": valid,
            "directional": directional,
            "structure_bias": structure_bias,
            "evidence": self._evidence(candles, detections, hh, hl, lh, ll),
        }
    def _evidence(
        self,
        candles: list[Candle],
        detections: list[SMCDetection],
        hh: Decimal | None,
        hl: Decimal | None,
        lh: Decimal | None,
        ll: Decimal | None,
    ) -> dict[str, float]:
        evidence: dict[str, float] = {k: 0.0 for k in self._w}
        if hh is not None:
            evidence["hh"] = self._w["hh"]
        if hl is not None:
            evidence["hl"] = self._w["hl"]
        if lh is not None:
            evidence["lh"] = self._w["lh"]
        if ll is not None:
            evidence["ll"] = self._w["ll"]

        hs = [c.high for c in candles[-20:]] or [candles[-1].high]
        ls = [c.low for c in candles[-20:]] or [candles[-1].low]
        hi, lo = max(hs), min(ls)
        if hi > lo:
            eq = lo + (hi - lo) / 2
            if candles[-1].close > eq:
                evidence["above_equilibrium"] = self._w["above_equilibrium"]
                evidence["below_equilibrium"] = 0.0
            elif candles[-1].close < eq:
                evidence["below_equilibrium"] = self._w["below_equilibrium"]
                evidence["above_equilibrium"] = 0.0

        for d in detections:
            if d.index < len(candles) - self._settings.max_structure_age:
                continue
            key = None
            if d.concept == SMCConcept.BOS:
                key = "bos_bullish" if d.direction == "bullish" else "bos_bearish"
            elif d.concept == SMCConcept.CHOCH:
                key = "choch_bullish" if d.direction == "bullish" else "choch_bearish"
            elif d.concept == SMCConcept.MSS:
                key = "mss_bullish" if d.direction == "bullish" else "mss_bearish"
            if key and key in evidence:
                evidence[key] = self._w[key]

        return evidence
    def _classify(
        self,
        candles: list[Candle],
        swings_highs: list[SwingPoint],
        swings_lows: list[SwingPoint],
        detections: list[SMCDetection] | None,
        structure: dict[str, Any],
    ) -> tuple[MarketRegime, float, float, str]:
        detections = detections or []
        score = round(_clamp(sum(structure["evidence"].values())), 1)

        if not structure["valid"]:
            return (
                MarketRegime.RANGE,
                0.0,
                0.2,
                "Structure invalide (événements trop anciens) → RANGE / NO TRADE",
            )

        if not structure.get("directional"):
            return (
                MarketRegime.RANGE,
                score,
                0.3,
                "Pas de structure directionnelle claire (HH/HL ou LH/LL) → RANGE / NO TRADE",
            )

        recent = [
            d
            for d in detections
            if d.index >= len(candles) - self._settings.max_structure_age
        ]
        counter = [
            d for d in recent if d.concept in (SMCConcept.CHOCH, SMCConcept.MSS)
        ]

        structure_bias = structure.get("structure_bias", "neutral")
        if counter and structure_bias != "neutral":
            opposite_direction = (
                "bearish" if structure_bias == "bullish" else "bullish"
            )
            has_opposite = any(d.direction == opposite_direction for d in counter)
            if has_opposite:
                return (
                    MarketRegime.TRANSITION,
                    score,
                    0.4,
                    f"Bascule de structure (CHoCH/MSS {opposite_direction}) opposée au "
                    f"biais {structure_bias} → TRANSITION / NO TRADE",
                )
            # CHoCH/MSS dans le même sens que le biais structurel = confirmation
            # On continue vers la classification par score.

        if score >= self._settings.strong_bullish:
            return (
                MarketRegime.STRONG_BULLISH,
                score,
                0.8,
                f"Régime strong_bullish | score {score:+.0f} | structure {self._structure_summary(structure)}",
            )
        if score >= self._settings.bullish:
            return (
                MarketRegime.BULLISH,
                score,
                0.7,
                f"Régime bullish | score {score:+.0f} | structure {self._structure_summary(structure)}",
            )
        if score > self._settings.range_band:
            return (
                MarketRegime.WEAK_BULLISH,
                score,
                0.55,
                f"Régime weak_bullish | score {score:+.0f}",
            )
        if score <= self._settings.strong_bearish:
            return (
                MarketRegime.STRONG_BEARISH,
                score,
                0.8,
                f"Régime strong_bearish | score {score:+.0f} | structure {self._structure_summary(structure)}",
            )
        if score <= self._settings.bearish:
            return (
                MarketRegime.BEARISH,
                score,
                0.7,
                f"Régime bearish | score {score:+.0f} | structure {self._structure_summary(structure)}",
            )
        if score < -self._settings.range_band:
            return (
                MarketRegime.WEAK_BEARISH,
                score,
                0.55,
                f"Régime weak_bearish | score {score:+.0f}",
            )

        return (
            MarketRegime.RANGE,
            score,
            0.3,
            f"Score de tendance {score:+.0f} trop faible pour une direction claire → RANGE / NO TRADE",
        )
    def _no_trade_reasons(
        self, regime: MarketRegime, structure: dict[str, Any]
    ) -> list[str]:
        reasons: list[str] = []
        if regime == MarketRegime.RANGE:
            reasons.append(NoTradeReason.H1_RANGE.value)
        elif regime == MarketRegime.TRANSITION:
            reasons.append(NoTradeReason.H1_TRANSITION.value)
        if not structure.get("valid"):
            reasons.append(NoTradeReason.NO_STRUCTURE.value)
        if not self._settings.allow_counter_trend and regime in {
            MarketRegime.STRONG_BULLISH,
            MarketRegime.BULLISH,
            MarketRegime.WEAK_BULLISH,
            MarketRegime.STRONG_BEARISH,
            MarketRegime.BEARISH,
            MarketRegime.WEAK_BEARISH,
        }:
            reasons.append(NoTradeReason.COUNTER_TREND.value)
        return list(dict.fromkeys(reasons))

    def _structure_summary(self, structure: dict[str, Any]) -> str:
        parts = []
        for label in ("hh", "hl", "lh", "ll"):
            if structure.get(label) is not None:
                parts.append(label.upper())
        return " + ".join(parts[:2]) if parts else "aucune"

    def _empty(self, reason: str) -> MarketStructureAnalysis:
        return MarketStructureAnalysis(
            regime=MarketRegime.RANGE,
            trend="neutral",
            structure_bias="neutral",
            no_trade_reasons=[NoTradeReason.NO_STRUCTURE.value],
            justification=f"Données insuffisantes ({reason}) → RANGE / NO TRADE",
            structure_valid=False,
        )


def _regime_to_trend(regime: MarketRegime) -> str:
    if regime in {
        MarketRegime.STRONG_BULLISH,
        MarketRegime.BULLISH,
        MarketRegime.WEAK_BULLISH,
    }:
        return "bullish"
    if regime in {
        MarketRegime.STRONG_BEARISH,
        MarketRegime.BEARISH,
        MarketRegime.WEAK_BEARISH,
    }:
        return "bearish"
    if regime == MarketRegime.TRANSITION:
        return "transition"
    return "neutral"
