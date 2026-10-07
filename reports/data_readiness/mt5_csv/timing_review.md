# Comparaison fine MT5 / Dukascopy 2020

Conclusion : Europe/Athens est confirmee sur la periode commune observee, jusqu'au 22 decembre 2020 a 23:59 UTC. Aucun jour avec suffisamment de paires ne diverge; aucun jour score n'est ambigu. Janvier 1, jours de faible couverture et dates apres le 22 decembre ne sont pas certifies par cette comparaison.

## Scores mensuels

| Mois | Decalage optimal (min) | Ecart absolu median USD/once | Ecart signe median MT5-Duka | Correlation retours M1 |
|---|---:|---:|---:|---:|
| 2020-01 | 0 | 0.0350 | 0.0320 | 0.9958 |
| 2020-02 | 0 | 0.0420 | 0.0420 | 0.9972 |
| 2020-03 | 0 | 0.0620 | 0.0350 | 0.8961 |
| 2020-04 | 0 | 0.1750 | 0.1320 | 0.9156 |
| 2020-05 | 0 | 0.0950 | 0.0220 | 0.9403 |
| 2020-06 | 0 | 0.0750 | 0.0720 | 0.9825 |
| 2020-07 | 0 | 0.0720 | 0.0720 | 0.9906 |
| 2020-08 | 0 | 0.0850 | 0.0850 | 0.9951 |
| 2020-09 | 0 | 0.1120 | 0.1120 | 0.9939 |
| 2020-10 | 0 | 0.0850 | 0.0850 | 0.9938 |
| 2020-11 | 0 | 0.0950 | 0.0950 | 0.9960 |
| 2020-12 | 0 | 0.0920 | 0.0920 | 0.9963 |

## Semaines de bascule et semaines adjacentes

| Semaine UTC debut | Fin exclusive | Decalage optimal min | Ecart absolu median USD/once | Correlation M1 |
|---|---|---:|---:|---:|
| 2020-03-02 | 2020-03-09 | 0 | 0.0520 | 0.9981 |
| 2020-03-09 | 2020-03-16 | 0 | 0.0450 | 0.9980 |
| 2020-03-16 | 2020-03-23 | 0 | 0.0550 | 0.9964 |
| 2020-03-23 | 2020-03-30 | 0 | 0.1150 | 0.6898 |
| 2020-03-30 | 2020-04-06 | 0 | 0.1420 | 0.9317 |
| 2020-10-19 | 2020-10-26 | 0 | 0.0750 | 0.9943 |
| 2020-10-26 | 2020-11-02 | 0 | 0.0920 | 0.9958 |
| 2020-11-02 | 2020-11-09 | 0 | 0.0950 | 0.9965 |
| 2020-11-09 | 2020-11-16 | 0 | 0.1020 | 0.9965 |

Mars : US le 8, UE le 29. Automne : UE le 25 octobre, US le 1 novembre. Les semaines de divergence restent a 0 minute optimal, y compris 23-30 mars, ou les ecarts de prix et la correlation sont moins bons mais l'optimal est distinct.

## Methode et limites

Les horodatages MT5 sont convertis en memoire avec Europe/Athens. Une valeur positive du lag deplace MT5 plus tard : timestamp_MT5 + lag = timestamp_Dukascopy. Recherche exhaustive de -120 a +120 minutes; selection par correlation des variations de cloture de minutes consecutives, sans franchir les trous. Au moins 300 paires de clotures et 100 paires de retours consecutifs; clair si correlation >=0.5 et avance >=0.1 sur le second lag. Ces seuils sont diagnostiques, pas un test statistique. Les biais de niveau de prix sont rapportes separement (mediane signee/absolue, et residu apres retrait du biais).

Le JSON conserve tous les scores de lag, les sources locales avec SHA-256 et les resultats quotidiens. Les jours non scores sont dans insufficient_overlap_days; ils incluent weekends, 1 janvier et 10 avril. Aucune preuve sur les dates absentes de la source Dukascopy, dont 23-31 decembre. Les scores pause/news seuls laissaient 2020 ambigue (UE 13.43, US 18.27, fixe 32.66); les trajectoires bid minute par minute resolvent cette ambiguite sur l'overlap observe. La preuve ne porte ni sur la performance d'une strategie ni sur l'ask.

## Conversion autorisee

Regle de travail Europe/Athens appliquee a 2020-2026 selon la decision utilisateur, avec support de la comparaison 2020 disponible et des diagnostics annuels 2021-2026. Le bid seul est converti dans data/external/mt5_converted; l'ask est absent tant que le point de Specification n'est pas atteste. Le manifeste porte unavailable_pending_point; aucun point 0.01 n'est invente. Audit reel dans real_conversion/depth_report.json. Ni data/raw ni split.yaml ne sont modifies. Aucun reseau/backtest.