#!/usr/bin/env python3
"""Fetch lab-economics pack (Phase A BLS/QCEW + Phase B Census HS trade); publish to R2.

Phase A: PPI supplies/services, CES wages/headcount, ECI, QCEW NAICS 621511 (US + TX).
Phase B: Census HS 382219 import value, kg, and $/kg (3M-avg YoY).

Stores native-frequency raw levels, then model metrics from the catalog.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.env import load_env, require_env  # noqa: E402
from lib import bls as blslib  # noqa: E402
from lib import r2 as r2lib  # noqa: E402
from lib import transforms as tx  # noqa: E402
from lib import census_trade as census  # noqa: E402
from lib import theme_score as scores  # noqa: E402

SCHEMA_VERSION = 0
PACK = "lab_economics"
R2_PREFIX = f"themedata/{PACK}"
UA = "mosaicbot_themedata/0.1 (+lab_economics)"
_DEFAULT_OUT = _ROOT / "data" / "lab_economics"
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "lab_economics_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
HS_LAB_REAGENTS = "382219"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _period_to_date(year: str, period: str, *, frequency: str) -> str | None:
    """Map BLS year+period to ISO date (period start month)."""
    y = int(year)
    if period == "M13":
        return None  # annual average — skip
    if period.startswith("M") and len(period) == 3:
        month = int(period[1:])
        return f"{y:04d}-{month:02d}-01"
    # BLS uses Q01..Q04 (3 chars); accept Q1..Q4 too
    if period.startswith("Q"):
        q = int(period[1:])
        if q not in (1, 2, 3, 4):
            return None
        month = {1: 1, 2: 4, 3: 7, 4: 10}[q]
        return f"{y:04d}-{month:02d}-01"
    if frequency == "quarterly" and period.startswith("M"):
        month = int(period[1:])
        if month not in (1, 4, 7, 10):
            return None
        return f"{y:04d}-{month:02d}-01"
    return None


def fetch_bls_series(
    series_ids: list[str],
    *,
    start_year: int,
    end_year: int,
    api_key: str,
) -> dict[str, list[dict[str, Any]]]:
    """POST BLS v2 with retries; return raw rows keyed by series id."""
    return blslib.fetch_series(
        series_ids,
        start_year=start_year,
        end_year=end_year,
        api_key=api_key,
        user_agent=UA,
    )


def rows_to_points(rows: list[dict[str, Any]], *, frequency: str) -> list[dict[str, float]]:
    pts: list[dict[str, float]] = []
    for row in rows:
        year, period, value = row.get("year"), row.get("period"), row.get("value")
        if not year or not period or value in (None, "", "-"):
            continue
        date = _period_to_date(str(year), str(period), frequency=frequency)
        if not date:
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        pts.append({"date": date, "value": v})
    pts.sort(key=lambda p: p["date"])
    return pts


def _qcew_industry_quarter(year: int, qtr: int, industry: str = "621511") -> list[dict[str, str]]:
    """One quarter of QCEW open-data rows for an industry (all areas)."""
    url = f"https://data.bls.gov/cew/data/api/{year}/{qtr}/industry/{industry}.csv"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise RuntimeError(f"QCEW HTTP {e.code} for {url}") from e
    return list(csv.DictReader(io.StringIO(text)))


def fetch_qcew_multi_area(
    *,
    area_fips_list: list[str],
    industry: str,
    start_year: int,
    end_year: int,
    own_code: str = "5",
) -> dict[str, tuple[list[dict[str, float]], list[dict[str, float]]]]:
    """For each area → (employment points, avg_weekly_wage points).

    Downloads each industry/quarter CSV once (shared across areas).
    """
    out: dict[str, tuple[list[dict[str, float]], list[dict[str, float]]]] = {
        a: ([], []) for a in area_fips_list
    }
    now = datetime.now(timezone.utc)
    for year in range(start_year, end_year + 1):
        for qtr in (1, 2, 3, 4):
            if year == now.year and qtr > ((now.month - 1) // 3) + 1:
                break
            rows = _qcew_industry_quarter(year, qtr, industry=industry)
            time.sleep(0.1)
            if not rows:
                continue
            date = f"{year:04d}-{({1: 1, 2: 4, 3: 7, 4: 10}[qtr]):02d}-01"
            by_area: dict[str, dict[str, str]] = {}
            for r in rows:
                if r.get("own_code") != own_code:
                    continue
                area = r.get("area_fips")
                if area not in out:
                    continue
                if (r.get("disclosure_code") or "").strip() == "N":
                    continue
                prev = by_area.get(area)
                if prev is None or r.get("size_code", "0") == "0":
                    by_area[area] = r
            for area, hit in by_area.items():
                try:
                    m1 = float(hit["month1_emplvl"])
                    m2 = float(hit["month2_emplvl"])
                    m3 = float(hit["month3_emplvl"])
                    wage = float(hit["avg_wkly_wage"])
                except (KeyError, TypeError, ValueError):
                    continue
                emp_list, aww_list = out[area]
                emp_list.append({"date": date, "value": (m1 + m2 + m3) / 3.0})
                aww_list.append({"date": date, "value": wage})
    for area in out:
        emp, aww = out[area]
        emp.sort(key=lambda p: p["date"])
        aww.sort(key=lambda p: p["date"])
        out[area] = (emp, aww)
    return out


def fetch_qcew_area_series(
    *,
    area_fips: str,
    industry: str,
    start_year: int,
    end_year: int,
    own_code: str = "5",
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    multi = fetch_qcew_multi_area(
        area_fips_list=[area_fips],
        industry=industry,
        start_year=start_year,
        end_year=end_year,
        own_code=own_code,
    )
    return multi[area_fips]


def derive_metrics(points: list[dict[str, Any]], derived_names: list[str], *, frequency: str) -> dict[str, Any]:
    lag = 4 if frequency == "quarterly" else 12
    metrics: dict[str, Any] = {}
    for name in derived_names:
        if name == "yoy_pct":
            series = tx.yoy_pct(points, lag=lag)
            metrics[name] = {
                "unit": "pct",
                "points": series,
                "latest": tx.latest(series),
            }
        elif name == "m3_avg_yoy_pct":
            series = tx.rolling_mean_yoy_pct(points, window=3, lag=12)
            metrics[name] = {
                "unit": "pct",
                "points": series,
                "latest": tx.latest(series),
            }
        elif name == "m3_net_change":
            series = tx.lag_diff(points, lag=3)
            metrics[name] = {
                "unit": "level_diff",
                "points": series,
                "latest": tx.latest(series),
            }
        else:
            raise ValueError(f"unknown derived metric: {name}")
    return metrics


def build_pack(
    *,
    start_year: int,
    end_year: int,
    catalog: dict,
    attachments: dict,
) -> dict[str, Any]:
    bls_key = require_env("BLS_API_KEY")["BLS_API_KEY"]
    series_meta = catalog["series"]
    timeseries_meta = [s for s in series_meta if s.get("source") == "bls"]
    qcew_meta = [s for s in series_meta if s.get("source") == "bls_qcew_open"]
    census_meta = [s for s in series_meta if s.get("source") == "census_trade"]

    bls_ids = [s["bls_id"] for s in timeseries_meta]
    raw_by_id: dict[str, list] = {}
    chunk = 20
    for i in range(0, len(bls_ids), chunk):
        part = fetch_bls_series(
            bls_ids[i : i + chunk],
            start_year=start_year,
            end_year=end_year,
            api_key=bls_key,
        )
        raw_by_id.update(part)

    # QCEW open data: one pull per quarter for all areas
    qcew_points: dict[str, list[dict[str, float]]] = {}
    areas = sorted({s["area_fips"] for s in qcew_meta})
    if areas:
        multi = fetch_qcew_multi_area(
            area_fips_list=areas,
            industry="621511",
            start_year=start_year,
            end_year=end_year,
        )
        for area, (emp_pts, aww_pts) in multi.items():
            qcew_points[f"{area}:employment"] = emp_pts
            qcew_points[f"{area}:avg_weekly_wage"] = aww_pts

    # Census HS trade (Phase B)
    census_points: dict[str, list[dict[str, float]]] = {}
    census_notes: list[str] = []
    if census_meta:
        census_key = require_env("CENSUS_API_KEY")["CENSUS_API_KEY"]
        hs = HS_LAB_REAGENTS
        try:
            val_pts, qty_hs6, unit_hs6 = census.fetch_hs6_world_monthly(
                hs, start_year=start_year, end_year=end_year, api_key=census_key
            )
            census_points["import_value_usd"] = val_pts
            kg_pts: list[dict[str, float]] = []
            unit_u = (unit_hs6 or "").upper()
            if qty_hs6 and unit_u in ("KG", "KGS", "KILOGRAM", "KILOGRAMS"):
                kg_pts = qty_hs6
                census_notes.append(f"HS6 qty unit={unit_hs6}")
            else:
                # Quantity in kg is usually only at HS10 — sum children.
                # HS 382219 exists under HS2022+; earlier year chunks return empty.
                kg_pts = census.fetch_hs10_world_kg_monthly(
                    hs, start_year=start_year, end_year=end_year, api_key=census_key
                )
                if kg_pts:
                    census_notes.append(
                        f"kg from sum of HS10 children UNIT_QY1=KG "
                        f"({kg_pts[0]['date']}→{kg_pts[-1]['date']})"
                    )
                else:
                    census_notes.append("HS10 kg: no KG rows returned")
            census_points["import_kg"] = kg_pts
            census_points["import_unit_value_usd_per_kg"] = census.unit_value_usd_per_kg(
                val_pts, kg_pts
            )
        except census.CensusApiError as e:
            census_notes.append(str(e))
            for s in census_meta:
                # leave empty; errors recorded per series below
                pass

    fetched_at = datetime.now(timezone.utc).isoformat()
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    as_of = ""

    for meta in series_meta:
        key = meta["key"]
        sid = meta.get("bls_id") or meta.get("series_id") or key
        freq = meta["frequency"]
        points: list[dict[str, float]] = []

        if meta.get("source") == "bls_qcew_open":
            area = meta["area_fips"]
            metric = meta["qcew_metric"]
            points = qcew_points.get(f"{area}:{metric}", [])
        elif meta.get("source") == "census_trade":
            points = census_points.get(meta["census_metric"], [])
        else:
            rows = raw_by_id.get(sid, [])
            points = rows_to_points(rows, frequency=freq)

        if not points:
            extra = ""
            if meta.get("source") == "census_trade" and census_notes:
                extra = f" ({'; '.join(census_notes)})"
            errors.append(f"{key} ({sid}): no observations{extra}")
            continue
        derived = derive_metrics(points, meta.get("derived") or [], frequency=freq)
        series_out[key] = {
            "bls_id": sid,
            "source": meta.get("source"),
            "label": meta["label"],
            "official_name": meta.get("official_name"),
            "short_name": meta.get("short_name"),
            "display_name": meta.get("display_name"),
            "frequency": freq,
            "seasonal": meta.get("seasonal"),
            "unit": meta["unit"],
            "raw": {
                "points": points,
                "latest": tx.latest(points),
            },
            "derived": derived,
        }
        latest_date = points[-1]["date"]
        if latest_date > as_of:
            as_of = latest_date

    sources = ["BLS Public Data API v2", "QCEW open data"]
    if census_meta:
        sources.append("Census intltrade imports/hs")
    return {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "source": " + ".join(sources),
        "fetched_at": fetched_at,
        "as_of": as_of,
        "start_year": start_year,
        "end_year": end_year,
        "catalog": catalog,
        "attachments": attachments,
        "series": series_out,
        "errors": errors,
        "notes": census_notes,
    }


def _write_local(out_dir: Path, payload: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    raw_dir = out_dir / "raw"
    der_dir = out_dir / "derived"
    raw_dir.mkdir(exist_ok=True)
    der_dir.mkdir(exist_ok=True)
    for key, ser in payload["series"].items():
        (raw_dir / f"{key}.json").write_text(
            json.dumps(
                {
                    "key": key,
                    "bls_id": ser["bls_id"],
                    "label": ser["label"],
                    "official_name": ser.get("official_name"),
                    "short_name": ser.get("short_name"),
                    "display_name": ser.get("display_name"),
                    "frequency": ser["frequency"],
                    "unit": ser["unit"],
                    "points": ser["raw"]["points"],
                    "latest": ser["raw"]["latest"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (der_dir / f"{key}.json").write_text(
            json.dumps(
                {
                    "key": key,
                    "bls_id": ser["bls_id"],
                    "derived": ser["derived"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    (out_dir / "attachments.json").write_text(
        json.dumps(payload["attachments"], indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "catalog.json").write_text(
        json.dumps(payload["catalog"], indent=2) + "\n", encoding="utf-8"
    )


def _publish_r2(payload: dict) -> list[str]:
    uris = []
    uris.append(r2lib.put_json(f"{R2_PREFIX}/latest.json", payload))
    uris.append(r2lib.put_json(f"{R2_PREFIX}/catalog.json", payload["catalog"]))
    uris.append(r2lib.put_json(f"{R2_PREFIX}/attachments.json", payload["attachments"]))
    for key, ser in payload["series"].items():
        uris.append(
            r2lib.put_json(
                f"{R2_PREFIX}/raw/{key}.json",
                {
                    "key": key,
                    "bls_id": ser["bls_id"],
                    "label": ser["label"],
                    "official_name": ser.get("official_name"),
                    "short_name": ser.get("short_name"),
                    "display_name": ser.get("display_name"),
                    "frequency": ser["frequency"],
                    "unit": ser["unit"],
                    "points": ser["raw"]["points"],
                    "latest": ser["raw"]["latest"],
                },
            )
        )
        uris.append(
            r2lib.put_json(
                f"{R2_PREFIX}/derived/{key}.json",
                {"key": key, "bls_id": ser["bls_id"], "derived": ser["derived"]},
            )
        )
    # slim dashboard snapshot
    slim = {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "fetched_at": payload["fetched_at"],
        "as_of": payload["as_of"],
        "metrics": {},
    }
    for key, ser in payload["series"].items():
        slim["metrics"][key] = {
            "raw_latest": ser["raw"]["latest"],
            "derived_latest": {
                name: meta.get("latest") for name, meta in ser["derived"].items()
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
    # Prefer canonical R2 attachments when available; else local seed.
    attachments = _load_json(_ATTACHMENTS)
    try:
        remote = r2lib.get_json("themedata/series_attachments.json")
        if isinstance(remote, dict) and isinstance(remote.get("attachments"), list):
            attachments = remote
    except Exception:
        pass
    payload = build_pack(
        start_year=start_year,
        end_year=end_year,
        catalog=catalog,
        attachments=attachments,
    )

    if dry_run:
        score_result = scores.recompute_and_publish(
            payload,
            scores_dir=_SCORES_OUT,
            skip_r2=True,
            prefer_remote_recipe=True,
        )
        payload["_theme_scores_detail"] = score_result.get("theme_scores") or []
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
        payload["_r2_uris"] = r2_uris
    return payload

def main(argv: list[str] | None = None) -> int:
    now_y = datetime.now(timezone.utc).year
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT)
    p.add_argument("--start-year", type=int, default=now_y - 15)
    p.add_argument("--end-year", type=int, default=now_y)
    p.add_argument("--dry-run", action="store_true", help="fetch+derive only; no writes")
    p.add_argument("--skip-r2", action="store_true", help="write local files only")
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
        "as_of": payload.get("as_of"),
        "fetched_at": payload.get("fetched_at"),
        "series_ok": sorted(payload.get("series", {}).keys()),
        "errors": payload.get("errors") or [],
        "theme_scores": payload.get("theme_scores")
        or [
            {"score_id": s.get("score_id"), "score": s.get("score"), "as_of": s.get("as_of")}
            for s in (payload.get("_theme_scores_detail") or [])
        ],
        "latest_derived": {
            k: {n: m.get("latest") for n, m in v["derived"].items()}
            for k, v in payload.get("series", {}).items()
        },
    }
    if payload.get("_r2_uris"):
        summary["r2_objects"] = len(payload["_r2_uris"])
        summary["r2_prefix"] = f"r2://mosaic-themes/{R2_PREFIX}/"
    print(json.dumps(summary, indent=2))
    if payload.get("errors"):
        print(f"\nWARN: {len(payload['errors'])} series empty/failed", file=sys.stderr)
        for err in payload["errors"]:
            print(f"  - {err}", file=sys.stderr)
        # soft-fail only if everything missing
        if not payload.get("series"):
            return 1
    if not args.dry_run:
        print(f"\nwrote {args.out_dir}/latest.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
