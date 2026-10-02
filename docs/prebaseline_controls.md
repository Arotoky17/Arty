# Contrôles avant la baseline

Aucun backtest de baseline n'a été lancé. Le Setup 1 reste bloqué tant que les
données 2020–2026 et le préenregistrement ne sont pas validés par l'utilisateur.

## Dates figées

Développement : `[2020-01-01, 2025-01-01)`. Purge :
`[2025-01-01, 2025-01-16)`. Hold-out : début au 2025-01-16, fin exclusivement
calculée depuis la dernière bougie fermée commune bid/ask réellement importée.
Sa fin est actuellement `null` : aucune donnée locale ne dépasse son début.
Les dix jours ouvrés excluent les weekends et le 1er janvier explicitement
configuré comme jour férié. Cette convention est à valider.

L'audit initial H1 reste disponible. L'audit mensuel demandé ensuite emploie
ADX Wilder D1(14) et structure H4 confirmée, voir `data_readiness.md`.
Les données 2025–2026 sont absentes : **les deux régimes ne sont pas confirmés**.
L'accès de marché est explicite et journalisé séparément ; il ne consomme pas
l'accès final aux performances d'un setup et ne calcule aucun P&L.

## Commandes sans backtest

Depuis la racine, avec l'environnement virtuel activé :

```powershell
$env:MPLCONFIGDIR = "$PWD/.quality-cache/matplotlib"
python tools/audit_data.py --output reports/prebaseline/data_audit
python tools/review_detections.py --output reports/prebaseline/detection_review
python tools/compare_detections.py --output reports/prebaseline/detection_regression
python tools/audit_holdout_regimes.py --allow-holdout-market-audit --output reports/prebaseline/holdout_market_audit
```

Pour des ticks véritables, ajouter `--ticks fichier.csv` à l'audit (colonnes
timestamp/bid/ask, epoch millisecondes). Les CSV disponibles sont des M1 :
ils permettent de signaler des bougies aberrantes candidates, pas de certifier
la qualité de chaque tick. Les doublons de ticks identiques sont distingués
des cotations différentes partageant la même milliseconde.

L'audit produit JSON et CSV mensuel : trous, doublons, ordre temporel, OHLC,
weekends, plateaux, outliers, couverture et empreintes SHA-256. Les timestamps
epoch sont interprétés en UTC ; cela ne certifie pas l'horloge du fournisseur.
Les bougies HTF deviennent disponibles à leur clôture ; les contrôles vérifient
les préfixes et rejettent les bougies finales incomplètes. Les données brutes
ne sont jamais corrigées ni réécrites.

## Lecture des livrables

`reports/prebaseline/detection_review/index.md` contient 20 graphiques aléatoires
reproductibles pour chacun des sept types (EQH et EQL séparés), soit 140.
Le CSV associé donne la fréquence hebdomadaire des événements uniques.
Ce sont des snapshots de fenêtres fermées et chevauchantes, pas une simulation
de déclenchements de trades. Les seuils doivent être jugés sur ces graphiques,
jamais sur un résultat financier.

La non-régression compare janvier–juin 2024 au commit pré-refactor
`ff73228102403e11f9f7a9908de9349a1db00a82`. Le JSON conserve chaque ajout,
suppression et changement de métadonnées ; `undocumented_differences.json`
liste les écarts hors des règles déclarées dans `config/detection_changes.yaml`.
Un écart couvert par une règle signifie qu'il appartient à une famille de
changements prévue ; ce n'est pas une preuve causale individuelle.

## Coûts et décisions avant approbation

Les rapports simple/MTF/API exposent les coûts ×1, ×1,5 et ×2 sur les mêmes
trades et fills, sans relancer ni redimensionner la stratégie. Les scénarios
affichent expectancy nette en R, PF et drawdown. Le registre compte le run
initial une seule fois. Le replay bid/ask utilise ses spreads effectivement
observés pour éviter de facturer deux fois un spread synthétique.

Le modèle synthétique utilise des sessions en heures UTC fixes et majore
le spread autour des événements NFP/FOMC/CPI explicitement fournis. Le calendrier
`config/news_calendar.csv` contient désormais 224 événements 2020–2026 sourcés ;
les événements futurs sont marqués scheduled.
Valider les spreads, slippage, commissions, horaires/DST, calendrier news et
traitement des swaps avant signature.

Valider aussi les règles proposées d'entrée/stop/TP/risque, les seuils ATR,
le bootstrap par blocs hebdomadaires et le rôle éliminatoire éventuel des
scénarios de coûts stressés dans `preregistration.md`. La signature attendue
n'a pas été renseignée. Le snapshot de configuration est dans
`reports/prebaseline/preregistration_snapshot.json`.
