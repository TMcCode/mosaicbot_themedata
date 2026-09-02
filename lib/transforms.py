"""Growth / rolling transforms on dated level series."""

from __future__ import annotations

from typing import Any


def _sorted_points(points: list[dict[str, Any]]) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for p in points:
        d, v = p.get("date"), p.get("value")
        if d is None or v is None:
            continue
        out.append((str(d), float(v)))
    out.sort(key=lambda x: x[0])
    return out


def yoy_pct(points: list[dict[str, Any]], *, lag: int) -> list[dict[str, Any]]:
    """Year-over-year % change. lag=12 monthly, lag=4 quarterly."""
    rows = _sorted_points(points)
    out: list[dict[str, Any]] = []
    for i in range(lag, len(rows)):
        d, v = rows[i]
        _, v0 = rows[i - lag]
        if v0 == 0:
            continue
        out.append({"date": d, "value": (v / v0 - 1.0) * 100.0})
    return out


def rolling_mean_levels(points: list[dict[str, Any]], *, window: int) -> list[dict[str, Any]]:
    rows = _sorted_points(points)
    out: list[dict[str, Any]] = []
    for i in range(window - 1, len(rows)):
        chunk = rows[i - window + 1 : i + 1]
        avg = sum(v for _, v in chunk) / window
        out.append({"date": rows[i][0], "value": avg})
    return out


def rolling_mean_yoy_pct(
    points: list[dict[str, Any]], *, window: int = 3, lag: int = 12
) -> list[dict[str, Any]]:
    """YoY % of an N-period rolling mean of levels (e.g. 3M-avg YoY)."""
    return yoy_pct(rolling_mean_levels(points, window=window), lag=lag)


def lag_diff(points: list[dict[str, Any]], *, lag: int) -> list[dict[str, Any]]:
    """Level change vs lag periods ago (e.g. 3M net job additions)."""
    rows = _sorted_points(points)
    out: list[dict[str, Any]] = []
    for i in range(lag, len(rows)):
        d, v = rows[i]
        _, v0 = rows[i - lag]
        out.append({"date": d, "value": v - v0})
    return out


def latest(points: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not points:
        return None
    rows = _sorted_points(points)
    if not rows:
        return None
    d, v = rows[-1]
    return {"date": d, "value": v}
