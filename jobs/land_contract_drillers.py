#!/usr/bin/env python3
"""Energy Services '26: Land Contract Drillers pack.

Theme slug: energy-services-26-land-contract-drillers.

HP NA/Intl Solutions + Saudi/Argentina geo + NBR Drilling Solutions /
International / Rig Technologies + PTEN Drilling (ex-Completion) + PDS /
ESI.TO / TOT.TO / 2381.SR + CapEx + drilling PPI/IP + Henry Hub + transcripts
+ Kalshi TX oil + USASpending drilling keywords + wiki. WTI reused from
energy_bottleneck_us_refiners. Forums sidecar separate.
"""

from __future__ import annotations

import argparse
import json
import re
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
from lib import theme_score as scores  # noqa: E402
from lib import fmp_pack as fmp_packlib  # noqa: E402
from lib import kalshi as kalshilib  # noqa: E402
from lib import kalshi_pack  # noqa: E402
from lib import fred as fredlib  # noqa: E402
from lib import usaspending as usa  # noqa: E402
from lib import tradingeconomics as teapi  # noqa: E402
from lib.pack_derived import apply_derived_sums, apply_derived_ratios  # noqa: E402

SCHEMA_VERSION = 0
PACK = "land_contract_drillers"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "land_contract_drillers_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
UA_WIKI = "mosaicbot_themedata/0.1 (https://stockcontext.info; land_contract_drillers pageviews)"


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


def _te_ticker_points(
    out_dir: Path,
    *,
    series_key: str,
    te_symbol: str,
    start: str,
    backfill: bool,
) -> tuple[list[dict[str, Any]], str]:
    """Local jsonl + TE ``historical/ticker`` (Baker Hughes via TE)."""
    hist_path = out_dir / f"{series_key}_history.jsonl"
    r2_key = f"{R2_PREFIX}/{series_key}_history.jsonl"
    rows: list[dict[str, Any]] = []
    if hist_path.exists():
        for line in hist_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        try:
            raw = r2lib.get_bytes(r2_key)
            if raw:
                for line in raw.decode("utf-8").splitlines():
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
        except Exception:
            pass

    note = f"TE ticker {te_symbol} cached={len(rows)}"
    if backfill or not rows:
        try:
            te_rows = teapi.ticker_historical(
                te_symbol,
                d1=start[:10],
                force=backfill,
                allow_reserve=True,
            )
            pts = teapi.ticker_to_points(te_rows)
            by = {p["date"]: p["value"] for p in pts}
            for r in rows:
                d = str(r.get("date") or "")[:10]
                if d and d not in by and r.get("value") is not None:
                    by[d] = float(r["value"])
            rows = [{"date": d, "value": by[d]} for d in sorted(by)]
            out_dir.mkdir(parents=True, exist_ok=True)
            hist_path.write_text(
                "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
                encoding="utf-8",
            )
            try:
                r2lib.put_bytes(
                    r2_key,
                    hist_path.read_bytes(),
                    content_type="application/x-ndjson",
                )
            except Exception as e:
                note += f"; R2 hist soft-fail {e}"
            note = f"TE ticker {te_symbol} n={len(rows)} (pulled)"
        except teapi.TeBudgetError as e:
            note = f"TE ticker {te_symbol} budget: {e} cached={len(rows)}"
        except Exception as e:
            note = f"TE ticker {te_symbol} soft-fail: {e} cached={len(rows)}"

    pts = [
        {"date": str(r["date"])[:10], "value": float(r["value"])}
        for r in rows
        if r.get("date") and r.get("value") is not None and str(r["date"])[:10] >= start
    ]
    return pts, note



def _wiki_monthly(article: str, *, start: str) -> list[dict[str, Any]]:
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
    out_dir: Path,
    te_backfill: bool = False,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    start = f"{start_year:04d}-01-01"
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    notes: list[str] = [
        "Land contract drillers — super-spec dayrates + NOC backlog + automation; "
        "reuse WTI via energy_bottleneck_us_refiners. Ex-PTEN Completion / offshore. "
        "BH US oil/total rigs via TE (direct BH IR Akamai-403).",
    ]
    latest_date = ""
    fmp_caches = fmp_packlib.FmpCaches.from_catalog(catalog)

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
                notes.append(f"{key}: FRED {sid} ({len(pts)} pts)")
            elif src == "te_ticker":
                sym = str(meta.get("te_symbol") or "")
                if not sym:
                    raise RuntimeError("te_symbol required")
                pts, note = _te_ticker_points(
                    out_dir,
                    series_key=str(key),
                    te_symbol=sym,
                    start=start,
                    backfill=te_backfill,
                )
                notes.append(note)
            elif src == "wikipedia_pageviews":
                art = str(meta.get("wiki_article") or "")
                pts = _wiki_monthly(art, start=start)
                time.sleep(0.2)
                notes.append(f"{key}: wiki {art} n={len(pts)}")
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
                    f"{key}: USASpending keywords={keywords[:4]}… n={len(pts)}"
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
            "FMP land-driller segments/CapEx + BH US oil/total rigs (TE) + "
            "drilling PPI/IP + HH + transcripts + Kalshi TX oil + USASpending + Wikipedia"
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
    return uris


def run(
    *,
    out_dir: Path,
    start_year: int = 2018,
    dry_run: bool = False,
    skip_r2: bool = False,
    te_backfill: bool = False,
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
    )
    if dry_run:
        return payload
    _write_local(out_dir, payload)
    r2_uris: list[str] = []
    if not skip_r2:
        r2_uris.extend(_publish_r2(payload))
        try:
            r2_uris.append(
                r2lib.put_json(
                    "themedata/series_attachments.json", payload["attachments"]
                )
            )
        except Exception:
            pass
        try:
            detail = scores.recompute_and_publish(
                payload,
                scores_dir=_SCORES_OUT,
                skip_r2=False,
                sync_recipe=True,
                prefer_remote_recipe=False,
            )
            payload["_theme_scores_detail"] = detail
            r2_uris.extend(detail.get("r2_uris") or [])
        except Exception as e:
            payload.setdefault("errors", []).append(f"theme_scores: {e}")
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
        help="Force TE historical/ticker pull for BH US oil/total rigs",
    )
    args = p.parse_args(argv)
    try:
        payload = run(
            out_dir=args.out_dir,
            te_backfill=args.te_backfill,
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
