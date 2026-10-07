# Forward demo sur compte démo

Le forward demo exécute le Setup 1 **en direct sur un compte démo MT5**, en
complément du backtest. Une seule implémentation de stratégie, deux modes
d'exécution : le paquet `src/arty_trading/forward/` n'ajoute que la politique
opérationnelle qu'un compte démo impose (gel de version, vérification du
compte, kill switch, journal, réconciliation, reporting et audit broker). Aucune
logique de détection n'y est dupliquée : les détecteurs partagés,
`config/definitions.yaml` et le préenregistrement sont réutilisés à l'identique.

**Aucun ordre n'est jamais envoyé sans préenregistrement signé ni manifeste de
run validé.** Le mode démo est tracé sous un setup id dérivé
(`setup_1_bos_retest_ob_fvg_v1:forward_demo`) : il ne consomme jamais les trois
essais du backtest.

## Commandes

```powershell
# 1. Geler code, configs, modèle de coûts, modèle d'exécution et préenregistrement
python tools/forward_demo.py manifest

# 2. Re-vérifier le gel (toute modification invalide le run)
python tools/forward_demo.py verify

# 3. État go/no-go : manifeste, préenregistrement signé, compte démo broker
python tools/forward_demo.py preflight

# 4. Rapports hebdomadaires et mensuels depuis le journal SQLite
python tools/forward_demo.py report --run-id <run_id>

# 5. Démarrage de session : refuse sans approbation explicite
python tools/forward_demo.py run --execute --i-approve-forward-demo
```

Chaque sous-commande est non destructive par défaut : `manifest`, `verify` et
`preflight` ne consomment aucun essai et n'envoient aucun ordre.

## Portes de sécurité

| Porte | Contrôle |
| --- | --- |
| Compte démo | `mt5.account_info().trade_mode` doit valoir exactement `0`. Un drapeau de configuration ne suffit jamais : `1` (réel) et `2` (concours) sont refusés. |
| Préenregistrement | `approval.status == approved` + approbateur + horodatage UTC + empreinte de configuration. `config/forward_demo.yaml` ne fait que documenter l'attente. |
| Gel de version | Empreinte SHA-256 du code exécuté, des configurations surveillées, des modèles de coûts/d'exécution et du préenregistrement. Une modification en cours de run lève `RunInvalidatedError`. |
| Kill switch | Perte journalière, perte cumulée sur le run, erreurs d'exécution consécutives, flux périmé ou déconnecté, spread anormal. Une déclenchement reste halté jusqu'à relance explicite. |
| Approbation | `run` refuse en dry-run sans `--execute` **et** `--i-approve-forward-demo`. |

## Boucle d'exécution live

`ForwardDemoRunner` ne contient aucune règle de stratégie : les signaux arrivent
par `signal_source` injecté (`SMCDetector` + `SignalGenerator`). La boucle
applique uniquement la politique opérationnelle du Setup 1 :

1. `session_gate` — jour non tradable, pause quotidienne, fenêtre des 20
   minutes après réouverture (depuis `config/market_calendar.yaml`).
2. `risk_gate` — kill switch actif, une seule position, `pending_max` ordres en
   attente.
3. `on_signal` — ordre limite au milieu de la zone, expiration à 10 barres
   fermées, stop avec buffer `0.1 ATR`, TP à `2R`.
4. `on_fill` — enregistre l'horodatage d'entrée et la limite des 48 barres.
5. `manage_open_position` — priorité au stop sur le TP, sortie à la clôture de
   la 48e barre ou à NY 17:00, jamais de position overnight.
6. `poll` — rafraîchit l'équité, le spread et le dernier tick dans le kill
   switch avant chaque décision.

Le broker est injecté par `BrokerPort` (`place_limit`, `cancel`,
`close_position`, `open_positions`, `equity`, `spread`, `last_tick_utc`) : les
tests l'ont entièrement simulé, sans réseau ni MetaTrader5.

## Journal et reporting

`ForwardJournal` (SQLite) écrit une ligne par signal considéré (rempli, expiré,
rejeté ou ignoré, avec le prix théorique, le stop, la cible, le motif, le prix de
remplissage, le spread, le slippage et la latence) et une ligne par trade clos
avec le R brut et net. Les décisions de petit compte sont stockées dans la
colonne `sizing` et extraites par `small_account_rows()` afin d'être rapportées
**séparément** des statistiques principales.

`reporting.build_report` réutilise le bootstrap en blocs hebdomadaires UTC du
backtest (`xauusd_diagnostics.summarize`) : l'intervalle du forward est mesuré
par exactement la même méthode que le préenregistrement. Aucun verdict n'est
émis avant `minimum_trades_for_verdict` (100) trades remplis ; en dessous,
`verdict_status = insufficient_trades`.

## Audit broker

`broker_audit.build_audit` compare les bougies M1 du broker à la boutique brute
Dukascopy (fuseau, symbole, sessions, écarts de niveau) et enregistre la
spécification du symbole réellement utilisée (taille de contrat, commission,
spread, levier, grille de volume) face aux valeurs configurées. Il ne lit aucun
P&L.

## Réconciliation

`reconcile.compare_signals` rejoue une période dans le moteur de backtest sur
données broker puis compare, signal par signal, avec ce que la session live a
produit. Tolérances par défaut : `0.05` sur le prix, `90 s` sur le temps. Toute
différence non expliquée lève une alerte ; un écart inexpliqué invalide la
comparaison.

## Limites connues

- `tools/forward_demo.py` `run` autorise la session mais ne contient pas encore
  la boucle live complète : le câblage de la boucle ( boucle de polling et
  ordres réels) reste à connecter à l'exécutant MT5 existant.
- Les coûts réels (`costs_usd`) doivent provenir des deals du broker ; la
  valeur par défaut `0.0` signifie « coût broker non encore renseigné ».
- Le spread calibré (`spread_calibration.yaml`) est la référence de modèle ; le
  spread broker réel est journalisé à côté pour le diagnostic.

## Liens

- `preregistration.md` — préenregistrement à signer avant tout run.
- `config/forward_demo.yaml` — politique du compte démo.
- `docs/validation.md` — critères de succès et budget d'essais.
- `docs/quality-baselines.md` — baselines Ruff/mypy figées.
