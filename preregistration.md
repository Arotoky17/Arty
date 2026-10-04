# Préenregistrement — Setup 1 : BOS + retest OB/FVG

Statut : EN ATTENTE D’APPROBATION EXPLICITE. Aucun backtest autorisé.
Préparé le 3 octobre 2026 ; setup `setup_1_bos_retest_ob_fvg_v1`.
Responsable, signature UTC et commit final : à renseigner avant approbation.
Les valeurs proposées ci-dessous ne sont pas encore validées.
Unité XAUUSD : 1 pip = 0,01 USD par once ; 1 lot = 100 onces, à confirmer broker.

## Hypothèse et règles déterminées

XAUUSD, décisions M5 fermées, données bid/ask UTC. Un BOS externe avec
displacement puis premier retest OB/FVG produit une expectancy nette positive.
Aucun filtre régime, biais HTF, session ou confluence.

1. Swing externe : fractal strict de 5 barres de chaque côté, disponible après
   clôture des 5 barres droites. BOS de continuation : clôture M5 strictement
   au-delà du dernier swing externe confirmé dans le sens de la structure.
   Mèche seule, CHoCH et MSS exclus. Corps de cassure > 1 ATR(14) Wilder,
   calculé sur les barres ouvertes jusqu’à cette clôture ; aucun signal avant
   warmup complet. La tendance est l’état du détecteur structure existant :
   dernière cassure haussière/baissière ; son état initial unknown peut établir
   le premier BOS. Dédupliquer BOS/external_BOS par swing, direction et niveau.
2. Zone de même direction déjà confirmée : OB non mitigé, âge ≤30 barres ouvertes,
   taille ≤3 ATR ; sinon FVG, gap >0,25 ATR et âge ≤30 barres. OB prioritaire.
   Âge compté depuis la bougie OB ou la première bougie du FVG.
   Dans chaque famille : confirmation la plus récente, puis origine la plus
   récente, puis prix bas le plus petit. Aucun remplacement ultérieur.
   OB : bougie opposée immédiatement suivie de 2 barres consécutives de même
   direction, corps cumulés >1 ATR mesuré à l’origine OB ; zone [low, high].
   Ces 2 barres doivent être toutes deux fermées. Après confirmation, aucune
   intersection entre une barre ultérieure et la zone jusqu’au BOS : frais.
   FVG : trois barres, gap haussier low[3]−high[1], baissier low[1]−high[3],
   mesuré à clôture de la troisième ; zone du gap. Remplissage complet : low
   ≤borne basse pour un gap haussier, high ≥borne haute pour un gap baissier,
   sur une barre ultérieure à confirmation ; un gap rempli est exclu.
3. Limite au milieu de la zone déposée à la clôture du BOS. Premier retest :
   première barre ultérieure traversant la limite d’au moins 0,10 USD (10 pips) ; cotation
   ask pour achat, bid pour vente. Cette traversée est la seule pénalité d’exécution
   à l’entrée : aucun slippage d’entrée n’est ajouté par-dessus (anti double
   comptage). Probabilité 1, seed 0. Aucun fill sur la barre
   de dépôt. Expiration après 10 barres ouvertes ; annulation sur cassure de
   structure opposée ou pause NY 17 h. Un seul ordre en attente.
   Stress de fill : traversée de 0,30 USD (30 pips), sans réglage selon P&L.
   Le stop peut être touché sur la barre du fill : oui, scénario pessimiste.
   Le TP ne peut pas être exécuté sur cette barre, faute d’ordre intrabar connu.
4. Stop long : bord bas moins 0,1 ATR du BOS ; short : bord haut plus ce buffer.
   Stop figé, TP à 2 fois la distance entrée–stop. Stop prioritaire si SL et TP
   touchés sur la même barre. Sortie à la clôture de la 48e barre ouverte après
   fill ou dernière clôture avant NY 17 h, selon celle qui arrive en premier.
   Aucune position overnight, sortie partielle, pyramide ou trailing.
   Distance entrée–stop ≥0,5 ATR(14) du BOS ; sinon aucun trade.
