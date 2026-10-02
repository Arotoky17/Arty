# Préenregistrement — Setup 1 : BOS + retest OB/FVG

**Statut : EN ATTENTE DE VALIDATION PAR L'UTILISATEUR. Aucun run de baseline autorisé.**

Préparé le 2 octobre 2026. Setup : `setup_1_bos_retest_ob_fvg_v1`.
Phase : baseline de développement, une seule variante prévue.
Référence avant refactor : `ff73228102403e11f9f7a9908de9349a1db00a82`.
Commit final : à renseigner avant signature (travail non commité).
Responsable et approbateur : utilisateur, signature/date à renseigner.

## Hypothèse et règles proposées, à approuver avant tout run

Un BOS confirmé avec displacement suivi du premier retest d'un OB frais ou FVG
valide a une expectancy nette positive. Aucun filtre régime, biais HTF, killzone,
session, news ou score de confluence. Les sessions/news influencent uniquement
les coûts. Les régimes du hold-out décrivent la couverture, jamais un choix des
dates selon la performance.

- Instrument proposé : XAUUSD ; signal et exécution M5, bougies fermées UTC.
- BOS : cassure en clôture d'un swing confirmé ; corps strictement supérieur
  au seuil ATR de `definitions.yaml`. Pas de CHoCH/MSS comme signal de continuation.
- Zone : plus récente parmi celles déjà confirmées à la clôture du BOS,
  OB frais prioritaire, sinon FVG ; aucun choix selon le résultat futur.
- Limite : milieu de la zone ; dépôt à la clôture, fill dès la bougie suivante.
  Traversée d'au moins 0,1 pip, probabilité 1, expiration 10 bougies, seed 0.
- Stop : bord opposé + buffer de 0,1 ATR ; TP fixe 2R.
- Risque initial fixe 1R, budget proposé 1 % ; aucun sizing par confluence.
  Une position à la fois, pas de pyramide, trailing ou sortie partielle.
  Aucun deuxième trade sur le même BOS après fill.
- Définitions dans `definitions.yaml`, règles proposées de stratégie dans
  `setup1_preregistration.yaml` ; toutes sont figées après approbation.

## Données et split figés

- Source attendue : Dukascopy M1 bid/ask XAUUSD du 2020-01-01 à la dernière
  bougie fermée réellement disponible à la date d'import, manifeste SHA-256.
- Disponibles : janvier–juin 2024 seulement. Couverture requise : **NON FOURNIE**.
- Dev : `[2020-01-01 00:00 UTC, 2025-01-01 00:00 UTC)`.
- Purge : `[2025-01-01, 2025-01-16)`, exclue des deux jeux ; 10 jours ouvrés
  lundi–vendredi hors 1er janvier, calendrier explicite dans `split.yaml`.
- Hold-out : début figé au 2025-01-16 ; fin calculée exclusivement depuis la
  dernière clôture M1 commune bid/ask importée, jamais une date future.
  **Indisponible : aucune donnée locale postérieure au début du hold-out.**
- Régimes : ADX Wilder D1(14), tendance ≥25, range ≤20, épisodes de 3 barres ;
  structure H4 sur swings confirmés, épisodes de 6 barres. Les deux mesures
  observent tendance/range dans janvier–juin 2024, mais la couverture complète
  dev et hold-out reste **NON CONFIRMÉE : données manquantes**.
  Aucun déplacement des dates pour améliorer les résultats.
- Audit mensuel des trous, doublons, prix aberrants, weekends, UTC et
  disponibilité HTF à la clôture requis ; revue humaine des anomalies/détecteurs.

## Coûts et hypothèses d'exécution

`execution.yaml` contient des valeurs illustratives à calibrer : spreads par
session UTC, override horaire, slippage 0,2 pip par côté et commission 3,5 unités
de compte par lot et par côté. Autour des NFP/FOMC/CPI : spread ×1,5 pendant les
15 minutes avant/après l'heure officielle UTC fournie. Calendrier :
`config/news_calendar.csv`, 224 événements 2020–2026, sources BLS/Federal Reserve
citées par ligne, UTC/DST vérifiés. Les événements futurs sont marqués scheduled ;
l'heure de deux futurs FOMC reste conventionnelle et à confirmer avant échéance.
Broker, devise USD, contrat XAUUSD, spread réel et convention OHLC : à confirmer.
Swap non modélisé : l'ajouter ou exclure explicitement l'exposition overnight
AVANT approbation si nécessaire.

Sensibilité : coûts totaux ×1, ×1,5, ×2, mêmes fills, volumes et prix. Un seul run
et une seule ligne du registre. Aucun réglage des seuils à partir de ce stress.
Il ne simule pas un changement de fill sous fort spread.

## Critères de succès — tous obligatoires

1. Expectancy nette en R >0 ET borne basse de l'IC bootstrap 95 % >0.
2. PF net de coûts >1,2.
3. Au moins 100 trades indépendants en dev ET 100 en hold-out.
4. Évaluation séparée dev puis hold-out, sans réajustement intermédiaire.

Bootstrap proposé : blocs hebdomadaires calendaires UTC, 10 000 réplications,
seed 20261002, IC percentile bilatéral 95 %. Toutes les tentatives sont dans le
registre, y compris interrompues. Les audits visuels ne regardent aucun P&L.

Échec d'un critère ou nombre de trades insuffisant : **suppression du setup,
aucun réajustement**. Données manquantes : « non évalué », jamais une autorisation.
L'usage des stress ×1,5/×2 comme critères éliminatoires supplémentaires reste à
valider AVANT le run ; les critères par défaut concernent les coûts calibrés ×1.

## Empreintes et approbation

L'empreinte canonique couvre règles et critères du setup, `definitions.yaml`,
`split.yaml`, `execution.yaml` et le contenu du calendrier news. Le moteur vérifie sa signature, l'empreinte de
l'audit complet et chaque fichier source avant un run du Setup 1.

```powershell
.venv\Scripts\python.exe -c "from arty_trading.validation.preregistration import configuration_sha256; print(configuration_sha256())"
```

- Empreinte à signer : `reports/prebaseline/preregistration_snapshot.json`.
- Empreinte audit complet 2020–2026 : à renseigner après fourniture des données.
- Approbation utilisateur : **EN ATTENTE**, nom/date UTC/signature à renseigner.
- `setup1_preregistration.yaml` garde `pending_user_validation` et
  `missing_2020_2026` jusqu'à votre validation explicite.

## Accès final au hold-out

Après succès dev et autorisation de la phase finale, une seule utilisation de
`load_holdout(setup_id, reason, enabled=True, loader=...)`.
Raison prévue : « évaluation finale selon le préenregistrement approuvé ».
L'audit OHLC/ADX a son propre journal de marché sans P&L et ne consomme pas cet
accès de performance. Aucun run hold-out avant la phase finale autorisée.

## Résultats et décision après phase

Dev : NON EXÉCUTÉ. Hold-out : NON EXÉCUTÉ. Décision : NON ÉVALUÉ.
Ne pas réécrire les critères après observation d'un résultat.
