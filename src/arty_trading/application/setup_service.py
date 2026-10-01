"""Mise à jour des setups SMC à partir du contexte marché (service partagé).

Extrait de ``application/trading_engine._update_setups`` sans changer le
comportement, afin que le backtest multi-timeframe utilise EXACTEMENT la même
logique de création/évaluation des setups que le chemin live.

Phase 12 : quand ``OB_QUALITY_ENABLED=true``, chaque Order Block est noté
(Grade A/B/C/D) avant création du setup — seuls les grades >= ``OB_MIN_GRADE``
deviennent des setups — et la confirmation M5 est recalculée à chaque cycle
pour les setups OB actifs (``ob_m5_confirmed`` dans les métadonnées).
"""

from __future__ import annotations

from datetime import UTC, timedelta
from typing import Any

from arty_trading.config.settings import OBQualitySettings, Settings
from arty_trading.core.entities import Candle
from arty_trading.core.enums import Direction, LogCategory, TimeFrame
from arty_trading.logging.logger import get_logger
from arty_trading.modules.smc import Setup, SetupState, SetupTracker
from arty_trading.modules.smc.m5_confirmation import (
    M5ConfirmationResult,
    evaluate_m5_confirmation,
)
from arty_trading.modules.smc.ob_quality import (
    assess_order_block_quality,
    grade_meets_min,
)
from arty_trading.modules.smc.setup_classifier import (
    classify_setup_type,
    qualify_setup_type,
)
from arty_trading.modules.smc.setup_tracker import MarketPhase, MarketPhaseType

logger = get_logger(LogCategory.SYSTEM)


