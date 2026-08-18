"""
Garde-fous d'exécution — revalidation et gate final avant envoi d'ordre.

Regroupe les deux contrôles de sécurité qu'applique le ``TradingEngine``
**juste avant** d'envoyer un ordre, entre la génération du signal et l'appel
au gestionnaire de risque / exécuteur :

1. ``revalidate_before_execution`` : vérifie que le contexte n'a pas changé entre
   la génération du signal et l'envoi de l'ordre (tendance H1 alignée).
2. ``final_gate_before_execution`` : revalide toutes les conditions critiques au
   moment de l'envoi (symbole, tendance H1, régime, SL/TP, R/R, spread, retest).

Ces fonctions sont **pures au sens du pipeline** : elles ne placent aucun ordre
et ne modifient aucun état. Elles retournent un booléen d'autorisation.

Ce module est extrait de ``application/trading_engine.py`` sans changer le
comportement : la logique, les logs et les valeurs de retour sont identiques.
"""

from __future__ import annotations

from typing import Any

from arty_trading.core.entities import Signal
from arty_trading.core.enums import Direction, LogCategory
from arty_trading.logging.logger import get_logger
from arty_trading.utils.helpers import retest_still_valid_detailed


async def revalidate_before_execution(
    symbol: str, signal: Signal, market_context: Any
) -> bool:
    """Revalidation finale d'un signal juste avant l'exécution.

    Vérifie que le contexte n'a pas changé entre la génération du signal et
    l'envoi de l'ordre. Un signal devenu invalide est rejeté.

    Args:
        symbol: Symbole du trade.
        signal: Signal à revalider.
        market_context: Contexte marché actuel.

    Returns:
        True si le signal est toujours valide, False sinon.
    """
    reval_logger = get_logger(LogCategory.SIGNAL)

    direction_str = "bullish" if signal.direction == Direction.BUY else "bearish"
    current_trend = market_context.master_trend

    reval_logger.info(
        "[REVALIDATION] %s | direction=%s | master_trend=%s",
        symbol, direction_str, current_trend,
    )

    # Garde conservateur : on ne trade QUE si la tendance H1 est claire et
    # directionnelle (bullish/bearish). H1 inconnu / neutre / RANGE / etc.
    # -> rejet, pour ne PAS risquer un trade contre tendance.
    if current_trend not in ("bullish", "bearish"):
        reval_logger.warning(
            "Signal REJETÉ (revalidation) | %s | tendance H1 non directionnelle=%r | "
            "BUY et SELL interdits",
            symbol, current_trend,
        )
        return False

    if current_trend == "bullish" and direction_str == "bearish":
        reval_logger.warning(
            "Signal REJETÉ (revalidation) | %s | H1=BULLISH + SELL → MASTER_TREND_CONFLICT",
            symbol,
        )
        return False

    if current_trend == "bearish" and direction_str == "bullish":
        reval_logger.warning(
            "Signal REJETÉ (revalidation) | %s | H1=BEARISH + BUY → MASTER_TREND_CONFLICT",
            symbol,
        )
        return False

    reval_logger.info(
        "[REVALIDATION] %s | PASS | signal toujours valide",
        symbol,
    )
    return True

