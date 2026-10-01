"""Tests Phase 4 — backtest multi-timeframe (H1/M15/M5).

Couvre :
- reproductibilité et symétrie du Dataset B ;
- agrégation M5→M15/H1 sans look-ahead ;
- synchronisation des vues (bougies clôturées uniquement) ;
- gating H1/M15/M5 ;
- reproductibilité du dataset legacy ;
- déterminisme et look-ahead du pipeline complet (marqués slow).
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from arty_trading.application.setup_service import update_setups_from_market_context
from arty_trading.config.settings import Settings
from arty_trading.core.enums import Direction, TimeFrame
from arty_trading.modules.backtesting.data_generator import (
    aggregate_candles,
    generate_multi_regime_candles,
    regime_at_index,
)
from arty_trading.modules.backtesting.mtf_engine import MTFBacktestEngine
from arty_trading.modules.signals import SignalGenerator
from arty_trading.modules.smc import SetupTracker


def _mk_candle(time, o, h, low, c, tf=TimeFrame.M5, symbol="XAUUSD"):
    from arty_trading.core.entities import Candle

    return Candle(
        symbol=symbol, timeframe=tf, time=time,
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(low)), close=Decimal(str(c)),
        volume=100, spread=20,
    )


# ---------------------------------------------------------------------------
# Dataset B
# ---------------------------------------------------------------------------

def test_dataset_b_deterministic_same_seed():
    a, sa = generate_multi_regime_candles(n_m5=500, seed=42)
    b, sb = generate_multi_regime_candles(n_m5=500, seed=42)
    assert sa == sb
    assert len(a) == len(b) == 500
    for x, y in zip(a, b):
        assert x.time == y.time
        assert x.open == y.open and x.close == y.close
        assert x.high == y.high and x.low == y.low


def test_dataset_b_symmetric_bull_bear_drift():
    bull, _ = generate_multi_regime_candles(n_m5=600, seed=42, regimes=[("BULL", 600)])
    bear, _ = generate_multi_regime_candles(n_m5=600, seed=42, regimes=[("BEAR", 600)])
    up = float(bull[-1].close) - float(bull[0].close)
    down = float(bear[-1].close) - float(bear[0].close)
    # Le drift haussier du BULL est exactement compensé par le BEAR
    # (contrairement au générateur legacy où i % 7 == 0 pousse toujours +0.5).
    assert up > 100          # tendance haussière réelle
    assert down < -100       # tendance baissière réelle
    assert abs(up + down) < 1e-6


def test_dataset_b_regimes_spans():
    _, spans = generate_multi_regime_candles(
        n_m5=100, seed=42, regimes=[("BULL", 40), ("BEAR", 60)]
    )
    assert spans == [(0, 40, "BULL"), (40, 100, "BEAR")]
    assert regime_at_index(spans, 0) == "BULL"
    assert regime_at_index(spans, 99) == "BEAR"
    assert regime_at_index(spans, 100) == "UNKNOWN"


# ---------------------------------------------------------------------------
# Agrégation (anti look-ahead)
# ---------------------------------------------------------------------------

def _mk_m5_series(n, start="2024-01-01T00:00:00"):
    t0 = datetime.fromisoformat(start).replace(tzinfo=UTC)
    candles = []
    price = 2000.0
    for i in range(n):
        t = t0 + timedelta(minutes=5 * i)
        o = price
        c = price + 0.5
        candles.append(_mk_candle(t, o, max(o, c) + 0.2, min(o, c) - 0.2, c))
        price = c
    return candles


def test_aggregate_counts_and_ohlc():
    m5 = _mk_m5_series(25)  # 8 M15 complètes + 1 partielle ; 2 H1 + 1 partielle
    m15 = aggregate_candles(m5, TimeFrame.M15)
    h1 = aggregate_candles(m5, TimeFrame.H1)
    assert len(m15) == 8
    assert len(h1) == 2
    # OHLC fusionnés corrects (1re M15 = 3 M5)
    assert m15[0].open == m5[0].open
    assert m15[0].close == m5[2].close
    assert m15[0].high == max(m5[0].high, m5[1].high, m5[2].high)
    assert m15[0].low == min(m5[0].low, m5[1].low, m5[2].low)
    assert m15[0].timeframe == TimeFrame.M15
    assert h1[0].timeframe == TimeFrame.H1
    assert h1[0].close == m5[11].close

# ---------------------------------------------------------------------------
# Synchronisation des vues (aucune bougie future)
# ---------------------------------------------------------------------------

class _RecordingDetector:
    """Détecteur stub : enregistre les vues dans l'ordre, ne détecte rien."""

    def __init__(self):
        self.calls: list[tuple[str, datetime, datetime]] = []  # (tf, open, close)

    async def detect(self, candles, symbol):
        if not candles:
            return []
        tf_minutes = {"M5": 5, "M15": 15, "H1": 60}[candles[-1].timeframe.value]
        self.calls.append((
            candles[-1].timeframe.value,
            candles[-1].time,
            candles[-1].time + timedelta(minutes=tf_minutes),
        ))
        return []


