"""NIST NVD CVE 2.0 helpers — weekly Critical publish counts.

Unauthenticated requests are heavily rate-limited; set ``NVD_API_KEY`` when
available. We page in ~90-day windows and bucket by ``published`` date.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

UA = "mosaicbot_themedata/0.1 (+nvd_cve)"
NVD = "https://services.nvd.nist.gov/rest/json/cves/2.0"


def _week_ending_sunday(d: date) -> str:
    return (d + timedelta(days=(6 - d.weekday()))).isoformat()


def densify_week_ending_sunday(
    points: list[dict[str, float]],
    *,
    today: date | None = None,
) -> list[dict[str, float]]:
    """Fill zero weeks on a Sunday-ending spine (for cached / sparse history)."""
    today = today or datetime.now(timezone.utc).date()
    rows = sorted(
        (
            (str(p["date"])[:10], float(p["value"]))
            for p in points
            if p.get("date") is not None and p.get("value") is not None
        ),
        key=lambda x: x[0],
    )
    rows = [(d, v) for d, v in rows if d <= today.isoformat()]
    if not rows:
        return []
    by = {d: v for d, v in rows}
    first = date.fromisoformat(rows[0][0])
    last = date.fromisoformat(rows[-1][0])
    cur_we = date.fromisoformat(_week_ending_sunday(today))
    if last >= cur_we and today < cur_we:
        last = cur_we - timedelta(days=7)
    if last < first:
        return []
    out: list[dict[str, float]] = []
    cur = first
    while cur <= last:
        ds = cur.isoformat()
        out.append({"date": ds, "value": float(by.get(ds, 0.0))})
        cur += timedelta(days=7)
    return out


def _get(params: dict[str, str], *, timeout: int = 90) -> dict[str, Any]:
    q = urllib.parse.urlencode(params)
    headers = {"User-Agent": UA}
    key = (os.environ.get("NVD_API_KEY") or "").strip()
    if key:
        headers["apiKey"] = key
    req = urllib.request.Request(f"{NVD}?{q}", headers=headers)
    last: Exception | None = None
    for i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            # 403/429/503 — back off
            if e.code in (403, 429, 503):
                time.sleep(6.0 * (i + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(2.0 * (i + 1))
    assert last is not None
    raise last


def weekly_critical_publishes(
    *,
    start: str = "2022-01-01",
    severity: str = "CRITICAL",
    sleep_s: float | None = None,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Weekly count of CVEs published with CVSS v3 ``severity``.

    Uses ``cvssV3Severity`` filter. Sleep defaults to 6s without API key
    (NVD guidance) and 0.6s with a key.
    """
    key = (os.environ.get("NVD_API_KEY") or "").strip()
    if sleep_s is None:
        sleep_s = 0.6 if key else 6.0

    start_d = date.fromisoformat(start[:10])
    end_d = datetime.now(timezone.utc).date()
    counts: Counter[str] = Counter()
    windows = 0
    fetched = 0

    cursor = start_d
    while cursor < end_d:
        window_end = min(cursor + timedelta(days=90), end_d)
        # NVD wants ISO-8601 with time
        pub_start = f"{cursor.isoformat()}T00:00:00.000"
        pub_end = f"{window_end.isoformat()}T23:59:59.999"
        start_index = 0
        while True:
            params = {
                "cvssV3Severity": severity,
                "pubStartDate": pub_start,
                "pubEndDate": pub_end,
                "startIndex": str(start_index),
            }
            data = _get(params)
            windows += 1
            total = int(data.get("totalResults") or 0)
            results = data.get("vulnerabilities") or []
            fetched += len(results)
            for row in results:
                cve = (row.get("cve") or {})
                pub = str(cve.get("published") or "")[:10]
                if len(pub) < 10 or pub < start:
                    continue
                try:
                    d = date.fromisoformat(pub)
                except ValueError:
                    continue
                counts[_week_ending_sunday(d)] += 1
            start_index += len(results)
            if start_index >= total or not results:
                break
            time.sleep(sleep_s)
        cursor = window_end + timedelta(days=1)
        time.sleep(sleep_s)

    # Dense weekly spine (zeros for quiet weeks) so lag-52 YoY is calendar-true.
    pts = densify_week_ending_sunday(
        [{"date": we, "value": float(n)} for we, n in counts.items()],
        today=end_d,
    )
    meta = {
        "severity": severity,
        "windows": windows,
        "cves_seen": fetched,
        "points": len(pts),
        "api_key": bool(key),
        "nonzero_weeks": sum(1 for p in pts if p["value"] > 0),
    }
    return pts, meta
