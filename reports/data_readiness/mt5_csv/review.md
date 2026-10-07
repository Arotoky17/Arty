# Diagnostic local Plan B MT5

Aucun fuseau s?lectionn?. Candidats ex ?quo : Europe/Athens, Europe/Helsinki, Europe/Vilnius, Europe/Kiev et Europe/Bucharest. Les r?gles candidates donnent UTC+2/UTC+3 sur chaque ann?e 2020?2026. Ce constat ne prouve pas les r?gles historiques du serveur.

| Ann?e | Pause locale modale | ?cart m?dian au d?but de pause du calendrier, min | Pic news : ?cart m?dian UTC, min | ?v?nements |
|---|---|---:|---:|---:|
| 2020 | 23:55 | -5.0 | 1.0 | 21 |
| 2021 | 23:55 | -5.0 | 1.0 | 19 |
| 2022 | 23:59 | -1.0 | 6.0 | 20 |
| 2023 | 23:59 | -1.0 | 0.0 | 19 |
| 2024 | 23:59 | -1.0 | 1.0 | 20 |
| 2025 | 23:59 | -1.0 | 1.0 | 19 |
| 2026 | 00:00 | 0.0 | 1.0 | 15 |

Le diagnostic de pause utilise le d?but du trou, pas la r?ouverture elle-m?me. Les ?carts de quelques minutes peuvent refl?ter les horaires du courtier. Les pics de volume peuvent ?tre retard?s ou sans lien causal avec la news.

Ambigu?t?s : horaires multimodaux en 2022; zones ?quivalentes; trous atypiques; pics ex ?quo ou fen?tres sans barres d?taill?s dans timezone_evidence.json. Aucune observation 2015?2019.

Conversion r?elle UTC, rapport de profondeur UTC et comparaison Dukascopy 2020 en attente du fuseau/r?gles historiques et du point valid?s. Les outils et tests synth?tiques sont en place.

54 tests cibl?s r?ussis; Ruff r?ussi. Aucun r?seau, aucun backtest, split.yaml inchang?.