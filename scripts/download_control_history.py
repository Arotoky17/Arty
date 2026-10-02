"""Fetch pinned public Dukascopy CSV mirror; record every input checksum."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any
from urllib.request import urlopen

REVISION = "1d8e4c008fe177be455f6de05f5057ebdde901ae"
REPOSITORY = "3650326613-png/dukascopy_xauusd_1m_data"
BASE = f"https://raw.githubusercontent.com/{REPOSITORY}/{REVISION}/"


def download(relative: str, expected_hashes: dict[str, str]) -> dict[str, Any]:
    path = Path("data/historical") / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    data = path.read_bytes() if path.exists() else b""
    if hashlib.sha256(data).hexdigest() != expected_hashes.get(path.as_posix()):
        with urlopen(BASE + relative, timeout=45) as response:
            data = response.read()
        path.write_bytes(data)
    return {
        "path": path.as_posix(),
        "url": BASE + relative,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }


def main() -> None:
    files = [
        f"xauusd/{side}/m1/xauusd_{side}_m1_2024_{month:02d}.csv"
        for side in ("bid", "ask")
        for month in range(1, 7)
    ]
    manifest_path = Path("data/historical/source_manifest.json")
    existing = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )
    hashes = (
        {row["path"]: row["sha256"] for row in existing.get("files", [])}
        if existing.get("revision") == REVISION
        else {}
    )
    with ThreadPoolExecutor(max_workers=3) as executor:
        entries = list(executor.map(partial(download, expected_hashes=hashes), files))
    manifest = {
        "provider": "Dukascopy",
        "mirror": f"https://github.com/{REPOSITORY}",
        "revision": REVISION,
        "files": entries,
    }
    Path("data/historical/source_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"Downloaded {len(entries)} files, {sum(row['bytes'] for row in entries)} bytes")


if __name__ == "__main__":
    main()
