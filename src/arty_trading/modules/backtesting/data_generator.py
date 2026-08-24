"""Générateur de dataset synthétique multi-régime (Dataset B — Phase 4).

Contrairement au générateur « legacy » de ``scripts/run_backtest.py`` (impulsions
±1.5 tous les 20 barres H1 + pushes ``i % 7 == 0`` TOUJOURS positifs → drift
haussier permanent ~+42), ce générateur contrôle **explicitement** les régimes
et reste **symétrique** :

- BULL et BEAR ont exactement le même drift total (mêmes durées, mêmes
  amplitudes) → aucun drift global résiduel ;
- RANGE : oscillation sinusoïdale sans drift ;
- HIGH_VOLATILITY : drift nul, bruit et impulsions symétriques ;
- BULL_TO_BEAR / BEAR_TO_BULL : transitions progressives.

Timeframe de base : M5. Les timeframes supérieurs (M15, H1) sont obtenus par
agrégation ``aggregate_candles`` (bougies clôturées uniquement, aucune donnée
future : une bougie agrégée n'est émise qu'une fois toutes ses M5 connues).

Reproductibilité : ``random.Random(seed)`` avec seed fixe (défaut 42).
Chaque exécution produit exactement les mêmes candles.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from arty_trading.core.entities import Candle
from arty_trading.core.enums import TimeFrame

TF_MINUTES: dict[TimeFrame, int] = {
    TimeFrame.M1: 1,
    TimeFrame.M5: 5,
    TimeFrame.M15: 15,
    TimeFrame.H1: 60,
    TimeFrame.H4: 240,
}

# Échelle XAUUSD (points). drift/noise par bougie M5.
#
# Le ratio drift/bruit est calibré pour que les bougies H1 agrégées présentent
# des swing points naturels (local highs/lows) — condition indispensable pour
# que ``MasterTrendAnalyzer`` (via ``find_swing_points``) puisse détecter une
# tendance.  Avec noise=0.25 l'ancien ratio était ~7:1 sur H1 (quasi-monotone,
# zéro swing).  Avec noise=0.90 le ratio tombe à ~1.8:1 — suffisant pour une
# tendance claire tout en produisant des retracements réalistes.
XAUUSD_SCALE = {
    "base": 2000.0,
    "trend_drift": 0.30,   # par M5 en régime tendanciel (≈ +3.6 / heure)
    "impulse": 2.5,        # impulsion périodique dans le sens du régime
    "noise": 0.90,         # bruit par bougie (crée des swing points sur H1)
    "wick": 0.60,          # mèche maximale (élargie pour swing highs/lows)
    "range_amplitude": 12.0,  # amplitude du range (demi-excursion)
    "pullback_amplitude": 0.45,  # amplitude de l'onde de retracement
    "pullback_period": 20,       # période (en M5) de l'onde de retracement
}

# Régimes par défaut : durées en bougies M5. BULL/BEAR équilibrés.
DEFAULT_REGIMES: list[tuple[str, int]] = [
    ("BULL", 1200),
    ("RANGE", 900),
    ("BEAR", 1200),
    ("HIGH_VOLATILITY", 900),
    ("BULL_TO_BEAR", 600),
    ("BEAR_TO_BULL", 600),
]


def _regime_drift(regime: str, i: int, length: int, sc: dict) -> float:
    """Drift par bougie M5 pour un régime donné (symétrique bull/bear)."""
    if regime == "BULL":
        return sc["trend_drift"]
    if regime == "BEAR":
        return -sc["trend_drift"]
    if regime == "RANGE":
        return 0.0
    if regime == "HIGH_VOLATILITY":
        return 0.0
    if regime == "BULL_TO_BEAR":
        # drift décroit linéairement de +d à -d (transition progressive)
        frac = i / max(length - 1, 1)
        return sc["trend_drift"] * (1.0 - 2.0 * frac)
    if regime == "BEAR_TO_BULL":
        frac = i / max(length - 1, 1)
        return -sc["trend_drift"] * (1.0 - 2.0 * frac)
    return 0.0

def generate_multi_regime_candles(
    n_m5: int | None = None,
    seed: int = 42,
    start_time: datetime | None = None,
    scale: dict | None = None,
    regimes: list[tuple[str, int]] | None = None,
) -> tuple[list[Candle], list[tuple[int, int, str]]]:
    """Génère le Dataset B : bougies M5 multi-régimes, symétriques.

    Returns:
        (candles, regime_spans) — regime_spans est une liste de
        (start_index, end_index_exclusive, regime_name) alignée sur l'index
        M5, utilisée pour le breakdown par régime.
    """
    rng = random.Random(seed)
    sc = scale or dict(XAUUSD_SCALE)
    regime_plan = list(regimes or DEFAULT_REGIMES)

    if n_m5 is not None:
        # Tronquer/étendre le plan pour atteindre exactement n_m5 bougies.
        plan: list[tuple[str, int]] = []
        remaining = n_m5
        for name, length in regime_plan:
            take = min(length, remaining)
            if take <= 0:
                break
            plan.append((name, take))
            remaining -= take
        # Si le plan est trop court, boucler sur les régimes (symétrie
        # préservée : un cycle complet est neutre en drift).
        i = 0
        while remaining > 0:
            name, length = regime_plan[i % len(regime_plan)]
            take = min(length, remaining)
            plan.append((name, take))
            remaining -= take
            i += 1
        regime_plan = plan

    time = start_time or datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    price = sc["base"]
    candles: list[Candle] = []
    spans: list[tuple[int, int, str]] = []

    idx = 0
    for regime, length in regime_plan:
        start_idx = idx
        for i in range(length):
            drift = _regime_drift(regime, i, length, sc)

            # Impulsions périodiques dans le sens du régime (structure SMC) :
            # impulsion suivie de pullbacks (~40 %) — symétriques bull/bear.
            impulse = 0.0
            if regime in ("BULL", "BEAR", "BULL_TO_BEAR", "BEAR_TO_BULL"):
                phase = i % 30
                direction = 1.0 if drift >= 0 else -1.0
                if abs(drift) < 1e-9:
                    direction = 1.0 if (idx % 60) < 30 else -1.0
                if phase == 10:
                    impulse = direction * sc["impulse"]
                elif phase == 11:
                    impulse = -direction * sc["impulse"] * 0.6
                elif phase == 12:
                    impulse = -direction * sc["impulse"] * 0.3
            else:
                direction = 1.0 if drift >= 0 else -1.0
                if abs(drift) < 1e-9:
                    direction = 1.0 if (idx % 60) < 30 else -1.0

            # Onde de retracement sinusoïdale (régimes tendanciels uniquement) :
            # crée des swing points naturels sur H1 sans annuler le drift.
            pullback_wave = 0.0
            if regime in ("BULL", "BEAR", "BULL_TO_BEAR", "BEAR_TO_BULL"):
                pullback_wave = (
                    -direction
                    * sc.get("pullback_amplitude", 0.45)
                    * math.sin(2 * math.pi * i / sc.get("pullback_period", 20))
                )

            # RANGE : oscillation sinusoïdale (retour vers le centre).
            range_pull = 0.0
            if regime == "RANGE":
                range_pull = -math.sin(idx / 25.0) * 0.15 * sc["trend_drift"] * 10

            # HIGH_VOL : impulsions symétriques aléatoires.
            hv = 0.0
            if regime == "HIGH_VOLATILITY" and rng.random() < 0.05:
                hv = rng.choice((-1.0, 1.0)) * sc["impulse"] * 1.5

            noise_mult = 3.0 if regime == "HIGH_VOLATILITY" else 1.0
            # Le bruit est multiplié par ``direction`` pour garantir une
            # symétrie EXACTE entre BULL et BEAR (même seed → drift total
            # opposé au centime près).  La distribution uniforme étant
            # symétrique, cela ne change pas la loi du bruit.
            noise = rng.uniform(-sc["noise"], sc["noise"]) * noise_mult * direction

            move = drift + impulse + pullback_wave + range_pull + hv + noise

            o = Decimal(str(round(price, 2)))
            c = Decimal(str(round(price + move, 2)))
            wick = sc["wick"] * noise_mult
            h = max(o, c) + Decimal(str(round(rng.uniform(0.05, wick), 2)))
            l = min(o, c) - Decimal(str(round(rng.uniform(0.05, wick), 2)))

            candles.append(
                Candle(
                    symbol="XAUUSD",
                    timeframe=TimeFrame.M5,
                    time=time,
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                    volume=rng.randint(80, 300),
                    spread=20,
                )
            )
            price = float(c)
            time = time + timedelta(minutes=5)
            idx += 1
        spans.append((start_idx, idx, regime))

    return candles, spans
def aggregate_candles(m5_candles: list[Candle], timeframe: TimeFrame) -> list[Candle]:
    """Agrège des bougies M5 en bougies d'un timeframe supérieur.

    Règle de clôture (anti look-ahead) : une bougie agrégée couvrant
    [t, t + tf) n'est émise que si TOUTES ses bougies M5 sont présentes
    (3 pour M15, 12 pour H1). Sa date est l'ouverture du bucket.
    """
    minutes = TF_MINUTES[timeframe]
    out: list[Candle] = []
    current: list[Candle] = []
    current_key: int | None = None

    for c in m5_candles:
        ts = int(c.time.timestamp())
        key = (ts // (minutes * 60)) * (minutes * 60)
        if current_key is None or key != current_key:
            if _bucket_complete(current_key, current, minutes):
                out.append(_merge(current, timeframe))
            current = []
            current_key = key
        current.append(c)
    if _bucket_complete(current_key, current, minutes):
        out.append(_merge(current, timeframe))
    return out


def _bucket_complete(key: int | None, group: list[Candle], minutes: int) -> bool:
    """Un bucket est complet s'il couvre toute sa durée en M5."""
    if key is None or not group:
        return False
    expected = minutes // 5
    return len(group) >= expected


def _merge(group: list[Candle], timeframe: TimeFrame) -> Candle:
    return Candle(
        symbol=group[0].symbol,
        timeframe=timeframe,
        time=group[0].time,
        open=group[0].open,
        high=max(c.high for c in group),
        low=min(c.low for c in group),
        close=group[-1].close,
        volume=sum(c.volume for c in group),
        spread=group[-1].spread,
    )


def regime_at_index(spans: list[tuple[int, int, str]], index: int) -> str:
    """Retourne le régime couvrant l'index M5 donné (ou 'UNKNOWN')."""
    for start, end, name in spans:
        if start <= index < end:
            return name
    return "UNKNOWN"

