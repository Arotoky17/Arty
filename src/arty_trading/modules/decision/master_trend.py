"""
Analyseur de tendance maître (Master Trend Analyzer).

Détermine la tendance d'un timeframe supérieur (1H par défaut) à partir
de la structure de marché HH/HL/LH/LL, en utilisant uniquement les bougies
clôturées. Ce n'est pas un simple détecteur de BOS : c'est une analyse
structurelle complète qui permet de distinguer :

- BULLISH : HH + HL
- BEARISH : LH + LL
- NEUTRAL : structure ambiguë, transition non confirmée, pas assez de swings

Règles importantes :
- Ne jamais considérer une bougie encore en formation comme confirmation
- Les BOS/CHoCH anciens ne doivent pas influencer la tendance actuelle
- La structure la plus récente prime sur l'historique
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from decimal import Decimal

from arty_trading.core.entities import Candle
from arty_trading.core.enums import SMCConcept, TimeFrame
from arty_trading.modules.decision.market_context import MarketContext
from arty_trading.modules.smc.base import (
    SMCDetection,
    find_external_swing_points,
    find_internal_swing_points,
    find_swing_points,
)
from arty_trading.modules.smc.structure import StructureDetector
from arty_trading.utils.helpers import calculate_atr_sliding


def _smc_from_dict(d: dict) -> SMCDetection:
    """Convertit un dict de détection SMC en objet SMCDetection.

    Utilisé par ``build_market_context`` pour alimenter le MarketStructureEngine
    avec les détections HTF déjà calculées.
    """
    concept = d.get("concept") or SMCConcept.POI.value
    try:
        concept_enum = SMCConcept(concept)
    except ValueError:
        concept_enum = SMCConcept.POI
    return SMCDetection(
        concept=concept_enum,
        direction=str(d.get("direction", "neutral")),
        price=Decimal(str(d.get("price", "0"))),
        index=int(d.get("index", 0)),
        details=dict(d.get("details", {}) or {}),
    )


@dataclass
class TrendAnalysis:
    """Résultat de l'analyse de tendance."""

    trend: str
    confidence: float
    hh: Decimal | None = None
    hl: Decimal | None = None
    lh: Decimal | None = None
    ll: Decimal | None = None
    recent_bos: list[dict] = field(default_factory=list)
    recent_choch: list[dict] = field(default_factory=list)
    recent_mss: list[dict] = field(default_factory=list)
    swing_high: Decimal | None = None
    swing_low: Decimal | None = None
    structure_age: int = 0
    details: dict[str, Any] = field(default_factory=dict)


