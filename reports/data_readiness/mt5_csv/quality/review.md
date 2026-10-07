# Audit MT5 : ask reconstruit, spreads et trous

Ask reconstruit avec point 0.01 sur 2375973 barres, 2102 fichiers UTC.
Bid et data/raw inchanges. Aucun prix corrige, supprime ou impute.

## Spreads USD par once

| Annee | Mediane | P95 | Maximum |
|---|---:|---:|---:|
| 2020 | 0.170 | 0.620 | 17.440 |
| 2021 | 0.060 | 0.190 | 2.300 |
| 2022 | 0.050 | 0.150 | 1.630 |
| 2023 | 0.080 | 0.180 | 2.090 |
| 2024 | 0.150 | 0.330 | 2.300 |
| 2025 | 0.160 | 0.190 | 10.170 |
| 2026 | 0.040 | 0.250 | 6.220 |

Detail par heure UTC et annee : spread_by_year_hour_utc.csv et spread_quality.json.
63791 spreads nuls; 5892 flags
(spread >5 USD/oz OU >10 fois la mediane annuelle).
95 jours a >=95% de spread identique;
578 runs constants >=240 minutes consecutives.
Les flags ne prouvent pas une erreur et ne sont pas automatiquement effaces.
Ils peuvent refleter les mecanismes d'export; leur origine reste a documenter.

## Comparaison des sources locales

343458 minutes communes, du 2020-01-02T04:00:00+00:00
au 2020-12-22T23:59:00+00:00.

Clotures bid : ecart absolu median 0.075,
P95 0.275 USD/oz.
Ecart signe median MT5-Dukascopy : 0.072.
Spread MT5 median 0.170, P95 0.620;
Dukascopy median 0.397, P95 1.164.
Difference signee mediane des spreads MT5-Dukascopy :
-0.244 USD/oz.
Il s'agit de Spread de barre MT5 et de close ask-bid Dukascopy,
pas de ticks synchrones. Detail mensuel et SHA : dukascopy_spread_comparison.json.
Aucun cout de reference calibre sur MT5.

## Trous

| Classe | Intervalles | Minutes absentes |
|---|---:|---:|
| expected_closed_market | 1756 | 1146971 |
| isolated_open_minute | 1065 | 1065 |
| short_open_gap_2_15 | 1874 | 8255 |
| large_open_gap_gt15 | 241 | 23767 |

Detail par annee/heure : gap_by_year_hour_utc.csv; intervalles : gap_intervals.csv.
Les fermetures attendues utilisent les exclusions prudentes du calendrier,
pas des horaires historiques du broker attestes. La classe courte 2-15 minutes
est distincte des minutes isolees et des gros trous strictement >15 minutes.

Le masque gap_policy.json marque les gros trous non_tradable pour ATR,
detecteurs et fills. Les vues derivees de resampling bloquent les barres qui
les recouvrent, sans changer OHLC. gap_segment_id et current_gap_segment
isolent des historiques causaux apres trou; flags de bougie conserves par le
lecteur et checksum du masque verifie. Le branchement reset/cancel dans les
consommateurs du moteur de baseline reste a verifier avant execution : les
marqueurs ne constituent pas seuls une integration moteur terminee.

## Amendement et blocages

Brouillon non applique : preregistration_mt5_primary_amendment.md et
proposals/mt5_primary_amendment.json. Confirmation distincte :
preregistration_mt5_confirmation_trial.md.

Restent bloquants : validation de la source principale/fin hold-out/nouveau SHA;
broker reel et couts conservateurs documentes; decision x2 bloquant ou non;
traitement des flags spread et trous courts; audit imparfait accepte avec
exclusions; revue visuelle/precision Setup 1; lecteur MT5/segments/reset/cancel
branches et testes; modele effectif de cout sans double comptage et adaptation
du gate actuel apres approbation; source/dates/couts/budget du trial de confirmation.
La fin exclusive proposee est 2026-10-05T11:11:00Z, sans gel applique.

Aucun backtest/reseau. Document principal, SHA et split.yaml inchanges.
2015-2019 absent, hors du dev actuellement fixe 2020-2024.