def update_setups_from_market_context(
    tracker: SetupTracker,
    symbol: str,
    market_context: Any,
    settings: Settings,
) -> None:
    """Met à jour le state machine des setups pour le symbole.

    Identique à ``TradingEngine._update_setups`` (le moteur live délègue ici).

    NOTE : ``expire_old_setups`` est appelé avec un ``current_time`` explicite
    (dernière bougie LTF clôturée, UTC aware). Sans cela, le tracker compare
    un ``expires_at`` aware (créé par ``create_setup``) à un ``utcnow()``
    naïf → ``TypeError``. En backtest, l'heure « réelle » serait de toute
    façon fausse : l'heure de la dernière bougie est la référence correcte.
    """
    # Heure de référence : clôture de la dernière bougie LTF connue (UTC).
    ltf_candles_ref: list[Candle] = getattr(market_context, "ltf_candles", []) or []
    ref_time = ltf_candles_ref[-1].time if ltf_candles_ref else None
    if ref_time is not None:
        tracker.expire_old_setups(symbol, current_time=ref_time)
    else:
        from datetime import datetime

        tracker.expire_old_setups(
            symbol, current_time=datetime.now(UTC)
        )

    # --- Récupérer les paramètres de marché ---
    smc_data: list[dict[str, Any]] = getattr(market_context, "ltf_smc_data", []) or []
    # Phase 3 : les setups proviennent du timeframe de setup (M15) quand
    # disponible ; sinon fallback M5 (rétro-compatibilité). Le M5 reste
    # le timeframe de confirmation d'entrée (tracker.evaluate).
    setup_smc_data: list[dict[str, Any]] = (
        getattr(market_context, "setup_smc_data", None) or smc_data
    )
    setup_trend: str = getattr(market_context, "setup_trend", "neutral")
    htf_trend: str = getattr(market_context, "master_trend", "neutral")
    h4_trend: str = getattr(market_context, "h4_trend", "unknown")
    ltf_candles: list[Candle] = getattr(market_context, "ltf_candles", []) or []
    setup_candles: list[Candle] = (
        getattr(market_context, "setup_candles", None) or ltf_candles
    )
    setup_timeframe = getattr(
        market_context,
        "setup_tf",
        setup_candles[-1].timeframe if setup_candles else TimeFrame.M5,
    )
    atr = float(getattr(market_context, "atr", 0))
    profile = settings.get_instrument_profile(symbol)
    max_distance_atr_mult = profile.retest_atr_mult if profile else 1.0
    max_zone_age_bars = profile.max_zone_age_bars if profile else 20
    current_price = float(ltf_candles[-1].close) if ltf_candles else None
    tracker.expire_phases(symbol, ref_time)
    _record_observed_phases(
        tracker,
        symbol,
        market_context,
        setup_smc_data,
        setup_candles,
        setup_timeframe,
        max_zone_age_bars,
    )

    # Phase 12 : notation de qualité des Order Blocks (désactivée par défaut).
    ob_settings: OBQualitySettings | None = getattr(settings, "ob_quality", None)
    ob_grading_enabled = bool(ob_settings is not None and ob_settings.enabled)
    graded_setups = 0
    rejected_by_grade = 0

    # --- Créer des setups à partir des zones SMC détectées ---
    # Phase 3 : itération sur les détections du timeframe de setup (M15).
    for detection in setup_smc_data:
        concept = detection.get("concept", "")
        direction_str = detection.get("direction", "")

        # Une zone est un candidat observable, jamais un setup exploitable à elle seule.
        if concept not in ("fair_value_gap", "order_block"):
            continue
        if direction_str not in ("bullish", "bearish"):
            continue
        if htf_trend not in ("bullish", "bearish") or direction_str != htf_trend:
            continue

        try:
            zone_index = int(detection.get("index", 0))
        except (TypeError, ValueError):
            continue
        details = detection.get("details", {})

        if concept == "fair_value_gap":
            zone_top = details.get("gap_top")
            zone_bottom = details.get("gap_bottom")
        elif concept == "order_block":
            zone_top = details.get("ob_top")
            zone_bottom = details.get("ob_bottom")
        else:
            continue

        if zone_top is None or zone_bottom is None:
            continue

        # The detectors anchor an OB/FVG to its origin candle; use the candle
        # that completed its displacement/three-bar pattern as the creation time.
        confirmation_index = zone_index
        if concept == "order_block":
            confirmation_index = int(details.get("displacement_index", zone_index))
        elif concept == "fair_value_gap":
            confirmation_index = zone_index + 1
        if confirmation_index < 0 or confirmation_index >= len(setup_candles):
            continue
        zone_time = setup_candles[confirmation_index].time

        # Phase 12 : un OB doit être de Grade A/B pour devenir un setup.
        quality = None
        if concept == "order_block" and ob_grading_enabled and ob_settings is not None:
            quality = assess_order_block_quality(
                detection,
                atr=atr,
                htf_trend=htf_trend,
                smc_data=setup_smc_data,
                zone_index=int(zone_index),
                bars_since_zone=max(0, len(setup_candles) - 1 - confirmation_index)
                if setup_candles
                else None,
                settings=ob_settings,
            )
            if not grade_meets_min(quality.grade, ob_settings.min_grade):
                rejected_by_grade += 1
                logger.info(
                    "[OB REJECTED] %s | %s | grade=%s score=%d < min=%s | "
                    "zone=%.5f-%.5f idx=%d | %s",
                    symbol, direction_str, quality.grade.value, quality.score,
                    ob_settings.min_grade, float(zone_bottom), float(zone_top),
                    int(zone_index), " | ".join(quality.reasons),
                )
                continue

        direction_enum = Direction.BUY if direction_str == "bullish" else Direction.SELL
        zone_price = (float(zone_top) + float(zone_bottom)) / 2.0

        setup_index = confirmation_index
        setup = next(
            (
                active
                for active in tracker.get_active_setups(symbol)
                if active.direction == direction_enum
                and active.zone_concept == concept
                and active.zone_index == setup_index
            ),
            None,
        )
        if setup is None:
            setup = tracker.create_setup(
                symbol=symbol,
                direction=direction_enum,
                zone_concept=concept,
                zone_index=setup_index,
                zone_price=zone_price,
                zone_high=float(zone_top),
                zone_low=float(zone_bottom),
                atr=atr,
                htf_trend=htf_trend,
                ttl_bars=max_zone_age_bars,
                source_timeframe=setup_timeframe,
                created_at=zone_time,
            )

        setup.source_timeframe = setup_timeframe
        m5 = _m5_confirmation_for_setup(
            setup, ltf_candles, atr, ob_settings, setup_timeframe=setup_timeframe
        )
        qualification = qualify_setup_type(
            direction=direction_str,
            smc_data=setup_smc_data,
            zone_index=setup_index,
            h4_trend=h4_trend,
            h1_trend=htf_trend,
            m5_confirmed=m5.confirmed,
            m5_retested=m5.zone_touched,
        )
        setup.setup_type = qualification.setup_type
        setup.confirmations = {
            "m5": {
                **m5.to_dict(),
                "reasons": list(m5.reasons),
            },
            "required": ["post_zone_retest", "directional_rejection"],
        }
        setup.no_trade_reasons = list(qualification.reasons)
        setup.invalidation_price = (
            float(zone_bottom) - atr * (profile.sl_buffer_atr_mult if profile else 0.5)
            if direction_enum == Direction.BUY
            else float(zone_top) + atr * (profile.sl_buffer_atr_mult if profile else 0.5)
        )
        setup.stop_loss = setup.invalidation_price
        target_concepts = {"equal_high", "equal_low", "buy_side_liquidity", "sell_side_liquidity"}
        target_levels = {
            float(item["price"])
            for item in (*setup_smc_data, *smc_data)
            if item.get("concept") in target_concepts
            and item.get("price") is not None
            and (
                float(item["price"]) > zone_price
                if direction_enum == Direction.BUY
                else float(item["price"]) < zone_price
            )
        }
        setup.liquidity_targets = sorted(
            target_levels,
            reverse=direction_enum == Direction.SELL,
        )
        setup.metadata.update(
            {
                "setup_type": qualification.setup_type.value,
                "setup_qualification_required": True,
                "setup_classification": classify_setup_type(
                    zone_concept=concept,
                    direction=direction_str,
                    smc_data=setup_smc_data,
                    zone_index=setup_index,
                ),
                "setup_tf_trend": setup_trend,
                "h4_trend": h4_trend,
                "h1_trend": htf_trend,
                "setup_evidence": list(qualification.evidence),
                "no_trade_reasons": list(qualification.reasons),
                "m5_confirmation": m5.to_dict(),
            }
        )
        phase_timestamp = ltf_candles[-1].time if ltf_candles else zone_time
        is_qualified = qualification.setup_type.value != "NO_TRADE"
        for phase_type in qualification.phases:
            phase = MarketPhase(
                phase_type=phase_type,
                timestamp=phase_timestamp,
                direction=direction_enum,
                timeframe=(
                    TimeFrame.M5
                    if phase_type == MarketPhaseType.RETRACEMENT
                    else setup_timeframe
                ),
                levels={"zone_low": float(zone_bottom), "zone_high": float(zone_top)},
                evidence=qualification.evidence or qualification.reasons,
                valid=is_qualified,
                expires_at=setup.expires_at,
                invalid_reason=qualification.reasons[0] if qualification.reasons else None,
            )
            setup.phase = phase
            if not any(
                existing.phase_type == phase_type
                and existing.timestamp == phase_timestamp
                for existing in setup.phases
            ):
                setup.phases.append(phase)
            tracker.record_phase(symbol, phase)

        if quality is not None:
            setup.metadata.update(quality.to_dict())
            graded_setups += 1
        logger.info(
            "[SETUP QUALIFICATION] %s | %s | type=%s | zone=%.5f-%.5f | id=%s | "
            "H4=%s H1=%s | M5=%s | reasons=%s",
            symbol, direction_str, setup.setup_type.value, float(zone_bottom),
            float(zone_top), setup.setup_id, h4_trend, htf_trend, m5.confirmation_type,
            ",".join(qualification.reasons) or "-",
        )

    # --- Évaluer les setups actifs (transitions de state machine) ---
    if ltf_candles:
        transitions = tracker.evaluate(
            symbol=symbol,
            candles=ltf_candles,
            smc_data=smc_data,
            htf_trend=htf_trend,
            atr=atr,
            max_distance_atr_mult=max_distance_atr_mult,
            max_zone_age_bars=max_zone_age_bars,
            current_price=current_price,
        )
        for t in transitions:
            logger.info(
                "[SETUP TRANSITION] %s | %s | %s → %s | reason=%s",
                symbol, t.setup_id, t.state.value, t.state.value, "",
            )

    # Phase 12 : rafraîchir la confirmation M5 des setups OB actifs.
    if ob_settings is not None and ob_settings.enabled and ltf_candles:
        _refresh_ob_m5_confirmation(tracker, symbol, ltf_candles, atr, ob_settings)

    active = tracker.get_active_setups(symbol)
    logger.info(
        "SETUPS | %s | actifs=%d | expirés/récemment traités=%d",
        symbol,
        len(active),
        sum(1 for s in tracker._setups.get(symbol.upper(), [])
            if s.state in (SetupState.CONSUMED, SetupState.SETUP_EXPIRED, SetupState.INVALIDATED)),
    )

    if ob_settings is not None and ob_settings.enabled:
        logger.info(
            "OB QUALITY | %s | ob_notés=%d | rejetés(grade<%s)=%d",
            symbol,
            graded_setups,
            ob_settings.min_grade,
            rejected_by_grade,
        )


