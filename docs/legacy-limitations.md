# Limites du legacy XAUUSD

Le chemin `USE_OB_QUALITY_FILTER=false` est un fallback. Son baseline sert
de point de comparaison, pas de validation de la stratégie cible ni de
rentabilité. La traçabilité OB des signaux est attendue avec la **Tâche 4**.

## Diagnostic et politique de reprise

Sur les mêmes CSV Dukascopy XAUUSD 2024-H1, avec les autres limites inchangées :

| Variante | Trades | Win rate | PF | Expectancy | Drawdown maximal |
| --- | ---: | ---: | ---: | ---: | ---: |
| Avant reprise automatique | 3 | 0 % | 0 | -1 R | 2,96 % |
| Diagnostic sans blocage de série | 35 | 22,86 % | 0,582 | -0,314 R | 10,68 % |
| Cooldown de 24 h | 57 | 28,07 % | 0,774 | -0,158 R | 10,71 % |

Le diagnostic `config/diagnostic_no_streak.yaml` porte uniquement la limite
de série à 1 000 000, donc hors d'atteinte sur ce jeu de 35 231 bougies.
C'est une désactivation expérimentale sur cette période, pas une configuration
live recommandée. Le rapport est `data/backtests/diagnostic_no_streak_2024H1.json`.

La politique retenue est un cooldown configurable de 24 h à partir de la perte
qui atteint trois pertes consécutives. Au premier contrôle après l'échéance,
seule la série de pertes est remise à zéro. Le reset journalier à minuit UTC
ne raccourcit pas ce délai ; les limites monétaires journalières et le drawdown
restent indépendants. Une clôture non perdante conserve le comportement existant
de remise à zéro de la série. Le replay injecte le temps historique, jamais
l'horloge de la machine ; le début du cooldown est évalué à la clôture M5 où
la sortie est constatée. Un nouveau cycle de trois pertes peut déclencher une
nouvelle pause. L'état est en mémoire, comme les autres compteurs de risque.

Les pauses modifient les occasions d'entrée : désactiver le breaker ne garantit
pas davantage de trades que le cooldown. Le garde de drawdown à 10 % finit
par arrêter les deux variantes. Le seuil est un garde d'ouverture, pas une
garantie de drawdown réalisé inférieur à 10 % : une position déjà ouverte peut
faire dépasser le seuil. Aucun seuil de risque n'a été desserré dans le baseline.

## Diagnostic exploitable, audit toujours incomplet

Le baseline conserve 588 signaux retournés par le générateur pour 57 trades
exécutés. `signals_generated` compte ces signaux avant les gardes d'exécution
et de risque ; il ne compte pas tous les candidats internes à la stratégie.
`legacy_ob_detected` compte 709 OB uniques par timeframe, date d'origine et
direction dans les fenêtres effectivement analysées. Il ne couvre pas les
fenêtres sautées après blocage et ne relie aucun OB à une entrée.

`rejections_by_reason` distingue série de pertes, perte journalière, drawdown,
les gardes de risque et les sous-raisons disponibles du validateur. Les clés
spread, session et confiance existent même si leur compteur vaut zéro. Un zéro
signifie aucun rejet observé à cette étape ; il ne prouve pas que toutes les
bougies ont été soumises à ce filtre. Les compteurs de breaker comptent des
bougies M5 bloquées, pas des signaux rejetés. Les sous-raisons peuvent se
recouvrir : leur somme n'est pas un nombre de candidats. `no_signal` reste une
limite du diagnostic interne legacy. La synthèse est écrite dans le JSON et
sur stderr ; les rejets du RiskManager exposent également leur raison au logger.

Les 57 trades legacy n'émettent ni association OB ni trace de confirmation.
Les timestamps OB/confirmation restent `null`, les grades `UNKNOWN`, et les
violations d'audit restent présentes. `validation_status=failed` et l'exit CLI 1
sont donc attendus malgré le critère de volume atteint. Les trois anciens tests
d'audit sautent uniquement un rapport entièrement legacy sans preuve OB ; leurs
assertions restent actives dès qu'il contient des traces. Le rappel explicite
`test_baseline_ob_traces_pending` est skip jusqu'à la Tâche 4. Les tests unitaires
de détection des violations continuent de s'exécuter.

## Contrôle synthétique

`python scripts/compare_synthetic_control.py` utilise le même replay H4/H1/M5,
le Dataset B (graine 42), 5 400 M5, 450 H1 et 112 H4, et les mêmes gardes.
Il produit 11 trades, 90 signaux et 174 OB détectés ; drawdown 6,18 %.
`data/backtests/synthetic_legacy_control.json` est explicitement synthétique et
n'entre jamais dans l'acceptation des données réelles. Les différences de
durée et de données empêchent une comparaison directe des performances.

Le timeout IPC MT5 reste non résolu ; la source CSV permet ce contrôle sans MT5.
Les coûts broker et les limites de provenance sont détaillés dans
[le contrôle historique](control-backtest.md).

Validation du changement : 1 105 tests passent, quatre skips de traçabilité OB
documentés ; les baselines Ruff et mypy n'introduisent aucune nouvelle erreur.
Les deux replays réels finaux sont identiques octet par octet, avec huit
réarmements du breaker. Le SHA-256 est consigné dans le contrôle historique.
