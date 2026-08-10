"""
Classes de base pour les détecteurs SMC.

Fournit :
- ``SwingPoint`` : point pivot (swing high/low) avec classification de force
- ``BaseDetector`` : classe de base pour tous les détecteurs SMC
- ``SMCDetection`` : structure de détection normalisée
- Utilitaires partagés : ``find_swing_points``, ``find_swing_highs``, ``find_swing_lows``

Concepts ICT clés
-----------------
Un **swing point** est un pivot de prix. En ICT/SMC, on distingue :

- **Swing interne** (minor / weak) : pivot de faible amplitude, formé sur peu de
  bougies. La cassure de ce niveau donne un *Internal BOS*.
- **Swing externe** (major / strong) : pivot de forte amplitude, structurellement
  significatif. La cassure de ce niveau donne un *External BOS* (ou CHoCH selon
  le contexte de tendance).

La force d'un swing est estimée par le nombre de bougies de part et d'autre
(window) et par l'amplitude du mouvement (high - low du pivot local).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from arty_trading.core.entities import Candle
from arty_trading.core.enums import SMCConcept


@dataclass(frozen=True)
class SwingPoint:
    """
    Point pivot (swing high ou swing low).

    Un swing high est un candle dont le high est plus élevé que N candles
    avant et après. Inversement pour un swing low.

    Attributes:
        index: Index du candle pivot dans la liste
        price: Niveau de prix du pivot (high pour swing high, low pour swing low)
        type: "high" ou "low"
        timestamp: Heure du candle pivot
        window: Fenêtre utilisée pour détecter le pivot (force relative)
        strength: Force du pivot ("internal" ou "external")
        amplitude: Amplitude du mouvement local (high - low du pivot)
    """

    index: int
    price: Decimal
    type: str  # "high" ou "low"
    timestamp: datetime = field(default_factory=datetime.utcnow)
    window: int = 2
    strength: str = "internal"  # "internal" ou "external"
    amplitude: Decimal = Decimal("0")


@dataclass
class SMCDetection:
    """
    Détection d'un concept SMC normalisée.

    Attributes:
        concept: Type de concept SMC détecté
        direction: "bullish", "bearish" ou "neutral"
        price: Niveau de prix associé
        index: Index du candle où la détection a eu lieu
        details: Informations supplémentaires spécifiques au concept
    """

    concept: SMCConcept
    direction: str  # "bullish", "bearish", "neutral"
    price: Decimal
    index: int
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convertit la détection en dictionnaire."""
        return {
            "concept": self.concept.value,
            "direction": self.direction,
            "price": float(self.price),
            "index": self.index,
            "details": self.details,
        }


class BaseDetector(ABC):
    """
    Classe de base pour tous les détecteurs SMC.

    Chaque détecteur est indépendant et peut être activé ou désactivé.
    Aucun détecteur n'ouvre de trade : il retourne uniquement des informations
    de marché sous forme de ``SMCDetection``.
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    @property
    @abstractmethod
    def name(self) -> str:
        """Nom unique du détecteur."""

    @property
    def enabled(self) -> bool:
        """Indique si le détecteur est activé."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @abstractmethod
    def detect(self, candles: list[Candle]) -> list[SMCDetection]:
        """
        Détecte les concepts SMC sur les bougies fournies.

        Args:
            candles: Liste des bougies (du plus ancien au plus récent)

        Returns:
            Liste des détections SMC
        """


# =============================================================================
# Utilitaires partagés
# =============================================================================


def find_swing_points(
    candles: list[Candle],
    window: int = 2,
    external_window: int = 5,
) -> list[SwingPoint]:
    """
    Identifie les swing highs et swing lows (points pivots).

    Un swing high est un candle dont le high est le plus élevé
    parmi les `window` candles avant et après.
    Un swing low est défini symétriquement.

    Chaque pivot est classifié en **internal** (faible) ou **external** (fort) :
    - Un pivot est *external* s'il est aussi un pivot avec une fenêtre plus large
      (`external_window`). Sinon, il est *internal*.

    Args:
        candles: Liste des bougies (du plus ancien au plus récent)
        window: Nombre de candles avant/après pour les pivots internes (2 = fractal)
        external_window: Fenêtre élargie pour qualifier un pivot d'external

    Returns:
        Liste triée par index des swing points
    """
    swing_points: list[SwingPoint] = []
    n = len(candles)

    if n < 2 * window + 1:
        return swing_points

    # Index des pivots externes (fenêtre large) pour classification
    external_indices: set[int] = set()
    if n >= 2 * external_window + 1:
        for i in range(external_window, n - external_window):
            is_swing_high = True
            is_swing_low = True
            for j in range(1, external_window + 1):
                if candles[i].high <= candles[i - j].high or candles[i].high <= candles[i + j].high:
                    is_swing_high = False
                if candles[i].low >= candles[i - j].low or candles[i].low >= candles[i + j].low:
                    is_swing_low = False
            if is_swing_high or is_swing_low:
                external_indices.add(i)

    for i in range(window, n - window):
        # Vérifier swing high
        is_swing_high = True
        is_swing_low = True

        for j in range(1, window + 1):
            if candles[i].high <= candles[i - j].high or candles[i].high <= candles[i + j].high:
                is_swing_high = False
            if candles[i].low >= candles[i - j].low or candles[i].low >= candles[i + j].low:
                is_swing_low = False

        amplitude = candles[i].high - candles[i].low
        strength = "external" if i in external_indices else "internal"

        if is_swing_high:
            swing_points.append(
                SwingPoint(
                    index=i,
                    price=candles[i].high,
                    type="high",
                    timestamp=candles[i].time,
                    window=window,
                    strength=strength,
                    amplitude=amplitude,
                )
            )

        if is_swing_low:
            swing_points.append(
                SwingPoint(
                    index=i,
                    price=candles[i].low,
                    type="low",
                    timestamp=candles[i].time,
                    window=window,
                    strength=strength,
                    amplitude=amplitude,
                )
            )

    return swing_points


def find_swing_highs(candles: list[Candle], window: int = 2) -> list[SwingPoint]:
    """Retourne uniquement les swing highs."""
    return [sp for sp in find_swing_points(candles, window) if sp.type == "high"]


def find_swing_lows(candles: list[Candle], window: int = 2) -> list[SwingPoint]:
    """Retourne uniquement les swing lows."""
    return [sp for sp in find_swing_points(candles, window) if sp.type == "low"]


def find_external_swing_points(candles: list[Candle], window: int = 2, external_window: int = 5) -> list[SwingPoint]:
    """Retourne uniquement les swing points externes (forts)."""
    return [sp for sp in find_swing_points(candles, window, external_window) if sp.strength == "external"]


def find_internal_swing_points(candles: list[Candle], window: int = 2, external_window: int = 5) -> list[SwingPoint]:
    """Retourne uniquement les swing points internes (faibles)."""
    return [sp for sp in find_swing_points(candles, window, external_window) if sp.strength == "internal"]