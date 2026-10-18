"""Growth / rolling transforms on dated level series."""

from __future__ import annotations

from datetime import date, timedelta
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


def _parse_iso(d: str) -> date | None:
    try:
        return date.fromisoformat(d[:10])
    except ValueError:
        return None


def yoy_pct(points: list[dict[str, Any]], *, lag: int) -> list[dict[str, Any]]:
    """Period YoY % change via fixed observation lag (12=monthly, 52=weekly, …)."""
    rows = _sorted_points(points)
    out: list[dict[str, Any]] = []
    for i in range(lag, len(rows)):
        d, v = rows[i]
        _, v0 = rows[i - lag]
        if v0 == 0:
            continue
        out.append({"date": d, "value": (v / v0 - 1.0) * 100.0})
    return out


def yoy_pct_calendar(
    points: list[dict[str, Any]], *, years: int = 1, lookback_days: int = 7
) -> list[dict[str, Any]]:
    """Calendar YoY for irregular/daily series: vs same date last year (or nearest prior print).

    Walks back up to ``lookback_days`` from the anniversary when that session is missing
    (weekends / holidays). Prefer this over a fixed lag for daily AMS prints.
    """
    rows = _sorted_points(points)
    by_date = {d: v for d, v in rows}
    out: list[dict[str, Any]] = []
    for d, v in rows:
        cur = _parse_iso(d)
        if cur is None:
            continue
        try:
            target = cur.replace(year=cur.year - years)
        except ValueError:
            # Feb 29 → Feb 28 prior year
            target = cur.replace(year=cur.year - years, day=28)
        v0: float | None = None
        for back in range(lookback_days + 1):
            key = (target - timedelta(days=back)).isoformat()
            if key in by_date:
                v0 = by_date[key]
                break
        if v0 is None or v0 == 0:
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


def rolling_mean_of_series(
    points: list[dict[str, Any]], *, window: int
) -> list[dict[str, Any]]:
    """Trailing mean of an already-computed series (e.g. 4W MA of YoY %)."""
    return rolling_mean_levels(points, window=window)


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


def series_spread(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """left − right on shared dates (e.g. menu YoY − grocery YoY)."""
    right_by = {str(p["date"]): float(p["value"]) for p in _sorted_points_as_dicts(right)}
    out: list[dict[str, Any]] = []
    for d, v in _sorted_points(left):
        if d not in right_by:
            continue
        out.append({"date": d, "value": v - right_by[d]})
    return out


def _sorted_points_as_dicts(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"date": d, "value": v} for d, v in _sorted_points(points)]
