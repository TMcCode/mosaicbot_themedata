#!/usr/bin/env python3
"""Energy Bottleneck '26: US Refiners pack; publish to R2.

Theme slug: energy-bottleneck-26-us-refiners.

EIA WPSR (hist_xls) + TE crack/RBOB/HO + FRED spots + FMP refining/RD segments
+ Kalshi export-ban (soft-fail). Forums sidecar separate.
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
from lib import fmp_pack as fmp_packlib  # noqa: E402
from lib import kalshi as kalshilib  # noqa: E402
from lib import theme_score as scores  # noqa: E402
from lib import eia_pet as eialib  # noqa: E402
from lib import te_spot  # noqa: E402
from lib import tradingeconomics as teapi  # noqa: E402
from lib.pack_derived import apply_derived_sums  # noqa: E402

SCHEMA_VERSION = 0
PACK = "energy_bottleneck_us_refiners"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "energy_bottleneck_us_refiners_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"

_TE_KEYS = ("te_crack_spread", "te_rbob_gasoline", "te_heating_oil")


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


def _te_specs() -> list[te_spot.SpotSpec]:
    return [
        s
        for s in te_spot.specs_from_symbology(pull_only=False)
        if s.pack == PACK and s.series_key in _TE_KEYS
    ]


def _refresh_te(out_dir: Path, *, backfill: bool) -> list[str]:
    notes: list[str] = []
    specs = _te_specs()
    if not specs:
        return ["TE: no symbology rows for pack (skip)"]
    try:
        if backfill:
            for spec in specs:
                n = te_spot.api_backfill(out_dir, spec, d1="2018-01-01")
                notes.append(f"TE backfill {spec.series_key}: {n} pts")
        refreshed = te_spot.refresh_batch_api(out_dir, specs, force=False)
        for key, (snap, inserted) in refreshed.items():
            notes.append(
                f"TE snapshot {key}: as_of={snap.get('as_of')} "
                f"px={snap.get('price_cny_kg')} inserted={inserted}"
            )
        # publish TE history jsonl
        for spec in specs:
            hist = te_spot.history_path(out_dir, spec)
            if hist.exists():
                try:
                    r2lib.put_bytes(
                        spec.r2_history_key,
                        hist.read_bytes(),
                        content_type="application/x-ndjson",
                    )
                except Exception as e:
                    notes.append(f"TE R2 {spec.series_key}: {e}")
    except teapi.TeBudgetError as e:
        notes.append(f"TE budget: {e}")
    except Exception as e:
        notes.append(f"TE refresh soft-fail: {e}")
    return notes


def _te_points(out_dir: Path, series_key: str) -> list[dict[str, Any]]:
    spec = next((s for s in _te_specs() if s.series_key == series_key), None)
    if spec is None:
        raise RuntimeError(f"TE spec missing for {series_key}")
    rows = te_spot.load_history(out_dir, spec)
    if not rows:
        try:
            raw = r2lib.get_bytes(spec.r2_history_key)
            if raw:
                rows = te_spot.rows_from_body(raw)
        except Exception:
            pass
    return te_spot.points_from_history(rows)


def build_pack(
    *,
    catalog: dict,
    attachments: dict,
    start_year: int,
    out_dir: Path,
    te_backfill: bool = False,
    skip_te: bool = False,
    skip_eia: bool = False,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    start = f"{start_year:04d}-01-01"
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    notes: list[str] = []
    latest_date = ""
    fmp_caches = fmp_packlib.FmpCaches.from_catalog(catalog)

    sources = {m.get("source") for m in (catalog.get("series") or [])}

    if not skip_te and "tradingeconomics" in sources:
        notes.extend(_refresh_te(out_dir, backfill=te_backfill))

    eia_cache: dict[str, list] = {}
    if not skip_eia and "eia_weekly" in sources:
        ids = sorted(
            {
                str(m.get("eia_series"))
                for m in (catalog.get("series") or [])
                if m.get("source") == "eia_weekly" and m.get("eia_series")
            }
        )
        eia_cache, en = eialib.fetch_many(ids, start=start)
        notes.extend(en)

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
            elif src == "eia_weekly":
                sid = str(meta.get("eia_series") or "")
                pts = [p for p in (eia_cache.get(sid) or []) if p["date"] >= start]
                notes.append(f"{key}: EIA {sid} n={len(pts)}")
            elif src == "tradingeconomics":
                pts = [p for p in _te_points(out_dir, key) if p["date"] >= start]
                notes.append(f"{key}: TE history n={len(pts)}")
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
                    f"oi={mm.get('open_interest')}"
                )
            elif src == "derived_sum":
                continue
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

    derived_latest = apply_derived_sums(
        catalog,
        series_out,
        notes=notes,
        errors=errors,
        fetched_at=fetched_at,
    )
    if derived_latest:
        latest_date = max(latest_date, derived_latest)

    if not series_out:
        raise RuntimeError("no series built: " + "; ".join(errors))

    return {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "source": (
            "EIA WPSR hist_xls + TE crack/RBOB/HO + FRED WTI/product spots + "
            "FMP refining/RD segments + Kalshi"
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


def _publish_r2(payload: dict, out_dir: Path) -> list[str]:
    uris = [
        r2lib.put_json(f"{R2_PREFIX}/latest.json", payload),
        r2lib.put_json(f"{R2_PREFIX}/catalog.json", payload["catalog"]),
        r2lib.put_json(f"{R2_PREFIX}/attachments.json", payload["attachments"]),
    ]
    for spec in _te_specs():
        hist = te_spot.history_path(out_dir, spec)
        if hist.exists():
            try:
                uris.append(
                    r2lib.put_bytes(
                        spec.r2_history_key,
                        hist.read_bytes(),
                        content_type="application/x-ndjson",
                    )
                )
            except Exception:
                pass
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
    metrics = {
        "pack": PACK,
        "as_of": payload.get("as_of"),
        "fetched_at": payload.get("fetched_at"),
        "n_series": len(payload.get("series") or {}),
        "errors": payload.get("errors") or [],
    }
    uris.append(r2lib.put_json(f"{R2_PREFIX}/metrics_latest.json", metrics))
    return uris


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT)
    p.add_argument("--start-year", type=int, default=2015)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-r2", action="store_true")
    p.add_argument("--te-backfill", action="store_true")
    p.add_argument("--skip-te", action="store_true")
    p.add_argument("--skip-eia", action="store_true")
    args = p.parse_args()

    load_env()
    catalog = _load_json(_CATALOG)
    attachments = _load_attachments()
    payload = build_pack(
        catalog=catalog,
        attachments=attachments,
        start_year=args.start_year,
        out_dir=args.out_dir,
        te_backfill=args.te_backfill,
        skip_te=args.skip_te,
        skip_eia=args.skip_eia,
    )
    if args.dry_run:
        scores.recompute_and_publish(
            payload, scores_dir=_SCORES_OUT, skip_r2=True, prefer_remote_recipe=False
        )
        print(
            json.dumps(
                {
                    "pack": PACK,
                    "as_of": payload.get("as_of"),
                    "n_series": len(payload.get("series") or {}),
                    "errors": payload.get("errors"),
                    "notes": payload.get("notes"),
                    "r2": [],
                },
                indent=2,
            )
        )
        return 0 if payload.get("series") else 1

    _write_local(args.out_dir, payload)
    r2_uris: list[str] = []
    if not args.skip_r2:
        r2_uris = _publish_r2(payload, args.out_dir)
    try:
        score_result = scores.recompute_and_publish(
            payload,
            scores_dir=_SCORES_OUT,
            skip_r2=args.skip_r2,
            sync_recipe=True,
            prefer_remote_recipe=False,
        )
        r2_uris.extend(score_result.get("r2_uris") or [])
    except Exception as e:
        payload.setdefault("errors", []).append(f"theme_score: {e}")
    print(
        json.dumps(
            {
                "pack": PACK,
                "as_of": payload.get("as_of"),
                "n_series": len(payload.get("series") or {}),
                "errors": payload.get("errors"),
                "notes": payload.get("notes"),
                "r2": r2_uris,
            },
            indent=2,
        )
    )
    return 0 if payload.get("series") else 1


if __name__ == "__main__":
    raise SystemExit(main())