class _NullGenerator:
    """Générateur stub : n'émet jamais de signal."""

    def __init__(self):
        self.calls = 0

    async def generate(self, *args, **kwargs):
        self.calls += 1
        return None


def test_mtf_views_use_only_closed_candles():
    m5 = _mk_m5_series(400)
    m15 = aggregate_candles(m5, TimeFrame.M15)
    h1 = aggregate_candles(m5, TimeFrame.H1)
    det = _RecordingDetector()
    gen = _NullGenerator()
    engine = MTFBacktestEngine(
        initial_balance=Decimal("10000"), symbol="XAUUSD",
        h1_window=50, m15_window=50, m5_window=50,
    )
    asyncio.run(engine.run_mtf_async(m5, m15, h1, gen, det))
    tfs = {tf for tf, _, _ in det.calls}
    assert {"M5", "M15", "H1"} <= tfs

    # Ordre des appels dans build() : H1 (cache) → M5 → M15.
    # Chaque vue H1/M15 doit être justifiée par une M5 clôturée : la H1/M15
    # clôture au plus tard à la clôture de la bougie M5 de SON itération.
    calls = det.calls
    for k, (tf, _, close) in enumerate(calls):
        if tf == "M5":
            continue
        next_m5 = next((c for c in calls[k + 1:] if c[0] == "M5"), calls[-1])
        assert close <= next_m5[2], (
            f"Look-ahead: bougie {tf} clôturant à {close} utilisée alors que "
            f"la M5 courante clôture à {next_m5[2]}"
        )


def test_mtf_sync_boundary_exact():
    # Aucune H1/M15 vue ne doit clôturer après la dernière clôture M5.
    m5 = _mk_m5_series(400)
    m15 = aggregate_candles(m5, TimeFrame.M15)
    h1 = aggregate_candles(m5, TimeFrame.H1)
    det = _RecordingDetector()
    gen = _NullGenerator()
    engine = MTFBacktestEngine(
        initial_balance=Decimal("10000"), symbol="XAUUSD",
        h1_window=50, m15_window=50, m5_window=50,
    )
    asyncio.run(engine.run_mtf_async(m5, m15, h1, gen, det))

    assert {tf for tf, _, _ in det.calls} >= {"M5", "M15", "H1"}
    last_m5_close = m5[-1].time + timedelta(minutes=5)
    for timeframe, _, close in det.calls:
        if timeframe in ("M15", "H1"):
            assert close <= last_m5_close


# ---------------------------------------------------------------------------
# Gating H1 / M15 / M5
# ---------------------------------------------------------------------------

def _fake_context(master_trend, setup_detections, ltf_candles, ltf_detections=None):
    return SimpleNamespace(
        master_trend=master_trend,
        setup_smc_data=setup_detections,
        setup_trend="bullish" if master_trend == "bullish" else "bearish",
        ltf_smc_data=ltf_detections or [],
        ltf_candles=ltf_candles,
        atr=1.0,
    )


def _fvg_detection(direction="bullish", idx=5, top=2010.0, bottom=2008.0):
    return {
        "concept": "fair_value_gap",
        "direction": direction,
        "index": idx,
        "details": {"gap_top": top, "gap_bottom": bottom},
    }


