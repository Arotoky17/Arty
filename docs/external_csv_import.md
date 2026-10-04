# Plan B — import de CSV M1 externes (bid/ask)

Commande : `python tools/fetch_dukascopy.py --csv-root <repertoire> [--until ...] [--output ...]`
Implémentation : `src/arty_trading/validation/csv_import.py` (`run_csv_import`).

## Format attendu

Un fichier CSV **par jour UTC et par côté** (`bid`, `ask`) :

| Emplacement | Recherche |
|---|---|
| `<root>/xauusd/<side>/<YYYY-MM-DD>.csv` | 1er choix |
| `<root>/<side>/<YYYY-MM-DD>.csv` | 2e choix |
| `<root>/<YYYY-MM-DD>_<side>.csv` | 3e choix |
| `<root>/<YYYY-MM-DD>.csv` | dernier recours |
| n'importe quel `*<YYYY-MM-DD>*.csv` sous ces dossiers | repli |

En-tête **exactement** :

```
timestamp,open,high,low,close,volume
```

Contraintes auditées avant publication (identiques au décodeur Dukascopy) :

- `timestamp` : **entier**, millisecondes Unix **UTC**, aligné à la minute
  (`% 60000 == 0`), **strictement croissant**, unique, et **contenu dans le jour UTC nommé**.
- `open`/`high`/`low`/`close` : prix finis et **strictement positifs**, avec
  `low <= min(open, close) <= max(open, close) <= high`.
- `volume` : entier de tick-volume **>= 0**.
- **Cotation croisée** : pour chaque timestamp, `bid.close <= ask.close` ;
  un nombre de lignes différent entre les deux côtés est rejeté.
- Un jour **entièrement fermé** au sens de `config/market_calendar.yaml`
  (weekend, pause, jour férié configuré) **ne nécessite aucun fichier** et n'est
  jamais compté comme manquant.

## Sortie et garanties

- Publication **append-only** via `publish_immutable` : un objet existant dont le
  contenu diffère est refusé, jamais réécrit.
- **SHA-256** du fichier canonique publié et du fichier source, journalisés dans
  `data/raw/import_manifest.jsonl` avec le même schéma que l'import Dukascopy
  (`date`, `side`, `rows`, `last_closed_at`, `status`, `path`, `sha256`,
  `snapshot_cutoff`) ; le champ `provider` vaut `external_csv`.
- Les entrées CSV n'ont **pas** de `binary_path` : la boucle de reprise de
  `run_fetch` ignore alors cette clé au lieu d'échouer.
- Rapport : `reports/data_readiness/import/import_status_csv.json`
  (`verified_daily_sides`, `deferred_files`, `missing_files`, `resume_required`).
- Un fichier absent devient `missing_source` ; un fichier invalide devient
  `rejected` avec le motif. Aucun des deux n'écrit de donnée brute.
- Le gel de la fin du hold-out (`freeze_available_end`) n'est déclenché que si
  `--until` est omis **et** qu'aucun fichier n'est différé ou manquant.

Aucun appel réseau : ce mode lit uniquement le dossier fourni. Aucun backtest.