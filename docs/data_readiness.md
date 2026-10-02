# Préparation des données sans backtest

## Diagnostic de l'audit bid/ask

La règle initiale combinait trois conditions par OR :

- `abs(close / previous_close - 1) > 0.02` ;
- `(high - low) / previous_close > 0.02` ;
- `TR > 20 × ATR_précédent`, avec ATR positif et
  `TR = max(high-low, abs(high-previous_close), abs(low-previous_close))`.

L'ATR d'audit est une EWM causale des TR précédents, alpha 1/14,
`adjust=False`. Il n'est pas l'ATR opérationnel des détecteurs.
Les seuils sont configurés dans `validation.yaml` / `definitions.yaml`.

Sur les six mois XAUUSD 2024, les 135/136 flags proviennent tous du critère
ATR ; aucun rendement/amplitude ne dépasse 2 %. Les lignes plates inchangées
de la grille M1 font artificiellement décroître l'ATR pendant l'inactivité.
128 flags bid et 129 ask disparaissent quand son horloge est figée sur ces
lignes. Toutes les lignes et les anciens flags restent conservés ; aucune
interpolation de prix ni correction brute.

Les sept candidats restants par côté concernent les mêmes timestamps.
Deux correspondent aux dates/heures CPI/NFP du calendrier officiel ; les
autres comprennent de forts mouvements et des réouvertures. Ce ne sont pas
des corruptions démontrées. Les vérifier sur les ticks source avant exclusion.
Aucun timestamp manquant entre bid/ask ni spread de clôture négatif n'a été
observé. Les epochs alignés ne prouvent pas à eux seuls l'horloge fournisseur.

Les cinq exemples OHLC bruts, métriques et empreintes sont dans
`reports/data_readiness/anomalies/investigation.md` et `.json`.

```powershell
python tools/investigate_anomalies.py
python tools/audit_data.py --data data/historical --output reports/data_readiness/audit
```

## Import append-only

```powershell
python tools/fetch_dukascopy.py
```

`config/data_import.yaml` fixe XAUUSD bid/ask M1, début 2020-01-01,
format BI5, échelle des prix, endpoint, retries et concurrence. La limite est
la dernière minute UTC fermée au début du run. Aucune dépendance supplémentaire.
Les archives BI5 originales et les CSV décodés sont publiés atomiquement dans
`data/raw/xauusd/{bid,ask}/`. Les fichiers existants ne sont jamais remplacés.
Le manifeste JSONL est append-only, avec URL, SHA-256 des deux fichiers, date,
cutoff et statut. Une relance vérifie les checksums et reprend les fichiers
déjà téléchargés, y compris une archive dont le décodage a été interrompu.
Le verrou SQLite est libéré par le système en cas d'interruption.

Les snapshots intraday portent leur cutoff dans le nom ; la vue de lecture
choisit le plus récent sans supprimer les anciens. Les lignes sans source ne
sont jamais inventées. Un 404 est journalisé comme indisponible, pas comme une
fermeture de marché prouvée. La fin du hold-out, intervalle exclusif, correspond
à la clôture de la dernière bougie commune bid/ask importée. Aucun accès moteur.

Le script refuse un hold-out futur **avant tout téléchargement/écriture brute**.
Si aucune donnée ne dépasse son début, sa fin est `null` et tout accès moteur
au hold-out est bloqué. Les derniers imports tentés ont échoué côté fournisseur
(HTTP 429/503) : aucune bougie nouvelle téléchargée ; statut explicite dans
`reports/data_readiness/import/import_status.json`. Une relance n'autorise
aucun backtest ni approbation de préenregistrement.

## Calendrier news

`config/news_calendar.csv` contient 224 événements NFP/CPI/FOMC 2020–2026.
Chaque ligne cite le calendrier BLS ou le communiqué Federal Reserve ; les
heures ET sont converties avec `America/New_York`, y compris EST/EDT.
Les décisions extraordinaires FOMC de mars 2020 sont incluses ; les minutes
et déclarations de cadre général sont exclues. Les reports/annulations BLS
2025 sont reflétés : 11 publications NFP et 11 CPI, sans inventer les absentes.

Les dates futures sont scheduled, non réalisées. Les horaires des futurs
FOMC du 28 octobre et 9 décembre 2026 utilisent 14 h ET conventionnelles,
explicitement signalées comme à confirmer. Le modèle de coûts charge le CSV,
refuse UTC implicite/doublons/type inconnu/source absente, et applique la fenêtre
configurée. Le contenu du CSV fait partie du hash de préenregistrement.

Sources : [BLS](https://www.bls.gov/schedule/2026/home.htm),
[Federal Reserve](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm),
[Dukascopy](https://www.dukascopy.com/wiki/en/development/data-export/).

## Régimes mensuels

```powershell
python tools/audit_monthly_regimes.py --data data/raw --allow-holdout-market-audit
# Inspection limitée aux données existantes, tant que le téléchargement échoue :
python tools/audit_monthly_regimes.py --data data/historical --allow-holdout-market-audit
```

CSV/JSON dans `reports/data_readiness/regimes/` : ADX(14) D1 UTC, ses min/max/
moyennes et catégories par mois ; structure H4 HH+HL / LH+LL ou range.
Les paramètres sont dans `definitions.yaml`. Les pivots H4 ne deviennent
utilisables qu'après fermeture des barres de confirmation. Les labels sont
datés à la clôture. Le warmup utilise uniquement le passé, y compris avant
le début de la période examinée. Les D1 partiels du dimanche sont exclus ;
les H4 réellement cotés du dimanche sont conservés. Convention à valider.

Deux régimes doivent avoir des épisodes soutenus dans les deux mesures.
La présence observée et la couverture complète sont séparées : sur les
données actuelles, les deux régimes sont observés en dev, mais 54 mois dev
manquent et le hold-out est indisponible. Aucune conclusion complète possible.
Le journal d'accès marché est distinct du compteur de performance d'un setup.

À valider : règle ATR d'audit corrigée, sept candidats restants sur ticks,
calibration coûts, heures futures FOMC et convention D1 UTC/structure H4.
Le préenregistrement reste non approuvé. Aucun backtest effectué.
