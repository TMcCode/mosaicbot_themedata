#!/usr/bin/env python3
"""Regional Oil '26: The Pre-Salt & North Sea Engines pack; publish to R2.

Theme slug: regional-oil-26-the-pre-salt-north-sea-engines.

NOD NCS fields + ANP Pre-Salt/Santos + FRED Brent/EU gas + Gassco exit +
UK ET 3.1 + FMP members + Wikipedia + Kalshi PBR. Forums sidecar separate.
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
from lib import kalshi as kalshilib  # noqa: E402
from lib import theme_score as scores  # noqa: E402
from lib import regional_oil_alt as alt  # noqa: E402
from lib.pack_derived import apply_derived_sums  # noqa: E402

SCHEMA_VERSION = 0
PACK = "regional_oil_presalt_ncs"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "regional_oil_presalt_ncs_catalog.json"
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
    skip_anp_zips: bool = False,
    skip_anp_bulletin: bool = False,
    skip_gassco: bool = False,
    skip_abicom: bool = False,
    skip_uk: bool = False,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    start = f"{start_year:04d}-01-01"
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    notes: list[str] = []
    latest_date = ""
    fmp_caches = fmp_packlib.FmpCaches.from_catalog(catalog)

    sources = {m.get("source") for m in (catalog.get("series") or [])}

    nod_fields: dict[str, list] = {}
    if "nod_field" in sources:
        nod_fields, n = alt.fetch_nod_field_oil_kbpd(start_year=start_year)
        notes.extend(n)

    anp_fields: dict[str, list] = {}
    if not skip_anp_zips and "anp_presalt_field" in sources:
        anp_fields, n = alt.fetch_anp_presalt_fields(
            out_dir / "anp_zip_cache", start_year=max(start_year, 2018)
        )
        notes.extend(n)

    santos_pts: list = []
    if not skip_anp_bulletin and (
        "anp_santos_bulletin" in sources or "anp_presalt_field" in sources
    ):
        santos_pts, n = alt.fetch_anp_santos_oil_from_bulletins(
            out_dir / "anp_bulletin_cache", start_year=max(start_year, 2022)
        )
        notes.extend(n)
        # Tabela 10 + open-data mar CSVs extend Pre-Salt stacks past 2023 (CDP-gated wells).
        if "anp_presalt_field" in sources:
            bull_fields, bn = alt.fetch_anp_presalt_fields_from_bulletins(
                out_dir / "anp_bulletin_cache", start_year=max(start_year, 2022)
            )
            notes.extend(bn)
            od_fields, odn = alt.fetch_anp_presalt_fields_from_opendata(
                start_year=max(start_year, 2024)
            )
            notes.extend(odn)
            if bull_fields or od_fields:
                anp_fields = alt.merge_anp_field_histories(
                    anp_fields, bull_fields, od_fields
                )
                end = (anp_fields.get("buzios") or [{}])[-1].get("date")
                notes.append(
                    "ANP fields: merged well-zips + bulletin Tabela 10 + open-data mar "
                    f"(buzios through {end})"
                )

    gassco_hist = out_dir / "gassco_ncs_exit_history.jsonl"
    gassco_weekly: list = []
    if not skip_gassco and "gassco_exit" in sources:
        _row, n = alt.scrape_gassco_ncs_exit(gassco_hist)
        notes.extend(n)
        daily = alt.gassco_points_from_history(gassco_hist)
        gassco_weekly = alt.gassco_weekly_avg(daily)
        notes.append(f"Gassco weekly pts={len(gassco_weekly)}")

    uk_pts: list = []
    if not skip_uk and "uk_et31" in sources:
        uk_pts, n = alt.fetch_uk_indigenous_oil(start_year=start_year)
        notes.extend(n)

    abicom_hist = out_dir / "abicom_parity_history.jsonl"
    abicom_pts: list = []
    if not skip_abicom and "abicom_parity" in sources:
        abicom_pts, n = alt.scrape_abicom_parity(abicom_hist)
        notes.extend(n)

    wiki_brands: dict[str, list] = {}
    wiki_basket: list = []
    if "wikipedia_pageviews" in sources:
        wiki_brands, wiki_basket, n = alt.wikipedia_brand_and_basket(
            start_year=max(start_year, 2018)
        )
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
                # daily Brent → month-end last for cleaner boards (keep daily if few)
                notes.append(f"{key}: FRED {sid} ({len(pts)} pts)")
            elif src == "nod_field":
                field = str(meta.get("nod_field") or "")
                pts = [p for p in (nod_fields.get(field) or []) if p["date"] >= start]
                notes.append(f"{key}: NOD {field} n={len(pts)}")
            elif src == "anp_presalt_field":
                field = str(meta.get("anp_field") or "")
                pts = [p for p in (anp_fields.get(field) or []) if p["date"] >= start]
                notes.append(f"{key}: ANP {field} n={len(pts)}")
            elif src == "anp_santos_bulletin":
                pts = [p for p in santos_pts if p["date"] >= start]
                notes.append(f"{key}: Santos n={len(pts)}")
            elif src == "gassco_exit":
                pts = [p for p in gassco_weekly if p["date"] >= start]
                if not pts:
                    raise RuntimeError("no Gassco history yet")
            elif src == "uk_et31":
                pts = [p for p in uk_pts if p["date"] >= start]
                notes.append(f"{key}: UK ET n={len(pts)}")
            elif src == "abicom_parity":
                pts = [p for p in abicom_pts if p["date"] >= start]
                if not pts:
                    raise RuntimeError("no Abicom points (site may block scrapes)")
            elif src == "wikipedia_pageviews":
                field = str(meta.get("metric_field") or "basket")
                pts = wiki_basket if field == "basket" else (wiki_brands.get(field) or [])
                pts = [p for p in pts if str(p["date"]) >= start]
            elif src == "kalshi":
                series_t = str(meta.get("kalshi_series") or "")
                ticker = str(meta.get("kalshi_ticker") or "")
                if not series_t or not ticker:
                    raise RuntimeError("kalshi_series and kalshi_ticker required")
                pts, mm = kalshilib.fetch_daily_probability(
                    series_ticker=series_t,
                    market_ticker=ticker,
                )
                pts = [p for p in pts if p["date"] >= start]
                notes.append(
                    f"{key}: Kalshi {ticker} last={mm.get('last_price')} "
                    f"oi={mm.get('open_interest')}"
                )
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

    # Prefer pre-computed ANP focus sum from scraper if derived_sum layers missing months
    if "presalt_focus" in anp_fields and "presalt_focus_oil" not in series_out:
        meta = next(
            (m for m in catalog.get("series") or [] if m.get("key") == "presalt_focus_oil"),
            None,
        )
        pts = [p for p in anp_fields["presalt_focus"] if p["date"] >= start]
        if meta and pts:
            try:
                series_out["presalt_focus_oil"] = ps.series_shell(
                    meta, points=pts, fetched_at=fetched_at
                )
                notes.append(f"presalt_focus_oil: from ANP aggregate n={len(pts)}")
            except Exception as e:
                errors.append(f"presalt_focus_oil: {e}")

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
            "NOD FactPages + ANP PreSal/Santos + FRED Brent/EU gas + Gassco UMM + "
            "DESNZ ET 3.1 + FMP members + Wikipedia + Kalshi"
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
        "gassco_ncs_exit_history.jsonl",
        "abicom_parity_history.jsonl",
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
                pass
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
    skip_anp_zips: bool = False,
    skip_anp_bulletin: bool = False,
    skip_gassco: bool = False,
    skip_abicom: bool = False,
    skip_uk: bool = False,
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
        skip_anp_zips=skip_anp_zips,
        skip_anp_bulletin=skip_anp_bulletin,
        skip_gassco=skip_gassco,
        skip_abicom=skip_abicom,
        skip_uk=skip_uk,
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
    p.add_argument("--skip-anp-zips", action="store_true")
    p.add_argument("--skip-anp-bulletin", action="store_true")
    p.add_argument("--skip-gassco", action="store_true")
    p.add_argument("--skip-abicom", action="store_true")
    p.add_argument("--skip-uk", action="store_true")
    args = p.parse_args(argv)
    try:
        payload = run(
            out_dir=args.out_dir,
            start_year=args.start_year,
            dry_run=args.dry_run,
            skip_r2=args.skip_r2,
            skip_anp_zips=args.skip_anp_zips,
            skip_anp_bulletin=args.skip_anp_bulletin,
            skip_gassco=args.skip_gassco,
            skip_abicom=args.skip_abicom,
            skip_uk=args.skip_uk,
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
        "notes": (payload.get("notes") or [])[-40:],
        "r2": payload.get("_r2_uris") or [],
    }
    if not args.dry_run:
        print(f"\nwrote {args.out_dir}/latest.json", file=sys.stderr)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
