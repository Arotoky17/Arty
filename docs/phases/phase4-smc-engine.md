# Phase 4 – Moteur SMC (Smart Money Concepts)

## Objectif

Détecter automatiquement les 15 concepts Smart Money (SMC/ICT) à partir des données de marché. Chaque détecteur est indépendant et activable/désactivable individuellement.

## Modules livrés

### 1. Classes de base (`modules/smc/base.py`)

- **`SwingPoint`** — point pivot (swing high/low) avec index, prix, type, timestamp
- **`SMCDetection`** — structure de détection normalisée (concept, direction, price, index, details)
- **`BaseDetector`** — classe abstraite pour tous les détecteurs (enabled, name, detect)
- **`find_swing_points()`** — identifie les swing highs/lows (fractal, window configurable)
- **`find_swing_highs()` / `find_swing_lows()`** — filtres par type

### 2. Détecteurs SMC

| Détecteur | Fichier | Concepts détectés |
|-----------|---------|-------------------|
| `StructureDetector` | `structure.py` | BOS, CHoCH, MSS |
| `FairValueGapDetector` | `fair_value_gap.py` | FVG, IFVG |
| `OrderBlockDetector` | `order_blocks.py` | Order Block, Breaker Block, Mitigation Block |
| `LiquidityDetector` | `liquidity.py` | Liquidity Sweep, Equal High, Equal Low |
| `PremiumDiscountDetector` | `premium_discount.py` | Premium/Discount, OTE |

### 3. Détecteur principal (`modules/smc/detector.py`)

**`SMCDetector`** implémente `ISMCDetector` :
- Orchestre les 5 sous-détecteurs
- `enable_concept()` / `disable_concept()` — activation individuelle
- `enable_all()` / `disable_all()` — activation globale
- `get_enabled_concepts()` — liste des détecteurs activés
- `detect()` — version async (retourne des dicts)
- `detect_sync()` — version sync (retourne des SMCDetection)

### 4. Concepts SMC détectés (15/15)

| # | Concept | Description |
|---|---------|-------------|
| 1 | BOS | Break of Structure — cassure dans le sens de la tendance |
| 2 | CHoCH | Change of Character — cassure contre la tendance |
| 3 | MSS | Market Structure Shift — retournement significatif |
| 4 | FVG | Fair Value Gap — déséquilibre de 3 bougies |
| 5 | IFVG | Inverse FVG — FVG rempli puis inversé |
| 6 | Order Block | Dernière bougie opposée avant un déplacement |
| 7 | Breaker Block | OB échoué qui devient résistance/support |
| 8 | Mitigation Block | OB après un liquidity sweep |
| 9 | Liquidity Sweep | Stop hunt — prix dépasse un swing puis inverse |
| 10 | Equal High | Deux swing highs au même niveau |
| 11 | Equal Low | Deux swing lows au même niveau |
| 12 | Premium/Discount | Division du range en zones premium/discount |
| 13 | OTE | Optimal Trade Entry — zone 62%-79% Fibonacci |

## Architecture

```
ISMCDetector (core/interfaces.py)  ← Port
    ↓
SMCDetector (modules/smc/detector.py)
    ↓
5 sous-détecteurs indépendants :
    ├── StructureDetector      → BOS, CHoCH, MSS
    ├── FairValueGapDetector   → FVG, IFVG
    ├── OrderBlockDetector    → OB, Breaker, Mitigation
    ├── LiquidityDetector     → Sweep, EQH, EQL
    └── PremiumDiscountDetector → Premium/Discount, OTE
```

## Tests — 33 tests (tous ✅)

| Catégorie | Tests |
|-----------|-------|
| Swing points | 4 |
| Structure (BOS, CHoCH, MSS) | 4 |
| Fair Value Gap (FVG, IFVG) | 5 |
| Order Blocks (OB, Breaker, Mitigation) | 4 |
| Liquidité (Sweep, EQH, EQL) | 4 |
| Premium/Discount et OTE | 4 |
| SMCDetector principal | 8 |

## Prochaine phase

**Phase 5 – Stratégies & Signaux** :
- Moteur de stratégies multiples (SMC Trend, Breakout, Momentum, etc.)
- Générateur de signaux (fusion SMC + stratégie, score de confiance)