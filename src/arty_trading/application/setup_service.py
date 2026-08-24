"""Mise à jour des setups SMC à partir du contexte marché (service partagé).

Extrait de ``application/trading_engine._update_setups`` sans changer le
comportement, afin que le backtest multi-timeframe utilise EXACTEMENT la même
logique de création/évaluation des setups que le chemin live.
"""

from __future__ import annotations

from typing import Any

from arty_trading.config.settings import Settings
from arty_trading.core.entities import Candle
from arty_trading.core.enums import LogCategory
from arty_trading.logging.logger import get_logger
from arty_trading.modules.smc import SetupState, SetupTracker
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
        from datetime import datetime, timezone

        tracker.expire_old_setups(
            symbol, current_time=datetime.now(timezone.utc)
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

        from arty_trading.core.enums import Direction as DirEnum

        direction_enum = DirEnum.BUY if direction_str == "bullish" else DirEnum.SELL
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
            logger.info(
                "[SETUP DETECTED] %s | %s | zone=%.5f-%.5f | concept=%s idx=%d id=%s | type=%s | trend=%s atr=%.6f",
                symbol, direction_str, float(zone_bottom), float(zone_top),
                concept, zone_index, setup.setup_id, setup_type, htf_trend, atr,
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

    active = tracker.get_active_setups(symbol)
    logger.info(
        "SETUPS | %s | actifs=%d | expirés/récemment traités=%d",
        symbol,
        len(active),
        sum(1 for s in tracker._setups.get(symbol.upper(), [])
            if s.state in (SetupState.CONSUMED, SetupState.SETUP_EXPIRED, SetupState.INVALIDATED)),
    )
