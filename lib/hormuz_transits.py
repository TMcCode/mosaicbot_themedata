"""Strait of Hormuz tanker transit counts (IMF PortWatch via straits.live).

Free JSON, no API key. Daily history is capped (~365 days on the public
endpoint); we store what we get and append going forward.

Source: https://straits.live/api (methodology: IMF PortWatch chokepoint6)
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

UA = "mosaicbot_themedata/0.1 (+hormuz_transits; +https://stockcontext.info)"
TRANSITS_URL = "https://straits.live/api/v1/transits?history=1&limit=365"


def _http_json(url: str) -> Any:
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def fetch_daily_tanker_transits() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Daily Hormuz tanker transit counts (nTanker)."""
    payload = _http_json(TRANSITS_URL)
    hist = payload.get("history") or []
    pts: list[dict[str, Any]] = []
    for row in hist:
        d = str(row.get("date") or "")[:10]
        v = row.get("nTanker")
        if len(d) < 10 or v is None:
            continue
        try:
            pts.append({"date": d, "value": float(v)})
        except (TypeError, ValueError):
            continue
    pts.sort(key=lambda p: p["date"])
    if not pts:
        raise RuntimeError("straits.live transits: no nTanker history rows")

    latest = payload.get("latest") or {}
    meta = {
        "source": "straits_live_imf_portwatch",
        "url": TRANSITS_URL,
        "as_of": payload.get("asOf") or latest.get("date"),
        "methodology": payload.get("methodology"),
        "n_days": len(pts),
        "latest_n_tanker": latest.get("nTanker"),
        "pre_crisis_baseline_median": latest.get("preCrisisBaselineMedian"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    return pts, meta


def to_weekly_sum(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sum daily tanker counts into ISO weeks (week-ending Sunday as date)."""
    from datetime import date, timedelta

    buckets: dict[str, float] = defaultdict(float)
    for p in daily:
        d = date.fromisoformat(p["date"])
        # Week ending Sunday
        end = d + timedelta(days=(6 - d.weekday()) % 7)
        if d.weekday() == 6:
            end = d
        buckets[end.isoformat()] += float(p["value"])
    return [
        {"date": d, "value": round(v, 2)}
        for d, v in sorted(buckets.items())
    ]


def to_monthly_sum(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, float] = defaultdict(float)
    for p in daily:
        key = f"{p['date'][:7]}-01"
        buckets[key] += float(p["value"])
    return [
        {"date": d, "value": round(v, 2)}
        for d, v in sorted(buckets.items())
    ]
