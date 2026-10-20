#!/usr/bin/env python3
"""Beauty Prestige backdrop pack; publish to R2.

Theme: Beauty '26: Prestige Beauty
(stockthemes slug: beauty-26-prestige-beauty).

US health/personal-care retail + toilet-prep PPI + cosmetics CPI + sentiment +
e-commerce + Census HS 330499/330300 + FMP CapEx/revenue (EL+IPAR+WALD USD;
MC.PA/OR.PA/etc. ticker-local) + transcript keywords. Forums separate.
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
from lib import census_trade as census  # noqa: E402
from lib import fmp_pack as fmp_packlib  # noqa: E402
from lib import tradingeconomics as teapi  # noqa: E402
from lib import chinadata_trade as chinadata  # noqa: E402
from lib import dramexchange as dxlib  # noqa: E402
from lib import kalshi as kalshilib  # noqa: E402
from lib import manifold as manifoldlib  # noqa: E402
from lib import theme_score as scores  # noqa: E402

SCHEMA_VERSION = 0
PACK = "beauty_prestige"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "beauty_prestige_catalog.json"
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
    end_year: int,
    out_dir: Path,
    skip_dx_scrape: bool = False,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    start = f"{start_year:04d}-01-01"
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    notes: list[str] = []
    latest_date = ""

    census_key = ""
    if any(
        (m.get("source") == "census_trade") for m in (catalog.get("series") or [])
    ):
        census_key = require_env("CENSUS_API_KEY")["CENSUS_API_KEY"]

    census_cache: dict[str, tuple[list, list, str | None]] = {}
    fmp_caches = fmp_packlib.FmpCaches.from_catalog(catalog)
    te_ind_by_key: dict[str, dict[str, Any]] = {
        s["series_key"]: s for s in teapi.load_indicator_specs(pull_only=False)
    }

    dx_snap: dict[str, Any] | None = None
    need_dx = any(
        m.get("source") == "dramexchange" for m in (catalog.get("series") or [])
    )
    if need_dx and not skip_dx_scrape:
        try:
            wanted = {}
            for m in catalog.get("series") or []:
                if m.get("source") != "dramexchange":
                    continue
                k = m.get("key")
                label = m.get("dx_product") or dxlib.PRODUCTS.get(k or "")
                if k and label:
                    wanted[str(k)] = str(label)
            dx_snap = dxlib.scrape_spot_snapshot(products=wanted or None)
            notes.append(
                "dramexchange: scraped "
                + ",".join(sorted((dx_snap.get("spots") or {}).keys()))
            )
        except Exception as e:
            errors.append(f"dramexchange: {e}")
            dx_snap = None
    elif need_dx and skip_dx_scrape:
        notes.append("dramexchange: skipped scrape (history-only)")

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
                sym = spec.get("symbol") or ""
                notes.append(
                    f"{key}: TE indicator {sym} "
                    f"{spec.get('country')}/{spec.get('indicator')} n={len(pts)}"
                )
            elif src == "chinadata_trade":
                hs = str(meta.get("hs") or "")
                flow = str(meta.get("flow") or "import")
                if not hs:
                    raise RuntimeError("hs required")
                pts = [
                    p
                    for p in chinadata.fetch_hs_monthly(hs, flow=flow)
                    if p["date"] >= start
                ]
                notes.append(
                    f"{key}: chinadata GACC HS{hs} {flow} n={len(pts)}"
                )
            elif src == "dramexchange":
                hist_path = out_dir / f"{key}_history.jsonl"
                if dx_snap and key in (dx_snap.get("spots") or {}):
                    spot = dx_snap["spots"][key]
                    pts = dxlib.append_spot(
                        hist_path,
                        date=str(spot["date"]),
                        value=float(spot["value"]),
                        meta={
                            "product": spot.get("product"),
                            "fetched_at": dx_snap.get("fetched_at"),
                            "source": dx_snap.get("source"),
                        },
                    )
                else:
                    pts = dxlib.points_from_history(hist_path)
                pts = [p for p in pts if p["date"] >= start]
                if not pts:
                    raise RuntimeError("no DRAMeXchange history points")
                notes.append(
                    f"{key}: DRAMeXchange spot history n={len(pts)} "
                    f"last={pts[-1]['date']} ${pts[-1]['value']}"
                )
            elif fmp_packlib.is_fmp_source(src):
                pts, note = fmp_caches.resolve(meta, start=start)
                for n in fmp_caches.notes:
                    if n not in notes:
                        notes.append(n)
                fmp_caches.notes.clear()
                notes.append(note)

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
                    f"oi={mm.get('open_interest')} url={mm.get('url')}"
                )
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
            "FRED beauty retail/PPI/CPI + Census HS 330499/330300 + "
            "TE China retail YoY + chinadata GACC HS 3304 + "
            "FMP prestige members (beauty_prestige)"
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
    skip_dx_scrape: bool = False,
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
        skip_dx_scrape=skip_dx_scrape,
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
    p.add_argument("--start-year", type=int, default=2018)
    p.add_argument("--end-year", type=int, default=now_y)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-r2", action="store_true")
    p.add_argument(
        "--skip-dx-scrape",
        action="store_true",
        help="Reuse *_history.jsonl without re-hitting DRAMeXchange",
    )
    args = p.parse_args(argv)

    try:
        payload = run(
            out_dir=args.out_dir,
            start_year=args.start_year,
            end_year=args.end_year,
            dry_run=args.dry_run,
            skip_r2=args.skip_r2,
            skip_dx_scrape=args.skip_dx_scrape,
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
