#!/usr/bin/env python3
"""Weekly Trading Economics germanium spot scrape → dew_defense history + R2.

AISI-style: public page = latest level only. Append local/R2 history, then merge
the spot series into the published dew_defense pack (no full FRED/Census refetch).
Mac Mini host (residential IP).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.env import load_env  # noqa: E402
from lib import r2 as r2lib  # noqa: E402
from lib import pack_series as ps  # noqa: E402
from lib import tradingeconomics_ge as te  # noqa: E402

SCHEMA_VERSION = 0
PACK = "dew_defense"
SERIES_KEY = "germanium_spot_cny_kg"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_CATALOG = _ROOT / "config" / "dew_defense_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_attachments() -> dict:
    attachments = _load_json(_ATTACHMENTS) if _ATTACHMENTS.exists() else {"attachments": []}
    try:
        remote = r2lib.get_json("themedata/series_attachments.json")
        if isinstance(remote, dict) and isinstance(remote.get("attachments"), list):
            # Prefer remote editorial map, but ensure new seed keys exist
            remote_keys = {
                (a.get("series_key"), a.get("scope"), a.get("theme"), a.get("ticker"))
                for a in remote["attachments"]
            }
            for a in attachments.get("attachments") or []:
                key = (a.get("series_key"), a.get("scope"), a.get("theme"), a.get("ticker"))
                if key not in remote_keys:
                    remote["attachments"].append(a)
            return remote
    except Exception:
        pass
    return attachments


def _catalog_meta(catalog: dict) -> dict:
    for meta in catalog.get("series") or []:
        if meta.get("key") == SERIES_KEY:
            return meta
    raise RuntimeError(f"{SERIES_KEY} missing from dew_defense catalog")


def _merge_history(local: list[dict], remote: list[dict]) -> list[dict]:
    by_as_of: dict[str, dict] = {}
    for row in remote + local:
        key = str(row.get("as_of") or "")
        if not key:
            continue
        by_as_of[key] = row
    return [by_as_of[k] for k in sorted(by_as_of)]


def _load_merged_history(out_dir: Path) -> list[dict]:
    local = te.load_history(out_dir)
    remote: list[dict] = []
    try:
        raw = r2lib.get_bytes(te.R2_HISTORY_KEY)
        if raw:
            remote = te.rows_from_r2_body(raw)
    except Exception:
        remote = []
    return _merge_history(local, remote)


def _write_history(out_dir: Path, rows: list[dict]) -> None:
    path = te.history_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(te.history_to_r2_body(rows), encoding="utf-8")


def _load_existing_pack(out_dir: Path) -> dict[str, Any] | None:
    local = out_dir / "latest.json"
    if local.exists():
        return _load_json(local)
    try:
        remote = r2lib.get_json(f"{R2_PREFIX}/latest.json")
        if isinstance(remote, dict):
            return remote
    except Exception:
        pass
    return None


def _publish_series_slice(payload: dict, key: str) -> list[str]:
    ser = payload["series"][key]
    uris = [
        r2lib.put_json(
            f"{R2_PREFIX}/raw/{key}.json",
            {
                "key": key,
                "label": ser.get("label"),
                "display_name": ser.get("display_name"),
                "frequency": ser.get("frequency"),
                "unit": ser.get("unit"),
                "points": ser["raw"]["points"],
                "latest": ser["raw"]["latest"],
            },
        ),
        r2lib.put_json(
            f"{R2_PREFIX}/derived/{key}.json",
            {"key": key, "derived": ser.get("derived")},
        ),
    ]
    # Refresh pack shell + metrics for this key
    uris.append(r2lib.put_json(f"{R2_PREFIX}/latest.json", payload))
    uris.append(r2lib.put_json(f"{R2_PREFIX}/catalog.json", payload["catalog"]))
    slim = {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "fetched_at": payload.get("fetched_at"),
        "as_of": payload.get("as_of"),
        "metrics": {},
    }
    try:
        existing_slim = r2lib.get_json(f"{R2_PREFIX}/metrics_latest.json")
        if isinstance(existing_slim, dict) and isinstance(existing_slim.get("metrics"), dict):
            slim["metrics"] = dict(existing_slim["metrics"])
            slim["fetched_at"] = payload.get("fetched_at") or existing_slim.get("fetched_at")
            # Keep pack as_of as max of prior + this series
            prior = str(existing_slim.get("as_of") or "")
            cur = str(payload.get("as_of") or "")
            slim["as_of"] = max(prior, cur)
    except Exception:
        pass
    slim["metrics"][key] = {
        "raw_latest": ser["raw"]["latest"],
        "derived_latest": {
            name: meta.get("latest") for name, meta in (ser.get("derived") or {}).items()
        },
    }
    uris.append(r2lib.put_json(f"{R2_PREFIX}/metrics_latest.json", slim))
    return uris


def run(
    *,
    out_dir: Path,
    dry_run: bool = False,
    skip_r2: bool = False,
    skip_fetch: bool = False,
) -> dict:
    load_env()
    catalog = _load_json(_CATALOG)
    meta = _catalog_meta(catalog)
    attachments = _load_attachments()

    snapshot = None
    inserted = False
    if not skip_fetch:
        snapshot, inserted = te.scrape_and_upsert(out_dir)

    history = _load_merged_history(out_dir)
    if snapshot and not any(r.get("as_of") == snapshot.get("as_of") for r in history):
        history = _merge_history(history, [snapshot])

    if not history:
        raise RuntimeError("no germanium TE history — fetch once first")

    # Persist merged history locally (and later R2)
    if not dry_run:
        _write_history(out_dir, history)
        if snapshot:
            te.latest_path(out_dir).write_text(
                json.dumps(snapshot, indent=2) + "\n", encoding="utf-8"
            )

    pts = te.points_from_history(history)
    series_obj = ps.series_shell(meta, points=pts)

    existing = _load_existing_pack(out_dir)
    if existing is None:
        existing = {
            "schema_version": SCHEMA_VERSION,
            "pack": PACK,
            "source": "Trading Economics germanium spot (partial)",
            "catalog": catalog,
            "attachments": attachments,
            "series": {},
            "errors": [],
            "notes": [],
        }
    else:
        existing["catalog"] = catalog
        existing["attachments"] = attachments

    existing["series"][SERIES_KEY] = series_obj
    existing["fetched_at"] = (snapshot or {}).get("fetched_at") or datetime.now(
        timezone.utc
    ).isoformat()
    # Pack as_of: max across series latest dates when available
    as_of = ""
    for ser in (existing.get("series") or {}).values():
        latest = (ser.get("raw") or {}).get("latest") or {}
        if latest.get("date"):
            as_of = max(as_of, str(latest["date"]))
    existing["as_of"] = as_of or (snapshot or {}).get("as_of") or ""
    notes = list(existing.get("notes") or [])
    note = "germanium_spot_cny_kg: Trading Economics CFD proxy (weekly scrape)"
    if note not in notes:
        notes.append(note)
    existing["notes"] = notes
    existing["_ge_spot"] = {
        "inserted": inserted,
        "snapshot": snapshot,
        "history_points": len(pts),
    }

    if dry_run:
        return existing

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.json").write_text(
        json.dumps(existing, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "catalog.json").write_text(
        json.dumps(catalog, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "attachments.json").write_text(
        json.dumps(attachments, indent=2) + "\n", encoding="utf-8"
    )

    r2_uris: list[str] = []
    if not skip_r2:
        r2_uris.append(
            r2lib.put_bytes(
                te.R2_HISTORY_KEY,
                te.history_to_r2_body(history).encode("utf-8"),
                content_type="application/x-ndjson",
            )
        )
        snap_for_r2 = snapshot or (history[-1] if history else None)
        if snap_for_r2:
            r2_uris.append(
                r2lib.put_json(
                    f"{R2_PREFIX}/{te.LATEST_NAME}",
                    snap_for_r2,
                )
            )
        r2_uris.append(r2lib.put_json(f"{R2_PREFIX}/attachments.json", attachments))
        r2_uris.extend(_publish_series_slice(existing, SERIES_KEY))
    existing["_r2_uris"] = r2_uris
    return existing


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-r2", action="store_true")
    p.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Rebuild series from existing germanium_te_history.jsonl only",
    )
    args = p.parse_args(argv)

    try:
        payload = run(
            out_dir=args.out_dir,
            dry_run=args.dry_run,
            skip_r2=args.skip_r2,
            skip_fetch=args.skip_fetch,
        )
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1

    ge = payload.get("_ge_spot") or {}
    snap = ge.get("snapshot") or {}
    summary = {
        "pack": PACK,
        "series_key": SERIES_KEY,
        "as_of": snap.get("as_of") or payload.get("as_of"),
        "price_cny_kg": snap.get("price_cny_kg"),
        "history_points": ge.get("history_points"),
        "inserted": ge.get("inserted"),
        "r2": payload.get("_r2_uris") or [],
    }
    print(json.dumps(summary, indent=2))
    if not args.dry_run:
        flag = "new as_of" if ge.get("inserted") else "history rebuild"
        print(f"\nwrote {args.out_dir}/{te.LATEST_NAME} ({flag})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
