"""Shared pack series helpers (derived metrics + naming fields)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from lib import transforms as tx


def derive_metrics(
    points: list[dict[str, Any]],
    derived_names: list[str],
    *,
    frequency: str,
) -> dict[str, Any]:
    lag = 4 if frequency == "quarterly" else 12
    # Weekly YoY ≈ 52 weeks; annual YoY = 1 period
    if frequency == "weekly":
        lag = 52
    elif frequency == "annual":
        lag = 1
    metrics: dict[str, Any] = {}
    for name in derived_names:
        if name == "yoy_pct":
            series = tx.yoy_pct(points, lag=lag)
            metrics[name] = {
                "unit": "pct",
                "points": series,
                "latest": tx.latest(series),
            }
        elif name == "m3_avg_yoy_pct":
            window = 3
            yoy_lag = 12 if frequency not in ("weekly", "annual") else (52 if frequency == "weekly" else 1)
            series = tx.rolling_mean_yoy_pct(points, window=window, lag=yoy_lag)
            metrics[name] = {
                "unit": "pct",
                "points": series,
                "latest": tx.latest(series),
            }
        elif name == "m3_net_change":
            series = tx.lag_diff(points, lag=3)
            metrics[name] = {
                "unit": "level_diff",
                "points": series,
                "latest": tx.latest(series),
            }
        elif name == "wow_pct":
            series = tx.yoy_pct(points, lag=1)  # 1-period % change
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
    if source_id_field and meta.get(source_id_field):
        out[source_id_field] = meta[source_id_field]
    # common id aliases
    for k in ("fred_id", "bls_id", "manifold_id", "hs6", "hs_code"):
        if meta.get(k):
            out[k] = meta[k]
    return out