5. Budget : 1 % de l’équité à l’entrée. Volume arrondi vers le bas au pas broker,
   refus sous volume minimum. 1R = distance entrée–stop × volume × valeur du
   contrat avant coûts ; R net = P&L net / risque initial effectivement engagé.
   Une position à la fois ; un seul trade par BOS ; aucun sizing par confluence.
   Plafonds proposés : levier brut agrégé ≤10×, marge utilisée ≤50 % de l’équité,
   marge calculée avec levier broker 20×. Réduire le volume au pas 0,01 lot,
   minimum 0,01 ; sinon refuser. Hypothèses à confirmer avec le broker.
   Rapport par trade : risque initial USD, coûts USD, coûts/1R, levier et marge.

Ces règles spécifient la future baseline ; implémentation et tests de conformité
requis avant lancement. Les détecteurs existants restent la source unique.
Paramètres : definitions, setup1_preregistration, execution, market_calendar YAML.

## Données, calendrier et revue sans P&L

Dev : [2020-01-01 00:00 UTC, 2025-01-01 00:00 UTC).
Purge : [2025-01-01, 2025-01-15), 10 jours lundi–vendredi sans fériés.
Hold-out : début 2025-01-15 ; fin explicite après import complet, dernière clôture
M1 commune bid/ask. Actuellement indisponible ; ni date future ni extension
automatique après gel. Raw immuable, checksums requis.

Pause régulière XAUUSD NY 17–18 h ; weekend vendredi 17 h à dimanche 18 h,
DST America/New_York. Proposition : aucun signal ni entrée pendant 20 minutes
après réouverture. Jours non tradables entiers en date America/New_York :
24–26 décembre, 31 décembre–2 janvier et Vendredi saint. Exclusion prudente,
sans prétendre attester les horaires historiques du fournisseur.
D1 actif : clôture New York 17 h, conversion UTC avec DST. ADX D1, PDH/PDL et
biais D1 partagent les mêmes barres fermées issues de H1 ; première session
partielle exclue. H4 reste ancré UTC. ATR : `require_full`, 14 TR causaux, donc
15 barres tradables au minimum ; Wilder initialisé par leur moyenne avant usage.

Audit mensuel complet 2020–fin figée, HTF exploitable uniquement à clôture,
ADX(14) D1 et structure H4 : tendance ET range dans chaque période.
Couverture manquante : NON ÉVALUÉ, run bloqué. Aucun choix de dates selon P&L.
Revue dev : 20 graphiques aléatoires par type, ≥16/20 corrects pour chaque type
(swing, displacement, OB, FVG), graphiques M5 et verdict humain avant run.
Sweep, EQH/EQL : revue informative, non bloquante pour ce Setup 1.
Toute modification impose justification au registre et nouvelle signature.

## Coûts, news et diagnostics

Référence ×1 : P75 horaire UTC du ratio (ask−bid)/prix médian, sur TOUT le dev
2020–2024, hors pauses, exclusions calendrier et fenêtres news. Application :
ratio × prix courant, conversion en pips avec 1 pip = 0,01 USD par once.
Facteur broker 1 à confirmer. Calibrage actuel provisoire janvier–juin 2024 :
couverture incomplète, run bloqué ; clôtures M1, pas des ticks exécutables.
Hold-out 2025 : calibrage dev complet figé. Pour 2026 : recalibrage sur les seules
cotations bid/ask de 2025, sans stratégie ni P&L, figé et journalisé avant le run.
Aucune cotation de l’année évaluée ne calibre son propre spread. Fichier annuel,
dates, SHA-256 et absence de P&L obligatoires ; sans fichier annuel, run refusé.
NFP/FOMC/CPI : spread ×1,5 dans ±15 min. Entrée limite : seule la traversée de
fill de 0,10 USD est modélisée ; aucun coût de slippage d’entrée n’est ajouté
(entry_limit_slippage_usd = 0,00 USD), afin d’éviter le double comptage du même
mouvement adverse de 0,10 USD. Sortie TP en limite : 0,10 USD (inchangé) ;
stop et market, sortie temporelle incluse : 0,30 USD (inchangé). Hypothèses à
confirmer broker avant signature.
Commission 3,5 USD par lot et côté ; pip XAUUSD = 0,01 USD par once. Contrat, devise,
spreads et frais broker à approuver. Pas de swap car aucune position overnight.
Sources news par ligne BLS/Fed ; deux futurs horaires FOMC marqués non confirmés.

