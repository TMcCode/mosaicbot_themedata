#!/usr/bin/env python3
"""Ocean Freight '26: VLCCs pack; publish to R2.

Theme slug: ocean-freight-26-vlccs.

PortWatch Cape/Bab tanker reroute + EIA US crude exports + TE China crude
imports + BWET + FMP VLCC revenue/segments/dividends/FCF + Kalshi crude
export ban. Forums sidecar separate. MidEast Hormuz / Brazil Santos / wiki
attached from sibling packs.
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
from lib import fmp_pack as fmp_packlib  # noqa: E402
from lib import fmp_prices as fmp_priceslib  # noqa: E402
from lib import eia_pet as eialib  # noqa: E402
from lib import portwatch_chokepoints as pwlib  # noqa: E402
from lib import kalshi as kalshilib  # noqa: E402
from lib import kalshi_pack  # noqa: E402
from lib import tradingeconomics as teapi  # noqa: E402
from lib import theme_score as scores  # noqa: E402
from lib.pack_derived import apply_derived_sums, apply_derived_ratios  # noqa: E402

SCHEMA_VERSION = 0
PACK = "ocean_freight_vlccs"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "ocean_freight_vlccs_catalog.json"
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


def _te_indicator_points(
    out_dir: Path,
    *,
    series_key: str,
    country: str,
    indicator: str,
    backfill: bool,
) -> tuple[list[dict[str, Any]], str]:
    """Local jsonl + TE indicator_historical (budget-aware cache)."""
    hist_path = out_dir / f"{series_key}_history.jsonl"
    r2_key = f"{R2_PREFIX}/{series_key}_history.jsonl"
    rows: list[dict[str, Any]] = []
    if hist_path.exists():
        for line in hist_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    elif not rows:
        try:
            raw = r2lib.get_bytes(r2_key)
            if raw:
                for line in raw.decode("utf-8").splitlines():
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
        except Exception:
            pass

    note = f"TE {country}/{indicator} cached={len(rows)}"
    need_pull = backfill or not rows
    if need_pull:
        try:
            te_rows = teapi.indicator_historical(
                country,
                indicator,
                d1="2015-01-01" if backfill or not rows else None,
                force=backfill,
                allow_reserve=True,
            )
            pts = teapi.indicator_to_points(te_rows)
            by = {p["date"]: p["value"] for p in pts}
            for r in rows:
                d = str(r.get("date") or "")[:10]
                if d and d not in by and r.get("value") is not None:
                    by[d] = float(r["value"])
            rows = [{"date": d, "value": by[d]} for d in sorted(by)]
            out_dir.mkdir(parents=True, exist_ok=True)
            hist_path.write_text(
                "".join(
                    json.dumps(r, separators=(",", ":")) + "\n" for r in rows
                ),
                encoding="utf-8",
            )
            try:
                r2lib.put_bytes(
                    r2_key,
                    hist_path.read_bytes(),
                    content_type="application/x-ndjson",
                )
            except Exception as e:
                note += f" r2_hist={e}"
            note = f"TE {country}/{indicator} pulled n={len(rows)}"
        except teapi.TeBudgetError as e:
            note = f"TE budget: {e}; using cached={len(rows)}"
        except Exception as e:
            note = f"TE soft-fail: {e}; using cached={len(rows)}"

    pts = [
        {"date": str(r["date"])[:10], "value": float(r["value"])}
        for r in rows
        if r.get("date") is not None and r.get("value") is not None
    ]
    return pts, note


def build_pack(
    *,
    catalog: dict,
    attachments: dict,
    start_year: int,
    out_dir: Path,
    skip_eia: bool = False,
    skip_portwatch: bool = False,
    skip_te: bool = False,
    te_backfill: bool = False,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    start = f"{start_year:04d}-01-01"
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    notes: list[str] = []
    latest_date = ""
    fmp_caches = fmp_packlib.FmpCaches.from_catalog(catalog)

    sources = {m.get("source") for m in (catalog.get("series") or [])}

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

    pw_daily: dict[str, list[dict[str, Any]]] = {}
    if not skip_portwatch and "portwatch_chokepoint" in sources:
        keys = sorted(
            {
                str(m.get("portwatch_key") or "").lower()
                for m in (catalog.get("series") or [])
                if m.get("source") == "portwatch_chokepoint" and m.get("portwatch_key")
            }
        )
        for ck in keys:
            try:
                daily, hm = pwlib.fetch_daily_tanker_transits(ck)
                pw_daily[ck] = daily
                notes.append(
                    f"portwatch {ck}: days={hm.get('n_days')} as_of={hm.get('as_of')} "
                    f"pages={hm.get('pages')}"
                )
            except Exception as e:
                notes.append(f"portwatch {ck} soft-fail: {e}")

    for meta in catalog.get("series") or []:
        key = meta.get("key")
        if not key:
            continue
        src = meta.get("source")
        try:
            if src == "eia_weekly":
                sid = str(meta.get("eia_series") or "")
                pts = [p for p in (eia_cache.get(sid) or []) if p["date"] >= start]
                notes.append(f"{key}: EIA {sid} n={len(pts)}")
            elif src == "portwatch_chokepoint":
                ck = str(meta.get("portwatch_key") or "").lower()
                daily = pw_daily.get(ck) or []
                if not daily:
                    raise RuntimeError(f"portwatch daily empty for {ck}")
                agg = str(meta.get("portwatch_agg") or "weekly").lower()
                if agg == "monthly":
                    pts = pwlib.to_monthly_sum(daily)
                else:
                    pts = pwlib.to_weekly_sum(daily)
                pts = [p for p in pts if p["date"] >= start]
                notes.append(f"{key}: portwatch {ck} {agg} n={len(pts)}")
            elif src == "fmp_eod_monthly":
                sym = str(meta.get("fmp_symbol") or "").upper()
                pts, mm = fmp_priceslib.fetch_monthly_avg_close(sym, start=start)
                notes.append(
                    f"{key}: FMP EOD {sym} months={mm.get('n_months')} "
                    f"days={mm.get('n_days')}"
                )
            elif src == "te_indicator":
                if skip_te:
                    raise RuntimeError("TE skipped")
                country = str(meta.get("te_country") or "")
                indicator = str(meta.get("te_indicator") or "")
                pts, tn = _te_indicator_points(
                    out_dir,
                    series_key=str(key),
                    country=country,
                    indicator=indicator,
                    backfill=te_backfill,
                )
                pts = [p for p in pts if p["date"] >= start]
                notes.append(f"{key}: {tn}")
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
            elif src in ("derived_sum", "derived_ratio"):
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
            "IMF PortWatch Cape/Bab + EIA WCREXUS2 + TE CHNCOI + FMP "
            "revenue/segments/dividends/FCF/BWET + Kalshi KXCRUDEEXPORTBAN"
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
    p.add_argument("--skip-eia", action="store_true")
    p.add_argument("--skip-portwatch", action="store_true")
    p.add_argument("--skip-te", action="store_true")
    p.add_argument("--te-backfill", action="store_true")
    args = p.parse_args()

    load_env()
    catalog = _load_json(_CATALOG)
    attachments = _load_attachments()
    try:
        payload = build_pack(
            catalog=catalog,
            attachments=attachments,
            start_year=args.start_year,
            out_dir=args.out_dir,
            skip_eia=args.skip_eia,
            skip_portwatch=args.skip_portwatch,
            skip_te=args.skip_te,
            te_backfill=args.te_backfill,
        )
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1

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
        r2_uris = _publish_r2(payload)
        try:
            r2_uris.append(
                r2lib.put_json(
                    "themedata/series_attachments.json", payload["attachments"]
                )
            )
        except Exception:
            pass
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
