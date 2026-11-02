"""Shared derived_sum / derived_ratio post-pass for pack jobs."""

from __future__ import annotations

from typing import Any

from lib import pack_series as ps
from lib import transforms as tlib


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
            # Match FMP multi-ticker stacks: require_all drops thin panels so
            # lag YoY does not compare a 1-of-N print to a full-panel prior year.
            require_all = bool(meta.get("require_all", False))
            dates_sorted = sorted(by_date)
            pts: list[dict] = []
            for d in dates_sorted:
                n_hit = counts.get(d, 0)
                if require_all and n_hit < n_expected:
                    continue
                row: dict = {
                    "date": d,
                    "value": round(by_date[d], 4),
                    "n_contributors": n_hit,
                    "n_expected": n_expected,
                }
                # Any thin panel (vendor gap or earnings lag) — keep on charts,
                # but YoY / theme scores ignore via incomplete (not tip-only).
                if 0 < n_hit < n_expected:
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


def apply_derived_ratios(
    catalog: dict[str, Any],
    series_out: dict[str, Any],
    *,
    notes: list[str],
    errors: list[str],
    fetched_at: str | None = None,
) -> str:
    """Build ``source=derived_ratio`` series: scale * numerator / denominator.

    Catalog fields: ``numerator``, ``denominator``, optional ``ratio_scale``
    (default 100 → percent share).
    """
    latest_date = ""
    for meta in catalog.get("series") or []:
        if meta.get("source") != "derived_ratio":
            continue
        key = meta.get("key")
        if not key:
            continue
        try:
            num_key = str(meta.get("numerator") or "").strip()
            den_key = str(meta.get("denominator") or "").strip()
            if not num_key or not den_key:
                raise RuntimeError("derived_ratio needs numerator + denominator")
            if num_key not in series_out or den_key not in series_out:
                raise RuntimeError(f"missing components: {num_key}/{den_key}")
            scale = float(meta.get("ratio_scale") if meta.get("ratio_scale") is not None else 100.0)
            pts = tlib.series_ratio(
                series_out[num_key]["raw"]["points"],
                series_out[den_key]["raw"]["points"],
                scale=scale,
            )
            if not pts:
                raise RuntimeError("no points after ratio")
            series_out[key] = ps.series_shell(
                meta, points=pts, fetched_at=fetched_at
            )
            notes.append(f"{key}: derived_ratio {num_key}/{den_key} *{scale}")
            latest = series_out[key]["raw"]["latest"]
            if latest and latest.get("date"):
                latest_date = max(latest_date, str(latest["date"]))
        except Exception as e:
            errors.append(f"{key}: {e}")
    return latest_date
