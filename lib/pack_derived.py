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
            for part_key in parts:
                for p in series_out[part_key]["raw"]["points"]:
                    d, v = p.get("date"), p.get("value")
                    if d is None or v is None:
                        continue
                    by_date[str(d)[:10]] = by_date.get(str(d)[:10], 0.0) + float(v)
            pts = [{"date": d, "value": round(by_date[d], 4)} for d in sorted(by_date)]
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
