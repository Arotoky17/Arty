# Validation de recherche — étape 1

## Configuration et lancement

Les définitions opérationnelles sont dans `config/definitions.yaml`. Les fenêtres
UTC sont figées dans `config/split.yaml` : développement du 01/01/2020 inclus au
01/01/2025 exclu ; hold-out du 01/01/2025 inclus au 01/01/2027 exclu (~71/29).
Les données locales disponibles ne couvrent que XAUUSD, janvier–juin 2024.
La fin 2026 reste future à la date de cette implémentation : aucune couverture
complète du hold-out n'est revendiquée.

Les modèles chargent obligatoirement `config/execution.yaml`, sauf injection
explicite de `CostModel` et `FillModel`. Une configuration absente ou invalide
empêche le lancement. Les valeurs de coûts proposées sont illustratives.
Installer le dépôt en mode editable et lancer depuis sa racine.

```python
from arty_trading.modules.backtesting.engine import BacktestEngine
from arty_trading.validation.trial_registry import TrialRegistry

registry = TrialRegistry("data/validation.sqlite")
engine = BacktestEngine(setup_id="bos_retest_v1", parameters={"variant": "baseline"},
                        registry=registry)
stats = await engine.run_async(dev_candles, signal_generator, smc_detector)
print(stats.expectancy_r, stats.profit_factor, stats.max_drawdown,
      stats.total_trades, stats.fill_rate, stats.unfilled_orders)
print(registry.get_n_trials())
```

Le pipeline de génération existant reste injectable. Ce code ne constitue pas
encore la baseline sans filtres de l'étape 2. Copier et remplir
`preregistration.md` avant toute phase. Tous les arguments effectifs propres à la
stratégie doivent être fournis dans `parameters`, y compris ceux des composants
injectés ; le moteur ajoute les définitions, le split, le symbole, le capital,
le risque, les coûts et le fill à l'empreinte.

```powershell
.venv\Scripts\python.exe -m pytest tests --ignore=tests/integration -p no:cacheprovider --basetemp=.quality-cache/validation-pytest
.venv\Scripts\python.exe scripts/check_baselines.py fresh all
```

## Registre et lecture des résultats

SQLite est utilisé sans nouvelle dépendance. `trials` contient un essai réservé
avec le statut `running` AVANT la simulation ; sa finalisation est obligatoire
pour retourner des statistiques. Si la simulation ou l'écriture finale échoue,
la ligne reste `running` et compte dans `get_n_trials()` : un essai interrompu
ne disparaît pas du nombre de tentatives. Aucun résultat réussi n'est retourné
si l'écriture est impossible. Le nombre peut être filtré par setup ; le nombre
global reste disponible pour la correction des essais multiples.

```sql
SELECT timestamp, setup_id, parameter_hash, timeframe, period_start, period_end,
       partition, status, n_trades, expectancy_r, profit_factor, sharpe,
       max_drawdown, fill_rate FROM trials ORDER BY id;
SELECT setup_id, COUNT(*) FROM holdout_access GROUP BY setup_id;
```

`expectancy_r` est nette des coûts et divisée par le risque monétaire initial
de chaque ordre. Les sorties partielles sont regroupées par ticket. Le PF est
net de coûts ; le MDD est une fraction de l'équité. Le Sharpe existant est une
moyenne/écart-type par trade, sans annualisation, et ne doit pas être interprété
comme un Sharpe annuel. Les intervalles bootstrap et le rapport de baseline
seront introduits à l'étape 2 ; DSR et Reality Check restent à l'étape 5.

`fill_rate` = ordres remplis / ordres soumis. Les ordres expirés et ceux encore
en attente à la fin sont non remplis. Les signaux invalides ou rejetés par le
risque ne sont pas des fills. Le replay de contrôle historique utilise des
ordres au marché et ses cotations bid/ask réelles : le mode touché/traversé
concerne les limites des moteurs simple et MTF. Son ratio trades/signaux
inclut encore les rejets de risque ; il reste un diagnostic legacy.

Les tests isolent leur registre avec `ARTY_TRIAL_REGISTRY` et ne consomment
jamais le registre de recherche. Ne pas remplacer, effacer ou déplacer ce
dernier entre les phases : le garde-fou persiste dans cette base.

## Accès au hold-out

Un chargement ou un run ordinaire rejette les bougies hors développement,
y compris les timeframes de contexte. Les lecteurs CSV et les demandes
d'historique protègent également les périodes hors développement.

