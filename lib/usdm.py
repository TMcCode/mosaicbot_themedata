"""U.S. Drought Monitor (USDM) area-percent stats via NDMC REST API.

Traditional statisticsType=1: D1 = % of area in D1 or worse (cumulative).
State AOI uses FIPS codes (e.g. FL=12), not abbreviations.
"""

from __future__ import annotations

import csv
import io
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Iterable

UA = "mosaicbot_themedata/0.1 (+lib.usdm)"
_STATE = (
    "https://usdmdataservices.unl.edu/api/StateStatistics/"
    "GetDroughtSeverityStatisticsByAreaPercent"
)

# Southeast outdoor / lawn-garden footprint for TSCO/LESL
SE_STATE_FIPS: tuple[str, ...] = (
    "01",  # AL
    "12",  # FL
    "13",  # GA
    "22",  # LA
    "28",  # MS
    "37",  # NC
    "45",  # SC
    "47",  # TN
)


def _get_csv(url: str, *, retries: int = 4) -> str:
    last: Exception | None = None
    for i in range(retries):
        req = urllib.request.Request(
            url, headers={"User-Agent": UA, "Accept": "text/csv"}
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(1.2 * (i + 1))
    assert last is not None
    raise last


def _fmt_mdy(d: date) -> str:
    return f"{d.month}/{d.day}/{d.year}"


def fetch_state_d1_or_worse(
    fips: str,
    *,
    start: date,
    end: date,
) -> list[tuple[str, float]]:
    """Weekly (ValidStart, D1%) for one state FIPS. Traditional cumulative D1+."""
    qs = urllib.parse.urlencode(
        {
            "aoi": str(fips).zfill(2),
            "startdate": _fmt_mdy(start),
            "enddate": _fmt_mdy(end),
            "statisticsType": 1,
        }
    )
    raw = _get_csv(f"{_STATE}?{qs}")
    if not raw.strip():
        return []
    rows: list[tuple[str, float]] = []
    for row in csv.DictReader(io.StringIO(raw)):
        d = (row.get("ValidStart") or "")[:10]
        v = row.get("D1")
        if not d or v in (None, ""):
            continue
        rows.append((d, float(v)))
    rows.sort(key=lambda x: x[0])
    return rows


def fetch_region_mean_d1_or_worse(
    fips_list: Iterable[str],
    *,
    start: date,
    end: date,
) -> list[tuple[str, float]]:
    """Unweighted mean of state D1-or-worse % by week across ``fips_list``."""
    by_date: dict[str, list[float]] = {}
    for fips in fips_list:
        for d, v in fetch_state_d1_or_worse(fips, start=start, end=end):
            by_date.setdefault(d, []).append(v)
        time.sleep(0.35)
    return [
        (d, round(sum(vs) / len(vs), 4))
        for d, vs in sorted(by_date.items())
        if vs
    ]
