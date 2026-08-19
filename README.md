# Arty

Plateforme professionnelle de trading Forex (SMC/ICT + IA) — architecture modulaire, évolutive et maintenable.

---

## 🤖 Qu'est-ce qu'Arty ?

**Arty** est un bot de trading Forex automatisé qui analyse le marché avec les concepts **Smart Money Concepts / ICT** et exécute des ordres sur **MetaTrader 5** — en mode **Démo** par défaut (le mode réel est strictement désactivé tant que `ALLOW_LIVE_TRADING=false`).

### Comment fonctionne le bot

1. **Connexion MT5** — Arty se connecte à votre terminal MetaTrader 5 (identifiants dans `.env`), vérifie que le compte est bien un compte démo et surveille la connexion en continu (reconnexion automatique).
2. **Analyse de marché** — Le moteur SMC détecte automatiquement les concepts Smart Money sur les bougies en temps réel.
3. **Génération de signaux** — La stratégie active (*SMC Trend Following*) fusionne les détections SMC et produit des signaux complets : entrée, Stop Loss, Take Profit, ratio R/R et niveau de confiance.
4. **Gestion du risque** — Chaque signal passe par une batterie de contrôles avant exécution (voir ci-dessous).
5. **Exécution & suivi** — L'ordre est envoyé à MT5, puis Arty gère la position de façon dynamique (break-even, TP partiel, trailing stop).

### Détections Smart Money (SMC/ICT)

Le moteur détecte : Break Of Structure (BOS), Change Of Character (CHoCH), Market Structure Shift (MSS), Fair Value Gap (FVG) et inverse FVG, Order Blocks, Breaker & Mitigation Blocks, Liquidity Sweeps, Equal Highs/Lows, zones Premium/Discount, Optimal Trade Entry (OTE), sessions (Asie, Londres, New York), Kill Zones et Points d'Intérêt (POI).

Chaque détection est indépendante et peut être activée/désactivée dans la configuration.

### Gestion du risque (sécurité intégrée)

- Risque par trade paramétrable (défaut : 1 % du capital) et taille de position automatique
- Sizing limité par la marge disponible (facteur de sécurité 80 %)
- Stop Loss et Take Profit obligatoires sur chaque ordre
- Filtre de spread (par symbole : EURUSD 30 pts, XAUUSD 200 pts)
- Circuit breakers : perte journalière max (3 %), drawdown max (10 %), pertes consécutives max (3), nombre max de positions ouvertes (3)
- Un seul trade simultané par symbole
- Politique confiance/RR adaptative : confiance 0.60–0.85 → RR minimum 2.0 ; confiance ≥ 0.85 → politique standard
- **Blocage automatique de tout compte réel** en mode démo (alerte critique, aucun ordre exécuté)

### Suivi actif des positions

- **Break-even** automatique à +1R
- **Take Profit partiel** (50 %) à +2R
- **Trailing stop** à partir de +3R (distance 1R)

### Autres fonctionnalités

- **Backtesting** — simulation historique avec courbe de capital, Profit Factor, Win Rate, Drawdown, Sharpe Ratio, Expectancy
- **API REST + WebSocket** (FastAPI) — état du bot, positions, signaux, santé, contrôle MT5 (`/health`, `/mt5/connect`, etc.)
- **Notifications** — Telegram, Discord, Email (trade ouvert/fermé, erreurs critiques, rapport journalier)
- **Assistant IA** — explique les signaux, analyse les trades et résume les performances (l'IA ne peut jamais ouvrir une position seule)
- **Persistance PostgreSQL** — trades, statistiques et historique
- **Symboles actifs** : EURUSD et XAUUSD (facilement extensibles)

---

## État du développement

| Phase | Module | Statut |
|-------|--------|--------|
| 1 | Fondations (config, logging, core) | ✅ Terminé |
| 2 | Connexion MT5 | ✅ Terminé |
| 3 | Données de marché | ✅ Terminé |
| 4 | Moteur SMC | ✅ Terminé |
| 5 | Stratégies & Signaux | ✅ Terminé |
| 6 | Gestion du risque | ✅ Terminé |
| 7 | Exécution des ordres | ✅ Terminé |
| 8 | Backtesting | ✅ Terminé |
| 9 | API & Dashboard | ✅ Terminé |
| 10 | IA Assistant | ✅ Terminé |
| 11 | Notifications | ✅ Terminé |

---

## Installation rapide

```powershell
cd C:\Arty

# Créer l'environnement virtuel
python -m venv .venv
.venv\Scripts\activate

# Installer les dépendances
pip install -e ".[dev]"

# Configurer l'environnement
copy .env.example .env

# Lancer Arty
arty serve
# ou
python -m arty_trading.cli serve
```

Ouvrir http://localhost:8000/health

---

## 🧪 Lancer Arty sur un compte DÉMO MT5

Le mode **`demo`** connecte réellement le bot à votre compte démo MetaTrader 5
et exécute de **vrais ordres sur ce compte démo** (aucun argent réel engagé).

### 1. Configurer le compte démo dans `.env`

```env
# Mode démo : connexion réelle au compte démo + exécution réelle des ordres
TRADING_MODE=demo
ALLOW_LIVE_TRADING=false

# Identifiants de VOTRE compte démo MetaTrader 5
MT5_LOGIN=12345678
MT5_PASSWORD=VotreMotDePasse
MT5_SERVER=MetaQuotes-Demo
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
```

> 💡 `ALLOW_LIVE_TRADING` reste `false` : le mode démo n'en exige pas.
> Le trading réel (`live`) reste donc **strictement désactivé**.

### 2. Vérifications de sécurité automatiques

- **Compte réel bloqué** : si le compte connecté s'avère être un compte
  **réel** (et non démo), le trading est automatiquement refusé et une alerte
  critique est envoyée. Aucun ordre ne sera exécuté sur un compte réel.
- **Spread filtré** : les trades sont bloqués si le spread dépasse
  `MAX_SPREAD_POINTS` (défaut 30).
- **Circuit breakers** : perte journalière max, pertes consécutives max et
  drawdown max — le reset journalier se fait automatiquement à minuit UTC.

### 3. Lancer

```powershell
.\run.ps1
# ou
python -m arty_trading.cli serve
```

Vérifier l'état :

```powershell
Invoke-RestMethod http://localhost:8000/health
```

La réponse indique `trading_mode: demo`, `mt5_connected`, et `engine_running`.

### 4. Alertes Telegram (optionnel mais recommandé)

Renseigner dans `.env` :

```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...   # obtenu via @BotFather
TELEGRAM_CHAT_ID=123456789
```

Le bot enverra alors :
- 🔔 nouveau trade ouvert / fermé (avec résultat)
- ❌ erreurs critiques (échec d'exécution, perte de connexion MT5, drawdown atteint)
- 📊 rapport journalier simple (à chaque reset UTC)

---

## Structure du projet

```
Arty/
├── src/arty_trading/
│   ├── core/              # Entités domaine, interfaces, enums
│   ├── config/            # Configuration Pydantic
│   ├── infrastructure/    # MT5, DB, cache, notifications
│   ├── application/       # Cas d'usage, orchestration
│   ├── modules/           # Modules métier (SMC, stratégies, etc.)
│   └── api/               # FastAPI + WebSocket
├── tests/
├── docs/
├── docker/
├── run.ps1                # Script de lancement Windows
├── pyproject.toml
└── docker-compose.yml
```

---

## Licence

MIT