```python
# Exemple documentaire uniquement : ne pas exécuter pendant le développement.
holdout = engine.load_holdout(
    "bos_retest_v1", "évaluation finale des critères préenregistrés",
    enabled=True, loader=read_holdout_candles,
)
stats = await engine.run_async(holdout, signal_generator, smc_detector)
```

Le loader n'est appelé qu'après écriture atomique de l'accès : date UTC, setup
et raison. Deux processus concurrents ne peuvent pas obtenir un premier accès
pour le même setup. Même un loader qui échoue consomme cet accès. Un second
accès exige `override=True` ET `override_reason` non vide, journalisé. Le lot
autorisé ne peut être modifié ni réutilisé pour un deuxième run.

Pour MTF, le loader retourne un dictionnaire de listes M5/M15/H1 ; un seul accès
autorise l'ensemble, puis les listes exactes sont passées à `run_mtf_async`.
`ControlReplay.load_holdout` accepte de même un mapping `TimeFrame` → bougies.
La commande legacy de contrôle reste réservée au dev. Il s'agit de protections
des API applicatives, pas d'une restriction d'accès au système de fichiers.

## Écarts de comportement avant/après

| Définition | Avant | Après |
|---|---|---|
| Swings | fenêtres 2/5 en dur ; pivot structure disponible immédiatement | fenêtres YAML, pivot structure disponible après ses barres de confirmation |
| Displacement | OB : corps cumulé ≥ seuil ; ATR de la fenêtre entière | corps cumulé > seuil YAML ; ATR au moment de l'OB ; BOS structurels conservés avec qualification `details.displacement` |
| FVG | minimum fixe de 5 pips, ATR optionnel global | minimum strict de 0.1 ATR par défaut, 0.25 pour XAUUSD ; ATR à la formation |
| OB frais | âge 30 en dur ; pas d'indicateur de fraîcheur | âge YAML ; `details.fresh` exige un OB non mitigé ; les autres zones restent disponibles pour les consommateurs legacy |
| Sweep | réintégration dans la bougie, dépassement quelconque | dépassement minimal de 0.05 ATR, réintégration au plus 2 barres après le dépassement ; événement daté à la réintégration |
| EQH/EQL | tolérance fixe de 2 pips | 0.1 ATR à la confirmation du deuxième pivot |
| Profils instrument | seuils réécrits depuis Settings et copies de scripts | seuils du YAML commun au live et aux replays ; overrides XAUUSD existants conservés |
| Exécution simple/MTF | ouverture immédiate au signal, coûts absents | limite dès la barre suivante ; expiration, probabilité et seed ; coûts aller-retour |
| Intrabar | SL avant TP | SL avant TP, aucune attribution de TP sur la bougie du fill ; gap adverse au stop exécuté à l'ouverture |
| R | recalcul à partir du stop éventuellement déplacé | risque initial conservé et coûts déduits ; sorties partielles regroupées |

Les constructeurs explicites des détecteurs restent compatibles pour les
tests et intégrations existants ; leurs arguments remplacent les valeurs par
défaut du YAML. Pour une recherche préenregistrée, modifier le YAML et enregistrer
les paramètres effectifs, plutôt que multiplier ces overrides. Les détecteurs
continuent à retourner leurs concepts historiques ; aucune couche de stratégie
nouvelle n'est activée dans cette étape.

ATR : Wilder sur 14 transitions ; lorsque la fenêtre est trop courte,
`atr_warmup: available_mean` emploie la moyenne des transitions disponibles.
`require_full` permet d'exiger l'historique complet. Les distances en ATR
nécessitent une volatilité strictement positive.

## Hypothèses à valider avant l'étape 2

- Dates figées 2020–2024 / 2025–2026 et source de l'historique manquant.
- Instrument(s), broker, convention des OHLC et calibration des coûts.
- Seuils opérationnels proposés et politique de warmup ATR.
- Limites valables 10 barres, probabilité 1 par défaut, seed 0 ; slippage
  constant, pas de swap dans le modèle simple.
- Conservation des zones non fraîches dans les sorties legacy ; la future
  baseline devra sélectionner explicitement les OB frais.
- Bootstrap par blocs, paramètres préenregistrés, avant le premier rapport.

Aucune donnée brute n'a été modifiée. Aucun hold-out réel n'a été chargé.
