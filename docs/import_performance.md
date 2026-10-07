# Import : reprise sans accès brut et téléchargements concurrents

Mesures du 5 octobre 2026, exclusivement hors ligne. Aucun appel réseau réel ni
backtest. Les écritures utilisent le disque du workspace, les véritables SHA-256,
liens immuables et `fsync`. Les modifications préexistantes du workspace sont conservées.

## Causes confirmées

La version précédente lisait le manifeste une seule fois par session, mais
re-checksumait tous les objets historiques et relisait les CSV pour l’appariement
BID/ASK. La première version, avant cette optimisation, redécodait aussi les
fichiers et ajoutait des enregistrements répétés à chaque reprise. Ces doublons
faisaient croître le travail avec le manifeste. Le contrôle historique avait
ainsi effectué 14 660 lectures pour seulement 1 244 objets distincts.

Sur le chemin réussi, le délai est appliqué **une fois par requête/fichier**.
Les autres attentes sont les retries HTTP, les retries fichier et le cooldown
global du fournisseur. Un taux de 0 % d’erreurs finales n’exclut pas des retries
finalement réussis. Les chronométrages locaux mockés ne reproduisent pas les
19 s/fichier observés ; la durée réelle du réseau n’a pas été mesurée.

## Nouvelle architecture

- Un passage `index_manifest` construit les index des entrées vérifiées, des
  checksums enregistrés et des certificats BID/ASK. Le manifeste n’est jamais
  relu par fichier. Les checksums contradictoires sont rejetés en mémoire.
- Une reprise ordinaire fait confiance aux objets immuables validés et inscrits
  au manifeste : aucune ouverture de BI5/CSV, aucun re-checksum ni décodage pour
  les journées complètes réutilisables. Les snapshots partiels et les signatures
  de validation modifiées restent traités comme du travail à effectuer.
- Les anciens enregistrements sans signature sont réutilisables avec les
  paramètres actuels par défaut. `--verify-existing` effectue volontairement
  l’audit SHA-256 sur disque, une fois par objet, avant le budget de téléchargement.
  La reprise ordinaire ne cherche donc pas une altération externe des fichiers.
- `--workers` accepte 1 à 32 téléchargements concurrents ; le défaut CLI est 1.
  Les workers téléchargent, décodent et valident en mémoire. Une file bornée à
  `workers` résultats limite également les fichiers préparés en attente.
- Le thread principal est l’unique écrivain. Il consomme les résultats dans
  l’ordre prévu date/BID/ASK, publie les BI5 et CSV immuables, puis ajoute les
  entrées au manifeste. Les workers ne publient aucun fichier et n’écrivent pas
  le manifeste. Le verrou SQLite de session reste actif.
- Chaque worker conserve son délai et sa politique de backoff. Un 429/503
  prolonge une échéance partagée respectant `Retry-After` ; tous les workers
  attendent avant leur prochaine requête. Les requêtes déjà en vol peuvent finir.
  Un `Retry-After` dépassant le cap arrête toute la session avec `provider_pause`.
- Le budget `--max-runtime` démarre après indexation, planification et audit
  éventuel. Les délais et timeouts de requête sont bornés par le temps restant.
  À expiration, aucune nouvelle requête n’est lancée ; les requêtes en cours sont
  terminées ou annulées avant la fermeture du pool. Une reprise sans travail ne
  consomme pas ce budget et n’ajoute aucune entrée au manifeste.

Les règles de décodage, validation OHLC/temps/volume et les SHA-256 des nouveaux
objets restent identiques. Le writer conserve les comparaisons d’immuabilité et
les écritures synchronisées. Les snapshots distincts conservent leurs chemins.

## Appariement BID/ASK sans relecture à la reprise

Lorsqu’une paire nouvelle passe les contrôles existants d’égalité des timestamps
et de non-croisement des quotes, le writer enregistre un certificat `pair_verified`
avec les deux SHA-256, la signature du décodeur et la dernière clôture commune.
La reprise utilise ce certificat sans rouvrir les fichiers.

