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

from datetime import UTC
from typing import Any

from arty_trading.config.settings import OBQualitySettings, Settings
from arty_trading.core.entities import Candle
from arty_trading.core.enums import Direction, LogCategory
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
from arty_trading.modules.smc.setup_classifier import classify_setup_type

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
    ltf_candles: list[Candle] = getattr(market_context, "ltf_candles", []) or []
    atr = float(getattr(market_context, "atr", 0))
    profile = settings.get_instrument_profile(symbol)
    max_distance_atr_mult = profile.retest_atr_mult if profile else 1.0
    max_zone_age_bars = profile.max_zone_age_bars if profile else 20
    current_price = float(ltf_candles[-1].close) if ltf_candles else None

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

        # Ne créer un setup que pour les concepts de zone (FVG, Order Block)
        # et uniquement dans la direction de la tendance maître H1.
        if concept not in ("fair_value_gap", "order_block"):
            continue
        if direction_str not in ("bullish", "bearish"):
            continue
        if htf_trend not in ("bullish", "bearish"):
            continue
        if direction_str != htf_trend:
            continue

        zone_index = detection.get("index", 0)
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

        # Phase 12 : un OB doit être de Grade A/B pour devenir un setup.
        quality = None
        if concept == "order_block" and ob_grading_enabled and ob_settings is not None:
            quality = assess_order_block_quality(
                detection,
                atr=atr,
                htf_trend=htf_trend,
                smc_data=setup_smc_data,
                zone_index=int(zone_index),
                bars_since_zone=(
                    max(0, len(ltf_candles) - 1 - int(zone_index)) if ltf_candles else None
                ),
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

        # Éviter les doublons
        if tracker.is_duplicate(symbol, direction_enum, concept, zone_index):
            continue

        # Créer le setup avec les données de marché
        setup = tracker.create_setup(
            symbol=symbol,
            direction=direction_enum,
            zone_concept=concept,
            zone_index=zone_index,
            zone_price=zone_price,
            zone_high=float(zone_top),
            zone_low=float(zone_bottom),
            atr=atr,
            htf_trend=htf_trend,
            ttl_bars=max_zone_age_bars,
        )
        if setup is not None:
            setup_type = classify_setup_type(
                zone_concept=concept,
                direction=direction_str,
                smc_data=setup_smc_data,
                zone_index=zone_index,
            )
            setup.metadata["setup_type"] = setup_type
            setup.metadata["setup_tf_trend"] = setup_trend
            grade_label = "-"
            if quality is not None:
                setup.metadata.update(quality.to_dict())
                # Confirmation M5 évaluée dès la création (rafraîchie à chaque cycle).
                m5 = _m5_confirmation_for_setup(setup, ltf_candles, atr, ob_settings)
                setup.metadata.update(m5.to_dict())
                grade_label = f"{quality.grade.value}({quality.score})"
                graded_setups += 1
            logger.info(
                "[SETUP DETECTED] %s | %s | zone=%.5f-%.5f | concept=%s idx=%d id=%s | "
                "type=%s | grade=%s | trend=%s atr=%.6f",
                symbol, direction_str, float(zone_bottom), float(zone_top),
                concept, zone_index, setup.setup_id, setup_type, grade_label,
                htf_trend, atr,
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
    return evaluate_m5_confirmation(
        candles,
        direction,
        float(setup.zone_high),
        float(setup.zone_low),
        atr=atr,
        lookback_bars=config.m5_confirmation_lookback_bars,
        min_rejection_ratio=config.m5_min_rejection_ratio,
        require_displacement=config.m5_require_displacement,
        displacement_atr_mult=config.m5_displacement_atr_mult,
    )


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
