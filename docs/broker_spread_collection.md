# Collecte de spreads broker - outil hors ligne prepare le 6 octobre 2026

Aucun terminal ni reseau lance par ce travail. Aucun backtest.
Copier tools/SpreadLogger.mq5 dans MQL5/Experts, compiler avec MetaEditor,
puis attacher manuellement au graphique XAUUSDm du compte demo Standard Exness.
Compilation MQL5 et fonctionnement terminal non verifies dans cet environnement.
Le logger ne passe aucun ordre. Export dans le dossier MQL5/Files du terminal.
Synchroniser l horloge du poste : observed_utc est TimeGMT a reception,
server_time_msc reste brut, sans supposer le fuseau historique du serveur.
OnTick peut regrouper des ticks; un timestamp milliseconde deja vu est ignore.
Ce journal n est donc pas une archive exhaustive ni une preuve de fills reels.
Conserver les fichiers par compte et campagne; ne pas melanger demo et reel.

Copier le CSV dans data/external/broker_spreads, puis executer :

    python tools/analyze_broker_spreads.py data/external/broker_spreads/Arty_spreads.csv --output reports/broker_spreads/review.json

Option de scenario independant (valeur a choisir, pas estimee automatiquement) :

    --spread-broker-reel VALEUR_USD_PAR_ONCE --evidence "source independante et date"

Le parametre est prepare et teste dans BrokerSpreadHypothesis, sans branchement
au moteur ni modification de execution.yaml. Il calcule max(spread MT5, plancher)
avec stress x1 / x1.5 / x2; le spread doit etre facture une seule fois via bid/ask.
Les commissions et slippages restent des parametres distincts a justifier.
Le rapport calcule quantiles par heure/jour, valeurs invalides, doublons et heures
absentes. Quantiles ponderes par ticks recus, pas par temps; absence horaire ne
prouve pas un trou en marche ouvert. Une campagne demo ne calibre pas seule
les couts de reference du broker reel. News, sessions, couverture representative,
constance et execution doivent etre revues avant gel et validation.

Decisions utilisateur : fin hold-out 2026-10-05T11:11:00Z; montant du plancher
broker reel et profil heures/news, preuves, commission/slippage/marge; stress x2
bloquant ou descriptif; traitement spreads nuls/constants/extremes et trous courts;
validation amendement et signature future; source/dates/budget du trial distinct.
Capital de reference confirme 10000 USD; essai reel petit capital hors validation.
Configuration active de demo (symbole XAUUSD) a adapter a XAUUSDm apres validation
avec specification effective et capital controles; non applique dans cette etape.
