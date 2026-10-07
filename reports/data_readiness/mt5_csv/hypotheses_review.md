# Regles horaires MT5 2020-2026 : revue locale

Aucune regle unique appliquee : 2020 reste ambigue. EET avec DST europeen domine nettement sur 2021-2026. L'annee 2026 est partielle. Point XAUUSD non atteste : la valeur probable 0.01 n'est pas utilisee pour reconstruire des donnees reelles.

| Annee | DST UE | DST US | UTC+3 fixe | Marge du meilleur | Decision |
|---|---:|---:|---:|---:|---|
| 2020 | 13.43 | 18.27 | 32.66 | 4.84 | ambigue |
| 2021 | 4.94 | 24.40 | 37.94 | 19.46 | UE |
| 2022 | 6.82 | 18.81 | 32.22 | 11.99 | UE |
| 2023 | 3.58 | 18.14 | 34.41 | 14.56 | UE |
| 2024 | 4.05 | 20.20 | 34.63 | 16.15 | UE |
| 2025 | 5.43 | 19.65 | 34.93 | 14.22 | UE |
| 2026 | 12.37 | 29.38 | 41.78 | 17.00 | UE |

Scores en minutes : plus petit = meilleur. Moyennes des ecarts absolus plafonnes a 60 minutes; pause 35%, reouverture 35%, news 30%. Le score annuel combine pour moitie les observations globales et pour moitie les semaines sensibles. Les seuils diagnostiques, fixes avant lecture des scores, exigent une marge >=10 minutes, un meilleur score <=15, au moins 5 pauses/reouvertures sensibles et 5 news annuelles. Ce score ne constitue pas un test statistique.

Hypotheses : Europe/Athens; New York +7 heures, donc UTC+2/+3 avec dates DST americaines; UTC+3 fixe. UTC+3 observe le 5 octobre 2026 est compatible avec les trois et ne les distingue pas.

Semaines sensibles : divergence des offsets UE/US a +/-7 jours. Le JSON donne aussi les scores par mois de mars/octobre/novembre et les mois adjacents fevrier/avril. Le CSV conserve les dates, horaires serveur, UTC candidates, ecarts et types de news.

Detection de pause : plus long trou de 5 a 180 minutes par date serveur de semaine, independamment de l'hypothese. Debut de pause et reouverture sont compares separement au market_calendar local. Les pauses du broker peuvent avoir une duree differente, et des trous accidentels peuvent entrer dans cet echantillon. Le calendrier conservateur n'atteste pas les horaires historiques du broker. Les pics NFP/FOMC sont cherches a +/-90 minutes; les pics ex aequo sont exclus et journalises. Un pic peut etre sans lien causal ou retarde.

2020 : avance UE de seulement 4.84 minutes. En semaines sensibles, ecart moyen plafonne de pause 6.67 minutes, reouverture 26.67 minutes, news 18 minutes. Un decalage historique ou une regle unique n'est pas infere.

2026 : export arrete au 5 octobre. Les bascules de fin octobre et debut novembre ne sont pas observees. Le score disponible, notamment mars, prefere UE; cette preference doit encore etre confirmee apres les bascules d'automne.

## Etat des livrables

58 tests cibl?s passent. Ruff propre. Aucun reseau, aucun backtest. data/raw et split.yaml restent inchanges. Conversion reelle, profondeur UTC et comparaison Dukascopy attendent une regle historique validee pour 2020 et le point atteste. Sources dans data/external/mt5_source. 2015-2019 absent.

Le texte de provenance est preregistration_mt5_addendum.md : MT5 secondaire, ask reconstruit, couts de reference non calibres sur MT5, tout resultat positif a confirmer sur Dukascopy/broker. Le SHA inclut preregistration.md; le document principal est restaure exactement, et le SHA avant/apres est identique. Un ajout dans le fichier principal serait incompatible avec la conservation de ce SHA. Voir protected_verification.json.

Decisions restantes : regle historique 2020, point de la fiche Specification, approximation OHLC ask et export 2015-2019. Aucun gel du hold-out.