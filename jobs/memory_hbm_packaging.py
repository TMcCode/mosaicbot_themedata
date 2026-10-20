#!/usr/bin/env python3
"""Memory HBM Inspection, Packaging & Bonding backdrop pack; publish to R2.

Theme: Memory '26: HBM Inspection, Packaging & Bonding
(stockthemes slug: memory-26-hbm-inspection-packaging-bonding).

Members: AMAT/AMKR/CAMT/ONTO/KLIC (+ ASX/BESI.AS ticker-local). Shared macros:
hyperscaler CapEx, KR/TW exports, HS 903082, measuring IP/PPI, DX DDR5 die,
transcript keywords. Forums separate. Distinct from memory_hbm_testing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
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
from lib import fmp as fmplib  # noqa: E402
from lib import tradingeconomics as teapi  # noqa: E402
from lib import dramexchange as dxlib  # noqa: E402
from lib import kalshi as kalshilib  # noqa: E402
from lib import manifold as manifoldlib  # noqa: E402
from lib import theme_score as scores  # noqa: E402

SCHEMA_VERSION = 0
PACK = "memory_hbm_packaging"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "memory_hbm_packaging_catalog.json"
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
    fmp_capex_by_symbol: dict[str, list[dict[str, Any]]] | None = None
    fmp_rev_by_symbol: dict[str, list[dict[str, Any]]] | None = None
    # (symbol, year, quarter) → transcript row with content/date
    fmp_tx_cache: dict[tuple[str, int, int], dict[str, Any]] | None = None
    fmp_capex_symbols: list[str] = []
    fmp_rev_symbols: list[str] = []
    fmp_tx_symbols: list[str] = []
    te_ind_by_key: dict[str, dict[str, Any]] = {
        s["series_key"]: s for s in teapi.load_indicator_specs(pull_only=False)
    }

    def _add_syms(bucket: list[str], meta_row: dict[str, Any]) -> None:
        syms = list(meta_row.get("fmp_symbols") or [])
        one = meta_row.get("fmp_symbol")
        if one:
            syms.append(str(one))
        for s in syms:
            su = str(s).upper()
            if su and su not in bucket:
                bucket.append(su)

    for _m in catalog.get("series") or []:
        src0 = _m.get("source")
        if src0 in ("fmp_capex", "fmp_capex_ticker"):
            if src0 == "fmp_capex" and not _m.get("fmp_symbols"):
                for s in fmplib.DEFAULT_HYPERSCALERS:
                    if s not in fmp_capex_symbols:
                        fmp_capex_symbols.append(s)
            _add_syms(fmp_capex_symbols, _m)
        elif src0 in ("fmp_revenue", "fmp_revenue_ticker"):
            _add_syms(fmp_rev_symbols, _m)
        elif src0 in ("fmp_transcript_keywords", "fmp_transcript_keywords_ticker"):
            _add_syms(fmp_tx_symbols, _m)

    dx_snap: dict[str, Any] | None = None
    need_dx = any(
        m.get("source") == "dramexchange" for m in (catalog.get("series") or [])
    )
    if need_dx and not skip_dx_scrape:
        try:
            dx_snap = dxlib.scrape_spot_snapshot()
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
            elif src in ("fmp_capex", "fmp_capex_ticker"):
                if fmp_capex_by_symbol is None:
                    if not fmp_capex_symbols:
                        raise RuntimeError("no FMP CapEx symbols in catalog")
                    fmp_capex_by_symbol = fmplib.fetch_capex_by_symbol(
                        fmp_capex_symbols,
                        limit=int(meta.get("fmp_limit") or 40),
                    )
                    notes.append(
                        f"fmp: fetched CapEx for {','.join(fmp_capex_symbols)}"
                    )
                if src == "fmp_capex_ticker":
                    sym = str(meta.get("fmp_symbol") or "").upper()
                    if not sym:
                        raise RuntimeError("fmp_symbol required")
                    pts = [
                        {"date": p["date"], "value": p["value"]}
                        for p in fmp_capex_by_symbol.get(sym) or []
                        if p["date"] >= start
                    ]
                    notes.append(f"{key}: FMP CapEx {sym} n={len(pts)}")
                else:
                    agg_syms = [
                        str(s).upper()
                        for s in (
                            meta.get("fmp_symbols")
                            or list(fmplib.DEFAULT_HYPERSCALERS)
                        )
                    ]
                    subset = {
                        s: fmp_capex_by_symbol[s]
                        for s in agg_syms
                        if s in fmp_capex_by_symbol
                    }
                    pts, mm = fmplib.aggregate_capex_points(
                        subset,
                        require_all=bool(meta.get("require_all", True)),
                    )
                    pts = [p for p in pts if p["date"] >= start]
                    notes.append(
                        f"{key}: FMP CapEx sum {','.join(mm.get('symbols') or [])} "
                        f"quarters={mm.get('quarters')}"
                    )
            elif src in ("fmp_revenue", "fmp_revenue_ticker"):
                rev_start = fmplib.revenue_history_start(start, meta)
                if fmp_rev_by_symbol is None:
                    if not fmp_rev_symbols:
                        raise RuntimeError("no FMP revenue symbols in catalog")
                    fmp_rev_by_symbol = fmplib.fetch_revenue_by_symbol(
                        fmp_rev_symbols,
                        limit=int(meta.get("fmp_limit") or 40),
                    )
                    notes.append(
                        f"fmp: fetched revenue for {','.join(fmp_rev_symbols)} "
                        f"(history from {rev_start})"
                    )
                if src == "fmp_revenue_ticker":
                    sym = str(meta.get("fmp_symbol") or "").upper()
                    if not sym:
                        raise RuntimeError("fmp_symbol required")
                    pts = [
                        {"date": p["date"], "value": p["value"]}
                        for p in fmp_rev_by_symbol.get(sym) or []
                        if p["date"] >= rev_start
                    ]
                    notes.append(f"{key}: FMP revenue {sym} n={len(pts)}")
                else:
                    agg_syms = [
                        str(s).upper() for s in (meta.get("fmp_symbols") or [])
                    ]
                    subset = {
                        s: fmp_rev_by_symbol[s]
                        for s in agg_syms
                        if s in fmp_rev_by_symbol
                    }
                    pts, mm = fmplib.aggregate_revenue_points(
                        subset,
                        require_all=bool(meta.get("require_all", False)),
                    )
                    pts = [p for p in pts if p["date"] >= rev_start]
                    notes.append(
                        f"{key}: FMP revenue sum {','.join(mm.get('symbols') or [])} "
                        f"quarters={mm.get('quarters')}"
                    )
            elif src in (
                "fmp_transcript_keywords",
                "fmp_transcript_keywords_ticker",
            ):
                kws = [str(k).strip() for k in (meta.get("keywords") or []) if str(k).strip()]
                if not kws:
                    raise RuntimeError("keywords required")
                limit_q = int(meta.get("fmp_limit") or 16)
                if fmp_tx_cache is None:
                    if not fmp_tx_symbols:
                        raise RuntimeError("no FMP transcript symbols in catalog")
                    fmp_tx_cache = {}
                    for sym in fmp_tx_symbols:
                        for row in fmplib.list_transcript_dates(sym)[:limit_q]:
                            year = int(row.get("fiscalYear") or row.get("year") or 0)
                            quarter = int(row.get("quarter") or 0)
                            if not year or not quarter:
                                continue
                            tr = fmplib.fetch_transcript(
                                sym, year=year, quarter=quarter
                            )
                            time.sleep(0.1)
                            if not tr:
                                continue
                            fmp_tx_cache[(sym, year, quarter)] = tr
                    notes.append(
                        f"fmp: cached {len(fmp_tx_cache)} transcripts for "
                        f"{','.join(fmp_tx_symbols)}"
                    )

                def _tx_points_for(sym: str) -> list[dict[str, Any]]:
                    from collections import defaultdict as _dd

                    by_date: dict[str, float] = _dd(float)
                    for (s, _y, _q), tr in fmp_tx_cache.items():
                        if s != sym:
                            continue
                        d_raw = str(tr.get("date") or "")[:10]
                        if not d_raw:
                            continue
                        hits = fmplib.count_keyword_hits(
                            str(tr.get("content") or ""), kws
                        )
                        key_d = fmplib.calendar_quarter_end(d_raw)
                        by_date[key_d] += float(sum(hits.values()))
                    return [
                        {"date": d, "value": round(v, 4), "symbol": sym}
                        for d, v in sorted(by_date.items())
                    ]

                if src == "fmp_transcript_keywords_ticker":
                    sym = str(meta.get("fmp_symbol") or "").upper()
                    if not sym:
                        raise RuntimeError("fmp_symbol required")
                    pts = [
                        {"date": p["date"], "value": p["value"]}
                        for p in _tx_points_for(sym)
                        if p["date"] >= start
                    ]
                    notes.append(
                        f"{key}: FMP transcript keywords {sym} n={len(pts)}"
                    )
                else:
                    by_sym = {
                        s: _tx_points_for(s)
                        for s in [
                            str(x).upper() for x in (meta.get("fmp_symbols") or [])
                        ]
                    }
                    pts, mm = fmplib.aggregate_transcript_keyword_points(
                        by_sym,
                        require_all=bool(meta.get("require_all", False)),
                    )
                    pts = [p for p in pts if p["date"] >= start]
                    notes.append(
                        f"{key}: FMP transcript keywords sum "
                        f"{','.join(mm.get('symbols') or [])} "
                        f"quarters={mm.get('quarters')}"
                    )

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
            "DRAMeXchange DDR5 spot + TE Korea/Taiwan semiconductor exports + "
            "FRED + Census HS 854232 + FMP hyperscaler CapEx (memory_hbm_testing)"
        ),
        "fetched_at": fetched_at,
        "as_of": latest_date,
        "catalog": catalog,
        "attachments": attachments,
        "series": series_out,
        "errors": errors,
        "notes": notes,
        "dramexchange_meta": {
            "fetched_at": (dx_snap or {}).get("fetched_at"),
            "spots": {
                k: {"date": v.get("date"), "value": v.get("value")}
                for k, v in ((dx_snap or {}).get("spots") or {}).items()
            },
        }
        if dx_snap
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
        # Persist spot history alongside pack objects.
        for key in ("ddr5_rdimm_32gb_spot", "ddr5_16gb_die_spot"):
            hist = out_dir / f"{key}_history.jsonl"
            if hist.exists():
                try:
                    r2_uris.append(
                        r2lib.put_bytes(
                            f"{R2_PREFIX}/{hist.name}",
                            hist.read_bytes(),
                            content_type="application/x-ndjson",
                        )
                    )
                except AttributeError:
                    r2_uris.append(
                        r2lib.put_json(
                            f"{R2_PREFIX}/{key}_history.json",
                            {
                                "points": dxlib.points_from_history(hist),
                            },
                        )
                    )
                except Exception:
                    pass
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
