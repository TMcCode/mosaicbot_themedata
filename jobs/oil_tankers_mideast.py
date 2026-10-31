#!/usr/bin/env python3
"""Supply Shock in MidEast Long '26: Oil Tankers pack; publish to R2.

Theme slug: supply-shock-in-mideast-long-26-oil-tankers.

Hormuz tanker transits (IMF PortWatch / straits.live) + EIA US crude exports
+ Brent + FMP member revenue + INSW crude/product + DHT/FRO/NAT voyage vs TC
+ BWET freight ETF proxy + wiki VLCC. Forums sidecar separate.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
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
from lib import fmp_prices as fmp_priceslib  # noqa: E402
from lib import eia_pet as eialib  # noqa: E402
from lib import hormuz_transits as hormuzlib  # noqa: E402
from lib import theme_score as scores  # noqa: E402
from lib.pack_derived import apply_derived_sums  # noqa: E402

SCHEMA_VERSION = 0
PACK = "oil_tankers_mideast"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "oil_tankers_mideast_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
UA_WIKI = "mosaicbot_themedata/0.1 (https://stockcontext.info; oil-tankers pageviews)"


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


def _wiki_monthly(article: str, *, start: str) -> list[dict[str, Any]]:
    """Monthly pageview totals for one Wikipedia article (enwiki)."""
    end = datetime.now(timezone.utc).strftime("%Y%m%d")
    start_d = start.replace("-", "")[:8]
    if len(start_d) < 8:
        start_d = "20150101"
    url = (
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        f"en.wikipedia/all-access/user/{urllib.parse.quote(article, safe='')}/"
        f"monthly/{start_d}/{end}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA_WIKI})
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    pts: list[dict[str, Any]] = []
    for row in data.get("items") or []:
        ts = str(row.get("timestamp") or "")
        if len(ts) < 8:
            continue
        d = f"{ts[:4]}-{ts[4:6]}-01"
        if d < start:
            continue
        pts.append({"date": d, "value": float(row.get("views") or 0)})
    pts.sort(key=lambda p: p["date"])
    return pts


def build_pack(
    *,
    catalog: dict,
    attachments: dict,
    start_year: int,
    skip_eia: bool = False,
    skip_hormuz: bool = False,
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

    hormuz_daily: list[dict[str, Any]] = []
    if not skip_hormuz and "hormuz_transits" in sources:
        try:
            hormuz_daily, hm = hormuzlib.fetch_daily_tanker_transits()
            notes.append(
                f"hormuz: days={hm.get('n_days')} as_of={hm.get('as_of')} "
                f"latest_nTanker={hm.get('latest_n_tanker')} "
                f"pre_crisis_median={hm.get('pre_crisis_baseline_median')}"
            )
        except Exception as e:
            notes.append(f"hormuz soft-fail: {e}")

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
            elif src == "hormuz_transits":
                if not hormuz_daily:
                    raise RuntimeError("hormuz daily empty")
                agg = str(meta.get("hormuz_agg") or "weekly").lower()
                if agg == "monthly":
                    pts = hormuzlib.to_monthly_sum(hormuz_daily)
                else:
                    pts = hormuzlib.to_weekly_sum(hormuz_daily)
                pts = [p for p in pts if p["date"] >= start]
                notes.append(f"{key}: hormuz {agg} n={len(pts)}")
            elif src == "fmp_eod_monthly":
                sym = str(meta.get("fmp_symbol") or "").upper()
                pts, mm = fmp_priceslib.fetch_monthly_avg_close(sym, start=start)
                notes.append(
                    f"{key}: FMP EOD {sym} months={mm.get('n_months')} "
                    f"days={mm.get('n_days')}"
                )
            elif src == "wikipedia_pageviews":
                art = str(meta.get("wiki_article") or "")
                pts = _wiki_monthly(art, start=start)
                time.sleep(0.2)
                notes.append(f"{key}: wiki {art} n={len(pts)}")
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
            "straits.live Hormuz (IMF PortWatch) + EIA WCREXUS2 + FRED Brent + "
            "FMP revenue/segments/BWET EOD + Wikipedia VLCC"
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
    p.add_argument("--skip-hormuz", action="store_true")
    args = p.parse_args()

    load_env()
    catalog = _load_json(_CATALOG)
    attachments = _load_attachments()
    try:
        payload = build_pack(
            catalog=catalog,
            attachments=attachments,
            start_year=args.start_year,
            skip_eia=args.skip_eia,
            skip_hormuz=args.skip_hormuz,
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
