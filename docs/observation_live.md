# Observation live Exness — plumbing_only

Ce mode sert à regarder le fonctionnement du bot. Aucune valeur de preuve,
aucun verdict, aucun trial : aucune lecture/écriture du registre de trials.
La configuration signée, le préenregistrement principal et split.yaml restent
inchangés. Aucun backtest ni connexion MT5 n'a été exécuté pendant le développement.
Les comptes, ticks, barres et ordres des tests sont mockés.

## Avant de lancer sur votre poste

Ouvrir MT5, se connecter au compte **démo Exness Standard en USD** disposant
initialement de **10 000 USD**, symbole **XAUUSDm** visible. Le code vérifie
techniquement `account_info().trade_mode == 0`, le serveur Exness, la devise et
le capital initial (tolérance 1 USD). Contrat/minimum/pas/maximum attendus :
100 oz, 0,01/0,01/200 lots. Le point et les politiques d'exécution proviennent
du terminal : le point MetaQuotes n'est pas imposé au symbole Exness.
Le type commercial « Standard » reste à vérifier visuellement : l'API ne
fournit pas une attestation complète de cette désignation.

Synchroniser l'horloge Windows en UTC. Deux ticks distincts avançant sont requis
en 60 secondes pour établir le décalage des timestamps API à l'heure UTC du poste.
Le résidu autorisé est de 5 secondes; pas de 15 minutes et plage UTC−12 à UTC+14.
Un tick figé, un résidu incohérent ou une conversion dans le futur refuse/arrête
la session. Les epochs Python MT5 sont normalement UTC : un décalage détecté
de zéro est valide et ne signifie pas que l'horloge graphique du serveur est UTC.
Cette mesure ne prouve pas un fuseau historique/DST et dépend de l'horloge du poste.

Depuis D:\Arty avec Python et les dépendances installées :

```powershell
# Mode A : lecture MT5, aucun appel order_send possible
python tools/observe_live.py --expected-server "Exness-MT5Trial9" --expected-login 477484456 --html reports/observation_live/live.html

# Mode B : ordres limites sur DEMO seulement, compte dédié sans positions/ordres
python tools/observe_live.py --expected-server "Exness-MT5Trial9" --expected-login 477484456 --send-demo-orders --html reports/observation_live/live.html
```

L'outil utilise le terminal connecté; aucun mot de passe n'est écrit dans le
journal. Le mode B exige un compte dédié et refuse toute exposition préalable,
même manuelle. Type et identité du compte sont revérifiés avant chaque mutation.
Il n'existe pas de fallback qui simulerait silencieusement un échec broker.

## Ce qui est traité

Le symbole broker XAUUSDm est mappé en XAUUSD à la frontière du flux pour
réutiliser `definitions.yaml` et ses overrides. Une seule source
`application/setup1_source.py` est utilisée en observation et par le chemin
Setup 1 de `BacktestEngine.run_async`; elle utilise `SMCDetector` et
`find_swing_points`, sans recopier les détecteurs. La priorité des sorties est
partagée dans `modules/execution/setup1_policy.py`. Le replay de contrôle
générique à entrées marché reste un chemin différent et n'est pas lancé ici.

Le warmup lit jusqu'à 500 barres M5 closes, sans rejouer leurs signaux/ordres.
Les décisions ne sont prises qu'une fois par nouvelle clôture M5. Le BOS doit
être externe et avec displacement, la zone confirmée fraîche dans le même sens,
OB prioritaire puis FVG, limite au milieu, stop au-delà de la zone + 0,1 ATR,
distance minimale 0,5 ATR, TP 2R. Ordre soumis après clôture du BOS, fill sur une
barre suivante, expiration après 10 barres de marché, annulation sur cassure
opposée, sortie après 48 barres de marché ou à NY 17h. Une seule position/limite.
Pause/calendrier et 20 minutes après réouverture bloquent les entrées.

Mode A utilise `FillModel` et `CostModel` communs : crossing 0,10 USD,
stop prioritaire et possible sur la barre de fill, TP interdit sur cette barre,
prix bid pour la simulation, spread du modèle facturé une seule fois dans les
coûts. Le spread broker est un diagnostic affiché séparément. Le modèle de
référence refuse légitimement 2026 sans recalibrages annuels : cette observation
utilise explicitement un snapshot des spreads dev avec le même calcul
horaire/prix, marqué non validé pour 2026. Il ne change pas la configuration
de référence et ne prétend pas la débloquer. Commissions/slippage restent ceux
du snapshot, à confirmer; aucune estimation de coûts réels n'est inventée.

Mode B utilise les vrais fills et deals broker : les stops/TP protecteurs sont
envoyés avec la limite. Le broker est autoritaire, pas un OHLC simulé. Le
crossing théorique de FillModel ne peut pas imposer au broker son prix de fill.
Les écarts d'exécution sont de la plomberie, pas une validation de précision.
Pas de retry automatique sur envoi ambigu; réconciliation ou arrêt requis.
Risque demandé fixe : 100 USD (1% de 10 000), volume arrondi au pas inférieur,
levier brut ≤10x, marge ≤50% de l'équité et marge disponible réelle contrôlées.
Le sizing partagé applique aussi l'hypothèse prudente de marge du fichier actif.

