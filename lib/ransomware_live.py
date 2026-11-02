"""ransomware.live leak-site victim claims → weekly counts.

Public JSON at https://data.ransomware.live/victims.json (no auth).
Each row is a *claim* by a ransomware group, not a confirmed breach.
"""

from __future__ import annotations

import json
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

UA = "mosaicbot_themedata/0.1 (+ransomware_live)"
VICTIMS_JSON = "https://data.ransomware.live/victims.json"


def _week_ending_sunday(d: date) -> str:
    return (d + timedelta(days=(6 - d.weekday()))).isoformat()


def _parse_day(raw: Any) -> date | None:
    s = str(raw or "").strip()
    if not s:
        return None
    # ISO datetime or date
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def fetch_victims(*, timeout: int = 120) -> list[dict[str, Any]]:
    req = urllib.request.Request(VICTIMS_JSON, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, list):
        raise RuntimeError(f"ransomware.live unexpected type {type(data)}")
    return data


def _dense_weekly(
    counts: Counter[str], *, today: date
) -> list[dict[str, float]]:
    keys = sorted(we for we in counts if we <= today.isoformat())
    if not keys:
        return []
    first = date.fromisoformat(keys[0])
    last = date.fromisoformat(keys[-1])
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


def weekly_victim_claims(
    *,
    start: str = "2020-01-01",
    victims: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Weekly count of leak-site victim claims by discovery (fallback: published).

    Dense Sunday spine for calendar-aligned weekly YoY.
    """
    rows = victims if victims is not None else fetch_victims()
    counts: Counter[str] = Counter()
    used = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        d = _parse_day(row.get("discovered")) or _parse_day(row.get("published"))
        if d is None or d.isoformat() < start:
            continue
        counts[_week_ending_sunday(d)] += 1
        used += 1
    today = datetime.now(timezone.utc).date()
    pts = _dense_weekly(counts, today=today)
    meta = {
        "source": "ransomware.live",
        "url": VICTIMS_JSON,
        "victims_raw": len(rows),
        "matched": used,
        "points": len(pts),
        "nonzero_weeks": sum(1 for p in pts if p["value"] > 0),
        "notes": (
            "Leak-site claims only — not confirmed breaches; "
            "bucketed by discovered (else published)."
        ),
    }
    return pts, meta
