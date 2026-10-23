#!/usr/bin/env python3
"""Fashion Styles '24: Streetwear, Sportstyle & Sneaker Culture pack.

Theme slug: fashion-styles-24-streetwear-sportstyle-sneaker-culture.

FRED shoe/sporting-goods + footwear CPI + cotton/rubber PPI + Census HS
footwear + FMP USD members + StockX hero-SKU Chrome scrape (weekly append).
Forums in streetwear_sneakers_forums.
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
from lib import bls as blslib  # noqa: E402
from lib import census_trade as census  # noqa: E402
from lib import fmp_pack as fmp_packlib  # noqa: E402
from lib import stockx as stockxlib  # noqa: E402
from lib import theme_score as scores  # noqa: E402


def _period_to_date(year: str, period: str) -> str | None:
    y = int(year)
    if period == "M13":
        return None
    if period.startswith("M") and len(period) == 3:
        return f"{y:04d}-{int(period[1:]):02d}-01"
    return None


def _bls_rows_to_points(rows: list[dict[str, Any]]) -> list[dict[str, float]]:
    pts: list[dict[str, float]] = []
    for row in rows:
        year, period, value = row.get("year"), row.get("period"), row.get("value")
        if not year or not period or value in (None, "", "-"):
            continue
        d = _period_to_date(str(year), str(period))
        if not d:
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        pts.append({"date": d, "value": v})
    pts.sort(key=lambda p: p["date"])
    return pts

SCHEMA_VERSION = 0
PACK = "streetwear_sneakers"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "streetwear_sneakers_catalog.json"
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
    skip_stockx: bool = False,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    start = f"{start_year:04d}-01-01"
    end_year = datetime.now(timezone.utc).year
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    notes: list[str] = []

    census_key = ""
    if any(
        (m.get("source") == "census_trade") for m in (catalog.get("series") or [])
    ):
        census_key = require_env("CENSUS_API_KEY")["CENSUS_API_KEY"]

    census_cache: dict[str, tuple[list, list, str | None]] = {}
    fmp_caches = fmp_packlib.FmpCaches.from_catalog(catalog)

    bls_ids = [
        str(m["bls_id"])
        for m in (catalog.get("series") or [])
        if m.get("source") == "bls" and m.get("bls_id")
    ]
    bls_raw: dict[str, list[dict[str, Any]]] = {}
    if bls_ids:
        bls_key = require_env("BLS_API_KEY")["BLS_API_KEY"]
        for y0 in range(start_year, end_year + 1, 20):
            y1 = min(y0 + 19, end_year)
            chunk = blslib.fetch_series(
                bls_ids, start_year=y0, end_year=y1, api_key=bls_key
            )
            for sid, rows in chunk.items():
                bls_raw.setdefault(sid, []).extend(rows)
        notes.append(f"bls: fetched {sorted(bls_raw.keys())}")

    stockx_history_path = out_dir / "stockx_hero_history.jsonl"
    stockx_meta: dict[str, Any] = {}
    if not skip_stockx and any(
        m.get("source") == "stockx_hero_basket" for m in (catalog.get("series") or [])
    ):
        try:
            snaps, stockx_meta = stockxlib.scrape_hero_basket(
                history_path=stockx_history_path
            )
            notes.append(
                f"stockx: chrome={stockx_meta.get('chrome')} "
                f"ok={stockx_meta.get('n_ok')} "
                f"week={stockx_meta.get('week_ending')} "
                f"errors={len(stockx_meta.get('errors') or [])}"
            )
            for e in (stockx_meta.get("errors") or [])[:8]:
                notes.append(f"stockx warn: {e}")
            if snaps:
                (out_dir / "stockx_latest_snapshot.json").write_text(
                    json.dumps(
                        {"fetched_at": fetched_at, "meta": stockx_meta, "skus": snaps},
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
        except Exception as e:
            errors.append(f"stockx_hero_basket: {e}")
            notes.append(f"stockx scrape failed: {e}")

    history = stockxlib.load_history(stockx_history_path)
    premium_pts = stockxlib.basket_premium_points(history)
    index_pts = stockxlib.basket_last_sale_index_points(history)

    latest_date = ""

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
            elif src == "bls":
                sid = str(meta["bls_id"])
                pts = [
                    p
                    for p in _bls_rows_to_points(bls_raw.get(sid) or [])
                    if p["date"] >= start
                ]
                notes.append(f"{key}: BLS {sid} ({len(pts)} pts)")
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
                if field == "qty":
                    pts = qty_pts
                    notes.append(f"{key}: Census HS{hs} qty unit={unit}")
                else:
                    pts = val_pts
                    notes.append(f"{key}: Census HS{hs} value n={len(pts)}")
            elif src == "stockx_hero_basket":
                field = str(meta.get("metric_field") or "premium_pct")
                if field == "last_sale_index":
                    pts = index_pts
                else:
                    pts = premium_pts
                if not pts:
                    raise RuntimeError(
                        "no StockX history yet (Chrome scrape blocked or skipped)"
                    )
                notes.append(f"{key}: StockX basket {field} n={len(pts)}")
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
            "FRED shoe/sporting + BLS footwear CPI/cotton-rubber PPI + Census HS "
            "footwear + FMP members + StockX hero basket scrape"
        ),
        "fetched_at": fetched_at,
        "as_of": latest_date,
        "catalog": catalog,
        "attachments": attachments,
        "series": series_out,
        "errors": errors,
        "notes": notes,
        "stockx": stockx_meta,
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
    hist = out_dir / "stockx_hero_history.jsonl"
    if hist.exists():
        uris.append(
            r2lib.put_bytes(
                f"{R2_PREFIX}/stockx_hero_history.jsonl",
                hist.read_bytes(),
                content_type="application/x-ndjson",
            )
            if hasattr(r2lib, "put_bytes")
            else r2lib.put_json(
                f"{R2_PREFIX}/stockx_hero_history.json",
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
    skip_stockx: bool = False,
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
        skip_stockx=skip_stockx,
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
    p.add_argument("--skip-stockx", action="store_true")
    args = p.parse_args(argv)

    try:
        payload = run(
            out_dir=args.out_dir,
            start_year=args.start_year,
            dry_run=args.dry_run,
            skip_r2=args.skip_r2,
            skip_stockx=args.skip_stockx,
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
