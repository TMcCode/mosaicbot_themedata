#!/usr/bin/env python3
"""Drones '25: Ag Drones backdrop pack; publish to R2.

Theme: Drones '25: Ag Drones (stockthemes slug: drones-25-ag-drones).

FRED farm prices/PPI + AEM tractor/combine retail + FAA Part 137 operator
counts (v-1x) + Census HS 8806 + FMP OEM segments/revenue + USASpending
precision-ag keywords. Forums live in ag_drones_forums.
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
from lib import usaspending as usa  # noqa: E402
from lib import census_trade as census  # noqa: E402
from lib import aem_tractor_combine as aemlib  # noqa: E402
from lib import faa_part137 as p137lib  # noqa: E402
from lib import theme_score as scores  # noqa: E402
from lib import fmp_pack as fmp_packlib  # noqa: E402

SCHEMA_VERSION = 0
PACK = "ag_drones"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "ag_drones_catalog.json"
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
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    start = f"{start_year:04d}-01-01"
    end_year = datetime.now(timezone.utc).year
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    notes: list[str] = []
    latest_date = ""
    fmp_caches = fmp_packlib.FmpCaches.from_catalog(catalog)

    census_key = ""
    if any((m.get("source") == "census_trade") for m in (catalog.get("series") or [])):
        census_key = require_env("CENSUS_API_KEY")["CENSUS_API_KEY"]
    census_cache: dict[str, tuple[list, list, str | None]] = {}

    aem_bundle: dict[str, list[dict[str, float]]] | None = None
    aem_meta: dict[str, Any] | None = None
    aem_hist = out_dir / aemlib.HISTORY_NAME

    p137_bundle: dict[str, list[dict[str, float]]] | None = None
    p137_meta: dict[str, Any] | None = None
    p137_hist = out_dir / p137lib.HISTORY_NAME

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
            elif src == "aem_tractor_combine":
                if aem_bundle is None:
                    aem_bundle, aem_meta = aemlib.refresh_bundle(
                        history_path=aem_hist, start=start
                    )
                    notes.append(
                        "aem: "
                        + "; ".join((aem_meta.get("scrape_notes") or [])[:3])
                    )
                metric = str(meta.get("aem_metric") or "")
                pts = (aem_bundle or {}).get(metric) or []
                if not pts:
                    raise RuntimeError(f"aem metric {metric!r} empty")
            elif src == "faa_part137":
                if p137_bundle is None:
                    p137_bundle, p137_meta = p137lib.refresh_bundle(
                        history_path=p137_hist, start=start
                    )
                    latest = (p137_meta or {}).get("latest") or {}
                    notes.append(
                        f"part137: total={latest.get('total')} "
                        f"drone_named={latest.get('drone_named')} "
                        f"snapshots={(p137_meta or {}).get('n_snapshots')}"
                    )
                metric = str(meta.get("part137_metric") or "")
                pts = (p137_bundle or {}).get(metric) or []
                if not pts:
                    raise RuntimeError(f"part137 metric {metric!r} empty")
            elif src == "census_trade":
                hs = str(meta.get("hs6") or "")
                # Census often has no world total at parent HS6 8806 — sum children.
                hs_list = [
                    str(x)
                    for x in (
                        meta.get("hs6_sum")
                        or ([hs] if hs else [])
                    )
                    if str(x).strip()
                ]
                if not hs_list:
                    raise RuntimeError("hs6 or hs6_sum required")
                by_date: dict[str, float] = {}
                units: list[str] = []
                for code in hs_list:
                    if code not in census_cache:
                        census_cache[code] = census.fetch_hs6_world_monthly(
                            code,
                            start_year=max(start_year, 2022),
                            end_year=end_year,
                            api_key=census_key,
                        )
                    val_pts, qty_pts, unit = census_cache[code]
                    field = str(meta.get("field") or "value")
                    use = qty_pts if field == "qty" else val_pts
                    if unit:
                        units.append(str(unit))
                    for p in use:
                        d = str(p["date"])
                        if d < start:
                            continue
                        by_date[d] = by_date.get(d, 0.0) + float(p["value"])
                pts = [
                    {"date": d, "value": v} for d, v in sorted(by_date.items())
                ]
                notes.append(
                    f"{key}: Census HS{'+'.join(hs_list)} "
                    f"field={meta.get('field') or 'value'} n={len(pts)} "
                    f"units={sorted(set(units)) or None}"
                )
            elif src == "usaspending":
                recipient = meta.get("recipient")
                keywords = list(meta.get("keywords") or [])
                award_types = meta.get("award_type_codes")
                program_numbers = meta.get("program_numbers")
                if recipient:
                    pts = usa.fetch_monthly_obligations(
                        recipient_search_text=[str(recipient)],
                        award_type_codes=award_types,
                        start_date=start,
                    )
                    notes.append(
                        f"{key}: USASpending recipient={recipient!r} n={len(pts)}"
                    )
                else:
                    pts = usa.fetch_monthly_obligations(
                        keywords=keywords or None,
                        award_type_codes=award_types,
                        program_numbers=program_numbers,
                        start_date=start,
                    )
                    notes.append(
                        f"{key}: USASpending keywords={keywords or None} n={len(pts)}"
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
            "FRED farm products/machinery/chemicals PPI + crop prices + "
            "AEM tractor/combine retail PDF + v-1x Part 137 roster + "
            "Census HS 8806 + FMP OEM segments/revenue + USASpending precision-ag"
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
    # Persist scrape histories alongside pack so VPS restarts don't lose
    # append-only AEM / Part 137 snapshots.
    for hist_name in (aemlib.HISTORY_NAME, p137lib.HISTORY_NAME):
        hist_path = _DEFAULT_OUT / hist_name
        # Prefer the out_dir used in this run if present via payload path — caller
        # writes local first; re-read from default after _write_local.
        try:
            local = Path(payload.get("_out_dir") or _DEFAULT_OUT) / hist_name
            if local.exists():
                data = json.loads(local.read_text(encoding="utf-8"))
                uris.append(r2lib.put_json(f"{R2_PREFIX}/{hist_name}", data))
        except Exception:
            pass
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
        out_dir=out_dir,
    )
    payload["_out_dir"] = str(out_dir)

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
