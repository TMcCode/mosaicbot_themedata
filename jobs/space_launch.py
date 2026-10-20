#!/usr/bin/env python3
"""Space launch / orbital logistics backdrop pack; publish to R2.

Theme: Space Supply Chain '26: Space Launch & Orbital Logistics
(stockthemes slug: space-supply-chain-26-space-launch-orbital-logistics).

Orbital launch cadence (LL2) + USASpending NSSL/launch (ex-CLPS) + CLPS keywords +
recipient awards (RKLB/LUNR/FLY/MNTS/SpaceX/Avio). Defense spend + GPR from
dew_defense — not re-fetched here.
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
from lib import usaspending as usa  # noqa: E402
from lib import orbital_launches as oll  # noqa: E402
from lib import theme_score as scores  # noqa: E402
from lib import fmp_pack as fmp_packlib  # noqa: E402

SCHEMA_VERSION = 0
PACK = "space_launch"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "space_launch_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_attachments() -> dict:
    attachments = _load_json(_ATTACHMENTS) if _ATTACHMENTS.exists() else {"attachments": []}
    try:
        remote = r2lib.get_json("themedata/series_attachments.json")
        if isinstance(remote, dict) and isinstance(remote.get("attachments"), list):
            attachments = remote
    except Exception:
        pass
    return attachments


def build_pack(
    *,
    catalog: dict,
    attachments: dict,
    start_year: int,
    out_dir: Path,
    full_backfill: bool = False,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    start = f"{start_year:04d}-01-01"
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    notes: list[str] = []
    latest_date = ""

    pending_derived: list[dict[str, Any]] = []
    fmp_caches = fmp_packlib.FmpCaches.from_catalog(catalog)

    for meta in catalog.get("series") or []:
        key = meta.get("key")
        if not key:
            continue
        src = meta.get("source")
        if src == "derived_sum":
            pending_derived.append(meta)
            continue
        try:
            if src == "launch_library":
                cache = out_dir / "orbital_launches_monthly.json"
                rows = oll.refresh_monthly(
                    cache,
                    start_year=start_year,
                    full_backfill=full_backfill,
                )
                pts = [{"date": d, "value": v} for d, v in rows if d >= start]
                notes.append(
                    f"{key}: LL2 orbital launches ({len(pts)} months; "
                    f"full_backfill={full_backfill})"
                )
            elif src == "usaspending":
                recipient = meta.get("recipient")
                if recipient:
                    pts = usa.fetch_monthly_obligations(
                        recipient_search_text=[str(recipient)],
                        start_date=start,
                    )
                    notes.append(f"{key}: USASpending recipient={recipient!r}")
                else:
                    keywords = list(
                        meta.get("keywords")
                        or usa.DEFAULT_LAUNCH_LOGISTICS_KEYWORDS
                    )
                    pts = usa.fetch_monthly_obligations(
                        keywords=keywords,
                        start_date=start,
                    )
                    notes.append(f"{key}: USASpending keywords={keywords}")
            elif fmp_packlib.is_fmp_source(src):
                pts, note = fmp_caches.resolve(meta, start=start)
                for n in fmp_caches.notes:
                    if n not in notes:
                        notes.append(n)
                fmp_caches.notes.clear()
                notes.append(note)
            else:
                raise RuntimeError(f"unknown source {src}")
            if not pts:
                raise RuntimeError("no points")
            series_out[key] = ps.series_shell(
                meta, points=pts, fetched_at=fetched_at
            )
            latest = series_out[key]["raw"]["latest"]
            if latest and latest.get("date"):
                latest_date = max(latest_date, str(latest["date"]))
        except Exception as e:
            errors.append(f"{key}: {e}")

    for meta in pending_derived:
        key = meta.get("key")
        if not key:
            continue
        try:
            parts = list(meta.get("sum_of") or [])
            if not parts:
                raise RuntimeError("derived_sum missing sum_of")
            by_date: dict[str, float] = {}
            missing = [k for k in parts if k not in series_out]
            if missing:
                raise RuntimeError(f"missing components: {missing}")
            for part_key in parts:
                for p in series_out[part_key]["raw"]["points"]:
                    d, v = p.get("date"), p.get("value")
                    if d is None or v is None:
                        continue
                    by_date[str(d)] = by_date.get(str(d), 0.0) + float(v)
            pts = [
                {"date": d, "value": by_date[d]} for d in sorted(by_date.keys())
            ]
            if not pts:
                raise RuntimeError("no points after sum")
            series_out[key] = ps.series_shell(
                meta, points=pts, fetched_at=fetched_at
            )
            notes.append(f"{key}: derived_sum of {parts}")
            latest = series_out[key]["raw"]["latest"]
            if latest and latest.get("date"):
                latest_date = max(latest_date, str(latest["date"]))
        except Exception as e:
            errors.append(f"{key}: {e}")

    if not series_out:
        raise RuntimeError("no series built: " + "; ".join(errors))

    return {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "source": "Launch Library 2 + USASpending (launch/CLPS/NSSL + recipients)",
        "fetched_at": fetched_at,
        "as_of": latest_date,
        "catalog": catalog,
        "attachments": attachments,
        "series": series_out,
        "errors": errors,
        "notes": notes,
    }


def _write_local(out_dir: Path, payload: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "catalog.json").write_text(
        json.dumps(payload["catalog"], indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "attachments.json").write_text(
        json.dumps(payload["attachments"], indent=2) + "\n", encoding="utf-8"
    )
    raw_dir = out_dir / "raw"
    der_dir = out_dir / "derived"
    raw_dir.mkdir(exist_ok=True)
    der_dir.mkdir(exist_ok=True)
    for key, ser in (payload.get("series") or {}).items():
        (raw_dir / f"{key}.json").write_text(
            json.dumps(
                {
                    "key": key,
                    "label": ser.get("label"),
                    "display_name": ser.get("display_name"),
                    "frequency": ser.get("frequency"),
                    "unit": ser.get("unit"),
                    "points": ser["raw"]["points"],
                    "latest": ser["raw"]["latest"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (der_dir / f"{key}.json").write_text(
            json.dumps({"key": key, "derived": ser.get("derived")}, indent=2) + "\n",
            encoding="utf-8",
        )


def _publish_r2(payload: dict) -> list[str]:
    uris = [
        r2lib.put_json(f"{R2_PREFIX}/latest.json", payload),
        r2lib.put_json(f"{R2_PREFIX}/catalog.json", payload["catalog"]),
        r2lib.put_json(f"{R2_PREFIX}/attachments.json", payload["attachments"]),
    ]
    for key, ser in (payload.get("series") or {}).items():
        uris.append(
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
            )
        )
        uris.append(
            r2lib.put_json(
                f"{R2_PREFIX}/derived/{key}.json",
                {"key": key, "derived": ser.get("derived")},
            )
        )
    slim = {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "fetched_at": payload.get("fetched_at"),
        "as_of": payload.get("as_of"),
        "metrics": {
            key: {
                "raw_latest": ser["raw"]["latest"],
                "derived_latest": {
                    name: meta.get("latest")
                    for name, meta in (ser.get("derived") or {}).items()
                },
            }
            for key, ser in (payload.get("series") or {}).items()
        },
    }
    uris.append(r2lib.put_json(f"{R2_PREFIX}/metrics_latest.json", slim))
    return uris


def run(
    *,
    out_dir: Path,
    start_year: int,
    dry_run: bool = False,
    skip_r2: bool = False,
    full_backfill: bool = False,
) -> dict:
    load_env()
    catalog = _load_json(_CATALOG)
    attachments = _load_attachments()
    payload = build_pack(
        catalog=catalog,
        attachments=attachments,
        start_year=start_year,
        out_dir=out_dir,
        full_backfill=full_backfill,
    )

    if dry_run:
        scores.recompute_and_publish(
            payload, scores_dir=_SCORES_OUT, skip_r2=True, prefer_remote_recipe=False
        )
        return payload

    _write_local(out_dir, payload)
    score_result = scores.recompute_and_publish(
        payload,
        scores_dir=_SCORES_OUT,
        skip_r2=skip_r2,
        sync_recipe=True,
        prefer_remote_recipe=False,
    )
    payload["_theme_scores_detail"] = score_result.get("theme_scores") or []

    r2_uris: list[str] = []
    if not skip_r2:
        r2_uris.extend(_publish_r2(payload))
        r2_uris.extend(score_result.get("r2_uris") or [])
        # Keep global editorial maps in sync
        try:
            r2_uris.append(
                r2lib.put_json(
                    "themedata/series_attachments.json", payload["attachments"]
                )
            )
        except Exception:
            pass
    payload["_r2_uris"] = r2_uris
    return payload


def main(argv: list[str] | None = None) -> int:
    now_y = datetime.now(timezone.utc).year
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT)
    p.add_argument("--start-year", type=int, default=2015)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-r2", action="store_true")
    p.add_argument(
        "--full-backfill",
        action="store_true",
        help="Re-fetch all LL2 quarterly windows from start-year (slow; rate-limited)",
    )
    args = p.parse_args(argv)

    try:
        payload = run(
            out_dir=args.out_dir,
            start_year=args.start_year,
            dry_run=args.dry_run,
            skip_r2=args.skip_r2,
            full_backfill=args.full_backfill,
        )
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1

    summary = {
        "pack": PACK,
        "as_of": payload.get("as_of"),
        "fetched_at": payload.get("fetched_at"),
        "series": list((payload.get("series") or {}).keys()),
        "errors": payload.get("errors") or [],
        "theme_scores": payload.get("theme_scores"),
        "r2": payload.get("_r2_uris") or [],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
