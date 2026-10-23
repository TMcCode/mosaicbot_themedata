#!/usr/bin/env python3
"""Toys & Hobbies '25: Trad Toys, Dolls & Games pack; publish to R2.

Theme slug: toys-hobbies-25-trad-toys-dolls-games.

FRED toys CPI / hobby-store sales / resins & dolls PPI + Census HS 9503 +
FMP members/segments + Wikipedia pageviews + Amazon bestsellers (Chrome) +
Tonies catalog + BGG family own/hotness + CPSC toy recalls.
Forums in trad_toys_forums.
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

from lib.env import load_env, require_env  # noqa: E402
from lib import r2 as r2lib  # noqa: E402
from lib import pack_series as ps  # noqa: E402
from lib import fred as fredlib  # noqa: E402
from lib import census_trade as census  # noqa: E402
from lib import fmp_pack as fmp_packlib  # noqa: E402
from lib import theme_score as scores  # noqa: E402
from lib import trad_toys_alt as alt  # noqa: E402
from lib.pack_derived import apply_derived_sums  # noqa: E402

SCHEMA_VERSION = 0
PACK = "trad_toys"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "trad_toys_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_attachments() -> dict:
    attachments = (
        _load_json(_ATTACHMENTS) if _ATTACHMENTS.exists() else {"attachments": []}
    )
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
    skip_amazon: bool = False,
    skip_tonies: bool = False,
    skip_bgg: bool = False,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    start = f"{start_year:04d}-01-01"
    end_year = datetime.now(timezone.utc).year
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    notes: list[str] = []
    latest_date = ""

    census_key = ""
    if any((m.get("source") == "census_trade") for m in (catalog.get("series") or [])):
        census_key = require_env("CENSUS_API_KEY")["CENSUS_API_KEY"]
    census_cache: dict[str, tuple[list, list, str | None]] = {}
    fmp_caches = fmp_packlib.FmpCaches.from_catalog(catalog)

    # Wikipedia (API — full history each run)
    wiki_brands: dict[str, list] = {}
    wiki_basket: list = []
    if any(m.get("source") == "wikipedia_pageviews" for m in (catalog.get("series") or [])):
        wiki_brands, wiki_basket, wiki_notes = alt.wikipedia_brand_and_basket(
            start_year=max(start_year, 2018)
        )
        notes.extend(wiki_notes)

    # Amazon / Tonies / BGG — weekly append scrapes
    amazon_hist = out_dir / "amazon_bestsellers_history.jsonl"
    tonies_hist = out_dir / "tonies_catalog_history.jsonl"
    bgg_hist = out_dir / "bgg_history.jsonl"

    if not skip_amazon and any(
        m.get("source") == "amazon_bestsellers" for m in (catalog.get("series") or [])
    ):
        _row, n = alt.scrape_amazon_bestsellers(amazon_hist)
        notes.extend(n)

    if not skip_tonies and any(
        m.get("source") == "tonies_catalog" for m in (catalog.get("series") or [])
    ):
        _row, n = alt.scrape_tonies_catalog(tonies_hist)
        notes.extend(n)

    if not skip_bgg and any(
        m.get("source") == "bgg" for m in (catalog.get("series") or [])
    ):
        _row, n = alt.scrape_bgg(bgg_hist)
        notes.extend(n)

    cpsc_pts: list[dict[str, float]] | None = None
    if any(m.get("source") == "cpsc_recalls" for m in (catalog.get("series") or [])):
        try:
            cpsc_pts = alt.cpsc_monthly_counts(start_year=start_year)
            notes.append(f"cpsc: monthly toy recalls n={len(cpsc_pts)}")
        except Exception as e:
            errors.append(f"cpsc_toy_recalls: {e}")
            notes.append(f"cpsc: ERR {e}")

    for meta in catalog.get("series") or []:
        key = meta.get("key")
        if not key:
            continue
        src = meta.get("source")
        try:
            if src == "fred":
                sid = str(meta["fred_id"])
                pts = [
                    {"date": d if len(d) == 10 else d[:10], "value": v}
                    for d, v in fredlib.fetch_series(sid)
                    if d >= start
                ]
                notes.append(f"{key}: FRED {sid} ({len(pts)} pts)")
            elif src == "census_trade":
                hs = str(meta.get("hs6") or "")
                if not hs:
                    raise RuntimeError("hs6 required")
                if hs not in census_cache:
                    census_cache[hs] = census.fetch_hs6_world_monthly(
                        hs,
                        start_year=start_year,
                        end_year=end_year,
                        api_key=census_key,
                    )
                val_pts, qty_pts, unit = census_cache[hs]
                field = str(meta.get("field") or "value")
                pts = qty_pts if field == "qty" else val_pts
                pts = [p for p in pts if str(p["date"]) >= start]
                notes.append(f"{key}: Census HS{hs} {field} unit={unit} n={len(pts)}")
            elif src == "wikipedia_pageviews":
                field = str(meta.get("metric_field") or "basket")
                if field == "basket":
                    pts = wiki_basket
                else:
                    pts = wiki_brands.get(field) or []
                pts = [p for p in pts if str(p["date"]) >= start]
                notes.append(f"{key}: wiki {field} n={len(pts)}")
            elif src == "amazon_bestsellers":
                field = str(meta.get("metric_field") or "any_theme_brand")
                pts = alt.amazon_points_from_history(amazon_hist, field=field)
                if not pts:
                    raise RuntimeError("no Amazon history yet (Chrome scrape failed?)")
            elif src == "tonies_catalog":
                field = str(meta.get("metric_field") or "catalog_size")
                pts = alt.tonies_points_from_history(tonies_hist, field=field)
                if not pts:
                    raise RuntimeError("no Tonies history yet")
            elif src == "bgg":
                field = str(meta.get("metric_field") or "family_own_sum")
                pts = alt.bgg_points_from_history(bgg_hist, field=field)
                if not pts:
                    raise RuntimeError("no BGG history yet")
            elif src == "cpsc_recalls":
                pts = [p for p in (cpsc_pts or []) if str(p["date"]) >= start]
                if not pts:
                    raise RuntimeError("no CPSC points")
            elif src == "derived_sum":
                continue  # built after component series
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

    derived_latest = apply_derived_sums(
        catalog,
        series_out,
        notes=notes,
        errors=errors,
        fetched_at=fetched_at,
    )
    if derived_latest:
        latest_date = max(latest_date, derived_latest)

    if not series_out:
        raise RuntimeError("no series built: " + "; ".join(errors))

    return {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "source": (
            "FRED toys CPI/hobby sales/PPI + Census HS 9503 + FMP members/segments + "
            "Wikipedia pageviews + Amazon bestsellers + Tonies catalog + BGG + CPSC"
        ),
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


def _publish_r2(payload: dict, out_dir: Path) -> list[str]:
    uris = [
        r2lib.put_json(f"{R2_PREFIX}/latest.json", payload),
        r2lib.put_json(f"{R2_PREFIX}/catalog.json", payload["catalog"]),
        r2lib.put_json(f"{R2_PREFIX}/attachments.json", payload["attachments"]),
    ]
    for name in (
        "amazon_bestsellers_history.jsonl",
        "tonies_catalog_history.jsonl",
        "bgg_history.jsonl",
    ):
        hist = out_dir / name
        if hist.exists():
            try:
                uris.append(
                    r2lib.put_bytes(
                        f"{R2_PREFIX}/{name}",
                        hist.read_bytes(),
                        content_type="application/x-ndjson",
                    )
                )
            except Exception:
                uris.append(
                    r2lib.put_json(
                        f"{R2_PREFIX}/{name.replace('.jsonl', '.json')}",
                        {
                            "rows": [
                                json.loads(l)
                                for l in hist.read_text(encoding="utf-8").splitlines()
                                if l.strip()
                            ]
                        },
                    )
                )
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
    skip_amazon: bool = False,
    skip_tonies: bool = False,
    skip_bgg: bool = False,
) -> dict:
    load_env()
    catalog = _load_json(_CATALOG)
    attachments = _load_attachments()
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_pack(
        catalog=catalog,
        attachments=attachments,
        start_year=start_year,
        out_dir=out_dir,
        skip_amazon=skip_amazon,
        skip_tonies=skip_tonies,
        skip_bgg=skip_bgg,
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
        r2_uris.extend(_publish_r2(payload, out_dir))
        r2_uris.extend(score_result.get("r2_uris") or [])
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
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT)
    p.add_argument("--start-year", type=int, default=2015)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-r2", action="store_true")
    p.add_argument("--skip-amazon", action="store_true")
    p.add_argument("--skip-tonies", action="store_true")
    p.add_argument("--skip-bgg", action="store_true")
    args = p.parse_args(argv)
    try:
        payload = run(
            out_dir=args.out_dir,
            start_year=args.start_year,
            dry_run=args.dry_run,
            skip_r2=args.skip_r2,
            skip_amazon=args.skip_amazon,
            skip_tonies=args.skip_tonies,
            skip_bgg=args.skip_bgg,
        )
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    summary = {
        "pack": PACK,
        "as_of": payload.get("as_of"),
        "series": {
            k: (v.get("raw") or {}).get("latest")
            for k, v in (payload.get("series") or {}).items()
        },
        "errors": payload.get("errors") or [],
        "notes": payload.get("notes") or [],
        "r2": payload.get("_r2_uris") or [],
    }
    if not args.dry_run:
        print(f"\nwrote {args.out_dir}/latest.json", file=sys.stderr)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