Stress ×1, ×1,5, ×2 sur même chemin de trades sans retuning. Expectancy négative
à ×1,5 : fragile ; expectancy ≤0 à ×1,5 : échec. Le stress ne modélise pas
un changement de fill dû au spread. Le stress de traversée 0,30 USD est configuré,
non exécuté ; toute simulation historique additionnelle consomme un trial et
nécessite un budget préenregistré. Il ne peut être présenté comme un rerun gratuit.

Diagnostics seulement : année, H4 haussier/baissier/range connu à l’entrée,
longs/shorts, sessions UTC, IC bootstrap et buy-and-hold au même risque initial
référencé à 1 ATR sans stop exécuté. Benchmark après warmup, mêmes plafonds de risque.
Une variante dev avec exclusion des signaux/entrées dans ±45 min des news utilise
le même moteur. Elle ne peut sauver une référence en échec. Le sous-ensemble
des trades est distingué d’une réelle variante réexécutée. Aucun variant hold-out.

## Succès, abandon et budget de trials

Référence dev, critères bloquants : expectancy nette R >0, borne basse IC bootstrap
95 % >0, PF net >1,2, ≥100 trades remplis, expectancy nette >0 à coûts ×1,5.
Bootstrap percentile en blocs hebdomadaires UTC, 10 000 réplications, seed 20261002 ;
semaines sans trade incluses.

Hold-out, critères bloquants en estimation ponctuelle : expectancy nette R >0,
PF net >1,2, ≥100 trades remplis. L’IC bootstrap 95 % est rapporté pour information
mais NON bloquant en hold-out ; la base de décision hold-out est l’estimation
ponctuelle.

La variante news, diagnostic dev uniquement, est ANNULÉE si la référence dev
échoue : aucun trial news n’est lancé tant que les critères dev bloquants ne sont
pas satisfaits.

Maximum 3 trials : 1 référence dev, 1 diagnostic news dev, 1 référence hold-out.
Une seule configuration, aucune répétition ni optimisation. Tentatives interrompues
comptées ; stress et audits sans P&L ne sont pas des trials supplémentaires.
Limites contrôlées atomiquement par le registre. Échec d’un seul critère bloquant,
<100 trades ou budget épuisé : suppression du setup, aucun réajustement.
Données manquantes : NON ÉVALUÉ et blocage.

## Puissance statistique

Hypothèses de planification à valider, pas des comptages observés : 250 trades
remplis attendus en dev, 100 en hold-out ; écart-type des R =1,5, effet de plan
hebdomadaire =2, puissance 80 %, confiance bilatérale 95 %.
Expectancy minimale détectable approximative :
`(z_0,975 + z_0,80) × 1,5 × sqrt(2/n)` : 0,376 R en dev, 0,594 R en hold-out.
À 100 trades, un petit avantage peut rester indétectable. Ces approximations
normales ne garantissent pas la réussite du bootstrap en blocs. Nombre réel de
trades et variance inconnus sans données complètes et run autorisé ; aucune
estimation de P&L n’a été utilisée. Ne pas rallonger le hold-out après résultat.

## Provenance des overrides XAUUSD

FVG 0,25 ATR, sweep rejet 0,5 et displacement 1 ATR, OB taille 3 ATR,
displacement confirmation 2 barres : repris de `config/settings.py`, présents
dans les commits `2afe9e0` et `ebb1c92` du 24 août 2026, migrés vers YAML le
2 octobre 2026 (`bf2297d`). Vérification le 3 octobre par `git log -S`/`git show`.
Date de choix initial et usage initial de backtest/P&L : inconnus, à confirmer
par le responsable. Des rapports P&L existent ; ils ne prouvent pas leur rôle
dans ce choix. Aucun P&L utilisé pour cette migration ou les présents contrôles.

## Approbation et accès final

Run bloqué jusqu’à signature explicite de ce fichier, des configs, du calendrier
news, du calibrage et de l’audit complet SHA-256. Tout changement invalide
l’approbation. À valider : revue M5 des quatre détecteurs, fenêtre de réouverture,
coûts/contrat/levier broker, hypothèses de puissance et provenance des overrides.
Après succès dev et autorisation finale : un seul load_holdout pour ce setup,
flag explicite et raison journalisée. Audit OHLC/ADX sans P&L : accès distinct.

Dev : NON EXÉCUTÉ. Hold-out : NON EXÉCUTÉ. Décision : NON ÉVALUÉ.
Approbation utilisateur : EN ATTENTE.
