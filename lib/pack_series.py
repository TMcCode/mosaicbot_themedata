"""Shared pack series helpers (derived metrics + naming fields)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from lib import transforms as tx


def _yoy_lag(frequency: str) -> int:
    f = (frequency or "").strip().lower()
    if f == "quarterly":
        return 4
    if f in ("semiannual", "semi-annual", "semi_annual", "half", "biannual"):
        return 2
    if f == "weekly":
        return 52
    if f in ("annual", "yearly"):
        return 1
    if f == "daily":
        # Prefer calendar YoY via yoy_pct_calendar; lag kept as fallback only.
        return 252
    return 12


def complete_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop partial panel prints (``incomplete: true``) for YoY / scores."""
    return [p for p in points if not p.get("incomplete")]


def derive_metrics(
    points: list[dict[str, Any]],
    derived_names: list[str],
    *,
    frequency: str,
) -> dict[str, Any]:
    lag = _yoy_lag(frequency)
    # Growth metrics must not compare against an incomplete current quarter.
    pts_for_growth = complete_points(points)
    metrics: dict[str, Any] = {}
    for name in derived_names:
        if name == "yoy_pct":
            if frequency == "daily":
                series = tx.yoy_pct_calendar(pts_for_growth, years=1)
            else:
                series = tx.yoy_pct(pts_for_growth, lag=lag)
            metrics[name] = {
                "unit": "pct",
                "points": series,
                "latest": tx.latest(series),
            }
        elif name == "m3_avg":
            series = tx.rolling_mean_levels(pts_for_growth, window=3)
            metrics[name] = {
                "unit": "level",  # same dimension as raw; UI may reuse series unit
                "points": series,
                "latest": tx.latest(series),
            }
        elif name in ("m4_avg", "wma4"):
            window = 20 if frequency == "daily" else 4
            series = [
                {"date": p["date"], "value": round(float(p["value"]), 2)}
                for p in tx.rolling_mean_levels(pts_for_growth, window=window)
            ]
            metrics[name] = {
                "unit": "level",
                "points": series,
                "latest": tx.latest(series),
            }
        elif name == "m3_avg_yoy_pct":
            # Monthly (and default): YoY of 3M avg via calendar date, not a fixed
            # print lag — gappy sources (USASpending) otherwise compare to the
            # wrong year-ago month and invent ±100% cliffs.
            window = 3
            avg = tx.rolling_mean_levels(pts_for_growth, window=window)
            freq = (frequency or "").strip().lower()
            if freq in ("", "monthly", "month"):
                series = [
                    {"date": p["date"], "value": round(float(p["value"]), 4)}
                    for p in tx.yoy_pct_calendar(avg, years=1)
                ]
            else:
                series = tx.rolling_mean_yoy_pct(
                    pts_for_growth, window=window, lag=_yoy_lag(frequency)
                )
            metrics[name] = {
                "unit": "pct",
                "points": series,
                "latest": tx.latest(series),
            }
        elif name == "wma4_yoy_pct":
            # Weekly/other: YoY % of the 4-period average (same idea as m3_avg_yoy_pct).
            # Daily: calendar YoY then ~20-session MA (irregular sessions).
            if frequency == "daily":
                yoy = tx.yoy_pct_calendar(pts_for_growth, years=1)
                series = [
                    {"date": p["date"], "value": round(float(p["value"]), 4)}
                    for p in tx.rolling_mean_of_series(yoy, window=20)
                ]
            else:
                window = 4
                series = [
                    {"date": p["date"], "value": round(float(p["value"]), 4)}
                    for p in tx.rolling_mean_yoy_pct(
                        pts_for_growth, window=window, lag=lag
                    )
                ]
            metrics[name] = {
                "unit": "pct",
                "points": series,
                "latest": tx.latest(series),
            }
        elif name == "yoy_4y_pct":
            # Same calendar point ~4 years prior (midterm↔midterm / prez↔prez).
            if frequency == "daily":
                series = tx.yoy_pct_calendar(pts_for_growth, years=4)
            else:
                series = tx.yoy_pct(pts_for_growth, lag=lag * 4)
            metrics[name] = {
                "unit": "pct",
                "points": series,
                "latest": tx.latest(series),
            }
        elif name == "wma4_yoy_4y_pct":
            if frequency == "daily":
                yoy = tx.yoy_pct_calendar(pts_for_growth, years=4)
                window = 20
            else:
                yoy = tx.yoy_pct(pts_for_growth, lag=lag * 4)
                window = 4
            series = [
                {"date": p["date"], "value": round(float(p["value"]), 4)}
                for p in tx.rolling_mean_of_series(yoy, window=window)
            ]
            metrics[name] = {
                "unit": "pct",
                "points": series,
                "latest": tx.latest(series),
            }
        elif name == "m3_avg_yoy_4y_pct":
            series = tx.rolling_mean_yoy_pct(
                pts_for_growth, window=3, lag=lag * 4
            )
            metrics[name] = {
                "unit": "pct",
                "points": series,
                "latest": tx.latest(series),
            }
        elif name == "m3_net_change":
            series = tx.lag_diff(pts_for_growth, lag=3)
            metrics[name] = {
                "unit": "level_diff",
                "points": series,
                "latest": tx.latest(series),
            }
        elif name == "wow_pct":
            # Daily: ~5 sessions ≈ week; else 1 period.
            wow_lag = 5 if frequency == "daily" else 1
            series = tx.yoy_pct(pts_for_growth, lag=wow_lag)
            metrics[name] = {
                "unit": "pct",
                "points": series,
                "latest": tx.latest(series),
            }
        else:
            raise ValueError(f"unknown derived metric: {name}")
    return metrics


