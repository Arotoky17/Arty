"""
Utilitaires de calcul pour le trading Forex.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from arty_trading.core.entities import Candle

# Symboles JPY : 1 pip = 0.01, autres paires : 1 pip = 0.0001
JPY_PAIRS = {"USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY", "NZDJPY"}


def get_pip_size(symbol: str) -> Decimal:
    """Retourne la taille d'un pip pour le symbole."""
    symbol = symbol.upper().replace("/", "")
    if "JPY" in symbol and not symbol.startswith("XAU"):
        return Decimal("0.01")
    if symbol.startswith("XAU"):
        return Decimal("0.01")
    return Decimal("0.0001")


def get_price_digits(symbol: str) -> int:
    """Retourne le nombre de décimales pour l'affichage des prix."""
    symbol = symbol.upper()
    if "JPY" in symbol or symbol.startswith("XAU"):
        return 3
    return 5


def round_price(price: float | Decimal, symbol: str) -> Decimal:
    """Arrondit un prix selon les digits du symbole."""
    digits = get_price_digits(symbol)
    quantize_str = "0." + "0" * (digits - 1) + "1" if digits > 0 else "1"
    return Decimal(str(price)).quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)


def calculate_pips(symbol: str, price_from: float, price_to: float) -> float:
    """Calcule la distance en pips entre deux prix."""
    pip_size = float(get_pip_size(symbol))
    return abs(price_to - price_from) / pip_size


def pip_value(symbol: str, lot_size: float = 1.0, account_currency: str = "USD") -> float:
    """
    Estime la valeur monétaire d'un pip (approximation standard).
    Pour un calcul exact, utiliser les infos du symbole MT5.
    """
    _ = account_currency  # réservé pour calcul multi-devises futur
    symbol = symbol.upper()
    if symbol.startswith("XAU"):
        return lot_size * 1.0  # ~1 USD/pip/lot standard pour l'or
    if "JPY" in symbol:
        # 1 lot = 100 000 unités, 1 pip = 0.01 → 100 000 * 0.01 = 1 000 JPY.
        # Conversion JPY→USD ≈ taux arbitré ~100 → ≈ 10 USD/pip/lot
        # (approximation : le taux exact est fourni par MT5 en réel).
        return lot_size * 100000 * 0.01 / 100
    return lot_size * 10.0  # 10 USD/pip pour 1 lot standard EURUSD


def calculate_atr(candles: list[Candle], period: int = 14) -> Decimal:
    """
    Calcule l'Average True Range (ATR) sur les bougies fournies.

    L'ATR mesure la volatilité du marché et permet d'adapter
    les filtres de distance à la volatilité actuelle.

    Args:
        candles: Liste des bougies (du plus ancien au plus récent)
        period: Période de calcul (défaut 14)

    Returns:
        Valeur ATR en prix (pas en pips)
    """
    if len(candles) < period + 1:
        return Decimal("0")

    true_ranges: list[Decimal] = []
    for i in range(1, len(candles)):
        current = candles[i]
        previous = candles[i - 1]
        tr = max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return Decimal("0")

    # Première moyenne simple
    atr = sum(true_ranges[:period]) / period
    # Puis moyenne lissée (Wilder)
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period

    return atr


def calculate_atr_sliding(candles: list[Candle], period: int = 14) -> Decimal:
    """
    ATR par moyenne simple glissante sur ``period`` bougies (utilisé par le
    DecisionEngine pour son score, volontairement DIFFÉRENT du ATR Wilder).

    ATTENTION : cette formule est distincte de ``calculate_atr`` (Wilder).
    Les deux produisent des valeurs différentes. Ne pas les fusionner : cela
    changerait silencieusement le score du ``DecisionEngine``.

    Args:
        candles: Liste des bougies (du plus ancien au plus récent)
        period: Nombre de transitions considérées dans la fenêtre glissante

    Returns:
        Valeur ATR en prix (moyenne simple), ou ``0`` si impossible.
    """
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


