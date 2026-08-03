# Phase 2 – Connexion MT5

## Objectif

Mettre en place un connecteur MetaTrader 5 robuste avec gestion des erreurs, reconnexion automatique et vérifications de sécurité (compte et terminal).

## Modules livrés

### 1. Connecteur MT5 (`infrastructure/mt5/connector.py`)

**Classe `MT5Connector`** implémente le port `IMT5Connector` :

- **Connexion** : `connect()` → initialise le terminal, vérifie l'état, authentifie, récupère le compte
- **Déconnexion** : `disconnect()` → shutdown propre du terminal MT5
- **Reconnexion** : `reconnect()` → disconnect + connect
- **État temps réel** : `is_connected()` → interroge le terminal pour vérifier la connexion active
- **Infos compte** : `get_account_info()` → retourne l'entité `TradingAccount`
- **Statut complet** : `get_connection_status()` → dict avec connected, mt5_available, login, server, account
- **Infos terminal** : `get_terminal_info()` → dict avec build, connected, trade_allowed, etc.

### 2. Sécurité

- **Mode dégradé** : si le package `MetaTrader5` n'est pas installé, le connecteur fonctionne en mode mock
- **Compte réel forcé en démo** : si `ALLOW_LIVE_TRADING=false`, un compte réel (trade_mode=1) est automatiquement forcé en mode DEMO
- **Garde-fou intégré** : vérification du terminal (connected, trade_allowed) avant authentification

### 3. Exceptions personnalisées

| Exception | Usage |
|-----------|-------|
| `MT5ConnectionError` | Erreur de connexion (non connecté, initialize échoué) |
| `MT5TerminalError` | Erreur liée au terminal (terminal_info indisponible) |
| `MT5AccountError` | Erreur liée au compte (account_info indisponible) |

### 4. API MT5

Endpoints ajoutés à l'API FastAPI :

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| `/mt5/status` | GET | Statut de la connexion MT5 |
| `/mt5/connect` | POST | Tente une connexion MT5 |
| `/mt5/disconnect` | POST | Déconnecte MT5 |
| `/mt5/reconnect` | POST | Tente une reconnexion MT5 |

## Architecture

```
Settings (config)
    ↓
MT5Connector (infrastructure/mt5/connector.py)
    ↓
IMT5Connector (core/interfaces.py)  ← Port
    ↓
TradingAccount (core/entities.py)  ← Entité domaine
```

Le connecteur utilise `asyncio.to_thread()` pour exécuter les appels synchrones MT5 dans un thread pool, évitant ainsi de bloquer l'event loop async.

## Tests

| Fichier | Couverture |
|---------|------------|
| `test_mt5_connector.py` | 16 tests : init, connect, disconnect, reconnect, is_connected, get_account_info, get_connection_status, get_terminal_info, sécurité (compte réel forcé en démo), perte de connexion |

### Scénarios testés

- ✅ Initialisation du connecteur
- ✅ Connexion réussie (avec MT5 mocké)
- ✅ Échec initialize
- ✅ Échec login
- ✅ Déconnexion propre
- ✅ Reconnexion
- ✅ Statut de connexion
- ✅ Infos terminal
- ✅ Mode mock (MT5 non disponible)
- ✅ Compte réel forcé en démo
- ✅ Détection de perte de connexion
- ✅ get_account_info lève une erreur si non connecté

## Lancer les tests

```powershell
pytest tests/test_mt5_connector.py -v
```

## Configuration

Variables d'environnement (`.env`) :

```env
MT5_LOGIN=12345
MT5_PASSWORD=your_password
MT5_SERVER=YourBroker
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_TIMEOUT=60000
```

## Prochaine phase

**Phase 3 – Données de marché** :
- Récupération des données historiques (OHLCV)
- Données temps réel (ticks)
- Cache et contrôle des erreurs
- Implémentation de `IMarketDataProvider`