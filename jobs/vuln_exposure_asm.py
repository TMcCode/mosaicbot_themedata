#!/usr/bin/env python3
"""Cybersecurity '26: Vulnerability, Exposure & Attack Surface Management pack.

Theme slug: cybersecurity-26-vulnerability-exposure-attack-surface-management.

FMP pure-play VM + platform security stacks, quality segments, CTEM/EASM
transcripts, CISA KEV weekly adds, NVD Critical weekly, USASpending cyber
keywords. Reuses software_ipp (ai_resilient) + CES 5415 (devops) via
attachments/source_pack. Forums separate.
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
from lib import theme_score as scores  # noqa: E402
from lib import fmp_pack as fmp_packlib  # noqa: E402
from lib import usaspending as usa  # noqa: E402
from lib import cisa_kev as kevlib  # noqa: E402
from lib import nvd_cve as nvdlib  # noqa: E402
from lib.pack_derived import apply_derived_sums, apply_derived_ratios  # noqa: E402

SCHEMA_VERSION = 0
PACK = "vuln_exposure_asm"
R2_PREFIX = f"themedata/{PACK}"
_DEFAULT_OUT = _ROOT / "data" / PACK
_SCORES_OUT = _ROOT / "data" / "scores"
_CATALOG = _ROOT / "config" / "vuln_exposure_asm_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
_NVD_HISTORY = "nvd_critical_weekly_history.json"


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


def _merge_weekly_points(
    existing: list[dict[str, float]],
    fresh: list[dict[str, float]],
) -> list[dict[str, float]]:
    by = {str(p["date"]): float(p["value"]) for p in existing if p.get("date")}
    for p in fresh:
        d = str(p.get("date") or "")
        if not d:
            continue
        by[d] = float(p["value"])
    return [{"date": d, "value": by[d]} for d in sorted(by)]


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
        "reuse via attachments/source_pack: software_ipp_investment "
        "(ai_resilient_software); computer_systems_design_emp "
        "(devops_observability).",
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
            if src == "usaspending":
                keywords = list(meta.get("keywords") or [])
                pts = usa.fetch_monthly_obligations(
                    keywords=keywords or None,
                    start_date=start,
                )
                notes.append(
                    f"{key}: USASpending keywords={keywords or None} n={len(pts)}"
                )
            elif src == "cisa_kev":
                # Default skips catalog-launch dump (287 in week of 2021-11-07)
                # + early backlog waves that pin chart y-axes near 300.
                kev_start = str(meta.get("kev_start") or "2022-07-01")
                pts, meta_k = kevlib.weekly_net_new(start=max(start, kev_start))
                notes.append(
                    f"{key}: CISA KEV weekly adds n={len(pts)} "
                    f"start={kev_start} catalog={meta_k.get('catalog_count')} "
                    f"ver={meta_k.get('catalog_version')}"
                )
            elif src == "nvd_cve":
                import os

                hist_path = out_dir / _NVD_HISTORY
                prev: list[dict[str, float]] = []
                if hist_path.exists():
                    try:
                        prev = json.loads(hist_path.read_text(encoding="utf-8"))
                        if not isinstance(prev, list):
                            prev = []
                    except Exception:
                        prev = []
                if (os.environ.get("THEMEDATA_SKIP_NVD") or "").strip() in (
                    "1",
                    "true",
                    "yes",
                ):
                    pts = nvdlib.densify_week_ending_sunday(prev)
                    notes.append(
                        f"{key}: skipped NVD fetch (THEMEDATA_SKIP_NVD); "
                        f"history n={len(pts)}"
                    )
                    if not pts:
                        raise RuntimeError("NVD skipped and no local history")
                else:
                    nvd_start = str(meta.get("nvd_start") or "2024-01-01")
                    severity = str(meta.get("nvd_severity") or "CRITICAL")
                    fresh, meta_n = nvdlib.weekly_critical_publishes(
                        start=nvd_start, severity=severity
                    )
                    pts = nvdlib.densify_week_ending_sunday(
                        _merge_weekly_points(prev, fresh)
                    )
                    out_dir.mkdir(parents=True, exist_ok=True)
                    hist_path.write_text(
                        json.dumps(pts, indent=2) + "\n", encoding="utf-8"
                    )
                    notes.append(
                        f"{key}: NVD {severity} weekly n={len(pts)} "
                        f"(fetched={meta_n.get('cves_seen')} "
                        f"key={meta_n.get('api_key')})"
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
            "FMP pure-play VM + platform security revenue/segments + "
            "CTEM/EASM transcripts + CISA KEV + NVD Critical + USASpending cyber"
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
    start_year: int,
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
        out_dir=out_dir,
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
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT)
    p.add_argument("--start-year", type=int, default=2015)
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
