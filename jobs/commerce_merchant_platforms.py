#!/usr/bin/env python3
"""Retail Tech '26: Commerce & Merchant Platforms pack; publish to R2.

Theme slug: retail-tech-26-commerce-merchant-platforms.

FRED e-com retail sales + FMP member/segment mix + Wikipedia pageviews +
Shopify App Store category counts + iTunes ratings + npm SDK downloads.
Forums in commerce_merchant_platforms_forums. Reuses agentic_payments
ecommerce_sales_share / retail_sales via attachments.
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
from lib import fred as fredlib  # noqa: E402
from lib import fmp_pack as fmp_packlib  # noqa: E402
from lib import theme_score as scores  # noqa: E402
from lib import commerce_merchant_alt as alt  # noqa: E402
from lib.pack_derived import apply_derived_sums  # noqa: E402

SCHEMA_VERSION = 0
PACK = "commerce_merchant_platforms"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "commerce_merchant_platforms_catalog.json"
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
    skip_shopify_apps: bool = False,
    skip_itunes: bool = False,
    skip_npm: bool = False,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    start = f"{start_year:04d}-01-01"
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    notes: list[str] = [
        "reuse via attachments: ecommerce_sales_share + retail_sales "
        "(agentic_payments) — no re-pull"
    ]
    latest_date = ""
    fmp_caches = fmp_packlib.FmpCaches.from_catalog(catalog)

    wiki_brands: dict[str, list] = {}
    wiki_basket: list = []
    if any(m.get("source") == "wikipedia_pageviews" for m in (catalog.get("series") or [])):
        wiki_brands, wiki_basket, wiki_notes = alt.wikipedia_brand_and_basket(
            start_year=max(start_year, 2018)
        )
        notes.extend(wiki_notes)

    shopify_hist = out_dir / "shopify_apps_history.jsonl"
    itunes_hist = out_dir / "itunes_ratings_history.jsonl"
    npm_hist = out_dir / "npm_downloads_history.jsonl"

    if not skip_shopify_apps and any(
        m.get("source") == "shopify_app_store" for m in (catalog.get("series") or [])
    ):
        _row, n = alt.scrape_shopify_app_categories(shopify_hist)
        notes.extend(n)

    if not skip_itunes and any(
        m.get("source") == "itunes_ratings" for m in (catalog.get("series") or [])
    ):
        _row, n = alt.scrape_itunes_ratings(itunes_hist)
        notes.extend(n)

    if not skip_npm and any(
        m.get("source") == "npm_downloads" for m in (catalog.get("series") or [])
    ):
        _row, n = alt.scrape_npm_downloads(npm_hist)
        notes.extend(n)

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
            elif src == "wikipedia_pageviews":
                field = str(meta.get("metric_field") or "basket")
                if field == "basket":
                    pts = wiki_basket
                else:
                    pts = wiki_brands.get(field) or []
                pts = [p for p in pts if str(p["date"]) >= start]
                notes.append(f"{key}: wiki {field} n={len(pts)}")
            elif src == "shopify_app_store":
                field = str(meta.get("metric_field") or "category_sum")
                pts = alt.shopify_app_points(shopify_hist, field)
                if not pts:
                    raise RuntimeError("no Shopify App Store history yet")
            elif src == "itunes_ratings":
                field = str(meta.get("metric_field") or "ratings_basket")
                pts = alt.itunes_points(itunes_hist, field)
                if not pts:
                    raise RuntimeError("no iTunes history yet")
            elif src == "npm_downloads":
                field = str(meta.get("metric_field") or "npm_basket")
                pts = alt.npm_points(npm_hist, field)
                if not pts:
                    raise RuntimeError("no npm history yet")
            elif src == "derived_sum":
                continue
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
            "FRED e-com retail + FMP members/segments + Wikipedia + "
            "Shopify App Store + iTunes + npm"
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
        "shopify_apps_history.jsonl",
        "itunes_ratings_history.jsonl",
        "npm_downloads_history.jsonl",
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
    skip_shopify_apps: bool = False,
    skip_itunes: bool = False,
    skip_npm: bool = False,
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
        skip_shopify_apps=skip_shopify_apps,
        skip_itunes=skip_itunes,
        skip_npm=skip_npm,
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
    p.add_argument("--skip-shopify-apps", action="store_true")
    p.add_argument("--skip-itunes", action="store_true")
    p.add_argument("--skip-npm", action="store_true")
    args = p.parse_args(argv)
    try:
        payload = run(
            out_dir=args.out_dir,
            start_year=args.start_year,
            dry_run=args.dry_run,
            skip_r2=args.skip_r2,
            skip_shopify_apps=args.skip_shopify_apps,
            skip_itunes=args.skip_itunes,
            skip_npm=args.skip_npm,
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
