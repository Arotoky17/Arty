# Arty

Plateforme professionnelle de trading Forex (SMC/ICT + IA) — architecture modulaire, évolutive et maintenable.

## Validation de recherche SMC

L'infrastructure de l'étape 1 centralise les définitions dans
[`config/definitions.yaml`](config/definitions.yaml), protège le split dev/hold-out
et impose un registre SQLite ainsi que des modèles de coûts et de fill aux
backtests. Voir [le guide de validation](docs/validation.md) pour lancer les tests,
lire les métriques, consulter le registre et connaître les changements de
comportement. Remplir [le préenregistrement](preregistration.md) avant chaque phase.

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
- Filtre de spread specialise XAUUSD (defaut : 200 points MT5)
- Circuit breakers : perte journalière max (3 %), drawdown max (10 %), pertes consécutives max (3), nombre max de positions ouvertes (3)
- Un seul trade simultané par symbole
- Politique confiance/RR adaptative : confiance 0.60–0.85 → RR minimum 2.0 ; confiance ≥ 0.85 → politique standard
- **Blocage automatique de tout compte réel** en mode démo (alerte critique, aucun ordre exécuté)

### Suivi actif des positions

- **Break-even** automatique à +1R
- **Take Profit partiel** (50 %) à +2R
- **Trailing stop** à partir de +3R (distance 1R)

### Qualité des Order Blocks (Phase 12 — optionnel)

Activé par `OB_QUALITY_ENABLED=true`, le bot ne trade plus « tous les Order
Blocks » mais uniquement les OB de **haute qualité** :

- chaque OB est noté sur 100 (displacement, hauteur de zone, mitigations,
  fraîcheur, tendance H1, confluences Sweep/CHoCH/FVG, Premium/Discount) puis
  classé **Grade A/B/C/D** (seuils 85 / 70 / 50) ;
- seuls les OB dont le grade est >= `OB_MIN_GRADE` (défaut `B`) deviennent des
  setups ;
- un signal OB n'est produit qu'après **confirmation M5** : retest de la zone
  puis bougie de rejet (avec displacement optionnel) ;
- documentation complète : [`docs/phases/phase12-ob-quality.md`](docs/phases/phase12-ob-quality.md).

Désactivé par défaut : le comportement des phases 1-11 reste inchangé.

### Autres fonctionnalités

