# Profondeur UTC MT5 bid 2020-2026

2102 fichiers journaliers bid; 2375973 barres, hashes du manifeste verifies. Europe/Athens. Point inconnu : aucun ask publie. Aucun doublon.

| Annee UTC | Minutes ouvertes attendues | Presentes | Manquantes | Barres hors calendrier |
|---|---:|---:|---:|---:|
| 2020 | 354000 | 348163 | 5837 | 3095 |
| 2021 | 355380 | 349831 | 5549 | 1195 |
| 2022 | 355680 | 352186 | 3494 | 714 |
| 2023 | 352920 | 350659 | 2261 | 2097 |
| 2024 | 352260 | 350351 | 1909 | 5322 |
| 2025 | 351240 | 346917 | 4323 | 4904 |
| 2026 | 268871 | 259157 | 9714 | 1382 |

2026 est partielle : fin observee exclusive 2026-10-05T11:11:00Z.

| Fenetre | Couverture minutes ouvertes | Minutes manquantes |
|---|---:|---:|
| development | 98.9239% | 19050 |
| holdout | 97.7370% | 13790 |

Le hold-out commence au 2025-01-15; sa fin ci-dessus est seulement diagnostique. split.yaml garde end=null; aucun gel ni backtest.

3180 intervalles de trous sont listes avec debut, fin exclusive et nombre de minutes dans holes_utc.csv; les jours entiers absents sont inclus.

Le calendrier applique les exclusions de fetes prudentes configurees. Les barres hors calendrier ne prouvent pas une erreur de donnees du broker; des pauses plus longues ou des horaires propres au broker peuvent produire des minutes manquantes. Aucun trou n'est rempli et aucune barre n'est supprimee des fichiers bid.

Decision restante indispensable pour l'ask : point de la fiche Specification. L'approximation OHLC par un spread de barre reste a valider. Historique 2015-2019 absent. Cout de reference non calibre sur MT5.