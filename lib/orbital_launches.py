"""Global orbital launch counts via Launch Library 2 (The Space Devs).

Free tier is rate-limited (~15 req/hr unauthenticated). We fetch quarter-sized
windows (usually one page), cache monthly totals locally, and only refresh the
recent window on routine runs.
"""

from __future__ import annotations

import calendar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

UA = "mosaicbot_themedata/0.1 (+lib.orbital_launches)"
LL2 = "https://ll.thespacedevs.com/2.2.0/launch/"

# Attempted orbital launches (exclude Go / TBD future).
_STATUS_OK = {
    "Launch Successful",
    "Launch Failure",
    "Launch was a Partial Failure",
}


def _get(url: str, *, retries: int = 4, fail_fast_429: bool = False) -> dict[str, Any]:
    last: Exception | None = None
    for i in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if (
                fail_fast_429
                and isinstance(e, urllib.error.HTTPError)
                and e.code == 429
            ):
                raise
            # 429 → wait longer
            wait = 8.0 * (i + 1)
            if isinstance(e, urllib.error.HTTPError) and e.code == 429:
                wait = 65.0
            time.sleep(wait)
    assert last is not None
    raise last


def _quarter_windows(start: date, end: date) -> list[tuple[date, date]]:
    windows: list[tuple[date, date]] = []
    y, m = start.year, ((start.month - 1) // 3) * 3 + 1
    cur = date(y, m, 1)
    while cur <= end:
        q_end_m = cur.month + 2
        q_end_y = cur.year
        if q_end_m > 12:
            q_end_m -= 12
            q_end_y += 1
        q_last = date(
            q_end_y, q_end_m, calendar.monthrange(q_end_y, q_end_m)[1]
        )
        w0 = max(cur, start)
        w1 = min(q_last, end)
        if w0 <= w1:
            windows.append((w0, w1))
        # next quarter
        nm = cur.month + 3
        ny = cur.year
        if nm > 12:
            nm -= 12
            ny += 1
        cur = date(ny, nm, 1)
    return windows


def fetch_window_counts(
    start: date, end: date, *, fail_fast_429: bool = False
) -> Counter:
    """Count launches by YYYY-MM for NET in [start, end] (inclusive)."""
    counts: Counter = Counter()
    offset = 0
    while True:
        qs = urllib.parse.urlencode(
            {
                "mode": "list",
                "limit": 100,
                "ordering": "net",
                "offset": offset,
                "net__gte": f"{start.isoformat()}T00:00:00Z",
                "net__lte": f"{end.isoformat()}T23:59:59Z",
            }
        )
        data = _get(f"{LL2}?{qs}", fail_fast_429=fail_fast_429)
        batch = data.get("results") or []
        if not batch:
            break
        for row in batch:
            st = ((row.get("status") or {}).get("name") or "").strip()
            if st not in _STATUS_OK:
                continue
            net = row.get("net") or ""
            if len(net) < 7:
                continue
            counts[net[:7]] += 1
        offset += len(batch)
        total = int(data.get("count") or 0)
        if offset >= total or len(batch) < 100:
            break
        time.sleep(0.8)
    return counts


def load_cache(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("monthly"), dict):
        return {str(k): float(v) for k, v in raw["monthly"].items()}
    return {}


def save_cache(path: Path, monthly: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Launch Library 2 (ll.thespacedevs.com)",
        "monthly": {k: monthly[k] for k in sorted(monthly)},
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def refresh_monthly(
    cache_path: Path,
    *,
    start_year: int = 2015,
    full_backfill: bool = False,
) -> list[tuple[str, float]]:
    """Return sorted (YYYY-MM-01, count) points; update cache."""
    today = date.today()
    monthly = load_cache(cache_path)

    if full_backfill:
        windows = _quarter_windows(date(start_year, 1, 1), today)
    elif not monthly:
        # First fill: last ~3 years only (LL2 free tier is ~15 req/hr).
        fill_start = date(max(start_year, today.year - 3), 1, 1)
        windows = _quarter_windows(fill_start, today)
    else:
        # Refresh last ~9 months (3 quarters)
        refresh_start = date(today.year, today.month, 1)
        for _ in range(8):
            m = refresh_start.month - 1
            y = refresh_start.year
            if m < 1:
                m = 12
                y -= 1
            refresh_start = date(y, m, 1)
        windows = _quarter_windows(refresh_start, today)

    # Prefer existing cache when rate-limited (Wikipedia seed / prior LL2 fill).
    fail_fast = bool(monthly) and not full_backfill
    try:
        for w0, w1 in windows:
            c = fetch_window_counts(w0, w1, fail_fast_429=fail_fast)
            # Clear months in window then rewrite so incomplete months don't stale-high
            y, m = w0.year, w0.month
            while date(y, m, 1) <= w1:
                key = f"{y:04d}-{m:02d}"
                monthly[key] = float(c.get(key, 0))
                m += 1
                if m > 12:
                    m = 1
                    y += 1
            time.sleep(1.0)
        save_cache(cache_path, monthly)
    except urllib.error.HTTPError as e:
        # Free-tier 429: keep seeded/prior cache rather than failing the pack.
        if e.code == 429 and monthly:
            save_cache(cache_path, monthly)
        else:
            raise

    return [
        (f"{ym}-01", v)
        for ym, v in sorted(monthly.items())
        if ym >= f"{start_year:04d}-01" and v >= 0
    ]
