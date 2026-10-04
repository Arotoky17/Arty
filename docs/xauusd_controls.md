# Contrôles XAUUSD — 3 octobre 2026

Aucun backtest historique exécuté, aucun accès de performance au hold-out.
Tests des moteurs : données synthétiques et registre temporaire.

## Unités et exécution

**1 pip = 0,01 USD par once**, contrat proposé 100 onces par lot.
Slippage par exécution : limite 0,10 USD (10 pips), stop/market 0,30 USD (30 pips).
Traversée limite 0,10 USD ; stress 0,30 USD. Stop autorisé sur barre du fill,
prioritaire sur TP ; TP interdit sur cette barre. Hypothèses à confirmer broker.
Stop minimal : distance entrée–stop ≥0,5 ATR du BOS, sinon rejet.
ATR `require_full` : 14 TR avec clôture précédente, donc 15 barres tradables.
Plafonds proposés : levier brut agrégé 10×, marge utilisée 50 %, levier broker 20×,
pas/minimum 0,01 lot. Réduire le volume vers le bas ou refuser. Rapports par trade :
risque initial USD, coûts USD, coûts/R, levier et marge (`execution_audit`).

## Spread et calendrier

P75 horaire UTC de `(ask−bid)/prix médian` sur **tout le dev 2020–2024**, hors
fermetures, réouverture et news. Application : ratio × prix courant, puis
conversion avec 0,01 USD/pip. News NFP/FOMC/CPI : ×1,5 dans ±15 minutes.
Sources BLS/Fed dans le CSV ; futurs horaires non attestés marqués.
Sensibilité ×1/×1,5/×2 sur chemin fixe ; pas de simulation supplémentaire gratuite.

Données disponibles : 2024-H1 et quelques jours 2020. Diagnostic partiel marqué
`coverage_complete: false` avec SHA-256 ; la porte de lancement le refuse.
Hold-out 2025 : dev complet figé. 2026 : seules cotations de 2025, sans P&L,
fichier figé avant run dans `cost.annual_calibration_paths`, contenu signé.
Aucune cotation de l’année évaluée ne calibre son propre spread. Accès aux
cotations journalisé ; aucun fichier annuel complet validé actuellement.

Pause NY 17–18 h, weekend vendredi 17 h à dimanche 18 h, DST NY.
Réouverture : 20 minutes d’exclusion proposées. Jours entiers non tradables en
date NY : 24–26/12, 31/12–02/01, Vendredi saint. Exclusion prudente, sans attester
les horaires du fournisseur. Raw jamais réécrit.
D1 **NY 17 h actif** : dérivé de H1 par le provider ; ADX, PDH/PDL et biais D1
partagent `validation/daily_context.py`, disponibilité à clôture, DST testé.
H4 conserve son ancrage UTC.

## Provenance et puissance

Overrides hérités de `config/settings.py`, présents dans `2afe9e0` et `ebb1c92`
du 24 août 2026 ; YAML du 2 octobre (`bf2297d`). Vérification le 3 octobre par
`git log -S`/`git show`. Date de choix initial et usage initial de P&L inconnus,
à confirmer : aucun P&L utilisé pour les présents contrôles.

Revue bloquante : **20 graphiques M5 chacun, swing/displacement/OB/FVG, ≥16/20**.
Sweep/EQH/EQL informatifs. Après règles de jours fériés/warmup, utiliser
`precision_detection_review` et `precision_regimes` ; verdict humain en attente.
Puissance : hypothèses non mesurées 250 trades dev, 100 hold-out, écart-type R=1,5,
effet de plan=2, puissance 80 %, confiance 95 %. Expectancy minimale détectable
approximative 0,376 R / 0,594 R ; aucune garantie du bootstrap ni comptage réel.
Rapport `reports/prebaseline/statistical_power.json`.

## Blocage réseau et reprise locale

Tentatives précédentes : sandbox Windows `WinError 10013` (socket interdite),
et Dukascopy HTTP 429/503/timeout de lecture, aussi hors sandbox autorisé.
Donc blocage pas uniquement sandbox. Aucun nouveau téléchargement dans cette révision.
11 fichiers bid/ask journaliers validés ; interruption le 6 janvier 2020 ask.
Commande exacte en PowerShell local, même dépôt :

```powershell
Set-Location -LiteralPath 'D:\Arty'
.\.venv\Scripts\python.exe .\tools\fetch_dukascopy.py --output .\data\raw
```

Relancer la même commande après interruption : SHA-256, fichiers existants
conservés, reprise des manquants, journal append-only, séquentiel avec backoff
et Retry-After. Aucune donnée chargée dans le moteur. Hold-out : début 15/01/2025,
fin indisponible avant import complet, puis dernière clôture commune figée ;
les dates futures sont refusées.
Après import complet, commandes de marché uniquement :

```powershell
.\.venv\Scripts\python.exe .\tools\audit_data.py --data .\data\raw --output .\reports\data_readiness\raw_audit
.\.venv\Scripts\python.exe .\tools\calibrate_spreads.py --data .\data\raw --output .\reports\data_readiness\spreads_full_dev
.\.venv\Scripts\python.exe .\tools\calibrate_spreads.py --data .\data\raw --effective-year 2026 --allow-holdout-quotes --output .\reports\data_readiness\spreads_2026
.\.venv\Scripts\python.exe .\tools\audit_monthly_regimes.py --data .\data\raw --allow-holdout-market-audit
.\.venv\Scripts\python.exe .\tools\statistical_power.py
```

Calibrages produits dans reports ; publication config et journalisation avant
nouvelle signature. Sans données complètes et signature explicite, Setup 1 reste
bloqué. Baseline dédiée : implémentation ultérieure, aucun run ici.
À valider : contrat/frais/marge broker, plafonds proposés, provenance des overrides,
hypothèses de puissance, réouverture et revue M5.