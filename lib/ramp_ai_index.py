"""Ramp AI Index — free public series embedded in https://ramp.com/data/ai-index.

No API key. Parses Next.js flight/HTML for published arrays (adoption, spend).
Partner API (`/v1/public/ai-index`) stays optional if ``RAMP_DATA_API_KEY`` is set.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

UA = "mosaicbot_themedata/0.1 (+ramp_ai_index)"
PAGE_URL = "https://ramp.com/data/ai-index"
API_URL = "https://api.ramp.com/developer/v1/public/ai-index"

# Embedded array names we care about on the public page.
_ARRAY_NAMES = (
    "adoptionOverall",
    "adoptionUsEstimate",
    "adoptionVendor",
    "adoptionIndustry",
    "spendPerEmployee",
)


def _http_get(url: str, *, headers: dict[str, str] | None = None) -> str:
    h = {"User-Agent": UA, "Accept": "text/html,application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _extract_array(html: str, name: str) -> list[dict[str, Any]] | None:
    """Pull a named JSON array from escaped Next.js payload or raw JSON."""
    m = re.search(rf'\\"{re.escape(name)}\\":(\[.*?\])', html)
    if not m:
        m = re.search(rf'"{re.escape(name)}":(\[.*?\])', html)
    if not m:
        return None
    raw = m.group(1).replace('\\"', '"')
    data = json.loads(raw)
    if not isinstance(data, list):
        return None
    return [r for r in data if isinstance(r, dict)]


def fetch_page_payload() -> dict[str, list[dict[str, Any]]]:
    """Return named arrays scraped from the public AI Index page."""
    html = _http_get(PAGE_URL)
    out: dict[str, list[dict[str, Any]]] = {}
    for name in _ARRAY_NAMES:
        rows = _extract_array(html, name)
        if rows:
            out[name] = rows
    if "adoptionOverall" not in out:
        raise RuntimeError("Ramp AI Index page missing adoptionOverall embed")
    return out


def fetch_api_payload() -> dict[str, Any] | None:
    """Optional partner API when ``RAMP_DATA_API_KEY`` is set; else None."""
    key = (os.environ.get("RAMP_DATA_API_KEY") or "").strip()
    if not key:
        return None
    try:
        body = _http_get(
            API_URL,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        return json.loads(body)
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return None


def _num(v: Any) -> float | None:
    if v is None or v == "" or v == "$undefined":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _month(row: dict[str, Any]) -> str | None:
    d = row.get("date_month") or row.get("date") or row.get("month")
    if not d:
        return None
    s = str(d)[:10]
    if len(s) >= 7 and s[4] == "-":
        # Normalize to first-of-month YYYY-MM-01
        return f"{s[:7]}-01"
    return None


def points_adoption_overall(
    payload: dict[str, list[dict[str, Any]]],
) -> list[dict[str, float]]:
    pts: list[dict[str, float]] = []
    for row in payload.get("adoptionOverall") or []:
        d = _month(row)
        v = _num(row.get("adoption_rate_pct"))
        if d is None or v is None:
            continue
        pts.append({"date": d, "value": v})
    pts.sort(key=lambda p: p["date"])
    return pts


def points_census_estimate(
    payload: dict[str, list[dict[str, Any]]],
) -> list[dict[str, float]]:
    pts: list[dict[str, float]] = []
    for row in payload.get("adoptionUsEstimate") or []:
        d = _month(row)
        v = _num(row.get("adoption_rate_pct"))
        if d is None or v is None:
            continue
        pts.append({"date": d, "value": v})
    pts.sort(key=lambda p: p["date"])
    return pts


def points_spend_median_pepm(
    payload: dict[str, list[dict[str, Any]]],
) -> list[dict[str, float]]:
    pts: list[dict[str, float]] = []
    for row in payload.get("spendPerEmployee") or []:
        d = _month(row)
        v = _num(row.get("median_pepm"))
        if d is None or v is None:
            continue
        pts.append({"date": d, "value": v})
    pts.sort(key=lambda p: p["date"])
    return pts


def points_vendor_adoption(
    payload: dict[str, list[dict[str, Any]]],
    vendor: str,
) -> list[dict[str, float]]:
    want = vendor.strip().lower()
    pts: list[dict[str, float]] = []
    for row in payload.get("adoptionVendor") or []:
        if str(row.get("vendor") or "").strip().lower() != want:
            continue
        d = _month(row)
        v = _num(row.get("adoption_rate_pct"))
        if d is None or v is None:
            continue
        pts.append({"date": d, "value": v})
    pts.sort(key=lambda p: p["date"])
    return pts


def points_industry_adoption(
    payload: dict[str, list[dict[str, Any]]],
    naics_sector: str,
) -> list[dict[str, float]]:
    want = naics_sector.strip().lower()
    pts: list[dict[str, float]] = []
    for row in payload.get("adoptionIndustry") or []:
        if str(row.get("naics_sector") or "").strip().lower() != want:
            continue
        d = _month(row)
        v = _num(row.get("adoption_rate_pct"))
        if d is None or v is None:
            continue
        pts.append({"date": d, "value": v})
    pts.sort(key=lambda p: p["date"])
    return pts
