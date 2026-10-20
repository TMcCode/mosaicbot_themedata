"""Google Political Ads Transparency Report — weekly US Spend_USD totals.

Source (free, redistributable):
  https://storage.googleapis.com/political-csv/google-political-ads-transparency-bundle.zip
  file: ``google-political-ads-advertiser-weekly-spend.csv``

We sum ``Spend_USD`` by ``Week_Start_Date``. Non-US advertisers report in local
currency columns with ``Spend_USD=0``, so USD totals match US-region advertisers.

Point date = Google week-start (typically Sunday). ``as_of_semantics: vendor_as_of``.
Google/YouTube election ads only — not total digital or AdImpact channel mix.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

UA = "mosaicbot_themedata/0.1 (+lib.google_political_ads)"
BUNDLE_URL = (
    "https://storage.googleapis.com/political-csv/"
    "google-political-ads-transparency-bundle.zip"
)
WEEKLY_NAME = "google-political-ads-advertiser-weekly-spend.csv"


def _download_bundle(*, timeout: float = 300) -> bytes:
    req = Request(BUNDLE_URL, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def weekly_us_spend_points(
    *,
    start: str | None = None,
    bundle_bytes: bytes | None = None,
    cache_path: Path | None = None,
    max_cache_age_hours: float = 20.0,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Return weekly USD points + scrape meta.

    Point ``date`` = Google ``Week_Start_Date`` (Sunday-start weeks).
    If ``cache_path`` is set, reuse a downloaded zip newer than
    ``max_cache_age_hours`` to avoid re-pulling ~300MB every run.
    """
    meta: dict[str, Any] = {"url": BUNDLE_URL, "file": WEEKLY_NAME}
    raw = bundle_bytes
    if raw is None and cache_path is not None and cache_path.exists():
        age_h = (
            datetime.now(timezone.utc).timestamp() - cache_path.stat().st_mtime
        ) / 3600.0
        if age_h <= max_cache_age_hours:
            raw = cache_path.read_bytes()
            meta["cache"] = "hit"
            meta["cache_age_hours"] = round(age_h, 2)
    if raw is None:
        raw = _download_bundle()
        meta["cache"] = "miss" if cache_path else "none"
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(raw)

    meta["bundle_bytes"] = len(raw)
    by_week: dict[str, float] = defaultdict(float)
    n_rows = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        with zf.open(WEEKLY_NAME) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
            for row in csv.DictReader(text):
                n_rows += 1
                week = (row.get("Week_Start_Date") or "").strip()
                if not week:
                    continue
                try:
                    amt = float(str(row.get("Spend_USD") or "0").replace(",", ""))
                except ValueError:
                    continue
                if amt <= 0:
                    continue
                by_week[week[:10]] += amt

    # Drop incomplete current week (week-start >= this week's Sunday).
    today = datetime.now(timezone.utc).date()
    this_week_start = today - timedelta(days=(today.weekday() + 1) % 7)
    points: list[dict[str, float]] = []
    for week_start in sorted(by_week):
        if start and week_start < start[:10]:
            continue
        if week_start >= this_week_start.isoformat():
            continue
        points.append({"date": week_start, "value": float(by_week[week_start])})

    meta.update(
        {
            "rows_read": n_rows,
            "weeks": len(points),
            "first": points[0]["date"] if points else None,
            "last": points[-1]["date"] if points else None,
            "excluded_incomplete_week_start": this_week_start.isoformat(),
        }
    )
    return points, meta
