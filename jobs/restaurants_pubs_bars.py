#!/usr/bin/env python3
"""Restaurants '26: Pubs & Bars backdrop pack; publish to R2.

Theme slug: restaurants-26-pubs-bars.

UK ONS IoS F&B + alcohol duty + AWE + beer PPI + Revolut restaurants card spend
+ TE hospitality CPI / Japan / Australia + FMP company revenue stacks
+ Wikipedia pageviews + sports calendars (PL/CL/WC/Euro/Six Nations/NFL).
Forums sidecar separate.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.env import load_env  # noqa: E402
from lib import r2 as r2lib  # noqa: E402
from lib import pack_series as ps  # noqa: E402
from lib import ons as onslib  # noqa: E402
from lib import ons_revolut as revolutlib  # noqa: E402
from lib import tradingeconomics as teapi  # noqa: E402
from lib import fmp_pack as fmp_packlib  # noqa: E402
from lib import openfootball_pl as ofpl  # noqa: E402
from lib import openfootball_cups as ofcups  # noqa: E402
from lib import six_nations_calendar as sixn  # noqa: E402
from lib import nflverse_schedule as nflsch  # noqa: E402
from lib import theme_score as scores  # noqa: E402
from lib.pack_derived import apply_derived_sums  # noqa: E402
from lib import transforms as tx  # noqa: E402

SCHEMA_VERSION = 0
PACK = "restaurants_pubs_bars"
R2_PREFIX = f"themedata/{PACK}"
UA_WIKI = "mosaicbot_themedata/0.1 (https://stockcontext.info; pubs-bars pageviews)"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "restaurants_pubs_bars_catalog.json"
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


def _month_start(ts: str) -> str:
    # Wikimedia timestamps: YYYYMMDD00
    s = str(ts)[:8]
    return f"{s[0:4]}-{s[4:6]}-01"


def fetch_wiki_monthly(article: str, *, start: date, end: date) -> list[dict[str, float]]:
    art = urllib.parse.quote(article, safe="")
    url = (
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        f"en.wikipedia/all-access/user/{art}/monthly/"
        f"{start.strftime('%Y%m%d')}/{end.strftime('%Y%m%d')}"
    )
    req = urllib.request.Request(
        url, headers={"User-Agent": UA_WIKI, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    pts: list[dict[str, float]] = []
    for item in data.get("items") or []:
        ts = item.get("timestamp")
        views = item.get("views")
        if not ts or views is None:
            continue
        pts.append({"date": _month_start(str(ts)), "value": float(views)})
    pts.sort(key=lambda p: p["date"])
    return pts


def _raw_level_points(ser: dict[str, Any]) -> list[dict[str, Any]]:
    return list((ser.get("raw") or {}).get("points") or [])


def build_pack(
    *,
    catalog: dict,
    attachments: dict,
    start_year: int,
    end_year: int,
    out_dir: Path,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    start = f"{start_year:04d}-01-01"
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    notes: list[str] = []
    latest_date = ""

    fmp_caches = fmp_packlib.FmpCaches.from_catalog(catalog)
    te_ind_by_key: dict[str, dict[str, Any]] = {
        s["series_key"]: s for s in teapi.load_indicator_specs(pull_only=False)
    }
    wiki_end = datetime.now(timezone.utc).date() - timedelta(days=2)
    wiki_start = date(start_year, 1, 1)
    wiki_cache: dict[str, list[dict[str, float]]] = {}

    for meta in catalog.get("series") or []:
        key = meta.get("key")
        if not key:
            continue
        src = meta.get("source")
        if src in ("derived_spread", "derived_ratio", "derived_sum"):
            continue
        try:
            if src == "ons":
                uri = str(meta.get("ons_uri") or "")
                if not uri:
                    raise RuntimeError("ons_uri required")
                pts, mm = onslib.fetch_timeseries(uri)
                pts = [p for p in pts if p["date"] >= start]
                notes.append(
                    f"{key}: ONS {meta.get('ons_cdid') or ''} "
                    f"({mm.get('title') or uri}) n={len(pts)}"
                )
            elif src == "te_indicator":
                te_key = str(meta.get("te_series_key") or key)
                spec = te_ind_by_key.get(te_key)
                if not spec:
                    raise RuntimeError(
                        f"te_symbols.json indicators missing {te_key!r}"
                    )
                rows = teapi.indicator_historical(
                    str(spec["country"]),
                    str(spec["indicator"]),
                    d1=f"{start_year:04d}-01-01",
                )
                pts = [
                    p
                    for p in teapi.indicator_to_points(rows)
                    if p["date"] >= start
                ]
                # Normalize TE month-end dates to month-start for joins.
                pts = [
                    {"date": p["date"][:8] + "01", "value": p["value"]} for p in pts
                ]
                # Dedupe after normalize
                by = {p["date"]: p["value"] for p in pts}
                pts = [{"date": d, "value": by[d]} for d in sorted(by)]
                notes.append(
                    f"{key}: TE {spec.get('symbol')} "
                    f"{spec.get('country')}/{spec.get('indicator')} n={len(pts)}"
                )
            elif fmp_packlib.is_fmp_source(src):
                pts, note = fmp_caches.resolve(meta, start=start)
                notes.append(f"{key}: {note}")
            elif src == "ons_revolut":
                sector = str(meta.get("revolut_sector") or "Restaurants")
                pts, mm = revolutlib.fetch_monthly_sector(sector)
                pts = [p for p in pts if p["date"] >= start]
                notes.append(
                    f"{key}: ONS Revolut {sector} SA n={len(pts)} "
                    f"xlsx={mm.get('xlsx_url', '')[-40:]}"
                )
            elif src == "wikipedia_pageviews":
                article = str(meta.get("wiki_article") or "")
                if not article:
                    raise RuntimeError("wiki_article required")
                if article not in wiki_cache:
                    wiki_cache[article] = fetch_wiki_monthly(
                        article, start=wiki_start, end=wiki_end
                    )
                    time.sleep(0.2)
                pts = [p for p in wiki_cache[article] if p["date"] >= start]
                notes.append(f"{key}: wiki {article} n={len(pts)}")
            elif src == "wikipedia_pageviews_basket":
                articles = list(meta.get("wiki_articles") or [])
                by_month: dict[str, float] = {}
                for art in articles:
                    if art not in wiki_cache:
                        try:
                            wiki_cache[art] = fetch_wiki_monthly(
                                art, start=wiki_start, end=wiki_end
                            )
                        except Exception as e:
                            wiki_cache[art] = []
                            notes.append(f"{key}: wiki {art} ERR {e}")
                        time.sleep(0.2)
                    for p in wiki_cache[art]:
                        if p["date"] >= start:
                            by_month[p["date"]] = by_month.get(p["date"], 0.0) + float(
                                p["value"]
                            )
                pts = [{"date": d, "value": v} for d, v in sorted(by_month.items())]
                notes.append(f"{key}: wiki basket {articles} n={len(pts)}")
            elif src == "openfootball_pl":
                pts, mm = ofpl.fetch_monthly_match_counts(
                    start_season=max(2017, start_year),
                    ahead_months=12,
                )
                pts = [p for p in pts if p["date"] >= start]
                notes.append(
                    f"{key}: openfootball PL months={mm.get('n_months')} "
                    f"matches={mm.get('n_matches')} "
                    f"horizon={mm.get('horizon')} "
                    f"seasons={','.join(mm.get('seasons_fetched') or [])}"
                )
            elif src == "openfootball_cl":
                pts, mm = ofcups.fetch_champions_league_monthly(
                    start_season=max(2017, start_year),
                    ahead_months=12,
                )
                pts = [p for p in pts if p["date"] >= start]
                notes.append(
                    f"{key}: openfootball CL months={mm.get('n_months')} "
                    f"matches={mm.get('n_matches')} "
                    f"horizon={mm.get('horizon')} "
                    f"seasons={','.join(mm.get('seasons_fetched') or [])}"
                )
            elif src == "openfootball_wc":
                pts, mm = ofcups.fetch_world_cup_monthly(
                    start_year=max(2018, start_year),
                    ahead_months=12,
                )
                pts = [p for p in pts if p["date"] >= start]
                notes.append(
                    f"{key}: openfootball WC months={mm.get('n_months')} "
                    f"matches={mm.get('n_matches')} "
                    f"horizon={mm.get('horizon')} "
                    f"tournaments={','.join(mm.get('tournaments_fetched') or [])}"
                )
            elif src == "openfootball_euro":
                pts, mm = ofcups.fetch_euro_monthly(
                    start_year=max(2016, start_year),
                    ahead_months=12,
                )
                pts = [p for p in pts if p["date"] >= start]
                notes.append(
                    f"{key}: openfootball Euro months={mm.get('n_months')} "
                    f"matches={mm.get('n_matches')} "
                    f"horizon={mm.get('horizon')} "
                    f"tournaments={','.join(mm.get('tournaments_fetched') or [])}"
                )
            elif src == "six_nations_calendar":
                pts, mm = sixn.fetch_monthly_match_counts(
                    start_year=max(2018, start_year),
                    ahead_months=12,
                )
                pts = [p for p in pts if p["date"] >= start]
                notes.append(
                    f"{key}: Six Nations months={mm.get('n_months')} "
                    f"matches={mm.get('n_matches')} "
                    f"horizon={mm.get('horizon')}"
                )
            elif src == "nflverse_schedule":
                pts, mm = nflsch.fetch_monthly_game_counts(
                    start_year=max(2017, start_year),
                    ahead_months=12,
                )
                pts = [p for p in pts if p["date"] >= start]
                notes.append(
                    f"{key}: nflverse NFL months={mm.get('n_months')} "
                    f"games={mm.get('n_games')} "
                    f"horizon={mm.get('horizon')} "
                    f"bytes={mm.get('bytes')} "
                    f"url={mm.get('url')} "
                    f"SB={','.join(mm.get('super_bowls') or [])[-1:]}"
                )
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

    notes.extend(fmp_caches.notes)

    # Derived sum (UK pubs revenue)
    latest_date = max(
        latest_date,
        apply_derived_sums(
            catalog, series_out, notes=notes, errors=errors, fetched_at=fetched_at
        ),
    )

    # Spreads + ratios
    for meta in catalog.get("series") or []:
        src = meta.get("source")
        key = meta.get("key")
        if not key or src not in ("derived_spread", "derived_ratio"):
            continue
        try:
            left_key = meta["left_series_key"]
            right_key = meta["right_series_key"]
            left_ser = series_out.get(left_key)
            right_ser = series_out.get(right_key)
            if not left_ser or not right_ser:
                raise RuntimeError(f"missing {left_key} or {right_key}")
            if src == "derived_spread":
                left_metric = meta.get("left_metric") or "yoy_pct"
                right_metric = meta.get("right_metric") or "yoy_pct"
                left_pts = (
                    (left_ser.get("derived") or {}).get(left_metric) or {}
                ).get("points") or []
                right_pts = (
                    (right_ser.get("derived") or {}).get(right_metric) or {}
                ).get("points") or []
                pts = tx.series_spread(left_pts, right_pts)
                notes.append(
                    f"{key}: {left_key}.{left_metric} − {right_key}.{right_metric} "
                    f"n={len(pts)}"
                )
            else:
                left_pts = _raw_level_points(left_ser)
                right_pts = _raw_level_points(right_ser)
                scale = float(meta.get("scale") or 100.0)
                pts = tx.series_ratio(left_pts, right_pts, scale=scale)
                notes.append(
                    f"{key}: {left_key}/{right_key}×{scale} n={len(pts)}"
                )
            if not pts:
                raise RuntimeError("no overlapping dates")
            series_out[key] = ps.series_shell(
                meta, points=pts, fetched_at=fetched_at
            )
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
        "source": (
            "ONS IoS/AWE/PPI/duty + Revolut RTI + TE CPI/JP/AU + FMP revenue "
            "+ Wikipedia + sports calendars (PL/CL/WC/Euro/Six Nations/NFL)"
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
    end_year: int,
    dry_run: bool = False,
    skip_r2: bool = False,
) -> dict:
    load_env()
    catalog = _load_json(_CATALOG)
    attachments = _load_attachments()
    payload = build_pack(
        catalog=catalog,
        attachments=attachments,
        start_year=start_year,
        end_year=end_year,
        out_dir=out_dir,
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
    p.add_argument("--end-year", type=int, default=now_y)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-r2", action="store_true")
    args = p.parse_args(argv)

    try:
        payload = run(
            out_dir=args.out_dir,
            start_year=args.start_year,
            end_year=args.end_year,
            dry_run=args.dry_run,
            skip_r2=args.skip_r2,
        )
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1

    summary = {
        "pack": PACK,
        "as_of": payload.get("as_of"),
        "series": list((payload.get("series") or {}).keys()),
        "errors": payload.get("errors") or [],
        "notes": payload.get("notes") or [],
        "r2": payload.get("_r2_uris") or [],
        "theme_scores": payload.get("_theme_scores_detail") or [],
    }
    print(json.dumps(summary, indent=2))
    if payload.get("errors"):
        print(
            f"\nWARN: {len(payload['errors'])} series empty/failed",
            file=sys.stderr,
        )
        for err in payload["errors"]:
            print(f"  - {err}", file=sys.stderr)
    if not args.dry_run:
        print(f"\nwrote {args.out_dir}/latest.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
