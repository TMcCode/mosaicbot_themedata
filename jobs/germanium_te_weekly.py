#!/usr/bin/env python3
"""Weekly Trading Economics commodity spots → dew_defense history + R2.

Budget-aware (TE ~500 req/month):
  - Default: one batched ``markets/symbol`` for every ``status=pull`` row in
    ``config/te_symbols.json`` (Ge + Ga today).
  - ``--mode scrape``: germanium HTML only (0 API); gallium skipped unless API.
  - ``--backfill`` / ``--ingest-historical``: rare historical pulls.
  - Monthly ``dew_defense`` pack reads history only — does not call TE.
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
from lib import te_spot  # noqa: E402
from lib import tradingeconomics as teapi  # noqa: E402
from lib import tradingeconomics_ge as te_ge  # noqa: E402
from lib import theme_score as scores  # noqa: E402

SCHEMA_VERSION = 0
PACK = "dew_defense"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "dew_defense_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_attachments() -> dict:
    attachments = _load_json(_ATTACHMENTS) if _ATTACHMENTS.exists() else {"attachments": []}
    try:
        remote = r2lib.get_json("themedata/series_attachments.json")
        if isinstance(remote, dict) and isinstance(remote.get("attachments"), list):
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


def _catalog_meta(catalog: dict, series_key: str) -> dict:
    for meta in catalog.get("series") or []:
        if meta.get("key") == series_key:
            return meta
    raise RuntimeError(f"{series_key} missing from dew_defense catalog")


def _merge_history(local: list[dict], remote: list[dict]) -> list[dict]:
    by_as_of: dict[str, dict] = {}
    for row in remote + local:
        key = str(row.get("as_of") or "")
        if not key:
            continue
        by_as_of[key] = row
    return [by_as_of[k] for k in sorted(by_as_of)]


def _load_merged_history(out_dir: Path, spec: te_spot.SpotSpec) -> list[dict]:
    local = te_spot.load_history(out_dir, spec)
    remote: list[dict] = []
    try:
        raw = r2lib.get_bytes(spec.r2_history_key)
        if raw:
            remote = te_spot.rows_from_body(raw)
    except Exception:
        remote = []
    return _merge_history(local, remote)


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
    mode: str = "auto",
    backfill: bool = False,
    ingest_historical: dict[str, Path] | None = None,
    force: bool = False,
    only: list[str] | None = None,
) -> dict:
    load_env()
    catalog = _load_json(_CATALOG)
    attachments = _load_attachments()
    specs = te_spot.specs_from_symbology(pull_only=True)
    if only:
        want = set(only)
        specs = [s for s in specs if s.series_key in want]
    if not specs:
        raise RuntimeError("no TE spot series selected")

    per_series: dict[str, Any] = {}
    refresh_via = None

    ingest_historical = ingest_historical or {}
    for spec in specs:
        if spec.series_key in ingest_historical:
            n = te_spot.ingest_historical_file(
                out_dir, spec, ingest_historical[spec.series_key]
            )
            per_series.setdefault(spec.series_key, {})["backfill_points"] = n

    if backfill and not dry_run:
        for spec in specs:
            n = te_spot.api_backfill(out_dir, spec, force=force)
            per_series.setdefault(spec.series_key, {})["backfill_points"] = n

    if not skip_fetch:
        mode_l = (mode or "auto").lower()
        if mode_l == "auto":
            stats = teapi.usage_stats()
            mode_l = "api" if stats["routine_remaining"] > 0 else "scrape"

        if mode_l == "api":
            refreshed = te_spot.refresh_batch_api(out_dir, specs, force=force)
            refresh_via = "api"
            for key, (snap, inserted) in refreshed.items():
                per_series.setdefault(key, {}).update(
                    {"snapshot": snap, "inserted": inserted}
                )
        elif mode_l == "scrape":
            # Ge only — public HTML; Ga needs API
            refresh_via = "scrape"
            for spec in specs:
                if spec.series_key == "germanium_spot_cny_kg":
                    snap, inserted = te_ge.scrape_and_upsert(out_dir)
                    per_series.setdefault(spec.series_key, {}).update(
                        {"snapshot": snap, "inserted": inserted}
                    )
                else:
                    per_series.setdefault(spec.series_key, {})["note"] = (
                        "scrape mode: no HTML fallback; history unchanged"
                    )
        else:
            raise ValueError(f"unknown mode {mode!r}")

    existing = _load_existing_pack(out_dir)
    if existing is None:
        existing = {
            "schema_version": SCHEMA_VERSION,
            "pack": PACK,
            "source": "Trading Economics commodity spots",
            "catalog": catalog,
            "attachments": attachments,
            "series": {},
            "errors": [],
            "notes": [],
        }
    else:
        existing["pack"] = PACK
        existing["catalog"] = catalog
        existing["attachments"] = attachments

    histories: dict[str, list[dict]] = {}
    for spec in specs:
        history = _load_merged_history(out_dir, spec)
        snap = (per_series.get(spec.series_key) or {}).get("snapshot")
        if snap and not any(r.get("as_of") == snap.get("as_of") for r in history):
            history = _merge_history(history, [snap])
        if not history:
            raise RuntimeError(
                f"no history for {spec.series_key} — run --backfill once first"
            )
        histories[spec.series_key] = history
        if not dry_run:
            te_spot.write_history(out_dir, spec, history)

        meta = _catalog_meta(catalog, spec.series_key)
        pts = te_spot.points_from_history(history)
        existing["series"][spec.series_key] = ps.series_shell(meta, points=pts)
        per_series.setdefault(spec.series_key, {})["history_points"] = len(pts)

    existing["fetched_at"] = datetime.now(timezone.utc).isoformat()
    as_of = ""
    for ser in (existing.get("series") or {}).values():
        latest = (ser.get("raw") or {}).get("latest") or {}
        if latest.get("date"):
            as_of = max(as_of, str(latest["date"]))
    existing["as_of"] = as_of

    notes = [
        n
        for n in (existing.get("notes") or [])
        if not str(n).startswith("germanium_spot_cny_kg:")
        and not str(n).startswith("gallium_spot_cny_kg:")
        and not str(n).startswith("te_spots:")
    ]
    notes.append(
        f"te_spots: weekly refresh via {refresh_via or 'history-only'} "
        f"({', '.join(s.series_key for s in specs)})"
    )
    existing["notes"] = notes
    existing["_te_spots"] = {
        "refresh_via": refresh_via,
        "series": per_series,
        "te_usage": teapi.usage_stats(),
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

    # Backdrop score: cheap recompute from pack series (no TE/source refetch)
    score_result = scores.recompute_and_publish(
        existing,
        scores_dir=_SCORES_OUT,
        skip_r2=True,  # publish below with pack objects
        sync_recipe=False,
        prefer_remote_recipe=False,  # use local seed (includes Ge/Ga spots)
    )
    existing["_theme_scores_detail"] = score_result.get("theme_scores") or []

    r2_uris: list[str] = []
    if not skip_r2:
        for spec in specs:
            hist = histories[spec.series_key]
            r2_uris.append(
                r2lib.put_bytes(
                    spec.r2_history_key,
                    te_spot.history_to_body(hist).encode("utf-8"),
                    content_type="application/x-ndjson",
                )
            )
            snap = (per_series.get(spec.series_key) or {}).get("snapshot") or (
                hist[-1] if hist else None
            )
            if snap:
                r2_uris.append(
                    r2lib.put_json(f"{R2_PREFIX}/{spec.latest_name}", snap)
                )
            r2_uris.extend(_publish_series_slice(existing, spec.series_key))
        r2_uris.append(r2lib.put_json(f"{R2_PREFIX}/attachments.json", attachments))
        # Publish scores + sync recipe so weekly TE refresh updates the chip
        if score_result.get("theme_scores"):
            r2_uris.extend(
                scores.publish_scores_r2(
                    score_result["theme_scores"],
                    score_result["index"],
                    recipe=scores.load_recipe(prefer_remote=False),
                )
            )
    existing["_r2_uris"] = r2_uris
    return existing


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-r2", action="store_true")
    p.add_argument("--skip-fetch", action="store_true")
    p.add_argument("--mode", choices=("auto", "api", "scrape"), default="auto")
    p.add_argument("--backfill", action="store_true")
    p.add_argument(
        "--ingest-historical",
        action="append",
        default=[],
        metavar="SERIES=PATH",
        help="Merge saved TE historical JSON (0 API). Repeatable.",
    )
    p.add_argument("--force", action="store_true")
    p.add_argument(
        "--only",
        action="append",
        default=[],
        help="Limit to series_key (repeatable). Default: all pullable.",
    )
    args = p.parse_args(argv)

    ingest: dict[str, Path] = {}
    for item in args.ingest_historical:
        if "=" not in item:
            print(f"FAILED: --ingest-historical needs SERIES=PATH, got {item!r}", file=sys.stderr)
            return 2
        k, v = item.split("=", 1)
        ingest[k.strip()] = Path(v.strip())

    try:
        payload = run(
            out_dir=args.out_dir,
            dry_run=args.dry_run,
            skip_r2=args.skip_r2,
            skip_fetch=args.skip_fetch,
            mode=args.mode,
            backfill=args.backfill,
            ingest_historical=ingest or None,
            force=args.force,
            only=args.only or None,
        )
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1

    te = payload.get("_te_spots") or {}
    summary = {
        "pack": PACK,
        "as_of": payload.get("as_of"),
        "refresh_via": te.get("refresh_via"),
        "te_usage": te.get("te_usage"),
        "theme_scores": payload.get("theme_scores"),
        "series": {
            k: {
                "as_of": (v.get("snapshot") or {}).get("as_of"),
                "price_cny_kg": (v.get("snapshot") or {}).get("price_cny_kg"),
                "history_points": v.get("history_points"),
                "inserted": v.get("inserted"),
                "backfill_points": v.get("backfill_points"),
            }
            for k, v in (te.get("series") or {}).items()
        },
        "r2": payload.get("_r2_uris") or [],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
