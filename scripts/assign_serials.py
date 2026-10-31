#!/usr/bin/env python3
"""Assign frozen chart serials to datasets (+ optional catalog / pack stamp).

Idempotent: existing ``serial`` / ``serial_*`` fields are kept; only new
datasets get the next free ``dataset_n`` under ``(source, family)``.

Usage:
  python scripts/assign_serials.py
  python scripts/assign_serials.py --pack lab_economics
  python scripts/assign_serials.py --pack lab_economics --stamp-latest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib import serials as ser  # noqa: E402

DATASETS_PATH = ROOT / "config" / "datasets.json"
REGISTRY_PATH = ROOT / "config" / "serial_registry.json"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _existing_assignment(row: dict[str, Any]) -> dict[str, Any] | None:
    parsed = ser.parse_serial(row.get("serial"))
    if parsed:
        return parsed
    src = row.get("serial_source")
    fam = row.get("serial_family")
    dn = row.get("serial_dataset_n")
    sn = row.get("serial_series_n")
    if src and fam is not None and dn is not None:
        return {
            "source": ser.slug_token(str(src)),
            "family": ser.slug_token(str(fam), fallback=ser.FAMILY_FLAT),
            "dataset_n": int(dn),
            "series_n": int(sn or 1),
            "serial": ser.format_serial(str(src), str(fam), int(dn), int(sn or 1)),
        }
    return None


def catalog_source_hints() -> dict[str, str]:
    """dataset_id → source from pack catalogs (fills sparse datasets.json rows)."""
    hints: dict[str, str] = {}
    for path in sorted((ROOT / "config").glob("*_catalog.json")):
        try:
            cat = _load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        for row in cat.get("series") or []:
            did = str(row.get("dataset_id") or row.get("key") or "").strip()
            src = str(row.get("source") or "").strip()
            if did and src and did not in hints:
                hints[did] = src
    return hints


def enrich_missing_sources(datasets: list[dict[str, Any]]) -> int:
    hints = catalog_source_hints()
    n = 0
    for row in datasets:
        if row.get("source"):
            continue
        did = str(row.get("id") or "").strip()
        if did and did in hints:
            row["source"] = hints[did]
            n += 1
    return n


def _row_source_family(row: dict[str, Any]) -> tuple[str, str]:
    return ser.split_source_family(
        row.get("source"),
        provider=row.get("provider"),
        dataset_id=row.get("id"),
        description=row.get("description"),
        name=row.get("name"),
    )


def assign_datasets(datasets: list[dict[str, Any]]) -> dict[str, Any]:
    """Stamp serials onto dataset rows; return registry payload."""
    counters: dict[str, int] = {}
    # Seed counters from existing assignments so we never reuse numbers.
    for row in datasets:
        existing = _existing_assignment(row)
        if not existing:
            continue
        ck = ser.counter_key(existing["source"], existing["family"])
        counters[ck] = max(counters.get(ck, 0), int(existing["dataset_n"]))

    # Stable order for first-time assigns.
    indexed = list(enumerate(datasets))
    indexed.sort(
        key=lambda pair: (
            _row_source_family(pair[1]),
            str(pair[1].get("id") or ""),
            pair[0],
        )
    )

    by_dataset: dict[str, Any] = {}
    by_series: dict[str, str] = {}

    for _i, row in indexed:
        did = str(row.get("id") or "").strip()
        if not did:
            continue

        source, family = _row_source_family(row)
        existing = _existing_assignment(row)
        if existing and existing["source"] == source and existing["family"] == family:
            dataset_n = int(existing["dataset_n"])
        else:
            # Re-source/family change or new row → next free under new bucket.
            ck = ser.counter_key(source, family)
            counters[ck] = counters.get(ck, 0) + 1
            dataset_n = counters[ck]
            # Keep counter max in sync when preserving mismatched? handled above.

        # Ensure counter reflects this n even when preserved.
        ck = ser.counter_key(source, family)
        counters[ck] = max(counters.get(ck, 0), dataset_n)

        series_keys = [
            str(k).strip()
            for k in (row.get("series_keys") or [])
            if str(k).strip()
        ]
        if not series_keys:
            series_keys = [did]

        series_entries: list[dict[str, Any]] = []
        for si, sk in enumerate(series_keys, start=1):
            parts = ser.serial_parts_dict(
                source=source,
                family=family,
                dataset_n=dataset_n,
                series_n=si,
            )
            series_entries.append({"series_key": sk, **parts})
            # Prefer dataset_id == series_key as the canonical owner when
            # multiple datasets incorrectly list the same series_key.
            if sk == did or sk not in by_series:
                by_series[sk] = parts["serial"]

        primary = series_entries[0]
        row["serial"] = primary["serial"]
        row["serial_source"] = primary["serial_source"]
        row["serial_family"] = primary["serial_family"]
        row["serial_dataset_n"] = primary["serial_dataset_n"]
        row["serial_series_n"] = primary["serial_series_n"]

        by_dataset[did] = {
            "serial": primary["serial"],
            "source": primary["serial_source"],
            "family": primary["serial_family"],
            "dataset_n": primary["serial_dataset_n"],
            "series_keys": series_keys,
            "series": [
                {
                    "series_key": e["series_key"],
                    "serial": e["serial"],
                    "series_n": e["serial_series_n"],
                }
                for e in series_entries
            ],
        }

    return {
        "schema_version": 0,
        "description": (
            "Frozen chart serial crosswalk. Format: "
            "{source}-{family}-{dataset:04d}-{series:04d}. "
            "family=0000 means flat (no mid-node). "
            "Never renumber; append only. Boards/attachments keep using series_key."
        ),
        "format": "{source}-{family}-{dataset:04d}-{series:04d}",
        "family_flat_token": ser.FAMILY_FLAT,
        "source_family_rules": {
            k: {"source": v[0], "family": v[1] or ser.FAMILY_FLAT}
            for k, v in sorted(ser.SOURCE_FAMILY_RULES.items())
        },
        "counters": {k: counters[k] for k in sorted(counters)},
        "by_dataset_id": dict(sorted(by_dataset.items())),
        "by_series_key": dict(sorted(by_series.items())),
    }


def _serial_for_series(
    *,
    series_key: str,
    dataset_id: str | None,
    by_series: dict[str, str],
    by_dataset: dict[str, Any],
) -> str | None:
    """Resolve serial preferring dataset ownership over shared series_key aliases."""
    did = (dataset_id or series_key or "").strip()
    if did and did in by_dataset:
        for entry in by_dataset[did].get("series") or []:
            if entry.get("series_key") == series_key:
                return entry.get("serial")
        # dataset exists but key not listed — use primary dataset serial
        primary = by_dataset[did].get("serial")
        if primary:
            return primary
    if series_key in by_series:
        return by_series[series_key]
    if did in by_series:
        return by_series[did]
    return None


def stamp_catalog(
    pack: str,
    by_series: dict[str, str],
    by_dataset: dict[str, Any],
) -> Path | None:
    catalog_path = ROOT / "config" / f"{pack}_catalog.json"
    if not catalog_path.exists():
        print(f"catalog not found: {catalog_path}")
        return None
    cat = _load_json(catalog_path)
    n = 0
    for row in cat.get("series") or []:
        key = row.get("key")
        if not key:
            continue
        serial = _serial_for_series(
            series_key=str(key),
            dataset_id=row.get("dataset_id"),
            by_series=by_series,
            by_dataset=by_dataset,
        )
        if not serial:
            continue
        parts = ser.parse_serial(serial)
        if not parts:
            continue
        row["serial"] = parts["serial"]
        row["serial_source"] = parts["source"]
        row["serial_family"] = parts["family"]
        row["serial_dataset_n"] = parts["dataset_n"]
        row["serial_series_n"] = parts["series_n"]
        n += 1
    _dump_json(catalog_path, cat)
    print(f"stamped {n} series on {catalog_path.name}")
    return catalog_path


def list_packs() -> list[str]:
    """Pack ids from ``config/*_catalog.json`` filenames."""
    packs: list[str] = []
    for path in sorted((ROOT / "config").glob("*_catalog.json")):
        name = path.name[: -len("_catalog.json")]
        if name:
            packs.append(name)
    return packs


def stamp_latest(
    pack: str,
    by_series: dict[str, str],
    by_dataset: dict[str, Any],
    *,
    quiet_missing: bool = False,
) -> Path | None:
    latest_path = ROOT / "data" / pack / "latest.json"
    if not latest_path.exists():
        if not quiet_missing:
            print(f"latest.json not found: {latest_path}")
        return None
    latest = _load_json(latest_path)
    series_map = latest.get("series") or {}
    n = 0
    for key, obj in series_map.items():
        if not isinstance(obj, dict):
            continue
        serial = _serial_for_series(
            series_key=str(key),
            dataset_id=obj.get("dataset_id"),
            by_series=by_series,
            by_dataset=by_dataset,
        )
        if not serial:
            continue
        parts = ser.parse_serial(serial)
        if not parts:
            continue
        obj["serial"] = parts["serial"]
        obj["serial_source"] = parts["source"]
        obj["serial_family"] = parts["family"]
        obj["serial_dataset_n"] = parts["dataset_n"]
        obj["serial_series_n"] = parts["series_n"]
        n += 1
    # catalog mirror inside latest if present
    for row in (latest.get("catalog") or {}).get("series") or []:
        key = row.get("key")
        if not key:
            continue
        serial = _serial_for_series(
            series_key=str(key),
            dataset_id=row.get("dataset_id") or key,
            by_series=by_series,
            by_dataset=by_dataset,
        )
        if serial:
            row["serial"] = serial
    _dump_json(latest_path, latest)
    print(f"stamped {n} series on {latest_path}")
    return latest_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--pack",
        help="Also stamp config/<pack>_catalog.json from the registry",
    )
    ap.add_argument(
        "--all-packs",
        action="store_true",
        help="Stamp every config/*_catalog.json (and latest.json when --stamp-latest)",
    )
    ap.add_argument(
        "--stamp-latest",
        action="store_true",
        help="Also stamp data/<pack>/latest.json (with --pack or --all-packs)",
    )
    args = ap.parse_args()
    if args.pack and args.all_packs:
        print("use --pack or --all-packs, not both", file=sys.stderr)
        return 2

    payload = _load_json(DATASETS_PATH)
    datasets = payload.get("datasets")
    if not isinstance(datasets, list):
        print("datasets.json missing datasets[]", file=sys.stderr)
        return 1

    filled = enrich_missing_sources(datasets)
    if filled:
        print(f"filled source on {filled} datasets from pack catalogs")

    # Document serial fields once.
    fields = payload.setdefault("fields", {})
    fields["serial"] = (
        "Frozen chart id {source}-{family}-{dataset:04d}-{series:04d}; "
        "see config/serial_registry.json"
    )
    fields["serial_source"] = "Canonical source slug for serial"
    fields["serial_family"] = "Family slug or 0000 when flat"
    fields["serial_dataset_n"] = "Dataset ordinal within (source, family)"
    fields["serial_series_n"] = "Series leaf within dataset (usually 0001)"

    registry = assign_datasets(datasets)
    _dump_json(DATASETS_PATH, payload)
    _dump_json(REGISTRY_PATH, registry)

    n_ds = len(registry["by_dataset_id"])
    n_ser = len(registry["by_series_key"])
    print(f"wrote {DATASETS_PATH.relative_to(ROOT)} ({n_ds} datasets)")
    print(f"wrote {REGISTRY_PATH.relative_to(ROOT)} ({n_ser} series keys)")

    packs: list[str] = []
    if args.all_packs:
        packs = list_packs()
    elif args.pack:
        packs = [args.pack]
    elif args.stamp_latest:
        print("--stamp-latest requires --pack or --all-packs", file=sys.stderr)
        return 2

    cat_ok = 0
    latest_ok = 0
    for pack in packs:
        if stamp_catalog(
            pack,
            registry["by_series_key"],
            registry["by_dataset_id"],
        ):
            cat_ok += 1
        if args.stamp_latest and stamp_latest(
            pack,
            registry["by_series_key"],
            registry["by_dataset_id"],
            quiet_missing=bool(args.all_packs),
        ):
            latest_ok += 1

    if packs:
        print(
            f"stamped catalogs: {cat_ok}/{len(packs)}"
            + (f"; latest.json: {latest_ok}" if args.stamp_latest else "")
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