def series_shell(
    meta: dict[str, Any],
    *,
    points: list[dict[str, Any]],
    source_id_field: str | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """Build pack series object: naming + raw + derived + provenance from catalog meta."""
    freq = str(meta.get("frequency") or "monthly")
    derived_names = list(meta.get("derived") or [])
    fetched = fetched_at or datetime.now(timezone.utc).isoformat()
    out: dict[str, Any] = {
        "key": meta["key"],
        "label": meta.get("label"),
        "official_name": meta.get("official_name"),
        "short_name": meta.get("short_name"),
        "display_name": meta.get("display_name"),
        "source": meta.get("source"),
        "dataset_id": meta.get("dataset_id") or meta.get("key"),
        "access": meta.get("access"),
        "cadence": meta.get("cadence") or freq,
        "frequency": freq,
        "unit": meta.get("unit"),
        "usefulness": meta.get("usefulness"),
        "as_of_semantics": meta.get("as_of_semantics") or "period_end",
        "raw": {
            "points": points,
            "latest": tx.latest(points),
        },
        "derived": derive_metrics(points, derived_names, frequency=freq),
        "provenance": {
            "fetched_at": fetched,
            "access": meta.get("access"),
            "source": meta.get("source"),
            "as_of_semantics": meta.get("as_of_semantics") or "period_end",
            # Point `date` = vendor/period as_of; fetched_at = when we pulled.
            "notes": (
                "Point dates are vendor/period as_of. "
                "provenance.fetched_at is pull time (not the economic date)."
            ),
        },
    }
    # Frozen chart serial (source-family-dataset-series); see lib/serials.py.
    if meta.get("serial"):
        out["serial"] = meta["serial"]
        for k in (
            "serial_source",
            "serial_family",
            "serial_dataset_n",
            "serial_series_n",
        ):
            if meta.get(k) is not None:
                out[k] = meta[k]
    if source_id_field and meta.get(source_id_field):
        out[source_id_field] = meta[source_id_field]
    # common id aliases
    for k in ("fred_id", "bls_id", "manifold_id", "hs6", "hs_code"):
        if meta.get(k):
            out[k] = meta[k]
    return out
