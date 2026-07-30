#!/usr/bin/env python3
"""Fetch Census C-30 construction spending via FRED (manufacturing, highway, …).

No HTML scrape — FRED mirrors Census Value Put in Place (SAAR, $ millions).
Run monthly after the Census release (usually ~first business week).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

SCHEMA_VERSION = 0
_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUT = _ROOT / "data" / "census"
UA = "mosaicbot_themedata/0.1 (+census_construction)"

# FRED series id → local key / label
SERIES = {
    "TLMFGCONS": {
        "key": "manufacturing",
        "label": "Total construction spending: Manufacturing",
        "unit": "millions_usd_saar",
    },
    "TLHWYCONS": {
        "key": "highway_and_street",
        "label": "Total construction spending: Highway and street",
        "unit": "millions_usd_saar",
    },
    "TTLCONS": {
        "key": "total",
        "label": "Total construction spending",
        "unit": "millions_usd_saar",
    },
}


def _fred_csv(series_id: str) -> list[tuple[str, float]]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=60) as resp:
        raw = resp.read()
    if raw[:15].lower().startswith(b"<!doctype") or b"<html" in raw[:200].lower():
        raise RuntimeError(f"FRED returned HTML for {series_id} (missing/blocked)")
    rows: list[tuple[str, float]] = []
    for row in csv.DictReader(io.StringIO(raw.decode())):
        d = row.get("observation_date") or row.get("DATE")
        v = row.get(series_id)
        if not d or v in (None, "", "."):
            continue
        rows.append((d, float(v)))
    return rows


def build_payload(start: str = "2015-01-01") -> dict:
    fetched_at = datetime.now(timezone.utc).isoformat()
    series_out: dict = {}
    latest_date = ""
    for sid, meta in SERIES.items():
        pts = [{"date": d, "value": v} for d, v in _fred_csv(sid) if d >= start]
        if not pts:
            raise RuntimeError(f"no points for {sid}")
        series_out[meta["key"]] = {
            "fred_id": sid,
            "label": meta["label"],
            "unit": meta["unit"],
            "points": pts,
            "latest": pts[-1],
        }
        latest_date = max(latest_date, pts[-1]["date"])

    return {
        "schema_version": SCHEMA_VERSION,
        "source": "Census C-30 via FRED",
        "fetched_at": fetched_at,
        "as_of": latest_date,
        "series": series_out,
    }


def run(*, out_dir: Path, start: str, dry_run: bool = False) -> dict:
    payload = build_payload(start=start)
    if dry_run:
        return payload
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "construction_latest.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    # slim history: one row per as_of with latest values only
    slim = {
        "schema_version": SCHEMA_VERSION,
        "fetched_at": payload["fetched_at"],
        "as_of": payload["as_of"],
        "values": {
            k: v["latest"]["value"] for k, v in payload["series"].items()
        },
    }
    hist = out_dir / "construction_snapshots.jsonl"
    lines = []
    if hist.exists():
        lines = [ln for ln in hist.read_text(encoding="utf-8").splitlines() if ln.strip()]
    # replace same as_of
    kept = []
    for ln in lines:
        row = json.loads(ln)
        if row.get("as_of") != slim["as_of"]:
            kept.append(ln)
    kept.append(json.dumps(slim, separators=(",", ":")))
    hist.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT)
    p.add_argument("--start", default="2015-01-01", help="earliest observation date")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    try:
        payload = run(out_dir=args.out_dir, start=args.start, dry_run=args.dry_run)
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1

    summary = {
        "as_of": payload["as_of"],
        "fetched_at": payload["fetched_at"],
        "latest": {k: v["latest"] for k, v in payload["series"].items()},
    }
    print(json.dumps(summary, indent=2))
    if not args.dry_run:
        print(f"\nwrote {args.out_dir}/construction_latest.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
