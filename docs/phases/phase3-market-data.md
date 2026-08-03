# Phase 3 – Données de marché

## Objectif

Mettre en place un provider de données de marché robuste avec cache, gestion des erreurs et mode dégradé. Récupérer les données historiques (OHLCV), les dernières bougies, le spread, les ticks temps réel et les infos symboles via MetaTrader 5.

## Modules livrés

### 1. Cache en mémoire (`infrastructure/cache/memory_cache.py`)

**Classe `InMemoryCache`** — cache générique avec TTL :

- **`get(key)`** — récupère une valeur (None si absente/expirée)
- **`set(key, value, ttl)`** — stocke une valeur avec TTL personnalisable
- **`delete(key)`** — supprime une entrée
- **`clear()`** — vide le cache
- **`cleanup()`** — nettoyage des entrées expirées
- **`__contains__`** — opérateur `in` avec vérification d'expiration
- **`CacheEntry`** — entrée avec expiration automatique (`time.monotonic`)

### 2. Provider de données MT5 (`infrastructure/mt5/market_data.py`)

**Classe `MT5MarketDataProvider`** implémente le port `IMarketDataProvider` :

| Méthode | Description | Source MT5 |
|---------|-------------|------------|
| `get_historical(symbol, timeframe, start, end)` | Données historiques OHLCV | `copy_rates_range` |
| `get_latest_candles(symbol, timeframe, count)` | Dernières bougies | `copy_rates_from_pos` |
| `get_spread(symbol)` | Spread actuel en points | `symbol_info_tick` |
| `subscribe_ticks(symbol)` | Flux temps réel (AsyncIterator) | `symbol_info_tick` (polling) |
| `get_symbol_info(symbol)` | Infos symbole (digits, point, volume) | `symbol_info` |
| `get_available_symbols()` | Liste des symboles MT5 | `symbols_get` |
| `get_tick(symbol)` | Tick actuel (bid, ask, last, volume) | `symbol_info_tick` |

### 3. Cache avec TTL différenciés

| Type de donnée | TTL par défaut | Raison |
|-----------------|----------------|--------|
| Bougies (historique + dernières) | 30s | Les bougies fermées ne changent pas |
| Spread | 5s | Le spread fluctue rapidement |
| Infos symbole | 300s (5 min) | Les infos symbole changent rarement |
| Liste des symboles | 300s (5 min) | La liste ne change pas en pratique |

### 4. Exceptions personnalisées

| Exception | Usage |
|-----------|-------|
| `MarketDataError` | Erreur lors de la récupération des données |
| `SymbolNotFoundError` | Le symbole demandé n'existe pas dans MT5 |

### 5. Mode dégradé

Si le package `MetaTrader5` n'est pas disponible :
- `get_historical` → DataFrame vide
- `get_latest_candles` → liste vide
- `get_spread` → 0
- `get_symbol_info` → valeurs par défaut
- `get_available_symbols` → liste vide
- `get_tick` → None
- `subscribe_ticks` → lève `MarketDataError`

### 6. API Données de marché

Endpoints ajoutés à l'API FastAPI :

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| `/market/symbols` | GET | Tous les symboles disponibles dans MT5 |
| `/market/symbol-info/{symbol}` | GET | Infos d'un symbole (digits, point, volume) |
| `/market/tick/{symbol}` | GET | Tick actuel (bid, ask, last, volume) |
| `/market/spread/{symbol}` | GET | Spread actuel en points |
| `/market/candles/{symbol}` | GET | Dernières bougies OHLCV (params: timeframe, count) |
| `/market/historical/{symbol}` | GET | Données historiques (params: timeframe, start, end) |
| `/market/ticks/{symbol}` | WS | WebSocket ticks temps réel |

## Architecture

```
IMarketDataProvider (core/interfaces.py)  ← Port
    ↓
MT5MarketDataProvider (infrastructure/mt5/market_data.py)
    ↓
InMemoryCache (infrastructure/cache/memory_cache.py)
    ↓
MetaTrader5 (copy_rates_range, copy_rates_from_pos, symbol_info_tick, ...)
```

Le provider utilise `asyncio.to_thread()` pour exécuter les appels synchrones MT5 dans un thread pool, évitant ainsi de bloquer l'event loop async.

Le mapping `TimeFrame` → constante MT5 (`mt5.TIMEFRAME_*`) est initialisé dynamiquement après l'import du package MetaTrader5.

## Tests

| Fichier | Couverture |
|---------|------------|
| `test_market_data.py` | 28 tests : cache (7), provider (21) |

### Scénarios testés

**Cache (`InMemoryCache`) :**
- ✅ set/get basique
- ✅ get clé inexistante
- ✅ delete
- ✅ clear
- ✅ opérateur `in`
- ✅ TTL personnalisé
- ✅ cleanup des entrées expirées

**Provider (`MT5MarketDataProvider`) :**
- ✅ Initialisation (défaut + TTL personnalisés)
- ✅ Mode dégradé (MT5 non disponible) pour toutes les méthodes
- ✅ get_latest_candles avec MT5 mocké
- ✅ get_historical avec MT5 mocké
- ✅ get_spread avec MT5 mocké
- ✅ get_symbol_info avec MT5 mocké
- ✅ get_available_symbols avec MT5 mocké
- ✅ get_tick avec MT5 mocké
- ✅ Cache hit (pas de double appel MT5)
- ✅ clear_cache
- ✅ Réponse vide (MT5 retourne None)
- ✅ SymbolNotFoundError
- ✅ subscribe_ticks lève une erreur si MT5 non disponible
- ✅ count clampé entre 1 et 1000

## Lancer les tests

```powershell
pytest tests/test_market_data.py -v
```

## Exemples d'utilisation

### Récupérer les dernières bougies

```python
from arty_trading.infrastructure.mt5 import MT5MarketDataProvider
from arty_trading.core.enums import TimeFrame

provider = MT5MarketDataProvider()
candles = await provider.get_latest_candles("EURUSD", TimeFrame.H1, count=100)
```

### Récupérer l'historique

```python
from datetime import datetime, timezone

start = datetime(2024, 1, 1, tzinfo=timezone.utc)
end = datetime(2024, 1, 31, tzinfo=timezone.utc)
df = await provider.get_historical("EURUSD", TimeFrame.H1, start, end)
```

### API REST

```powershell
# Dernières bougies
curl "http://localhost:8000/market/candles/EURUSD?timeframe=H1&count=100"

# Spread
curl "http://localhost:8000/market/spread/EURUSD"

# Tick actuel
curl "http://localhost:8000/market/tick/EURUSD"
```

## Prochaine phase

**Phase 4 – Moteur SMC** :
- Détection automatique des concepts Smart Money (BOS, CHoCH, FVG, Order Block, etc.)
- Implémentation de `ISMCDetector`
- Détections indépendantes activables/désactivables