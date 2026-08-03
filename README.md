# Arty

Plateforme professionnelle de trading Forex (SMC/ICT + IA) — architecture modulaire, évolutive et maintenable.

---

## Prompt de développement – Plateforme de Trading Forex Professionnelle (SMC/ICT + IA)

Tu es un ingénieur logiciel senior spécialisé en Python, MetaTrader 5, algorithmes de trading, architecture logicielle, intelligence artificielle et finance quantitative.

Je souhaite développer une plateforme professionnelle de trading Forex à partir de zéro, avec une architecture modulaire, évolutive et maintenable.

L'objectif n'est pas seulement de créer un bot de trading, mais une plateforme complète capable d'analyser le marché, détecter automatiquement les concepts Smart Money (SMC/ICT), effectuer du backtesting, assister le trader grâce à l'IA et exécuter des ordres sur MetaTrader 5 (en mode Démo dans un premier temps).

---

### Objectifs du projet

#### Marchés

- Forex uniquement (version 1)
- Extension future vers les indices, métaux, matières premières et cryptomonnaies

#### Plateforme cible

- MetaTrader 5
- Compte Démo uniquement (le mode réel devra être désactivé par défaut)

#### Symboles

Le système doit permettre d'ajouter facilement des symboles.

Par défaut :

- EURUSD
- GBPUSD
- USDJPY
- XAUUSD

---

### Architecture générale

Concevoir une architecture propre (Clean Architecture), orientée modules et facilement extensible.

Le projet devra être découpé en plusieurs modules indépendants.

Exemple :

- configuration
- authentification MT5
- récupération des données
- moteur d'analyse SMC
- moteur de stratégies
- moteur de signaux
- gestion du risque
- exécution des ordres
- journalisation
- notifications
- tableau de bord
- IA Assistant
- backtesting
- optimisation
- statistiques
- utilitaires
- tests

Chaque module devra être indépendant.

---

### Fonctionnalités

#### Connexion MT5

- Connexion robuste
- Reconnexion automatique
- Vérification du compte
- Gestion complète des erreurs
- Vérification du statut du terminal

#### Données de marché

Le système doit pouvoir récupérer :

- données historiques
- données temps réel
- OHLC
- Tick
- Volume
- Spread

avec cache et contrôle des erreurs.

#### Moteur Smart Money (SMC)

Le cœur de la plateforme devra détecter automatiquement :

- Break Of Structure (BOS)
- Change Of Character (CHoCH)
- Market Structure Shift (MSS)
- Fair Value Gap (FVG)
- Inverse FVG (IFVG)
- Order Block
- Breaker Block
- Mitigation Block
- Liquidity Sweep
- Equal High
- Equal Low
- Premium / Discount
- Optimal Trade Entry (OTE)
- Sessions :
  - Londres
  - New York
  - Asie
- Kill Zones
- Points d'intérêt (POI)

Toutes les détections devront être indépendantes afin de pouvoir être activées ou désactivées.

#### Moteur de stratégies

Le système devra permettre plusieurs stratégies.

Exemples :

- SMC Trend Following
- Breakout
- Momentum
- Reversal
- Scalping
- Swing Trading

Chaque stratégie devra être activable individuellement.

Le système devra permettre de créer facilement de nouvelles stratégies.

#### Générateur de signaux

Le moteur devra :

- fusionner les informations SMC
- appliquer les règles de la stratégie
- attribuer un score de confiance
- générer un signal Achat/Vente

Chaque signal devra contenir :

- Prix d'entrée
- Stop Loss
- Take Profit
- Ratio Risque/Rendement
- Niveau de confiance
- Justification

#### Gestion du risque

Le module devra gérer :

- risque par trade (paramétrable)
- taille de position automatique
- Stop Loss obligatoire
- Take Profit obligatoire
- risque journalier maximal
- drawdown maximal
- nombre maximal de positions
- nombre maximal de pertes consécutives
- un seul trade simultané par symbole (paramétrable)

#### Exécution des ordres

Connexion via MetaTrader5 officiel.

Fonctions :

- ouverture
- fermeture
- modification
- trailing stop
- break-even
- gestion des erreurs
- reprise automatique

Le mode Réel devra rester désactivé par défaut.

#### Backtesting

Créer un moteur de backtesting permettant :

- simulation historique
- courbe de capital
- Profit Factor
- Win Rate
- Drawdown
- Sharpe Ratio
- Expectancy
- statistiques détaillées
- export CSV et PDF

#### Dashboard

Créer une interface moderne affichant :

- graphiques
- positions ouvertes
- historique
- statistiques
- signaux
- état du bot
- performances
- gestion des stratégies
- paramètres

#### IA Assistant

Créer un assistant IA capable de :

- expliquer les signaux
- analyser les trades
- résumer les performances
- répondre aux questions de l'utilisateur
- suggérer des améliorations

L'IA ne devra jamais ouvrir une position seule sans validation des règles de la stratégie.

#### Journalisation

Créer un système complet de logs :

- console
- fichier
- erreurs
- exécution
- signaux
- positions

#### Notifications

Support :

- Telegram
- Discord
- Email

#### Configuration

Tous les paramètres devront être modifiables :

- symboles
- timeframe
- risque
- sessions
- horaires
- Stop Loss
- Take Profit
- filtres
- stratégies actives
- mode Démo/Réel

---

### Exigences techniques

Le code devra être :

- professionnel
- documenté
- fortement commenté
- modulaire
- facilement testable
- facilement maintenable

Respecter les bonnes pratiques Python.

Utiliser :

- MetaTrader5
- Pandas
- NumPy
- Pydantic
- FastAPI (pour l'API)
- PostgreSQL
- SQLAlchemy
- WebSocket
- Docker
- Pytest

---

### Documentation

Générer automatiquement :

- README complet
- Architecture UML
- Diagrammes de classes
- Diagrammes de séquence
- Diagrammes de composants
- Documentation des API
- Guide d'installation
- Guide utilisateur
- Guide développeur

---

### Développement

Développer le projet progressivement.

Pour chaque étape :

1. expliquer l'architecture retenue ;
2. créer l'arborescence ;
3. écrire le code complet ;
4. tester le module ;
5. documenter le module ;
6. attendre la validation avant de passer au suivant.

Le projet doit être conçu pour évoluer vers une plateforme de trading professionnelle, robuste, extensible et prête à accueillir de nouvelles stratégies et fonctionnalités.

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
| 9 | API & Dashboard | ⏳ À venir |
| 10 | IA Assistant | ⏳ À venir |
| 11 | Notifications | ⏳ À venir |

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