Les anciens manifestes n’enregistrent pas cette preuve. Une paire ancienne ou
mixte sans certificat est réutilisée, mais figure dans `pairing_audit_required`.
Le rapport n’affirme pas `bid_ask_pairing_validated` et ne fige pas un nouvel
holdout tant que cette vérification manque. `--verify-existing` certifie ces
paires avec les contrôles existants ; les reprises suivantes utilisent la preuve.
Une journée attendue fermée ne nécessite pas de paire, comme précédemment.

## Journal

Chaque fichier traité porte `phase_timings` : `network_seconds`, `delay_seconds`,
`backoff_seconds`, `cache_read_seconds`, `bi5_decode_seconds`,
`validation_seconds`, `checksum_seconds`, `write_seconds`, `total_seconds`.
Les mesures réseau excluent les délais/backoffs ; la validation inclut la
sérialisation CSV ; la publication exclut les SHA-256.

Un événement `session_timing` rassemble les phases et ajoute
`manifest_append_seconds`, `existing_checksum_seconds`, `resume_seconds`,
`reused_daily_sides` et `workers`. Le rapport JSON reprend ces mesures.
Les temps de workers peuvent se recouvrir : leur somme n’est pas le temps mural
de la session. Le temps d’ajout au manifeste mesure les entrées de fichiers,
sans inclure l’événement final ni les certificats d’appariement.
Une reprise sans nouveau fichier laisse les octets du manifeste inchangés et
publie ses mesures uniquement dans le rapport.

## Mesures

| Expérience | Résultat |
| --- | ---: |
| Reprise de 60 fichiers, avant / après, cProfile actif | 1,019 s → 0,086 s, ×11,8 |
| Reprise ancienne de 600 fichiers, 6 000 entrées répétées | 0,460 s, aucune lecture brute/checksum/validation |
| 60 fichiers, latence simulée 150 ms, 1 worker | 14,952 s |
| Même expérience, 4 workers | 6,095 s, ×2,45 |

Le benchmark de concurrence utilise seulement les timers de phases pour éviter
de comparer des profils CPU de threads différents. Les délais de 2 s sont
mockés : 60 appels à `sleep`, exactement 120 s demandées, mais ces 120 s ne
figurent pas dans le temps mural. La latence de 150 ms est simulée par une attente
locale indépendante. Ces résultats ne prédisent pas le débit du fournisseur réel.

Profil par fichier nouveau, réseau mocké sans latence, 1 worker, cProfile actif :

| Phase | Moyenne |
| --- | ---: |
| Téléchargement mocké hors délai | 0,44 ms |
| Décompression BI5 | 0,37 ms |
| Validation et sérialisation CSV | 36,30 ms |
| SHA-256 | 0,42 ms |
| Publication immuable hors SHA-256 | 13,09 ms |
| Ajout durable au manifeste | 3,83 ms |

La session entière de 60 fichiers prend 5,114 s, avec préparation et appariement
final. La lecture/indexation du manifeste reste O(nombre d’entrées), une fois par
session ; les reprises cessent de créer des doublons de fichiers validés.

## Reproduction hors ligne

```powershell
python -m tools.profile_import --output .quality-cache/import_concurrent_after.json
python -m tools.profile_import --workers 1 --mock-latency 0.15 --no-cprofile --output .quality-cache/import_workers_1.json
python -m tools.profile_import --workers 4 --mock-latency 0.15 --no-cprofile --output .quality-cache/import_workers_4.json
python -m tools.profile_import --resume-only --days 300 --output .quality-cache/import_resume_600.json
```

`--resume-only` crée uniquement un manifeste synthétique ancien : les objets
bruts sont volontairement absents. Le benchmark interdit réseau, checksum et
décodage, vérifie les 600 côtés réutilisés et l’identité du manifeste avant/après.

72 tests ciblés passent : concurrence effective à quatre workers, écriture sur
un seul thread, ordre du manifeste malgré des fins de téléchargement inversées,
absence de doublons, reprise idempotente sans accès brut, index unique, reprises
anciennes, audit explicite des corruptions, signatures modifiées, snapshots
partiels, délai unique, budget hors reprise, expiration avant requête,
`Retry-After` partagé 429/503, dépassement du cap, récupération du journal et
contraintes BID/ASK. Ruff passe. Aucun test de backtest n’a été exécuté.

Les nouveaux paramètres de l’import réel sont disponibles via
`python -m tools.fetch_dukascopy --help`. Aucun import réel n’a été lancé ici.
