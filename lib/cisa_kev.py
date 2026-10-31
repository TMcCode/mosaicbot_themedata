"""CISA Known Exploited Vulnerabilities (KEV) catalog helpers.

Public JSON feed — bucket ``dateAdded`` into weekly / monthly counts.
No auth. History comes from the live catalog (each CVE keeps its add date).
"""

from __future__ import annotations

import json
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

UA = "mosaicbot_themedata/0.1 (+cisa_kev)"
KEV_JSON = (
    "https://www.cisa.gov/sites/default/files/feeds/"
    "known_exploited_vulnerabilities.json"
)


def _week_ending_sunday(d: date) -> str:
    # Monday=0 … Sunday=6 → advance to Sunday
    return (d + timedelta(days=(6 - d.weekday()))).isoformat()


def fetch_catalog(*, timeout: int = 90) -> dict[str, Any]:
    req = urllib.request.Request(KEV_JSON, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _dense_weekly(
    counts: Counter[str], *, today: date
) -> list[dict[str, float]]:
    """Fill zero weeks so lag-52 YoY is calendar-aligned (not observation-index)."""
    keys = sorted(we for we in counts if we <= today.isoformat())
    if not keys:
        return []
    first = date.fromisoformat(keys[0])
    last = date.fromisoformat(keys[-1])
    # Drop in-progress week (week-ending Sunday still ahead / today mid-week).
    cur_we = date.fromisoformat(_week_ending_sunday(today))
    if last >= cur_we and today < cur_we:
        last = cur_we - timedelta(days=7)
    if last < first:
        return []
    pts: list[dict[str, float]] = []
    cur = first
    while cur <= last:
        ds = cur.isoformat()
        pts.append({"date": ds, "value": float(counts.get(ds, 0))})
        cur += timedelta(days=7)
    return pts


def weekly_net_new(
    *,
    start: str = "2021-11-01",
    catalog: dict[str, Any] | None = None,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Weekly count of KEV entries by ``dateAdded`` (week-ending Sunday).

    Dense spine (zeros for quiet weeks) so weekly YoY lag-52 matches calendar.
    """
    cat = catalog or fetch_catalog()
    counts: Counter[str] = Counter()
    for v in cat.get("vulnerabilities") or []:
        raw = str(v.get("dateAdded") or "")[:10]
        if len(raw) < 10 or raw < start:
            continue
        try:
            d = date.fromisoformat(raw)
        except ValueError:
            continue
        counts[_week_ending_sunday(d)] += 1
    today = datetime.now(timezone.utc).date()
    pts = _dense_weekly(counts, today=today)
    meta = {
        "catalog_version": cat.get("catalogVersion"),
        "date_released": cat.get("dateReleased"),
        "catalog_count": cat.get("count"),
        "points": len(pts),
        "nonzero_weeks": sum(1 for p in pts if p["value"] > 0),
    }
    return pts, meta


def monthly_net_new(
    *,
    start: str = "2021-11-01",
    catalog: dict[str, Any] | None = None,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Monthly count of KEV adds (``YYYY-MM-01``)."""
    cat = catalog or fetch_catalog()
    counts: Counter[str] = Counter()
    for v in cat.get("vulnerabilities") or []:
        raw = str(v.get("dateAdded") or "")[:10]
        if len(raw) < 10 or raw < start:
            continue
        counts[raw[:7] + "-01"] += 1
    today = datetime.now(timezone.utc).date()
    month_cap = f"{today.year:04d}-{today.month:02d}-01"
    pts = [
        {"date": m, "value": float(n)}
        for m, n in sorted(counts.items())
        if m < month_cap  # drop incomplete current month
    ]
    meta = {
        "catalog_version": cat.get("catalogVersion"),
        "date_released": cat.get("dateReleased"),
        "catalog_count": cat.get("count"),
        "points": len(pts),
    }
    return pts, meta
