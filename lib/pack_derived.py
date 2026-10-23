"""Shared derived_sum post-pass for pack jobs."""

from __future__ import annotations

from typing import Any

from lib import pack_series as ps


def apply_derived_sums(
    catalog: dict[str, Any],
    series_out: dict[str, Any],
    *,
    notes: list[str],
    errors: list[str],
    fetched_at: str | None = None,
) -> str:
    """Build ``source=derived_sum`` series from ``sum_of`` keys already in series_out.

    Returns max latest date among derived series ('' if none).
    """
    latest_date = ""
    for meta in catalog.get("series") or []:
        if meta.get("source") != "derived_sum":
            continue
        key = meta.get("key")
        if not key:
            continue
        try:
            parts = list(meta.get("sum_of") or [])
            if not parts:
                raise RuntimeError("derived_sum missing sum_of")
            missing = [k for k in parts if k not in series_out]
            if missing:
                raise RuntimeError(f"missing components: {missing}")
            by_date: dict[str, float] = {}
            counts: dict[str, int] = {}
            for part_key in parts:
                for p in series_out[part_key]["raw"]["points"]:
                    d, v = p.get("date"), p.get("value")
                    if d is None or v is None:
                        continue
                    ds = str(d)[:10]
                    by_date[ds] = by_date.get(ds, 0.0) + float(v)
                    counts[ds] = counts.get(ds, 0) + 1
            n_expected = len(parts)
            dates_sorted = sorted(by_date)
            peak = max((counts[d] for d in dates_sorted), default=0)
            latest_d = dates_sorted[-1] if dates_sorted else None
            latest_incomplete = bool(
                latest_d
                and peak > 0
                and 0 < counts.get(latest_d, 0) < peak
            )
            pts: list[dict] = []
            for d in dates_sorted:
                row: dict = {
                    "date": d,
                    "value": round(by_date[d], 4),
                    "n_contributors": counts.get(d, 0),
                    "n_expected": n_expected,
                }
                if latest_incomplete and d == latest_d:
                    row["incomplete"] = True
                pts.append(row)
            if not pts:
                raise RuntimeError("no points after sum")
            series_out[key] = ps.series_shell(
                meta, points=pts, fetched_at=fetched_at
            )
            notes.append(f"{key}: derived_sum of {parts}")
            latest = series_out[key]["raw"]["latest"]
            if latest and latest.get("date"):
                latest_date = max(latest_date, str(latest["date"]))
        except Exception as e:
            errors.append(f"{key}: {e}")
    return latest_date
