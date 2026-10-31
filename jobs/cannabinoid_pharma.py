#!/usr/bin/env python3
"""Cannabis '24: Cannabinoid Pharmaceuticals & Therapeutics pack.

Theme slug: cannabis-24-cannabinoid-pharmaceuticals-therapeutics.

Jazz Epidiolex (+ Sativex) product revenue + Epidiolex/Jazz share +
ClinicalTrials / PubMed / NIH cannabinoid pipeline + openFDA FAERS Epidiolex
AEs + clinical-stage R&D stack + FRED DFF + wiki Epidiolex/rescheduling.
Forums sidecar separate. Differentiates from MSO / recreational cannabis themes.
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
from lib import pubmed as pubmedlib  # noqa: E402
from lib import theme_score as scores  # noqa: E402
from lib import fmp_pack as fmp_packlib  # noqa: E402
from lib import clinicaltrials as ctlib  # noqa: E402
from lib import nih_reporter as nihlib  # noqa: E402
from lib import openfda_faers as faerslib  # noqa: E402
from lib import fred as fredlib  # noqa: E402
from lib.pack_derived import apply_derived_sums, apply_derived_ratios  # noqa: E402

SCHEMA_VERSION = 0
PACK = "cannabinoid_pharma"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "cannabinoid_pharma_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
UA_WIKI = "mosaicbot_themedata/0.1 (https://stockcontext.info; cannabinoid-pharma pageviews)"


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
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    start = f"{start_year:04d}-01-01"
    series_out: dict[str, Any] = {}
    errors: list[str] = []
    notes: list[str] = [
        "Cannabinoid Rx pack — Epidiolex commercial proof + pipeline pulse; "
        "not MSO/rec cannabis. Kalshi mjreschedule unavailable → wiki "
        "rescheduling pageviews stand in for policy narrative.",
    ]
    latest_date = ""
    fmp_caches = fmp_packlib.FmpCaches.from_catalog(catalog)
    pubmed_hist = out_dir / pubmedlib.HISTORY_NAME

    for meta in catalog.get("series") or []:
        key = meta.get("key")
        if not key:
            continue
        src = meta.get("source")
        if src in ("derived_sum", "derived_ratio"):
            continue
        try:
            if src == "pubmed":
                qid = str(meta.get("pubmed_query_id") or key)
                term = str(meta.get("pubmed_term") or "").strip()
                if not term:
                    raise RuntimeError("pubmed_term required")
                pstart = str(meta.get("pubmed_start") or f"{start_year:04d}-01")
                pts, mm = pubmedlib.refresh_query(
                    history_path=pubmed_hist,
                    query_id=qid,
                    term=term,
                    start=pstart,
                    refresh_months=int(meta.get("pubmed_refresh_months") or 4),
                )
                pts = [p for p in pts if p["date"] >= start]
                notes.append(
                    f"{key}: PubMed {qid!r} n={mm.get('n_points')} "
                    f"fetched={mm.get('fetched_months')}"
                )
            elif src == "clinicaltrials":
                term = str(meta.get("clinicaltrials_term") or "").strip() or None
                cstart = str(meta.get("clinicaltrials_start") or f"{start_year:04d}-01")
                pts, mm = ctlib.monthly_first_post_counts(term=term, start=cstart)
                pts = [p for p in pts if p["date"] >= start]
                notes.append(
                    f"{key}: ClinicalTrials n={mm.get('months')} "
                    f"errors={len(mm.get('errors') or [])}"
                )
            elif src == "nih_reporter":
                text = str(meta.get("nih_search_text") or "").strip() or None
                pts, mm = nihlib.monthly_award_amounts(search_text=text)
                pts = [p for p in pts if p["date"] >= start]
                notes.append(
                    f"{key}: NIH RePORTER projects={mm.get('projects')} "
                    f"months={len(pts)}"
                )
            elif src == "openfda_faers":
                search = str(meta.get("faers_search") or "").strip() or None
                fstart = str(meta.get("faers_start") or "20180101")
                pts, mm = faerslib.monthly_ae_counts(search=search, start=fstart)
                pts = [p for p in pts if p["date"] >= start]
                notes.append(
                    f"{key}: FAERS months={mm.get('months')} "
                    f"day_buckets={mm.get('day_buckets')}"
                )
                for n in mm.get("notes") or []:
                    notes.append(n)
            elif src == "fred":
                sid = str(meta["fred_id"])
                pts = [
                    {"date": d if len(d) == 10 else d[:10], "value": v}
                    for d, v in fredlib.fetch_series(sid)
                    if d >= start
                ]
                notes.append(f"{key}: FRED {sid} ({len(pts)} pts)")
            elif src == "wikipedia_pageviews":
                art = str(meta.get("wiki_article") or "")
                pts = _wiki_monthly(art, start=start)
                time.sleep(0.2)
                notes.append(f"{key}: wiki {art} n={len(pts)}")
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
            "FMP Jazz Epidiolex/Sativex + clinical R&D + PubMed/ClinicalTrials/"
            "NIH cannabinoid + openFDA FAERS Epidiolex + FRED DFF + Wikipedia"
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
    hist = out_dir / pubmedlib.HISTORY_NAME
    if hist.exists():
        try:
            data = json.loads(hist.read_text(encoding="utf-8"))
            uris.append(r2lib.put_json(f"{R2_PREFIX}/{pubmedlib.HISTORY_NAME}", data))
        except Exception:
            pass
    return uris


def run(
    *,
    out_dir: Path,
    start_year: int = 2018,
    dry_run: bool = False,
    skip_r2: bool = False,
) -> dict[str, Any]:
    load_env()
    catalog = _load_json(_CATALOG)
    attachments = _load_attachments()
    payload = build_pack(
        catalog=catalog,
        attachments=attachments,
        start_year=start_year,
        out_dir=out_dir,
    )
    if dry_run:
        return payload
    _write_local(out_dir, payload)
    r2_uris: list[str] = []
    if not skip_r2:
        r2_uris.extend(_publish_r2(payload, out_dir))
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
    args = p.parse_args(argv)
    try:
        payload = run(
            out_dir=args.out_dir,
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
