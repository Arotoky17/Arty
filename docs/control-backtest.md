# Contrôle XAUUSD 2024-H1

Commande :

```powershell
python -m arty_trading backtest --symbol XAUUSD --from 2024-01-01 --to 2024-06-30 --config config/baseline.yaml --output data/backtests/baseline_post_fixes_2024H1.json
```

La source est le terminal MT5 connecté (`terminal_path` facultatif), ou un
instantané JSON via `history_file`. Aucun historique synthétique n'est utilisé
en remplacement. L'instantané est sauvegardé dans `data/history` pour rejouer
exactement les mêmes entrées. Les dates sont en UTC, la fin est inclusive.

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
