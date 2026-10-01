"""
Confirmation M5 d'une zone Order Block (Phase 12).

Détermine si une zone OB de haute qualité (Grade A/B) est **réellement
confirmée** sur le timeframe d'entrée M5 avant d'autoriser un signal.

Deux règles ICT sont vérifiées, dans cet ordre :

1. **Retest** — la zone a été retestée (au moins une bougie M5 chevauche
   ``[zone_bottom, zone_top]``) dans la fenêtre d'observation.
2. **Rejet (rejection)** — la dernière bougie M5 clôturée repart dans le sens du
   setup avec un ratio de rejet proportionnel à la zone :

   - ``rejection`` >= 1.0 (clôture au-delà du bord opposé de la zone)
     → ``strong_rejection``
   - ``rejection`` >= ``min_rejection_ratio`` → ``rejection``
   - en dessous → ``weak_rejection`` (refusé)

3. **Displacement** (optionnel) — le corps de la bougie de rejet doit valoir au
   moins ``displacement_atr_mult`` x ATR, garantissant une réaction
   institutionnelle et non un simple bruit.

Le module est **pur** : aucune I/O, aucune exécution d'ordre, aucun accès
config (tous les paramètres sont passés en arguments).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from arty_trading.core.entities import Candle

#: Types de confirmation possibles (du plus fort au plus faible).
CONFIRMATION_TYPES: tuple[str, ...] = (
    "strong_rejection",
    "rejection",
    "weak_rejection",
    "no_displacement",
    "no_rejection",
    "no_retest",
    "invalid_zone",
    "invalid_direction",
    "no_data",
)

#: Types considérés comme une confirmation valide.
CONFIRMED_TYPES: frozenset[str] = frozenset({"strong_rejection", "rejection"})


@dataclass(frozen=True)
class M5ConfirmationResult:
    """Résultat de l'évaluation de la confirmation M5.

    Attributes:
        confirmed: ``True`` si la zone est confirmée sur M5.
        confirmation_type: Type de confirmation (voir ``CONFIRMATION_TYPES``).
        zone_touched: ``True`` si la zone a été retestée dans la fenêtre.
        retest_index: Index absolu de la dernière bougie ayant touché la zone.
        rejection_ratio: Clôture relative dans la zone (> 1 = au-delà du bord).
        body_atr_mult: Corps de la bougie de rejet en multiple d'ATR.
        reasons: Explications lisibles.
    """

    confirmed: bool
    confirmation_type: str
    zone_touched: bool
    retest_index: int | None
    rejection_ratio: float
    body_atr_mult: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Représentation sérialisable (métadonnées de setup / API)."""
        return {
            "ob_m5_confirmed": self.confirmed,
            "ob_m5_confirmation_type": self.confirmation_type,
            "ob_m5_zone_touched": self.zone_touched,
            "ob_m5_rejection_ratio": round(self.rejection_ratio, 4),
            "ob_m5_body_atr_mult": round(self.body_atr_mult, 4),
        }


def _bullish(candle: Candle) -> bool:
    return candle.close > candle.open


def _bearish(candle: Candle) -> bool:
    return candle.close < candle.open


def evaluate_m5_confirmation(
    candles: Sequence[Candle],
    direction: str,
    zone_top: float,
    zone_bottom: float,
    *,
    atr: float = 0.0,
    lookback_bars: int = 30,
    min_rejection_ratio: float = 0.5,
    require_displacement: bool = True,
    displacement_atr_mult: float = 1.0,
) -> M5ConfirmationResult:
    """Évalue la confirmation M5 d'une zone Order Block.

    Args:
        candles: Bougies M5 clôturées (de la plus ancienne à la plus récente).
        direction: Sens du setup (``bullish`` pour un OB d'achat).
        zone_top: Borne haute de la zone OB.
        zone_bottom: Borne basse de la zone OB.
        atr: ATR courant du M5 (0 = déplacement non mesurable).
        lookback_bars: Nombre de bougies M5 observées pour trouver le retest.
        min_rejection_ratio: Ratio de clôture minimal dans la zone (0-1).
        require_displacement: Exige un corps de rejet >= ``displacement_atr_mult`` x ATR.
        displacement_atr_mult: Multiple d'ATR exigé pour le corps de rejet.

    Returns:
        ``M5ConfirmationResult`` — ``confirmed`` est vrai uniquement pour les
        types ``rejection`` / ``strong_rejection``.

    Note:
        La fonction n'utilise que les bougies fournies : elle est donc
        compatible backtest (aucun look-ahead si l'appelant passe uniquement des
        bougies clôturées).
    """
    if not candles:
        return M5ConfirmationResult(
            False, "no_data", False, None, 0.0, 0.0, ("no_candles",)
        )
    if direction not in ("bullish", "bearish"):
        return M5ConfirmationResult(
            False, "invalid_direction", False, None, 0.0, 0.0, (f"direction={direction}",)
        )
    if zone_top <= zone_bottom:
        return M5ConfirmationResult(
            False, "invalid_zone", False, None, 0.0, 0.0, ("zone_top<=zone_bottom",)
        )

    window_start = 0 if lookback_bars <= 0 else max(0, len(candles) - lookback_bars)
    window = list(candles[window_start:])

    retest_offset: int | None = None
    for offset, candle in enumerate(window):
        if float(candle.low) <= zone_top and float(candle.high) >= zone_bottom:
            retest_offset = offset

    if retest_offset is None:
        return M5ConfirmationResult(
            False,
            "no_retest",
            False,
            None,
            0.0,
            0.0,
            ("zone_never_retested",),
        )
    retest_index = window_start + retest_offset

    last = window[-1]
    close = float(last.close)
    height = zone_top - zone_bottom
    if direction == "bullish":
        rejection_ratio = (close - zone_bottom) / height
        directional = _bullish(last)
    else:
        rejection_ratio = (zone_top - close) / height
        directional = _bearish(last)

    body = abs(float(last.close) - float(last.open))
    body_atr_mult = body / atr if atr > 0 else 0.0
    base_reasons = (
        f"retest_index={retest_index}",
        f"rejection_ratio={rejection_ratio:.2f}",
        f"body={body_atr_mult:.2f}atr",
    )

    if not directional:
        return M5ConfirmationResult(
            False,
            "no_rejection",
            True,
            retest_index,
            rejection_ratio,
            body_atr_mult,
            (*base_reasons, "rejection_candle_wrong_direction"),
        )

    if rejection_ratio >= 1.0:
        confirmation_type = "strong_rejection"
    elif rejection_ratio >= min_rejection_ratio:
        confirmation_type = "rejection"
    else:
        return M5ConfirmationResult(
            False,
            "weak_rejection",
            True,
            retest_index,
            rejection_ratio,
            body_atr_mult,
            (*base_reasons, "close_still_in_zone"),
        )

    if require_displacement and atr > 0 and body_atr_mult < displacement_atr_mult:
        return M5ConfirmationResult(
            False,
            "no_displacement",
            True,
            retest_index,
            rejection_ratio,
            body_atr_mult,
            (*base_reasons, f"displacement_required>={displacement_atr_mult:.2f}atr"),
        )

    return M5ConfirmationResult(
        True,
        confirmation_type,
        True,
        retest_index,
        rejection_ratio,
        body_atr_mult,
        base_reasons,
    )