- **Backtesting** — simulation historique avec courbe de capital, Profit Factor, Win Rate, Drawdown, Sharpe Ratio, Expectancy
- **API REST + WebSocket** (FastAPI) — état du bot, positions, signaux, santé, contrôle MT5 (`/health`, `/mt5/connect`, etc.)
- **Notifications** — Telegram, Discord, Email (trade ouvert/fermé, erreurs critiques, rapport journalier)
- **Assistant IA** — explique les signaux, analyse les trades et résume les performances (l'IA ne peut jamais ouvrir une position seule)
- **Persistance PostgreSQL** — trades, statistiques et historique
- **Symbole actif** : XAUUSD uniquement. L'architecture conserve les abstractions generiques `symbol`, mais le moteur principal n'analyse et ne trade pas EURUSD/GBPUSD/USDJPY.
- **Flux multi-timeframe Gold** : H4 contexte macro, H1 tendance/structure, M5 setup/confirmation/entree.

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
| 12 | Qualité des Order Blocks (Grade A/B) + confirmation M5 | ✅ Terminé |

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
python -m arty_trading serve
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
  `MAX_SPREAD_POINTS` (defaut XAUUSD : 200 points MT5).
- **Circuit breakers** : perte journalière max, pertes consécutives max et
  drawdown max — le reset journalier se fait automatiquement à minuit UTC.

### 3. Lancer

```powershell
.\run.ps1
# ou
python -m arty_trading serve
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
├── src/arty_trading/               # Code applicatif principal
│   ├── api/                       # FastAPI + WebSocket
│   ├── application/               # Cas d’usage et orchestration
│   ├── cli.py                     # Point d’entrée CLI
│   ├── config/                    # Config Pydantic / environnement
│   ├── core/                      # Entités, enum, interfaces
│   ├── infrastructure/            # MT5, persistance, notifications
│   ├── logging/                   # Logging structuré
│   ├── modules/                   # SMC, risque, exécution, IA, backtest
│   ├── utils/                     # Helpers génériques
│   └── __init__.py
├── tests/                         # Tests par catégorie
│   ├── unit/                     # Tests unitaires et de logique
│   ├── integration/              # Tests de connexion / live / intégration
│   ├── e2e/                      # Tests de bout en bout
│   ├── fixtures/                 # Données et fixtures partagées
│   ├── conftest.py               # Config pytest globale
│   └── __init__.py
├── scripts/                       # Scripts utilitaires par rôle
│   ├── launch/                   # Démarrage bot et backtests
│   ├── diagnostics/              # Diagnostic moteur et signaux
│   ├── analysis/                 # Analyse de performance
│   ├── maintenance/              # Nettoyage et maintenance
│   └── calibrate_retest_filter.py
├── docs/                          # Documentation fonctionnelle et technique
│   ├── architecture.md
│   ├── installation.md
│   ├── troubleshooting/
│   └── phases/
├── examples/                      # Scripts de démonstration
├── archive/                       # Fichiers historiques / debug
│   └── diagnostics/
├── data/                          # Données persistantes et état du bot
│   ├── position_states/
│   ├── backtests/
│   └── logs/
├── docker/                        # Fichiers Docker / runtime
├── .github/                       # CI / intégration continue
│   └── workflows/
├── .env.example                   # Modèle de configuration
├── docker-compose.yml             # Stack containerisée
├── pyproject.toml                 # Packaging Python
├── README.md                      # Documentation principale
├── run.bat                        # Lancement Windows
├── run.ps1                        # Lancement PowerShell
├── .gitignore
└── .pytest_cache/                 # Cache local pytest (à ignorer)
```

---

## Licence

MIT


## Contrôles avant la baseline Setup 1

Les audits, la revue visuelle et le préenregistrement sont décrits dans
[les contrôles pré-baseline](docs/prebaseline_controls.md).
L’import append-only, le diagnostic bid/ask, le calendrier news et les régimes
mensuels sont dans [le guide de préparation des données](docs/data_readiness.md).
Les rapports actuels sont dans `reports/data_readiness/`. Le Setup 1 reste bloqué
en attente des données complètes et de votre validation du [préenregistrement](preregistration.md).
# Contrôles XAUUSD avant baseline

Le début du hold-out est fixé au **15 janvier 2025** (purge de 10 jours
lundi–vendredi sans fériés). Import incomplet : fin du hold-out indisponible,
aucun backtest autorisé. Voir [les contrôles et commandes](docs/xauusd_controls.md)
et [le préenregistrement à approuver](preregistration.md).
Revue bloquante : 20 graphiques **M5** pour swing, displacement, OB et FVG,
≥16 corrects sur 20 chacun ; sweep/EQH/EQL restent informatifs.
Après warmup complet et exclusions des jours fériés, les nouveaux graphiques
sont dans `reports/data_readiness/precision_detection_review/`.
XAUUSD : **1 pip = 0,01 USD/once** ; slippage limite 0,10 USD, stop/market 0,30 USD ;
fill traversé 0,10 USD, stress 0,30 USD, stop possible sur barre du fill.
Stop minimal 0,5 ATR, plafonds de levier/marge proposés et coûts/R par trade.
D1 NY 17 h actif pour ADX, PDH/PDL et biais D1. Spread normalisé par le prix :
dev complet et recalibrage annuel sur cotations antérieures requis, actuellement
bloqués par les données manquantes. Le guide ci-dessus fournit la commande exacte
de reprise locale ; `tools/statistical_power.py` calcule la puissance de planification.

L'[audit de l'importateur Dukascopy](docs/dukascopy_importer_audit.md) documente
les réessais, le diagnostic réseau et la reprise limitée au développement avec
`--until '2025-01-01T00:00:00+00:00'`. L'import réel reste incomplet ; aucun
backtest ni accès au hold-out n'est autorisé par cette commande.
