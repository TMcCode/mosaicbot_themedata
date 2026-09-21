#!/usr/bin/env python3
"""Merge alt_data_map.csv (paid commercial map) into config/datasets.json.

Keeps all non-paid rows (free pulls, free map_only, scrapes). Replaces existing
license=paid map_only entries with CSV-derived rows.

Usage:
  python scripts/import_alt_data_map.py
  python scripts/import_alt_data_map.py --csv alt_data_map.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CSV = _ROOT / "alt_data_map.csv"
_DATASETS = _ROOT / "config" / "datasets.json"

_PAID_NOTES = (
    "Paid map — from alt_data_map.csv; do not pull/publish until redistribution license."
)


def _slug(s: str, max_len: int = 48) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")
    return (out or "x")[:max_len]


def _row_from_csv(r: dict) -> dict | None:
    provider = (r.get("Provider") or "").strip()
    if not provider or provider.startswith("http"):
        return None
    sector = (r.get("Sector") or "").strip()
    aliases = (r.get("Included brands / aliases") or "").strip()
    canonical = (r.get("Canonical data type") or "").strip()
    data_type = (r.get("Data type") or "").strip() or canonical
    if not data_type:
        return None
    region = (r.get("Source region") or "").strip()
    panel = (r.get("Panel / tracked universe") or "").strip()
    provider_desc = (r.get("Provider description") or "").strip()
    dataset_desc = (r.get("Dataset description") or "").strip()
    bucket = (r.get("") or "").strip() or "AltData"

    base = f"{_slug(provider)}_{_slug(data_type)}"
    desc = dataset_desc or f"{provider}: {data_type}"
    if aliases:
        desc = f"{desc} (aliases: {aliases})"

    return {
        "id": base,
        "license": "paid",
        "status": "map_only",
        "access": "file",
        "cadence": "irregular",
        "sector": sector or "unknown",
        "canonical_data_type": canonical or None,
        "data_type": data_type,
        "region": region or "unspecified",
        "description": desc,
        "provider": provider,
        "aliases": aliases or None,
        "panel": panel or None,
        "provider_description": provider_desc or None,
        "source": _slug(provider, 32),
        "pack": None,
        "series_keys": [],
        "as_of_semantics": "vendor_as_of",
        "map_bucket": bucket,
        "notes": _PAID_NOTES,
        "_disambiguate": f"{sector}|{canonical}|{dataset_desc[:80]}",
    }


def _unique_ids(rows: list[dict]) -> list[dict]:
    seen: dict[str, int] = {}
    out: list[dict] = []
    for row in rows:
        disamb = row.pop("_disambiguate", "")
        rid = row["id"]
        if rid not in seen:
            seen[rid] = 1
            out.append(row)
            continue
        seen[rid] += 1
        # Prefer sector suffix, else numeric
        sector_slug = _slug(str(row.get("sector") or ""), 24)
        candidate = f"{rid}_{sector_slug}" if sector_slug else f"{rid}_{seen[rid]}"
        if candidate in seen:
            candidate = f"{rid}_{seen[rid]}"
        # still collide → hash-ish short
        n = 2
        while candidate in seen:
            candidate = f"{rid}_{n}"
            n += 1
        seen[candidate] = 1
        row["id"] = candidate
        if disamb:
            row["notes"] = f"{_PAID_NOTES} Disambiguated duplicate provider+data_type."
        out.append(row)
    return out


def import_map(csv_path: Path, datasets_path: Path) -> dict:
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        raw = list(csv.DictReader(f))
    paid = _unique_ids([r for r in (_row_from_csv(x) for x in raw) if r])

    doc = json.loads(datasets_path.read_text(encoding="utf-8"))
    keep = [
        d
        for d in (doc.get("datasets") or [])
        if not (d.get("license") == "paid" and d.get("status") == "map_only")
    ]
    # Drop null optional fields for cleaner JSON
    cleaned_paid = []
    for row in paid:
        cleaned = {k: v for k, v in row.items() if v is not None and v != ""}
        cleaned_paid.append(cleaned)

    doc["datasets"] = keep + cleaned_paid
    doc["description"] = (
        "Global dataset registry for mosaicbot_themedata. Includes free API/scrape pulls "
        "and paid map-only commercial data (alt_data_map.csv). Theme/ticker wiring lives in "
        "series_attachments.json — not here. Paid rows stay status=map_only until a "
        "redistribution license exists."
    )
    fields = doc.setdefault("fields", {})
    fields.update(
        {
            "provider": "Vendor / product brand (paid map)",
            "aliases": "Included brands / aliases from paid map",
            "canonical_data_type": "High-level type bucket from paid map",
            "panel": "Panel / tracked universe note",
            "provider_description": "Vendor blurb",
            "map_bucket": "Source sheet bucket (e.g. AltData)",
        }
    )
    datasets_path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "kept": len(keep),
        "paid": len(cleaned_paid),
        "total": len(doc["datasets"]),
        "csv_rows": len(raw),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, default=_DEFAULT_CSV)
    ap.add_argument("--datasets", type=Path, default=_DATASETS)
    args = ap.parse_args()
    if not args.csv.is_file():
        raise SystemExit(f"CSV not found: {args.csv}")
    stats = import_map(args.csv, args.datasets)
    print(
        f"Wrote {args.datasets}: kept {stats['kept']} non-paid, "
        f"imported {stats['paid']} paid map_only "
        f"(from {stats['csv_rows']} CSV rows) → total {stats['total']}"
    )


if __name__ == "__main__":
    main()
