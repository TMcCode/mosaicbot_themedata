#!/usr/bin/env python3
"""Home improvement retailers backdrop pack; publish to R2.

Theme: Buying a House '26: Home Improvement Retailers
(stockthemes slug: buying-a-house-26-home-improvement-retailers).

Housing turnover (existing home sales) + NAHB/Wells Fargo HMI (builder
sentiment scrape) + mortgage rates + NAICS 444 retail sales + housing
starts/permits + lumber PPI + SE USDM drought (ticker-scoped).
Forums live in home_improvement_forums.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.env import load_env  # noqa: E402
from lib import r2 as r2lib  # noqa: E402
from lib import pack_series as ps  # noqa: E402
from lib import fred as fredlib  # noqa: E402
from lib import usdm as usdm_lib  # noqa: E402
from lib import nahb_hmi as nahb_lib  # noqa: E402
from lib import theme_score as scores  # noqa: E402
from lib import fmp_pack as fmp_packlib  # noqa: E402

SCHEMA_VERSION = 0
PACK = "home_improvement"
R2_PREFIX = f"themedata/{PACK}"
UA = "mosaicbot_themedata/0.1 (+home_improvement)"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "home_improvement_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
_EXHOS_ARCHIVE = _ROOT / "config" / "seeds" / "existing_home_sales_archive.csv"


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


def _fred_csv(series_id: str) -> list[tuple[str, float]]:
    return fredlib.fetch_series(series_id)


def _load_exhos_archive() -> list[tuple[str, float]]:
    """Provisional deep history (NAR SAAR). Live FRED only redistributes ~13 months."""
    if not _EXHOS_ARCHIVE.exists():
        return []
    rows: list[tuple[str, float]] = []
    with _EXHOS_ARCHIVE.open(encoding="utf-8") as f:
        # skip # comment lines before DictReader
        body = "\n".join(
            line for line in f.read().splitlines() if line and not line.startswith("#")
        )
    for row in csv.DictReader(io.StringIO(body)):
        d = (row.get("date") or "")[:10]
        v = row.get("value")
        if not d or v in (None, ""):
            continue
        rows.append((d, float(v)))
    rows.sort(key=lambda x: x[0])
    return rows


def _merge_exhos(
    fred_pts: list[dict[str, float]],
    archive: list[tuple[str, float]],
    *,
    start: str,
) -> tuple[list[dict[str, float]], str]:
    """Archive fills pre-FRED window; FRED wins on overlapping dates."""
    by: dict[str, float] = {}
    for d, v in archive:
        if d >= start:
            by[d] = v
    for p in fred_pts:
        by[str(p["date"])[:10]] = float(p["value"])
    pts = [{"date": d, "value": by[d]} for d in sorted(by)]
    note = (
        f"existing_home_sales: FRED live {len(fred_pts)} pts + archive "
        f"{len(archive)} rows → {len(pts)} merged (FRED wins overlap; "
        "archive = provisional NAR reconstruction — murky redistribute)"
    )
    return pts, note


def build_pack(
    *,
    catalog: dict,
    attachments: dict,
    start_year: int,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    start = f"{start_year:04d}-01-01"
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    notes: list[str] = []
    latest_date = ""
    exhos_archive = _load_exhos_archive()
    fmp_caches = fmp_packlib.FmpCaches.from_catalog(catalog)

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
                    for d, v in _fred_csv(sid)
                    if d >= start
                ]
                if key == "existing_home_sales" and exhos_archive:
                    pts, merge_note = _merge_exhos(pts, exhos_archive, start=start)
                    notes.append(merge_note)
                else:
                    notes.append(f"{key}: FRED {sid} ({len(pts)} pts)")
            elif src == "usdm":
                end = date.today()
                start_d = date(start_year, 1, 1)
                rows = usdm_lib.fetch_region_mean_d1_or_worse(
                    usdm_lib.SE_STATE_FIPS, start=start_d, end=end
                )
                pts = [{"date": d, "value": v} for d, v in rows if d >= start]
                notes.append(
                    f"{key}: USDM SE D1+ mean over {len(usdm_lib.SE_STATE_FIPS)} "
                    f"states ({len(pts)} pts)"
                )
            elif src == "nahb_hmi":
                pts, mm = nahb_lib.scrape_national_hmi(start=start)
                notes.append(
                    f"{key}: NAHB HMI Table 2 n={mm.get('n')} "
                    f"last={((mm.get('latest') or {}).get('date'))} "
                    f"xls={mm.get('history_xls')}"
                )
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

    if not series_out:
        raise RuntimeError("no series built: " + "; ".join(errors))

    return {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "source": (
            "FRED (housing turnover + home improvement retail) + "
            "NAHB/Wells Fargo HMI + USDM SE drought"
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
            json.dumps({"key": key, "derived": ser.get("derived")}, indent=2)
            + "\n",
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
) -> dict:
    load_env()
    catalog = _load_json(_CATALOG)
    attachments = _load_attachments()
    payload = build_pack(
        catalog=catalog,
        attachments=attachments,
        start_year=start_year,
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
    p.add_argument("--start-year", type=int, default=2015)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-r2", action="store_true")
    args = p.parse_args(argv)

    try:
        payload = run(
            out_dir=args.out_dir,
            start_year=args.start_year,
            dry_run=args.dry_run,
            skip_r2=args.skip_r2,
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
        "theme_scores": payload.get("_theme_scores_detail")
        or payload.get("theme_scores"),
        "r2": payload.get("_r2_uris") or [],
        "notes": payload.get("notes") or [],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