def is_fresh_retest(
    candles: list[Candle],
    smc_data: list[dict[str, Any]],
    max_age_bars: int = 20,
    max_distance_atr_mult: float = 1.0,
) -> bool:
    """
    Vérifie si un retest de zone SMC est considéré comme frais et valide.

    .. note::
        Ne confondez pas avec ``retest_still_valid`` (gate final d'exécution).
        Cette fonction n'a **pas** de notion de direction et **n'exige pas**
        de rejection confirmée : elle retourne ``True`` dès qu'une zone (FVG/OB)
        est fraîche et proche du prix. Elle est utilisée par le script de
        calibration (``scripts/calibrate_retest_filter.py``), pas par le moteur.

    Un retest est frais si :
    - La zone a été détectée il y a au plus ``max_age_bars`` bougies
    - Le prix est proche de la zone (dans un multiple de l'ATR)

    Args:
        candles: Liste des bougies OHLCV
        smc_data: Détections SMC
        max_age_bars: Âge maximum de la zone en bougies (défaut 20)
        max_distance_atr_mult: Distance maximum en multiple de l'ATR (défaut 1.0)

    Returns:
        True si un retest frais est détecté
    """
    if not candles or not smc_data:
        return False

    last_candle = candles[-1]
    atr = calculate_atr(candles)
    if atr == 0:
        return False

    max_distance = atr * Decimal(str(max_distance_atr_mult))

    for detection in smc_data:
        age = len(candles) - 1 - detection.get("index", 0)
        if age > max_age_bars:
            continue

        details = detection.get("details", {})
        concept = detection.get("concept")

        if concept == "fair_value_gap":
            top = details.get("gap_top")
            bottom = details.get("gap_bottom")
        elif concept == "order_block":
            top = details.get("ob_top")
            bottom = details.get("ob_bottom")
        else:
            continue

        if top is None or bottom is None:
            continue

        zone_mid = (float(top) + float(bottom)) / 2.0
        distance = abs(float(last_candle.close) - zone_mid)
        if distance <= float(max_distance):
            return True

    return False


def retest_still_valid(
    candles: list[Candle],
    smc_data: list[dict[str, Any]],
    direction: str,
    max_age_bars: int = 20,
    max_distance_atr_mult: float = 1.0,
) -> bool:
    """
    Re-validate un retest au moment de l'exécution (gate final).

    Vérifie qu'il existe encore une zone SMC (FVG ou Order Block) dans le sens
    du signal qui est :
    - *fraîche* (détectée il y a au plus ``max_age_bars`` bougies),
    - *proche* du prix courant (à moins de ``max_distance_atr_mult`` × ATR),
    - *rejetée* dans le bon sens (la dernière bougie confirme).

    Sémantique de sécurité : on ne bloque que si on peut prouver qu'aucune zone
    du bon sens n'est plus valide. Si les données sont absentes ou qu'aucune zone
    du bon sens n'existe, on ne bloque pas (le signal a déjà été validé à la
    génération par une rejection confirmée) → évite de tout rejeter en masse.

    .. note::
        Distinct de ``is_fresh_retest`` (utilisé par le script de calibration) :
        ici la **direction** est requise et une **rejection confirmée** dans le
        bon sens est exigée. Ne pas fusionner les deux : sémantiques différentes.

    Args:
        candles: Bougies du timeframe d'entrée (M5).
        smc_data: Détections SMC sur le timeframe d'entrée.
        direction: "bullish" ou "bearish" (sens du signal).
        max_age_bars: Âge maximum d'une zone pour être considérée fraîche.
        max_distance_atr_mult: Distance maximum prix−zone en multiple d'ATR.

    Returns:
        True si le retest est encore valide (ou non évaluable), False si une
        zone existe mais est devenue périmée / hors de portée.
    """
    if not candles or not smc_data:
        return True

    last = candles[-1]
    atr = calculate_atr(candles)
    if atr == 0:
        return False

    max_distance = atr * Decimal(str(max_distance_atr_mult))
    zones_in_direction = 0

    for detection in smc_data:
        if detection.get("direction") != direction:
            continue

        concept = detection.get("concept")
        details = detection.get("details", {})
        if concept == "fair_value_gap":
            top = details.get("gap_top")
            bottom = details.get("gap_bottom")
        elif concept == "order_block":
            top = details.get("ob_top")
            bottom = details.get("ob_bottom")
        else:
            continue

        if top is None or bottom is None:
            continue
        zones_in_direction += 1

        # 1. Fraîcheur de la zone
        age = len(candles) - 1 - detection.get("index", 0)
        if age > max_age_bars:
            continue

        # 2. Proximité du prix courant — distance à la FRONTIÈRE de la zone
        # (pas au milieu). Si le prix est à l'intérieur de la zone, la distance
        # est 0 : la zone est "touchée" et donc proche. On mesure la distance au
        # point de la zone le plus proche pour éviter de rejeter les zones larges
        # (gap FVG important) dont le milieu est loin du prix de confirmation.
        close = float(last.close)
        f_top = float(top)
        f_bottom = float(bottom)
        if close >= f_bottom and close <= f_top:
            distance = 0.0
        elif close > f_top:
            distance = close - f_top
        else:
            distance = f_bottom - close
        if distance > float(max_distance):
            continue

        # 3. Rejection confirmée par la dernière bougie (sens du signal)
        if direction == "bullish":
            if last.is_bullish and float(last.close) >= float(bottom):
                return True
        else:
            if not last.is_bullish and float(last.close) <= float(top):
                return True

    # On ne bloque que si des zones du bon sens existent mais ne sont plus
    # fraîches / en portée → la retest est devenue invalide.
    return zones_in_direction == 0


