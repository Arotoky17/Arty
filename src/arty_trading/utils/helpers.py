"""
Utilitaires de calcul pour le trading Forex.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

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
    smc_data: list[dict],
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
    smc_data: list[dict],
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

        # 2. Proximité du prix courant
        zone_mid = (float(top) + float(bottom)) / 2.0
        if abs(float(last.close) - zone_mid) > float(max_distance):
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


def is_fresh_structure(
    smc_data: list[dict],
    total_candles: int,
    max_age_bars: int = 40,
) -> dict | None:
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
