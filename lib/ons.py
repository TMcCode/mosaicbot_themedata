"""ONS (UK Office for National Statistics) public timeseries via beta API.

No API key. Fetches monthly points for a known timeseries URI under
``/businessindustryandtrade/...`` (or any ONS timeseries path).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

UA = "mosaicbot_themedata/0.1 (+ons)"
API = "https://api.beta.ons.gov.uk/v1/data"

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _month_num(label: str) -> int | None:
    return _MONTHS.get((label or "").strip().lower())


def fetch_timeseries(uri: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return ``({date, value}, ...)`` month points + description blob.

    ``uri`` is the ONS path, e.g.
    ``/businessindustryandtrade/retailindustry/timeseries/j5dz/drsi``.
    """
    path = uri if uri.startswith("/") else f"/{uri}"
    url = f"{API}?uri={quote(path, safe='/')}"
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=90) as resp:
        raw = json.loads(resp.read().decode("utf-8"))

    points: list[dict[str, Any]] = []
    for row in raw.get("months") or []:
        year = row.get("year")
        month = _month_num(str(row.get("month") or ""))
        value = row.get("value")
        if not year or not month or value in (None, "", "-"):
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        points.append({"date": f"{int(year):04d}-{month:02d}-01", "value": v})
    points.sort(key=lambda p: p["date"])
    meta = {
        "uri": raw.get("uri") or path,
        "description": raw.get("description") or {},
        "title": (raw.get("description") or {}).get("title")
        or (raw.get("description") or {}).get("cdnTitle"),
        "source_datasets": raw.get("sourceDatasets") or [],
    }
    return points, meta
