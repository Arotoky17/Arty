# Audit disponible : janvier?juin 2024

Aucun backtest ni calcul de P&L. Donn?es brutes inchang?es.

- bid: `{'rows': 262080, 'duplicates': 0, 'unordered_rows': 0, 'invalid_ohlc': 0, 'missing_minutes': 0, 'weekend_rows': 74880, 'weekend_flat_rows': 72313, 'outlier_m1_bars': 135}` ; resampling : 36 contr?les r?ussis.
- ask: `{'rows': 262080, 'duplicates': 0, 'unordered_rows': 0, 'invalid_ohlc': 0, 'missing_minutes': 0, 'weekend_rows': 74880, 'weekend_flat_rows': 72312, 'outlier_m1_bars': 136}` ; resampling : 36 contr?les r?ussis.

Les outliers sont des candidats ? examiner, pas des ticks invalides confirm?s.
Les grilles M1 incluent des weekends et plateaux : absence de trous dans la grille ne prouve pas une cotation continue.
Les ticks originaux ne sont pas fournis ; horloge UTC du fournisseur non certifi?e.
Couverture 2020?2026 : 78 mois manquants sur 84. Hold-out : 24 mois manquants ; tendance/range non confirm?s.

Non-r?gression : 25 338 ?v?nements avant, 25 683 apr?s ; swings identiques (9 218).
Les diff?rences attendues sont conserv?es dans le JSON complet ; aucun ?cart hors r?gles d?clar?es.
Cette comparaison utilise des snapshots ferm?s, pas un rejeu de trades.
