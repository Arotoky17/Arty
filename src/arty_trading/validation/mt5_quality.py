"""Offline MT5 reconstructed ask quality checks; reference calibration forbidden."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from arty_trading.validation.csv_import import read_m1_csv


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_side(root, side):
    paths = sorted((root / "xauusd" / side).glob("*.csv"))
    if not paths:
        raise ValueError(f"Missing converted {side}")
    frame = pd.concat([pd.read_csv(path) for path in paths], ignore_index=True)
    if frame.timestamp.duplicated().any():
        raise ValueError("Duplicate quote timestamps")
    return frame.set_index("timestamp").sort_index(), paths


def stats(values):
    values = np.asarray(values, dtype=float)
    return {
        "count": len(values),
        "median": float(np.median(values)) if len(values) else None,
        "p95": float(np.quantile(values, 0.95)) if len(values) else None,
        "p99": float(np.quantile(values, 0.99)) if len(values) else None,
        "max": float(np.max(values)) if len(values) else None,
    }


def spread_audit(spreads, outlier_usd=5.0):
    utc = pd.to_datetime(spreads.index, unit="ms", utc=True)
    frame = pd.DataFrame({"spread_usd": spreads.to_numpy()}, index=utc)
    yearly = {str(year): stats(group.spread_usd) for year, group in frame.groupby(frame.index.year)}
    hourly = {str(hour): stats(group.spread_usd) for hour, group in frame.groupby(frame.index.hour)}
    year_hour = {
        f"{year}-{hour:02d}": stats(group.spread_usd)
        for (year, hour), group in frame.groupby([frame.index.year, frame.index.hour])
    }
    baseline = frame.groupby(frame.index.year).spread_usd.transform("median")
    outliers = (frame.spread_usd > outlier_usd) | (frame.spread_usd > 10 * baseline)
    zero = frame.spread_usd == 0
    constant_days = []
    for day, group in frame.groupby(frame.index.normalize()):
        frequencies = group.spread_usd.round(8).value_counts()
        if len(group) >= 300 and frequencies.iloc[0] / len(group) >= 0.95:
            constant_days.append(
                {
                    "day_utc": day.isoformat(),
                    "rows": len(group),
                    "dominant_spread_usd": float(frequencies.index[0]),
                    "dominant_fraction": float(frequencies.iloc[0] / len(group)),
                }
            )
    values = frame.spread_usd.round(8).to_numpy()
    timestamps = frame.index.as_unit("ms").asi8
    breaks = np.flatnonzero((values[1:] != values[:-1]) | (np.diff(timestamps) != 60_000)) + 1
    long_runs = []
    starts = np.r_[0, breaks]
    ends = np.r_[breaks, len(values)]
    for start, end in zip(starts[ends - starts >= 240], ends[ends - starts >= 240], strict=True):
        long_runs.append(
            {
                "start_utc": frame.index[start].isoformat(),
                "end_exclusive": (frame.index[end - 1] + pd.Timedelta(minutes=1)).isoformat(),
                "minutes": int(end - start),
                "spread_usd": float(values[start]),
            }
        )
    examples = frame.loc[outliers].head(100)
    report = {
        "unit": "USD per ounce; MT5 Spread points * 0.01",
        "ask_origin": "reconstructed_ask",
        "allowed_for_reference_cost_model": False,
        "yearly": yearly,
        "hourly_utc": hourly,
        "year_hour_utc": year_hour,
        "zero_spread_bars": int(zero.sum()),
        "outliers": {
            "count": int(outliers.sum()),
            "absolute_threshold_usd": outlier_usd,
            "relative_threshold": "greater than 10 times the annual median",
            "action": "flag only, no removal or replacement",
            "examples": [
                {"utc": index.isoformat(), "spread_usd": float(row.spread_usd)}
                for index, row in examples.iterrows()
            ],
        },
        "constant_days_95_percent": constant_days,
        "constant_runs_240_minutes": long_runs,
        "constancy_caveat": "Flags may reflect broker export mechanics; not proof of manipulation",
    }
    return report, frame.assign(outlier=outliers, zero_spread=zero)


def reconstruct(source, root, output, point=0.01):
    raw = Path("data/raw").resolve()
    if root.resolve() == raw or raw in root.resolve().parents:
        raise ValueError("MT5 reconstructed ask must remain separate from data/raw")
    current = json.loads((root / "provenance.json").read_text())
    if current.get("provider") != "mt5_csv" or current.get("server_timezone") != "Europe/Athens":
        raise ValueError("Expected a separated MT5 Europe/Athens conversion")
    if point != 0.01:
        raise ValueError("This specification snapshot confirms point 0.01 only")
    with source.open(encoding="utf-8-sig") as stream:
        separator = csv.Sniffer().sniff(stream.read(4096), delimiters=",;\t").delimiter
    original = pd.read_csv(source, sep=separator)
    original.columns = [name.strip("<>").upper() for name in original.columns]
    local = pd.DatetimeIndex(
        pd.to_datetime(original.DATE + " " + original.TIME, format="%Y.%m.%d %H:%M:%S")
    )
    utc = local.tz_localize("Europe/Athens", ambiguous="NaT", nonexistent="NaT").tz_convert("UTC")
    valid = ~utc.isna()
    spread_values = pd.to_numeric(original.SPREAD, errors="raise").to_numpy(dtype=float)
    if not np.isfinite(spread_values).all() or (spread_values < 0).any():
        raise ValueError("Invalid original Spread")
    spreads = pd.Series(spread_values[valid] * point, index=utc[valid].as_unit("ms").asi8)
    if spreads.index.duplicated().any():
        raise ValueError("Ambiguous duplicate MT5 source timestamps")
    bid, paths = read_side(root, "bid")
    spreads = spreads.reindex(bid.index)
    if spreads.isna().any():
        raise ValueError("Converted bid has no corresponding MT5 source Spread")
    output.mkdir(parents=True, exist_ok=True)
    entries = [
        json.loads(line) for line in (root / "conversion_manifest.jsonl").read_text().splitlines()
    ]
    bid_entries = [entry for entry in entries if entry["side"] == "bid"]
    expected = {str(Path(entry["path"]).resolve()): entry["sha256"] for entry in bid_entries}
    for path in paths:
        if expected.get(str(path.resolve())) != digest(path):
            raise ValueError(f"Bid manifest checksum mismatch: {path}")
    asks = []
    for path in paths:
        day = pd.read_csv(path)
        shifts = spreads.reindex(day.timestamp).to_numpy()
        for name in ("open", "high", "low", "close"):
            day[name] = day[name] + shifts
        payload, info = read_m1_csv(
            day.to_csv(index=False, lineterminator="\n").encode(), path.stem
        )
        target = root / "xauusd/ask" / path.name
        if target.exists() and target.read_bytes() != payload:
            raise ValueError(f"Conflicting existing ask: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        asks.append(
            {
                "date": path.stem,
                "side": "ask",
                "path": str(target),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "rows": info["rows"],
                "provider": "mt5_csv",
                "quote_origin": "reconstructed_ask",
                "ask_origin": "reconstructed_ask",
                "point": "0.01",
                "status": "converted",
                "server_timezone": "Europe/Athens",
                "allowed_for_reference_cost_model": False,
                "separated_from_dukascopy": True,
            }
        )
    for entry in bid_entries:
        entry.update({"point": "0.01", "ask_origin": "reconstructed_ask"})
    with (root / "conversion_manifest.jsonl").open("w", encoding="utf-8") as stream:
        for entry in bid_entries + asks:
            stream.write(json.dumps(entry) + "\n")
    provenance = json.loads((root / "provenance.json").read_text())
    provenance.update(
        {
            "point": "0.01",
            "ask_origin": "reconstructed_ask",
            "bid_only": False,
            "ask_reconstruction": "bid OHLC + original bar Spread * 0.01; constant within bar",
            "allowed_for_reference_cost_model": False,
            "broker_specification": {
                "contract_oz": 100,
                "volume_min_lot": 0.01,
                "volume_step_lot": 0.01,
                "source": "User-confirmed MetaQuotes-Demo Specification 2026-10-05",
                "real_broker_specification_confirmed": False,
            },
        }
    )
    (root / "provenance.json").write_text(json.dumps(provenance, indent=2))
    assert all(digest(path) == expected[str(path.resolve())] for path in paths)
    report, flags = spread_audit(spreads)
    report.update(
        {
            "bid_files_unchanged": len(paths),
            "ask_files_written": len(asks),
            "bars": len(spreads),
            "source_sha256": digest(source),
            "point": point,
            "ohlc_bid_ask_aligned": True,
            "nonnegative_spreads": True,
            "network": False,
            "backtest_run": False,
        }
    )
    (output / "spread_quality.json").write_text(json.dumps(report, indent=2))
    flags.loc[flags.outlier | flags.zero_spread].to_csv(
        output / "spread_flags.csv", index_label="utc"
    )
    return bid, spreads, report


def compare_duka(bid, spreads, raw_root):
    paths = sorted((raw_root / "xauusd/bid/m1").glob("2020-*.csv"))
    parts = []
    sources = []
    missing_ask = []
    for path in paths:
        left = pd.read_csv(path, usecols=["timestamp", "close"]).set_index("timestamp")
        ask_path = raw_root / "xauusd/ask/m1" / path.name
        if not ask_path.exists():
            missing_ask.append(path.name)
            left["duka_spread"] = np.nan
        else:
            right = pd.read_csv(ask_path, usecols=["timestamp", "close"]).set_index("timestamp")
            left["duka_spread"] = right.close.reindex(left.index) - left.close
            sources.append({"path": str(ask_path), "sha256": digest(ask_path)})
        parts.append(left)
        sources.append({"path": str(path), "sha256": digest(path)})
    if not parts:
        return {"status": "no_local_dukascopy"}
    duka = pd.concat(parts)
    common = bid.index.intersection(duka.index)
    joined = pd.DataFrame(
        {
            "mt5_bid": bid.close.reindex(common),
            "duka_bid": duka.close.reindex(common),
            "mt5_spread": spreads.reindex(common),
            "duka_spread": duka.duka_spread.reindex(common),
        }
    )
    joined.index = pd.to_datetime(joined.index, unit="ms", utc=True)

    def summary(frame):
        valid = frame.dropna(subset=["duka_spread"])
        return {
            "bid_abs_price_diff_usd": stats(abs(frame.mt5_bid - frame.duka_bid)),
            "bid_signed_price_diff_usd": stats(frame.mt5_bid - frame.duka_bid),
            "mt5_spread_usd": stats(valid.mt5_spread),
            "duka_spread_usd": stats(valid.duka_spread),
            "spread_signed_diff_mt5_minus_duka": stats(valid.mt5_spread - valid.duka_spread),
            "duka_negative_spread_pairs": int((valid.duka_spread < 0).sum()),
            "spread_caveat": "MT5 bar spread vs Dukascopy M1 close ask-bid, not synchronous ticks",
        }

    return {
        "matched_bid_minutes": len(joined),
        "first_common_utc": joined.index.min().isoformat(),
        "last_common_utc": joined.index.max().isoformat(),
        "overall": summary(joined),
        "monthly": {
            str(month): summary(group)
            for month, group in joined.groupby(joined.index.tz_localize(None).to_period("M"))
        },
        "missing_duka_ask_files": missing_ask,
        "source_hashes": sources,
        "ask_origin": "reconstructed_ask",
        "reference_calibration": False,
        "network": False,
        "backtest_run": False,
    }