def _m5_confirmation_for_setup(
    setup: Setup,
    candles: list[Candle],
    atr: float,
    settings: OBQualitySettings | None,
    *,
    setup_timeframe: TimeFrame | str | None = None,
) -> M5ConfirmationResult:
    """Évalue la confirmation M5 d'un setup dont la zone est un Order Block.

    Args:
        setup: Setup suivi (zone OB).
        candles: Bougies M5 clôturées.
        atr: ATR courant du M5.
        settings: Paramètres de qualité OB (``None`` → valeurs par défaut).

    Returns:
        ``M5ConfirmationResult`` — non confirmé si la zone est incomplète.
    """
    config = settings or OBQualitySettings()
    if setup.zone_high is None or setup.zone_low is None:
        return M5ConfirmationResult(
            False, "invalid_zone", False, None, 0.0, 0.0, ("missing_zone_bounds",)
        )
    direction = "bullish" if setup.direction == Direction.BUY else "bearish"
    timeframe = setup_timeframe or setup.source_timeframe
    zone_available_at = setup.created_at + timedelta(
        minutes=_timeframe_minutes(timeframe)
    )
    post_zone_candles = [candle for candle in candles if candle.time >= zone_available_at]
    return evaluate_m5_confirmation(
        post_zone_candles,
        direction,
        float(setup.zone_high),
        float(setup.zone_low),
        atr=atr,
        lookback_bars=config.m5_confirmation_lookback_bars,
        min_rejection_ratio=config.m5_min_rejection_ratio,
        require_displacement=config.m5_require_displacement,
        displacement_atr_mult=config.m5_displacement_atr_mult,
    )


