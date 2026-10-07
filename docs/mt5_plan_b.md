# Plan B MT5 local

Le convertisseur ne contacte aucun service et ne lance aucun backtest. Il ne modifie pas `config/split.yaml`.

## Usage

Depuis D:/Arty, avec le paquet install? ou PYTHONPATH=src :

```powershell
.venv/Scripts/python.exe tools/mt5_to_arty_csv.py detect-timezone --input data/external/mt5_source/mt5_xauusd_m1.csv
.venv/Scripts/python.exe tools/mt5_to_arty_csv.py convert --input data/external/mt5_source/mt5_xauusd_m1.csv --output data/external/mt5_converted --server-timezone <IANA_VALIDE> --point <POINT_MT5_VALIDE>
```

La conversion exige un fuseau explicite et un point positif. Les minutes DST ambigu?s ou inexistantes sont exclues et journalis?es. Les doublons locaux sont conserv?s dans le rapport (premi?re observation retenue, conflit signal?). Les fichiers journaliers sont compatibles avec `csv_import`; une r?ex?cution refuse de remplacer un contenu diff?rent.

L'ask OHLC est approxim? par bid OHLC + Spread ? point. Cela ne reconstitue pas les extrema r?els de l'ask. Le manifeste et `provenance.json` portent `reconstructed_ask` et interdisent son utilisation pour le calibrage de r?f?rence. Le r?pertoire converti doit rester s?par? du magasin Dukascopy. Ne pas importer les deux sources dans le m?me magasin.

## Diagnostics

`timezone_evidence.json` classe les candidats sans en choisir un. Les pauses quotidiennes sont confront?es au calendrier configur? et les pics TickVol aux ?v?nements NFP/FOMC locaux. Des fuseaux IANA peuvent partager les m?mes r?gles; les pics ne prouvent pas un fuseau. Une politique historique propre au courtier peut n?cessiter des r?gles dat?es hors des candidats propos?s. Les exclusions de f?tes du calendrier sont conservatrices, pas une affirmation sur les horaires historiques du courtier.

Apr?s conversion, `depth_report.json` compte les minutes ouvertes absentes dans l'intervalle observ?, y compris entre jours et jours absents; les fen?tres dev et hold-out rapportent aussi les jours manquants. Un hold-out sans fin fig?e est signal? comme provisoire ou indisponible. `conversion_summary.json` conserve les doublons et exclusions DST. `dukascopy_compare_2020.json` compare les OHLC bid et les minutes UTC communes ? Dukascopy local; TickVol et volume Dukascopy ont des unit?s diff?rentes.

## ?tat de l'export fourni

2 375 973 barres, du 2020-01-02 06:00 au 2026-10-05 14:10 en heure serveur. Les ann?es 2015 ? 2019 sont absentes. L'export source est copi? dans `data/external/mt5_source/`. Aucune conversion r?elle n'est appliqu?e sans fuseau et point valid?s; le rapport de profondeur UTC et la comparaison r?elle 2020 attendent ces param?tres.

## D?cisions ? valider

- Fuseau/r?gles historiques du serveur et point XAUUSD.
- Approximation du Spread de barre appliqu? aux quatre prix OHLC.
- Usage de TickVol pour la colonne volume, distinct de Vol et du volume Dukascopy.
- Traitement des doublons conflictuels et des minutes DST exclues.
- Obtention d'un export couvrant 2015-2019.
- R?pertoire s?par?, exclusion du calibrage de r?f?rence et fin du hold-out inchang?e.

Les modifications pr?existantes non commit?es ne portent pas d'auteur fiable dans Git. Elles ont ?t? conserv?es; les changements li?s au forward demo restent hors du p?rim?tre MT5 CSV.


## Comparaison des trois r?gles historiques

Commande locale : `tools/mt5_to_arty_csv.py compare-hypotheses --input data/external/mt5_source/mt5_xauusd_m1.csv`. Le rapport est `reports/data_readiness/mt5_csv/hypotheses_review.md`; les scores annuels, mensuels sensibles et observations d?taill?es sont dans le JSON et le CSV voisins. Les seuils sont diagnostiques, sans P&L. EET europ?en domine sur 2021?2026, mais 2020 est ambigu? : aucune conversion r?elle globale n?est appliqu?e. Le point doit ?tre attest?. Le texte de provenance est dans `preregistration_mt5_addendum.md` pour conserver le SHA, qui inclut le document principal.


## Etat apres comparaison fine Dukascopy 2020

Europe/Athens est confirmee sur l'overlap local jusqu'au 22 decembre 2020 : lag optimal nul dans tous les mois et toutes les semaines de bascule, aucun jour suffisamment couvert divergent/ambigu. Voir timing_review.md et timing_2020.json. La regle de travail approuvee est appliquee a 2020-2026.

Conversion bid reelle : data/external/mt5_converted, 2102 fichiers et 2375973 barres avec hashes verifies. Aucun ask n'est publie faute de point atteste (unavailable_pending_point). L'audit minute exact est real_conversion/minute_depth_report.json; holes_utc.csv liste les intervalles manquants. Le dev couvre 98.9239% des minutes ouvertes du calendrier; le hold-out provisoire 97.7370%. split.yaml reste inchange.

Relance bid sans point : ajouter --bid-only et --timezone-evidence reports/data_readiness/mt5_csv/timing_2020.json a la commande convert. Une fois le point confirme, retirer --bid-only et fournir --point; l'ask sera marque reconstructed_ask. Ne pas reutiliser cet ask pour les couts de reference.
