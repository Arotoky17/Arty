# Phase 1 – Fondations

## Objectif

Poser les bases architecturales de la plateforme : configuration, domaine, logging, utilitaires et API minimale.

## Modules livrés

### 1. Configuration (`config/`)

- `Settings` Pydantic centralisé
- Chargement depuis `.env`
- Sous-configurations : MT5, risque, sessions, DB, API, notifications, IA
- **Garde-fou sécurité** : mode DEMO forcé si `ALLOW_LIVE_TRADING=false`

### 2. Core Domain (`core/`)

- **Entités** : `Candle`, `Signal`, `Trade`, `TradingAccount`
- **Enums** : `TradingMode`, `Direction`, `TimeFrame`, `SMCConcept`, `StrategyType`, etc.
- **Interfaces (Ports)** : `IMT5Connector`, `IMarketDataProvider`, `IStrategy`, `IRiskManager`, etc.

### 3. Logging (`logging/`)

- Console + fichiers rotatifs par catégorie
- Helpers : `log_signal()`, `log_trade()`, `log_error()`

### 4. Utilitaires (`utils/`)

- Calcul pips et arrondi prix
- Sessions ICT (Asie, Londres, NY)
- Kill Zones

### 5. API (`api/`)

- FastAPI avec lifespan
- Endpoints : `/health`, `/config/symbols`, `/config/risk`

### 6. CLI (`cli.py`)

- `arty-trading serve` – lance l'API
- `arty-trading info` – affiche la config
- `arty-trading version`

## Tests

| Fichier | Couverture |
|---------|------------|
| `test_config.py` | Settings, sécurité demo |
| `test_core_entities.py` | Candle, Signal, Trade |
| `test_utils.py` | Pips, sessions, kill zones |
| `test_api.py` | Endpoints FastAPI |
| `test_logging.py` | Setup et helpers |

## Lancer les tests

```powershell
pip install -e ".[dev]"
pytest
```

## Prochaine phase

**Phase 2 – Connexion MT5** :
- Connecteur robuste avec reconnexion
- Vérification compte et terminal
- Tests avec mock MT5