def _record_observed_phases(
    tracker: SetupTracker,
    symbol: str,
    market_context: Any,
    detections: list[dict[str, Any]],
    candles: list[Candle],
    timeframe: TimeFrame | str,
    ttl_bars: int,
) -> None:
    """Translate detector evidence into independent, expiring phase observations."""
    from datetime import timedelta

    duration = timedelta(minutes=_timeframe_minutes(timeframe))
    valid_directions = {"bullish": Direction.BUY, "bearish": Direction.SELL}
    phase_map = {
        "liquidity_sweep": MarketPhaseType.LIQUIDITY_SWEEP,
        "bos": MarketPhaseType.STRUCTURE_SHIFT,
        "break_of_structure": MarketPhaseType.STRUCTURE_SHIFT,
        "internal_bos": MarketPhaseType.STRUCTURE_SHIFT,
        "external_bos": MarketPhaseType.STRUCTURE_SHIFT,
        "choch": MarketPhaseType.STRUCTURE_SHIFT,
        "change_of_character": MarketPhaseType.STRUCTURE_SHIFT,
        "mss": MarketPhaseType.STRUCTURE_SHIFT,
        "market_structure_shift": MarketPhaseType.STRUCTURE_SHIFT,
    }
    phase_observations: list[MarketPhase] = []
    if getattr(market_context, "regime", None) == "range" and candles:
        phase_observations.append(
            MarketPhase(
                MarketPhaseType.RANGE_DETECTED,
                candles[-1].time + duration,
                None,
                timeframe,
                evidence=("market_structure_regime=range",),
                expires_at=candles[-1].time + duration * ttl_bars,
            )
        )

    for detection in detections:
        try:
            index = int(detection.get("index", -1))
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= len(candles):
            continue
        concept = str(detection.get("concept", "")).lower()
        phase_type = phase_map.get(concept)
        details = detection.get("details", {})
        direction = valid_directions.get(detection.get("direction"))
        event_time = candles[index].time + duration
        levels = {
            key: float(details[key])
            for key in ("swept_level", "broken_level", "sweep_high", "sweep_low")
            if details.get(key) is not None
        }
        if phase_type is not None:
            phase_observations.append(
                MarketPhase(
                    phase_type,
                    event_time,
                    direction,
                    timeframe,
                    levels=levels,
                    evidence=(concept, f"index={index}"),
                    expires_at=event_time + duration * ttl_bars,
                )
            )
            if (
                phase_type == MarketPhaseType.STRUCTURE_SHIFT
                and direction is not None
                and getattr(market_context, "h4_trend", "unknown")
                == getattr(market_context, "master_trend", "neutral")
                == detection.get("direction")
            ):
                phase_observations.append(
                    MarketPhase(
                        MarketPhaseType.CONTINUATION,
                        event_time,
                        direction,
                        timeframe,
                        levels=levels,
                        evidence=(concept, "h4_h1_aligned"),
                        expires_at=event_time + duration * ttl_bars,
                    )
                )
        displacement_index = details.get("displacement_index")
        if concept == "order_block" and displacement_index is not None:
            try:
                displacement_index = int(displacement_index)
            except (TypeError, ValueError):
                continue
            if 0 <= displacement_index < len(candles):
                phase_time = candles[displacement_index].time + duration
                phase_observations.append(
                    MarketPhase(
                        MarketPhaseType.DISPLACEMENT,
                        phase_time,
                        direction,
                        timeframe,
                        levels=levels,
                        evidence=("order_block_displacement",),
                        expires_at=phase_time + duration * ttl_bars,
                    )
                )

    for phase in phase_observations:
        tracker.record_phase(symbol, phase)


