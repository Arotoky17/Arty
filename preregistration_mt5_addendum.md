# Source secondaire MT5 - addendum de provenance du 5 octobre 2026

L'export M1 bid XAUUSD de MetaQuotes-Demo constitue une source secondaire,
conservee dans data/external et separee physiquement de Dukascopy.
L'ask futur sera reconstruit par bid + Spread * point et portera reconstructed_ask.
Un spread de barre applique aux OHLC ne restitue pas les extrema ask observes.
Le point exige une preuve issue de la fiche Specification du serveur; 0.01
reste probable et non atteste. La regle horaire historique doit etre validee
sur les cotations et les evenements, sans selection selon P&L.

Les couts du modele de reference ne sont pas calibres sur MT5 ni sur cet ask
reconstruit. Tout resultat positif obtenu sur la source secondaire doit etre
confirme sur des donnees Dukascopy bid/ask ou sur les cotations du broker
concerne. Aucun backtest n'est execute ou autorise par cet addendum.

Ce document accompagne le preregistrement principal sans modifier les regles,
le split, les champs d'approbation ou le gel du hold-out. Le SHA de configuration
inclut preregistration.md : le texte principal reste exactement inchange.
L'addendum separe conserve donc le SHA existant; il ne vaut pas approbation.
Voir reports/data_readiness/mt5_csv/protected_verification.json.

## Regle horaire retenue et preuve locale 2020

Decision utilisateur : regle de travail Europe/Athens pour 2020-2026, UTC+2
hiver et UTC+3 ete selon DST europeen. L'observation UTC+3 du 5 octobre 2026
est compatible avec cette regle mais ne constitue pas seule une preuve.
Les scores annuels pause/reouverture/NFP/FOMC favorisent UE sur 2021-2026.
En 2020 ils donnaient UE 13.43, US 18.27, UTC+3 fixe 32.66 minutes, sans
marge suffisante pour une selection a eux seuls.

Comparaison independante de timing avec les clotures bid Dukascopy locales :
lag optimal 0 minute pour chacun des 12 mois disponibles et toutes les semaines
de bascule de mars/octobre/novembre. Correlations mensuelles des retours M1 :
0.896 a 0.997; ecarts absolus medians de prix : 0.035 a 0.175 USD/once.
Aucun jour suffisamment couvert ne diverge ou ne reste ambigu. La preuve
confirme UE sur les dates communes disponibles jusqu'au 22 decembre 2020;
les dates de faible couverture et le 23-31 decembre ne sont pas certifiees.
Voir reports/data_readiness/mt5_csv/timing_review.md et timing_2020.json,
avec detail de tous les lags -120..+120, resultats journaliers et SHA des sources.

La conversion bid reelle est separee sous data/external/mt5_converted.
Le point n'etant pas renseigne, aucun ask reellement reconstruit n'est publie :
ask_origin = unavailable_pending_point. La reconstruction future sera marquee
reconstructed_ask et restera exclue du calibrage des couts de reference.
Le document principal, le SHA signe et split.yaml ne sont pas modifies.

## Profondeur des donnees bid converties

2102 fichiers bid UTC, 2375973 barres, aucun doublon; hashes du manifeste verifies.
Dev 2020-2024 : 1751190 / 1770240 minutes ouvertes presentes (98.9239%),
19050 manquantes. Hold-out diagnostique du 2025-01-15 au 2026-10-05 11:11 UTC
(fin exclusive non gelee) : 595581 / 609371 minutes (97.7370%), 13790 manquantes.
3180 intervalles de trous dans le corpus 2020-2026, rapportes sans imputation.
Ces chiffres utilisent les exclusions prudentes du market_calendar; les
horaires propres au broker peuvent expliquer une partie des ecarts. Aucun
backtest, aucune modification de split.yaml ou du SHA du document principal.


## Specification confirmee et ask publie le 5 octobre 2026

Point XAUUSD 0.01, contrat 100 oz, volume minimum/pas 0.01 lot : fiche
Specification MetaQuotes-Demo confirmee par l'utilisateur ce jour. L'ask
est maintenant reconstruit sur 2375973 barres, 2102 fichiers, et marque
reconstructed_ask; les etats bid-only mentionnes plus haut sont historiques.
Le bid et data/raw restent inchanges. Audit spread/trous/comparaison dans
reports/data_readiness/mt5_csv/quality/review.md. La specification demo ne
confirme pas les frais du broker reel. Aucun cout de reference calibre sur MT5.

L'option source principale est proposee separement dans
preregistration_mt5_primary_amendment.md, sans SHA applique; la confirmation
Dukascopy/broker fait l'objet d'un brouillon de trial distinct. La signature
active, le document principal et split.yaml ne sont pas modifies.
