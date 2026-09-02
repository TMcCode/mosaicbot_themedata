#!/usr/bin/env python3
"""Fetch lab-economics BLS series (Phase A); derive YoY metrics; publish to R2.

Series: PPI supplies/services, CES wages/headcount, ECI compensation,
QCEW NAICS 621511 (US theme + TX ticker proxy for CSTL).

Stores native-frequency raw levels, then model metrics from the catalog
(m3_avg_yoy_pct, yoy_pct, m3_net_change).
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
from lib import r2 as r2lib  # noqa: E402
from lib import transforms as tx  # noqa: E402

SCHEMA_VERSION = 0
PACK = "lab_economics"
R2_PREFIX = f"themedata/{PACK}"
UA = "mosaicbot_themedata/0.1 (+lab_economics_bls)"
BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
_DEFAULT_OUT = _ROOT / "data" / "lab_economics"
_CATALOG = _ROOT / "config" / "lab_economics_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"


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
    """POST BLS v2; return {series_id: [{year, period, value, footnotes}, ...]} raw rows."""
    payload = {
        "seriesid": series_ids,
        "startyear": str(start_year),
        "endyear": str(end_year),
        "registrationkey": api_key,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BLS_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"BLS HTTP {e.code}: {detail}") from e

    if raw.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS status={raw.get('status')!r} message={raw.get('message')!r}")

    out: dict[str, list[dict[str, Any]]] = {}
    for series in raw.get("Results", {}).get("series", []):
        sid = series.get("seriesID")
        rows = []
        for row in series.get("data", []):
            rows.append(
                {
                    "year": row.get("year"),
                    "period": row.get("period"),
                    "periodName": row.get("periodName"),
                    "value": row.get("value"),
                    "footnotes": row.get("footnotes") or [],
                }
            )
        out[sid] = rows
        msgs = series.get("message") or []
        if msgs and not rows:
            # keep empty; caller may soft-fail per series
            out[sid] = []
    return out


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
    api_key = require_env("BLS_API_KEY")["BLS_API_KEY"]
    series_meta = catalog["series"]
    timeseries_meta = [s for s in series_meta if s.get("source") == "bls"]
    qcew_meta = [s for s in series_meta if s.get("source") == "bls_qcew_open"]

    bls_ids = [s["bls_id"] for s in timeseries_meta]
    raw_by_id: dict[str, list] = {}
    chunk = 20
    for i in range(0, len(bls_ids), chunk):
        part = fetch_bls_series(
            bls_ids[i : i + chunk],
            start_year=start_year,
            end_year=end_year,
            api_key=api_key,
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

    fetched_at = datetime.now(timezone.utc).isoformat()
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    as_of = ""

    for meta in series_meta:
        key = meta["key"]
        sid = meta["bls_id"]
        freq = meta["frequency"]
        points: list[dict[str, float]] = []

        if meta.get("source") == "bls_qcew_open":
            area = meta["area_fips"]
            metric = meta["qcew_metric"]
            points = qcew_points.get(f"{area}:{metric}", [])
        else:
            rows = raw_by_id.get(sid, [])
            points = rows_to_points(rows, frequency=freq)

        if not points:
            errors.append(f"{key} ({sid}): no observations")
            continue
        derived = derive_metrics(points, meta.get("derived") or [], frequency=freq)
        series_out[key] = {
            "bls_id": sid,
            "label": meta["label"],
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

    return {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "source": "BLS Public Data API v2 + QCEW open data",
        "fetched_at": fetched_at,
        "as_of": as_of,
        "start_year": start_year,
        "end_year": end_year,
        "catalog": catalog,
        "attachments": attachments,
        "series": series_out,
        "errors": errors,
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
    attachments = _load_json(_ATTACHMENTS)
    payload = build_pack(
        start_year=start_year,
        end_year=end_year,
        catalog=catalog,
        attachments=attachments,
    )
    if dry_run:
        return payload
    _write_local(out_dir, payload)
    if not skip_r2:
        payload["_r2_uris"] = _publish_r2(payload)
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
