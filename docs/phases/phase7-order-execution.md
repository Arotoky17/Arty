# Phase 7 – Exécution des ordres

## Objectif

Mettre en place un exécuteur d'ordres qui gère l'ouverture, fermeture et modification d'ordres via MT5, avec trailing stop et break-even automatique.

## Modules livrés

### 1. OrderExecutor (`modules/execution/executor.py`)

**`OrderExecutor`** implémente `IOrderExecutor` :

#### Ouverture d'ordres (`open_order`)
- Crée un ordre market BUY/SELL basé sur un signal
- Assigne un ticket MT5 unique
- Préserve le signal_id et le strategy_name
- Mode mock automatique quand MT5 indisponible
- Mode Réel bloqué par défaut (sécurité)

#### Fermeture d'ordres (`close_order`)
- Ferme la position via MT5
- Met à jour close_price et profit
- Retire le trade du suivi interne

#### Modification d'ordres (`modify_order`)
- Modifie le Stop Loss et/ou le Take Profit
- None = inchangé

### 2. Trailing Stop (`apply_trailing_stop`)
- Distance paramétrable en pips (0 = désactivé)
- BUY : SL suit le prix vers le haut uniquement
- SELL : SL suit le prix vers le bas uniquement
- Gestion automatique des pips JPY (0.01) vs standard (0.0001)

### 3. Break-Even (`apply_break_even`)
- Seuil de profit paramétrable en pips (0 = désactivé)
- BUY : SL déplacé à l'entrée quand le prix monte du seuil
- SELL : SL déplacé à l'entrée quand le prix baisse du seuil
- Ne modifie pas si SL déjà à l'entrée

### 4. Mode Mock (quand MT5 indisponible)
- Simulation complète des ordres
- Tickets incrémentaux (10001, 10002, ...)
- Suivi interne des trades mockés
- Bascule automatique vers mock si MT5 échoue

### 5. Mode MT5 Réel (quand MT5 disponible)
- `order_send()` avec `TRADE_ACTION_DEAL` pour ouverture/fermeture
- `order_send()` avec `TRADE_ACTION_SLTP` pour modification
- Vérification du retcode (`TRADE_RETCODE_DONE`)
- Bascule vers mock en cas d'échec

## Architecture

```
IOrderExecutor (core/interfaces.py)  ← Port
    ↓
OrderExecutor (modules/execution/executor.py)
    ↓
    ├── open_order()         → Mock ou MT5 order_send
    ├── close_order()        → Mock ou MT5 order_send
    ├── modify_order()       → Mock ou MT5 order_send
    ├── apply_trailing_stop() → SL suit le prix
    └── apply_break_even()   → SL à l'entrée
```

## Tests — 23 tests (tous ✅)

| Catégorie | Tests |
|-----------|-------|
| Initialisation | 3 |
| Ouverture d'ordres | 5 |
| Fermeture d'ordres | 2 |
| Modification d'ordres | 4 |
| Trailing stop | 4 |
| Break-even | 5 |

## État global du projet

| Phase | Tests |
|-------|-------|
| 1-3 (Fondations, MT5, Marché) | 72 |
| 4 (SMC) | 33 |
| 5 (Stratégies & Signaux) | 30 |
| 6 (Gestion du risque) | 30 |
| 7 (Exécution des ordres) | 23 |
| **Total** | **188** |

## Prochaine phase

**Phase 8 – Backtesting** :
- Simulation historique
- Courbe de capital
- Profit Factor, Win Rate, Drawdown
- Sharpe Ratio, Expectancy
- Statistiques détaillées
- Export CSV et PDF