def _timeframe_minutes(timeframe: TimeFrame | str | None) -> int:
    """Return the source candle duration used to start post-zone confirmation."""
    value = timeframe.value if isinstance(timeframe, TimeFrame) else str(timeframe or "M5")
    return {
        "M1": 1,
        "M5": 5,
        "M15": 15,
        "M30": 30,
        "H1": 60,
        "H4": 240,
        "D1": 1440,
    }.get(value.upper(), 5)


def _refresh_ob_m5_confirmation(
    tracker: SetupTracker,
    symbol: str,
    candles: list[Candle],
    atr: float,
    settings: OBQualitySettings,
) -> None:
    """Met à jour ``ob_m5_confirmed`` pour chaque setup OB noté (Grade A/B).

    N'affecte pas la state machine du tracker : la décision finale reste prise
    par le ``SignalGenerator`` via ``evaluate_setup_ob_gate``.
    """
    for setup in tracker.get_active_setups(symbol):
        if setup.zone_concept != "order_block" or "ob_grade" not in setup.metadata:
            continue
        result = _m5_confirmation_for_setup(setup, candles, atr, settings)
        setup.metadata.update(result.to_dict())
        setup.confirmations["m5"] = {
            **result.to_dict(),
            "reasons": list(result.reasons),
        }
