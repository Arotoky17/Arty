"""Render local MT5 quality reports, without changing quotes or configuration."""

import csv
import json
from pathlib import Path

import pandas as pd


def render(output):
    spread = json.loads((output / "spread_quality.json").read_text())
    comparison = json.loads((output / "dukascopy_spread_comparison.json").read_text())
    gaps = json.loads((output / "gap_classification.json").read_text())
    flags = pd.read_csv(output / "spread_flags.csv")
    dates = pd.to_datetime(flags.utc, utc=True)
    spread["flag_distribution"] = {}
    for kind in ("outlier", "zero_spread"):
        mask = flags[kind].to_numpy(dtype=bool)
        spread["flag_distribution"][kind] = {
            "year": {
                str(year): int((mask & (dates.dt.year.to_numpy() == year)).sum())
                for year in sorted(dates.dt.year.unique())
            },
            "hour_utc": {
                str(hour): int((mask & (dates.dt.hour.to_numpy() == hour)).sum())
                for hour in range(24)
            },
        }
    (output / "spread_quality.json").write_text(json.dumps(spread, indent=2))
    with (output / "spread_by_year_hour_utc.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "year",
                "hour_utc",
                "count",
                "median_usd_per_oz",
                "p95_usd_per_oz",
                "max_usd_per_oz",
            ],
        )
        writer.writeheader()
        for key, row in sorted(spread["year_hour_utc"].items()):
            year, hour = key.split("-")
            writer.writerow(
                {
                    "year": year,
                    "hour_utc": hour,
                    "count": row["count"],
                    "median_usd_per_oz": row["median"],
                    "p95_usd_per_oz": row["p95"],
                    "max_usd_per_oz": row["max"],
                }
            )
    with (output / "gap_by_year_hour_utc.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=["classification", "year", "hour_utc", "missing_minutes"]
        )
        writer.writeheader()
        for kind, distribution in gaps["distribution_minutes"].items():
            for key, value in sorted(distribution["year_hour_utc"].items()):
                year, hour = key.split("-")
                writer.writerow(
                    {
                        "classification": kind,
                        "year": year,
                        "hour_utc": hour,
                        "missing_minutes": value,
                    }
                )
    body = """# Audit MT5 : ask reconstruit, spreads et trous

Ask reconstruit avec point 0.01 sur 2375973 barres, 2102 fichiers UTC.
Bid et data/raw inchanges. Aucun prix corrige, supprime ou impute.

## Spreads USD par once

| Annee | Mediane | P95 | Maximum |
|---|---:|---:|---:|
"""
    for year, row in spread["yearly"].items():
        body += f"| {year} | {row['median']:.3f} | {row['p95']:.3f} | {row['max']:.3f} |\n"
    body += f"""
Detail par heure UTC et annee : spread_by_year_hour_utc.csv et spread_quality.json.
{spread["zero_spread_bars"]} spreads nuls; {spread["outliers"]["count"]} flags
(spread >5 USD/oz OU >10 fois la mediane annuelle).
{len(spread["constant_days_95_percent"])} jours a >=95% de spread identique;
{len(spread["constant_runs_240_minutes"])} runs constants >=240 minutes consecutives.
Les flags ne prouvent pas une erreur et ne sont pas automatiquement effaces.
Ils peuvent refleter les mecanismes d'export; leur origine reste a documenter.

## Comparaison des sources locales

{comparison["matched_bid_minutes"]} minutes communes, du {comparison["first_common_utc"]}
au {comparison["last_common_utc"]}.
"""
    overall = comparison["overall"]
    body += f"""
Clotures bid : ecart absolu median {overall["bid_abs_price_diff_usd"]["median"]:.3f},
P95 {overall["bid_abs_price_diff_usd"]["p95"]:.3f} USD/oz.
Ecart signe median MT5-Dukascopy : {overall["bid_signed_price_diff_usd"]["median"]:.3f}.
Spread MT5 median {overall["mt5_spread_usd"]["median"]:.3f},
P95 {overall["mt5_spread_usd"]["p95"]:.3f};
Dukascopy median {overall["duka_spread_usd"]["median"]:.3f},
P95 {overall["duka_spread_usd"]["p95"]:.3f}.
Difference signee mediane des spreads MT5-Dukascopy :
{overall["spread_signed_diff_mt5_minus_duka"]["median"]:.3f} USD/oz.
Il s'agit de Spread de barre MT5 et de close ask-bid Dukascopy,
pas de ticks synchrones. Detail mensuel et SHA : dukascopy_spread_comparison.json.
Aucun cout de reference calibre sur MT5.

## Trous

| Classe | Intervalles | Minutes absentes |
|---|---:|---:|
"""
    for kind, row in gaps["totals"].items():
        body += f"| {kind} | {row['intervals']} | {row['missing_minutes']} |\n"
    body += """
Detail par annee/heure : gap_by_year_hour_utc.csv; intervalles : gap_intervals.csv.
Les fermetures attendues utilisent les exclusions prudentes du calendrier,
pas des horaires historiques du broker attestes. La classe courte 2-15 minutes
est distincte des minutes isolees et des gros trous strictement >15 minutes.

Le masque gap_policy.json marque les gros trous non_tradable pour ATR,
detecteurs et fills. Les vues derivees de resampling bloquent les barres qui
les recouvrent, sans changer OHLC. gap_segment_id et current_gap_segment
isolent des historiques causaux apres trou; flags de bougie conserves par le
lecteur et checksum du masque verifie. Le branchement reset/cancel dans les
consommateurs du moteur de baseline reste a verifier avant execution : les
marqueurs ne constituent pas seuls une integration moteur terminee.

## Amendement et blocages

Brouillon non applique : preregistration_mt5_primary_amendment.md et
proposals/mt5_primary_amendment.json. Confirmation distincte :
preregistration_mt5_confirmation_trial.md.

Restent bloquants : validation de la source principale/fin hold-out/nouveau SHA;
broker reel et couts conservateurs documentes; decision x2 bloquant ou non;
traitement des flags spread et trous courts; audit imparfait accepte avec
exclusions; revue visuelle/precision Setup 1; lecteur MT5/segments/reset/cancel
branches et testes; modele effectif de cout sans double comptage et adaptation
du gate actuel apres approbation; source/dates/couts/budget du trial de confirmation.
La fin exclusive proposee est 2026-10-05T11:11:00Z, sans gel applique.

Aucun backtest/reseau. Document principal, SHA et split.yaml inchanges.
2015-2019 absent, hors du dev actuellement fixe 2020-2024.
"""
    (output / "review.md").write_text(body, encoding="utf-8")


if __name__ == "__main__":
    render(Path("reports/data_readiness/mt5_csv/quality"))