# =============================================================================
# Diagnostic détaillé du retest (Phase 3A — audit instrumentation)
# =============================================================================
# Codes de raison exactement dérivés de la logique existante dans
# ``retest_still_valid``. Aucun code n'est inventé.


@dataclass
class RetestDiagnostic:
    """Résultat détaillé du retest pour le diagnostic d'exécution.

    Capture les mêmes informations que ``retest_still_valid`` mais expose la
    cause exacte du rejet (si un rejet existe).
    """

    valid: bool
    reason: str
    symbol: str = ""
    direction: str = ""
    last_candle_time: datetime | None = None
    atr: float = 0.0
    max_distance: float = 0.0
    max_zone_age_bars: int = 20
    retest_atr_mult: float = 1.0
    zone_type: str | None = None
    zone_id: str | None = None
    zone_created_index: int | None = None
    zone_age_bars: int | None = None
    distance_to_zone: float | None = None
    zone_consumed: bool | None = None
    zone_direction: str | None = None
    retest_detected: bool = False
    retest_confirmed: bool = False
    zones_in_direction: int = 0
    zone_details: list[dict[str, Any]] = field(default_factory=list)


def retest_still_valid_detailed(
    candles: list[Candle],
    smc_data: list[dict[str, Any]],
    direction: str,
    max_age_bars: int = 20,
    max_distance_atr_mult: float = 1.0,
    symbol: str = "",
) -> RetestDiagnostic:
    """
    Variante diagnostique de ``retest_still_valid``.

    Produit exactement le même résultat ``valid`` que la fonction originale,
    mais expose **la cause exacte** du rejet et toutes les données capturables.

    Raisons possibles (dérivées strictement de la logique du code) :

    - ``PASS`` — retest valide (zone fraîche, proche, rejet confirmé).
    - ``NO_ZONES_IN_DIRECTION`` — aucune zone FVG/OB du bon sens n'existe
      (la fonction originale ne bloque pas dans ce cas → valid=True).
    - ``ATR_ZERO`` — ATR calculé à 0, impossible d'évaluer la distance.
    - ``ZONE_TOO_OLD`` — zone(s) du bon sens existent mais toutes sont plus
      anciennes que ``max_age_bars``.
    - ``ZONE_TOO_FAR`` — zone(s) du bon sens existent, certaines sont fraîches
      mais aucune n'est assez proche du prix (distance > max_distance).
    - ``NO_RETEST_CONFIRMATION`` — zone(s) du bon sens fraîche(s) et proche(s)
      mais la dernière bougie ne confirme pas le rejet dans le bon sens.

    Args:
        candles: Bougies du timeframe d'entrée (M5).
        smc_data: Détections SMC sur le timeframe d'entrée.
        direction: "bullish" ou "bearish" (sens du signal).
        max_age_bars: Âge maximum d'une zone pour être considérée fraîche.
        max_distance_atr_mult: Distance maximum prix−zone en multiple d'ATR.
        symbol: Symbole (pour le diagnostic).

    Returns:
        RetestDiagnostic avec le résultat booléen, la raison et tous les
        détails capturables.
    """
    empty = RetestDiagnostic(
        valid=True,
        reason="NO_DATA",
        symbol=symbol,
        direction=direction,
        max_zone_age_bars=max_age_bars,
        retest_atr_mult=max_distance_atr_mult,
    )

    if not candles or not smc_data:
        empty.reason = "NO_ZONES_IN_DIRECTION"
        return empty

    last = candles[-1]
    empty.last_candle_time = last.time

    atr = calculate_atr(candles)
    if atr == 0:
        empty.valid = False
        empty.reason = "ATR_ZERO"
        empty.atr = 0.0
        return empty

    empty.atr = float(atr)
    max_distance = atr * Decimal(str(max_distance_atr_mult))
    empty.max_distance = float(max_distance)

    zones_in_direction = 0
    zone_details: list[dict[str, Any]] = []

    all_zones_old = True
    all_zones_far = True
    any_zone_fresh_close = False

    for detection in smc_data:
        if detection.get("direction") != direction:
            continue

        concept = detection.get("concept")
        details = detection.get("details", {})
        if concept == "fair_value_gap":
            top = details.get("gap_top")
            bottom = details.get("gap_bottom")
        elif concept == "order_block":
            top = details.get("ob_top")
            bottom = details.get("ob_bottom")
        else:
            continue

        if top is None or bottom is None:
            continue

        zones_in_direction += 1

        zone_index = detection.get("index", 0)
        age = len(candles) - 1 - zone_index
        f_top = float(top)
        f_bottom = float(bottom)
        close = float(last.close)
        if close >= f_bottom and close <= f_top:
            distance = 0.0
        elif close > f_top:
            distance = close - f_top
        else:
            distance = f_bottom - close
        zone_mid = (f_top + f_bottom) / 2.0
        zone_direction = detection.get("direction", direction)

        zone_detail = {
            "concept": concept,
            "index": zone_index,
            "age_bars": age,
            "zone_top": float(top),
            "zone_bottom": float(bottom),
            "zone_mid": zone_mid,
            "distance_to_zone": distance,
            "max_distance": float(max_distance),
            "fresh": age <= max_age_bars,
            "close_enough": distance <= float(max_distance),
            "zone_direction": zone_direction,
            "mitigation_count": details.get("mitigation_count", 0),
            "filled": details.get("filled", False),
        }
        zone_details.append(zone_detail)

        # --- Check 1: Fraîcheur de la zone ---
        if age > max_age_bars:
            continue
        all_zones_old = False

        # --- Check 2: Proximité du prix courant ---
        if distance > float(max_distance):
            continue
        all_zones_far = False
        any_zone_fresh_close = True

        # --- Check 3: Rejection confirmée par la dernière bougie ---
        if direction == "bullish":
            retest_confirmed = bool(last.is_bullish and float(last.close) >= float(bottom))
        else:
            retest_confirmed = bool(not last.is_bullish and float(last.close) <= float(top))

        if retest_confirmed:
            empty.retest_detected = True
            empty.retest_confirmed = True
            empty.valid = True
            empty.reason = "PASS"
            empty.zones_in_direction = zones_in_direction
            empty.zone_details = zone_details
            # Populate zone diagnostics even on PASS (Phase 2.1 — instrumentation fix)
            empty.zone_type = concept
            empty.zone_id = f"{concept}_{zone_index}"
            empty.zone_created_index = zone_index
            empty.zone_age_bars = age
            empty.distance_to_zone = distance
            empty.zone_direction = detection.get("direction", direction)
            empty.zone_consumed = details.get("mitigation_count", 0) > 2
            return empty

    # === Aucune zone n'a passé les 3 checks ===
    empty.zones_in_direction = zones_in_direction
    empty.zone_details = zone_details

    # If zones exist but none passed all checks, determine the primary reason
    if zones_in_direction > 0:
        empty.valid = False

        if all_zones_old:
            empty.reason = "ZONE_TOO_OLD"
        elif all_zones_far or not any_zone_fresh_close:
            empty.reason = "ZONE_TOO_FAR"
        else:
            # At least one zone was fresh AND close, but rejection not confirmed
            empty.reason = "NO_RETEST_CONFIRMATION"

        # Capture the latest zone details for diagnostic
        if zone_details:
            latest_zone = max(zone_details, key=lambda z: z["index"])
            empty.zone_type = latest_zone["concept"]
            empty.zone_id = f"{latest_zone['concept']}_{latest_zone['index']}"
            empty.zone_created_index = latest_zone["index"]
            empty.zone_age_bars = latest_zone["age_bars"]
            empty.distance_to_zone = latest_zone["distance_to_zone"]
            empty.zone_direction = latest_zone.get("zone_direction", direction)
            empty.retest_detected = any_zone_fresh_close
            empty.retest_confirmed = False
    else:
        # zones_in_direction == 0 but we have data
        empty.valid = True
        empty.reason = "NO_ZONES_IN_DIRECTION"

    return empty


