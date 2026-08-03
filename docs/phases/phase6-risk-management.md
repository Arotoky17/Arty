# Phase 6 – Gestion du risque

## Objectif

Mettre en place un gestionnaire de risque central qui valide les signaux, calcule la taille de position optimale, et gère les limites de risque (positions ouvertes, drawdown, pertes consécutives, risque journalier).

## Modules livrés

### 1. RiskManager (`modules/risk/manager.py`)

**`RiskManager`** implémente `IRiskManager` :

#### Validation des signaux (`validate_signal`)
8 vérifications :
1. Stop Loss présent et différent de l'entrée
2. Take Profit présent et différent de l'entrée
3. Score de confiance >= `min_confidence` (défaut 0.5)
4. Ratio R/R >= `min_risk_reward` (défaut 1.5)
5. Nombre de positions < `max_open_positions` (défaut 3)
6. Risque journalier non dépassé (`max_daily_risk` = 3%)
7. Drawdown non dépassé (`max_drawdown` = 10%)
8. Pertes consécutives < `max_consecutive_losses` (défaut 3)
9. Un seul trade par symbole (si `one_trade_per_symbol` = True)

#### Calcul de la taille de position (`calculate_position_size`)
- `risk_amount = balance * risk_per_trade` (1% par défaut)
- `sl_pips = sl_distance / pip_size` (0.0001 ou 0.01 pour JPY)
- `volume = risk_amount / (sl_pips * pip_value_per_lot)`
- Volume minimum : 0.01 lot

#### Vérification d'ouverture (`can_open_trade`)
- Nombre max de positions
- Un seul trade par symbole (paramétrable)

### 2. Suivi des positions

| Méthode | Description |
|---------|-------------|
| `register_trade(trade)` | Enregistre un trade ouvert |
| `close_trade(trade, profit)` | Ferme un trade, met à jour les compteurs |
| `update_equity(equity)` | Met à jour l'équité et calcule le drawdown |
| `reset_daily()` | Réinitialise les compteurs journaliers |
| `get_risk_report()` | Retourne un rapport complet de l'état du risque |

### 3. Configuration (`config/settings.py`)

```python
class RiskSettings(BaseSettings):
    risk_per_trade: float = 0.01       # 1% par trade
    max_daily_risk: float = 0.03       # 3% par jour
    max_drawdown: float = 0.10         # 10% drawdown max
    max_open_positions: int = 3        # 3 positions simultanées
    max_consecutive_losses: int = 3    # 3 pertes consécutives max
    one_trade_per_symbol: bool = True  # 1 trade par symbole
```

## Architecture

```
IRiskManager (core/interfaces.py)  ← Port
    ↓
RiskManager (modules/risk/manager.py)
    ↓
    ├── validate_signal()     → 8 vérifications
    ├── calculate_position_size() → Formule basée sur le risque
    ├── can_open_trade()      → Max positions + 1 trade/symbole
    ├── register_trade()      → Suivi des positions
    ├── close_trade()         → Compteurs (perte journalière, pertes consécutives)
    ├── update_equity()       → Drawdown
    └── get_risk_report()     → Rapport complet
```

## Tests — 30 tests (tous ✅)

| Catégorie | Tests |
|-----------|-------|
| Initialisation | 2 |
| Validation des signaux | 10 |
| Calcul de la taille de position | 4 |
| Vérification d'ouverture | 5 |
| Suivi des trades | 4 |
| Drawdown | 3 |
| Reset journalier | 1 |
| Rapport de risque | 1 |

## État global du projet

| Phase | Tests |
|-------|-------|
| 1-3 (Fondations, MT5, Marché) | 72 |
| 4 (SMC) | 33 |
| 5 (Stratégies & Signaux) | 30 |
| 6 (Gestion du risque) | 30 |
| **Total** | **165** |

## Prochaine phase

**Phase 7 – Exécution des ordres** :
- Ouverture/fermeture/modification d'ordres via MT5
- Trailing stop et break-even
- Gestion des erreurs et reprise automatique
- Mode Réel désactivé par défaut