async def final_gate_before_execution(
    symbol: str, signal: Signal, market_context: Any, settings: Any
) -> bool:
    """Gate final avant OrderSend.

    Revalide TOUTES les conditions critiques au moment de l'envoi. Si une
    condition n'est plus valide → REJECT, même si elle était valide lors de la
    génération du signal.

    Conditions vérifiées :
    1. Symbole supporté
    2. Tendance H1 toujours alignée
    3. Régime H1 toujours directionnel (pas RANGE/TRANSITION)
    4. SL != entry, TP != entry
    5. R/R minimum
    6. Spread toujours acceptable
    7. Retest toujours frais + rejet encore confirmé

    Args:
        symbol: Symbole du trade.
        signal: Signal à revalider.
        market_context: Contexte marché actuel.
        settings: Configuration globale (``supported_symbols``, profils).

    Returns:
        True si toutes les conditions sont remplies, False sinon.
    """
    gate_logger = get_logger(LogCategory.SIGNAL)

    # 1. Symbole supporté
    if symbol.upper() not in settings.supported_symbols:
        gate_logger.warning("FINAL GATE REJECT | %s | symbole non supporte", symbol)
        return False

    # 2. Tendance H1 toujours alignée (+ garde : H1 inconnu → rejet)
    direction_str = "bullish" if signal.direction == Direction.BUY else "bearish"
    current_trend = market_context.master_trend
    if current_trend not in ("bullish", "bearish"):
        gate_logger.warning(
            "FINAL GATE REJECT | %s | tendance H1 non directionnelle=%r",
            symbol, current_trend,
        )
        return False
    if current_trend == "bullish" and direction_str == "bearish":
        gate_logger.warning("FINAL GATE REJECT | %s | H1=BULLISH + SELL", symbol)
        return False
    if current_trend == "bearish" and direction_str == "bullish":
        gate_logger.warning("FINAL GATE REJECT | %s | H1=BEARISH + BUY", symbol)
        return False

    # 3. Régime H1 toujours directionnel
    regime = getattr(market_context, "regime", "unknown")
    if regime in ("range", "transition"):
        gate_logger.warning("FINAL GATE REJECT | %s | régime=%s", symbol, regime)
        return False

    # 4. SL != entry, TP != entry
    if signal.stop_loss == signal.entry_price or signal.take_profit == signal.entry_price:
        gate_logger.warning("FINAL GATE REJECT | %s | SL ou TP invalide", symbol)
        return False

    # 5. RR minimum
    rr = signal.risk_reward_ratio
    profile = settings.get_instrument_profile(symbol)
    min_rr = profile.min_risk_reward if profile else 2.0
    if rr < min_rr:
        gate_logger.warning("FINAL GATE REJECT | %s | R/R=%.2f < min=%.2f", symbol, rr, min_rr)
        return False

    # 6. Spread toujours acceptable
    current_spread = getattr(market_context, "spread", 0)
    max_spread = profile.max_spread_points if profile else 30
    if current_spread > max_spread:
        gate_logger.warning(
            "FINAL GATE REJECT | %s | spread=%d > max=%d",
            symbol,
            current_spread,
            max_spread,
        )
        return False

    # 7. Retest toujours frais + rejet encore confirmé (anti trade contre-tendance).
    profile = settings.get_instrument_profile(symbol)
    retest_diag = retest_still_valid_detailed(
        getattr(market_context, "ltf_candles", []),
        getattr(market_context, "ltf_smc_data", []),
        direction_str,
        max_age_bars=profile.max_zone_age_bars if profile else 20,
        max_distance_atr_mult=profile.retest_atr_mult if profile else 1.0,
        symbol=symbol,
    )
    if not retest_diag.valid:
        gate_logger.warning(
            "FINAL GATE REJECT | %s | retest périmé/invalidé "
            "(direction=%s, reason=%s, zones_in_direction=%d, "
            "latest_age=%s bars, latest_distance=%s, max_distance=%s, "
            "atr=%s, max_age_bars=%s, retest_atr_mult=%s)",
            symbol,
            direction_str,
            retest_diag.reason,
            retest_diag.zones_in_direction,
            retest_diag.zone_age_bars,
            retest_diag.distance_to_zone,
            retest_diag.max_distance,
            retest_diag.atr,
            retest_diag.max_zone_age_bars,
            retest_diag.retest_atr_mult,
        )
        # Log diagnostic détaillé (Phase 2.1 — instrumentation)
        gate_logger.warning(
            "RETEST DETAILS | symbol=%s direction=%s zone_type=%s zone_id=%s "
            "zone_age_bars=%s max_zone_age_bars=%s age_condition=%s "
            "zone_distance=%s max_distance=%s distance_condition=%s "
            "retest_detected=%s retest_confirmed=%s confirmation_condition=%s "
            "zones_in_direction=%s zone_direction=%s zone_consumed=%s",
            symbol,
            direction_str,
            retest_diag.zone_type,
            retest_diag.zone_id,
            retest_diag.zone_age_bars,
            retest_diag.max_zone_age_bars,
            (
                retest_diag.zone_age_bars is not None
                and retest_diag.zone_age_bars <= retest_diag.max_zone_age_bars
            ),
            retest_diag.distance_to_zone,
            retest_diag.max_distance,
            (
                retest_diag.distance_to_zone is not None
                and retest_diag.distance_to_zone <= retest_diag.max_distance
            ),
            retest_diag.retest_detected,
            retest_diag.retest_confirmed,
            retest_diag.retest_confirmed,
            retest_diag.zones_in_direction,
            retest_diag.zone_direction,
            retest_diag.zone_consumed,
        )
        return False

    gate_logger.info(
        "[FINAL GATE] %s | PASS | toutes les conditions critiques remplies",
        symbol,
    )
    return True
