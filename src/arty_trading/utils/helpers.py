"""
Utilitaires de calcul pour le trading Forex.
"""

from decimal import Decimal, ROUND_HALF_UP


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
        return lot_size * 1000 * 0.01 / 100  # approximation
    return lot_size * 10.0  # 10 USD/pip pour 1 lot standard EURUSD