class MasterTrendAnalyzer:
    """
    Analyseur de tendance principal pour le timeframe supérieur.

    Utilise la structure de marché (HH/HL/LH/LL) pour déterminer la tendance.
    Ne se base pas uniquement sur la couleur de la dernière bougie ou les EMA.
    """

    def __init__(
        self,
        htf: TimeFrame = TimeFrame.H1,
        swing_window: int = 2,
        external_window: int = 5,
        min_swings: int = 4,
        max_structure_age: int = 20,
    ) -> None:
        """
        Args:
            htf: Timeframe maître pour l'analyse de tendance
            swing_window: Fenêtre pour les swing points internes
            external_window: Fenêtre pour les swing points externes
            min_swings: Nombre minimum de swing points pour déterminer la tendance
            max_structure_age: Nombre maximum de bougies pour considérer
                un BOS/CHoCH comme récent
        """
        self._htf = htf
        self._swing_window = swing_window
        self._external_window = external_window
        self._min_swings = min_swings
        self._max_structure_age = max_structure_age
        self._structure_detector = StructureDetector(
            swing_window=swing_window,
            external_window=external_window,
            confirmation_bars=1,
        )

    @property
    def htf(self) -> TimeFrame:
        return self._htf

    def analyze(self, candles: list[Candle]) -> TrendAnalysis:
        """
        Analyse la structure de marché et détermine la tendance.

        Args:
            candles: Liste des bougies du timeframe supérieur (HTF)
                du plus ancien au plus récent. Seules les bougies CLÔTURÉES
                sont utilisées pour la détermination de la tendance.

        Returns:
            TrendAnalysis avec la tendance (BULLISH/BEARISH/NEUTRAL)
            et les niveaux structurels pertinents.
        """
        if not candles or len(candles) < self._min_swings + 2 * self._swing_window:
            return TrendAnalysis(
                trend="neutral",
                confidence=0.0,
                details={"reason": "insufficient_data"},
            )

        closed_candles = candles[:-1] if len(candles) > 1 else candles

        swing_points = find_swing_points(
            closed_candles, self._swing_window, self._external_window
        )

        if len(swing_points) < self._min_swings:
            return TrendAnalysis(
                trend="neutral",
                confidence=0.0,
                details={"reason": "insufficient_swings", "swing_count": len(swing_points)},
            )

        swing_highs = [sp for sp in swing_points if sp.type == "high"]
        swing_lows = [sp for sp in swing_points if sp.type == "low"]

        trend_result = self._determine_trend(swing_highs, swing_lows)

        structure_detections = self._structure_detector.detect(closed_candles)
        recent_bos, recent_choch, recent_mss = self._filter_recent_structure(
            structure_detections, len(candles)
        )

        last_high = swing_highs[-1] if swing_highs else None
        last_low = swing_lows[-1] if swing_lows else None

        return TrendAnalysis(
            trend=trend_result["trend"],
            confidence=trend_result["confidence"],
            hh=trend_result.get("hh"),
            hl=trend_result.get("hl"),
            lh=trend_result.get("lh"),
            ll=trend_result.get("ll"),
            recent_bos=recent_bos,
            recent_choch=recent_choch,
            recent_mss=recent_mss,
            swing_high=last_high.price if last_high else None,
            swing_low=last_low.price if last_low else None,
            structure_age=trend_result.get("structure_age", 0),
            details=trend_result.get("details", {}),
        )

    def _determine_trend(self, swing_highs: list, swing_lows: list) -> dict[str, Any]:
        """
        Détermine la tendance à partir des swing highs et swing lows.

        BULLISH : Higher High (HH) + Higher Low (HL)
        BEARISH : Lower High (LH) + Lower Low (LL)
        NEUTRAL : structure ambiguë
        """
        result = {
            "trend": "neutral",
            "confidence": 0.0,
            "details": {},
        }

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            result["details"]["reason"] = "not_enough_swings_for_trend"
            return result

        recent_highs = swing_highs[-4:] if len(swing_highs) >= 4 else swing_highs
        recent_lows = swing_lows[-4:] if len(swing_lows) >= 4 else swing_lows

        last_high = recent_highs[-1]
        prev_high = recent_highs[-2]
        last_low = recent_lows[-1]
        prev_low = recent_lows[-2]

        is_hh = last_high.price > prev_high.price
        is_hl = last_low.price > prev_low.price
        is_lh = last_high.price < prev_high.price
        is_ll = last_low.price < prev_low.price

        if is_hh and is_hl:
            result["trend"] = "bullish"
            result["confidence"] = 0.8
            result["hh"] = last_high.price
            result["hl"] = last_low.price
            result["details"]["pattern"] = "HH+HL"
            result["details"]["last_high"] = float(last_high.price)
            result["details"]["prev_high"] = float(prev_high.price)
            result["details"]["last_low"] = float(last_low.price)
            result["details"]["prev_low"] = float(prev_low.price)
            return result

        if is_lh and is_ll:
            result["trend"] = "bearish"
            result["confidence"] = 0.8
            result["lh"] = last_high.price
            result["ll"] = last_low.price
            result["details"]["pattern"] = "LH+LL"
            result["details"]["last_high"] = float(last_high.price)
            result["details"]["prev_high"] = float(prev_high.price)
            result["details"]["last_low"] = float(last_low.price)
            result["details"]["prev_low"] = float(prev_low.price)
            return result

        if is_hh and not is_hl:
            result["trend"] = "bullish"
            result["confidence"] = 0.5
            result["hh"] = last_high.price
            result["hl"] = last_low.price
            result["details"]["pattern"] = "HH_only"
            result["details"]["note"] = "HH confirmed but HL not confirmed"
            return result

        if is_hl and not is_hh:
            result["trend"] = "bullish"
            result["confidence"] = 0.5
            result["hh"] = last_high.price
            result["hl"] = last_low.price
            result["details"]["pattern"] = "HL_only"
            result["details"]["note"] = "HL confirmed but HH not confirmed"
            return result

        if is_lh and not is_ll:
            result["trend"] = "bearish"
            result["confidence"] = 0.5
            result["lh"] = last_high.price
            result["ll"] = last_low.price
            result["details"]["pattern"] = "LH_only"
            result["details"]["note"] = "LH confirmed but LL not confirmed"
            return result

        if is_ll and not is_lh:
            result["trend"] = "bearish"
            result["confidence"] = 0.5
            result["lh"] = last_high.price
            result["ll"] = last_low.price
            result["details"]["pattern"] = "LL_only"
            result["details"]["note"] = "LL confirmed but LH not confirmed"
            return result

        result["details"]["reason"] = "conflicting_structure"
        result["details"]["is_hh"] = is_hh
        result["details"]["is_hl"] = is_hl
        result["details"]["is_lh"] = is_lh
        result["details"]["is_ll"] = is_ll
        return result

    def _filter_recent_structure(
        self, detections: list[SMCDetection], total_candles: int
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """
        Filtre les détections de structure pour ne garder que les plus récentes.

        Args:
            detections: Toutes les détections de structure
            total_candles: Nombre total de bougies (pour calculer l'ancienneté)

        Returns:
            Tuple (recent_bos, recent_choch, recent_mss) avec seulement
            les détections récentes et pertinentes.
        """
        cutoff = total_candles - self._max_structure_age

        recent_bos = []
        recent_choch = []
        recent_mss = []

        for d in detections:
            if d.index < cutoff:
                continue

            d_dict = d.to_dict()
            d_dict["age_candles"] = total_candles - d.index

            if d.concept == SMCConcept.BOS:
                recent_bos.append(d_dict)
            elif d.concept == SMCConcept.CHOCH:
                recent_choch.append(d_dict)
            elif d.concept == SMCConcept.MSS:
                recent_mss.append(d_dict)

        recent_bos.sort(key=lambda x: x.get("index", 0), reverse=True)
        recent_choch.sort(key=lambda x: x.get("index", 0), reverse=True)
        recent_mss.sort(key=lambda x: x.get("index", 0), reverse=True)

        return recent_bos, recent_choch, recent_mss

    def get_master_trend(self, candles: list[Candle]) -> str:
        """
        Méthode utilitaire pour obtenir rapidement la tendance maître.

        Args:
            candles: Bougies du timeframe supérieur

        Returns:
            "bullish", "bearish", ou "neutral"
        """
        result = self.analyze(candles)
        return result.trend

    def build_market_context(
        self,
        symbol: str,
        htf_candles: list[Candle],
        ltf_candles: list[Candle],
        htf_smc_data: list[dict],
        ltf_smc_data: list[dict],
    ) -> MarketContext:
        """
        Construit un MarketContext complet à partir des données multi-timeframe.

        Args:
            symbol: Symbole tradé
            htf_candles: Bougies du timeframe supérieur (1H)
            ltf_candles: Bougies du timeframe d'entrée (5M)
            htf_smc_data: Détections SMC du timeframe supérieur
            ltf_smc_data: Détections SMC du timeframe d'entrée

        Returns:
            MarketContext centralisé
        """
        trend_analysis = self.analyze(htf_candles)

        latest_htf = htf_candles[-1] if htf_candles else None
        latest_ltf = ltf_candles[-1] if ltf_candles else None

        active_obs = [
            d for d in htf_smc_data + ltf_smc_data
            if d.get("concept") == "order_block"
            and not d.get("details", {}).get("mitigated", False)
        ]

        active_fvgs = [
            d for d in htf_smc_data + ltf_smc_data
            if d.get("concept") == "fair_value_gap"
        ]

        pd_data = [
            d for d in htf_smc_data
            if d.get("concept") == "premium_discount"
        ]

        premium = None
        discount = None
        if pd_data:
            latest_pd = max(pd_data, key=lambda x: x.get("index", 0))
            premium = Decimal(str(latest_pd.get("details", {}).get("premium_end", 0)))
            discount = Decimal(str(latest_pd.get("details", {}).get("discount_start", 0)))

        spread = 0
        if latest_ltf:
            spread = getattr(latest_ltf, "spread", 0)
        elif latest_htf:
            spread = getattr(latest_htf, "spread", 0)

        atr = Decimal("0")
        if ltf_candles:
            atr = calculate_atr_sliding(ltf_candles, 14)

        # Régime de marché / score de tendance structurel (MarketStructureEngine).
        from arty_trading.modules.decision.market_structure_engine import (
            MarketStructureEngine,
        )

        regime_analysis = MarketStructureEngine().analyze(
            htf_candles,
            [_smc_from_dict(d) for d in htf_smc_data],
        )

        from arty_trading.config.settings import get_settings

        profile = None
        try:
            settings = get_settings()
            profile = settings.get_instrument_profile(symbol)
        except Exception:
            pass

        return MarketContext(
            symbol=symbol,
            timestamp=latest_ltf.time if latest_ltf else (
                latest_htf.time if latest_htf else datetime.utcnow()
            ),
            htf=self._htf,
            ltf=TimeFrame.M5,
            master_trend=trend_analysis.trend,
            trend_confidence=trend_analysis.confidence,
            hh=trend_analysis.hh,
            hl=trend_analysis.hl,
            lh=trend_analysis.lh,
            ll=trend_analysis.ll,
            recent_bos=trend_analysis.recent_bos,
            recent_choch=trend_analysis.recent_choch,
            recent_mss=trend_analysis.recent_mss,
            swing_high=trend_analysis.swing_high,
            swing_low=trend_analysis.swing_low,
            premium=premium,
            discount=discount,
            liquidity_zones=self._extract_liquidity_zones(htf_smc_data),
            key_levels=self._extract_key_levels(htf_smc_data),
            active_order_blocks=active_obs,
            active_fvgs=active_fvgs,
            spread=spread,
            atr=atr,
            htf_candles=htf_candles,
            ltf_candles=ltf_candles,
            htf_smc_data=htf_smc_data,
            ltf_smc_data=ltf_smc_data,
            metadata={
                "trend_details": trend_analysis.details,
                "structure_age": trend_analysis.structure_age,
            },
            regime=regime_analysis.regime.value,
            trend_score=regime_analysis.trend_score,
            no_trade_reasons=regime_analysis.no_trade_reasons,
            structure_valid=regime_analysis.structure_valid,
            structure_age=regime_analysis.structure_age,
            sl_buffer_atr_mult=0.5,
            instrument_profile=profile,
        )

    def _extract_liquidity_zones(self, smc_data: list[dict]) -> list[dict]:
        zones = []
        for d in smc_data:
            if d.get("concept") in (
                SMCConcept.LIQUIDITY_SWEEP.value,
                SMCConcept.EQUAL_HIGH.value,
                SMCConcept.EQUAL_LOW.value,
            ):
                zones.append(d)
        return zones

    def _extract_key_levels(self, smc_data: list[dict]) -> list[dict]:
        levels = []
        for d in smc_data:
            if d.get("concept") in (
                SMCConcept.BOS.value,
                SMCConcept.CHOCH.value,
                SMCConcept.MSS.value,
                "swing_high",
                "swing_low",
                "support",
                "resistance",
            ):
                levels.append(d)
        return levels