## Lire l'affichage

Ouvrir `reports/observation_live/live.html` dans un navigateur. Il recharge le
fichier local toutes les 5 secondes, sans serveur HTTP. L'état contient :

- UTC, bid/ask, spread broker, spread modèle et leur différence;
- état ouvert/pause/post-réouverture, dernière clôture M5;
- swings confirmés, BOS récents, zones OB/FVG encore éligibles;
- limite et barres avant expiration, position, stop, TP et R latent brut;
- dernier candidat rejeté (ou absence de signal) et motif, équité et kill switch.

L'affichage signale Exness live versus MetaQuotes bid + ask reconstruit ou
Dukascopy observé. Aucun écart de prix contemporain n'est mesuré sans seconde
source synchrone; les écarts historiques restent dans le rapport MT5 existant.

## Arrêter et lire le journal

**Ctrl+C** arrête la boucle. En mode B, l'outil tente d'annuler ses limites et
de fermer ses positions; il ne modifie pas les ordres manuels. Une déconnexion
peut empêcher cette opération : `cleanup_failed` est écrit et une vérification
manuelle dans MT5 est nécessaire. Les SL/TP déjà acceptés restent côté broker.
En mode A, une simulation encore ouverte à l'arrêt est explicitement signalée,
sans résultat de clôture fabriqué.

Kill switch verrouillé pour la session : perte journalière ou de session 3%,
3 erreurs d'exécution consécutives, tick obsolète >180 s, déconnexion, spread
>0,60 USD/oz. Une clôture M5 manquée arrête aussi le mode : pas de catch-up
d'ordres sur des prix passés. Une pause peut rendre le flux obsolète et arrêter
la session; redémarrer explicitement à la réouverture. Aucun restart automatique.

SQLite : `reports/observation_live/observation.sqlite`, tables
`observation_runs` et `observation_events`. Chaque ligne porte `plumbing_only=1`.
Le manifeste de démarrage inclut empreintes du code, configuration active,
modèles, spécification broker, horodatage et scope non validant. Les événements
contiennent signaux acceptés/rejetés, placements, fills, expirations, deals,
arrêts et échecs de nettoyage. Aucune table de trials ni métrique probante.

```sql
SELECT id, plumbing_only, manifest FROM observation_runs;
SELECT utc, kind, payload FROM observation_events
WHERE run_id = '<run_id>' ORDER BY id;
```

## Décisions à valider

Le capital, le contrat et la grille de lots fournis sont déjà confirmés.
Restent : accepter les seuils opérationnels 3%/3 erreurs/180 s/0,60 USD,
le comportement arrêt/restart aux pauses et le snapshot de coûts pour observation;
vérifier visuellement le compte Standard, l'horloge et le terminal utilisé;
choisir explicitement le mode B au lancement. Les décisions de recherche
(amendement, coûts broker réels, hold-out et trial de confirmation) restent
séparées et ne bloquent pas ce mode sans valeur de preuve.


## Audit Mode B avant envoi

Remplacer `Exness-Demo` dans la commande par le nom exact affich? dans MT5.
Le serveur est compar? exactement, puis son identit? est rev?rifi?e avant chaque
mutation. Le symbole retourn? doit ?tre XAUUSDm. Minimum/pas/maximum, tick_value,
tick_size et stops level viennent dynamiquement du terminal; aucun arrondi des
prix ne modifie le signal. Un prix incompatible est refus? par order_check.
Le budget demand? reste 100 USD, arrondi au pas inf?rieur, avec les plafonds de
levier et de marge existants. Tout candidat, audit, order_check et order_send est
journalis?; les retcodes, prix demand?/rempli, spread et slippage sont conserv?s.
Un placement de limite ne constitue pas un fill; le fill r?el est journalis? lors
de la r?conciliation. Aucun retry d'un candidat d?j? envoy?, m?me apr?s timeout.

La variante optionnelle trois positions n'est pas impl?ment?e : une position ou
limite maximum. Les tables observation restent s?par?es des rapports de validation.
? valider avant lancement : nom exact du serveur, compte d?di? d?mo USD 10000,
seuils du kill switch, r?duction du risque par grille/marge, arr?t et nettoyage
sur Ctrl+C, et choix explicite --send-demo-orders. Aucune preuve statistique.


D?cisions op?rationnelles accept?es : serveur Exness-MT5Trial9, login 477484456,
compte d?mo USD 10000, kill switch 3%/3 erreurs/180 s/0,60, risque demand?
100 USD avec arrondi inf?rieur, nettoyage ? l'arr?t, plumbing_only sans preuve.
Connexion dans le terminal MT5 : aucun mot de passe dans les arguments ou fichiers.
`order_audit` et `fill_risk_audit` enregistrent initial_risk_usd,
risk_fraction_reference et within_one_percent. La r?f?rence est 10000 USD.
Le risque est la distance entr?e r?elle?stop, hors frais et slippage du stop;
une perte finale peut d?passer ce montant lors d'un gap. Un d?passement d?tect?
au fill d?clenche l'arr?t et le nettoyage de l'exposition.