def test_h1_bearish_blocks_bullish_m15_setup():
    """H1 bearish + zones M15 bullish → AUCUN setup BUY créé (gate H1)."""
    tracker = SetupTracker()
    m5 = _mk_m5_series(30)
    ctx = _fake_context("bearish", [_fvg_detection("bullish")], m5)
    update_setups_from_market_context(tracker, "XAUUSD", ctx, Settings())
    assert tracker.get_active_setups("XAUUSD") == []


def test_h1_bullish_allows_bullish_m15_setup():
    """H1 bullish + zone M15 bullish → setup BUY créé (voie setup possible)."""
    tracker = SetupTracker()
    m5 = _mk_m5_series(30)
    ctx = _fake_context("bullish", [_fvg_detection("bullish")], m5)
    update_setups_from_market_context(tracker, "XAUUSD", ctx, Settings())
    setups = tracker.get_active_setups("XAUUSD")
    assert len(setups) == 1
    assert setups[0].direction == Direction.BUY


def test_m15_setup_without_m5_confirmation_no_trade():
    """Setup M15 créé mais prix M5 loin de la zone → aucun setup READY →
    aucun signal via la voie setup."""
    tracker = SetupTracker()
    m5 = _mk_m5_series(30)  # prix ~2015, zone 2008-2010 (loin)
    ctx = _fake_context("bullish", [_fvg_detection("bullish")], m5)
    update_setups_from_market_context(tracker, "XAUUSD", ctx, Settings())
    assert tracker.get_ready_setups("XAUUSD") == []
    gen = SignalGenerator(min_confidence=0.30, strategies=[], setup_tracker=tracker)
    signal = asyncio.run(gen.generate(m5, [], master_trend="bullish"))
    assert signal is None


def test_m5_signal_without_m15_setup_no_setup_trade():
    """Aucun setup M15 → la voie setup ne produit aucun signal (la voie
    stratégie reste le seul chemin possible, comme en live)."""
    tracker = SetupTracker()
    m5 = _mk_m5_series(30)
    gen = SignalGenerator(min_confidence=0.30, strategies=[], setup_tracker=tracker)
    signal = asyncio.run(gen.generate(m5, [], master_trend="bullish"))
    assert signal is None


# ---------------------------------------------------------------------------
# Dataset legacy reproductible
# ---------------------------------------------------------------------------

def _load_legacy_module():
    spec = importlib.util.spec_from_file_location(
        "run_backtest_legacy", "scripts/run_backtest.py"
    )
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_legacy_dataset_reproducible():
    mod = _load_legacy_module()
    candles = mod.generate_candles("XAUUSD", 600, seed=42)
    h = hashlib.sha256("|".join(str(c.close) for c in candles).encode()).hexdigest()
    # Empreinte du dataset legacy 600 bougies XAUUSD seed 42 (figée).
    assert h == "679b3a97a0bed04a1c7010e87f7d19ec7db34178eaf8e07fd80c40fd4b9ce175"
    assert len(candles) == 600
    assert candles[0].timeframe == TimeFrame.H1

    m5 = _mk_m5_series(800)
    m15 = aggregate_candles(m5, TimeFrame.M15)
    h1 = aggregate_candles(m5, TimeFrame.H1)
    det = _RecordingDetector()
    gen = _NullGenerator()
    engine = MTFBacktestEngine(
        initial_balance=Decimal("10000"), symbol="XAUUSD",
        h1_window=500, m15_window=500, m5_window=500,
    )
    asyncio.run(engine.run_mtf_async(m5, m15, h1, gen, det))
    last_m5_close = m5[-1].time + timedelta(minutes=5)
    for tf, _, close in det.calls:
        if tf in ("H1", "M15"):
            assert close <= last_m5_close



def test_aggregate_incomplete_bucket_not_emitted():
    # 4 M5 : 1 M15 complète + 1 M5 orpheline → 1 seule M15, 0 H1.
    m5 = _mk_m5_series(4)
    assert len(aggregate_candles(m5, TimeFrame.M15)) == 1
    assert len(aggregate_candles(m5, TimeFrame.H1)) == 0
