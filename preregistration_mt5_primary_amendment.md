# Amendement propose : MT5 source principale - 5 octobre 2026

Statut : BROUILLON, validation explicite requise. Aucun SHA nouveau applique,
aucun trial enregistre, aucune baseline ni backtest execute. Le document
principal et split.yaml restent inchanges. Parametres proposes lisibles dans
proposals/mt5_primary_amendment.json; les valeurs null sont bloquantes.

## Source et calendrier

Source principale proposee : export M1 bid XAUUSD MetaQuotes-Demo, distinct de
Dukascopy sous data/external/mt5_converted. Regle Europe/Athens 2020-2026,
UTC+2 hiver et UTC+3 ete avec DST europeen. Point 0.01, contrat 100 oz,
minimum/pas 0.01 lot : fiche Specification MetaQuotes-Demo, confirmee par
l'utilisateur le 5 octobre 2026. Cela ne confirme pas les frais et conditions
d'un broker reel. Source, manifeste de conversion et masque des trous sont
identifies par SHA dans la proposition, sans application a la signature active.

Ask OHLC = bid OHLC + Spread de la barre * 0.01, origine reconstructed_ask.
Le spread constant de barre ne reconstitue pas les extrema ni les ticks ask.
Les cotations initiales ne sont pas corrigees ou imputees. L'ask reconstruit
ne calibre jamais les couts du modele de reference.

Dev/purge/debut hold-out restent ceux du preregistrement. Fin hold-out proposee,
A CONFIRMER : derniere barre MT5 ouverte le 2026-10-05 a 11:10 UTC, fin exclusive
2026-10-05T11:11:00Z. Cette fin est un instant de cloture M1, pas l'ouverture.
Apres validation, figer la source et la fin avant le premier resultat; aucune
extension apres resultat. La proposition n'ecrit pas cette fin dans split.yaml.

## Hypothese de cout conservatrice parametrable

Capital de reference du backtest et de la demo Exness : 10 000 USD.
Compte demo Standard, symbole XAUUSDm, contrat 100 oz par lot, volume
minimum/pas/maximum 0.01/0.01/200 lots (declaration utilisateur, 6 octobre 2026).
Le test reel a petit capital est hors perimetre de validation. Cette decision
ne modifie aucune configuration active ni la signature du preregistrement.
Le risque configure de 1% represente 100 USD; plafond de levier brut 10x.
Sans exposition existante et hors frais/marge, seuil de stop D = prix * 0.01 / 10.
A 2 000 / 3 000 / 4 000 USD, D = 2 / 3 / 4 USD par once; le plafond devient
contraignant pour un stop strictement plus court. Quantites maximales continues
50 / 33.3333 / 25 oz, soit 0.50 / 0.333333 / 0.25 lots; sur pas 0.01,
0.50 / 0.33 / 0.25 lots. Frais, marge et exposition existante peuvent reduire
ces volumes. Le seuil continu ne depend pas du capital si le risque est
proportionnel au meme capital; le pas de lot introduit une discretisation.
Les observations de spread Exness demo sont diagnostiques; elles ne prouvent
pas les conditions d'execution du compte reel.

Identifier le broker reel et des preuves independantes pour spread plancher,
profil horaire/news, commission par lot et par cote, slippage stop/marche et
limite de sortie. Ces valeurs sont null dans la proposition et doivent etre
fixees avant toute baseline. Aucun quantile MT5 ne fixe ces parametres.
La specification demo ne vaut pas tarif du broker reel.

Spread effectif propose = max(spread MT5 reconstruit, plancher conservateur
broker fixe independamment). L'ask effectif de la vue d'execution applique
ce spread une seule fois; aucune commission de spread ajoutee par-dessus.
Commission par cote et slippage de sortie sont distincts. L'entree limite
conserve la traversee proposee de 0.10 USD : aucun slippage d'entree ajoute
par-dessus, pour eviter le double comptage. Le modele effectif et l'audit des
couts doivent etre implementes et testes avant activation de cette option.

Stress prespecifie x1, x1.5 et x2 sur les composantes monetaires spread,
commission et slippage de sortie, sans ajustement selon P&L. Le stress x1.5
reste bloquant comme au preregistrement; choisir AVANT signature si x2 est
bloquant ou seulement rapporte. Les regles de signal, stop, cible, risque,
sizing et criteres statistiques restent ceux du Setup 1, sauf amendement
explicitement identifie et approuve. Aucun parametre ne peut etre retouche
apres observation du resultat.

## Trous et controles de donnees

Les absences sont classees en fermeture attendue du calendrier, minute isolee
ouverte, trou court 2-15 minutes ouvert et gros trou strictement >15 minutes
ouvert. Les gros trous portent non_tradable pour ATR, detecteurs et fills dans
un masque derive : aucune modification des prix. Toute barre agregee qui
recouvre un tel trou est bloquee. Les vues contiennent un gap_segment_id;
reinitialiser ATR/detecteurs apres chaque gros trou et annuler les ordres en
attente, avec warmup causal complet. Ne jamais calculer ATR ou un pattern en
traversant ces segments. Le branchement de ces controles dans le lecteur et
les consommateurs de baseline doit etre verifie avant le run. La politique
pour les trous courts doit etre validee avant signature.

Les flags spread extreme, nul ou constant ne sont pas des suppressions
silencieuses. Documenter leur origine et leur traitement avant activation;
le plancher broker independant ne suffit pas a prouver les extrema ask reels.
Le calendrier de fetes est une exclusion prudente, pas une attestation des
horaires historiques du broker. L'audit actuel doit etre approuve avec ses
exceptions : la couverture n'est pas declaree complete par cet amendement.

## Confirmation et conclusion conditionnelle

Un resultat MT5 positif signifie seulement que le Setup 1 survit dans ce
scenario de cotations synthetiques et de couts conservateurs. Il ne prouve pas
une edge executable. La conclusion positive definitive exige un trial de
confirmation preenregistre separement sur Dukascopy bid/ask observes ou sur
le broker reel; voir preregistration_mt5_confirmation_trial.md.

Le trial, la source, les dates, les couts, les criteres et le budget total
(probleme de tests multiples) sont fixes avant lancement. Une confirmation
negative annule la conclusion positive; pas de retuning ni choix retrospectif
d'une autre source. La limite actuelle de trois trials n'est pas relevee par
ce brouillon : verifier/reviser explicitement le budget avant enregistrement.

## Blocages avant baseline

- Validation de cet amendement, de la fin hold-out explicite et du nouveau SHA.
- Broker reel et valeurs conservatrices de cout documentees puis gelees.
- Decision du caractere bloquant de x2 et politique des trous courts.
- Validation des spreads nuls/constants/extremes et des limites ask OHLC.
- Lecteur MT5 et controles de segments branches/testes pour ATR, detecteurs,
  fills et ordres en attente; revue de precision/revue visuelle Setup 1.
- Audit MT5 approuve avec exclusions : pas d'attestation fictive de couverture.
- Gate actuelle exigeant un calibrage dev complet : adapter l'option approuvee
  sans permettre un calibrage de reference sur reconstructed_ask.
- Trial de confirmation, dates/source/budget preenregistres separement.

Aucun backtest; split.yaml et signature active inchanges.
