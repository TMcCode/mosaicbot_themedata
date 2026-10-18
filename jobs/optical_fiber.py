#!/usr/bin/env python3
"""Optical & fiber systems backdrop pack; publish to R2.

Theme: Data Centers '25: Optical & Fiber Systems
(stockthemes slug: data-centers-25-optical-fiber-systems).

FRED (comms orders, fiber PPI, semi IP) + Census HS 854470 / 851762 imports
+ FMP hyperscaler CapEx (MSFT/GOOGL/META/AMZN/ORCL, per-ticker + sum)
+ TeleGeography submarine cable RFS km (SubTel watchlist proxy). Kalshi chart-only.
Ge/Ga spots attach from dew_defense — not re-fetched here.
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
from urllib.request import Request, urlopen

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.env import load_env, require_env  # noqa: E402
from lib import r2 as r2lib  # noqa: E402
from lib import pack_series as ps  # noqa: E402
from lib import fred as fredlib  # noqa: E402
from lib import census_trade as census  # noqa: E402
from lib import kalshi as kalshilib  # noqa: E402
from lib import fmp as fmplib  # noqa: E402
from lib import telegeography_cables as teglib  # noqa: E402
from lib import theme_score as scores  # noqa: E402
from lib.as_of import freshness_as_of  # noqa: E402

SCHEMA_VERSION = 0
PACK = "optical_fiber"
R2_PREFIX = f"themedata/{PACK}"
UA = "mosaicbot_themedata/0.1 (+optical_fiber)"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "optical_fiber_catalog.json"
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


def _fred_csv(series_id: str) -> list[tuple[str, float]]:
    return fredlib.fetch_series(series_id)


def build_pack(
    *,
    catalog: dict,
    attachments: dict,
    start_year: int,
    end_year: int,
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

    # Cache Census HS6 fetches (value+qty share one API call).
    census_cache: dict[str, tuple[list, list, str | None]] = {}
    # One TeleGeography full-catalog pull shared by subsea series.
    teg_cables: list[dict[str, Any]] | None = None
    # Shared FMP CapEx pulls (aggregate + per-ticker reuse same cache).
    fmp_by_symbol: dict[str, list[dict[str, Any]]] | None = None
    fmp_symbols_needed: list[str] = []
    for _m in catalog.get("series") or []:
        _src = _m.get("source")
        if _src == "fmp_capex":
            for s in _m.get("fmp_symbols") or list(fmplib.DEFAULT_HYPERSCALERS):
                su = str(s).upper()
                if su not in fmp_symbols_needed:
                    fmp_symbols_needed.append(su)
        elif _src == "fmp_capex_ticker":
            su = str(_m.get("fmp_symbol") or "").upper()
            if su and su not in fmp_symbols_needed:
                fmp_symbols_needed.append(su)

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
                    for d, v in _fred_csv(sid)
                    if d >= start
                ]
                notes.append(f"{key}: FRED {sid}")
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
                    if unit:
                        meta = {**meta, "unit": unit.lower() if unit else meta.get("unit")}
                        notes.append(f"{key}: Census HS{hs} qty unit={unit}")
                    else:
                        notes.append(f"{key}: Census HS{hs} qty")
                else:
                    pts = val_pts
                    notes.append(f"{key}: Census HS{hs} value")
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
            elif src in ("fmp_capex", "fmp_capex_ticker"):
                if fmp_by_symbol is None:
                    if not fmp_symbols_needed:
                        raise RuntimeError("no FMP CapEx symbols in catalog")
                    fmp_by_symbol = fmplib.fetch_capex_by_symbol(
                        fmp_symbols_needed,
                        limit=int(meta.get("fmp_limit") or 40),
                    )
                    notes.append(
                        f"fmp: fetched CapEx for {','.join(fmp_symbols_needed)}"
                    )
                if src == "fmp_capex_ticker":
                    sym = str(meta.get("fmp_symbol") or "").upper()
                    if not sym:
                        raise RuntimeError("fmp_symbol required")
                    pts = [
                        {"date": p["date"], "value": p["value"]}
                        for p in fmp_by_symbol.get(sym) or []
                        if p["date"] >= start
                    ]
                    notes.append(
                        f"{key}: FMP CapEx {sym} n={len(pts)} (calendar Q)"
                    )
                else:
                    # Aggregate only the symbols listed on this series (not every
                    # ticker series that happens to share the FMP cache).
                    agg_syms = [
                        str(s).upper()
                        for s in (
                            meta.get("fmp_symbols")
                            or list(fmplib.DEFAULT_HYPERSCALERS)
                        )
                    ]
                    subset = {
                        s: fmp_by_symbol[s]
                        for s in agg_syms
                        if s in fmp_by_symbol
                    }
                    pts, mm = fmplib.aggregate_capex_points(
                        subset,
                        require_all=bool(meta.get("require_all", True)),
                    )
                    pts = [p for p in pts if p["date"] >= start]
                    notes.append(
                        f"{key}: FMP CapEx sum {','.join(mm.get('symbols') or [])} "
                        f"quarters={mm.get('quarters')} unit={mm.get('unit')}"
                    )
            elif src == "telegeography_cables":
                if teg_cables is None:
                    teg_cables = teglib.fetch_all_cable_details()
                    notes.append(
                        f"telegeography: fetched {len(teg_cables)} cable details"
                    )
                segment = str(meta.get("segment") or "").strip().lower()
                if not segment:
                    segment = (
                        "hyperscaler"
                        if meta.get("hyperscaler_only")
                        else "all"
                    )
                pts, mm = teglib.annual_rfs_km_points(
                    teg_cables,
                    segment=segment,
                    include_planned=bool(meta.get("include_planned", False)),
                    start_year=int(meta.get("start_year") or start_year),
                )
                pts = [p for p in pts if p["date"] >= start]
                notes.append(
                    f"{key}: TE subsea RFS km years={mm.get('years')} "
                    f"segment={mm.get('segment')} skipped={mm.get('skipped')}"
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

    # Prefer a non-future pack as_of (annual YTD is dated YYYY-12-31).
    pack_as_of = latest_date
    try:
        y, m, d = (int(x) for x in str(latest_date)[:10].split("-"))
        clamped = freshness_as_of(date(y, m, d))
        pack_as_of = clamped.isoformat()
    except Exception:
        pass

    return {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "source": "FRED + Census + FMP CapEx + TeleGeography cables (optical fiber)",
        "fetched_at": fetched_at,
        "as_of": pack_as_of,
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
        "fetched_at": payload.get("fetched_at"),
        "series": list((payload.get("series") or {}).keys()),
        "errors": payload.get("errors") or [],
        "theme_scores": payload.get("theme_scores"),
        "r2": payload.get("_r2_uris") or [],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
