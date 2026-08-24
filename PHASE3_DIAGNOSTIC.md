# Phase 3A — Diagnostic technique Arty Trading

## 1. Pipeline réel observé

```
Market Data (MT5)
    ↓
H1 candles (master trend)
M5 candles (entry/setup)
[M15 : NON TÉLÉCHARGÉ / NON ANALYSÉ]
    ↓
SMCDetector sur H1 → master_trend + htf_smc_data
SMCDetector sur M5 → ltf_smc_data
    ↓
MarketContext (H1 bias + M5 detections)
    ↓
SetupTracker (crée des setups UNIQUEMENT depuis FVG et Order Block M5)
    ↓
SignalGenerator (stratégies sur M5)
    ↓
SignalValidator (Phase 2 — weighted scoring, STABLE)
    ↓
DecisionEngine
    ↓
RiskManager
    ↓
Execution
```

## 2. Problèmes identifiés

### 2.1 CRITIQUE — M15 absent du pipeline
**Fichier** : `application/trading_engine.py:533-539`, `application/market_context_builder.py`
**Constat** : Le pipeline télécharge H1 et M5 uniquement. M15 est mentionné dans les commentaires mais jamais téléchargé ni analysé.
**Impact** : L'architecture cible H1 → M15 → M5 n'existe pas. Les setups sont détectés directement sur M5 sans contexte M15.
**Correction** : Ajouter le téléchargement et l'analyse M15 dans MarketContextBuilder et TradingEngine.

### 2.2 CRITIQUE — Direction opposée en fallback (SMCTrendStrategy)
**Fichier** : `modules/strategies/strategies.py:96-98`
**Constat** :
```python
if not candidates:
    _try_direction(Direction.SELL)
    _try_direction(Direction.BUY)
```
Si le sens primaire (BOS le plus récent) échoue, la stratégie tente les DEUX directions. Le garde-fou `_is_htf_aligned` est censé bloquer, mais si H1 est bearish et que SELL échoue, BUY peut passer si `htf_trend` est neutre ou dérivé incorrectement.
**Impact** : "Marché bearish → bot cherche BUY".
**Correction** : Supprimer le fallback opposé. Si le sens primaire est bloqué par HTF, retourner None.

### 2.3 IMPORTANT — Liquidity Sweep trop simpliste
**Fichier** : `modules/smc/liquidity.py:80-132`
**Constat** :
```python
if candle.low < sp.price and candle.close > sp.price:
    # → bullish sweep
```
Un sweep est détecté si une seule bougie fait un wick sous un swing low puis clôture au-dessus. Aucune mesure de :
- force du rejet (distance du wick vs corps)
- displacement après le sweep
- contexte H1/M15
**Impact** : Un simple wick est confondu avec un vrai liquidity sweep.
**Correction** : Ajouter la détection de displacement après le sweep et mesurer la force du rejet.

### 2.4 IMPORTANT — Displacement basé sur une seule bougie
**Fichier** : `modules/smc/order_blocks.py:83-93`
**Constat** :
```python
next_body = abs(next_candle.close - next_candle.open)
if next_candle.close > next_candle.open and next_body >= displacement_min:
```
Le displacement est validé sur UNE SEULE bougie. Pour XAUUSD, une petite bougie peut dépasser 1x ATR sans être une impulsion significative.
**Impact** : Micro-OB détectés sur petits mouvements.
**Correction** : Exiger au moins 2 bougies consécutives dans la même direction avec corps > 0.5x ATR.

### 2.5 MODÉRÉ — FVG seuil fixe en pips
**Fichier** : `modules/smc/fair_value_gap.py:32-43`
**Constat** : `min_gap_pips=5.0` avec `pip_size=0.0001`. Pour XAUUSD, un gap de 5 pips = 0.0005, ce qui est minuscule comparé à la volatilité du Gold.
**Impact** : Micro-FVG comptés comme des gaps significatifs.
**Correction** : Rendre le seuil relatif à l'ATR pour XAUUSD (ex: min_gap = 0.3x ATR).

### 2.6 MODÉRÉ — SetupTracker ne crée des setups que depuis FVG et OB
**Fichier** : `application/trading_engine.py:1020`
**Constat** :
```python
if concept not in ("fair_value_gap", "order_block"):
    continue
```
Les liquidity sweeps, BOS, CHOCH ne créent pas de setups traçables.
**Impact** : Pas de traçabilité des setups de reversal ou de sweep.
**Correction** : Autoriser `liquidity_sweep` et `break_of_structure` comme origine de setup.

