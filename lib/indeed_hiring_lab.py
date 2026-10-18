"""Indeed Hiring Lab Job Postings Index — public GitHub CSV.

Source: https://github.com/hiring-lab/job_postings_tracker
US sector file includes ``Food Preparation & Service`` (seasonally adjusted
index, Feb 1 2020 = 100). Free redistributable research data.
"""

from __future__ import annotations

import csv
import io
from typing import Any
from urllib.request import Request, urlopen

SECTOR_CSV_URL = (
    "https://raw.githubusercontent.com/hiring-lab/job_postings_tracker/"
    "master/US/job_postings_by_sector_US.csv"
)
UA = "mosaicbot_themedata/0.1 (+indeed_hiring_lab; public GitHub CSV)"
DEFAULT_SECTOR = "Food Preparation & Service"
DEFAULT_VARIABLE = "total postings"


def fetch_sector_csv(url: str = SECTOR_CSV_URL) -> str:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=120) as resp:
        return resp.read().decode("utf-8")


def points_food_service(
    *,
    sector: str = DEFAULT_SECTOR,
    variable: str = DEFAULT_VARIABLE,
    url: str = SECTOR_CSV_URL,
) -> list[dict[str, float]]:
    """Return daily SA index points for Food Preparation & Service."""
    text = fetch_sector_csv(url)
    reader = csv.DictReader(io.StringIO(text))
    pts: list[dict[str, float]] = []
    for row in reader:
        if (row.get("display_name") or "").strip() != sector:
            continue
        if (row.get("variable") or "").strip() != variable:
            continue
        d = (row.get("date") or "").strip()
        raw = row.get("indeed_job_postings_index")
        if not d or raw in (None, ""):
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        pts.append({"date": d, "value": v})
    pts.sort(key=lambda p: p["date"])
    return pts


def weekly_last(points: list[dict[str, float]]) -> list[dict[str, float]]:
    """Downsample daily index to one point per ISO week (last observation)."""
    by_week: dict[str, dict[str, float]] = {}
    for p in points:
        d = str(p["date"])
        # ISO week ending Sunday-ish: use YYYY-Www as key via date parts
        from datetime import date as date_cls

        dt = date_cls.fromisoformat(d)
        # Monday-based ISO week → Sunday end
        iso = dt.isocalendar()
        # week key = year-week
        key = f"{iso.year:04d}-W{iso.week:02d}"
        by_week[key] = p
    out = sorted(by_week.values(), key=lambda p: p["date"])
    return out


def fetch_meta() -> dict[str, Any]:
    return {
        "source": SECTOR_CSV_URL,
        "sector": DEFAULT_SECTOR,
        "variable": DEFAULT_VARIABLE,
        "provider": "Indeed Hiring Lab",
    }
