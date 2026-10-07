# Trial de confirmation MT5 : proposition separee, non enregistree

Statut : BROUILLON. Aucun SHA applique, aucun trial reserve/enregistre, aucun
backtest. Valider avant toute confirmation et comptabiliser dans le budget
total des hypotheses/essais. Le budget de trois essais du Setup 1 ne change pas.

Hypothese : le resultat du Setup 1 obtenu avec MT5 et des couts conservateurs
se reproduit sur une source de cotations bid/ask OBSERVES, avec les memes regles
gelees. Choisir exactement une source avant lancement : Dukascopy OU broker
reel identifie. Si Dukascopy, proposer les memes bornes dev/hold-out figees pour
une replication entre feeds; ce n'est pas un nouvel echantillon temporel
independant. Si broker reel/fenetre future, definir dates et minimum de trades
avant lancement. Source, bornes, hashes, frais et autorisation restent a remplir.

Aucune adaptation de signal, seuil ou periode a partir du P&L primaire. Revue
OHLC/spreads/calendrier permise sans strategie. Appliquer les memes criteres
Setup 1 (expectancy, PF, minimum de trades, bootstrap et stress x1.5), ainsi que
la decision x2 prealablement gelee. Fixer le mapping contractuel et les couts
broker avant lancement; aucune substitution de spread demo non observe.

Si les criteres sont satisfaits : confirmation conditionnelle aux hypotheses
de cout et d'execution documentees. Sinon : resultat primaire non confirme,
aucun retuning ni seconde source choisie retrospectivement. Aucun trial ne
peut etre lance tant que la source/dates/couts, le budget et le protocole ne
sont pas explicitement valides et signes.