### 2.7 MINEUR — Pas de classification des setups
**Constat** : Le système ne distingue pas CONTINUATION vs REVERSAL vs LIQUIDITY_SWEEP_REVERSAL.
**Impact** : Impossible de mesurer les performances par type de setup.
**Correction** : Ajouter un champ `setup_type` dans Setup et Signal.

### 2.8 MINEUR — Backtest ne teste pas le pipeline réel
**Fichier** : `scripts/run_backtest.py`, `modules/backtesting/engine.py`
**Constat** : Le backtest génère des bougies H1 synthétiques et appelle `SignalGenerator.generate()` directement. Aucune donnée M5/M15 n'est générée. Le SetupTracker n'est pas utilisé.
**Impact** : Les chiffres de backtest ne reflètent pas le comportement réel du bot.
**Correction** : Générer des données M5 réalistes et intégrer le SetupTracker dans le backtest.

## 3. Architecture cible (à implémenter)

```
H1 (HTF)
    ↓
BIAS (MasterTrendAnalyzer)
    ↓
M15 (SETUP TF)
    ↓
SETUP DETECTION (Liquidity, BOS, CHOCH, FVG, OB, Displacement)
    ↓
SetupTracker (DETECTED → WATCHING → ARMED → READY)
    ↓
M5 (ENTRY TF)
    ↓
CONFIRMATION (rejection, engulfing, displacement)
    ↓
VALIDATOR (Phase 2 — weighted scoring)
    ↓
DECISION
    ↓
RISK
    ↓
EXECUTION
```

## 4. Corrections planifiées (par ordre de priorité)

### P1 — Direction consistante
- **Fichier** : `modules/strategies/strategies.py`
- **Action** : Supprimer le fallback opposé dans SMCTrendStrategy
- **Justification** : Empêche le bot de trader contre le biais H1

### P1 — Pipeline M15
- **Fichiers** : `application/trading_engine.py`, `application/market_context_builder.py`
- **Action** : Télécharger et analyser M15 entre H1 et M5
- **Justification** : L'architecture cible nécessite M15 comme timeframe de setup

### P2 — Liquidity Sweep qualitatif
- **Fichier** : `modules/smc/liquidity.py`
- **Action** : Exiger un displacement bullish/bearish après le sweep pour valider le signal
- **Justification** : Distingue vrai sweep de simple wick

### P2 — Displacement robuste
- **Fichier** : `modules/smc/order_blocks.py`, `modules/smc/base.py`
- **Action** : Exiger 2+ bougies consécutives avec corps > 0.5x ATR pour valider un displacement
- **Justification** : Évite les micro-déplacements

### P3 — FVG relatif à la volatilité
- **Fichier** : `modules/smc/fair_value_gap.py`, `config/settings.py`
- **Action** : Rendre `min_gap_pips` configurable par symbole, avec valeur par défaut relative à l'ATR pour XAUUSD
- **Justification** : Un seuil fixe de 5 pips est inadapté au Gold

### P3 — SetupTracker étendu
- **Fichier** : `application/trading_engine.py`
- **Action** : Autoriser `liquidity_sweep` et `break_of_structure` comme origine de setup
- **Justification** : Meilleure traçabilité des setups

### P4 — Classification des setups
- **Fichiers** : `modules/smc/setup_tracker.py`, `core/entities.py`
- **Action** : Ajouter `setup_type` (CONTINUATION, REVERSAL, SWEEP_REVERSAL, FVG_RETRACE, OB_RETRACE)
- **Justification** : Mesure des performances par type

### P4 — Backtest réaliste
- **Fichier** : `scripts/run_backtest.py`, `modules/backtesting/engine.py`
- **Action** : Générer des bougies M5 réalistes, intégrer SetupTracker et MarketContext
- **Justification** : Chiffres de backtest fiables

## 5. Fichiers volontairement non modifiés

- `SignalValidator` (Phase 2 — stable)
- `RiskManager`
- `PositionManager`
- `PositionStateStore`
- `StructureTrailing`
- `PositionMonitor`
- `Execution`

Sauf nécessité réelle démontrée pendant l'implémentation.

## 6. Tests à ajouter

- Liquidity sweep avec displacement vs simple wick
- Displacement sur 2+ bougies
- SMCTrendStrategy sans fallback opposé
- FVG avec seuil ATR-relatif
- Setup classification
- Pipeline M15 intégré
- Backtest avec M5 data
