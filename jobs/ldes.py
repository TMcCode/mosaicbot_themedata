#!/usr/bin/env python3
"""Renewable Energy '26: Long-Duration Energy Storage pack; publish to R2.

Theme slug: renewable-energy-26-long-duration-energy-storage.

BE Product/Installation + FLNC Product/Americas + EOSE/LGO/STEM + LDES
transcripts + EIA-860M battery MW/duration shares + FRED zinc/battery
PPI/transformer PPI/DGS10 + TE lithium carbonate (append history) +
PJM BRA RTO clearing + USASpending LDES/OCED + Kalshi VA/ratepayer DC
policy. Forums separate (ldes_forums). No TE vanadium.
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
from lib import kalshi as kalshilib  # noqa: E402
from lib import kalshi_pack  # noqa: E402
from lib import theme_score as scores  # noqa: E402
from lib import fmp_pack as fmp_packlib  # noqa: E402
from lib import usaspending as usa  # noqa: E402
from lib import eia_860m as eia860mlib  # noqa: E402
from lib import te_spot  # noqa: E402
from lib import tradingeconomics as teapi  # noqa: E402
from lib import pjm_bra as pjmbralib  # noqa: E402
from lib.pack_derived import apply_derived_sums, apply_derived_ratios  # noqa: E402

SCHEMA_VERSION = 0
PACK = "ldes"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "ldes_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
_TE_KEYS = ("lithium_carbonate_cny_t",)


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
                n = te_spot.api_backfill(out_dir, spec, d1="2020-01-01")
                notes.append(f"TE backfill {spec.series_key}: {n} pts")
        refreshed = te_spot.refresh_batch_api(out_dir, specs, force=False)
        for key, (snap, inserted) in refreshed.items():
            notes.append(
                f"TE snapshot {key}: as_of={snap.get('as_of')} "
                f"px={snap.get('price_cny_kg')} inserted={inserted}"
            )
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
                te_spot.write_history(out_dir, spec, rows)
        except Exception:
            pass
    return te_spot.points_from_history(rows)


def _pjm_points(out_dir: Path, *, refresh: bool) -> tuple[list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    if refresh:
        rows, n = pjmbralib.refresh_and_upsert(out_dir)
        notes.extend(n)
    else:
        rows = pjmbralib.load_history(out_dir)
    if not rows:
        try:
            raw = r2lib.get_bytes(pjmbralib.R2_HISTORY_KEY)
            if raw:
                rows = pjmbralib.rows_from_body(raw)
                pjmbralib.write_history(out_dir, rows)
                notes.append(f"pjm_bra: loaded R2 history n={len(rows)}")
        except Exception as e:
            notes.append(f"pjm_bra: R2 load soft-fail: {e}")
    if refresh and rows:
        try:
            r2lib.put_bytes(
                pjmbralib.R2_HISTORY_KEY,
                pjmbralib.history_to_body(rows).encode("utf-8"),
                content_type="application/x-ndjson",
            )
        except Exception as e:
            notes.append(f"pjm_bra: R2 put soft-fail: {e}")
    return pjmbralib.points_from_history(rows), notes


def build_pack(
    *,
    catalog: dict,
    attachments: dict,
    start_year: int,
    out_dir: Path,
    te_backfill: bool = False,
    skip_te: bool = False,
    skip_pjm: bool = False,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    start = f"{start_year:04d}-01-01"
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    notes: list[str] = []
    latest_date = ""
    fmp_caches = fmp_packlib.FmpCaches.from_catalog(catalog)
    eia_cache: dict[str, list[dict[str, Any]]] | None = None
    eia_meta: dict[str, Any] | None = None

    sources = {m.get("source") for m in (catalog.get("series") or [])}
    if not skip_te and "tradingeconomics" in sources:
        notes.extend(_refresh_te(out_dir, backfill=te_backfill))
    pjm_refreshed = False
    if not skip_pjm and "pjm_bra" in sources:
        # refresh once before series loop
        _, pn = _pjm_points(out_dir, refresh=True)
        notes.extend(pn)
        pjm_refreshed = True

    for meta in catalog.get("series") or []:
        key = meta.get("key")
        if not key:
            continue
        src = meta.get("source")
        if src in ("derived_sum", "derived_ratio"):
            continue
        try:
            if src == "fred":
                sid = str(meta["fred_id"])
                pts = [
                    {"date": d if len(d) == 10 else d[:10], "value": v}
                    for d, v in fredlib.fetch_series(sid)
                    if d >= start
                ]
                notes.append(f"{key}: FRED {sid} n={len(pts)}")
            elif src == "kalshi":
                pts, note = kalshi_pack.fetch_from_meta(
                    meta,
                    out_dir=out_dir,
                    r2_prefix=R2_PREFIX,
                    catalog_path=_CATALOG,
                    fetched_at=fetched_at,
                    start=start,
                )
                notes.append(note)
            elif src == "usaspending":
                keywords = list(meta.get("keywords") or [])
                pts = usa.fetch_monthly_obligations(
                    keywords=keywords or None,
                    start_date=start,
                )
                notes.append(
                    f"{key}: USASpending keywords={keywords} n={len(pts)}"
                )
            elif src == "eia_860m":
                metric = str(meta.get("eia_metric") or "").strip()
                if not metric:
                    raise RuntimeError("eia_metric required")
                if eia_cache is None:
                    eia_cache, eia_meta = eia860mlib.refresh_battery_metrics(
                        history_path=out_dir / eia860mlib.HISTORY_NAME,
                        start=str(meta.get("eia_start") or f"{start_year:04d}-01"),
                        refresh_months=int(meta.get("eia_refresh_months") or 4),
                        backfill_if_empty=36,
                    )
                    notes.append(
                        f"eia860m: fetched={eia_meta.get('fetched_months')} "
                        f"history={eia_meta.get('n_history')} "
                        f"skipped={eia_meta.get('skipped_missing')}"
                    )
                    for n in eia_meta.get("notes") or []:
                        notes.append(n)
                    for err in eia_meta.get("errors") or []:
                        notes.append(f"eia860m warn: {err}")
                pts = list((eia_cache or {}).get(metric) or [])
                pts = [p for p in pts if p["date"] >= start]
                notes.append(f"{key}: EIA-860m {metric} n={len(pts)}")
            elif src == "tradingeconomics":
                pts = [p for p in _te_points(out_dir, key) if p["date"] >= start]
                notes.append(f"{key}: TE history n={len(pts)}")
            elif src == "pjm_bra":
                pts, pn = _pjm_points(out_dir, refresh=not pjm_refreshed)
                if not pjm_refreshed:
                    notes.extend(pn)
                    pjm_refreshed = True
                pts = [p for p in pts if p["date"] >= start]
                notes.append(f"{key}: PJM BRA RTO n={len(pts)}")
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
    ratio_latest = apply_derived_ratios(
        catalog,
        series_out,
        notes=notes,
        errors=errors,
        fetched_at=fetched_at,
    )
    if ratio_latest:
        latest_date = max(latest_date, ratio_latest)

    if not series_out:
        raise RuntimeError("no series built: " + "; ".join(errors))

    return {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "source": (
            "FMP LDES OEM segments/totals + transcripts + EIA-860M battery "
            "duration + FRED zinc/PPI/DGS10 + TE lithium carbonate + PJM BRA "
            "RTO + USASpending LDES/OCED + Kalshi VA/ratepayer DC policy"
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


def _publish_r2(payload: dict, out_dir: Path) -> list[str]:
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
    hist = out_dir / eia860mlib.HISTORY_NAME
    if hist.exists():
        try:
            data = json.loads(hist.read_text(encoding="utf-8"))
            uris.append(r2lib.put_json(f"{R2_PREFIX}/{eia860mlib.HISTORY_NAME}", data))
        except Exception:
            pass
    for name in (
        "lithium_carbonate_te_history.jsonl",
        pjmbralib.HISTORY_NAME,
    ):
        path = out_dir / name
        if path.exists():
            try:
                uris.append(
                    r2lib.put_bytes(
                        f"{R2_PREFIX}/{name}",
                        path.read_bytes(),
                        content_type="application/x-ndjson",
                    )
                )
            except Exception:
                pass
    return uris


def run(
    *,
    out_dir: Path,
    start_year: int = 2018,
    dry_run: bool = False,
    skip_r2: bool = False,
    te_backfill: bool = False,
    skip_te: bool = False,
    skip_pjm: bool = False,
) -> dict[str, Any]:
    load_env()
    catalog = _load_json(_CATALOG)
    attachments = _load_attachments()
    payload = build_pack(
        catalog=catalog,
        attachments=attachments,
        start_year=start_year,
        out_dir=out_dir,
        te_backfill=te_backfill,
        skip_te=skip_te,
        skip_pjm=skip_pjm,
    )
    if dry_run:
        scores.recompute_and_publish(
            payload,
            scores_dir=_SCORES_OUT,
            skip_r2=True,
            prefer_remote_recipe=False,
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
    p.add_argument("--start-year", type=int, default=2018)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-r2", action="store_true")
    p.add_argument(
        "--te-backfill",
        action="store_true",
        help="Pull TE markets/historical for lithium carbonate (budget-aware)",
    )
    p.add_argument("--skip-te", action="store_true")
    p.add_argument("--skip-pjm", action="store_true")
    args = p.parse_args(argv)
    try:
        payload = run(
            out_dir=args.out_dir,
            start_year=args.start_year,
            dry_run=args.dry_run,
            skip_r2=args.skip_r2,
            te_backfill=args.te_backfill,
            skip_te=args.skip_te,
            skip_pjm=args.skip_pjm,
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
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
