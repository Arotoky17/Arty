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

## Résultat après réarmement du circuit breaker

Le baseline `data/backtests/baseline_post_fixes_2024H1.json` produit désormais
57 trades sur les 35 231 bougies M5 réelles, contre 3 avant correction.
Le diagnostic avec limite de série hors d'atteinte produit 35 trades ; le
blocage permanent des pertes consécutives était donc le blocage principal.
Le cooldown de 24 h utilise le temps historique et préserve les autres gardes.

Deux replays finaux produisent des JSON identiques octet par octet.
SHA-256 : `d62573ab7f8e23404ad7f69b20861202c93aee15c3ab27cb93ab2972b366a63d`.
Le rapport enregistre huit réarmements du breaker.

Le win rate est 28,07 %, le PF 0,774, l'expectancy -0,158 R et le drawdown
10,71 %. Le baseline reste déficitaire. Le drawdown finit par arrêter les
ouvertures ; le garde n'empêche pas une position déjà ouverte de dépasser 10 %.

Le critère de volume (au moins 20 trades) est satisfait. Les preuves OB restent
absentes des 57 trades legacy ; le rapport et son exit CLI restent en échec de
validation d'audit. Cette limite du fallback est documentée dans
[Limites du legacy](legacy-limitations.md), avec les politiques, compteurs,
contrôles synthétiques et tests en attente de la Tâche 4.

Les anciennes assertions d'audit sont sautées uniquement lorsque le rapport
est entièrement legacy sans preuves OB. Elles restent actives pour un rapport
avec traces. Le skip `test_baseline_ob_traces_pending` rappelle explicitement
la Tâche 4. Les tests unitaires de violations d'audit continuent de vérifier
les entrées prématurées, les fenêtres et le contact avec l'OB.

Le replay respecte le câblage live : biais H1 transmis au validateur,
DecisionEngine injecté uniquement si activé, et copie du Signal immuable pour
appliquer les prix d'exécution bid/ask. Les CSV bruts restent locaux et ignorés
par Git ; manifeste et script permettent de récupérer la révision utilisée.

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
Exit 0 : contrôle complet réussi (volume et audit). Les tests nécessitant un
rapport réel sont sautés en son absence ; les tests unitaires de violations
restent exécutés. Le fallback legacy n'obtient pas cet exit 0 sans preuve OB.
Le précédent `_mtf_report.txt` utilise des données synthétiques
et une autre hiérarchie temporelle : ses zéro trades ne sont pas une comparaison
de performance sur les mêmes données réelles.
