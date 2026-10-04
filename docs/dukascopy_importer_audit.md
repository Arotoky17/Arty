# Audit de l'importateur Dukascopy

## Diagnostic et périmètre

L'erreur locale rapportée `WinError 10060` est un timeout réseau. L'indentation
de `fetch_bytes` et la syntaxe Python sont valides ; elles n'expliquent pas cette
erreur. Depuis le processus sandbox de cet audit, les deux sondes HTTPS (site
et datafeed) échouent avec `WinError 10013` : restriction de socket observée
localement. Cela ne prouve ni une panne Dukascopy ni la cause précise du timeout
sur la machine utilisateur. Aucune désactivation TLS ni contournement réseau.

Les URL gardent les mois à base zéro et les fichiers BID/ASK M1 distincts.
Les 15 ressources journalières de janvier 2020 existantes ont été contrôlées :
checksums BI5/CSV conformes au manifeste et CSV identiques après redécodage.
Ces contrôles ne prouvent pas la complétude du marché ou l'absence d'anomalies
économiques. Les données brutes, le manifeste et le statut du véritable import
n'ont pas été modifiés pendant cet audit. Aucun fichier du hold-out ouvert.
L'import réel reste incomplet ; aucun backtest ni ordre MT5 exécuté.

## Corrections

- Réessais bornés : 3 tentatives, timeout 30 secondes, attente 5 puis 10 secondes,
  plafond 60 secondes et jitter 1–2 secondes avant chaque requête, selon
  `config/data_import.yaml` (configuration conservée). Téléchargement séquentiel.
- Réessais pour 429/500/502/503/504, timeout, connexion interrompue et réponse
  tronquée. Les autres erreurs HTTP permanentes et certificats TLS invalides
  arrêtent immédiatement. Le diagnostic conserve URL, type et message d'erreur.
- `Retry-After` numérique ou date HTTP respecté ; valeur invalide ignorée au
  profit du backoff. Une attente supérieure au plafond arrête la tentative et
  demande une reprise après le délai, sans raccourcir celui demandé par le serveur.
- 404 signifie ressource indisponible, jamais marché fermé : une paire manquante
  interdit la finalisation. Aucun remplissage artificiel de bougies manquantes.
- Réponse vide, flux LZMA incomplet/corrompu, octets supplémentaires, enregistrement
  tronqué ou absence de bougie clôturée rejetés avant publication.
- Reprise append-only avec checksums ; fichiers vérifiés conservés, seules les
  ressources manquantes sont demandées. Comparaison exacte des timestamps BID/ASK
  et contrôle ASK >= BID sur OHLC avant validation de la paire. Un échec demande
  investigation, sans réparation automatique des cotations.
- `--until` impose une borne exclusive UTC dans le développement. Les fichiers
  bruts hors périmètre ne sont pas lus ; ce mode ne fige pas la fin du hold-out.
  `coverage_validated` reste faux : l'intégrité des ressources ne remplace pas
  l'audit de couverture mensuelle.

## Vérification exécutée

```powershell
.\.venv\Scripts\python.exe -m py_compile src/arty_trading/validation/dukascopy_import.py tools/fetch_dukascopy.py
.\.venv\Scripts\python.exe -m pytest tests/test_dukascopy_import.py -q -p no:cacheprovider --basetemp=.quality-cache/importer-unit
.\.venv\Scripts\python.exe -m pytest tests/test_data_readiness.py tests/test_xauusd_validation.py -q -p no:cacheprovider --basetemp=.quality-cache/importer-existing
.\.venv\Scripts\python.exe scripts/check_baselines.py check all
.\.venv\Scripts\python.exe tools/fetch_dukascopy.py --help
.\.venv\Scripts\python.exe tools/fetch_dukascopy.py --diagnose-network
```

Compilation et aide CLI : succès. Tests mockés : **35 réussis** ; tests existants
concernés : **29 réussis** ; aucun échec ni test ignoré. Baselines Ruff/mypy :
aucun nouveau diagnostic (642/231 diagnostics préexistants). La commande réseau
produit un rapport valide, mais **les deux connexions échouent** avec 10013.
Logs : `.quality-cache/importer-{unit,existing,quality,network}.txt`.
Preuve d'intégrité et diagnostic :
`reports/importer_audit/integrity_and_network.json`.
Non exécutés : import complet réel, accès au hold-out, backtest, ordre MT5.

## Prochaines commandes sur la machine locale

```powershell
Set-Location -LiteralPath 'D:\Arty'
.\.venv\Scripts\python.exe .\tools\fetch_dukascopy.py --diagnose-network
.\.venv\Scripts\python.exe .\tools\fetch_dukascopy.py --output .\data\raw --until '2025-01-01T00:00:00+00:00'
```

Relancer exactement la dernière commande reprend sans remplacer les fichiers
déjà vérifiés. La borne exclut toute donnée de 2025. Ne pas lancer la commande
sans `--until` dans le cadre de cette mission. Si le timeout persiste localement,
le routage, proxy ou pare-feu vers datafeed:443 doit être diagnostiqué ; les
réessais corrigés ne garantissent pas sa résolution. Une ressource 404/vide
reste bloquante en attendant une justification indépendante de disponibilité.

Références de configuration inspectées :
[export Dukascopy](https://www.dukascopy.com/wiki/en/development/data-export/),
[implémentation dukascopy-node](https://github.com/Leo4815162342/dukascopy-node).
