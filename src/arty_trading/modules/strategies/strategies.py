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

from decimal import Decimal

from arty_trading.core.entities import Candle, Signal
from arty_trading.core.enums import Direction, SignalType, TimeFrame
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
    ) -> None:
        super().__init__(enabled, risk_reward_min, confidence_min)

    @property
    def name(self) -> str:
        return "SMC Trend Following"

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
            sig = self._build_trend_signal(
                symbol, timeframe, current_price, smc_data, candles, direction
            )
            if sig is not None:
                candidates.append(sig)

        if latest_bull and (
            not latest_bear or latest_bull["index"] > latest_bear["index"]
        ):
            _try_direction(Direction.BUY)
        elif latest_bear:
            _try_direction(Direction.SELL)

        if not candidates:
            return None

        return max(candidates, key=lambda s: s.confidence)

    def _build_trend_signal(
        self,
        symbol: str,
        timeframe: TimeFrame,
        current_price: Decimal,
        smc_data: list[dict],
        candles: list[Candle],
        direction: Direction,
    ) -> Signal | None:
        """Construit un signal de tendance SMC pour la direction donnée.

        Args:
            symbol: Symbole tradé
            timeframe: Timeframe analysé
            current_price: Prix d'entrée
            smc_data: Détections SMC
            candles: Liste des bougies (pour le calcul ATR)
            direction: Direction du signal (BUY ou SELL)

        Returns:
            Le signal construit, ou None si la confiance est insuffisante.
        """
        dir_tag = "bullish" if direction == Direction.BUY else "bearish"
        signal_type = SignalType.BUY if direction == Direction.BUY else SignalType.SELL

        # Vérifier la confirmation de clôture dans la zone avant de compter les confluences.
        # Un retest valide doit voir la dernière bougie clôturer dans la zone (ou au-delà)
        # et dans le bon sens, pas seulement une mèche qui touche la zone.
        confirmed_rejection = False
        for concept in ("fair_value_gap", "order_block"):
            items = self._filter_smc(smc_data, concept, dir_tag)
            if items:
                latest = max(items, key=lambda d: d.get("index", -1))
                details = latest.get("details", {})
                if concept == "fair_value_gap":
                    zone_top = details.get("gap_top")
                    zone_bottom = details.get("gap_bottom")
                else:
                    zone_top = details.get("ob_top")
                    zone_bottom = details.get("ob_bottom")
                if zone_top is not None and zone_bottom is not None:
                    if self._has_confirmed_rejection(
                        candles, float(zone_top), float(zone_bottom), dir_tag
                    ):
                        confirmed_rejection = True
                        break

        if not confirmed_rejection:
            return None

        confluences = 0
        concepts = []

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

        sl, tp = self._calculate_atr_based_sl_tp(current_price, direction, candles)
        trend_word = "haussière" if direction == Direction.BUY else "baissière"

        return self._build_signal(
            symbol=symbol,
            signal_type=signal_type,
            direction=direction,
            entry_price=current_price,
            stop_loss=sl,
            take_profit=tp,
            confidence=confidence,
            timeframe=timeframe,
            smc_concepts=concepts,
            justification=(
                f"Tendance {trend_word} confirmée par BOS (le plus récent) "
                f"avec {confluences} confluences"
            ),
        )


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
