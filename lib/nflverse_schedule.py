"""NFL monthly game counts from nflverse schedules (GitHub release).

Pulls ``games.csv.gz`` (~0.5 MB) — same table as the ~2 MB CSV, gzip’d.
Aggregates to monthly counts in memory; CSV is not stored.
Source: https://github.com/nflverse/nflverse-data (schedules release).
"""

from __future__ import annotations

import csv
import gzip
import io
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

UA = "mosaicbot_themedata/0.1 (+nflverse_schedule; +https://stockcontext.info)"
# Gzip asset is ~4× smaller than games.csv; same columns.
CSV_GZ_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "schedules/games.csv.gz"
)
# Fallback if the .gz asset ever moves.
CSV_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "schedules/games.csv"
)


def _http_bytes(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urlopen(req, timeout=90) as resp:
        return resp.read()


def _load_schedule_csv() -> tuple[str, int, str]:
    """Return ``(csv_text, download_bytes, url_used)``."""
    try:
        raw = _http_bytes(CSV_GZ_URL)
        text = gzip.decompress(raw).decode("utf-8", errors="replace")
        return text, len(raw), CSV_GZ_URL
    except Exception:
        raw = _http_bytes(CSV_URL)
        return raw.decode("utf-8", errors="replace"), len(raw), CSV_URL


def fetch_monthly_game_counts(
    *,
    start_year: int = 2017,
    ahead_months: int = 12,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return monthly NFL game counts (regular + playoffs + Super Bowl)."""
    text, nbytes, url_used = _load_schedule_csv()
    rows = list(csv.DictReader(io.StringIO(text)))
    by_month: Counter[str] = Counter()
    sb_dates: list[str] = []
    for r in rows:
        d = (r.get("gameday") or "").strip()
        if len(d) < 10:
            continue
        if d[:4] < f"{start_year:04d}":
            continue
        key = f"{d[:7]}-01"
        by_month[key] += 1
        if (r.get("game_type") or "").strip().upper() == "SB":
            sb_dates.append(d)

    today = datetime.now(timezone.utc).date()
    y, m = today.year, today.month
    for _ in range(max(0, ahead_months)):
        m += 1
        if m > 12:
            y, m = y + 1, 1
    horizon = f"{y:04d}-{m:02d}-01"

    pts = [
        {"date": d, "value": float(v)}
        for d, v in sorted(by_month.items())
        if d <= horizon
    ]
    if not pts:
        raise RuntimeError("nflverse schedules: no monthly points after filter")

    meta = {
        "source": "nflverse_schedules",
        "url": url_used,
        "bytes": nbytes,
        "n_rows": len(rows),
        "n_months": len(pts),
        "n_games": int(sum(p["value"] for p in pts)),
        "horizon": horizon,
        "super_bowls": sb_dates[-8:],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    return pts, meta
