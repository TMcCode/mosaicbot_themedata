"""ClinicalTrials.gov v2 — monthly counts of newly posted studies.

Uses ``filter.advanced`` ``AREA[StudyFirstPostDate]RANGE[…]`` + ``countTotal``
so we never page thousands of study bodies. Soft research pulse for
ctDNA / MRD / liquid-biopsy / MCED trial activity.
"""

from __future__ import annotations

import calendar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any

UA = "mosaicbot_themedata/0.1 (+lib.clinicaltrials; CT.gov v2 research use)"
API = "https://clinicaltrials.gov/api/v2/studies"

DEFAULT_TERM = (
    '(ctDNA OR "circulating tumor DNA" OR "minimal residual disease" OR MRD '
    'OR "liquid biopsy" OR MCED OR "multi-cancer early detection" '
    'OR "multi cancer early detection")'
)


def _month_iter(start: str, end: str | None = None) -> list[str]:
    sy, sm = int(start[:4]), int(start[5:7])
    if end:
        ey, em = int(end[:4]), int(end[5:7])
    else:
        today = date.today()
        ey, em = today.year, today.month
    out: list[str] = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def _month_range(ym: str) -> tuple[str, str, str]:
    y, m = int(ym[:4]), int(ym[5:7])
    last = calendar.monthrange(y, m)[1]
    return (
        f"{y:04d}-{m:02d}-01",
        f"{y:04d}-{m:02d}-01",
        f"{y:04d}-{m:02d}-{last:02d}",
    )


def count_posted(
    term: str,
    *,
    start_date: str,
    end_date: str,
) -> int:
    params = {
        "query.term": term,
        "filter.advanced": (
            f"AREA[StudyFirstPostDate]RANGE[{start_date},{end_date}]"
        ),
        "pageSize": "1",
        "countTotal": "true",
    }
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return int(payload.get("totalCount") or 0)


def monthly_first_post_counts(
    *,
    term: str | None = None,
    start: str = "2018-01",
    sleep_s: float = 0.35,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One point per calendar month: studies first-posted that month."""
    q = (term or DEFAULT_TERM).strip()
    pts: list[dict[str, Any]] = []
    errors: list[str] = []
    for ym in _month_iter(start):
        point_date, d0, d1 = _month_range(ym)
        try:
            n = count_posted(q, start_date=d0, end_date=d1)
            pts.append({"date": point_date, "value": float(n)})
        except Exception as e:
            errors.append(f"{ym}: {e}")
        time.sleep(sleep_s)
    meta = {
        "source": "clinicaltrials.gov/api/v2",
        "term": q,
        "months": len(pts),
        "errors": errors[:12],
    }
    return pts, meta