def is_fresh_structure(
    smc_data: list[dict[str, Any]],
    total_candles: int,
    max_age_bars: int = 40,
) -> dict[str, Any] | None:
    """
    Retourne l'événement structurel (BOS/CHoCH/MSS) le plus récent et valide.

    Un événement structurel est considéré comme valide s'il a été détecté
    il y a au plus ``max_age_bars`` bougies. Cela empêche qu'un ancien BOS
    bullish ne justifie un trade actuel alors que la structure a depuis
    changé (ex: CHoCH bearish intervenu entre-temps).

    Args:
        smc_data: Détections SMC
        total_candles: Nombre total de bougies analysées
        max_age_bars: Âge maximum en bougies (défaut 20)

    Returns:
        Le dict de détection le plus récent et frais, ou None si aucun.
    """
    if not smc_data or total_candles <= 0:
        return None

    structural_concepts = {
        "break_of_structure",
        "internal_bos",
        "external_bos",
        "change_of_character",
        "market_structure_shift",
    }

    candidates = []
    for d in smc_data:
        concept = d.get("concept", "")
        if concept not in structural_concepts:
            continue
        age = total_candles - 1 - d.get("index", 0)
        if age <= max_age_bars:
            candidates.append(d)

    if not candidates:
        return None

    return max(candidates, key=lambda d: d.get("index", 0))
