"""BTS Border Crossing/Entry — US–Mexico truck crossings.

Public Socrata dataset (no API key):
  https://data.bts.gov/resource/keg4-3bc2.json
Docs: https://www.bts.gov/explore-topics-and-geography/geography/border-crossingentry-data
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BTS_RESOURCE = "https://data.bts.gov/resource/keg4-3bc2.json"
UA = "mosaicbot_themedata/0.1 (+bts_border)"


def _get(params: dict[str, str]) -> list[dict[str, Any]]:
    url = f"{BTS_RESOURCE}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=60) as resp:
        raw = resp.read()
        status = getattr(resp, "status", None) or resp.getcode()
    if status != 200:
        raise RuntimeError(f"BTS HTTP {status}")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise RuntimeError(f"BTS unexpected payload: {type(data)}")
    return data


def fetch_truck_crossings(
    *,
    port_name: str | None = None,
    border: str = "US-Mexico Border",
    measure: str = "Trucks",
) -> list[dict[str, float]]:
    """Monthly truck crossing totals (sum of ``value`` by month).

    ``port_name=None`` → all ports on ``border``.
    ``port_name="Laredo"`` → Laredo TX only (primary nearshoring corridor).
    Dates are month-start ISO (YYYY-MM-01).
    """
    where = f"border='{border}' AND measure='{measure}'"
    if port_name:
        # Escape single quotes in SoQL
        safe = str(port_name).replace("'", "''")
        where += f" AND port_name='{safe}'"
    rows = _get(
        {
            "$select": "date,sum(value) as trucks",
            "$where": where,
            "$group": "date",
            "$order": "date ASC",
            "$limit": "5000",
        }
    )
    out: list[dict[str, float]] = []
    for row in rows:
        d = str(row.get("date") or "")[:10]
        if not d:
            continue
        # Normalize to YYYY-MM-01
        if len(d) >= 7:
            d = f"{d[:7]}-01"
        try:
            v = float(row.get("trucks") or 0)
        except (TypeError, ValueError):
            continue
        out.append({"date": d, "value": v})
    return out
