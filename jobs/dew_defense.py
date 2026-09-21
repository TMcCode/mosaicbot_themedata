#!/usr/bin/env python3
"""Directed-energy / modern-warfare backdrop pack; publish to R2.

Sources: FRED defense spend, Caldara–Iacoviello GPR, USASpending DEW keywords,
Census HS Ge/Ga imports, USASpending LightPath awards.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.env import load_env, require_env  # noqa: E402
from lib import r2 as r2lib  # noqa: E402
from lib import pack_series as ps  # noqa: E402
from lib import gpr as gprlib  # noqa: E402
from lib import usaspending as usa  # noqa: E402
from lib import census_trade as census  # noqa: E402
from lib import usgs_mcs  # noqa: E402
from lib import tradingeconomics_ge as te  # noqa: E402
from lib import manifold as manifoldlib  # noqa: E402
from lib import theme_score as scores  # noqa: E402

SCHEMA_VERSION = 0
PACK = "dew_defense"
R2_PREFIX = f"themedata/{PACK}"
R2_SCORES_PREFIX = "themedata/scores"
UA = "mosaicbot_themedata/0.1 (+dew_defense)"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "dew_defense_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
_THEME_SCORES = _ROOT / "config" / "theme_scores.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fred_csv(series_id: str) -> list[tuple[str, float]]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=60) as resp:
        raw = resp.read()
    if raw[:15].lower().startswith(b"<!doctype") or b"<html" in raw[:200].lower():
        raise RuntimeError(f"FRED returned HTML for {series_id}")
    rows: list[tuple[str, float]] = []
    for row in csv.DictReader(io.StringIO(raw.decode())):
        d = row.get("observation_date") or row.get("DATE")
        v = row.get(series_id)
        if not d or v in (None, "", "."):
            continue
        rows.append((d, float(v)))
    return rows


def _load_attachments() -> dict:
    attachments = _load_json(_ATTACHMENTS) if _ATTACHMENTS.exists() else {"attachments": []}
    try:
        remote = r2lib.get_json("themedata/series_attachments.json")
        if isinstance(remote, dict) and isinstance(remote.get("attachments"), list):
            attachments = remote
    except Exception:
        pass
    return attachments


def _ge_spot_points(out_dir: Path) -> list[dict[str, Any]]:
    """Build TE germanium points from local + R2 history; best-effort scrape append."""
    try:
        te.scrape_and_upsert(out_dir)
    except Exception:
        pass

    rows = te.load_history(out_dir)
    if not rows:
        try:
            raw = r2lib.get_bytes(te.R2_HISTORY_KEY)
            if raw:
                rows = te.rows_from_r2_body(raw)
                if rows:
                    te.history_path(out_dir).parent.mkdir(parents=True, exist_ok=True)
                    te.history_path(out_dir).write_text(
                        te.history_to_r2_body(rows), encoding="utf-8"
                    )
        except Exception:
            rows = []
    return te.points_from_history(rows)


def build_pack(
    *,
    catalog: dict,
    attachments: dict,
    start_year: int,
    end_year: int,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    start = f"{start_year:04d}-01-01"
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    notes: list[str] = []
    latest_date = ""
    ge_dir = out_dir or _DEFAULT_OUT

    census_key = None
    for meta in catalog.get("series") or []:
        if meta.get("source") == "census_trade":
            census_key = require_env("CENSUS_API_KEY")["CENSUS_API_KEY"]
            break

    for meta in catalog.get("series") or []:
        key = meta.get("key")
        if not key:
            continue
        src = meta.get("source")
        try:
            pts: list[dict[str, Any]] = []
            if src == "fred":
                sid = meta["fred_id"]
                pts = [{"date": d, "value": v} for d, v in _fred_csv(sid) if d >= start]
            elif src == "gpr":
                pts = gprlib.fetch_gpr_monthly(start=start)
            elif src == "usaspending":
                if meta.get("recipient"):
                    pts = usa.fetch_monthly_obligations(
                        recipient_search_text=[str(meta["recipient"])],
                        start_date=start,
                    )
                else:
                    pts = usa.fetch_monthly_obligations(
                        keywords=list(usa.DEFAULT_DEW_KEYWORDS),
                        start_date=start,
                    )
            elif src == "census_trade":
                hs = str(meta.get("hs6") or "")
                val_pts, _qty, unit = census.fetch_hs6_world_monthly(
                    hs,
                    start_year=start_year,
                    end_year=end_year,
                    api_key=census_key or "",
                )
                pts = val_pts
                if unit:
                    notes.append(f"{key} qty_unit={unit}")
            elif src == "usgs_mcs":
                pts = usgs_mcs.fetch_germanium_metal_price_annual(
                    start_year=start_year,
                    end_year=end_year,
                )
            elif src == "tradingeconomics":
                pts = _ge_spot_points(ge_dir)
                notes.append(
                    f"{key}: Trading Economics CFD proxy (weekly scrape / history append)"
                )
                # Keep R2 history in sync when monthly pack also scraped
                hist = te.load_history(ge_dir)
                if hist:
                    try:
                        r2lib.put_bytes(
                            te.R2_HISTORY_KEY,
                            te.history_to_r2_body(hist).encode("utf-8"),
                            content_type="application/x-ndjson",
                        )
                        latest = hist[-1]
                        r2lib.put_json(f"{R2_PREFIX}/{te.LATEST_NAME}", latest)
                    except Exception as e:
                        notes.append(f"{key} history R2 sync skipped: {e}")
            elif src == "manifold":
                mid = str(meta.get("manifold_id") or "")
                if not mid:
                    raise RuntimeError("manifold_id required")
                pts, mm = manifoldlib.fetch_binary_daily_probability(mid)
                pts = [p for p in pts if p["date"] >= start]
                notes.append(
                    f"{key}: Manifold {mid} vol={mm.get('volume')} "
                    f"bettors={mm.get('unique_bettors')} url={mm.get('url')}"
                )
            else:
                raise RuntimeError(f"unknown source {src}")

            if not pts:
                raise RuntimeError("no points")
            series_out[key] = ps.series_shell(meta, points=pts)
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
        "source": "FRED + GPR + USASpending + Census trade + USGS MCS + Trading Economics + Manifold",
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


def _publish_r2(payload: dict) -> list[str]:
    uris = [
        r2lib.put_json(f"{R2_PREFIX}/latest.json", payload),
        r2lib.put_json(f"{R2_PREFIX}/catalog.json", payload["catalog"]),
        r2lib.put_json(f"{R2_PREFIX}/attachments.json", payload["attachments"]),
    ]
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
                name: meta.get("latest")
                for name, meta in (ser.get("derived") or {}).items()
            },
        }
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

    theme_scores: list[dict] = []
    scores_index_doc: dict | None = None
    scores_cfg = None
    try:
        remote_recipe = r2lib.get_json("themedata/scores/recipe.json")
        if isinstance(remote_recipe, dict) and isinstance(
            remote_recipe.get("themes"), list
        ):
            scores_cfg = remote_recipe
    except Exception:
        scores_cfg = None
    if scores_cfg is None and _THEME_SCORES.exists():
        scores_cfg = _load_json(_THEME_SCORES)
    if scores_cfg:
        theme_scores = scores.score_themes_for_pack(scores_cfg, pack_payload=payload)
        existing = None
        try:
            existing = r2lib.get_json(f"{R2_SCORES_PREFIX}/latest.json")
        except Exception:
            existing = None
        if existing is None and (_SCORES_OUT / "latest.json").exists():
            existing = _load_json(_SCORES_OUT / "latest.json")
        if theme_scores:
            scores_index_doc = scores.merge_scores_index(existing, theme_scores)
            payload["theme_scores"] = [
                {
                    "score_id": s.get("score_id"),
                    "theme": s.get("theme"),
                    "score": s.get("score"),
                    "as_of": s.get("as_of"),
                }
                for s in theme_scores
            ]

    if dry_run:
        return payload

    _write_local(out_dir, payload)
    if scores_index_doc is not None:
        _SCORES_OUT.mkdir(parents=True, exist_ok=True)
        themes_dir = _SCORES_OUT / "themes"
        themes_dir.mkdir(exist_ok=True)
        (_SCORES_OUT / "latest.json").write_text(
            json.dumps(scores_index_doc, indent=2) + "\n", encoding="utf-8"
        )
        for s in theme_scores:
            slug = s.get("theme_slug") or "unknown"
            (themes_dir / f"{slug}.json").write_text(
                json.dumps(s, indent=2) + "\n", encoding="utf-8"
            )

    r2_uris: list[str] = []
    if not skip_r2:
        r2_uris.extend(_publish_r2(payload))
        if scores_index_doc is not None:
            r2_uris.append(
                r2lib.put_json(f"{R2_SCORES_PREFIX}/latest.json", scores_index_doc)
            )
            for s in theme_scores:
                slug = s.get("theme_slug") or "unknown"
                r2_uris.append(
                    r2lib.put_json(f"{R2_SCORES_PREFIX}/themes/{slug}.json", s)
                )
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
        "series": list(payload.get("series") or {}),
        "errors": payload.get("errors") or [],
        "r2": payload.get("_r2_uris") or [],
    }
    print(json.dumps(summary, indent=2))
    if not args.dry_run:
        print(f"\nwrote {args.out_dir}/latest.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
