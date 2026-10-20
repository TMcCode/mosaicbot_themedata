#!/usr/bin/env python3
"""Political Spend '26 backdrop pack; publish to R2.

Theme: Political Spend '26: Ad Agencies & Polling
(stockthemes slug: political-spend-26-ad-agencies-polling).

FEC independent expenditures (monthly bulk CSV) + Google Political Ads weekly
US Spend_USD + FRED advertising agency PPI + NAICS 5418 revenue + U. Michigan
consumer sentiment + Kalshi midterm control odds + close-race counts (chart-only).
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
from lib import fec_ie as feclib  # noqa: E402
from lib import google_political_ads as gpalib  # noqa: E402
from lib import kalshi as kalshilib  # noqa: E402
from lib import theme_score as scores  # noqa: E402

SCHEMA_VERSION = 0
PACK = "political_spend"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "political_spend_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
_CLOSE_HISTORY = "kalshi_close_races_history.jsonl"
_GOOGLE_CACHE = "google_political_ads_bundle.zip"


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


def _load_close_history(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    by: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        d = str(row.get("date") or "")
        if d:
            by[d] = row
    return by


def _write_close_history(path: Path, by: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [by[k] for k in sorted(by)]
    path.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf-8",
    )


def _close_race_points(
    history: dict[str, dict[str, Any]], *, field: str
) -> list[dict[str, float]]:
    pts: list[dict[str, float]] = []
    for d in sorted(history):
        row = history[d]
        if field not in row or row[field] is None:
            continue
        pts.append({"date": d, "value": float(row[field])})
    return pts


def build_pack(
    *,
    catalog: dict,
    attachments: dict,
    start_year: int,
    out_dir: Path,
    skip_close_snapshot: bool = False,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    start = f"{start_year:04d}-01-01"
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    notes: list[str] = []
    latest_date = ""

    fec_points: list[dict[str, float]] | None = None
    fec_meta: list[dict[str, Any]] = []
    need_fec = any(
        m.get("source") == "fec_ie" for m in (catalog.get("series") or [])
    )
    if need_fec:
        try:
            fec_points, fec_meta = feclib.monthly_points(
                cycles=(2022, 2024, 2026),
                start=start,
            )
            notes.append(
                "fec_ie: cycles="
                + ",".join(str(m.get("cycle")) for m in fec_meta)
                + f" n_points={len(fec_points)}"
            )
            for m in fec_meta:
                notes.append(
                    f"fec_ie cycle={m.get('cycle')} raw={m.get('raw_rows')} "
                    f"dedup={m.get('deduped_rows')} drop_hi={m.get('dropped_over_max_txn')}"
                )
        except Exception as e:
            errors.append(f"fec_ie: {e}")
            fec_points = None

    google_points: list[dict[str, float]] | None = None
    google_meta: dict[str, Any] = {}
    need_google = any(
        m.get("source") == "google_political_ads"
        for m in (catalog.get("series") or [])
    )
    if need_google:
        try:
            google_points, google_meta = gpalib.weekly_us_spend_points(
                start=start,
                cache_path=out_dir / _GOOGLE_CACHE,
            )
            notes.append(
                "google_political_ads: weeks="
                f"{google_meta.get('weeks')} last={google_meta.get('last')} "
                f"cache={google_meta.get('cache')}"
            )
        except Exception as e:
            errors.append(f"google_political_ads: {e}")
            google_points = None

    close_hist_path = out_dir / _CLOSE_HISTORY
    close_by = _load_close_history(close_hist_path)
    need_close = any(
        m.get("source") == "kalshi_close_races" for m in (catalog.get("series") or [])
    )
    close_snap: dict[str, Any] | None = None
    if need_close and not skip_close_snapshot:
        try:
            close_snap = kalshilib.snapshot_close_federal_races()
            d = str(close_snap["date"])
            close_by[d] = {
                "date": d,
                "senate_close": close_snap["senate_close"],
                "house_close": close_snap["house_close"],
                "federal_close": close_snap["federal_close"],
                "senate_scanned": close_snap["senate_scanned"],
                "house_scanned": close_snap["house_scanned"],
                "lo": close_snap["lo"],
                "hi": close_snap["hi"],
            }
            _write_close_history(close_hist_path, close_by)
            notes.append(
                f"kalshi_close_races: senate={close_snap['senate_close']}/"
                f"{close_snap['senate_scanned']} house={close_snap['house_close']}/"
                f"{close_snap['house_scanned']} (35–65¢ band)"
            )
        except Exception as e:
            errors.append(f"kalshi_close_races: {e}")
    elif need_close and skip_close_snapshot:
        notes.append("kalshi_close_races: skipped snapshot (history-only)")

    for meta in catalog.get("series") or []:
        key = meta.get("key")
        if not key:
            continue
        src = meta.get("source")
        try:
            if src == "fred":
                sid = meta["fred_id"]
                pts = [
                    {"date": d, "value": v}
                    for d, v in fredlib.fetch_series(sid)
                    if d >= start
                ]
                notes.append(f"{key}: FRED {sid} n={len(pts)}")
            elif src == "fec_ie":
                if fec_points is None:
                    raise RuntimeError("FEC IE bulk unavailable")
                pts = [p for p in fec_points if p["date"] >= start]
                notes.append(f"{key}: FEC IE monthly n={len(pts)}")
            elif src == "google_political_ads":
                if google_points is None:
                    raise RuntimeError("Google Political Ads bundle unavailable")
                pts = [p for p in google_points if p["date"] >= start]
                notes.append(f"{key}: Google weekly Spend_USD n={len(pts)}")
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
                    f"{key}: Kalshi {ticker} n={len(pts)} last={mm.get('last_price')}"
                )
            elif src == "kalshi_close_races":
                field = str(meta.get("close_field") or "federal_close")
                pts = _close_race_points(close_by, field=field)
                pts = [p for p in pts if p["date"] >= start]
                notes.append(f"{key}: close-race history field={field} n={len(pts)}")
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

    if not series_out:
        raise RuntimeError("no series built: " + "; ".join(errors))

    return {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "source": (
            "FEC IE (bulk) + Google Political Ads weekly Spend_USD + "
            "FRED ad PPI / 5418 revenue / UMCSENT + "
            "Kalshi midterm control + close-race counts"
        ),
        "fetched_at": fetched_at,
        "as_of": latest_date,
        "catalog": catalog,
        "attachments": attachments,
        "series": series_out,
        "errors": errors,
        "notes": notes,
        "fec_ie_meta": fec_meta,
        "google_political_ads_meta": google_meta or None,
        "kalshi_close_snapshot": {
            k: close_snap[k]
            for k in (
                "date",
                "senate_close",
                "house_close",
                "federal_close",
                "senate_scanned",
                "house_scanned",
            )
            if close_snap and k in close_snap
        }
        if close_snap
        else None,
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
    skip_close_snapshot: bool = False,
) -> dict:
    load_env()
    catalog = _load_json(_CATALOG)
    attachments = _load_attachments()
    payload = build_pack(
        catalog=catalog,
        attachments=attachments,
        start_year=start_year,
        out_dir=out_dir,
        skip_close_snapshot=skip_close_snapshot,
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
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT)
    p.add_argument("--start-year", type=int, default=2018)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-r2", action="store_true")
    p.add_argument(
        "--skip-close-snapshot",
        action="store_true",
        help="Reuse kalshi_close_races_history.jsonl without rescanning race markets",
    )
    args = p.parse_args(argv)

    try:
        payload = run(
            out_dir=args.out_dir,
            start_year=args.start_year,
            dry_run=args.dry_run,
            skip_r2=args.skip_r2,
            skip_close_snapshot=args.skip_close_snapshot,
        )
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    n = len(payload.get("series") or {})
    print(
        f"OK pack={PACK} series={n} as_of={payload.get('as_of')} "
        f"errors={len(payload.get('errors') or [])}"
    )
    for err in payload.get("errors") or []:
        print(f"  warn: {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
