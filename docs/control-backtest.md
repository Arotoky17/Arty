# Contrôle XAUUSD 2024-H1

Commande :

```powershell
python -m arty_trading backtest --symbol XAUUSD --from 2024-01-01 --to 2024-06-30 --config config/baseline.yaml --output data/backtests/baseline_post_fixes_2024H1.json
```

La source est le terminal MT5 connecté (`terminal_path` facultatif), ou un
instantané JSON via `history_file`. Aucun historique synthétique n'est utilisé
en remplacement. L'instantané est sauvegardé dans `data/history` pour rejouer
exactement les mêmes entrées. Les dates sont en UTC, la fin est inclusive.

## Source CSV réelle pour ce contrôle

Le diagnostic du terminal `C:\Program Files\MetaTrader 5\terminal64.exe`
renvoie encore `(-10005, 'IPC timeout')`. La demande de fermeture propre du
processus n'a pas abouti ; aucune fermeture forcée du terminal n'a été effectuée.

La voie 2 utilise les fichiers M1 bid et ask du miroir
[Dukascopy XAUUSD](https://github.com/3650326613-png/dukascopy_xauusd_1m_data),
révision `1d8e4c008fe177be455f6de05f5057ebdde901ae`, janvier à juin 2024.
La [description de la source](https://github.com/3650326613-png/dukascopy_xauusd_1m_data/blob/1d8e4c008fe177be455f6de05f5057ebdde901ae/XAUUSD_DUKASCOPY_M1_BID_ASK_README.md)
indique un téléchargement via dukascopy-node et des timestamps UTC en
millisecondes. La provenance est celle déclarée par le miroir, pas un export
du compte MT5 de l'utilisateur. Les endpoints Dukascopy directs ont renvoyé
HTTP 429 lors de la tentative d'accès.

```powershell
python scripts/download_control_history.py
python -m arty_trading backtest --source csv --symbol XAUUSD --from 2024-01-01 --to 2024-06-30 --config config/baseline.yaml --output data/backtests/baseline_post_fixes_2024H1.json
```

`data/historical/source_manifest.json` enregistre les URL de révision fixe et
les SHA-256 des douze CSV (26 206 731 octets). Le chargeur vérifie les empreintes,
les limites OHLC, l'ordre et l'unicité des timestamps, puis l'égalité des
timestamps bid/ask. Il refuse de tronquer silencieusement les séries.
Les minutes dont les quatre prix sont identiques à la clôture précédente,
simultanément pour bid et ask, sont retirées avant agrégation. Cette règle peut
également retirer des minutes calmes ; elle évite d'inclure les répétitions
figées pendant les fermetures du marché.

Les M5/H1/H4 sont agrégés avec des bornes UTC fixes et des timestamps d'ouverture.
Seules les bougies fermées sont visibles au replay. Les OHLC ask sont conservés
pour les sorties short et le prix d'entrée long, plutôt que d'estimer toute
la bougie ask à partir du spread de clôture. Le spread en points utilisé par
les gardes est arrondi vers le haut. Ces CSV ne contiennent pas de volume :
le champ technique vaut zéro et `volume_available=false` le signale explicitement.
La configuration n'établit pas les commissions et swaps du broker réel ; les
métriques ne constituent donc pas une validation complète de rentabilité.

## Résultat du contrôle réel

Le rapport `data/backtests/baseline_post_fixes_2024H1.json` est un résultat
**d'échec du contrôle**, pas un baseline validé :

- 35 231 bougies M5, 2 939 H1 et 796 H4 issues des données réelles agrégées ;
- 3 trades, tous perdants : win rate 0, PF 0, expectancy -1 R ;
- drawdown maximal aux clôtures M5 : environ 2,9643 % ;
- moyenne : environ -150,8264 pips par trade (pip XAUUSD = 0,01) ;
- circuit breaker actif après trois pertes consécutives, sans reprise automatique.

`RiskManager.reset_daily()` efface les pertes monétaires du jour mais conserve
les pertes consécutives. Le replay préserve cette politique ; il ne la desserre
pas pour atteindre les vingt trades demandés. Après blocage, le compteur
`risk_circuit_breaker` compte les bougies sur lesquelles aucun nouvel ordre
ne peut être ouvert. Les autres compteurs concernent les étapes effectivement
atteintes avant blocage, pas des rejets hypothétiques après l'arrêt des signaux.

Les trois signaux proviennent du chemin legacy de la stratégie, sans identifiant
de setup/OB ni événement de confirmation traçable. Le rapport conserve les
timestamps d'entrée et les OHLC de la dernière bougie, mais les champs OB et
confirmation sont explicitement `null`. Il signale trois preuves d'audit
manquantes. Les grades sont `UNKNOWN` ; aucun OB n'est attribué par supposition.
Les zéros `no_contact_with_ob` et `choch_out_of_bounds` ne valident pas la sécurité :
aucun contrôle OB n'a pu être réalisé sur ces trois signaux. Les échantillons
sont limités aux trois trades réellement exécutés ; aucun quatrième/cinquième
trade n'est inventé.

Le replay respecte maintenant le câblage live : transmission du biais H1 au
validateur, moteur DecisionEngine injecté uniquement si activé, et copie du
Signal immuable pour appliquer le prix d'exécution bid/ask. Ces corrections
portent sur l'adaptateur de backtest ; les seuils et la logique live restent
ceux existants.

Les quatre tests d'acceptation échouent volontairement sur ce rapport : nombre
insuffisant et preuves OB absentes. Les assertions ne sont pas neutralisées
par un skip de données réelles ni par un changement de seuil. La source n'est
plus bloquante ; il reste à décider la politique de reprise du circuit breaker
et à rendre traçable le parcours legacy avant de pouvoir valider ce contrôle.

Deux lancements finaux ont produit des fichiers identiques octet par octet,
sur cet environnement Windows. SHA-256 du rapport :
`9a9e409dcce3889e9050cbc3c20aca0182ef721f03def55c8e070d8fa6f0f99e`.
Les CSV bruts sont conservés localement dans `data/historical` et sont ignorés
par Git ; le manifeste versionné et le script de téléchargement permettent
de récupérer exactement la révision utilisée.

Un export JSON doit contenir `source: "mt5_export"`, `symbol: "XAUUSD"`,
`candles: {"M5": [...], "H1": [...], "H4": [...]}`. Chaque bougie contient
`time` (secondes Unix UTC), `open`, `high`, `low`, `close`, `spread` (points)
et `tick_volume`. Les spécifications du contrat sont dans `broker` :
`contract_size`, `point`, `volume_min`, `volume_step`. Documenter les commissions
et le glissement dans la configuration. Le spread historique est obligatoire.

Le replay utilise les bougies fermées H4/H1/M5, le générateur existant et les
gardes du RiskManager, y compris pertes journalières, drawdown et pertes
consécutives. Le filtre de qualité OB reste désactivé. L'audit est observationnel :
une entrée legacy sans confirmation valide reste dans le résultat et fait
échouer la validation. La confirmation auditée est recalculée par le checker
corrigé ; elle n'est pas présentée comme une trace émise par le chemin legacy.

Le rapport contient tous les audits et cinq exemples, les empreintes des données
et de la configuration, et les hypothèses d'exécution. SL prévaut si SL et TP
sont touchés dans la même bougie ; spread et coûts configurés sont appliqués.
Les swaps et les annonces historiques ne sont pas modélisés. La graine est
enregistrée, mais le replay ne tire aucune valeur aléatoire.

Exit 2 : données indisponibles ou configuration invalide, sans rapport inventé.
Exit 1 : rapport réel créé, mais moins de 20 trades ou violation d'audit.
Exit 0 : contrôle réussi. Les quatre tests d'acceptation du rapport sont sautés
explicitement en son absence ; les tests unitaires de détection des violations
restent exécutés. Le précédent `_mtf_report.txt` utilise des données synthétiques
et une autre hiérarchie temporelle : ses zéro trades ne sont pas une comparaison
de performance sur les mêmes données réelles.
