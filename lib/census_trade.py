"""Census International Trade (HS imports) helpers."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


UA = "mosaicbot_themedata/0.1 (+census_trade)"
BASE = "https://api.census.gov/data/timeseries/intltrade/imports/hs"


class CensusApiError(RuntimeError):
    pass


def _get_json(params: dict[str, str], *, timeout: int = 120) -> list[list[str]]:
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        raise CensusApiError(f"Census HTTP {e.code}: {detail}") from e

    if raw.lstrip().lower().startswith("<!DOCTYPE") or raw.lstrip().lower().startswith(
        "<html"
    ):
        title = "HTML error"
        if "Invalid Key" in raw:
            title = "Invalid Key — check CENSUS_API_KEY (activate email link, then re-paste)"
        elif "Missing Key" in raw:
            title = "Missing Key"
        raise CensusApiError(f"Census API returned {title}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise CensusApiError(f"Census non-JSON response: {raw[:200]!r}") from e
    if not isinstance(data, list) or not data:
        raise CensusApiError(f"unexpected Census payload: {raw[:200]!r}")
    return data


def fetch_hs6_world_monthly(
    hs6: str,
    *,
    start_year: int,
    end_year: int,
    api_key: str,
) -> tuple[list[dict[str, float]], list[dict[str, float]], str | None]:
    """World-total (CTY_CODE=-) monthly general import value + qty1 for an HS6 code.

    Returns (value_points_usd, qty_points, unit_qy1_or_None).
    Quantity units are often only reliable at HS10 — callers may fall back.
    """
    params = {
        "get": "I_COMMODITY,I_COMMODITY_LDESC,COMM_LVL,GEN_VAL_MO,GEN_QY1_MO,UNIT_QY1,YEAR,MONTH",
        "time": f"from {start_year:04d}-01 to {end_year:04d}-12",
        "I_COMMODITY": hs6,
        "COMM_LVL": "HS6",
        "CTY_CODE": "-",
        "key": api_key,
    }
    rows = _get_json(params)
    header = rows[0]
    idx = {name: i for i, name in enumerate(header)}
    need = ("GEN_VAL_MO", "YEAR", "MONTH")
    for n in need:
        if n not in idx:
            raise CensusApiError(f"Census missing column {n}: {header}")

    value_pts: list[dict[str, float]] = []
    qty_pts: list[dict[str, float]] = []
    unit: str | None = None
    for row in rows[1:]:
        try:
            y = int(row[idx["YEAR"]])
            m = int(row[idx["MONTH"]])
            val = float(row[idx["GEN_VAL_MO"]])
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        date = f"{y:04d}-{m:02d}-01"
        value_pts.append({"date": date, "value": val})
        if "UNIT_QY1" in idx:
            u = (row[idx["UNIT_QY1"]] or "").strip()
            if u:
                unit = u
        if "GEN_QY1_MO" in idx:
            qraw = row[idx["GEN_QY1_MO"]]
            if qraw not in (None, "", "0", "-"):
                try:
                    qty_pts.append({"date": date, "value": float(qraw)})
                except (TypeError, ValueError):
                    pass

    value_pts.sort(key=lambda p: p["date"])
    qty_pts.sort(key=lambda p: p["date"])
    return value_pts, qty_pts, unit


def fetch_hs10_world_kg_monthly(
    hs6_prefix: str,
    *,
    start_year: int,
    end_year: int,
    api_key: str,
    chunk_years: int = 5,
) -> list[dict[str, float]]:
    """Sum GEN_QY1_MO across HS10 children where UNIT_QY1 is kilograms."""
    by_date: dict[str, float] = {}
    y0 = start_year
    while y0 <= end_year:
        y1 = min(y0 + chunk_years - 1, end_year)
        params = {
            "get": "I_COMMODITY,GEN_VAL_MO,GEN_QY1_MO,UNIT_QY1,YEAR,MONTH",
            "time": f"from {y0:04d}-01 to {y1:04d}-12",
            "I_COMMODITY": f"{hs6_prefix}*",
            "COMM_LVL": "HS10",
            "CTY_CODE": "-",
            "key": api_key,
        }
        rows = _get_json(params, timeout=180)
        if len(rows) >= 2:
            header = rows[0]
            idx = {name: i for i, name in enumerate(header)}
            # header may duplicate I_COMMODITY; first wins
            for row in rows[1:]:
                unit = (row[idx["UNIT_QY1"]] if "UNIT_QY1" in idx else "") or ""
                if unit.strip().upper() not in ("KG", "KGS", "KILOGRAM", "KILOGRAMS"):
                    continue
                try:
                    y = int(row[idx["YEAR"]])
                    m = int(row[idx["MONTH"]])
                    q = float(row[idx["GEN_QY1_MO"]])
                except (TypeError, ValueError, KeyError):
                    continue
                date = f"{y:04d}-{m:02d}-01"
                by_date[date] = by_date.get(date, 0.0) + q
        y0 = y1 + 1
        time.sleep(0.2)
    return [{"date": d, "value": by_date[d]} for d in sorted(by_date)]


def unit_value_usd_per_kg(
    value_pts: list[dict[str, float]],
    kg_pts: list[dict[str, float]],
) -> list[dict[str, float]]:
    kg_map = {p["date"]: p["value"] for p in kg_pts}
    out: list[dict[str, float]] = []
    for p in value_pts:
        kg = kg_map.get(p["date"])
        if kg is None or kg == 0:
            continue
        out.append({"date": p["date"], "value": p["value"] / kg})
    return out
