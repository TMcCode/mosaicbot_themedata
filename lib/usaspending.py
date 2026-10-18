"""USASpending.gov helpers — monthly obligation aggregates."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

UA = "mosaicbot_themedata/0.1 (+usaspending)"
SPENDING_OVER_TIME = "https://api.usaspending.gov/api/v2/search/spending_over_time/"

DEFAULT_DEW_KEYWORDS = [
    "directed energy",
    "laser weapon",
    "counter-UAS",
    "high energy laser",
    "HELSI",
]

# Space Supply Chain / surveillance satellites — GEOINT & EO procurement proxy.
DEFAULT_SAT_GEOINT_KEYWORDS = [
    "satellite imagery",
    "geospatial intelligence",
    "GEOINT",
    "Earth observation",
    "space-based surveillance",
    "commercial remote sensing",
]

# Space launch / orbital logistics — NSSL, launch services, OTVs (ex-CLPS).
# CLPS / lunar lander dollars live in DEFAULT_CLPS_KEYWORDS so stack layers
# do not double-count.
DEFAULT_LAUNCH_LOGISTICS_KEYWORDS = [
    "National Security Space Launch",
    "NSSL",
    "launch services",
    "orbital launch",
    "orbital transfer",
    "space tug",
]

DEFAULT_CLPS_KEYWORDS = [
    "Commercial Lunar Payload Services",
    "CLPS",
    "lunar lander",
    "lunar payload",
]

# Genomics sequencing platforms — research/clinical NGS demand proxy (grants).
DEFAULT_GENOMICS_GRANT_KEYWORDS = [
    "next-generation sequencing",
    "next generation sequencing",
    "genomic sequencing",
    "whole genome sequencing",
    "DNA sequencing",
    "RNA sequencing",
    "NGS",
]

# Contract award types (default). Assistance/grant codes for NIH-style awards.
CONTRACT_AWARD_TYPES = ["A", "B", "C", "D"]
GRANT_AWARD_TYPES = ["02", "03", "04", "05"]


def _post(url: str, payload: dict[str, Any], *, timeout: int = 120) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"USASpending HTTP {e.code}: {detail}") from e


def _fy_month_to_date(fiscal_year: str | int, month: str | int) -> str | None:
    """Map USASpending over-time FY + month → YYYY-MM-01.

    API returns fiscal_year + calendar month number for monthly groups in practice;
    treat as calendar year-month labeled under fiscal_year field.
    """
    try:
        y = int(fiscal_year)
        m = int(month)
    except (TypeError, ValueError):
        return None
    if m < 1 or m > 12:
        return None
    return f"{y:04d}-{m:02d}-01"


def fetch_monthly_obligations(
    *,
    keywords: list[str] | None = None,
    recipient_search_text: list[str] | None = None,
    award_type_codes: list[str] | None = None,
    program_numbers: list[str] | None = None,
    start_date: str = "2018-01-01",
    end_date: str = "2030-12-31",
) -> list[dict[str, float]]:
    """Monthly aggregated obligations (USD). Defaults to contracts A–D."""
    filters: dict[str, Any] = {
        "award_type_codes": list(award_type_codes or CONTRACT_AWARD_TYPES),
        "time_period": [{"start_date": start_date, "end_date": end_date}],
    }
    if keywords:
        filters["keywords"] = keywords
    if recipient_search_text:
        filters["recipient_search_text"] = recipient_search_text
    if program_numbers:
        filters["program_numbers"] = list(program_numbers)

    data = _post(SPENDING_OVER_TIME, {"group": "month", "filters": filters})
    points: list[dict[str, float]] = []
    for row in data.get("results") or []:
        tp = row.get("time_period") or {}
        date = _fy_month_to_date(tp.get("fiscal_year"), tp.get("month"))
        if not date or date < start_date:
            continue
        amt = row.get("aggregated_amount")
        if amt is None:
            amt = row.get("Contract_Obligations")
        if amt is None:
            continue
        try:
            v = float(amt)
        except (TypeError, ValueError):
            continue
        if v == 0:
            continue
        points.append({"date": date, "value": v})
    points.sort(key=lambda p: p["date"])
    return points
