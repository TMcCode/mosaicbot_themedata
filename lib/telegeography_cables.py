"""TeleGeography public Submarine Cable Map — annual RFS route-km aggregates.

Free JSON at submarinecablemap.com (not SubTel Forum — that site is HTML-only).
Used as the Optical & Fiber "SubTel subsea km" watchlist proxy: hyperscaler-
owned + all-system ready-for-service route kilometers by RFS year.

Each run re-fetches the full catalog (point-in-time snapshot) and rebuilds
annual series from RFS years — same philosophy as re-pulling BLS windows.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = "https://www.submarinecablemap.com/api/v3"
UA = "mosaicbot_themedata/0.1 (+telegeography_cables)"

# Owner tokens for Google / Meta / Microsoft / Amazon (watchlist: hyperscaler-funded).
HYPERSCALER_OWNER_RE = re.compile(
    r"\b(google|alphabet|meta|facebook|microsoft|amazon|aws)\b",
    re.I,
)
_LEN_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*km", re.I)


def _get_json(path: str) -> Any:
    url = f"{API}{path}"
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=60) as resp:
        raw = resp.read()
        status = getattr(resp, "status", None) or resp.getcode()
    if status != 200:
        raise RuntimeError(f"TeleGeography HTTP {status} for {path}")
    return json.loads(raw.decode("utf-8"))


def parse_length_km(length: Any) -> float | None:
    if length is None or length == "":
        return None
    m = _LEN_RE.search(str(length))
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def is_hyperscaler_owned(owners: Any) -> bool:
    return bool(HYPERSCALER_OWNER_RE.search(str(owners or "")))


def list_cables() -> list[dict[str, Any]]:
    rows = _get_json("/cable/all.json")
    if not isinstance(rows, list):
        raise RuntimeError(f"unexpected cable list type: {type(rows)}")
    return rows


def fetch_cable(cable_id: str) -> dict[str, Any]:
    body = _get_json(f"/cable/{cable_id}.json")
    if not isinstance(body, dict):
        raise RuntimeError(f"unexpected cable detail for {cable_id}")
    return body


def fetch_all_cable_details(
    *,
    sleep_s: float = 0.04,
    max_retries: int = 3,
) -> list[dict[str, Any]]:
    """Full catalog detail pull (≈700 GETs; ~30–90s)."""
    listing = list_cables()
    out: list[dict[str, Any]] = []
    errors: list[str] = []
    for i, row in enumerate(listing):
        cid = str(row.get("id") or "").strip()
        if not cid:
            continue
        last_err: Exception | None = None
        for attempt in range(max_retries):
            try:
                out.append(fetch_cable(cid))
                last_err = None
                break
            except (HTTPError, URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as e:
                last_err = e
                time.sleep(0.5 * (attempt + 1))
        if last_err is not None:
            errors.append(f"{cid}: {last_err}")
        if sleep_s and i + 1 < len(listing):
            time.sleep(sleep_s)
    if not out:
        raise RuntimeError("no cable details fetched: " + "; ".join(errors[:5]))
    return out


def annual_rfs_km_points(
    cables: list[dict[str, Any]],
    *,
    segment: str = "all",
    hyperscaler_only: bool | None = None,
    include_planned: bool = False,
    start_year: int = 2010,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Annual *new* RFS route-km (flow). Date = YYYY-12-31 (period_end).

    segment: ``all`` | ``hyperscaler`` | ``other`` (non-hyperscaler residual).
    Legacy ``hyperscaler_only=True`` maps to segment=hyperscaler.

    Planned systems excluded by default. Current calendar year is YTD RFS
    from today's TE snapshot (incomplete until year-end).
    """
    if hyperscaler_only is True:
        segment = "hyperscaler"
    elif hyperscaler_only is False and segment == "all":
        segment = "all"
    segment = str(segment or "all").lower().strip()
    if segment not in ("all", "hyperscaler", "other"):
        raise RuntimeError(f"unknown subsea segment: {segment}")

    by_year_km: dict[int, float] = defaultdict(float)
    by_year_n: dict[int, int] = defaultdict(int)
    skipped = {"no_length": 0, "no_rfs": 0, "planned": 0, "filtered": 0}

    for row in cables:
        if row.get("is_planned") and not include_planned:
            skipped["planned"] += 1
            continue
        hyper = is_hyperscaler_owned(row.get("owners"))
        if segment == "hyperscaler" and not hyper:
            skipped["filtered"] += 1
            continue
        if segment == "other" and hyper:
            skipped["filtered"] += 1
            continue
        km = parse_length_km(row.get("length"))
        if km is None:
            skipped["no_length"] += 1
            continue
        y = row.get("rfs_year")
        try:
            year = int(y)
        except (TypeError, ValueError):
            skipped["no_rfs"] += 1
            continue
        if year < start_year:
            continue
        by_year_km[year] += km
        by_year_n[year] += 1

    points = [
        {"date": f"{y:04d}-12-31", "value": round(by_year_km[y], 2)}
        for y in sorted(by_year_km)
    ]
    meta = {
        "segment": segment,
        "include_planned": include_planned,
        "start_year": start_year,
        "years": len(points),
        "cables_in": len(cables),
        "cable_counts_by_year": {str(y): by_year_n[y] for y in sorted(by_year_n)},
        "skipped": skipped,
        "source": "telegeography_submarine_cable_map",
        "unit": "km",
    }
    return points, meta
