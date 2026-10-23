"""TSA national checkpoint travel numbers (daily → weekly).

Official HTML tables:
  https://www.tsa.gov/travel/passenger-volumes
  https://www.tsa.gov/travel/passenger-volumes/<YYYY>

Chart-only leisure-travel proxy — not cruise-specific.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 "
    "mosaicbot_themedata/0.1 (+lib.tsa_throughput)"
)
BASE = "https://www.tsa.gov/travel/passenger-volumes"
_ROW_RE = re.compile(
    r"<tr[^>]*>\s*<td[^>]*>([^<]+)</td>\s*<td[^>]*>([^<]+)</td>",
    re.I,
)


def _get(url: str, *, timeout: float = 60, retries: int = 3) -> str:
    last: Exception | None = None
    for i in range(retries):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(1.2 * (i + 1))
    assert last is not None
    raise last


def _parse_rows(html: str) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for d_raw, v_raw in _ROW_RE.findall(html):
        d_s = d_raw.strip()
        v_s = v_raw.strip().replace(",", "")
        try:
            dt = datetime.strptime(d_s, "%m/%d/%Y").date()
            val = float(v_s)
        except ValueError:
            continue
        out.append((dt.isoformat(), val))
    return out


def fetch_daily(
    *,
    start_year: int = 2019,
    end_year: int | None = None,
) -> list[tuple[str, float]]:
    """Return ``(YYYY-MM-DD, passengers)`` ascending, deduped by date."""
    end = end_year or datetime.now(timezone.utc).year
    by: dict[str, float] = {}
    # Current year page (rolling) + archives.
    urls = [BASE] + [f"{BASE}/{y}" for y in range(start_year, end + 1)]
    notes_errs: list[str] = []
    for url in urls:
        try:
            html = _get(url)
            for d, v in _parse_rows(html):
                by[d] = v
            time.sleep(0.35)
        except Exception as e:
            notes_errs.append(f"{url}: {e}")
    if not by:
        raise RuntimeError(
            "TSA scrape empty: " + "; ".join(notes_errs[:4])
            if notes_errs
            else "TSA scrape empty"
        )
    return sorted(by.items(), key=lambda x: x[0])


def _week_ending_sunday(d: date) -> str:
    ahead = (6 - d.weekday()) % 7
    end = d + timedelta(days=ahead)
    return end.isoformat()


def to_weekly_sum(daily: list[tuple[str, float]]) -> list[dict[str, float]]:
    """Sum daily passengers into week-ending Sunday buckets."""
    buckets: dict[str, float] = {}
    for d_s, v in daily:
        we = _week_ending_sunday(date.fromisoformat(d_s[:10]))
        buckets[we] = buckets.get(we, 0.0) + float(v)
    return [
        {"date": we, "value": round(buckets[we], 0)}
        for we in sorted(buckets)
    ]


def fetch_weekly_points(
    *,
    start: str = "2019-01-01",
    start_year: int = 2019,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    daily = fetch_daily(start_year=start_year)
    weekly = [p for p in to_weekly_sum(daily) if p["date"] >= start]
    meta = {
        "n_daily": len(daily),
        "n_weekly": len(weekly),
        "daily_first": daily[0][0] if daily else None,
        "daily_last": daily[-1][0] if daily else None,
        "source": BASE,
    }
    return weekly, meta
