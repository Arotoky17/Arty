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
    ) -> Signal | None:
        if not self._enabled or len(candles) < 10:
            return None

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        current_price = candles[-1].close

        # Chercher un BOS haussier
        bullish_bos = self._filter_smc(smc_data, "break_of_structure", "bullish")
        bearish_bos = self._filter_smc(smc_data, "break_of_structure", "bearish")

        if bullish_bos:
            # Vérifier les confluences haussières
            confluences = 0
            concepts = []

            if self._has_concept(smc_data, "fair_value_gap", "bullish"):
                confluences += 2
                concepts.append("FVG bullish")

            if self._has_concept(smc_data, "order_block", "bullish"):
                confluences += 2
                concepts.append("Order Block bullish")

            if self._has_concept(smc_data, "optimal_trade_entry", "bullish"):
                confluences += 1
                concepts.append("OTE bullish")

            if self._has_concept(smc_data, "liquidity_sweep", "bullish"):
                confluences += 1
                concepts.append("Liquidity Sweep bullish")

            confluences += 1  # BOS lui-même
            concepts.append("BOS bullish")

            confidence = self._calculate_confidence(confluences, 7)
            if confidence < self._confidence_min:
                return None

            sl, tp = self._calculate_sl_tp(current_price, Direction.BUY)
            return self._build_signal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                direction=Direction.BUY,
                entry_price=current_price,
                stop_loss=sl,
                take_profit=tp,
                confidence=confidence,
                timeframe=timeframe,
                smc_concepts=concepts,
                justification=f"Tendance haussière confirmée par BOS avec {confluences} confluences",
            )

        if bearish_bos:
            confluences = 0
            concepts = []

            if self._has_concept(smc_data, "fair_value_gap", "bearish"):
                confluences += 2
                concepts.append("FVG bearish")

            if self._has_concept(smc_data, "order_block", "bearish"):
                confluences += 2
                concepts.append("Order Block bearish")

            if self._has_concept(smc_data, "optimal_trade_entry", "bearish"):
                confluences += 1
                concepts.append("OTE bearish")

            if self._has_concept(smc_data, "liquidity_sweep", "bearish"):
                confluences += 1
                concepts.append("Liquidity Sweep bearish")

            confluences += 1
            concepts.append("BOS bearish")

            confidence = self._calculate_confidence(confluences, 7)
            if confidence < self._confidence_min:
                return None

            sl, tp = self._calculate_sl_tp(current_price, Direction.SELL)
            return self._build_signal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                direction=Direction.SELL,
                entry_price=current_price,
                stop_loss=sl,
                take_profit=tp,
                confidence=confidence,
                timeframe=timeframe,
                smc_concepts=concepts,
                justification=f"Tendance baissière confirmée par BOS avec {confluences} confluences",
            )

        return None


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
    ) -> Signal | None:
        if not self._enabled or len(candles) < 10:
            return None

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        current_price = candles[-1].close

        # Chercher un BOS + FVG (cassure avec déplacement)
        bullish_bos = self._filter_smc(smc_data, "break_of_structure", "bullish")
        bullish_fvg = self._filter_smc(smc_data, "fair_value_gap", "bullish")

        if bullish_bos and bullish_fvg:
            # Vérifier le volume (dernière bougie > moyenne) - seuil assoupli
            avg_volume = sum(c.volume for c in candles[-20:]) / min(20, len(candles))
            if candles[-1].volume > avg_volume * 1.1:
                confidence = self._calculate_confidence(5, 6)
                sl, tp = self._calculate_sl_tp(current_price, Direction.BUY, 15, 30)
                return self._build_signal(
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
                )

        bearish_bos = self._filter_smc(smc_data, "break_of_structure", "bearish")
        bearish_fvg = self._filter_smc(smc_data, "fair_value_gap", "bearish")

        if bearish_bos and bearish_fvg:
            avg_volume = sum(c.volume for c in candles[-20:]) / min(20, len(candles))
            if candles[-1].volume > avg_volume * 1.1:
                confidence = self._calculate_confidence(5, 6)
                sl, tp = self._calculate_sl_tp(current_price, Direction.SELL, 15, 30)
                return self._build_signal(
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
                )

        return None


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
    ) -> Signal | None:
        if not self._enabled or len(candles) < 10:
            return None

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        current_price = candles[-1].close

        # Vérifier 3 bougies haussières consécutives + FVG
        if len(candles) >= 3:
            last3 = candles[-3:]
            if all(c.is_bullish for c in last3):
                if self._has_concept(smc_data, "fair_value_gap", "bullish"):
                    confidence = self._calculate_confidence(4, 5)
                    sl, tp = self._calculate_sl_tp(current_price, Direction.BUY, 12, 24)
                    return self._build_signal(
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
                    )

            if all(not c.is_bullish for c in last3):
                if self._has_concept(smc_data, "fair_value_gap", "bearish"):
                    confidence = self._calculate_confidence(4, 5)
                    sl, tp = self._calculate_sl_tp(current_price, Direction.SELL, 12, 24)
                    return self._build_signal(
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
                    )

        return None


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
    ) -> Signal | None:
        if not self._enabled or len(candles) < 10:
            return None

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        current_price = candles[-1].close

        # CHoCH haussier + Liquidity Sweep bullish
        bullish_choch = self._filter_smc(smc_data, "change_of_character", "bullish")
        bullish_sweep = self._filter_smc(smc_data, "liquidity_sweep", "bullish")

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
            sl, tp = self._calculate_sl_tp(current_price, Direction.BUY, 25, 50)
            return self._build_signal(
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
            )

        # CHoCH baissier + Liquidity Sweep bearish
        bearish_choch = self._filter_smc(smc_data, "change_of_character", "bearish")
        bearish_sweep = self._filter_smc(smc_data, "liquidity_sweep", "bearish")

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
            sl, tp = self._calculate_sl_tp(current_price, Direction.SELL, 25, 50)
            return self._build_signal(
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
            )

        return None


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
    ) -> Signal | None:
        if not self._enabled or len(candles) < 5:
            return None

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        current_price = candles[-1].close

        # FVG haussier + spread acceptable (assoupli de 5 à 20)
        bullish_fvg = self._filter_smc(smc_data, "fair_value_gap", "bullish")
        if bullish_fvg and candles[-1].spread <= 20:
            confidence = self._calculate_confidence(3, 4)
            sl, tp = self._calculate_sl_tp(current_price, Direction.BUY, 8, 8)
            return self._build_signal(
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
            )

        bearish_fvg = self._filter_smc(smc_data, "fair_value_gap", "bearish")
        if bearish_fvg and candles[-1].spread <= 20:
            confidence = self._calculate_confidence(3, 4)
            sl, tp = self._calculate_sl_tp(current_price, Direction.SELL, 8, 8)
            return self._build_signal(
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
            )

        return None


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

        if bullish_bos and bullish_ote:
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
            if confidence < self._confidence_min:
                return None

            sl, tp = self._calculate_sl_tp(current_price, Direction.BUY, 30, 90)
            return self._build_signal(
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
            )

        bearish_bos = self._filter_smc(smc_data, "break_of_structure", "bearish")
        bearish_ote = self._filter_smc(smc_data, "optimal_trade_entry", "bearish")
        bearish_ob = self._filter_smc(smc_data, "order_block", "bearish")

        if bearish_bos and bearish_ote:
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
            if confidence < self._confidence_min:
                return None

            sl, tp = self._calculate_sl_tp(current_price, Direction.SELL, 30, 90)
            return self._build_signal(
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
            )

        return None